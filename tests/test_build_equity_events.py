"""Tests for scripts/build_equity_events.py -- joining ibes_clean earnings
events to normalized_equity, day-0 alignment, and forward-return computation
(Phase I, Slice 8).
"""

import datetime as dt
import sys
import warnings
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


def test_find_day0_index_after_market_on_non_trading_day_does_not_double_shift():
    # 2020-01-04 is a Saturday -- it has no session of its own to have
    # already closed, so the after-market shift must not apply on top of
    # the roll-forward: day 0 is 2020-01-06 (idx 2), the same as the
    # not-after-market case, not one session further. Regression test for a
    # real bug: the original implementation shifted unconditionally,
    # produce day 0 one session late whenever anndats fell on a
    # weekend/holiday/market closure (e.g. the 2001-09-11 closures).
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-04"), is_after_market=True)
    assert (idx, status) == (2, "ok")


def test_find_day0_index_before_coverage_start_is_not_silently_matched_to_first_row():
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2015-01-01"), is_after_market=False)
    assert (idx, status) == (None, "anndats_before_permno_coverage_start")


def test_find_day0_index_after_last_row_is_unresolved():
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-09"), is_after_market=False)
    assert (idx, status) == (None, "no_trading_day_at_or_after_anndats")


def test_find_day0_index_empty_dates_is_unresolved():
    idx, status = bee.find_day0_index(np.array([], dtype="datetime64[ns]"), pd.Timestamp("2020-01-01"), False)
    assert (idx, status) == (None, "anndats_before_permno_coverage_start")


def test_find_day0_index_panel_ends_before_anndats_is_cause_i_status():
    # Cause (i): the panel genuinely has no session at/after anndats --
    # searchsorted alone already runs off the end, before the after-market
    # shift is even considered. Kept distinct from cause (ii) below (the
    # panel HAD a valid session, but the after-market shift pushed past
    # it) -- same "no day 0" outcome, different root cause.
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-09"), is_after_market=True)
    assert (idx, status) == (None, "no_trading_day_at_or_after_anndats")


def test_find_day0_index_after_market_shift_past_end_of_panel_is_distinct_status():
    # Cause (ii): anndats lands exactly on the panel's LAST trading day,
    # after-market -- the roll-forward finds a valid session (the last
    # one), but the after-market shift then pushes one step past the end of
    # the panel. This must NOT collapse into "no_trading_day_at_or_after_
    # anndats" (cause i, tested above): that string means the panel never
    # had a session near anndats at all; this one means it did, and the
    # shift is what ran out of room.
    idx, status = bee.find_day0_index(_BUSINESS_DATES, pd.Timestamp("2020-01-08"), is_after_market=True)
    assert (idx, status) == (None, "after_market_shift_past_end_of_panel")


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


def test_forward_window_return_null_not_zero_division_warning_after_total_wipeout():
    # ret[1] = -1.0 (a -100% day) zeroes the running product from that
    # point on; any window entirely after it is a 0/0 ratio, not a real
    # percentage return. Dormant in today's data (no ret == -1.0 in
    # normalized_equity) but guards against a future delisting-return merge
    # introducing one. Uses pytest's default warning behavior -- a
    # RuntimeWarning here would fail this test under this project's
    # warnings-as-errors convention elsewhere.
    ret = np.array([0.01, -1.0, 0.02, 0.03, 0.05])
    prefix = bee._prefix_arrays(ret)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = bee.forward_window_return(prefix, len(ret), idx0=1, horizon=2)
    assert np.isnan(result)


# ---------------------------------------------------------------------------
# welch_group_stats -- numpy-only Welch's t-stat helper for the attrition
# disclosure (Decision 4). No scipy dependency (deliberately excluded from
# requirements.txt for this project).
# ---------------------------------------------------------------------------


def test_welch_group_stats_matches_manual_calculation():
    g1 = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    g2 = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    result = bee.welch_group_stats(g1, g2)
    var1, var2 = g1.var(ddof=1), g2.var(ddof=1)
    expected_t = (g1.mean() - g2.mean()) / np.sqrt(var1 / 5 + var2 / 5)
    assert result["n1"] == 5
    assert result["n2"] == 5
    assert result["mean1"] == pytest.approx(3.0)
    assert result["mean2"] == pytest.approx(6.0)
    assert result["t"] == pytest.approx(expected_t)


def test_welch_group_stats_none_t_when_a_group_is_empty():
    result = bee.welch_group_stats(np.array([1.0, 2.0, 3.0]), np.array([]))
    assert result["n2"] == 0
    assert result["mean2"] is None
    assert result["t"] is None


def test_welch_group_stats_none_t_when_a_group_has_fewer_than_two_obs():
    # n=1 -> sample variance (ddof=1) is undefined, not zero.
    result = bee.welch_group_stats(np.array([1.0, 2.0, 3.0]), np.array([5.0]))
    assert result["n2"] == 1
    assert result["mean2"] == pytest.approx(5.0)
    assert result["t"] is None


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


def test_build_event_table_market_leg_skips_calendar_gap_in_trading_history():
    # permno 700 trades three sessions, then halts for two-plus weeks (no
    # rows AT ALL over that stretch, not even non-trading placeholders),
    # then resumes. Across that gap, the benchmark leg must compound only
    # over the sessions this security actually traded -- never the
    # calendar days it didn't (there is no market return recorded for a day
    # this permno has no row for). Distinct ret/mkt_ret values at every
    # index make any off-by-one or gap-padding bug show up numerically.
    dates = pd.to_datetime([
        "2005-01-03", "2005-01-04", "2005-01-05",  # day0 + 2 sessions, then a halt
        "2005-01-20", "2005-01-21", "2005-01-24",  # resumes 15 calendar days later
    ])
    equity = pd.DataFrame({
        "permno": 700, "date": dates,
        "ret": [0.00, 0.01, 0.02, 0.03, 0.04, 0.05],
        "mkt_ret": [0.00, 0.005, 0.010, 0.015, 0.020, 0.025],
    })
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "700", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(9, 0, 0)},
    ])

    events, _ = bee.build_event_table(earnings, equity)
    row = events.iloc[0]
    assert row["day0_date"] == pd.Timestamp("2005-01-03")

    # Horizon 5 spans indices 1-5, i.e. the 2 pre-halt sessions AND the 3
    # post-halt sessions -- exactly the 5 rows this permno actually has
    # after day0, regardless of the 15 calendar days with no row at all in
    # between.
    expected_raw_5d = (1.01 * 1.02 * 1.03 * 1.04 * 1.05) - 1
    expected_mkt_5d = (1.005 * 1.010 * 1.015 * 1.020 * 1.025) - 1
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


def test_build_event_table_anntims_unknown_sentinel_defaults_to_after_market():
    # Same exact-trading-day anndats, one with a known before-market time
    # (day0 = anndats itself) and one with the 00:00:00 "unknown" sentinel
    # (day0 rolls one further session -- treated as after-market, per the
    # empirical review finding that most sentinel-time rows react at
    # day0+1, not day0).
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(9, 0, 0)},
        {"ibes_ticker": "BBB", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(0, 0, 0),
         "fpedats": pd.Timestamp("2005-04-01")},
    ])
    equity = _daily_series(100, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)

    events, _ = bee.build_event_table(earnings, equity)
    known = events[events["ibes_ticker"] == "AAA"].iloc[0]
    unknown = events[events["ibes_ticker"] == "BBB"].iloc[0]

    assert not bool(known["anntims_unknown"])
    assert bool(unknown["anntims_unknown"])
    assert known["day0_date"] == pd.Timestamp("2005-01-03")
    assert unknown["day0_date"] == pd.Timestamp("2005-01-04")  # rolled forward, treated as after-market


def test_build_event_table_n_anntims_pre_4am_excludes_sentinel_and_late_hours():
    # Exact 00:00:00 is the "unknown" sentinel, not pre-dawn. 00:00:01 and
    # 03:59:59 are the inclusive boundaries of the pre-dawn count. 04:00:00
    # and later are out of range.
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(0, 0, 0)},
        {"ibes_ticker": "BBB", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(0, 0, 1),
         "fpedats": pd.Timestamp("2005-04-01")},
        {"ibes_ticker": "CCC", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(3, 59, 59),
         "fpedats": pd.Timestamp("2005-07-01")},
        {"ibes_ticker": "DDD", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(4, 0, 0),
         "fpedats": pd.Timestamp("2005-10-01")},
    ])
    equity = _daily_series(100, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)

    _, stats = bee.build_event_table(earnings, equity)
    assert stats["n_anntims_pre_4am"] == 2  # BBB and CCC only


def test_build_event_table_n_fwd_sessions_tracks_remaining_panel():
    earnings = _synthetic_earnings([
        {"ibes_ticker": "BBB", "permno": "200", "anndats": pd.Timestamp("2005-01-03")},
    ])
    equity = _daily_series(200, "2005-01-03", 25, ret=0.01, mkt_ret=0.0)

    events, _ = bee.build_event_table(earnings, equity)
    row = events.iloc[0]
    assert row["n_fwd_sessions"] == 24  # 25 rows, day0 at index 0 -> 24 remain after it


def test_build_event_table_day0_lag_days_is_calendar_not_trading_gap():
    # anndats falls on a Saturday with no trading data of its own; day0
    # rolls forward to the following Monday -- a 2-CALENDAR-day lag despite
    # being the very next trading day (day0_lag_days is calendar days, not
    # a trading-day count).
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-08"), "anntims": dt.time(9, 0, 0)},
    ])
    equity = _daily_series(100, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)

    events, stats = bee.build_event_table(earnings, equity)
    row = events.iloc[0]
    assert row["day0_date"] == pd.Timestamp("2005-01-10")
    assert row["day0_lag_days"] == 2
    assert stats["max_day0_lag_days"] == 2
    assert stats["n_day0_lag_over_5d"] == 0


def test_build_event_table_day0_lag_days_null_when_day0_unresolved():
    earnings = _synthetic_earnings([
        {"ibes_ticker": "DDD", "permno": "500", "anndats": pd.Timestamp("2005-01-03")},
    ])
    equity = _daily_series(999, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)  # unrelated permno

    events, _ = bee.build_event_table(earnings, equity)
    assert pd.isna(events.iloc[0]["day0_lag_days"])


def test_build_event_table_n_events_same_permno_day0_flags_duplicates():
    # FFF and GGG both announce on the same day0 for the same permno (a
    # delinquent filer catching up several fiscal quarters at once); HHH is
    # an unrelated single event on a different day0.
    earnings = _synthetic_earnings([
        {"ibes_ticker": "FFF", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "fpedats": pd.Timestamp("2004-09-30")},
        {"ibes_ticker": "GGG", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "fpedats": pd.Timestamp("2004-12-31")},
        {"ibes_ticker": "HHH", "permno": "100", "anndats": pd.Timestamp("2005-01-05")},
    ])
    equity = _daily_series(100, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)

    events, stats = bee.build_event_table(earnings, equity)
    by_ticker = events.set_index("ibes_ticker")
    assert by_ticker.loc["FFF", "n_events_same_permno_day0"] == 2
    assert by_ticker.loc["GGG", "n_events_same_permno_day0"] == 2
    assert by_ticker.loc["HHH", "n_events_same_permno_day0"] == 1
    assert stats["n_duplicate_permno_day0"] == 2  # counts rows in a >1 group, not groups


def test_build_event_table_permno_contested_flags_overlapping_tickers_sharing_a_permno():
    # III and JJJ both resolve to permno 100 for the SAME announcement date
    # -- a real IBES<->CRSP link ambiguity this table can surface but not
    # arbitrate. KKK resolves to a different permno entirely and must not
    # be flagged.
    earnings = _synthetic_earnings([
        {"ibes_ticker": "III", "permno": "100", "anndats": pd.Timestamp("2005-01-03")},
        {"ibes_ticker": "JJJ", "permno": "100", "anndats": pd.Timestamp("2005-01-03"), "fpedats": pd.Timestamp("2005-06-30")},
        {"ibes_ticker": "KKK", "permno": "200", "anndats": pd.Timestamp("2005-01-03")},
    ])
    equity = pd.concat([
        _daily_series(100, "2005-01-03", 10, ret=0.01, mkt_ret=0.0),
        _daily_series(200, "2005-01-03", 10, ret=0.01, mkt_ret=0.0),
    ], ignore_index=True)

    events, stats = bee.build_event_table(earnings, equity)
    by_ticker = events.set_index("ibes_ticker")
    assert bool(by_ticker.loc["III", "permno_contested"]) is True
    assert bool(by_ticker.loc["JJJ", "permno_contested"]) is True
    assert bool(by_ticker.loc["KKK", "permno_contested"]) is False
    assert stats["n_permno_contested"] == 2


def test_flag_contested_permnos_does_not_flag_sequential_non_overlapping_tickers():
    # A ticker rename (LLL 1996-2000, then MMM 2001+ on the same permno) is
    # a normal, non-erroneous pattern and must NOT be flagged as contested.
    df = pd.DataFrame({
        "permno": pd.array([100, 100], dtype="Int64"),
        "ibes_ticker": ["LLL", "MMM"],
        "anndats": pd.to_datetime(["1997-01-15", "2005-01-15"]),
    })
    flagged = bee.flag_contested_permnos(df)
    assert not flagged.any()


# ---------------------------------------------------------------------------
# Attrition disclosure (Decision 4) -- population definition independent of
# ret_fwd_1d nullity, and the gated causal-claim wording in render_findings.
# ---------------------------------------------------------------------------


def _ret_array_with_day1_value(n_days: int, day1_value: float) -> np.ndarray:
    """All-zero except index 1 (the day0+1 return, i.e. what ret_fwd_1d
    reads) -- lets a test pin down ret_fwd_1d exactly while leaving every
    other index (irrelevant to the attrition tests) at a harmless 0%."""
    arr = np.zeros(n_days, dtype="float64")
    arr[1] = day1_value
    return arr


def _attrition_group_rows(tickers_and_permnos, n_days, ret1_values, sue_values):
    """Builds (earnings_rows, equity_frames) for a group of events sharing
    one n_days (panel length, hence one attrited/retained bucket), each
    with its own pinned ret_fwd_1d and sue_primary value."""
    earnings_rows = []
    equity_frames = []
    for (ticker, permno), ret1, sue in zip(tickers_and_permnos, ret1_values, sue_values):
        earnings_rows.append({
            "ibes_ticker": ticker, "permno": str(permno), "anndats": pd.Timestamp("2005-01-03"),
            "anntims": dt.time(9, 0, 0), "sue_primary": sue,
        })
        ret_arr = _ret_array_with_day1_value(n_days, ret1)
        equity_frames.append(_daily_series(permno, "2005-01-03", n_days, ret=ret_arr, mkt_ret=0.0))
    return earnings_rows, equity_frames


def test_build_event_table_attrited_count_independent_of_ret_fwd_1d_nullity():
    # BBB: genuinely short panel (n_fwd_sessions=9 < 60) -> attrited, with a
    # perfectly ordinary (non-null) +1d return.
    # CCC: genuinely FULL panel (n_fwd_sessions=69 >= 60) -> retained by the
    # n_fwd_sessions definition, but its day0+1 return happens to be missing
    # (NaN), so ret_fwd_1d is null. This row must NOT be counted as
    # attrited -- attrited/retained membership comes from day0_date/
    # n_fwd_sessions alone, never from whether ret_fwd_1d itself is null.
    earnings = _synthetic_earnings([
        {"ibes_ticker": "BBB", "permno": "200", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(9, 0, 0)},
        {"ibes_ticker": "CCC", "permno": "300", "anndats": pd.Timestamp("2005-01-03"), "anntims": dt.time(9, 0, 0)},
    ])
    equity_short = _daily_series(200, "2005-01-03", 10, ret=0.01, mkt_ret=0.0)  # n_fwd_sessions = 9
    ret_full = np.full(65, 0.01, dtype="float64")
    ret_full[1] = np.nan  # day0+1 return missing
    equity_full = _daily_series(300, "2005-01-03", 65, ret=ret_full, mkt_ret=0.0)  # n_fwd_sessions = 64
    equity = pd.concat([equity_short, equity_full], ignore_index=True)

    events, stats = bee.build_event_table(earnings, equity)
    by_ticker = events.set_index("ibes_ticker")
    assert by_ticker.loc["BBB", "n_fwd_sessions"] == 9
    assert by_ticker.loc["CCC", "n_fwd_sessions"] == 64
    assert pd.isna(by_ticker.loc["CCC", "ret_fwd_1d"])
    assert stats["n_attrited_before_max_horizon"] == 1  # BBB only, not CCC


def test_render_findings_near_equal_attrition_groups_makes_no_causal_claim():
    attrited_ids = [("A1", 8001), ("A2", 8002), ("A3", 8003), ("A4", 8004), ("A5", 8005)]
    retained_ids = [("R1", 8101), ("R2", 8102), ("R3", 8103), ("R4", 8104), ("R5", 8105)]
    a_rows, a_equity = _attrition_group_rows(
        attrited_ids, n_days=10,
        ret1_values=[0.010, 0.012, 0.008, 0.011, 0.009], sue_values=[1.0, 1.2, 0.8, 1.1, 0.9],
    )
    r_rows, r_equity = _attrition_group_rows(
        retained_ids, n_days=65,
        ret1_values=[0.0105, 0.0095, 0.0115, 0.0085, 0.0100], sue_values=[1.05, 0.95, 1.15, 0.85, 1.00],
    )
    earnings = _synthetic_earnings(a_rows + r_rows)
    equity = pd.concat(a_equity + r_equity, ignore_index=True)

    events, stats = bee.build_event_table(earnings, equity)
    assert stats["n_attrited_before_max_horizon"] == 5
    findings = bee.render_findings(events, stats)

    assert "not statistically distinguishable from noise at conventional levels" in findings
    assert "attrited events average lower" not in findings
    assert "attrited events average higher" not in findings


def test_render_findings_large_attrition_gap_makes_correctly_signed_causal_claim():
    attrited_ids = [("A1", 8201), ("A2", 8202), ("A3", 8203), ("A4", 8204), ("A5", 8205)]
    retained_ids = [("R1", 8301), ("R2", 8302), ("R3", 8303), ("R4", 8304), ("R5", 8305)]
    a_rows, a_equity = _attrition_group_rows(
        attrited_ids, n_days=10,
        ret1_values=[-0.10, -0.11, -0.09, -0.12, -0.08], sue_values=[-2.0, -2.1, -1.9, -2.2, -1.8],
    )
    r_rows, r_equity = _attrition_group_rows(
        retained_ids, n_days=65,
        ret1_values=[0.10, 0.11, 0.09, 0.12, 0.08], sue_values=[2.0, 2.1, 1.9, 2.2, 1.8],
    )
    earnings = _synthetic_earnings(a_rows + r_rows)
    equity = pd.concat(a_equity + r_equity, ignore_index=True)

    events, stats = bee.build_event_table(earnings, equity)
    assert abs(stats["t_ret_attrition_diff"]) >= 2.0
    assert abs(stats["t_sue_attrition_diff"]) >= 2.0
    assert stats["mean_ret_attrited"] < stats["mean_ret_retained"]
    assert stats["mean_sue_attrited"] < stats["mean_sue_retained"]
    findings = bee.render_findings(events, stats)

    # Attrited group's mean is lower for both metrics -> direction wording
    # must say "lower", derived from the observed sign, never "higher".
    assert "attrited events average lower" in findings
    assert "attrited events average higher" not in findings
    assert "not statistically distinguishable from noise at conventional levels" not in findings


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


def test_load_earnings_asserts_on_null_anndats(tmp_path):
    # ibes_clean parses anndats with errors="raise", so this should never
    # happen in practice -- a tripwire against .between() silently treating
    # a NaT as "outside the window" and dropping the row (violating "tag,
    # don't filter") rather than failing loudly.
    df = pd.DataFrame({
        "ibes_ticker": ["A"], "permno": ["1"], "gvkey": ["000001"],
        "fpedats": pd.to_datetime(["2005-09-30"]), "anndats": pd.to_datetime([pd.NaT]),
        "anntims": [dt.time(9, 0, 0)], "sue_analyst": [1.0], "sue_seasonal": [np.nan],
        "sue_primary": [1.0], "sue_source": ["analyst"],
    })
    path = tmp_path / "ibes_clean.parquet"
    df.to_parquet(path, index=False)

    with pytest.raises(AssertionError):
        bee.load_earnings(path)


def test_load_earnings_asserts_on_null_permno(tmp_path):
    # Same tripwire logic as the anndats one above, for permno: a null here
    # would blow up resolve_permno's .split(",") with an opaque
    # AttributeError rather than failing loudly at the load boundary.
    # Latent today (zero nulls in-window); guards against a future upstream
    # regression.
    df = pd.DataFrame({
        "ibes_ticker": ["A"], "permno": [None], "gvkey": ["000001"],
        "fpedats": pd.to_datetime(["2005-09-30"]), "anndats": pd.to_datetime(["2005-10-20"]),
        "anntims": [dt.time(9, 0, 0)], "sue_analyst": [1.0], "sue_seasonal": [np.nan],
        "sue_primary": [1.0], "sue_source": ["analyst"],
    })
    path = tmp_path / "ibes_clean.parquet"
    df.to_parquet(path, index=False)

    with pytest.raises(AssertionError):
        bee.load_earnings(path)


def test_load_earnings_asserts_on_null_anntims(tmp_path):
    # Same tripwire logic, for anntims: a null here would blow up the
    # after-market comparison (`anntims >= AFTER_MARKET_THRESHOLD`) with an
    # opaque TypeError rather than failing loudly at the load boundary.
    df = pd.DataFrame({
        "ibes_ticker": ["A"], "permno": ["1"], "gvkey": ["000001"],
        "fpedats": pd.to_datetime(["2005-09-30"]), "anndats": pd.to_datetime(["2005-10-20"]),
        "anntims": [None], "sue_analyst": [1.0], "sue_seasonal": [np.nan],
        "sue_primary": [1.0], "sue_source": ["analyst"],
    })
    path = tmp_path / "ibes_clean.parquet"
    df.to_parquet(path, index=False)

    with pytest.raises(AssertionError):
        bee.load_earnings(path)


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
    # mkt_ret comes from vwretd (MARKET_RET_COL), not sprtrn -- both total
    # return, matching `ret`'s own dividend-inclusive convention.
    assert out.loc[0, ["permno", "ret", "mkt_ret"]].tolist() == [1, 0.01, 0.001]
    assert out.loc[2, ["permno", "ret", "mkt_ret"]].tolist() == [2, 0.03, 0.003]


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


def test_render_findings_asserts_on_empty_events_table():
    # A single tripwire at the top of render_findings, covering every
    # divide-by-`n` site in the function (rows-with-day0 percentage,
    # per-horizon coverage percentages, etc.) rather than guarding each site
    # individually -- reported "fixed" after round 1 but only a different
    # tripwire (load_earnings's NaT/None guard) actually landed; this is
    # the one that actually covers render_findings's own division sites.
    earnings = _synthetic_earnings([
        {"ibes_ticker": "AAA", "permno": "100", "anndats": pd.Timestamp("2005-01-03")},
    ])
    equity = _daily_series(100, "2005-01-03", 30, ret=0.01, mkt_ret=0.005)
    events, stats = bee.build_event_table(earnings, equity)

    with pytest.raises(AssertionError):
        bee.render_findings(events.iloc[0:0], stats)


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
        "vwretd": np.full(30, 0.005),
        "sprtrn": np.full(30, 0.0049),  # present alongside vwretd, as in the real archive; unused (MARKET_RET_COL)
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
