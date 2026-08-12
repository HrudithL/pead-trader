"""Tests for scripts/clean_ibes_earnings.py -- cleaning/joining the WRDS
I/B/E/S pull into a point-in-time-safe earnings/surprise table.
"""

import datetime
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import clean_ibes_earnings as clean  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures/helpers.
# ---------------------------------------------------------------------------


def _empty_consensus() -> pd.DataFrame:
    """An empty, but correctly-typed, cleaned-consensus frame -- for tests
    that need to prove "no consensus available at all" behaves safely."""
    return pd.DataFrame({
        "ticker": pd.Series(dtype="object"),
        "statpers": pd.Series(dtype="datetime64[ns]"),
        "fpedats": pd.Series(dtype="datetime64[ns]"),
        "meanest": pd.Series(dtype="float64"),
        "medest": pd.Series(dtype="float64"),
        "stdev": pd.Series(dtype="float64"),
        "numest": pd.Series(dtype="float64"),
    })


def _panel(rows) -> pd.DataFrame:
    """rows: iterable of (ticker, fpedats_str, eps_actual)."""
    return pd.DataFrame(
        [{"ticker": t, "fpedats": pd.Timestamp(fp), "eps_actual": eps} for t, fp, eps in rows]
    )


# ---------------------------------------------------------------------------
# Date/time parsing.
# ---------------------------------------------------------------------------


def test_parse_date_converts_varchar_to_datetime():
    result = clean._parse_date(pd.Series(["2000-03-31", "2001-12-31"]))
    assert result.dtype.kind == "M"
    assert result.iloc[0] == pd.Timestamp("2000-03-31")


def test_parse_date_raises_loudly_on_malformed_input():
    with pytest.raises(ValueError):
        clean._parse_date(pd.Series(["2000-13-45"]))


def test_parse_time_converts_varchar_to_time_objects():
    result = clean._parse_time(pd.Series(["09:30:00", "16:01:00"]))
    assert result.iloc[0] == datetime.time(9, 30, 0)
    assert result.iloc[1] == datetime.time(16, 1, 0)


# ---------------------------------------------------------------------------
# select_point_in_time_consensus -- the load-bearing point-in-time filter.
# ---------------------------------------------------------------------------


def test_lookahead_consensus_snapshot_is_excluded():
    """The core requirement: a consensus row whose STATPERS is AFTER the
    announcement must never be selected, even though it is the most recent
    row available for that fiscal quarter."""
    keys = pd.DataFrame({
        "ticker": ["AAA"],
        "fpedats": [pd.Timestamp("2000-03-31")],
        "anndats": [pd.Timestamp("2000-04-15")],
    })
    consensus = pd.DataFrame({
        "ticker": ["AAA", "AAA"],
        "statpers": [pd.Timestamp("2000-04-10"), pd.Timestamp("2000-04-20")],  # one before, one AFTER anndats
        "fpedats": [pd.Timestamp("2000-03-31"), pd.Timestamp("2000-03-31")],
        "meanest": [1.00, 1.50],
        "medest": [1.00, 1.50],
        "stdev": [0.05, 0.05],
        "numest": [4.0, 4.0],
    })

    result = clean.select_point_in_time_consensus(keys, consensus)

    assert result.loc[0, "consensus_meanest"] == 1.00
    assert result.loc[0, "consensus_statpers"] == pd.Timestamp("2000-04-10")
    assert result.loc[0, "n_candidates"] == 2
    assert result.loc[0, "n_lookahead_excluded"] == 1


def test_statpers_equal_to_anndats_is_excluded_not_just_strictly_after():
    """STATPERS must be STRICTLY before anndats -- same-day is not safe
    enough (the plan's own wording: "strictly before")."""
    keys = pd.DataFrame({
        "ticker": ["AAA"],
        "fpedats": [pd.Timestamp("2000-03-31")],
        "anndats": [pd.Timestamp("2000-04-15")],
    })
    consensus = pd.DataFrame({
        "ticker": ["AAA"],
        "statpers": [pd.Timestamp("2000-04-15")],  # same day as anndats
        "fpedats": [pd.Timestamp("2000-03-31")],
        "meanest": [1.00],
        "medest": [1.00],
        "stdev": [0.05],
        "numest": [4.0],
    })

    result = clean.select_point_in_time_consensus(keys, consensus)

    assert pd.isna(result.loc[0, "consensus_meanest"])
    assert result.loc[0, "n_candidates"] == 1
    assert result.loc[0, "n_lookahead_excluded"] == 1


def test_all_candidates_lookahead_yields_null_not_a_fallback_pick():
    """If every available snapshot is a look-ahead violation, the result
    must be null -- never silently fall back to the closest one anyway."""
    keys = pd.DataFrame({
        "ticker": ["AAA"],
        "fpedats": [pd.Timestamp("2000-03-31")],
        "anndats": [pd.Timestamp("2000-04-15")],
    })
    consensus = pd.DataFrame({
        "ticker": ["AAA", "AAA"],
        "statpers": [pd.Timestamp("2000-04-16"), pd.Timestamp("2000-05-01")],
        "fpedats": [pd.Timestamp("2000-03-31"), pd.Timestamp("2000-03-31")],
        "meanest": [1.00, 1.10],
        "medest": [1.00, 1.10],
        "stdev": [0.05, 0.05],
        "numest": [4.0, 4.0],
    })

    result = clean.select_point_in_time_consensus(keys, consensus)

    assert pd.isna(result.loc[0, "consensus_meanest"])
    assert pd.isna(result.loc[0, "consensus_statpers"])
    assert result.loc[0, "n_candidates"] == 2
    assert result.loc[0, "n_lookahead_excluded"] == 2


def test_selects_most_recent_eligible_snapshot_not_earliest():
    keys = pd.DataFrame({
        "ticker": ["AAA"],
        "fpedats": [pd.Timestamp("2000-03-31")],
        "anndats": [pd.Timestamp("2000-04-15")],
    })
    consensus = pd.DataFrame({
        "ticker": ["AAA", "AAA", "AAA"],
        "statpers": [pd.Timestamp("2000-02-01"), pd.Timestamp("2000-03-15"), pd.Timestamp("2000-04-01")],
        "fpedats": [pd.Timestamp("2000-03-31")] * 3,
        "meanest": [0.80, 0.90, 1.00],
        "medest": [0.80, 0.90, 1.00],
        "stdev": [0.05, 0.05, 0.05],
        "numest": [4.0, 4.0, 4.0],
    })

    result = clean.select_point_in_time_consensus(keys, consensus)

    assert result.loc[0, "consensus_meanest"] == 1.00
    assert result.loc[0, "consensus_statpers"] == pd.Timestamp("2000-04-01")
    assert result.loc[0, "n_lookahead_excluded"] == 0


def test_no_matching_ticker_fpedats_in_consensus_returns_null_not_error():
    keys = pd.DataFrame({
        "ticker": ["ZZZ"],
        "fpedats": [pd.Timestamp("2000-03-31")],
        "anndats": [pd.Timestamp("2000-04-15")],
    })

    result = clean.select_point_in_time_consensus(keys, _empty_consensus())

    assert pd.isna(result.loc[0, "consensus_meanest"])
    assert result.loc[0, "n_candidates"] == 0
    assert result.loc[0, "n_lookahead_excluded"] == 0


def test_select_point_in_time_consensus_handles_duplicate_keys_independently():
    """Duplicate (ticker, fpedats) rows in `keys` (as resolve_actual_
    duplicates passes in before a single anndats is settled) must each get
    their own independent match, keyed off their OWN anndats."""
    keys = pd.DataFrame({
        "ticker": ["AAA", "AAA"],
        "fpedats": [pd.Timestamp("2000-03-31"), pd.Timestamp("2000-03-31")],
        "anndats": [pd.Timestamp("2000-04-15"), pd.Timestamp("2000-04-15")],
    })
    consensus = pd.DataFrame({
        "ticker": ["AAA"],
        "statpers": [pd.Timestamp("2000-04-01")],
        "fpedats": [pd.Timestamp("2000-03-31")],
        "meanest": [1.00],
        "medest": [1.00],
        "stdev": [0.05],
        "numest": [4.0],
    })

    result = clean.select_point_in_time_consensus(keys, consensus)

    assert len(result) == 2
    assert (result["consensus_meanest"] == 1.00).all()


# ---------------------------------------------------------------------------
# clean_actuals.
# ---------------------------------------------------------------------------


def _raw_actu_row(ticker="AAA", anndats="2000-04-15", value=1.0, pends="2000-03-31",
                   anntims="09:00:00", curr_act="USD", cusip=None, oftic=None, cname=None):
    return {
        "ticker": ticker, "anndats": anndats, "value": value, "cusip": cusip,
        "oftic": oftic, "cname": cname, "pends": pends, "anntims": anntims, "curr_act": curr_act,
    }


def test_clean_actuals_drops_non_usd_and_null_value_rows():
    raw = pd.DataFrame([
        _raw_actu_row(ticker="AAA", value=1.00, curr_act="USD"),
        _raw_actu_row(ticker="AAA", value=1.10, curr_act="CAD"),
        _raw_actu_row(ticker="BBB", value=None, curr_act="USD"),
    ])

    cleaned, stats = clean.clean_actuals(raw)

    assert stats["non_usd_rows_dropped"] == 1
    assert stats["null_actual_eps_rows_dropped"] == 1
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["ticker"] == "AAA"
    assert cleaned.iloc[0]["eps_actual"] == 1.00
    assert cleaned.iloc[0]["fpedats"] == pd.Timestamp("2000-03-31")


def test_clean_actuals_drops_exact_duplicates_ignoring_anntims():
    raw = pd.DataFrame([
        _raw_actu_row(anntims="09:00:00", cusip="123"),
        _raw_actu_row(anntims="10:30:00", cusip="456"),  # anntims/cusip differ, everything else identical
    ])

    cleaned, stats = clean.clean_actuals(raw)

    assert len(cleaned) == 1
    assert stats["exact_duplicate_rows_dropped"] == 1


def test_clean_actuals_pre_1995_pends_are_counted_not_dropped():
    raw = pd.DataFrame([
        _raw_actu_row(ticker="AAA", anndats="1996-01-15", pends="1994-12-31", value=0.50),
    ])

    cleaned, stats = clean.clean_actuals(raw)

    assert stats["pre_1995_pends_rows"] == 1
    assert len(cleaned) == 1  # kept, not dropped


# ---------------------------------------------------------------------------
# clean_consensus.
# ---------------------------------------------------------------------------


def _raw_consensus_row(ticker="AAA", statpers="2000-04-01", fpedats="2000-03-31", fpi="6",
                        measure="EPS", fiscalp="QTR", curcode="USD", meanest=1.0, medest=1.0,
                        stdev=0.1, numest=5.0):
    return {
        "ticker": ticker, "statpers": statpers, "fpedats": fpedats, "fpi": fpi,
        "measure": measure, "fiscalp": fiscalp, "curcode": curcode,
        "meanest": meanest, "medest": medest, "stdev": stdev, "numest": numest,
    }


def test_clean_consensus_filters_to_fpi6_qtr_eps_and_usd():
    raw = pd.DataFrame([
        _raw_consensus_row(fpi="6", curcode="USD", meanest=1.0),   # keep
        _raw_consensus_row(fpi="1", fiscalp="ANN", meanest=4.0),   # drop: not fpi=6/QTR
        _raw_consensus_row(fpi="6", curcode="CAD", meanest=1.1),   # drop: non-USD
        _raw_consensus_row(fpi="6", curcode="USD", meanest=1.2, statpers="2000-05-01"),  # keep
    ])

    cleaned, stats = clean.clean_consensus(raw)

    assert stats["fpi6_qtr_eps_rows"] == 3
    assert stats["non_usd_rows_dropped"] == 1
    assert len(cleaned) == 2
    assert set(cleaned["meanest"]) == {1.0, 1.2}


def test_clean_consensus_drops_duplicate_snapshots():
    raw = pd.DataFrame([
        _raw_consensus_row(meanest=1.0),
        _raw_consensus_row(meanest=1.0),  # identical (ticker, statpers, fpedats)
    ])

    cleaned, stats = clean.clean_consensus(raw)

    assert len(cleaned) == 1
    assert stats["duplicate_snapshot_rows_dropped"] == 1


# ---------------------------------------------------------------------------
# resolve_actual_duplicates.
# ---------------------------------------------------------------------------


def _cleaned_actuals(rows):
    """rows: iterable of (ticker, fpedats_str, anndats_str, eps_actual)."""
    return pd.DataFrame([
        {"ticker": t, "fpedats": pd.Timestamp(fp), "anndats": pd.Timestamp(ad),
         "anntims": datetime.time(9, 0, 0), "eps_actual": eps}
        for t, fp, ad, eps in rows
    ])


def test_resolve_actual_duplicates_keeps_earliest_anndats_for_restatement():
    actuals = _cleaned_actuals([
        ("AAA", "2000-03-31", "2000-04-15", 1.00),
        ("AAA", "2000-03-31", "2000-04-20", 1.05),  # later restatement
    ])

    resolved, stats = clean.resolve_actual_duplicates(actuals, _empty_consensus())

    assert len(resolved) == 1
    assert resolved.iloc[0]["anndats"] == pd.Timestamp("2000-04-15")
    assert resolved.iloc[0]["eps_actual"] == 1.00
    assert stats["rows_dropped_for_later_anndats"] == 1


def test_resolve_actual_duplicates_disambiguates_via_consensus_proximity():
    """Simulates the real quarterly-vs-annual conflation: two EPS values
    under the same (ticker, fpedats, anndats). The quarterly consensus
    (which only ever forecasts the quarterly figure) should identify the
    quarterly one even though it is not the smaller-magnitude candidate in
    this example."""
    actuals = _cleaned_actuals([
        ("AAA", "2000-12-31", "2001-01-15", 0.85),  # e.g. the annual figure
        ("AAA", "2000-12-31", "2001-01-15", 0.20),  # e.g. the quarterly figure
    ])
    consensus = pd.DataFrame({
        "ticker": ["AAA"],
        "statpers": [pd.Timestamp("2001-01-10")],
        "fpedats": [pd.Timestamp("2000-12-31")],
        "meanest": [0.22],
        "medest": [0.22],
        "stdev": [0.03],
        "numest": [4.0],
    })

    resolved, stats = clean.resolve_actual_duplicates(actuals, consensus)

    assert len(resolved) == 1
    assert resolved.iloc[0]["eps_actual"] == 0.20
    assert resolved.iloc[0]["eps_actual_dup_flag"] == "consensus_disambiguated"
    assert stats["ambiguous_resolved_via_consensus"] == 1
    assert stats["ambiguous_resolved_via_magnitude_fallback"] == 0


def test_resolve_actual_duplicates_falls_back_to_magnitude_without_consensus():
    actuals = _cleaned_actuals([
        ("AAA", "2000-12-31", "2001-01-15", 0.85),
        ("AAA", "2000-12-31", "2001-01-15", 0.20),
    ])

    resolved, stats = clean.resolve_actual_duplicates(actuals, _empty_consensus())

    assert len(resolved) == 1
    assert resolved.iloc[0]["eps_actual"] == 0.20
    assert resolved.iloc[0]["eps_actual_dup_flag"] == "magnitude_fallback"
    assert stats["ambiguous_resolved_via_magnitude_fallback"] == 1


def test_resolve_actual_duplicates_marks_unique_rows_as_unique():
    actuals = _cleaned_actuals([("AAA", "2000-03-31", "2000-04-15", 1.00)])

    resolved, _stats = clean.resolve_actual_duplicates(actuals, _empty_consensus())

    assert resolved.iloc[0]["eps_actual_dup_flag"] == "unique"


def test_resolve_actual_duplicates_never_leaves_duplicates_behind():
    actuals = _cleaned_actuals([
        ("AAA", "2000-03-31", "2000-04-15", 1.00),   # unique
        ("BBB", "2000-12-31", "2001-01-15", 0.85),   # ambiguous, no consensus
        ("BBB", "2000-12-31", "2001-01-15", 0.20),
        ("CCC", "2000-06-30", "2000-07-10", 0.50),   # restatement
        ("CCC", "2000-06-30", "2000-07-20", 0.55),
    ])

    resolved, stats = clean.resolve_actual_duplicates(actuals, _empty_consensus())

    assert resolved.duplicated(["ticker", "fpedats"]).sum() == 0
    assert stats["rows_after_dedup"] == 3


# ---------------------------------------------------------------------------
# compute_sue_analyst.
# ---------------------------------------------------------------------------


def test_compute_sue_analyst_basic_calculation():
    result = clean.compute_sue_analyst(pd.Series([1.10]), pd.Series([1.00]), pd.Series([0.05]))
    assert result.iloc[0] == pytest.approx(2.0)


def test_compute_sue_analyst_null_stdev_yields_null():
    result = clean.compute_sue_analyst(pd.Series([1.10]), pd.Series([1.00]), pd.Series([np.nan]))
    assert pd.isna(result.iloc[0])


def test_compute_sue_analyst_zero_stdev_yields_null_not_inf():
    result = clean.compute_sue_analyst(pd.Series([1.10]), pd.Series([1.00]), pd.Series([0.0]))
    assert pd.isna(result.iloc[0])
    assert not np.isinf(result.iloc[0]) if not pd.isna(result.iloc[0]) else True


def test_compute_sue_analyst_floating_point_near_zero_stdev_yields_null():
    """Regression test for a real bug found against production data: a
    std dev that is mathematically zero but represented as a tiny nonzero
    float (catastrophic cancellation) must not produce a nonsense
    multi-trillion surprise."""
    result = clean.compute_sue_analyst(pd.Series([1.10]), pd.Series([1.00]), pd.Series([2.775558e-17]))
    assert pd.isna(result.iloc[0])


# ---------------------------------------------------------------------------
# compute_sue_seasonal.
# ---------------------------------------------------------------------------


def test_compute_sue_seasonal_requires_exact_year_ago_match():
    panel = _panel([
        ("AAA", "2000-03-31", 1.00),
        ("AAA", "2000-06-30", 1.10),  # no 1999 data at all -- no year-ago match
    ])

    result = clean.compute_sue_seasonal(panel)

    assert result.loc[result["fpedats"] == pd.Timestamp("2000-06-30"), "eps_year_ago"].isna().all()
    assert result.loc[result["fpedats"] == pd.Timestamp("2000-06-30"), "sue_seasonal"].isna().all()


def test_compute_sue_seasonal_min_history_gate():
    eps_values = [1.00, 1.05, 0.98, 1.10, 1.20, 1.12, 1.30, 1.25, 1.40, 1.35, 1.50, 1.45]
    quarters = pd.date_range("2000-03-31", periods=len(eps_values), freq="QE")
    panel = _panel([("AAA", q.strftime("%Y-%m-%d"), v) for q, v in zip(quarters, eps_values)])

    result = clean.compute_sue_seasonal(panel, min_history=4).sort_values("fpedats").reset_index(drop=True)

    # Diffs first become available at index 4 (year 2); the first 4 valid
    # diffs accumulate by index 7, so only from index 8 onward is there
    # enough PRIOR history (>=4 valid prior diffs) to compute a std dev.
    assert result.loc[:7, "sue_seasonal"].isna().all()
    assert result.loc[8:, "sue_seasonal"].notna().all()


def test_compute_sue_seasonal_scale_excludes_current_quarters_own_diff():
    """No self-referential leakage: the scaling std dev for a quarter must
    come only from PRIOR quarters, never from that quarter's own diff."""
    base_eps = [1.00, 1.05, 0.98, 1.10, 1.20, 1.12, 1.30, 1.25, 1.40, 1.35]
    quarters = pd.date_range("2000-03-31", periods=len(base_eps), freq="QE")

    def make_panel(eps_values):
        return _panel([("AAA", q.strftime("%Y-%m-%d"), v) for q, v in zip(quarters, eps_values)])

    modified = list(base_eps)
    modified[-1] = 999.0  # blow up only the LAST quarter's own actual EPS

    result_a = clean.compute_sue_seasonal(make_panel(base_eps)).sort_values("fpedats").reset_index(drop=True)
    result_b = clean.compute_sue_seasonal(make_panel(modified)).sort_values("fpedats").reset_index(drop=True)

    last = len(base_eps) - 1
    assert result_a.loc[last, "seasonal_diff_stdev"] == pytest.approx(result_b.loc[last, "seasonal_diff_stdev"])
    # sanity: the modification actually changed that row's own diff
    assert result_a.loc[last, "eps_diff_yoy"] != result_b.loc[last, "eps_diff_yoy"]


def test_compute_sue_seasonal_future_quarters_never_affect_past_rows():
    base_eps = [1.00, 1.05, 0.98, 1.10, 1.20, 1.12, 1.30, 1.25]
    quarters = pd.date_range("2000-03-31", periods=len(base_eps), freq="QE")

    def make_panel(eps_values):
        return _panel([("AAA", q.strftime("%Y-%m-%d"), v) for q, v in zip(quarters, eps_values)])

    modified = list(base_eps)
    modified[-1] = -999.0  # blow up only the LAST (most future) quarter

    result_a = clean.compute_sue_seasonal(make_panel(base_eps)).sort_values("fpedats").reset_index(drop=True)
    result_b = clean.compute_sue_seasonal(make_panel(modified)).sort_values("fpedats").reset_index(drop=True)

    pd.testing.assert_frame_equal(result_a.iloc[:-1], result_b.iloc[:-1])


def test_compute_sue_seasonal_prior_diff_count_capped_by_rolling_window():
    """Proves the scaling std dev uses a bounded trailing window, not an
    ever-expanding all-history window (which was found, against production
    data, to let stale volatility from years earlier distort a present-day
    surprise by several hundredfold)."""
    eps_values = [1.0 + 0.03 * ((-1) ** i) * i for i in range(16)]
    quarters = pd.date_range("2000-03-31", periods=len(eps_values), freq="QE")
    panel = _panel([("AAA", q.strftime("%Y-%m-%d"), v) for q, v in zip(quarters, eps_values)])

    result = clean.compute_sue_seasonal(panel, window=8, min_history=4).sort_values("fpedats").reset_index(drop=True)

    assert result["seasonal_n_prior_diffs"].max() <= 8
    assert result.iloc[-1]["seasonal_n_prior_diffs"] == 8


def test_compute_sue_seasonal_zero_variance_history_yields_null_not_inf():
    """A ticker whose year-over-year diffs are all identical (zero true
    variance) must not produce a division-by-zero-style blowup."""
    eps_values = [1.00, 1.05, 0.98, 1.10, 1.14, 1.19, 1.12, 1.24]  # diffs from idx4 on: all 0.14
    quarters = pd.date_range("2000-03-31", periods=len(eps_values), freq="QE")
    panel = _panel([("AAA", q.strftime("%Y-%m-%d"), v) for q, v in zip(quarters, eps_values)])

    result = clean.compute_sue_seasonal(panel, min_history=4)

    assert not np.isinf(result["sue_seasonal"].fillna(0)).any()
    assert not (result["sue_seasonal"].abs() > 1e6).any()


# ---------------------------------------------------------------------------
# Identifier crosswalk.
# ---------------------------------------------------------------------------


def test_build_identifier_crosswalk_simple_unambiguous_mapping():
    resolved = pd.DataFrame({
        "ibes_ticker": ["AAA", "BBB"],
        "permno": ["1000", "2000"],
        "gvkey": ["001000", "002000"],
    })

    crosswalk = clean.build_identifier_crosswalk(resolved).set_index("ibes_ticker")

    assert crosswalk.loc["AAA", "permno"] == "1000"
    assert crosswalk.loc["AAA", "gvkey"] == "001000"
    assert crosswalk.loc["AAA", "identifier_ambiguous"] is False or crosswalk.loc["AAA", "identifier_ambiguous"] == False  # noqa: E712


def test_build_identifier_crosswalk_explodes_comma_joined_tickers():
    resolved = pd.DataFrame({
        "ibes_ticker": ["AAA,BBB"],
        "permno": ["1000,2000"],
        "gvkey": ["001000"],
    })

    crosswalk = clean.build_identifier_crosswalk(resolved).set_index("ibes_ticker")

    assert set(crosswalk.index) == {"AAA", "BBB"}
    assert crosswalk.loc["AAA", "permno"] == "1000,2000"
    assert crosswalk.loc["AAA", "identifier_ambiguous"] == True  # noqa: E712


def test_build_identifier_crosswalk_flags_ticker_mapped_to_multiple_permnos():
    """Same I/B/E/S ticker shows up on two different secid rows with two
    different PERMNOs (e.g. after a corporate restructuring) -- both must
    be kept, comma-joined, and flagged ambiguous rather than one silently
    dropped."""
    resolved = pd.DataFrame({
        "ibes_ticker": ["AAA", "AAA"],
        "permno": ["1000", "2000"],
        "gvkey": ["001000", "001000"],
    })

    crosswalk = clean.build_identifier_crosswalk(resolved).set_index("ibes_ticker")

    assert crosswalk.loc["AAA", "permno"] == "1000,2000"
    assert crosswalk.loc["AAA", "gvkey"] == "001000"
    assert crosswalk.loc["AAA", "identifier_ambiguous"] == True  # noqa: E712


def test_build_identifier_crosswalk_ignores_unresolved_tickers():
    resolved = pd.DataFrame({
        "ibes_ticker": ["AAA", None, ""],
        "permno": ["1000", "9999", "8888"],
        "gvkey": ["001000", "009999", "008888"],
    })

    crosswalk = clean.build_identifier_crosswalk(resolved)

    assert list(crosswalk["ibes_ticker"]) == ["AAA"]


def test_attach_identifiers_marks_unmatched_tickers():
    panel = pd.DataFrame({"ticker": ["ZZZ"], "fpedats": [pd.Timestamp("2000-01-01")]})
    crosswalk = clean.build_identifier_crosswalk(
        pd.DataFrame({"ibes_ticker": ["AAA"], "permno": ["1"], "gvkey": ["1"]})
    )

    result = clean.attach_identifiers(panel, crosswalk)

    assert result.loc[0, "identifier_matched"] == False  # noqa: E712
    assert result.loc[0, "identifier_ambiguous"] == False  # noqa: E712
    assert pd.isna(result.loc[0, "permno"])
    assert "ticker" not in result.columns
    assert result.loc[0, "ibes_ticker"] == "ZZZ"


# ---------------------------------------------------------------------------
# assign_sue_primary -- the preemptive-check logic.
# ---------------------------------------------------------------------------


def test_assign_sue_primary_prefers_analyst_falls_back_to_seasonal_else_none():
    df = pd.DataFrame({
        "sue_analyst": [1.5, np.nan, np.nan],
        "sue_seasonal": [2.5, 3.5, np.nan],
    })

    result = clean.assign_sue_primary(df)

    assert result["sue_source"].tolist() == ["analyst", "seasonal", "none"]
    assert result.loc[0, "sue_primary"] == 1.5
    assert result.loc[1, "sue_primary"] == 3.5
    assert pd.isna(result.loc[2, "sue_primary"])


# ---------------------------------------------------------------------------
# End-to-end.
# ---------------------------------------------------------------------------


def test_build_event_table_end_to_end_small_synthetic_dataset():
    dates = ["2000-04-15", "2000-07-15", "2000-10-15", "2001-01-15", "2001-04-15"]
    pends = ["2000-03-31", "2000-06-30", "2000-09-30", "2000-12-31", "2001-03-31"]
    eps = [1.00, 1.05, 0.98, 1.10, 1.20]

    actu_raw = pd.DataFrame({
        "ticker": ["AAA"] * 5,
        "anndats": dates,
        "value": eps,
        "cusip": [None] * 5, "oftic": [None] * 5, "cname": [None] * 5,
        "pends": pends,
        "anntims": ["09:00:00"] * 5,
        "curr_act": ["USD"] * 5,
    })
    # consensus statpers is a few days before each announcement -- point-in-time safe
    statpers = ["2000-04-10", "2000-07-10", "2000-10-10", "2001-01-10", "2001-04-10"]
    statsumu_raw = pd.DataFrame({
        "ticker": ["AAA"] * 5,
        "statpers": statpers,
        "fpedats": pends,
        "fpi": ["6"] * 5,
        "measure": ["EPS"] * 5,
        "fiscalp": ["QTR"] * 5,
        "curcode": ["USD"] * 5,
        "meanest": [0.95, 1.00, 1.00, 1.05, 1.15],
        "medest": [0.95, 1.00, 1.00, 1.05, 1.15],
        "stdev": [0.05, 0.05, 0.05, 0.05, 0.05],
        "numest": [4.0, 4.0, 4.0, 4.0, 4.0],
    })
    resolved_raw = pd.DataFrame({
        "ibes_ticker": ["AAA"],
        "permno": ["10000"],
        "gvkey": ["001000"],
    })

    panel, stats = clean.build_event_table(actu_raw, statsumu_raw, resolved_raw)

    assert len(panel) == 5
    assert (panel["ibes_ticker"] == "AAA").all()
    assert (panel["permno"] == "10000").all()
    assert panel["sue_analyst"].notna().all()
    assert (panel["sue_source"] == "analyst").all()
    assert stats["point_in_time"]["total_lookahead_snapshots_excluded"] == 0


def test_build_event_table_excludes_lookahead_consensus_end_to_end():
    """End-to-end version of the look-ahead test: a consensus snapshot
    published AFTER the announcement must not leak into sue_analyst even
    when running the full pipeline, not just the unit-level selector."""
    actu_raw = pd.DataFrame({
        "ticker": ["AAA"],
        "anndats": ["2000-04-15"],
        "value": [1.10],
        "cusip": [None], "oftic": [None], "cname": [None],
        "pends": ["2000-03-31"],
        "anntims": ["09:00:00"],
        "curr_act": ["USD"],
    })
    # Only a POST-announcement snapshot exists -- must not be used.
    statsumu_raw = pd.DataFrame({
        "ticker": ["AAA"],
        "statpers": ["2000-04-20"],
        "fpedats": ["2000-03-31"],
        "fpi": ["6"],
        "measure": ["EPS"],
        "fiscalp": ["QTR"],
        "curcode": ["USD"],
        "meanest": [1.10],  # suspiciously exact match -- would zero out the surprise if used
        "medest": [1.10],
        "stdev": [0.05],
        "numest": [4.0],
    })
    resolved_raw = pd.DataFrame({"ibes_ticker": ["AAA"], "permno": ["10000"], "gvkey": ["001000"]})

    panel, stats = clean.build_event_table(actu_raw, statsumu_raw, resolved_raw)

    assert pd.isna(panel.loc[0, "sue_analyst"])
    assert pd.isna(panel.loc[0, "consensus_meanest"])
    assert panel.loc[0, "sue_source"] in ("seasonal", "none")
    assert stats["point_in_time"]["total_lookahead_snapshots_excluded"] == 1
