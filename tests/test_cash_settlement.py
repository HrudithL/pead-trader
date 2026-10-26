import numpy as np
import pandas as pd
import pytest
from dataclasses import replace

from pead.earnings_options.engine import Config, run
from pead.earnings_options.structures import Leg, Structure


def case(settlement=97.):
    dates=pd.bdate_range('2020-02-03',periods=6)
    expiry=pd.Timestamp('2020-02-08')
    e=pd.DataFrame([dict(event_id='index',secid=7,sector='index',entry_date=dates[0],
        release_date=dates[0]-pd.Timedelta(days=1),rank=.99,reaction=.01,rv=.2,adv=1e9,
        timing_verified=True,consensus_verified=True,identity_verified=True,
        corporate_actions_verified=True,assignment_verified=True,earnings_calendar_not_applicable=True)])
    q=pd.DataFrame([dict(date=d,secid=7,optionid=i,cp='P',strike=k,expiry=expiry,
        bid=bid,ask=ask,delta=delta,prior_volume=1000,prior_oi=1000,iv=.2,
        standard_contract=True) for d in dates[:4]
        for i,k,bid,ask,delta in [(1,100,2.,2.1,-.35),(2,95,.4,.5,-.15)]])
    b=pd.DataFrame(dict(date=dates,benchmark=0.,rf=0.))
    s=pd.DataFrame([dict(secid=7,expiry=expiry,settlement_date=dates[4],value=settlement)])
    def choose(ch,*args):
        legs=tuple(Leg(r.optionid,-1 if r.optionid==1 else 1,'P',r.strike,expiry,
            r.bid,r.ask,r.delta,r.prior_volume) for r in ch.itertuples())
        return Structure('directional',legs,1.5,3.5,expiry),0.,1
    cfg=Config(initial_nav=10000,sleeves=('directional',),hold_sessions=1,
        directional_risk=.5,issuer_risk=.8,sector_risk=.8,cohort_risk=.8,aggregate_risk=.8,
        require_price_edge=False,expiry_buffer_days=1,cash_settlement=True)
    return e,q,b,cfg,choose,s


@pytest.mark.parametrize('settlement,payoff',[(110.,0.),(97.,-300.),(90.,-500.)])
def test_cash_settlement_uses_intrinsic_payoff_without_expiry_quote(settlement,payoff):
    e,q,b,cfg,choose,s=case(settlement)
    r=run(e,q,b,cfg,selector=choose,settlements=s)
    t=r.trades.iloc[0]
    assert t.exit_date==s.settlement_date.iloc[0]
    assert t.reason=='cash_settlement'
    assert t.exit_value==pytest.approx(payoff)
    assert t.pnl==pytest.approx(150+payoff-4*.65)
    assert r.daily.nav.iloc[-1]==pytest.approx(10000+t.pnl)
    assert not t.exit_is_worst_loss_reserve
    assert r.diagnostics['missing_marks']==0
    assert (r.daily.free_cash>=0).all()


def test_future_settlement_does_not_change_entry_or_prior_marks():
    e,q,b,cfg,choose,s=case(110.)
    a=run(e,q,b,cfg,selector=choose,settlements=s)
    s['value']=90.
    z=run(e,q,b,cfg,selector=choose,settlements=s)
    pd.testing.assert_frame_equal(a.daily.iloc[:4],z.daily.iloc[:4])
    assert a.trades.entry_cashflow.equals(z.trades.entry_cashflow)
    assert a.trades.contracts.equals(z.trades.contracts)


@pytest.mark.parametrize('defect',['missing','duplicate','nonfinite','late'])
def test_cash_settlement_rejects_ambiguous_or_missing_resolution(defect):
    e,q,b,cfg,choose,s=case()
    if defect=='missing':s=s.iloc[:0]
    if defect=='duplicate':s=pd.concat([s,s])
    if defect=='nonfinite':s['value']=np.nan
    if defect=='late':s['settlement_date']=s.expiry+pd.Timedelta(days=1)
    with pytest.raises(ValueError,match='settlement'):
        run(e,q,b,cfg,selector=choose,settlements=s)


def test_settlement_risk_exit_stages_lots_and_settles_remainder():
    e,q,b,cfg,_,s=case(97.)
    b['risk_off']=[False,True,False,False,False,False]
    def choose(ch,*args):
        ex=s.expiry.iloc[0]
        legs=tuple(Leg(r.optionid,-1 if r.optionid==1 else 1,'P',r.strike,ex,
            r.bid,r.ask,r.delta,r.prior_volume) for r in ch.itertuples())
        return Structure('directional',legs,1.5,3.5,ex),0.,5
    cfg=replace(cfg,settlement_risk_exit=True)
    seen=[]
    def cap(live,ctx):
        seen.append((ctx['date'],dict(ctx['used_lots'])))
        return 1
    r=run(e,q,b,cfg,selector=choose,settlements=s,exit_capacity=cap)
    assert list(r.trades.contracts)==[1,1,1,2]
    assert list(r.trades.reason)==['risk_off','risk_off','risk_off','cash_settlement']
    assert len(seen)==3
    assert r.trades.pnl.sum()==pytest.approx(3*(-20-2.6)+2*(-150-2.6))
    assert r.daily.nav.iloc[-1]==pytest.approx(10000+r.trades.pnl.sum())


def test_settlement_rejects_implicit_early_rebalance():
    *_,cfg,choose,s=case()
    with pytest.raises(ValueError,match='rebalancing'):
        replace(cfg,current_risk_budget=True)


def test_exit_capacity_is_shared_between_positions_in_same_contract():
    e,q,b,cfg,_,s=case(110.)
    e=pd.concat([e.assign(event_id='a'),e.assign(event_id='b')],ignore_index=True)
    b['risk_off']=[False,True,True,True,False,False]
    def choose(ch,*args):
        ex=s.expiry.iloc[0]
        legs=tuple(Leg(r.optionid,-1 if r.optionid==1 else 1,'P',r.strike,ex,
            r.bid,r.ask,r.delta,r.prior_volume) for r in ch.itertuples())
        return Structure('directional',legs,1.5,3.5,ex),0.,10
    cfg=replace(cfg,directional_risk=.2,settlement_risk_exit=True)
    def cap(live,ctx):
        return max(0,min(1-ctx['used_lots'].get((ctx['date'],int(i)),0) for i in live.optionid))
    r=run(e,q,b,cfg,selector=choose,settlements=s,exit_capacity=cap)
    market_exits=r.trades[r.trades.reason=='risk_off']
    assert market_exits.groupby('exit_date').contracts.sum().max()==1
    assert r.trades.contracts.sum()==10
    assert r.daily.nav.iloc[-1]==pytest.approx(10000+r.trades.pnl.sum())


def test_missing_early_exit_quote_waits_for_exact_settlement():
    e,q,b,cfg,choose,s=case(97.)
    q=q[q.date==b.date.iloc[0]]
    b['risk_off']=[False,True,False,False,False,False]
    cfg=replace(cfg,settlement_risk_exit=True,defer_unquoted_exits=True)
    r=run(e,q,b,cfg,selector=choose,settlements=s,exit_capacity=lambda *_:1)
    assert len(r.trades)==1
    assert r.trades.reason.iloc[0]=='cash_settlement'
    assert r.trades.pnl.iloc[0]==pytest.approx(-152.6)
    assert r.diagnostics['forced_reserves']==0
    assert r.diagnostics['execution_valid'] is False


def test_costly_exit_cannot_spend_another_positions_cash_collateral():
    e,q,b,cfg,_,s=case(110.)
    e=pd.concat([e.assign(event_id='a'),e.assign(event_id='b',secid=8)],ignore_index=True)
    q=pd.concat([q,q.assign(secid=8,optionid=q.optionid+10)],ignore_index=True)
    bad=(q.secid==7)&(q.optionid==1)&(q.date>b.date.iloc[0])
    q.loc[bad,'bid']=9.3;q.loc[bad,'ask']=9.4
    s=pd.concat([s,s.assign(secid=8)],ignore_index=True)
    b['risk_off']=[False,True,True,True,False,False]
    def choose(ch,*args):
        ex=s.expiry.iloc[0]
        legs=tuple(Leg(r.optionid,-1 if r.strike==100 else 1,'P',r.strike,ex,
            r.bid,r.ask,r.delta,r.prior_volume) for r in ch.itertuples())
        return Structure('directional',legs,1.5,3.5,ex),0.,1
    cfg=replace(cfg,initial_nav=1000,aggregate_risk=.95,issuer_risk=.95,sector_risk=.95,
        cohort_risk=.95,settlement_risk_exit=True,defer_unquoted_exits=True)
    r=run(e,q,b,cfg,selector=choose,settlements=s,
        exit_capacity=lambda live,ctx:1 if live.secid.iloc[0]==7 else 0)
    assert (r.daily.free_cash>=0).all()
    assert set(r.trades.reason)=={'cash_settlement'}
    assert r.daily.nav.iloc[-1]==pytest.approx(1294.8)
