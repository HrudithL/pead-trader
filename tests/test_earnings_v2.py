import numpy as np
import pandas as pd
import pytest

from pead.earnings_options.liquidity_v2 import LiquidityPolicy,eligible,capacity
from pead.earnings_options.candidates_v2 import enumerate_structures
from pead.earnings_options.payoff_v2 import forecast,select_decisions


def quotes():
    rows=[(1,'C',90,.85,10.,10.2),(2,'C',100,.60,3.,3.1),
          (3,'C',105,.30,1.,1.1),(4,'C',110,.10,.1,.2),
          (5,'P',110,-.85,10.,10.2),(6,'P',100,-.60,3.,3.1),
          (7,'P',95,-.30,1.,1.1),(8,'P',90,-.10,.1,.2)]
    return pd.DataFrame([dict(optionid=i,cp=cp,strike=k,delta=d,bid=b,ask=a,
        secid=7,date=pd.Timestamp('2004-01-02'),expiry=pd.Timestamp('2004-02-20'),
        prior_volume=0.,prior_oi=500.,standard_contract=True,iv=.4,cfadj=1.)
        for i,cp,k,d,b,a in rows])


def test_zero_yesterday_volume_is_small_lot_not_automatic_rejection():
    q=quotes()
    assert len(eligible(q))>0
    assert capacity(q.iloc[:2])==1
    q['volume']=100000
    assert capacity(q.iloc[:2])==1


def test_bad_policy_and_unquoted_or_nonstandard_options_fail():
    with pytest.raises(ValueError):LiquidityPolicy(participation=2)
    q=quotes().iloc[:1]
    assert eligible(q.assign(standard_contract=False)).empty
    assert eligible(q.assign(ask=0.)).empty


def test_menu_includes_debit_and_credit_bullish_spreads():
    candidates=enumerate_structures(quotes(),'directional',1)
    assert candidates
    assert any(c['structure'].cashflow>0 for c in candidates)
    assert any(c['structure'].cashflow<0 for c in candidates)
    for c in candidates:
        s=c['structure'];assert s.max_loss>0
        assert len({x.expiry for x in s.legs})==1
        assert c['capacity']>=1


def test_menu_is_deterministic_and_does_not_require_exact_delta_window():
    a=enumerate_structures(quotes(),'directional',1)
    b=enumerate_structures(quotes().sample(frac=1,random_state=8),'directional',1)
    assert [x['key'] for x in a]==[x['key'] for x in b]


def history(n=150):
    return pd.DataFrame(dict(event_id=[f'e{i}' for i in range(n)],secid=np.arange(n)%20,
        entry_date=pd.Timestamp('2000-01-01'),exit_date=pd.Timestamp('2000-02-01'),
        archetype='bull_call_65_30',signal_bin=2,vol_bin=1,return_on_risk=.10,
        capacity=1,candidate_id=[f'c{i}' for i in range(n)],sleeve='directional'))


def test_forecast_cannot_read_unmatured_outcomes():
    h=history();target=h.iloc[0]
    first=forecast(h,target,pd.Timestamp('2001-01-01'))
    f=h.copy();f['exit_date']=pd.Timestamp('2002-01-01');f['return_on_risk']=100.
    second=forecast(pd.concat([h,f]),target,pd.Timestamp('2001-01-01'))
    assert first==second
    assert first['lower']>0
    assert forecast(h,target,pd.Timestamp('2000-01-15')) is None


def test_repeated_candidates_do_not_inflate_independent_sample_count():
    h=history();target=h.iloc[0]
    a=forecast(h,target,pd.Timestamp('2001-01-01'))
    b=forecast(pd.concat([h,h]),target,pd.Timestamp('2001-01-01'))
    assert a['events']==b['events']
    assert a['lower']==pytest.approx(b['lower'])


def test_no_selection_from_negative_historical_payoff():
    h=history();h['return_on_risk']=-.1
    target=h.iloc[:1].copy();target['entry_date']=pd.Timestamp('2001-01-01')
    assert select_decisions(h,target).empty


def test_selector_cannot_inject_an_artificial_entry_price():
    from pead.earnings_options.engine import run,Config
    from dataclasses import replace
    from test_earnings_options import inputs
    e,q,b=inputs()
    choice=enumerate_structures(quotes(),'directional',1)[0]['structure']
    def bad_selector(ch,sleeve,direction,event):
        from pead.earnings_options.structures import select_structure
        s=select_structure(ch,sleeve,direction)
        return replace(s,cashflow=-.01,max_loss=.01),.2,1
    with pytest.raises(ValueError,match='cashflow'):
        run(e,q,b,Config(sleeves=('directional',)),selector=bad_selector)


def test_raw_payoff_reserves_missing_leg_and_does_not_drop_losers():
    from pead.earnings_options.prepare_v2 import quote_payoff
    from pead.earnings_options.candidates_v2 import pack
    import json
    s=enumerate_structures(quotes(),'directional',1)[0]['structure']
    row=pd.Series(dict(legs=pack(s),sleeve=s.sleeve,cashflow=s.cashflow,max_loss=s.max_loss,
        expiry=s.expiry,secid=7,exit_date=pd.Timestamp('2004-01-30'),
        factors=json.dumps({str(x.optionid):1. for x in s.legs})))
    value,valid=quote_payoff(row,{})
    assert not valid
    assert s.cashflow+value==pytest.approx(-s.max_loss)


def test_conditional_model_prices_observed_friction_without_future_labels():
    h=history(200)
    h['friction']=np.linspace(.01,.25,len(h));h['iv_rv']=1.3
    h['return_on_risk']=.2-2*h.friction
    low=h.iloc[0].copy();high=h.iloc[-1].copy()
    a=forecast(h,low,pd.Timestamp('2001-01-01'))
    b=forecast(h,high,pd.Timestamp('2001-01-01'))
    assert a['mean']>b['mean']
    f=h.copy();f['exit_date']=pd.Timestamp('2002-01-01');f['return_on_risk']=10.
    assert forecast(pd.concat([h,f]),low,pd.Timestamp('2001-01-01'))['mean']==pytest.approx(a['mean'])


def test_fast_menu_matches_reference_on_complete_payoffs():
    from pead.earnings_options.fast_candidates_v2 import enumerate_structures as fast
    for family,direction in [('directional',1),('directional',-1),('condor',1)]:
        assert fast(quotes(),family,direction)==enumerate_structures(quotes(),family,direction)


def test_same_contract_capacity_is_shared_by_simultaneous_orders():
    from pead.earnings_options.engine import run,Config
    from pead.earnings_options.structures import select_structure
    from test_earnings_options import inputs
    e,q,b=inputs();e=pd.concat([e,e.assign(event_id='e2')])
    def choose(ch,sleeve,direction,event):return select_structure(ch,sleeve,direction),.2,1
    result=run(e,q,b,Config(initial_nav=1_000_000,sleeves=('directional',)),selector=choose)
    assert len(result.trades)==1


def test_predeclared_exit_matches_training_horizon():
    from pead.earnings_options.engine import run,Config
    from pead.earnings_options.structures import select_structure
    from test_earnings_options import inputs
    e,q,b=inputs()
    def choose(ch,sleeve,direction,event):return select_structure(ch,sleeve,direction),.2,1,b.date.iloc[1]
    result=run(e,q,b,Config(initial_nav=100_000,sleeves=('directional',)),selector=choose)
    assert result.trades.iloc[0].exit_date==b.date.iloc[1]


@pytest.mark.parametrize('nav,credit',[(100_000,True),(1_000_000,False)])
def test_allocator_chooses_best_feasible_structure_for_account_size(nav,credit):
    from pead.earnings_options.research_v2 import run_profile
    from pead.earnings_options.candidates_v2 import pack
    from pead.earnings_options.engine import Config
    from test_earnings_options import event
    dates=pd.bdate_range('2004-01-02',periods=5)
    e=event('2004-01-02')
    q=pd.concat([quotes().assign(date=d) for d in dates])
    b=pd.DataFrame(dict(date=dates,benchmark=0.,rf=0.))
    menu=enumerate_structures(quotes(),'directional',1)
    expensive=next(x for x in menu if x['key']=='bull_C_85_20')['structure']
    cheap=next(x for x in menu if x['key']=='bull_P_credit_30_10')['structure']
    rows=[]
    for name,s,edge in [('expensive',expensive,.9),('cheap',cheap,.1)]:
        rows.append(dict(event_id='e1',candidate_id=name,sleeve='directional',direction=1,
                         entry_date=dates[0],exit_date=dates[2],expiry=s.expiry,
                         cashflow=s.cashflow,max_loss=s.max_loss,legs=pack(s),
                         capacity=1,predicted_edge=edge))
    result=run_profile(e,q,b,pd.DataFrame(rows),Config(initial_nav=nav,sleeves=('directional',),directional_risk=.005))
    assert len(result.trades)==1
    assert (result.trades.iloc[0].entry_cashflow>0)==credit
