import numpy as np
import pandas as pd
import pytest

from pead.earnings_options.structures import select_structure
from pead.earnings_options.engine import Config, run
from pead.earnings_options.validation import purged_folds, evaluate


def chain(date, sleeve="directional", cp="C"):
    d = pd.Timestamp(date)
    expiry = pd.Timestamp("2020-03-20")
    rows = [(1, "C", 100, .60, 3., 3.10), (2, "C", 105, .30, 1., 1.05),
            (3, "P", 100, -.60, 3., 3.10), (4, "P", 95, -.30, 1., 1.05)]
    return pd.DataFrame([dict(date=d, optionid=i, secid=7, cp=c, strike=k,
        delta=delta, bid=b, ask=a, expiry=expiry, prior_volume=1000,
        prior_oi=1000, iv=.4, standard_contract=True) for i,c,k,delta,b,a in rows])


def event(date="2020-02-03", **updates):
    d = pd.Timestamp(date)
    row = dict(event_id="e1", secid=7, sector="tech", entry_date=d,
        release_date=d-pd.Timedelta(days=2), rank=.99, reaction=.02,
        rv=.25, adv=1e8, prior_rank=np.nan, scheduled_date=pd.NaT,
        schedule_known=False, timing_verified=True, consensus_verified=True,
        identity_verified=True, corporate_actions_verified=True)
    row.update(directional_edge=.2,condor_edge=.2,reversal_edge=.2)
    row.update(updates)
    return pd.DataFrame([row])


def inputs(**updates):
    dates=pd.bdate_range("2020-02-03",periods=5)
    quotes=pd.concat([chain(d) for d in dates],ignore_index=True)
    return event(**updates),quotes,pd.DataFrame(dict(date=dates,benchmark=.001,rf=0.))


def test_vertical_uses_executable_debit_and_correct_put_order():
    c=select_structure(chain("2020-02-03"),"directional",1)
    p=select_structure(chain("2020-02-03"),"directional",-1)
    assert c.cashflow == pytest.approx(-2.1)
    assert c.max_loss == pytest.approx(2.1)
    assert [x.strike for x in p.legs] == [100,95]
    assert select_structure(chain("2020-02-03").assign(prior_volume=0),"directional") is None


def test_selector_is_order_invariant_and_uses_same_expiry():
    c=chain("2020-02-03")
    assert select_structure(c,"directional") == select_structure(c.sample(frac=1,random_state=3),"directional")
    c.loc[c.optionid==2,"expiry"]=pd.Timestamp("2020-03-21")
    assert select_structure(c,"directional") is None


def test_accounting_pays_spreads_fees_and_reconciles_cash():
    ev,q,b=inputs()
    result=run(ev,q,b,Config(initial_nav=100_000,hold_sessions=2,sleeves=("directional",)))
    t=result.trades.iloc[0]
    assert t.contracts == 1
    assert t.pnl == pytest.approx(-15-2*2*.65)
    assert result.daily.iloc[-1].nav == pytest.approx(100_000+t.pnl)
    assert result.daily.nav.min()<100_000
    assert (result.daily.free_cash>=0).all()


def test_unverified_timing_skips_in_strict_mode():
    ev,q,b=inputs(timing_verified=False)
    result=run(ev,q,b,Config(initial_nav=100_000,sleeves=("directional",)))
    assert result.trades.empty
    assert "unverified_provenance" in result.skips.reason.tolist()


def test_future_quotes_do_not_change_entry_or_size():
    ev,q,b=inputs()
    cfg=Config(initial_nav=100_000,hold_sessions=2,sleeves=("directional",))
    before=run(ev,q,b,cfg)
    q.loc[q.date>q.date.min(),["bid","ask"]]*=1.2
    after=run(ev,q,b,cfg)
    assert before.trades.iloc[0].contracts==after.trades.iloc[0].contracts
    assert before.trades.iloc[0].entry_cashflow==after.trades.iloc[0].entry_cashflow


def test_missing_short_quote_is_not_forward_filled():
    ev,q,b=inputs()
    q=q[~((q.date==b.date.iloc[1]) & (q.optionid==2))]
    result=run(ev,q,b,Config(initial_nav=100_000,hold_sessions=2,sleeves=("directional",)))
    assert result.diagnostics["missing_marks"]>0
    assert result.diagnostics["execution_valid"] is False
    assert result.daily.iloc[1].nav <= 100_000-210


def test_event_close_cannot_use_its_own_release_signal():
    ev,q,b=inputs()
    ev["release_date"]=ev.entry_date
    r=run(ev,q,b,Config(initial_nav=100_000,sleeves=("directional",)))
    assert r.trades.empty


def test_cash_book_risk_limits_apply_across_simultaneous_events():
    ev,q,b=inputs()
    ev=pd.concat([ev.assign(event_id=f"e{i}",secid=i,sector=str(i)) for i in range(1,15)])
    q=pd.concat([q.assign(secid=i,optionid=q.optionid+i*10) for i in range(1,15)])
    cfg=Config(initial_nav=100_000,aggregate_risk=.005,cohort_risk=.005,sleeves=("directional",))
    r=run(ev,q,b,cfg)
    assert r.daily.reserved.max()<=500


def test_purged_folds_never_train_on_unmatured_outcomes():
    e=pd.DataFrame(dict(entry_date=pd.date_range("2000-01-01",periods=150,freq="MS")))
    e["label_end"]=e.entry_date+pd.Timedelta(days=60)
    folds=purged_folds(e,train_years=5,validation_years=1,test_years=1)
    assert folds
    for f in folds:
        assert e.loc[f["train"],"label_end"].max()<e.loc[f["validation"],"entry_date"].min()
        assert e.loc[f["validation"],"label_end"].max()<e.loc[f["test"],"entry_date"].min()


def test_benchmark_proxy_can_never_pass_investment_gate():
    dates=pd.bdate_range("2000-01-01",periods=1000)
    daily=pd.DataFrame(dict(date=dates,ret=.001,benchmark=.0002,rf=0.))
    stats=evaluate(daily,execution_valid=True,benchmark_total_return=False,repetitions=50)
    assert not stats["accepted"]
    assert not stats["gates"]["benchmark_total_return"]


def test_bad_quotes_and_duplicate_keys_fail_closed():
    ev,q,b=inputs()
    with pytest.raises(ValueError,match="duplicate"):
        run(ev,pd.concat([q,q.iloc[:1]]),b,Config())


def test_unpriced_or_negative_edge_is_not_a_trade():
    ev,q,b=inputs(directional_edge=-.1)
    r=run(ev,q,b,Config(sleeves=("directional",)))
    assert r.trades.empty
    assert r.skips.reason.tolist()==["forecast_does_not_cover_option_cost"]


def test_historical_price_model_never_uses_unmatured_returns():
    from pead.earnings_options.economics import expected_edge
    s=select_structure(chain("2020-02-03"),"directional")
    h=pd.DataFrame(dict(rank=[.99]*60,reaction=[.02]*60,forward_return=[.1]*60,
        label_end=pd.Timestamp("2020-02-04")))
    assert np.isnan(expected_edge(s,100,.25,pd.Timestamp("2020-02-03"),h))
    h["label_end"]=pd.Timestamp("2020-01-01")
    first=expected_edge(s,100,.25,pd.Timestamp("2020-02-03"),h)
    assert np.isfinite(first)
    future=h.copy();future["label_end"]=pd.Timestamp("2020-02-04");future["forward_return"]=-.99
    assert expected_edge(s,100,.25,pd.Timestamp("2020-02-03"),pd.concat([h,future]))==pytest.approx(first)


def condor_chain(date):
    d=pd.Timestamp(date)
    expiry=pd.Timestamp("2020-02-28")
    rows=[(11,"P",85,-.075,.50,.52),(12,"P",90,-.175,1.,1.04),
          (13,"C",110,.175,1.,1.04),(14,"C",115,.075,.50,.52)]
    return pd.DataFrame([dict(date=d,optionid=i,secid=7,cp=c,strike=k,delta=de,
        bid=bi,ask=a,expiry=expiry,prior_volume=1000,prior_oi=1000,iv=.5,
        standard_contract=True) for i,c,k,de,bi,a in rows])


def test_condor_collateral_and_four_leg_fills():
    s=select_structure(condor_chain("2020-02-03"),"condor")
    assert s.cashflow==pytest.approx(.96)
    assert s.max_loss==pytest.approx(4.04)
    ev,q,b=inputs()
    q=pd.concat([condor_chain(d) for d in b.date])
    r=run(ev,q,b,Config(initial_nav=1_000_000,hold_sessions=2,sleeves=("condor",)))
    assert len(r.trades)==1
    t=r.trades.iloc[0]
    assert t.contracts==4
    assert r.daily.free_cash.min()>=0
    assert t.pnl==pytest.approx((-12-5.2)*4)


def test_model_black_scholes_put_call_parity():
    from pead.earnings_options.economics import option_value
    c=option_value(np.array([90,100,110]),100,.1,.25,"C")
    p=option_value(np.array([90,100,110]),100,.1,.25,"P")
    assert c-p==pytest.approx(np.array([-10,0,10]))


def test_invalid_config_and_nonfinite_benchmark_fail():
    with pytest.raises(ValueError):Config(aggregate_risk=-1)
    ev,q,b=inputs();b.loc[0,"benchmark"]=np.nan
    with pytest.raises(ValueError):run(ev,q,b)


def test_adapter_normalizes_identifier_types_and_lags_features(tmp_path):
    from pead.earnings_options.data import event_panel
    for p in ("events","earnings","metadata","raw_wrds/crsp"):(tmp_path/p).mkdir(parents=True,exist_ok=True)
    dates=pd.bdate_range("2000-01-03",periods=250)
    ev=pd.DataFrame(dict(permno=[7,7],fpedats=["1999-12-31","2000-03-31"],
        anndats=["2000-03-01","2000-06-01"],day0_date=["2000-03-01","2000-06-01"],
        sue_source="analyst",day0_status="ok",sue_analyst=[1.,2.],anntims_unknown=False))
    ev.to_parquet(tmp_path/"events/equity_events_1996_2013.parquet")
    earnings=ev[["permno","fpedats","anndats"]].assign(permno="7",consensus_statpers="2000-02-01",eps_basis_status="anndats_basis_confirmed")
    earnings.to_parquet(tmp_path/"earnings/ibes_clean_1995_2014.parquet")
    pd.DataFrame(dict(permno=["7"],secid=[70],permno_ambiguous=[False])).to_parquet(tmp_path/"metadata/resolved_universe_ids.parquet")
    stocks=pd.concat([pd.DataFrame(dict(permno=i,date=dates,ret=.001,prc=100.,vol=1e6,cfacshr=1.)) for i in (7,84398)])
    stocks.to_parquet(tmp_path/"raw_wrds/crsp/dsf_2000.parquet")
    e,b=event_panel(tmp_path)
    assert len(e)==2
    assert e.iloc[1]["rank"]==1.
    assert e.iloc[1].entry_date>e.iloc[1].release_date
    assert e.iloc[1].forward_return==pytest.approx(1.001**20-1)
    assert e.iloc[1].adv==1e8


def test_ambiguous_vendor_quotes_are_excluded_not_arbitrarily_selected():
    from pead.earnings_options.data import unambiguous_quotes
    c=chain("2020-02-03")
    a=unambiguous_quotes(pd.concat([c,c.iloc[:1].assign(bid=100)]))
    assert 1 not in a.optionid.tolist()
    assert len(a)==3


def test_registered_strategy_uses_its_own_ledger():
    from pead.strategies import registry
    ev,q,b=inputs()
    class Context:
        def earnings_options(self):return ev,q,b
    strategy=registry.get("selective_earnings_options")
    r=strategy.run(Context())
    assert r.name=="selective_earnings_options"
    assert r.meta["status"]=="research"
    assert not r.summary["investment_accepted"]
    assert np.isfinite(r.daily.daily_return).all()


def test_cached_smoke_sample_cannot_be_misreported_as_performance(tmp_path):
    import json
    from pead.earnings_options.cli import load_prepared
    (tmp_path/"prepared_2000.json").write_text(json.dumps(dict(max_events=10)))
    with pytest.raises(ValueError,match="smoke"):
        load_prepared(tmp_path,2000,2000)


def test_cli_rejects_final_holdout_during_development():
    from pead.earnings_options.cli import main
    with pytest.raises(SystemExit):main(["backtest","--start","2012","--end","2013"])


def test_known_monday_earnings_exit_is_previous_friday():
    ev,q,b=inputs(scheduled_date=pd.Timestamp("2020-02-10"),schedule_known=True)
    r=run(ev,q,b,Config(initial_nav=100_000,hold_sessions=30,sleeves=("directional",)))
    assert r.trades.iloc[0].exit_date==pd.Timestamp("2020-02-07")


def test_training_labels_keep_delisting_loss_and_use_market_sessions():
    from pead.earnings_options.labels import forward_labels
    dates=pd.bdate_range("2020-01-02",periods=5)
    e=pd.DataFrame(dict(permno=[7],entry_date=dates[:1]))
    stocks=pd.DataFrame(dict(permno=[7,7],date=dates[:2],ret=[0.,.1]))
    dl=pd.DataFrame(dict(permno=[7],dlstdt=dates[2:3],dlret=[-.5]))
    result=forward_labels(e,stocks,dates,dl,max_horizon=3)
    assert result.iloc[0].forward_return_3==pytest.approx(-.45)
    result=forward_labels(e,stocks,dates,max_horizon=3)
    assert np.isnan(result.iloc[0].forward_return_3)
    dl["dlret"]=pd.Series([pd.NA],dtype="Float64")
    result=forward_labels(e,stocks,dates,dl,max_horizon=3)
    assert np.isnan(result.iloc[0].forward_return_3)

