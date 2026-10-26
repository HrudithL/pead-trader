import numpy as np
import pandas as pd
import pytest

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
