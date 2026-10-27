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


def test_remaining_loss_is_distinct_from_original_admission_loss():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    book=make_ledger();book.begin(q.date.iloc[0],0,0,q,{},100.)
    book.open_option(st,2,'carry',pd.Timestamp('2005-03-18'),q);book.finish()
    assert book.daily[-1]['carry_admission_risk']==pytest.approx(1632.)
    assert book.daily[-1]['carry_remaining_loss']==pytest.approx(1580.)


def test_later_quotes_cannot_revise_previous_ledger_observations():
    from pead.earnings_options.structures import Leg,Structure
    q=chain();ex=q.expiry.iloc[0]
    st=Structure('carry',(Leg(2,1,'P',90.,ex,1.,1.1,-.15,1000),
                           Leg(1,-1,'P',100.,ex,3.,3.1,-.35,1000)),1.9,8.1,ex)
    books=[make_ledger(),make_ledger()]
    for book in books:
        book.begin(q.date.iloc[0],0,0,q,{},100.)
        book.open_option(st,2,'carry',pd.Timestamp('2005-03-18'),q);book.finish()
    changed=q.copy();changed[['bid','ask']]*=1.8
    books[0].begin(pd.Timestamp('2005-01-04'),0,0,q,{},100.);books[0].finish()
    books[1].begin(pd.Timestamp('2005-01-04'),0,0,changed,{},100.);books[1].finish()
    assert books[0].daily[0]==books[1].daily[0]
    assert books[0].options[0]['lots']==books[1].options[0]['lots']


def test_stock_split_updates_share_units_before_capacity_rounding():
    book=make_ledger(equity_scale=1.);q=chain().iloc[:0]
    book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.open_stock(7,10,1000,0,pd.Timestamp('2005-01-10'),1e8);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{7:0.},100.,stock_prices={7:5.})
    assert book.stocks[0]['shares']==200
    assert book.stock_value==1000


def test_terminal_sale_removes_fractional_drip_dust():
    book=make_ledger(equity_scale=1.);q=chain().iloc[:0]
    book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.open_stock(7,10,10,0,pd.Timestamp('2005-01-04'),1e8)
    book.stocks[0]['shares']=1e-11;book.stocks[0]['value']=1e-10;book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{7:0.},100.)
    assert not book.stocks


def test_negligible_partial_trim_does_not_queue_a_zero_share_exit():
    book=make_ledger(equity_scale=1.);q=chain().iloc[:0]
    book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.open_stock(7,10,1000,0,pd.Timestamp('2005-01-10'),1e8)
    book.stock_capacity={7:1000.}
    book.close_stock(book.stocks[0],'risk_trim',1e-15);book.finish()
    assert not book.stocks[0].get('pending')
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{7:0.},100.)


def test_prior_iv_uses_only_adjacent_previous_session():
    from pead.risk_research.policy import attach_prior_iv
    cal=pd.bdate_range('2005-01-03',periods=4)
    q=pd.DataFrame(dict(date=[cal[0],cal[1],cal[3]],optionid=1,iv=[.2,.3,.8]))
    out=attach_prior_iv(q,cal)
    assert np.isnan(out.prior_iv.iloc[0])
    assert out.prior_iv.iloc[1]==.2
    assert np.isnan(out.prior_iv.iloc[2])
    q.loc[1,'iv']=9.
    assert attach_prior_iv(q,cal).prior_iv.iloc[1]==.2


def test_spy_short_proceeds_are_part_of_150pct_collateral():
    book=make_ledger(equity_scale=1.,linear_hedge=True)
    q=chain().iloc[:0];book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.rebalance_short(10_000,100.,1_000_000.)
    assert book.cash==pytest.approx(110_000-2)
    assert book.short_value==10_000
    assert book.collateral==15_000
    assert book.current_nav()==pytest.approx(100_000-2)


def test_spy_short_ex_dividend_drop_does_not_create_free_profit():
    book=make_ledger(equity_scale=1.,linear_hedge=True,short_borrow_rate=0.)
    q=chain().iloc[:0];book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.rebalance_short(10_000,100.,1_000_000.);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{},100.,short_observation=(0.,-.01,99.))
    assert book.short_value==9900
    assert book.short_dividends==100
    assert book.current_nav()==pytest.approx(99_998)
    book.rebalance_short(0.,99.,1_000_000.);book.finish()
    assert book.reconciliation()==pytest.approx(0,abs=1e-8)


def test_spy_split_changes_share_count_without_profit():
    book=make_ledger(equity_scale=1.,linear_hedge=True,short_borrow_rate=0.)
    q=chain().iloc[:0];book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.rebalance_short(10_000,100.,1_000_000.);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,0,q,{},100.,short_observation=(0.,0.,50.,.5))
    assert book.shorts[0]['shares']==200
    assert book.short_value==10000
    assert book.current_nav()==pytest.approx(99998)


def test_vendor_return_rounding_cannot_change_fixed_short_quantity():
    book=make_ledger(equity_scale=1.,linear_hedge=True,short_borrow_rate=0.)
    q=chain().iloc[:0];book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.rebalance_short(10000,100,1e6);book.finish()
    book.begin(pd.Timestamp('2005-01-04'),0,.010001,q,{},100.,short_observation=(.010001,.010001,101.,1.))
    assert book.shorts[0]['shares']==100
    assert book.short_value==10100


def test_short_admission_respects_margin_credit_limit():
    book=make_ledger(equity_scale=1.,linear_hedge=True,funding_limit=0.)
    q=chain().iloc[:0];book.begin(pd.Timestamp('2005-01-03'),0,0,q,{},100.)
    book.cash=1000
    book.rebalance_short(10000.,100.,1_000_000.)
    assert book.cash-book.collateral>=0
    assert book.short_value<=1900


def test_native_spot_date32_is_normalized(tmp_path):
    import datetime
    from pead.risk_research.study import spot_for_year
    pd.DataFrame(dict(secid=[108105],date=[datetime.date(2012,1,3)],close=[1250.])).to_parquet(tmp_path/'secprd2012.parquet')
    assert spot_for_year(tmp_path,2012,tmp_path)=={pd.Timestamp('2012-01-03'):1250.}

