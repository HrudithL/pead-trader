import pandas as pd

from pead.research.options_robustness import (
    prepare_selected_trades, prior_quarter_low_vol_cutoffs, simulate_trades,
    summarize_settled,
)


def test_prepare_rejects_gap_and_expiry_but_keeps_missing_bid_as_zero():
    calendar = pd.DatetimeIndex(["2010-12-30", "2010-12-31", "2012-01-03", "2012-01-04"])
    panel = pd.DataFrame({
        "secid": [1, 2, 3],
        "day0_date": pd.to_datetime(["2010-12-31", "2012-01-03", "2012-01-03"]),
        "kelly_optionid": [11, 22, 33],
        "kelly_side": ["C", "C", "C"],
        "kelly_growth": [0.1, 0.1, 0.1],
        "kelly_fwd_ask_0d": [2.0, 2.0, 2.0],
        "kelly_fwd_bid_1d": [1.0, float("nan"), 1.0],
        "kelly_fwd_mid_1d": [1.1, 1.1, 1.1],
    })
    chain = pd.DataFrame({
        "secid": [1, 2, 3],
        "day0_date": panel.day0_date,
        "optionid": [11, 22, 33],
        "exdate": pd.to_datetime(["2012-06-01", "2012-06-01", "2012-01-03"]),
        "strike": [105.0] * 3,
        "underlying_price": [100.0] * 3,
    })
    trades, audit = prepare_selected_trades(panel, chain, calendar, hold=1)
    assert len(trades) == 1
    assert trades.iloc[0].secid == 2
    assert trades.iloc[0].exit_bid == 0
    assert audit["coverage_gap"] == 1
    assert audit["missing_exit_bid"] == 1
    assert audit["expiry_before_exit"] == 1


def test_sequential_sizing_does_not_use_later_events():
    calendar = pd.date_range("2000-01-03", periods=8, freq="B")
    early = pd.DataFrame({
        "secid": [1], "optionid": [11], "entry_date": [calendar[0]],
        "exit_date": [calendar[2]], "entry_ask": [2.0], "exit_bid": [3.0],
    })
    later = pd.DataFrame({
        "secid": [2], "optionid": [22], "entry_date": [calendar[4]],
        "exit_date": [calendar[6]], "entry_ask": [2.0], "exit_bid": [1.0],
    })
    one, _ = simulate_trades(early, calendar, initial_capital=100_000, cap=0.2,
                             max_trade_frac=0.05)
    both, _ = simulate_trades(pd.concat([early, later]), calendar,
                              initial_capital=100_000, cap=0.2, max_trade_frac=0.05)
    assert one.loc[0, "contracts"] == both.loc[0, "contracts"]
    assert one.loc[0, "contracts"] > 0


def test_open_interest_caps_lots_without_future_volume():
    calendar = pd.date_range("2000-01-03", periods=4, freq="B")
    trade = pd.DataFrame({
        "secid": [1], "optionid": [11], "entry_date": [calendar[0]],
        "exit_date": [calendar[2]], "entry_ask": [2.0], "exit_bid": [3.0],
        "entry_oi": [20.0],
    })
    positions, _ = simulate_trades(trade, calendar, initial_capital=100_000,
                                    cap=0.2, max_trade_frac=0.05,
                                    max_oi_frac=0.10)
    assert positions.loc[0, "contracts"] == 2


def test_vol_cutoff_uses_only_strictly_earlier_quarters():
    quarters = [str(q) for q in pd.period_range("2000Q1", periods=10, freq="Q")]
    early = pd.DataFrame({"ann_quarter": quarters[:9], "tvol_60d": range(1, 10)})
    later = pd.concat([early, pd.DataFrame({"ann_quarter": [quarters[9]],
                                             "tvol_60d": [1000]})], ignore_index=True)
    a = prior_quarter_low_vol_cutoffs(early)
    b = prior_quarter_low_vol_cutoffs(later)
    assert a[quarters[8]] == b[quarters[8]]
    assert quarters[7] not in a.index


def test_same_close_entry_cannot_reuse_exit_proceeds():
    cal = pd.date_range("2000-01-03", periods=4, freq="B")
    trades = pd.DataFrame({
        "secid": [1, 2], "optionid": [11, 22],
        "entry_date": [cal[0], cal[2]], "exit_date": [cal[2], cal[3]],
        "entry_ask": [2.0, 2.0], "exit_bid": [3.0, 3.0],
    })
    positions, _ = simulate_trades(trades, cal, initial_capital=1000,
                                    cap=0.2, max_trade_frac=0.2)
    assert positions.contracts.tolist() == [1, 0]


def test_settlement_cagr_starts_with_first_funded_entry_not_calendar_warmup():
    positions = pd.DataFrame({
        "contracts": [1], "net_pnl": [100.0], "premium_paid": [100.0],
        "entry_date": [pd.Timestamp("2000-01-01")],
        "exit_date": [pd.Timestamp("2001-01-01")],
        "exit_bid": [2.0], "exit_bid_missing": [False],
    })
    daily = pd.DataFrame({
        "date": pd.to_datetime(["1990-01-01", "2001-01-01"]),
        "realized_pnl": [0.0, 100.0], "settled_nav": [1000.0, 1100.0],
    })
    got = summarize_settled(positions, daily)
    assert 0.09 < got["calendar_cagr_from_settlements"] < 0.11
