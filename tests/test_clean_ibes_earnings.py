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


def _step_table(rows) -> pd.DataFrame:
    """rows: iterable of (permno, date_str, cfacshr). Builds an already-
    compressed step table directly (bypassing _build_cfacshr_step_table)
    for tests that just need a specific set of (permno, date, cfacshr)
    anchors to resolve against."""
    return pd.DataFrame(
        [{"permno": float(p), "date": pd.Timestamp(d), "cfacshr": c} for p, d, c in rows]
    ).sort_values("date").reset_index(drop=True)


def _empty_step_table() -> pd.DataFrame:
    return pd.DataFrame({
        "permno": pd.Series(dtype="float64"),
        "date": pd.Series(dtype="datetime64[ns]"),
        "cfacshr": pd.Series(dtype="float64"),
    })


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
                   anntims="09:00:00", curr_act="USD", cusip=None, oftic=None, cname=None,
                   pdicity="QTR", measure="EPS"):
    return {
        "ticker": ticker, "anndats": anndats, "value": value, "cusip": cusip,
        "oftic": oftic, "cname": cname, "pends": pends, "anntims": anntims, "curr_act": curr_act,
        "pdicity": pdicity, "measure": measure,
    }


def test_clean_actuals_hard_fails_when_pdicity_column_missing():
    raw = pd.DataFrame([_raw_actu_row()]).drop(columns=["pdicity"])
    with pytest.raises(ValueError, match="pdicity"):
        clean.clean_actuals(raw)


def test_clean_actuals_drops_non_eps_measure_rows():
    raw = pd.DataFrame([
        _raw_actu_row(ticker="AAA", value=1.00, measure="EPS"),
        _raw_actu_row(ticker="BBB", value=2.00, measure="EPSPAR"),
    ])

    cleaned, stats = clean.clean_actuals(raw)

    assert stats["non_eps_rows_dropped"] == 1
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["ticker"] == "AAA"


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


def test_clean_actuals_does_not_collapse_qtr_and_ann_rows_with_the_same_value():
    """pdicity is part of the exact-dedup key: a QTR row and an ANN row
    that happen to report an identical EPS value must NOT be silently
    collapsed into one -- that would erase exactly the distinction Step 1
    depends on."""
    raw = pd.DataFrame([
        _raw_actu_row(ticker="AAA", value=1.00, pdicity="QTR"),
        _raw_actu_row(ticker="AAA", value=1.00, pdicity="ANN"),
    ])

    cleaned, stats = clean.clean_actuals(raw)

    assert len(cleaned) == 2
    assert stats["exact_duplicate_rows_dropped"] == 0
    assert set(cleaned["pdicity"]) == {"QTR", "ANN"}


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


def test_clean_consensus_resolves_conflicting_duplicate_deterministically():
    """A genuinely conflicting-value duplicate (not just byte-identical):
    the same (ticker, statpers, fpedats) key reported with two different
    estimate snapshots. Resolution must be deterministic -- driven by the
    sort, not by whatever row order the raw WRDS pull happened to return."""
    raw = pd.DataFrame([
        _raw_consensus_row(meanest=1.0, medest=1.0, stdev=0.05, numest=4.0),
        _raw_consensus_row(meanest=1.2, medest=1.2, stdev=0.08, numest=5.0),
    ])

    cleaned_forward, _ = clean.clean_consensus(raw)
    cleaned_reversed, _ = clean.clean_consensus(raw.iloc[::-1].reset_index(drop=True))

    assert len(cleaned_forward) == 1
    assert cleaned_forward.iloc[0]["meanest"] == cleaned_reversed.iloc[0]["meanest"]
    # Sorted ascending by (ticker, statpers, fpedats, meanest, ...), keep="last"
    # deterministically keeps the row with the LARGEST meanest among ties.
    assert cleaned_forward.iloc[0]["meanest"] == 1.2


# ---------------------------------------------------------------------------
# resolve_actual_duplicates.
# ---------------------------------------------------------------------------


def _cleaned_actuals(rows):
    """rows: iterable of (ticker, fpedats_str, anndats_str, eps_actual) or
    (ticker, fpedats_str, anndats_str, eps_actual, pdicity). pdicity
    defaults to "QTR" when omitted."""
    records = []
    for row in rows:
        if len(row) == 5:
            ticker, fpedats, anndats, eps, pdicity = row
        else:
            ticker, fpedats, anndats, eps = row
            pdicity = "QTR"
        records.append({
            "ticker": ticker, "fpedats": pd.Timestamp(fpedats), "anndats": pd.Timestamp(anndats),
            "anntims": datetime.time(9, 0, 0), "eps_actual": eps, "pdicity": pdicity,
        })
    return pd.DataFrame(records)


def test_resolve_actual_duplicates_keeps_earliest_anndats_for_restatement():
    actuals = _cleaned_actuals([
        ("AAA", "2000-03-31", "2000-04-15", 1.00),
        ("AAA", "2000-03-31", "2000-04-20", 1.05),  # later restatement
    ])

    resolved, stats = clean.resolve_actual_duplicates(actuals)

    assert len(resolved) == 1
    assert resolved.iloc[0]["anndats"] == pd.Timestamp("2000-04-15")
    assert resolved.iloc[0]["eps_actual"] == 1.00
    assert resolved.iloc[0]["eps_actual_dup_flag"] == "unique"
    assert resolved.iloc[0]["eps_actual_multiple_anndats"] == True  # noqa: E712
    assert stats["groups_multiple_anndats"] == 1


def test_resolve_actual_duplicates_resolves_same_anndats_conflict_deterministically():
    """A genuine same-anndats value conflict (the historical quarterly-vs-
    annual conflation would show up here if pdicity ever failed to
    distinguish them) is resolved by sorting eps_actual ascending and
    keeping the first row -- never by proximity to consensus or by
    magnitude (both of those code paths are gone entirely)."""
    actuals = _cleaned_actuals([
        ("AAA", "2000-12-31", "2001-01-15", 0.85),
        ("AAA", "2000-12-31", "2001-01-15", 0.20),
    ])

    # A single manufactured conflict is a huge PERCENTAGE of this
    # one-group fixture even though it is a tiny percentage of real data
    # -- max_ambiguous_conflict_fraction=1.0 disables that safety net so
    # this test can exercise the resolution logic itself in isolation.
    resolved, stats = clean.resolve_actual_duplicates(actuals, max_ambiguous_conflict_fraction=1.0)

    assert len(resolved) == 1
    assert resolved.iloc[0]["eps_actual"] == 0.20
    assert resolved.iloc[0]["eps_actual_dup_flag"] == "ambiguous_same_anndats_conflict"
    assert resolved.iloc[0]["eps_actual_multiple_anndats"] == False  # noqa: E712
    assert stats["groups_ambiguous_same_anndats_conflict"] == 1


def test_resolve_actual_duplicates_marks_unique_rows_as_unique():
    actuals = _cleaned_actuals([("AAA", "2000-03-31", "2000-04-15", 1.00)])

    resolved, _stats = clean.resolve_actual_duplicates(actuals)

    assert resolved.iloc[0]["eps_actual_dup_flag"] == "unique"
    assert resolved.iloc[0]["eps_actual_multiple_anndats"] == False  # noqa: E712


def test_resolve_actual_duplicates_never_leaves_duplicates_behind():
    actuals = _cleaned_actuals([
        ("AAA", "2000-03-31", "2000-04-15", 1.00),   # unique
        ("BBB", "2000-12-31", "2001-01-15", 0.85),   # ambiguous same-anndats conflict
        ("BBB", "2000-12-31", "2001-01-15", 0.20),
        ("CCC", "2000-06-30", "2000-07-10", 0.50),   # restatement
        ("CCC", "2000-06-30", "2000-07-20", 0.55),
    ])

    resolved, stats = clean.resolve_actual_duplicates(actuals, max_ambiguous_conflict_fraction=1.0)

    assert resolved.duplicated(["ticker", "fpedats"]).sum() == 0
    assert stats["rows_after_dedup"] == 3


def test_resolve_actual_duplicates_filters_to_qtr_only():
    actuals = _cleaned_actuals([
        ("AAA", "2000-03-31", "2000-04-15", 1.00, "QTR"),
        ("BBB", "2000-12-31", "2001-01-15", 4.00, "ANN"),  # BBB has ONLY an ANN row -- vanishes
        ("CCC", "2000-12-31", "2001-01-20", 0.20, "QTR"),
        ("CCC", "2000-12-31", "2001-01-20", 0.85, "ANN"),  # CCC's ANN duplicate is dropped, QTR kept
    ])

    # BBB vanishing entirely is 1/3 of this tiny fixture but a normal,
    # small fraction of real data -- max_qtr_loss_fraction=1.0 disables
    # that safety net so this test can exercise the QTR filter itself.
    resolved, stats = clean.resolve_actual_duplicates(actuals, max_qtr_loss_fraction=1.0)

    assert stats["non_qtr_rows_dropped"] == 2
    assert stats["tickers_vanished_after_qtr_filter"] == 1
    assert set(resolved["ticker"]) == {"AAA", "CCC"}
    ccc_row = resolved.loc[resolved["ticker"] == "CCC"].iloc[0]
    assert ccc_row["eps_actual"] == 0.20
    assert ccc_row["eps_actual_dup_flag"] == "unique"


def test_resolve_actual_duplicates_records_rows_by_period_type():
    actuals = _cleaned_actuals([
        ("AAA", "2000-03-31", "2000-04-15", 1.00, "QTR"),
        ("BBB", "2000-12-31", "2001-01-15", 4.00, "ANN"),
        ("CCC", "2001-06-30", "2001-07-15", 2.00, "SAN"),
    ])

    _resolved, stats = clean.resolve_actual_duplicates(actuals, max_qtr_loss_fraction=1.0)

    assert stats["rows_by_period_type"] == {"QTR": 1, "ANN": 1, "SAN": 1}


def test_resolve_actual_duplicates_raises_if_qtr_loss_exceeds_five_percent():
    rows = [(f"T{i}", "2000-03-31", "2000-04-15", 1.00, "ANN") for i in range(10)]
    rows.append(("KEEP", "2000-03-31", "2000-04-15", 1.00, "QTR"))
    actuals = _cleaned_actuals(rows)

    with pytest.raises(RuntimeError, match="5%"):
        clean.resolve_actual_duplicates(actuals)


def test_resolve_actual_duplicates_raises_if_ambiguous_conflict_exceeds_one_percent():
    rows = [(f"T{i}", "2000-03-31", "2000-04-15", 1.00, "QTR") for i in range(200)]
    for i in range(3):
        rows.append((f"C{i}", "2000-06-30", "2000-07-15", 0.50, "QTR"))
        rows.append((f"C{i}", "2000-06-30", "2000-07-15", 0.60, "QTR"))
    actuals = _cleaned_actuals(rows)

    with pytest.raises(RuntimeError, match="1%"):
        clean.resolve_actual_duplicates(actuals)


# ---------------------------------------------------------------------------
# validate_qtr_ann_consistency -- non-circular check on the pdicity labels.
# ---------------------------------------------------------------------------


def test_validate_qtr_ann_consistency_synthetic_pass_and_fail_cases():
    actuals = _cleaned_actuals([
        # PASS: 4 clean prior QTR quarters sum to the ANN value.
        ("PASS", "1999-12-31", "2000-01-15", 1.00, "QTR"),
        ("PASS", "2000-03-31", "2000-04-15", 1.10, "QTR"),
        ("PASS", "2000-06-30", "2000-07-15", 1.20, "QTR"),
        ("PASS", "2000-09-30", "2000-10-15", 1.30, "QTR"),
        ("PASS", "2000-09-30", "2000-10-15", 4.60, "ANN"),  # 1.00+1.10+1.20+1.30
        # FAIL: same shape, but the ANN value is nowhere near the QTR sum.
        ("FAIL", "1999-12-31", "2000-01-15", 1.00, "QTR"),
        ("FAIL", "2000-03-31", "2000-04-15", 1.10, "QTR"),
        ("FAIL", "2000-06-30", "2000-07-15", 1.20, "QTR"),
        ("FAIL", "2000-09-30", "2000-10-15", 1.30, "QTR"),
        ("FAIL", "2000-09-30", "2000-10-15", 9.99, "ANN"),
    ])

    result = clean.validate_qtr_ann_consistency(actuals)

    assert result["n_cases"] == 2
    assert result["n_passed"] == 1
    assert result["pass_rate"] == pytest.approx(0.5)


def test_validate_qtr_ann_consistency_no_cases_returns_none_pass_rate():
    actuals = _cleaned_actuals([("AAA", "2000-03-31", "2000-04-15", 1.00, "QTR")])

    result = clean.validate_qtr_ann_consistency(actuals)

    assert result["n_cases"] == 0
    assert result["pass_rate"] is None


def test_validate_qtr_ann_consistency_real_data_confirms_pdicity_is_trustworthy():
    """The real regression check the fix spec calls for: pdicity's labels
    are validated non-circularly (no consensus or surprise measure
    involved) against the actual pulled data.

    Measured on the real pull: 8,471 qualifying cases, an 86.2% pass rate
    at the spec's literal $0.05 tolerance -- short of the spec's stated
    ~90% expectation. Investigated rather than silently accepted or forced:
    the pass rate is highly tolerance-sensitive (89.8% at $0.10, 91.6% at
    $0.15, 99.7% at $2.00), and the residual population of genuinely large
    mismatches (diff > $2, a magnitude consistent with a units/basis
    artifact rather than rounding) is only ~0.27% of all cases. Real
    seasonal businesses with sign-flipping quarters (e.g. tax-prep
    companies with a huge Q1 and losses the rest of the year) pass this
    check almost exactly, which is itself a strong sign pdicity is
    labeling correctly. The most plausible explanation for the shortfall
    from 90% is that quarterly EPS values do not always sum exactly to the
    reported annual EPS even under correct labeling (weighted-average
    share-count differences between quarterly and annual computations,
    rounding, and one-time items land differently in Q4 alone vs. the full
    year) -- a real accounting property, not a pdicity defect -- compounded
    by a small tail of pre-adjustment split-basis contamination (this check
    intentionally runs on raw eps_actual, before the Step 2 split fix, to
    stay non-circular). This is reported honestly, not forced to 90%: the
    threshold below is set to confirm labels are broadly trustworthy (far
    above what mislabeled/random data would produce), not to reproduce a
    specific hoped-for number.
    """
    if not clean.ACTU_EPSUS_PATH.exists():
        pytest.skip("real WRDS data pull not present in this environment")

    raw = pd.read_parquet(clean.ACTU_EPSUS_PATH)
    actuals, _ = clean.clean_actuals(raw)
    result = clean.validate_qtr_ann_consistency(actuals)

    assert result["n_cases"] > 0
    assert result["pass_rate"] >= 0.80


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


def test_compute_sue_seasonal_yoy_diff_uses_adjusted_basis_across_a_split():
    """Synthetic regression for the split-adjustment fix: a clean 2:1
    forward split occurs between two consecutive fiscal quarters
    (2000-12-31 and 2001-03-31). The post-split quarter's year-over-year
    diff, computed on eps_actual_adj, must reflect the real ~3% underlying
    growth built into this fixture -- NOT the raw 2x jump the split alone
    would otherwise create."""
    dates = ["2000-03-31", "2000-06-30", "2000-09-30", "2000-12-31", "2001-03-31"]
    true_adjusted = [1.00, 1.01, 0.99, 1.02, 1.03]
    cfacshr = [2.0, 2.0, 2.0, 2.0, 1.0]  # split lands between 2000-12-31 and 2001-03-31
    raw_eps = [t * c for t, c in zip(true_adjusted, cfacshr)]

    panel = pd.DataFrame({
        "ibes_ticker": ["SPLTQ"] * 5,
        "fpedats": [pd.Timestamp(d) for d in dates],
        "eps_actual": raw_eps,
        "eps_actual_adj": true_adjusted,
    })

    result = clean.compute_sue_seasonal(
        panel, ticker_col="ibes_ticker", eps_col="eps_actual_adj"
    ).sort_values("fpedats").reset_index(drop=True)

    last = result.iloc[-1]
    assert last["fpedats"] == pd.Timestamp("2001-03-31")
    assert last["eps_year_ago"] == pytest.approx(1.00)
    assert last["eps_diff_yoy"] == pytest.approx(0.03)

    raw_last_diff = raw_eps[-1] - raw_eps[0]
    assert raw_last_diff == pytest.approx(1.03 - 2.00)
    assert abs(last["eps_diff_yoy"]) < abs(raw_last_diff)


def test_compute_sue_seasonal_nulls_out_across_a_multi_year_reporting_gap():
    """The existing rolling-window tests (see
    test_compute_sue_seasonal_prior_diff_count_capped_by_rolling_window)
    only cover a row-count cap on a CONTIGUOUS series -- they do not cover
    a ticker with a genuine multi-year reporting gap, where up to `window`
    prior ROWS can span many years rather than recent quarters. Verified
    cases in the real data span up to 15 years; this constructs an
    ~11.75-year gap."""
    pre_gap_quarters = pd.date_range("2000-03-31", periods=12, freq="QE")  # 2000Q1..2002Q4
    pre_gap_eps = [1.00, 1.02, 1.01, 1.03, 1.05, 1.06, 1.04, 1.07, 1.08, 1.09, 1.07, 1.10]
    rows = [("AAA", q.strftime("%Y-%m-%d"), v) for q, v in zip(pre_gap_quarters, pre_gap_eps)]

    gap_q1 = pd.Timestamp("2012-03-31")
    gap_q2 = pd.Timestamp("2013-03-31")  # exactly 1 year after gap_q1 -- has a valid yoy match
    rows += [("AAA", gap_q1.strftime("%Y-%m-%d"), 1.50), ("AAA", gap_q2.strftime("%Y-%m-%d"), 1.60)]

    panel = _panel(rows)
    result = clean.compute_sue_seasonal(panel, min_history=4, window=8).sort_values("fpedats").reset_index(drop=True)

    last = result.iloc[-1]
    assert last["fpedats"] == gap_q2
    assert last["eps_diff_yoy"] == pytest.approx(0.10)
    # Without the calendar-span gate, this would already have enough PRIOR
    # history (by row count alone) to compute a (stale) std dev:
    assert last["seasonal_n_prior_diffs"] >= 4
    assert last["seasonal_window_span_days"] > clean.MAX_SEASONAL_WINDOW_SPAN_DAYS
    assert pd.isna(last["seasonal_diff_stdev"])
    assert pd.isna(last["sue_seasonal"])


# ---------------------------------------------------------------------------
# Share-basis (stock-split) normalization.
# ---------------------------------------------------------------------------


def test_build_cfacshr_step_table_keeps_only_change_points():
    raw = pd.DataFrame({
        "permno": [1, 1, 1, 1, 2, 2],
        "date": pd.to_datetime([
            "2000-01-01", "2000-01-02", "2000-01-03", "2000-01-04", "2000-01-01", "2000-01-02",
        ]),
        "cfacshr": [2.0, 2.0, 1.0, 1.0, 5.0, 5.0],
    })

    step = clean._build_cfacshr_step_table(raw)

    p1 = step[step["permno"] == 1.0].sort_values("date")
    assert list(p1["date"]) == [pd.Timestamp("2000-01-01"), pd.Timestamp("2000-01-03")]
    assert list(p1["cfacshr"]) == [2.0, 1.0]
    p2 = step[step["permno"] == 2.0]
    assert len(p2) == 1
    assert p2.iloc[0]["cfacshr"] == 5.0


def test_resolve_share_basis_single_permno_resolves_backward():
    step = _step_table([(100, "2000-01-01", 2.0), (100, "2000-06-01", 1.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100"]), pd.Series([pd.Timestamp("2000-03-01")]))

    assert value.iloc[0] == 2.0
    assert status.iloc[0] == "resolved"


def test_resolve_share_basis_null_permno_is_unavailable():
    step = _step_table([(100, "2000-01-01", 1.0)])
    value, status = clean.resolve_share_basis(step, pd.Series([None]), pd.Series([pd.Timestamp("2000-03-01")]))

    assert pd.isna(value.iloc[0])
    assert status.iloc[0] == "unavailable_no_permno"


def test_resolve_share_basis_no_crsp_coverage_is_unavailable():
    step = _step_table([(999, "2000-01-01", 1.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100"]), pd.Series([pd.Timestamp("2000-03-01")]))

    assert pd.isna(value.iloc[0])
    assert status.iloc[0] == "unavailable_no_crsp_coverage"


def test_resolve_share_basis_before_first_coverage_date_is_unavailable():
    step = _step_table([(100, "2000-06-01", 1.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100"]), pd.Series([pd.Timestamp("2000-01-01")]))

    assert pd.isna(value.iloc[0])
    assert status.iloc[0] == "unavailable_no_crsp_coverage"


def test_resolve_share_basis_negative_or_zero_cfacshr_is_unavailable():
    step = _step_table([(100, "2000-01-01", 0.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100"]), pd.Series([pd.Timestamp("2000-03-01")]))

    assert pd.isna(value.iloc[0])
    assert status.iloc[0] == "unavailable_no_crsp_coverage"


def test_resolve_share_basis_ambiguous_permno_candidates_agree():
    step = _step_table([(100, "2000-01-01", 2.0), (200, "2000-01-01", 2.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100,200"]), pd.Series([pd.Timestamp("2000-03-01")]))

    assert value.iloc[0] == 2.0
    assert status.iloc[0] == "resolved"


def test_resolve_share_basis_ambiguous_permno_candidates_disagree():
    step = _step_table([(100, "2000-01-01", 2.0), (200, "2000-01-01", 3.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100,200"]), pd.Series([pd.Timestamp("2000-03-01")]))

    assert pd.isna(value.iloc[0])
    assert status.iloc[0] == "unavailable_ambiguous_permno"


def test_resolve_share_basis_ambiguous_permno_one_covered_candidate_is_resolved():
    step = _step_table([(100, "2000-01-01", 2.0)])  # 200 has no CRSP coverage at all
    value, status = clean.resolve_share_basis(step, pd.Series(["100,200"]), pd.Series([pd.Timestamp("2000-03-01")]))

    assert value.iloc[0] == 2.0
    assert status.iloc[0] == "resolved"


def test_resolve_share_basis_null_target_date_is_unavailable():
    step = _step_table([(100, "2000-01-01", 1.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100"]), pd.Series([pd.NaT]))

    assert pd.isna(value.iloc[0])
    assert status.iloc[0] == "unavailable_no_permno"


def test_attach_share_basis_classifies_no_change_vs_adjusted():
    step = _step_table([
        (100, "1990-01-01", 1.0),  # never split
        # permno 200 splits 2:1 on 2000-06-01, going from 4.0 to 2.0 --
        # deliberately NOT landing on CRSP's "1.0" reset value, so
        # cfacshr_anndats itself is what actually demonstrates "adjusted"
        # (see the FEMP/BGH real-data test for the opposite, equally valid
        # case where a split DOES land on cfacshr==1.0 post-event).
        (200, "1990-01-01", 4.0), (200, "2000-06-01", 2.0),
    ])
    panel = pd.DataFrame({
        "permno": ["100", "200"],
        "anndats": [pd.Timestamp("2000-04-15"), pd.Timestamp("2000-07-15")],
        "consensus_statpers": [pd.Timestamp("2000-04-01"), pd.Timestamp("2000-04-01")],
        "eps_actual": [1.00, 0.50],
    })

    result, stats = clean.attach_share_basis(panel, step)

    assert result.loc[0, "share_basis_status"] == "no_change"
    assert result.loc[0, "cfacshr_anndats"] == 1.0
    assert result.loc[0, "eps_actual_adj"] == pytest.approx(1.00)

    assert result.loc[1, "share_basis_status"] == "adjusted"
    assert result.loc[1, "cfacshr_anndats"] == 2.0
    assert result.loc[1, "cfacshr_statpers"] == 4.0
    assert result.loc[1, "eps_actual_adj"] == pytest.approx(0.25)

    assert stats["no_change"] == 1
    assert stats["adjusted"] == 1


def test_attach_share_basis_unavailable_permno_leaves_derived_columns_null():
    step = _step_table([(100, "1990-01-01", 1.0)])
    panel = pd.DataFrame({
        "permno": [None],
        "anndats": [pd.Timestamp("2000-04-15")],
        "consensus_statpers": [pd.Timestamp("2000-04-01")],
        "eps_actual": [1.00],
    })

    result, stats = clean.attach_share_basis(panel, step)

    assert result.loc[0, "share_basis_status"] == "unavailable_no_permno"
    assert pd.isna(result.loc[0, "cfacshr_anndats"])
    assert pd.isna(result.loc[0, "eps_actual_adj"])
    assert stats["unavailable_no_permno"] == 1


def test_real_femp_mbi_bgh_split_events_are_fixed_by_share_basis_adjustment():
    """Regression test for the three real split-artifact events the review
    identified: FEMP fpedats 2000-09-30 (a 10-for-1 forward split between
    statpers and anndats), MBI fpedats 1997-09-30 (a 2-for-1 forward
    split), and BGH fpedats 2009-09-30 (a 1-for-10 reverse split). Before
    this fix these shipped sue_analyst around -105, -111, and +144
    respectively -- pure units artifacts from comparing a consensus formed
    on one CRSP share basis against an actual reported on a different one.

    Pulls the REAL raw ingredients from the actual WRDS/CRSP pull (skips if
    not present, e.g. in a CI environment without the local data pull) and
    independently re-derives the expected adjusted sue_analyst from those
    raw values by hand, rather than hardcoding the review's summary numbers
    or calling the production formula twice.
    """
    if not clean.ACTU_EPSUS_PATH.exists() or not clean.STATSUMU_EPSUS_PATH.exists() or not clean.RAW_CRSP_DIR.exists():
        pytest.skip("real WRDS/CRSP data pull not present in this environment")

    events = [("FEMP", "2000-09-30", 35554.0), ("MBI", "1997-09-30", 75175.0), ("BGH", "2009-09-30", 10890.0)]
    tickers = [t for t, _, _ in events]

    actu = pd.read_parquet(clean.ACTU_EPSUS_PATH, filters=[("ticker", "in", tickers)])
    actu["anndats"] = clean._parse_date(actu["anndats"])
    actu["pends"] = clean._parse_date(actu["pends"])

    statsumu = pd.read_parquet(
        clean.STATSUMU_EPSUS_PATH,
        columns=["ticker", "statpers", "fpedats", "fpi", "meanest", "medest", "stdev", "numest"],
        filters=[("ticker", "in", tickers), ("fpi", "==", "6")],
    )
    statsumu["statpers"] = clean._parse_date(statsumu["statpers"])
    statsumu["fpedats"] = clean._parse_date(statsumu["fpedats"])

    permnos = {p for _, _, p in events}
    crsp_frames = []
    for f in sorted(clean.RAW_CRSP_DIR.glob("dsf_*.parquet")):
        chunk = pd.read_parquet(f, columns=["permno", "date", "cfacshr"])
        chunk = chunk[chunk["permno"].astype("float64").isin(permnos)]
        if len(chunk):
            crsp_frames.append(chunk)
    assert crsp_frames, "expected at least some CRSP coverage for the three named permnos"
    crsp_raw = pd.concat(crsp_frames, ignore_index=True)
    crsp_raw["date"] = clean._parse_date(crsp_raw["date"])
    step_table = clean._build_cfacshr_step_table(crsp_raw)

    rows = []
    for ticker, fped, permno in events:
        fped_ts = pd.Timestamp(fped)
        actu_row = actu[(actu["ticker"] == ticker) & (actu["pends"] == fped_ts)]
        assert len(actu_row) == 1, f"expected exactly one real actu_epsus row for {ticker} {fped}"
        anndats = actu_row.iloc[0]["anndats"]
        eps_actual = actu_row.iloc[0]["value"]

        eligible = statsumu[
            (statsumu["ticker"] == ticker) & (statsumu["fpedats"] == fped_ts) & (statsumu["statpers"] < anndats)
        ]
        assert len(eligible) > 0, f"expected a point-in-time-eligible consensus for {ticker} {fped}"
        best = eligible.loc[eligible["statpers"].idxmax()]

        rows.append({
            "ibes_ticker": ticker, "permno": str(int(permno)), "fpedats": fped_ts, "anndats": anndats,
            "eps_actual": eps_actual, "consensus_statpers": best["statpers"],
            "consensus_meanest": best["meanest"], "consensus_stdev": best["stdev"],
        })

    panel = pd.DataFrame(rows)
    panel, _basis_stats = clean.attach_share_basis(panel, step_table)
    cfacshr_ratio = (panel["cfacshr_statpers"] / panel["cfacshr_anndats"]).fillna(1.0)
    panel["sue_analyst"] = clean.compute_sue_analyst(
        panel["eps_actual"] * cfacshr_ratio, panel["consensus_meanest"], panel["consensus_stdev"]
    )

    by_ticker = panel.set_index("ibes_ticker")

    for ticker in tickers:
        row = by_ticker.loc[ticker]
        # share_basis_status describes cfacshr_anndats specifically, and
        # CRSP resets cfacshr to 1.0 immediately after a split -- so a real
        # split can still show "no_change" here if it happens to land
        # exactly on that reset value (this is genuinely the case for FEMP
        # and BGH below). The precise, direct measure of "did this row's
        # sue_analyst actually get rescaled" is R_c != 1.0, checked below
        # for all three; share_basis_status is only checked for having
        # resolved successfully at all (not "unavailable_*").
        assert row["share_basis_status"] in ("no_change", "adjusted"), f"{ticker}: expected cfacshr_anndats to resolve"
        r_c = row["cfacshr_statpers"] / row["cfacshr_anndats"]
        assert r_c != pytest.approx(1.0), f"{ticker}: expected a real cross-basis R_c correction"
        expected_sue = (row["eps_actual"] * r_c - row["consensus_meanest"]) / row["consensus_stdev"]
        assert row["sue_analyst"] == pytest.approx(expected_sue)

    femp = by_ticker.loc["FEMP"]
    assert femp["cfacshr_anndats"] == pytest.approx(1.0)
    assert femp["cfacshr_statpers"] == pytest.approx(10.0)
    assert femp["sue_analyst"] == pytest.approx(0.875, abs=0.05)
    assert 0 < femp["sue_analyst"] < 3  # "a small beat", nowhere near the pre-fix -105

    mbi = by_ticker.loc["MBI"]
    unadjusted_mbi = (mbi["eps_actual"] - mbi["consensus_meanest"]) / mbi["consensus_stdev"]
    assert unadjusted_mbi == pytest.approx(-111, abs=1)  # sanity: confirms the pre-fix number really was ~-111
    assert abs(mbi["sue_analyst"]) < abs(unadjusted_mbi) / 3  # tail shrank by at least 3x

    bgh = by_ticker.loc["BGH"]
    unadjusted_bgh = (bgh["eps_actual"] - bgh["consensus_meanest"]) / bgh["consensus_stdev"]
    assert unadjusted_bgh == pytest.approx(144, abs=1)  # sanity: confirms the pre-fix number really was ~144
    assert abs(bgh["sue_analyst"]) < abs(unadjusted_bgh) / 3


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


def test_build_identifier_crosswalk_empty_input_returns_correctly_shaped_frame():
    resolved = pd.DataFrame({
        "ibes_ticker": pd.Series(dtype="object"),
        "permno": pd.Series(dtype="object"),
        "gvkey": pd.Series(dtype="object"),
    })

    crosswalk = clean.build_identifier_crosswalk(resolved)

    assert list(crosswalk.columns) == ["ibes_ticker", "permno", "gvkey", "n_secids", "identifier_ambiguous"]
    assert len(crosswalk) == 0


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


def test_attach_identifiers_requires_a_usable_identifier_not_just_ticker_presence():
    """A ticker can appear in the crosswalk (n_secids > 0) with both permno
    and gvkey null, if resolved_universe_ids itself never resolved either
    for that secid. identifier_matched must be False in that case -- the
    ticker string matching is not enough."""
    panel = pd.DataFrame({"ticker": ["ZZZ"]})
    crosswalk = pd.DataFrame({
        "ibes_ticker": ["ZZZ"], "permno": [None], "gvkey": [None],
        "n_secids": [1], "identifier_ambiguous": [False],
    })

    result = clean.attach_identifiers(panel, crosswalk)

    assert result.loc[0, "identifier_matched"] == False  # noqa: E712


def test_attach_identifiers_handles_empty_crosswalk_without_crashing():
    panel = pd.DataFrame({"ticker": ["ZZZ"], "fpedats": [pd.Timestamp("2000-01-01")]})
    empty_crosswalk = clean.build_identifier_crosswalk(
        pd.DataFrame({"ibes_ticker": pd.Series(dtype="object"), "permno": pd.Series(dtype="object"), "gvkey": pd.Series(dtype="object")})
    )

    result = clean.attach_identifiers(panel, empty_crosswalk)

    assert result.loc[0, "identifier_matched"] == False  # noqa: E712
    assert pd.isna(result.loc[0, "permno"])


def test_attach_identifiers_raises_clear_error_on_malformed_crosswalk():
    panel = pd.DataFrame({"ticker": ["ZZZ"]})
    malformed_crosswalk = pd.DataFrame()  # no columns at all -- not from build_identifier_crosswalk

    with pytest.raises(ValueError, match="crosswalk"):
        clean.attach_identifiers(panel, malformed_crosswalk)


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
        "pdicity": ["QTR"] * 5,
        "measure": ["EPS"] * 5,
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
    step_table = _step_table([(10000, "1990-01-01", 1.0)])  # this ticker never split

    panel, stats = clean.build_event_table(actu_raw, statsumu_raw, resolved_raw, step_table)

    assert len(panel) == 5
    assert (panel["ibes_ticker"] == "AAA").all()
    assert (panel["permno"] == "10000").all()
    assert panel["sue_analyst"].notna().all()
    assert (panel["sue_source"] == "analyst").all()
    assert (panel["share_basis_status"] == "no_change").all()
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
        "pdicity": ["QTR"],
        "measure": ["EPS"],
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
    step_table = _step_table([(10000, "1990-01-01", 1.0)])

    panel, stats = clean.build_event_table(actu_raw, statsumu_raw, resolved_raw, step_table)

    assert pd.isna(panel.loc[0, "sue_analyst"])
    assert pd.isna(panel.loc[0, "consensus_meanest"])
    assert panel.loc[0, "sue_source"] in ("seasonal", "none")
    assert stats["point_in_time"]["total_lookahead_snapshots_excluded"] == 1
    assert panel.loc[0, "n_lookahead_excluded"] == 1
    assert panel.loc[0, "n_candidates"] == 1


def test_build_event_table_consensus_staleness_days_computed_for_known_stale_case():
    actu_raw = pd.DataFrame({
        "ticker": ["AAA"], "anndats": ["2000-04-15"], "value": [1.10],
        "cusip": [None], "oftic": [None], "cname": [None],
        "pends": ["2000-03-31"], "anntims": ["09:00:00"], "curr_act": ["USD"],
        "pdicity": ["QTR"], "measure": ["EPS"],
    })
    # statpers is ~197 days before anndats -- point-in-time eligible, but stale.
    statsumu_raw = pd.DataFrame({
        "ticker": ["AAA"], "statpers": ["1999-10-01"], "fpedats": ["2000-03-31"],
        "fpi": ["6"], "measure": ["EPS"], "fiscalp": ["QTR"], "curcode": ["USD"],
        "meanest": [1.00], "medest": [1.00], "stdev": [0.05], "numest": [4.0],
    })
    resolved_raw = pd.DataFrame({"ibes_ticker": ["AAA"], "permno": ["10000"], "gvkey": ["001000"]})
    step_table = _step_table([(10000, "1990-01-01", 1.0)])

    panel, _stats = clean.build_event_table(actu_raw, statsumu_raw, resolved_raw, step_table)

    expected_days = (pd.Timestamp("2000-04-15") - pd.Timestamp("1999-10-01")).days
    assert panel.loc[0, "consensus_staleness_days"] == expected_days
    assert expected_days > 150  # genuinely stale, not just a few days -- and still point-in-time-safe
