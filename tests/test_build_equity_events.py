"""Tests for scripts/build_equity_events.py -- joining ibes_clean earnings
events to normalized_equity, day-0 alignment, and forward-return computation
(Phase I, Slice 8).
"""

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_equity_events as bee  # noqa: E402


# ---------------------------------------------------------------------------
# parse_permno_candidates / compute_permno_coverage
# ---------------------------------------------------------------------------


def test_parse_permno_candidates_single():
    assert bee.parse_permno_candidates("12345") == [12345]


def test_parse_permno_candidates_multi():
    assert bee.parse_permno_candidates("111,222,333") == [111, 222, 333]


def test_compute_permno_coverage_min_max_per_permno():
    equity = pd.DataFrame({
        "permno": [1, 1, 1, 2, 2],
        "date": pd.to_datetime(["2000-01-03", "2000-01-04", "2000-01-05", "2005-06-01", "2005-06-02"]),
    })
    coverage = bee.compute_permno_coverage(equity)
    assert coverage[1] == (pd.Timestamp("2000-01-03"), pd.Timestamp("2000-01-05"))
    assert coverage[2] == (pd.Timestamp("2005-06-01"), pd.Timestamp("2005-06-02"))


# ---------------------------------------------------------------------------
# resolve_permno -- identifier resolution (join-key handling).
# ---------------------------------------------------------------------------


def test_resolve_permno_single_candidate_ignores_coverage():
    # No coverage entry at all for 999 -- single candidates are used as-is;
    # a coverage gap surfaces later as a day0_status, not a resolution
    # failure.
    permno, ambiguous, status = bee.resolve_permno("999", pd.Timestamp("2005-01-01"), coverage={})
    assert (permno, ambiguous, status) == (999, False, "single_candidate")


def test_resolve_permno_multi_candidate_resolved_by_unique_bracketing_coverage():
    coverage = {
        111: (pd.Timestamp("1990-01-01"), pd.Timestamp("1995-01-01")),  # doesn't bracket
        222: (pd.Timestamp("2000-01-01"), pd.Timestamp("2010-01-01")),  # brackets
    }
    permno, ambiguous, status = bee.resolve_permno("111,222", pd.Timestamp("2005-01-01"), coverage)
    assert (permno, ambiguous, status) == (222, True, "resolved_by_coverage")


def test_resolve_permno_multi_candidate_zero_bracketing_unresolved():
    coverage = {
        111: (pd.Timestamp("1990-01-01"), pd.Timestamp("1995-01-01")),
        222: (pd.Timestamp("1996-01-01"), pd.Timestamp("1998-01-01")),
    }
    permno, ambiguous, status = bee.resolve_permno("111,222", pd.Timestamp("2005-01-01"), coverage)
    assert (permno, ambiguous, status) == (None, True, "unresolved_no_bracketing_coverage")


def test_resolve_permno_multi_candidate_multiple_bracketing_unresolved():
    coverage = {
        111: (pd.Timestamp("2000-01-01"), pd.Timestamp("2010-01-01")),
        222: (pd.Timestamp("2001-01-01"), pd.Timestamp("2009-01-01")),
    }
    permno, ambiguous, status = bee.resolve_permno("111,222", pd.Timestamp("2005-01-01"), coverage)
    assert (permno, ambiguous, status) == (None, True, "unresolved_multiple_bracketing_coverage")


def test_resolve_permno_candidate_missing_from_coverage_dict_is_not_bracketing():
    # 333 has no coverage entry at all (never appears in normalized_equity);
    # only 222 brackets -- must resolve to 222, not treat the missing
    # candidate as a KeyError or a silent match.
    coverage = {222: (pd.Timestamp("2000-01-01"), pd.Timestamp("2010-01-01"))}
    permno, ambiguous, status = bee.resolve_permno("333,222", pd.Timestamp("2005-01-01"), coverage)
    assert (permno, ambiguous, status) == (222, True, "resolved_by_coverage")


# ---------------------------------------------------------------------------
# find_day0_index -- day-0 alignment, including non-trading-day anndats.
# ---------------------------------------------------------------------------


_BUSINESS_DATES = pd.to_datetime(
    ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]
).to_numpy()  # skips the 2020-01-04/05 weekend


def test_find_day0_index_exact_trading_day_not_after_market():
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-03"), is_after_market=False)
    assert (idx, status) == (1, "ok")


def test_find_day0_index_non_trading_day_rolls_to_next_trading_day():
    # 2020-01-04 is a Saturday -- not in the panel at all.
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-04"), is_after_market=False)
    assert (idx, status) == (2, "ok")  # 2020-01-06, the next trading day


def test_find_day0_index_after_market_shifts_one_further_trading_day():
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-03"), is_after_market=True)
    assert (idx, status) == (2, "ok")  # 2020-01-06, not 2020-01-03


def test_find_day0_index_after_market_on_non_trading_day_also_shifts():
    # Baseline (not-after-market) roll for 01-04 lands on 01-06 (idx 2);
    # after-market shifts one further, to 01-07 (idx 3).
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-04"), is_after_market=True)
    assert (idx, status) == (3, "ok")


def test_find_day0_index_before_coverage_start_is_not_silently_matched_to_first_row():
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2015-01-01"), is_after_market=False)
    assert (idx, status) == (None, "anndats_before_permno_coverage_start")


def test_find_day0_index_after_last_row_is_unresolved():
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-09"), is_after_market=False)
    assert (idx, status) == (None, "no_trading_day_at_or_after_anndats")


def test_find_day0_index_empty_dates_is_unresolved():
    idx, status = bee.find_day0_index(np.array([], dtype="datetime64[ns]"), pd.Timestamp("2020-01-01"), False)
    assert (idx, status) == (None, "anndats_before_permno_coverage_start")


# ---------------------------------------------------------------------------
# forward_window_return -- compounding, end-of-panel, missing-return nulling.
# ---------------------------------------------------------------------------


def test_prefix_arrays_matches_manual_cumulative_product():
    ret = np.array([0.01, 0.02, -0.01, 0.03])
    P, nan_count = bee._prefix_arrays(ret)
    assert P[0] == 1.0
    assert P[1] == pytest.approx(1.01)
    assert P[2] == pytest.approx(1.01 * 1.02)
    assert P[4] == pytest.approx(1.01 * 1.02 * 0.99 * 1.03)
    assert nan_count.tolist() == [0, 0, 0, 0, 0]


def test_forward_window_return_matches_manual_compounding():
    ret = np.array([0.01, 0.02, -0.01, 0.03, 0.05, 0.00, 0.02, -0.02, 0.01, 0.04])
    prefix = bee._prefix_arrays(ret)
    n = len(ret)
    # day0 = index 2; horizon 1 -> just ret[3]; horizon 2 -> ret[3], ret[4].
    assert bee.forward_window_return(prefix, n, idx0=2, horizon=1) == pytest.approx(0.03)
    expected_h2 = (1.03 * 1.05) - 1
    assert bee.forward_window_return(prefix, n, idx0=2, horizon=2) == pytest.approx(expected_h2)


def test_forward_window_return_null_when_window_contains_nan():
    ret = np.array([0.01, 0.02, -0.01, 0.03, np.nan, 0.02])
    prefix = bee._prefix_arrays(ret)
    # window for idx0=1, horizon=3 covers indices 2,3,4 -- includes the NaN.
    result = bee.forward_window_return(prefix, len(ret), idx0=1, horizon=3)
    assert np.isnan(result)
    # A shorter horizon avoiding the NaN still computes.
    result_short = bee.forward_window_return(prefix, len(ret), idx0=1, horizon=2)
    assert result_short == pytest.approx((1 - 0.01) * 1.03 - 1)


def test_forward_window_return_null_at_end_of_panel():
    ret = np.array([0.01, 0.02, -0.01, 0.03])  # 4 rows: indices 0-3
    prefix = bee._prefix_arrays(ret)
    n = len(ret)
    # idx0=2: horizon 1 needs index 3 (exists) -> fine.
    assert not np.isnan(bee.forward_window_return(prefix, n, idx0=2, horizon=1))
    # horizon 2 needs index 4 (doesn't exist) -> null, not truncated.
    assert np.isnan(bee.forward_window_return(prefix, n, idx0=2, horizon=2))


# ---------------------------------------------------------------------------
# build_event_table -- integration: join, day0, forward returns, market
# adjustment, delisting-truncation, and "tag don't filter" row preservation.
# ---------------------------------------------------------------------------


def _synthetic_earnings(rows) -> pd.DataFrame:
    """rows: dicts with at least ibes_ticker/permno/anndats; other required
    columns default to harmless placeholders."""
    defaults = {
        "gvkey": "000001", "fpedats": pd.Timestamp("2005-01-01"), "anntims": dt.time(9, 0, 0),
        "sue_analyst": 1.0, "sue_seasonal": np.nan, "sue_primary": 1.0, "sue_source": "analyst",
    }
    out = []
    for row in rows:
        merged = {**defaults, **row}
        out.append(merged)
    return pd.DataFrame(out)


def _daily_series(permno, start, n_days, ret, mkt_ret) -> pd.DataFrame:
    """n_days consecutive business days for one permno, constant ret/mkt_ret
    (or an array of per-day values) -- enough to make expected cumulative
    returns easy to hand-compute."""
    dates = pd.bdate_range(start, periods=n_days)
    ret_arr = np.full(n_days, ret, dtype="float64") if np.isscalar(ret) else np.asarray(ret, dtype="float64")
    mkt_arr = np.full(n_days, mkt_ret, dtype="float64") if np.isscalar(mkt_ret) else np.asarray(mkt_ret, dtype="float64")
    return pd.DataFrame({"permno": permno, "date": dates, "ret": ret_arr, "mkt_ret": mkt_arr})


def test_build_event_table_forward_returns_and_market_adjustment():
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(9, 0, 0)},
    ])
    equity = _daily_series(100, "2005-01-03", 30, ret=0.01, mkt_ret=0.005)

    events, stats = bee.build_event_table(earnings, equity)
    row = events.iloc[0]

    assert row["permno"] == 100
    assert row["permno_resolution"] == "single_candidate"
    assert row["day0_status"] == "ok"
    assert row["day0_date"] == pd.Timestamp("2005-01-03")  # exact trading day, BMO time

    expected_raw_5d = 1.01**5 - 1
    expected_mkt_5d = 1.005**5 - 1
    assert row["ret_fwd_5d"] == pytest.approx(expected_raw_5d)
    assert row["ret_fwd_5d_mktadj"] == pytest.approx(expected_raw_5d - expected_mkt_5d)


def test_build_event_table_after_market_shifts_day0():
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(17, 0, 0)},
    ])
    equity = _daily_series(100, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)

    events, _ = bee.build_event_table(earnings, equity)
    row = events.iloc[0]
    assert row["day0_date"] == pd.Timestamp("2005-01-04")  # rolled forward one trading day


def test_build_event_table_delisting_truncation_nulls_only_the_long_horizons():
    # A short-lived permno: 25 trading days total, as if delisted right
    # after. day0 anchors at the very first row (index 0), so the forward
    # window can reach at most index 24 -- horizons <= 20 need index <= 20
    # (fits); horizons 40/60 need index 40/60 (do not exist).
    earnings = _synthetic_earnings([
        {"ibes_ticker": "BBB", "permno": "200", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(9, 0, 0)},
    ])
    equity = _daily_series(200, "2005-01-03", 25, ret=0.01, mkt_ret=0.0)

    events, stats = bee.build_event_table(earnings, equity)
    row = events.iloc[0]

    for h in (1, 5, 10, 20):
        assert not pd.isna(row[f"ret_fwd_{h}d"]), f"horizon {h} should still be computable"
    for h in (40, 60):
        assert pd.isna(row[f"ret_fwd_{h}d"]), f"horizon {h} should be null past the delisting point"
        assert pd.isna(row[f"ret_fwd_{h}d_mktadj"])


def test_build_event_table_keeps_unresolved_ambiguous_row_with_null_returns():
    # Neither 300 nor 400 appear in the equity panel at all -- zero
    # bracketing candidates, genuinely unresolvable.
    earnings = _synthetic_earnings([
        {"ibes_ticker": "CCC", "permno": "300,400", "anndats": pd.Timestamp("2005-01-03"), "sue_analyst": 2.5},
    ])
    equity = _daily_series(999, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)  # unrelated permno

    events, stats = bee.build_event_table(earnings, equity)
    assert len(events) == 1  # tag, don't filter -- row survives
    row = events.iloc[0]
    assert pd.isna(row["permno"])
    assert row["permno_resolution"] == "unresolved_no_bracketing_coverage"
    assert pd.isna(row["day0_date"])
    assert pd.isna(row["ret_fwd_1d"])
    assert row["sue_analyst"] == 2.5  # surprise measure preserved regardless


def test_build_event_table_permno_not_in_equity_panel_status():
    earnings = _synthetic_earnings([
        {"ibes_ticker": "DDD", "permno": "500", "anndats": pd.Timestamp("2005-01-03")},
    ])
    equity = _daily_series(999, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)

    events, _ = bee.build_event_table(earnings, equity)
    row = events.iloc[0]
    assert row["permno"] == 500  # single candidate, used as-is
    assert row["permno_resolution"] == "single_candidate"
    assert row["day0_status"] == "permno_not_in_equity_panel"
    assert pd.isna(row["ret_fwd_1d"])


def test_build_event_table_anndats_before_coverage_start():
    earnings = _synthetic_earnings([
        {"ibes_ticker": "EEE", "permno": "100", "anndats": pd.Timestamp("2000-01-01")},
    ])
    equity = _daily_series(100, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)

    events, _ = bee.build_event_table(earnings, equity)
    row = events.iloc[0]
    assert row["day0_status"] == "anndats_before_permno_coverage_start"
    assert pd.isna(row["day0_date"])


# ---------------------------------------------------------------------------
# load_earnings -- core-window filter.
# ---------------------------------------------------------------------------


def test_load_earnings_filters_to_core_window(tmp_path):
    df = pd.DataFrame({
        "ibes_ticker": ["A", "B", "C"],
        "permno": ["1", "2", "3"],
        "gvkey": ["000001", "000002", "000003"],
        "fpedats": pd.to_datetime(["1995-09-30", "2005-09-30", "2013-12-31"]),
        "anndats": pd.to_datetime(["1995-10-20", "2005-10-20", "2014-01-15"]),
        "anntims": [dt.time(9, 0, 0)] * 3,
        "sue_analyst": [1.0, 2.0, 3.0],
        "sue_seasonal": [np.nan, np.nan, np.nan],
        "sue_primary": [1.0, 2.0, 3.0],
        "sue_source": ["analyst"] * 3,
    })
    path = tmp_path / "ibes_clean.parquet"
    df.to_parquet(path, index=False)

    out = bee.load_earnings(path)
    assert out["ibes_ticker"].tolist() == ["B"]  # only the 2005 row is in [1996, 2013]


# ---------------------------------------------------------------------------
# load_equity_with_market -- DuckDB projection + date join across the
# multi-file equity_*.parquet glob.
# ---------------------------------------------------------------------------


def test_load_equity_with_market_joins_by_date_across_files(tmp_path):
    equity_dir = tmp_path / "normalized_equity"
    equity_dir.mkdir()
    eq1 = pd.DataFrame({
        "permno": [1, 1], "date": pd.to_datetime(["2005-01-03", "2005-01-04"]),
        "ticker": ["X", "X"], "ret": [0.01, 0.02], "retx": [0.01, 0.02],
        "prc": [10.0, 10.1], "prc_is_bidask_avg": [False, False],
        "shrout": [100.0, 100.0], "vol": [1000.0, 1000.0], "cfacpr": [1.0, 1.0], "cfacshr": [1.0, 1.0],
    })
    eq2 = pd.DataFrame({
        "permno": [2], "date": pd.to_datetime(["2006-01-03"]),
        "ticker": ["Y"], "ret": [0.03], "retx": [0.03],
        "prc": [20.0], "prc_is_bidask_avg": [False],
        "shrout": [200.0], "vol": [2000.0], "cfacpr": [1.0], "cfacshr": [1.0],
    })
    eq1.to_parquet(equity_dir / "equity_2005.parquet", index=False)
    eq2.to_parquet(equity_dir / "equity_2006.parquet", index=False)

    market = pd.DataFrame({
        "date": pd.to_datetime(["2005-01-03", "2005-01-04", "2006-01-03"]),
        "vwretd": [0.001, 0.002, 0.003], "sprtrn": [0.0011, 0.0021, 0.0031],
        "vwretx": [0.001, 0.002, 0.003], "ewretd": [0.001, 0.002, 0.003], "ewretx": [0.001, 0.002, 0.003],
    })
    market_path = tmp_path / "market_benchmark.parquet"
    market.to_parquet(market_path, index=False)

    out = bee.load_equity_with_market(equity_dir, market_path)
    out = out.sort_values(["permno", "date"]).reset_index(drop=True)

    assert list(out.columns) == ["permno", "date", "ret", "mkt_ret"]
    assert out.loc[0, ["permno", "ret", "mkt_ret"]].tolist() == [1, 0.01, 0.0011]
    assert out.loc[2, ["permno", "ret", "mkt_ret"]].tolist() == [2, 0.03, 0.0031]


# ---------------------------------------------------------------------------
# render_findings -- smoke test only (content details covered above).
# ---------------------------------------------------------------------------


def test_render_findings_smoke():
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-03")},
        {"ibes_ticker": "CCC", "permno": "300,400", "anndats": pd.Timestamp("2005-01-03")},
    ])
    equity = _daily_series(100, "2005-01-03", 30, ret=0.01, mkt_ret=0.005)
    events, stats = bee.build_event_table(earnings, equity)

    findings = bee.render_findings(events, stats)
    assert "# Equity event table" in findings
    assert "permno_resolution" in findings
    assert "day0_status" in findings
    assert "| None |" not in findings  # unresolved rows get a readable label, not a raw None repr


# ---------------------------------------------------------------------------
# main() -- end-to-end against a fully faked data/ tree under tmp_path.
# ---------------------------------------------------------------------------


def test_main_end_to_end_produces_expected_output(tmp_path, monkeypatch, capsys):
    earnings_path = tmp_path / "ibes_clean.parquet"
    equity_dir = tmp_path / "normalized_equity"
    equity_dir.mkdir()
    market_path = equity_dir / "market_benchmark.parquet"
    out_dir = tmp_path / "events"

    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-03")},
    ])
    earnings.to_parquet(earnings_path, index=False)

    equity = _daily_series(100, "2005-01-03", 30, ret=0.01, mkt_ret=0.0)
    equity = equity.drop(columns=["mkt_ret"])
    equity.to_parquet(equity_dir / "equity_2005.parquet", index=False)
    market = pd.DataFrame({
        "date": pd.bdate_range("2005-01-03", periods=30),
        "sprtrn": np.full(30, 0.005),
    })
    market.to_parquet(market_path, index=False)

    monkeypatch.setattr(bee, "EARNINGS_PATH", earnings_path)
    monkeypatch.setattr(bee, "NORMALIZED_EQUITY_DIR", equity_dir)
    monkeypatch.setattr(bee, "MARKET_BENCHMARK_PATH", market_path)
    monkeypatch.setattr(bee, "OUTPUT_DIR", out_dir)
    monkeypatch.setattr(bee, "OUTPUT_PATH", out_dir / "equity_events_1996_2013.parquet")
    monkeypatch.setattr(bee, "FINDINGS_PATH", out_dir / "FINDINGS.md")

    bee.main()

    out = pd.read_parquet(out_dir / "equity_events_1996_2013.parquet")
    assert len(out) == 1
    assert out.loc[0, "ret_fwd_5d"] == pytest.approx(1.01**5 - 1)
    assert (out_dir / "FINDINGS.md").exists()
    captured = capsys.readouterr()
    assert "Wrote 1 rows" in captured.out
