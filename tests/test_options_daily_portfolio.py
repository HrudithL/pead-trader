import pandas as pd

from pead.research.options_daily_portfolio import (
    build_grid_daily_path_lookup, run_daily_grid_portfolios, simulate_daily,
    run_daily_portfolio, simulate_sizing_sensitivity,
)


def _trades(first_wins=True):
    cal = pd.bdate_range("2000-01-03", periods=3)
    bids = [3.0, 0.0] if first_wins else [0.0, 3.0]
    trades = pd.DataFrame({
        "secid": [1, 2], "optionid": [11, 22], "entry_date": [cal[0]] * 2,
        "exit_date": [cal[2]] * 2, "entry_ask": [2.0] * 2,
        "entry_bid": [1.8] * 2, "entry_oi": [1000] * 2,
        "exit_bid": bids,
    })
    quotes = pd.DataFrame({
        "trade_id": [0, 1, 0, 1, 0, 1],
        "date": [cal[0], cal[0], cal[1], cal[1], cal[2], cal[2]],
        "best_bid": [1.8, 1.8, 2.4, 0.5, *bids],
    })
    return cal, trades, quotes


def test_daily_grid_scans_shared_contract_path_only_through_longest_hold():
    cal = pd.bdate_range("2000-01-03", periods=5)
    trades = pd.DataFrame({
        "secid": [1, 1, 2], "optionid": [11, 11, 22],
        "entry_date": [cal[0]] * 3,
        "exit_date": [cal[2], cal[3], cal[2]],
    })
    mapped, lookup = build_grid_daily_path_lookup(trades, cal)
    shared_path_ids = mapped.loc[:1, "path_id"].unique()
    assert len(shared_path_ids) == 1
    shared = lookup[lookup.path_id.eq(shared_path_ids[0])]
    assert shared.date.tolist() == list(cal[:4])
    assert lookup.path_id.nunique() == 2
    assert len(lookup) == 7


def test_daily_grid_passes_cell_hold_to_portfolio_risk_scaler(monkeypatch, tmp_path):
    cal = pd.bdate_range("2000-01-03", periods=5)
    normalized = tmp_path / "normalized_equity"
    normalized.mkdir()
    pd.DataFrame({"date": cal, "vwretd": 0.0}).to_parquet(
        normalized / "market_benchmark.parquet")
    trades = pd.DataFrame({
        "secid": [1], "optionid": [11], "entry_date": [cal[0]],
        "exit_date": [cal[2]], "entry_ask": [2.0], "entry_bid": [1.8],
        "entry_oi": [1000], "direction": ["C"], "dte_target": [75],
        "delta_target": [0.7], "hold": [2],
    })
    seen_holds = []

    def fake_scan(lookup, optionmetrics_dir):
        return lookup[["secid", "optionid", "date"]].drop_duplicates().assign(best_bid=1.8)

    def fake_simulate(trades, quotes, calendar, **kwargs):
        seen_holds.append(kwargs["hold"])
        daily = pd.DataFrame({
            "date": calendar, "nav": 1_000_000.0, "return": 0.0,
            "cash": 1_000_000.0, "option_bid_value": 0.0,
            "open_positions": 0, "outstanding_premium": 0.0,
        })
        return daily, {"funded_trades": 0, "net_pnl": 0.0, "cagr": 0.0,
                       "daily_sharpe_excess_rf": 0.0, "max_drawdown": 0.0}

    import pead.research.options_daily_portfolio as module
    monkeypatch.setattr(module, "scan_unique_daily_bid_quotes", fake_scan)
    monkeypatch.setattr(module, "simulate_daily", fake_simulate)
    monkeypatch.setattr(module, "load_french_daily", lambda directory: (
        pd.DataFrame({"date": cal, "RF": 0.0, "Mkt-RF": 0.0, "SMB": 0.0,
                      "HML": 0.0, "Mom": 0.0}), {"test": True}))
    monkeypatch.setattr(module, "year_block_sharpe_diagnostics", lambda *args, **kwargs: (
        pd.DataFrame({"cell_id": ["C_75_D0.70_H2"], "excess_rf_sharpe": [0.0],
                      "year_block_ci_low": [0.0], "year_block_ci_high": [0.0]}), {}))

    run_daily_grid_portfolios(trades, cal, tmp_path, tmp_path, tmp_path / "out")
    assert seen_holds == [2]


def test_same_day_allocation_is_order_invariant():
    cal, trades, quotes = _trades()
    a, am = simulate_daily(trades, quotes, cal, initial_capital=1000,
                           premium_cap=0.50, max_trade_fraction=1.0,
                           trailing_scaler=False)
    flipped = trades.iloc[::-1].reset_index(drop=True)
    flipped_quotes = quotes.copy()
    flipped_quotes.trade_id = 1 - flipped_quotes.trade_id
    b, bm = simulate_daily(flipped, flipped_quotes, cal, initial_capital=1000,
                           premium_cap=0.50, max_trade_fraction=1.0,
                           trailing_scaler=False)
    assert am["funded_trades"] == bm["funded_trades"] == 2
    assert a.nav.tolist() == b.nav.tolist()
    assert a.nav.iloc[-1] == 900.0


def test_entry_spread_is_marked_and_cost_reduces_nav():
    cal, trades, quotes = _trades()
    daily, summary = simulate_daily(trades, quotes, cal, initial_capital=1000,
                                    premium_cap=1.0, max_trade_fraction=1.0,
                                    trailing_scaler=False, cost_fraction=0.0)
    costly, _ = simulate_daily(trades, quotes, cal, initial_capital=1000,
                               premium_cap=1.0, max_trade_fraction=1.0,
                               trailing_scaler=False, cost_fraction=0.10)
    assert daily.nav.iloc[0] < 1000
    assert costly.nav.iloc[-1] < daily.nav.iloc[-1]
    assert summary["premium_paid"] > 0


def test_sizing_sensitivity_scales_trade_budget_and_premium_cap_without_borrowing():
    cal, trades, quotes = _trades()
    scenarios = simulate_sizing_sensitivity(
        trades, quotes, cal, multipliers=(1.0, 2.0), initial_capital=100_000,
    )

    assert [item[0] for item in scenarios] == [1.0, 2.0]
    assert scenarios[0][2]["premium_cap_fraction"] == 0.20
    assert scenarios[1][2]["premium_cap_fraction"] == 0.40
    assert scenarios[0][2]["premium_paid"] == 400.0
    assert scenarios[1][2]["premium_paid"] == 800.0


def test_sizing_sensitivity_rejects_borrowed_premium_cap():
    cal, trades, quotes = _trades()
    try:
        simulate_sizing_sensitivity(trades, quotes, cal, multipliers=(5.01,))
    except ValueError as exc:
        assert "cannot exceed 5" in str(exc)
    else:
        raise AssertionError("premium-risk scaling above 100% NAV must be rejected")


def test_missing_interim_bid_zero_versus_carry():
    cal, trades, quotes = _trades()
    quotes = quotes[~((quotes.trade_id == 0) & (quotes.date == cal[1]))]
    zero, zm = simulate_daily(trades, quotes, cal, initial_capital=1000,
                              premium_cap=1.0, max_trade_fraction=1.0,
                              trailing_scaler=False, missing_policy="zero")
    carry, cm = simulate_daily(trades, quotes, cal, initial_capital=1000,
                               premium_cap=1.0, max_trade_fraction=1.0,
                               trailing_scaler=False, missing_policy="carry")
    assert zero.nav.iloc[1] < carry.nav.iloc[1]
    assert zero.nav.iloc[-1] == carry.nav.iloc[-1]
    assert zm["missing_open_marks"] > 0


def test_non_option_session_carries_last_bid_without_counting_missing():
    cal = pd.bdate_range("2000-01-03", periods=3)
    trades = pd.DataFrame({
        "secid": [1], "optionid": [11], "entry_date": [cal[0]],
        "exit_date": [cal[2]], "entry_ask": [2.0],
        "entry_bid": [1.8], "entry_oi": [1000],
    })
    quotes = pd.DataFrame({
        "trade_id": [0, 0], "date": [cal[0], cal[2]],
        "best_bid": [1.8, 3.0],
    })
    daily, summary = simulate_daily(
        trades, quotes, cal, option_trading_dates=cal[[0, 2]],
        initial_capital=1000, premium_cap=1.0, max_trade_fraction=1.0,
        max_oi_fraction=1.0, trailing_scaler=False,
    )
    assert daily.option_bid_value.iloc[1] == daily.option_bid_value.iloc[0]
    assert summary["missing_open_marks"] == 0
    assert summary["non_option_session_position_days"] == 1

def test_common_window_cagr_includes_idle_time():
    cal, trades, quotes = _trades()
    long_cal = cal.append(pd.bdate_range("2000-01-06", "2001-01-05"))
    short, sm = simulate_daily(trades, quotes, cal, initial_capital=1000,
                               premium_cap=1.0, max_trade_fraction=1.0,
                               trailing_scaler=False)
    long, lm = simulate_daily(trades, quotes, long_cal, initial_capital=1000,
                              premium_cap=1.0, max_trade_fraction=1.0,
                              trailing_scaler=False)
    assert long.nav.iloc[-1] == short.nav.iloc[-1]
    assert abs(lm["cagr"]) < abs(sm["cagr"])


def test_daily_pipeline_runs_timing_adjustment_and_hold_sensitivities(monkeypatch, tmp_path):
    cal = pd.bdate_range("2000-01-03", periods=70)
    base = tmp_path / "normalized_equity"
    base.mkdir()
    pd.DataFrame({"date": cal, "vwretd": 0.0}).to_parquet(
        base / "market_benchmark.parquet")
    trade = pd.DataFrame({
        "secid": [1], "optionid": [11], "day0_date": [cal[0]],
        "entry_date": [cal[0]], "exit_date": [cal[40]], "hold": [40],
        "entry_ask": [2.0], "entry_bid": [1.8], "exit_bid": [3.0],
        "exit_bid_missing": [False], "entry_oi": [500.0], "decile": [9],
        "delta": [0.5], "dte": [120], "moneyness": [1.0],
        "spread_fraction": [0.1],
    })
    q = pd.DataFrame({
        "trade_id": 0, "date": cal[:41], "best_bid": [1.8] + [2.0] * 39 + [3.0],
        "best_offer": [2.0] + [2.2] * 39 + [3.2],
        "open_interest": 500.0, "cfadj": 1.0, "ss_flag": "0",
    })
    import pead.research.options_daily_portfolio as module
    monkeypatch.setattr(module, "scan_selected_daily_quotes", lambda lookup, directory: q)
    monkeypatch.setattr(module, "load_french_daily", lambda directory: (
        pd.DataFrame({"date": cal, "RF": 0.0001}), {"test": True}))
    out = tmp_path / "out"
    audit = run_daily_portfolio(trade, cal, tmp_path, tmp_path, out)
    scenarios = pd.read_csv(out / "daily_sensitivity.csv")
    assert audit["contract_level_quotes_persisted"] is False
    assert "next_session_entry_same_exit" in scenarios.scenario.tolist()
    assert "paired_hold_40" in scenarios.scenario.tolist()
    assert "paired_hold_50" not in scenarios.scenario.tolist()
    assert (out / "paired_hold_profile.csv").exists()
