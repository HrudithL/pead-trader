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


def test_vendor_lagged_oi_is_not_shifted_twice():
    from pead.earnings_options.index_research import lagged_liquidity
    dates=pd.bdate_range('2004-01-02',periods=3)
    q=pd.concat([quotes().iloc[:1].assign(date=d,volume=7.,open_interest=100.+i,ss_flag='0') for i,d in enumerate(dates)])
    result=lagged_liquidity(q,dates)
    assert result.iloc[1].prior_volume==7.
    assert result.iloc[1].prior_oi==101.


def test_index_signals_are_flat_when_known_volatility_exceeds_limit():
    from pead.earnings_options.index_research import policy_direction,POLICIES
    b=pd.DataFrame(dict(trend_sign=[1,-1,1],breadth_sign=[1,-1,1],rv=[.2,.2,.4]))
    assert policy_direction(b,POLICIES['earnings_two_sided']).tolist()==[1,-1,0]


def test_risk_off_liquidation_uses_current_executable_quotes():
    from pead.earnings_options.engine import run,Config
    from test_earnings_options import inputs
    e,q,b=inputs();b['risk_off']=[False,True,True,True,True]
    r=run(e,q,b,Config(initial_nav=100_000,sleeves=('directional',)))
    assert r.trades.iloc[0].exit_date==b.date.iloc[1]
    assert r.trades.iloc[0].reason=='risk_off'


def test_current_value_risk_budget_does_not_reinvest_unprotected_paper_gains():
    from pead.earnings_options.engine import run,Config
    from test_earnings_options import inputs
    e,q,b=inputs()
    q.loc[(q.date>b.date.iloc[0])&(q.optionid==1),'bid']=5.
    q.loc[(q.date>b.date.iloc[0])&(q.optionid==1),'ask']=5.1
    cfg=Config(initial_nav=100_000,aggregate_risk=.003,sleeves=('directional',),current_risk_budget=True)
    r=run(e,q,b,cfg)
    assert r.trades.iloc[0].exit_date==b.date.iloc[1]
    assert r.trades.iloc[0].reason=='risk_rebalance'


def test_risk_rebalance_trims_integer_lots_and_preserves_remaining_book():
    from pead.earnings_options.engine import run,Config
    from test_earnings_options import inputs
    e,q,b=inputs()
    q.loc[(q.date>b.date.iloc[0])&(q.optionid==1),'bid']=5.
    q.loc[(q.date>b.date.iloc[0])&(q.optionid==1),'ask']=5.1
    cfg=Config(initial_nav=100_000,aggregate_risk=.006,directional_risk=.006,
               sleeves=('directional',),current_risk_budget=True)
    r=run(e,q,b,cfg)
    assert r.trades.contracts.tolist()==[1,1]
    assert r.trades.iloc[0].reason=='risk_rebalance'
    assert r.trades.iloc[1].exit_date==b.date.iloc[-1]
    assert r.daily.iloc[-1].nav==pytest.approx(100_000+r.trades.pnl.sum())


def test_fully_paid_long_call_has_no_fictitious_spread_profit_cap():
    from pead.earnings_options.engine import run,Config
    from pead.earnings_options.structures import Leg,Structure
    from test_earnings_options import inputs
    e,q,b=inputs();q=q[q.optionid==1].copy();q['underlying_price']=100.
    q.loc[q.date>b.date.iloc[0],'bid']=6.;q.loc[q.date>b.date.iloc[0],'ask']=6.1
    def choose(ch,sleeve,direction,event):
        r=ch.iloc[0]
        leg=Leg(int(r.optionid),1,'C',float(r.strike),r.expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
        return Structure(sleeve,(leg,),-r.ask,r.ask,r.expiry),.2,1
    r=run(e,q,b,Config(initial_nav=100_000,directional_risk=.01,sleeves=('directional',)),selector=choose)
    assert len(r.trades)==1
    assert r.trades.iloc[0].pnl==pytest.approx((6.-3.1)*100-1.3)

def test_single_short_put_reserves_entire_strike_and_reconciles():
    from pead.earnings_options.engine import Config,run
    from pead.earnings_options.structures import Leg,Structure
    from test_earnings_options import inputs
    e,q,b=inputs();q=q[q.optionid==3].copy()
    def choose(ch,sleeve,direction,event):
        r=ch.iloc[0]
        leg=Leg(int(r.optionid),-1,'P',float(r.strike),r.expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
        return Structure(sleeve,(leg,),r.bid,r.strike-r.bid,r.expiry),.1,100
    r=run(e,q,b,Config(initial_nav=100_000,directional_risk=.25,aggregate_risk=.25,issuer_risk=.25,sector_risk=.25,cohort_risk=.25,sleeves=('directional',)),selector=choose)
    assert r.trades.contracts.tolist()==[2]
    assert r.daily.iloc[0].free_cash==pytest.approx(80_598.7)
    assert r.trades.iloc[0].pnl==pytest.approx(-22.6)
    assert r.daily.iloc[-1].nav==pytest.approx(100_000+r.trades.pnl.sum())


def test_non_event_volatility_is_lagged_and_excludes_announcement_jump():
    from pead.earnings_options.cash_puts import non_event_features
    dates=pd.bdate_range('2000-01-03',periods=100)
    s=pd.DataFrame(dict(permno=7,date=dates,ret=[.01,-.01]*50,prc=100.))
    s.loc[70,'ret']=.80
    announcements=pd.DataFrame(dict(permno=[7],day0_date=[dates[70]]))
    r=non_event_features(s,announcements)
    assert r.loc[71,'rv_non_event']<.20
    changed=s.copy();changed.loc[72:,'ret']=.9
    other=non_event_features(changed,announcements)
    assert r.loc[72,'rv_non_event']==pytest.approx(other.loc[72,'rv_non_event'])


def test_expected_put_liability_increases_with_volatility_and_tail_stress():
    from pead.earnings_options.cash_puts import expected_liability
    a=expected_liability(100.,100.,.2,.3,20,35,tail=False)
    assert expected_liability(100.,100.,.4,.3,20,35,tail=False)>a
    assert expected_liability(100.,100.,.2,.3,20,35,tail=True)>a


def test_unquoted_exit_defers_and_freezes_new_entries():
    from test_earnings_options import inputs
    from pead.earnings_options.engine import Config,run
    e,q,b=inputs();new=e.copy()
    new['event_id']='e2';new['entry_date']=b.date.iloc[1];new['release_date']=b.date.iloc[0]
    e=pd.concat([e,new],ignore_index=True)
    q.loc[(q.optionid==1)&(q.date==b.date.iloc[1]),['bid','ask']]=np.nan
    b['risk_off']=[False,True,True,True,True]
    r=run(e,q,b,Config(initial_nav=100_000,sleeves=('directional',),defer_unquoted_exits=True))
    assert len(r.trades)==1
    assert r.trades.iloc[0].exit_date==b.date.iloc[2]
    assert not r.trades.iloc[0].exit_is_worst_loss_reserve
    assert r.diagnostics['missing_marks']>0
    assert not r.diagnostics['execution_valid']
    assert 'unquoted_open_position' in set(r.skips.reason)
    assert r.daily.iloc[-1].nav==pytest.approx(100_000+r.trades.pnl.sum())


def test_non_event_prior_price_is_on_entry_split_basis():
    from pead.earnings_options.cash_puts import non_event_features
    s=pd.DataFrame(dict(permno=[7,7],date=pd.to_datetime(['2000-01-03','2000-01-04']),ret=[0.,0.],prc=[100.,50.],cfacpr=[2.,1.]))
    e=pd.DataFrame(dict(permno=pd.Series(dtype=int),day0_date=pd.Series(dtype='datetime64[ns]')))
    assert non_event_features(s,e).prior_spot.iloc[-1]==pytest.approx(50.)


def test_capacity_call_selection_uses_feasible_liquid_strike():
    from pead.earnings_options.index_calls import CallPolicy,select_call
    from types import SimpleNamespace
    date=pd.Timestamp('2006-01-03')
    q=pd.DataFrame(dict(date=[date,date],expiry=[date+pd.Timedelta(days=270)]*2,
        optionid=[1,2],cp=['C','C'],strike=[70.,80.],bid=[29.9,19.9],ask=[30.,20.],
        delta=[.95,.90],prior_volume=[0.,100.],prior_oi=[100.,5000.],standard_contract=True,
        underlying_price=[100.,100.],prior_underlying_price=[100.,100.]))
    ctx=dict(nav=100000.,risk_budget=45000.,free_cash=100000.,commission=1.,date=date,
             used_lots={},open_delta_units=0.,greeks_complete=True)
    event=SimpleNamespace(spot=100.)
    selected=select_call(q,CallPolicy(False,1.5,.45,capacity_selection=True),event,ctx)
    assert selected[0].legs[0].optionid==2
    changed=q.copy();changed['underlying_price']=200.
    other=select_call(changed,CallPolicy(False,1.5,.45,capacity_selection=True),event,ctx)
    assert other[0].legs[0].optionid==2


def test_deferred_missing_exit_never_recovers_after_contract_terminal():
    from test_earnings_options import inputs
    from pead.earnings_options.engine import Config,run
    e,q,b=inputs();q['expiry']=b.date.iloc[3]
    b['risk_off']=[False,True,True,True,True]
    q.loc[q.date.isin(b.date.iloc[1:3]),['bid','ask']]=np.nan
    from pead.earnings_options.structures import Leg,Structure
    def choose(ch,sleeve,direction,event):
        a=ch[ch.optionid==1].iloc[0];z=ch[ch.optionid==2].iloc[0]
        legs=tuple(Leg(int(x.optionid),qty,'C',float(x.strike),x.expiry,float(x.bid),float(x.ask),float(x.delta),float(x.prior_volume)) for x,qty in ((a,1),(z,-1)))
        debit=a.ask-z.bid
        return Structure(sleeve,legs,-debit,debit,a.expiry),.2,1
    r=run(e,q,b,Config(initial_nav=100000,sleeves=('directional',),expiry_buffer_days=1,defer_unquoted_exits=True),selector=choose)
    assert len(r.trades)==1
    assert r.trades.iloc[0].exit_date==b.date.iloc[2]
    assert r.trades.iloc[0].exit_is_worst_loss_reserve
    assert r.trades.iloc[0].reason=='contract_terminal'


def test_single_name_call_selector_skips_unaffordable_low_cost_contract():
    from pead.earnings_options.earnings_calls import select_menu
    from pead.earnings_options.structures import Leg,Structure
    expiry=pd.Timestamp('2020-06-20')
    def st(oid,ask):
        leg=Leg(oid,1,'C',60.,expiry,ask-.1,ask,.9,100.)
        return Structure('directional',(leg,),-ask,ask,expiry)
    choices=[((0.,),st(1,40.),5),((1.,),st(2,21.),5)]
    date=pd.Timestamp('2020-02-03')
    ctx=dict(date=date,used_lots={},commission=.65,risk_budget=3000.,free_cash=100000.)
    assert select_menu(choices,ctx)[0].legs[0].optionid==2
    ctx['risk_budget']=30000.
    assert select_menu(choices,ctx)[0].legs[0].optionid==1


def test_known_corporate_action_exits_only_when_information_is_available():
    from test_earnings_options import inputs
    from pead.earnings_options.engine import Config,run
    e,q,b=inputs()
    a=pd.DataFrame(dict(secid=[7],known_date=[b.date.iloc[2]],effective_date=[b.date.iloc[-1]]))
    r=run(e,q,b,Config(initial_nav=100000,sleeves=('directional',)),corporate_actions=a)
    assert r.trades.iloc[0].entry_date==b.date.iloc[0]
    assert r.trades.iloc[0].exit_date==b.date.iloc[2]
    assert r.trades.iloc[0].reason=='announced_corporate_action'
    a['known_date']=b.date.iloc[0]
    blocked=run(e,q,b,Config(initial_nav=100000,sleeves=('directional',)),corporate_actions=a)
    assert blocked.trades.empty
    assert 'announced_corporate_action' in set(blocked.skips.reason)


def test_concentrated_call_selection_obeys_gross_stock_admission_limit():
    from pead.earnings_options.earnings_calls import select_menu
    from pead.earnings_options.structures import Leg,Structure
    from types import SimpleNamespace
    date=pd.Timestamp('2020-02-03');expiry=pd.Timestamp('2020-06-20')
    leg=Leg(1,1,'C',60.,expiry,39.9,40.,.9,100.)
    st=Structure('directional',(leg,),-40.,40.,expiry)
    ctx=dict(date=date,used_lots={},commission=.65,risk_budget=30000.,free_cash=100000.,
        stock_notional_complete=True,open_stock_notional_bound=195000.,nav=100000.)
    event=SimpleNamespace(portfolio_stock_limit=2.,spot=100.)
    assert select_menu([((0.,),st,20)],ctx,event) is None
    ctx['open_stock_notional_bound']=180000.
    assert select_menu([((0.,),st,20)],ctx,event)[2]==2


def test_stock_exposure_context_counts_other_issuers_using_prior_known_prices():
    from test_earnings_options import inputs
    from pead.earnings_options.engine import Config,run
    from pead.earnings_options.earnings_calls import select_menu
    from pead.earnings_options.structures import Leg,Structure
    e,q,b=inputs();one=q[q.optionid==1].copy();one['underlying_price']=100.
    events=[];quotes=[]
    for issuer,oid,name,day in ((7,1,'a',0),(8,11,'b',0),(9,21,'c',1)):
        x=e.copy();x['secid']=issuer;x['event_id']=name;x['spot']=100.;x['portfolio_stock_limit']=2.
        x['entry_date']=b.date.iloc[day];x['release_date']=b.date.iloc[day]-pd.Timedelta(days=2)
        events.append(x)
        y=one.copy();y['secid']=issuer;y['optionid']=oid;quotes.append(y)
    q=pd.concat(quotes,ignore_index=True)
    q.loc[q.date==b.date.iloc[1],'underlying_price']=1_000_000.
    contexts=[]
    def choose(ch,sleeve,direction,event,ctx):
        contexts.append((event.event_id,ctx['open_stock_notional_bound']))
        a=ch.iloc[0];leg=Leg(int(a.optionid),1,'C',float(a.strike),a.expiry,float(a.bid),float(a.ask),float(a.delta),float(a.prior_volume))
        st=Structure(sleeve,(leg,),-a.ask,a.ask,a.expiry)
        return select_menu([((0.,),st,10)],ctx,event)
    cfg=Config(initial_nav=100000,sleeves=('directional',),require_price_edge=False,directional_risk=.30,issuer_risk=.30,sector_risk=1.,cohort_risk=1.,aggregate_risk=.80)
    r=run(pd.concat(events,ignore_index=True),q,b,cfg,selector=choose,selector_context=True)
    assert dict(contexts)['b']==pytest.approx(100000.)
    assert dict(contexts)['c']==pytest.approx(190000.)
    assert r.trades.contracts.tolist()==[10,9]
    assert r.daily.free_cash.min()>=0
    assert r.daily.iloc[-1].nav==pytest.approx(100000+r.trades.pnl.sum())


@pytest.mark.parametrize('factor',[.5,2.])
def test_stock_bound_keeps_price_and_contract_factor_on_same_historical_basis(factor):
    from test_earnings_options import inputs
    from pead.earnings_options.engine import Config,run
    from pead.earnings_options.structures import Leg,Structure
    e,q,b=inputs();q=q[q.optionid==1].copy()
    q['underlying_price']=10.;q['cfadj']=1.;q['strike']=1.;q['bid']=9.;q['ask']=9.1
    q.loc[q.date>b.date.iloc[0],'underlying_price']=10./factor
    q.loc[q.date>b.date.iloc[0],'cfadj']=factor
    q.loc[q.date>b.date.iloc[0],'strike']=1./factor
    q.loc[q.date>b.date.iloc[0],'bid']=9./factor
    q.loc[q.date>b.date.iloc[0],'ask']=9.1/factor
    other=q.copy();other['secid']=8;other['optionid']=11
    e['spot']=10.;e['portfolio_stock_limit']=2.
    other_event=e.copy();other_event['event_id']='e2';other_event['secid']=8
    other_event['entry_date']=b.date.iloc[1];other_event['release_date']=b.date.iloc[0]
    observed=[]
    def choose(ch,sleeve,direction,event,ctx):
        if event.event_id=='e2':observed.append(ctx['open_stock_notional_bound']);return None
        a=ch.iloc[0];leg=Leg(int(a.optionid),1,'C',a.strike,a.expiry,a.bid,a.ask,a.delta,a.prior_volume)
        return Structure(sleeve,(leg,),-a.ask,a.ask,a.expiry),0.,1
    cfg=Config(initial_nav=100000,sleeves=('directional',),require_price_edge=False,directional_risk=.30)
    run(pd.concat([e,other_event],ignore_index=True),pd.concat([q,other],ignore_index=True),b,cfg,selector=choose,selector_context=True)
    assert observed==pytest.approx([1000.])


def test_streamed_quote_store_matches_cash_ledger_across_year_boundary():
    from test_earnings_options import inputs
    from pead.earnings_options.engine import Config,run
    from pead.earnings_options.quote_store import YearQuoteStore
    e,q,b=inputs();old=b.date.copy();new=pd.bdate_range('2020-12-30',periods=len(b))
    q['date']=q.date.map(dict(zip(old,new)));q['expiry']=pd.Timestamp('2021-02-19')
    e['entry_date']=new[0];e['release_date']=new[0]-pd.Timedelta(days=2);b['date']=new
    cfg=Config(initial_nav=100000,sleeves=('directional',),hold_sessions=3)
    expected=run(e,q,b,cfg)
    loaded=[]
    def load(year):loaded.append(year);return q[q.date.dt.year==year].copy()
    source=YearQuoteStore(load)
    actual=run(e,source,b,cfg)
    pd.testing.assert_frame_equal(actual.daily,expected.daily)
    pd.testing.assert_frame_equal(actual.trades,expected.trades)
    assert actual.trades.iloc[0].exit_date.year==2021
    assert loaded==[2020,2021]


def test_long_carry_uses_whole_trade_cost_instead_of_absolute_half_dollar_gate():
    from pead.earnings_options.earnings_carry import menu
    from types import SimpleNamespace
    date=pd.Timestamp('2005-01-03')
    q=pd.DataFrame(dict(date=[date],expiry=[date+pd.Timedelta(days=180)],optionid=[1],cp=['C'],
        strike=[400.],bid=[100.],ask=[102.],delta=[.9],iv=[.2],prior_delta=[.9],prior_iv=[.2],prior_volume=[100.],prior_oi=[1000.],standard_contract=[True]))
    assert len(menu(q,SimpleNamespace(spot=500.)))==1
    q['ask']=110.
    assert menu(q,SimpleNamespace(spot=500.))==[]


def test_carry_contract_admission_uses_prior_session_analytics():
    from pead.earnings_options.earnings_carry import menu
    from types import SimpleNamespace
    date=pd.Timestamp('2005-01-03')
    q=pd.DataFrame(dict(date=[date],expiry=[date+pd.Timedelta(days=180)],optionid=[1],cp=['C'],
        strike=[400.],bid=[100.],ask=[102.],delta=[.9],iv=[.2],prior_delta=[.9],prior_iv=[.2],
        prior_volume=[100.],prior_oi=[1000.],standard_contract=[True]))
    first=menu(q,SimpleNamespace(spot=500.))
    q['delta']=.1;q['iv']=float('nan')
    other=menu(q,SimpleNamespace(spot=500.))
    assert first==other
    q['prior_delta']=.1
    assert menu(q,SimpleNamespace(spot=500.))==[]


def test_intrinsic_carry_uses_price_and_exposure_bound_without_inventing_greeks():
    from pead.earnings_options.earnings_carry import menu
    from types import SimpleNamespace
    date=pd.Timestamp('2005-01-03')
    q=pd.DataFrame(dict(date=[date],expiry=[date+pd.Timedelta(days=180)],optionid=[1],cp=['C'],
        strike=[400.],bid=[100.],ask=[102.],delta=[float('nan')],iv=[float('nan')],
        prior_volume=[100.],prior_oi=[1000.],standard_contract=[True]))
    choices=menu(q,SimpleNamespace(spot=500.),intrinsic=True)
    assert len(choices)==1
    assert choices[0][1].cashflow==-102.
    assert choices[0][1].legs[0].delta==1.
    q['bid']=89.;q['ask']=90.
    assert menu(q,SimpleNamespace(spot=500.),intrinsic=True)==[]
