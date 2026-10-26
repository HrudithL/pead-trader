"""Adversarial accounting and causality tests for the new synthesis study."""
import numpy as np
import pandas as pd
import pytest

from pead.risk_research.policy import lagged_beta, hedge_menu, Policy
from pead.risk_research.ledger import Ledger


def chain(date='2005-01-03', expiry='2005-03-19'):
    date, expiry = pd.Timestamp(date), pd.Timestamp(expiry)
    return pd.DataFrame([
        dict(optionid=1, cp='P', strike=100., prior_delta=-.35, delta=-.35, bid=3., ask=3.1),
        dict(optionid=2, cp='P', strike=90., prior_delta=-.15, delta=-.15, bid=1., ask=1.1),
        dict(optionid=3, cp='P', strike=95., prior_delta=-.25, delta=-.25, bid=2., ask=2.1),
        dict(optionid=4, cp='P', strike=85., prior_delta=-.10, delta=-.10, bid=.5, ask=.6),
    ]).assign(date=date, expiry=expiry, prior_volume=1000., prior_oi=1000.,
              standard_contract=True, iv=.2, cfadj=1.)


def test_beta_is_lagged_and_does_not_use_future():
    idx=pd.bdate_range('2000-01-01', periods=100)
    m=pd.Series(np.sin(np.arange(100))*.01,index=idx); s=.4*m
    beta=lagged_beta(s,m,20,10)
    assert beta.iloc[:10].eq(0).all()
    assert beta.iloc[10] == pytest.approx(.4)
    changed=s.copy();changed.iloc[50:]=.5
    pd.testing.assert_series_equal(beta.iloc[:51],lagged_beta(changed,m,20,10).iloc[:51])
    with pytest.raises(ValueError):lagged_beta(s,m.iloc[::-1])


def test_hedge_menu_is_executable_deterministic_and_capacity_limited():
    q=chain(); menu=hedge_menu(q,commission=1.5)
    st,cap=menu[0]
    assert st.cashflow == pytest.approx(-1.6)
    assert st.max_loss == pytest.approx(1.6)
    assert [x.optionid for x in st.legs] == [3,4]
    assert cap==20
    assert hedge_menu(q.sample(frac=1,random_state=4),1.5)==menu
    assert hedge_menu(q.assign(prior_volume=0),1.5)==[]
    assert hedge_menu(q.assign(prior_delta=np.nan),1.5)==[]


def make_ledger(**kwargs):
    return Ledger(100_000,Policy(name='test',**kwargs))


def test_credit_roundtrip_cash_fees_and_full_width_collateral():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    book=make_ledger();book.begin(pd.Timestamp('2005-01-03'),0.,0.,q,{},100.)
    assert book.open_option(st,2,'carry',pd.Timestamp('2005-03-18'),q)==2
    assert book.cash==pytest.approx(100_000+380-6)
    assert book.collateral==2000
    book.close_option(book.options[0],q,'test')
    book.finish()
    assert book.cash==pytest.approx(100_000-40-12)
    assert book.nav==pytest.approx(book.cash)
    assert book.reconciliation()==pytest.approx(0,abs=1e-8)


def test_spread_settles_to_native_soq_without_quote_or_capacity():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    book=make_ledger(); book.begin(q.date.iloc[0],0,0,q,{},100.)
    book.open_option(st,1,'carry',pd.Timestamp('2005-03-18'),q);book.finish()
    book.begin(pd.Timestamp('2005-03-18'),0,0,q.iloc[:0],{},85.,{ex:85.})
    book.finish()
    assert not book.options
    assert book.nav==pytest.approx(100_000-810-6)
    assert book.reconciliation()==pytest.approx(0,abs=1e-8)
    assert book.missing_marks==0


def test_shared_leg_capacity_partial_exit_and_missing_quotes_persist():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    book=make_ledger();book.begin(q.date.iloc[0],0,0,q,{},100.)
    assert book.open_option(st,2,'carry',pd.Timestamp('2005-03-18'),q)==2
    book.finish();small=q.assign(prior_volume=20,prior_oi=1000)
    book.begin(pd.Timestamp('2005-01-04'),0,0,small,{},100.)
    book.options[0]['pending']='risk_off';book.close_option(book.options[0],small,'risk_off')
    assert book.options[0]['lots']==1
    assert book.open_option(st,1,'carry',pd.Timestamp('2005-03-18'),small)==0
    book.finish();book.begin(pd.Timestamp('2005-01-05'),0,0,q.iloc[:0],{},100.)
    assert book.options[0]['pending']=='risk_off'
    assert book.close_option(book.options[0],q.iloc[:0],'risk_off')==0
    assert book.missing_marks>0


def test_stock_cash_account_and_financing_are_not_nav_scaling():
    book=make_ledger(equity_scale=1.2)
    q=chain().iloc[:0]; book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    assert book.open_stock(7,10.,120_000,.001,pd.Timestamp('2005-01-05'),1e8)==120_000
    book.finish();book.begin(pd.Timestamp('2005-01-04'),.0001,.01,q,{7:.10},100.)
    assert book.stock_value==pytest.approx(132_000)
    assert book.financing==pytest.approx(20_120*(.0001+.02/252))
    book.finish();book.begin(pd.Timestamp('2005-01-05'),0,0,q,{7:0.},100.)
    book.finish()
    assert not book.stocks
    assert book.reconciliation()==pytest.approx(0,abs=1e-7)


def test_carry_limits_include_expiry_and_hedge_requirement():
    book=make_ledger(require_hedge=True,carry=True,hedge=True)
    q=chain();book.begin(q.date.iloc[0],0,0,q,{},100.)
    assert book.carry_room(q.expiry.iloc[0],.2)==0
    book.policy=Policy(name='test')
    assert book.carry_room(q.expiry.iloc[0],.2)==pytest.approx(5000)


def test_options_cannot_bypass_admission_cap_with_large_requested_lot_count():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    book=make_ledger();book.begin(q.date.iloc[0],0,0,q,{},100.)
    book.open_option(st,100,'carry',pd.Timestamp('2005-03-18'),q)
    risk=sum(p['risk_unit']*p['lots'] for p in book.options)
    assert risk<=book.previous_nav*.05


def test_annual_hedge_budget_does_not_recycle_sale_proceeds():
    q=chain(); st,_=hedge_menu(q)[0]
    book=make_ledger(hedge=True,annual_premium=.002)
    book.begin(q.date.iloc[0],0,0,q,{},100.)
    assert book.open_option(st,1,'hedge',pd.Timestamp('2005-03-18'),q)==1
    spent=book.premium_spent;book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{},100.)
    book.close_option(book.options[0],q,'roll')
    assert book.premium_spent==spent
    assert book.open_option(st,1,'hedge',pd.Timestamp('2005-03-18'),q)==0


def test_collateral_cannot_pay_for_an_unrelated_hedge():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    book=make_ledger(hedge=True);book.begin(q.date.iloc[0],0,0,q,{},100.)
    book.open_option(st,2,'carry',pd.Timestamp('2005-03-18'),q)
    book.cash=book.collateral+100
    hedge,_=hedge_menu(q)[0]
    assert book.open_option(hedge,1,'hedge',pd.Timestamp('2005-03-18'),q)==0


def test_report_metrics_include_first_return_and_cash_is_not_an_options_edge():
    from pead.risk_research.report import statistics
    dates=pd.bdate_range('2000-01-03',periods=252)
    d=pd.DataFrame(dict(date=dates,ret=.001,rf=.0001,benchmark=np.sin(np.arange(252))*.01))
    stats=statistics(d)
    assert stats['cagr']==pytest.approx(1.001**252-1)
    assert stats['max_drawdown']==0
    d['ret']=d.rf
    assert statistics(d)['excess_sharpe']==0


def test_paired_bootstrap_does_not_compare_different_dates():
    from pead.risk_research.report import paired_difference
    dates=pd.bdate_range('2000-01-03',periods=100)
    d=pd.DataFrame(dict(date=dates,ret=np.sin(np.arange(100))*.01,rf=0.,benchmark=0.))
    assert paired_difference(d,d,repetitions=20)['sharpe']['p025']==0
    with pytest.raises(ValueError):paired_difference(d,d.iloc[1:],repetitions=20)


def test_hedge_roll_preserves_protection_when_budget_cannot_replace_it():
    from pead.risk_research.study import manage_hedge
    from pead.earnings_options.index_settlement import credit_menu
    q=chain();st,cap=hedge_menu(q)[0]
    book=make_ledger(hedge=True,carry=True,require_hedge=True,annual_premium=.002)
    book.begin(q.date.iloc[0],0,0,q,{},100.)
    book.open_option(st,1,'hedge',pd.Timestamp('2005-03-18'),q);book.finish()
    book.begin(pd.Timestamp('2005-02-01'),0,0,q,{},100.);book.chain=q
    proposed=credit_menu(q,1.5)
    assert proposed
    manage_hedge(book,[(st,cap)],{st.expiry:(pd.Timestamp('2005-03-18'),85.)},pd.Timestamp('2005-12-31'),proposed)
    assert len(book.options)==1
    assert book.options[0]['entry_date']==pd.Timestamp('2005-01-03')


def test_stock_exits_and_entries_share_actual_lagged_capacity():
    book=make_ledger(equity_scale=1.)
    q=chain().iloc[:0]
    book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.open_stock(7,10.,1000,.001,pd.Timestamp('2005-01-04'),1e8);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{7:0.},100.,stock_capacity={7:200.})
    assert book.stock_value==pytest.approx(800.)
    assert book.open_stock(7,10.,100,.001,pd.Timestamp('2005-01-05'),1e8)==0


def test_funding_uses_unhaircut_lagged_rate():
    book=make_ledger(equity_scale=1.2);q=chain().iloc[:0]
    book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.open_stock(7,10,120000,.001,pd.Timestamp('2005-01-06'),1e8);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{7:0.},100.,funding_rf=.0002)
    assert book.financing==pytest.approx(20120*(.0002+.02/252))


def test_a_close_cannot_spend_another_positions_collateral_on_its_fee():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    book=make_ledger();book.begin(q.date.iloc[0],0,0,q,{},100.)
    book.open_option(st,1,'carry',pd.Timestamp('2005-03-18'),q)
    book.open_option(st,1,'carry',pd.Timestamp('2005-03-18'),q)
    book.cash=book.collateral+2
    q.loc[q.optionid.eq(1),'ask']=11.
    assert book.close_option(book.options[0],q,'risk_off')==0
    assert book.cash>=book.collateral


def test_completed_partial_risk_trim_does_not_liquidate_remaining_stock_next_day():
    book=make_ledger(equity_scale=1.);q=chain().iloc[:0]
    book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.open_stock(7,10,1000,0,pd.Timestamp('2005-01-10'),1e8)
    book.close_stock(book.stocks[0],'risk_trim',.2);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{7:0.},100.)
    assert book.stock_value==800


def test_stock_capacity_rounding_never_adds_a_share():
    book=make_ledger(equity_scale=1.);q=chain().iloc[:0]
    book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.open_stock(7,10,1000,0,pd.Timestamp('2005-01-04'),1e8);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{7:0.},100.,stock_capacity={7:70.})
    assert book.stock_value==930
    assert book.used_stock[7]<=70

