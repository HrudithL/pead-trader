import pandas as pd

from pead.research.options_expiry_grid import (
    select_entry_grid, attach_exit_dates, scan_exit_quotes, aggregate_outcomes,
    load_underlying_spot, paired_cell_comparisons, portfolio_outcomes,
)
from pead.prep.options.lib.options import scan_year_for_keys


def test_entry_grid_uses_direction_delta_expiry_and_live_quotes():
    date = pd.Timestamp("2003-04-01")
    events = pd.DataFrame({"secid": [1, 2], "day0_date": [date, date],
                           "decile": [10, 1], "underlying_price": [100.0, 100.0]})
    raw = pd.DataFrame({
        "secid": [1, 1, 1, 1, 2, 2], "date": [date] * 6,
        "exdate": pd.to_datetime(["2003-06-20", "2003-06-20", "2003-08-15",
                                   "2003-08-15", "2003-06-20", "2003-08-15"]),
        "cp_flag": ["C", "C", "C", "C", "P", "P"],
        "delta": [0.48, 0.30, 0.51, 0.50, -0.49, -0.51],
        "best_bid": [1.0, 0.0, 1.0, 1.0, 1.0, 1.0],
        "best_offer": [1.2, 0.2, 1.1, 1.1, 1.2, 1.1],
        "open_interest": [200] * 6,
        "volume": [5] * 6,
        "strike_price": [100000] * 6,
        "optionid": [11, 12, 13, 14, 21, 22],
    })
    got = select_entry_grid(raw, events, dte_targets=(80, 140), delta_targets=(0.5,))
    assert set(got.cp_flag) == {"C", "P"}
    assert got.loc[(got.secid == 1) & (got.dte_target == 80), "optionid"].iloc[0] == 11
    assert got.loc[(got.secid == 1) & (got.dte_target == 140), "optionid"].iloc[0] == 14
    assert len(got) == 4


def test_exit_dates_reject_coverage_gap_and_expiry_buffer():
    cal = pd.DatetimeIndex(["2010-12-30", "2010-12-31", "2012-01-03", "2012-01-04"])
    candidates = pd.DataFrame({
        "secid": [1, 2, 3],
        "day0_date": pd.to_datetime(["2010-12-31", "2012-01-03", "2012-01-03"]),
        "exdate": pd.to_datetime(["2012-06-01", "2012-01-10", "2012-06-01"]),
    })
    got = attach_exit_dates(candidates, cal, holds=(1,), expiry_buffer_days=10)
    assert got.secid.tolist() == [3]
    assert got.exit_date.iloc[0] == pd.Timestamp("2012-01-04")


def test_exit_scan_matches_option_id_and_missing_quote_loses_premium(tmp_path):
    date = pd.Timestamp("2000-02-01")
    pd.DataFrame({"secid": [1, 1], "date": [date, date],
                  "optionid": [10, 11], "best_bid": [9.0, 3.0]}).to_parquet(
                      tmp_path / "opprcd2000.parquet")
    lookup = pd.DataFrame({"secid": [1, 1], "exit_date": [date, date],
                           "optionid": [11, 12]})
    quotes = scan_exit_quotes(2000, lookup, tmp_path)
    assert quotes.optionid.tolist() == [11]
    contracts = pd.DataFrame({
        "secid": [1, 1], "optionid": [11, 12],
        "day0_date": pd.to_datetime(["2000-01-03"] * 2),
        "exit_date": [date, date], "direction": ["C", "C"],
        "dte_target": [120, 120], "delta_target": [0.5, 0.5],
        "hold": [20, 20], "entry_ask": [2.0, 2.0],
        "open_interest": [100.0, 100.0],
    })
    summary, _ = aggregate_outcomes(contracts, quotes)
    assert summary.loc[0, "missing_bids"] == 1
    assert summary.loc[0, "net_pnl"] == -1000  # +$1 and -$2 across ten lots each


def test_entry_scanner_uses_explicit_drive_path(tmp_path):
    day = pd.Timestamp("2000-02-01")
    pd.DataFrame({"secid": [1], "date": [day], "optionid": [42]}).to_parquet(
        tmp_path / "opprcd2000.parquet")
    keys = pd.DataFrame({"secid": [1], "date": [day]})
    found = scan_year_for_keys(2000, keys, columns=["secid", "date", "optionid"],
                               optionmetrics_dir=tmp_path)
    assert found.optionid.tolist() == [42]


def test_spot_scan_covers_event_without_old_120d_chain(tmp_path):
    day = pd.Timestamp("2000-02-01")
    pd.DataFrame({"secid": [1, 2], "date": [day, day],
                  "close": [100.0, 50.0]}).to_parquet(tmp_path / "secprd2000.parquet")
    events = pd.DataFrame({"secid": [2], "day0_date": [day], "decile": [10]})
    found = load_underlying_spot(2000, events, tmp_path)
    assert found.underlying_price.tolist() == [50.0]


def test_delta_cell_rejects_distant_delta():
    day = pd.Timestamp("2003-04-01")
    events = pd.DataFrame({"secid": [1], "day0_date": [day],
                           "decile": [10], "underlying_price": [100.0]})
    raw = pd.DataFrame({
        "secid": [1], "date": [day], "exdate": [pd.Timestamp("2003-08-01")],
        "cp_flag": ["C"], "delta": [0.9], "best_bid": [1.0],
        "best_offer": [1.1], "open_interest": [200], "volume": [5],
        "strike_price": [100000], "optionid": [11],
    })
    got = select_entry_grid(raw, events, dte_targets=(120,), delta_targets=(0.3,))
    assert got.empty


def test_paired_comparison_uses_only_same_event_candidates():
    entries = pd.DataFrame({
        "secid": [1, 1, 2],
        "day0_date": pd.to_datetime(["2000-01-03"] * 3),
        "exit_date": pd.to_datetime(["2000-02-01"] * 3),
        "optionid": [11, 12, 21], "direction": ["C"] * 3,
        "dte_target": [120] * 3, "delta_target": [0.3, 0.5, 0.3],
        "hold": [20] * 3, "entry_ask": [2.0] * 3,
        "open_interest": [100] * 3,
    })
    quotes = pd.DataFrame({
        "secid": [1, 1, 2], "date": pd.to_datetime(["2000-02-01"] * 3),
        "optionid": [11, 12, 21], "best_bid": [4.0, 2.0, 5.0],
    })
    paired, _ = paired_cell_comparisons(entries, quotes)
    row = paired[(paired.axis == "delta_target") & (paired.first_cell == 0.3)
                 & (paired.second_cell == 0.5)].iloc[0]
    assert row.paired_events == 1
    assert row.mean_roi_difference == 1.0


def test_grid_portfolio_sells_missing_bid_at_zero_and_respects_oi_cap():
    cal = pd.date_range("2000-01-03", periods=3, freq="B")
    contracts = pd.DataFrame({
        "secid": [1, 2], "optionid": [11, 22],
        "day0_date": [cal[0], cal[0]], "exit_date": [cal[2], cal[2]],
        "direction": ["C", "C"], "dte_target": [75, 75],
        "delta_target": [0.7, 0.7], "hold": [2, 2],
        "entry_ask": [2.0, 2.0], "open_interest": [100, 100],
    })
    quotes = pd.DataFrame({
        "secid": [1], "date": [cal[2]], "optionid": [11], "best_bid": [3.0],
    })
    summary, yearly = portfolio_outcomes(contracts, quotes, cal)
    assert summary.loc[0, "funded_trades"] == 2
    assert summary.loc[0, "premium_paid"] == 4000
    assert summary.loc[0, "net_pnl"] == -1000
    assert summary.loc[0, "missing_bid_funded"] == 1
    assert yearly.loc[0, "net_pnl_realized"] == -1000
