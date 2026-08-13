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
    anchors to resolve against.

    Also derives `prev_cfacshr`/`is_change` the same way
    _build_cfacshr_step_table() would (per-permno shift, sorted by date) --
    resolve_eps_basis() requires both columns on any step_table it's given,
    and since this helper bypasses the real compression function, it has to
    compute them itself rather than relying on that function's own logic.
    """
    df = pd.DataFrame(
        [{"permno": float(p), "date": pd.Timestamp(d), "cfacshr": c} for p, d, c in rows]
    ).sort_values(["permno", "date"]).reset_index(drop=True)
    prev_cfacshr = df.groupby("permno")["cfacshr"].shift(1)
    df["prev_cfacshr"] = prev_cfacshr
    df["is_change"] = prev_cfacshr.notna() & (df["cfacshr"] != prev_cfacshr)
    return df.sort_values("date").reset_index(drop=True)


def _empty_step_table() -> pd.DataFrame:
    return pd.DataFrame({
        "permno": pd.Series(dtype="float64"),
        "date": pd.Series(dtype="datetime64[ns]"),
        "cfacshr": pd.Series(dtype="float64"),
        "prev_cfacshr": pd.Series(dtype="float64"),
        "is_change": pd.Series(dtype="bool"),
    })


def _real_data_available() -> bool:
    return (
        clean.ACTU_EPSUS_PATH.exists()
        and clean.STATSUMU_EPSUS_PATH.exists()
        and clean.RESOLVED_UNIVERSE_PATH.exists()
        and clean.RAW_CRSP_DIR.exists()
        and any(clean.RAW_CRSP_DIR.glob("dsf_*.parquet"))
    )


def _load_real_raw_frames():
    """Loads the same three raw input frames + step table build_event_table()
    needs, exactly as main() does. Used by real-data regression tests that
    need the FULL resolved panel rather than a hand-built mini-panel -- the
    eps_basis discriminator needs each ticker's own PRIOR-quarter history,
    which a mini-panel limited to just the rows under test would not have.
    """
    actu_raw = pd.read_parquet(clean.ACTU_EPSUS_PATH)
    statsumu_raw = pd.read_parquet(
        clean.STATSUMU_EPSUS_PATH,
        columns=clean.STATSUMU_USECOLS,
        filters=[("fpi", "==", "6")],
    )
    resolved_raw = pd.read_parquet(clean.RESOLVED_UNIVERSE_PATH)
    cfacshr_step_table = clean.load_cfacshr_step_table()
    return actu_raw, statsumu_raw, resolved_raw, cfacshr_step_table


@pytest.fixture(scope="module")
def real_event_table():
    """The full real build_event_table() output + stats, shared across the
    handful of tests that need it. Module-scoped so the real pipeline
    (~7 seconds, dominated by reading CRSP's ~20M-row daily file set) runs
    only once for the whole test module rather than once per test.
    """
    if not _real_data_available():
        pytest.skip("real WRDS/CRSP data pull not present in this environment")
    actu_raw, statsumu_raw, resolved_raw, step_table = _load_real_raw_frames()
    return clean.build_event_table(actu_raw, statsumu_raw, resolved_raw, step_table)


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

    An earlier version of this check found the "3 prior same-ticker fiscal
    quarters" via `pd.DateOffset(months=3/6/9)`, which silently excluded
    every December fiscal-year-end (73% of the eligible sample) due to a
    day-of-month arithmetic mismatch -- see
    test_real_validate_qtr_ann_consistency_december_fix_produces_expected_counts
    for the exact before/after counts. That version measured 8,471
    qualifying cases at an 86.2% pass rate (spec's literal $0.05
    tolerance). The FIXED version (ordered prior-row lookups, gated by a
    day-count window instead of day-of-month arithmetic) finds 7.78x more
    cases at materially the same pass rate -- confirmatory evidence, not a
    reason to second-guess the underlying QTR/ANN disambiguation. The
    residual gap from 100% is itself informative, not a defect: quarterly
    EPS does not always sum exactly to reported annual EPS even under
    correct labeling (weighted-average share-count differences, rounding,
    one-time items landing differently in Q4 alone vs. the full year), not
    a pdicity mislabeling -- this check intentionally runs on raw
    eps_actual, before the Step 2 split-adjustment fix, to stay
    non-circular. This test only checks the pass rate is broadly high (far
    above what mislabeled/random data would produce); the dedicated test
    below checks the exact counts.
    """
    if not clean.ACTU_EPSUS_PATH.exists():
        pytest.skip("real WRDS data pull not present in this environment")

    raw = pd.read_parquet(clean.ACTU_EPSUS_PATH)
    actuals, _ = clean.clean_actuals(raw)
    result = clean.validate_qtr_ann_consistency(actuals)

    assert result["n_cases"] > 0
    assert result["pass_rate"] >= 0.80


def test_real_validate_qtr_ann_consistency_december_fix_produces_expected_counts():
    """Exact real-data before/after counts from the fix spec for the
    date-arithmetic fix. A meaningful mismatch here is a stop-and-report
    condition, not something to silently adjust to match."""
    if not clean.ACTU_EPSUS_PATH.exists():
        pytest.skip("real WRDS data pull not present in this environment")

    raw = pd.read_parquet(clean.ACTU_EPSUS_PATH)
    actuals, _ = clean.clean_actuals(raw)
    result = clean.validate_qtr_ann_consistency(actuals)

    assert result["n_cases"] == 65922
    assert result["pass_rate"] == pytest.approx(0.8579, abs=0.001)
    assert result["n_cases_december"] == 48340


def test_validate_qtr_ann_consistency_december_synthetic_minimal_repro():
    """Minimal synthetic reproduction of the DateOffset bug this function's
    rewrite fixes: a December fiscal-year-end. The OLD (pre-fix)
    implementation's `2000-09-30 + pd.DateOffset(months=3) == 2000-12-30`
    would never match the real `2000-12-31` QTR row two lines below, so it
    would find n_cases == 0 on this exact input -- silently excluding this
    case from the check entirely, which is exactly the bug. The FIXED
    implementation (ordered prior-row lookups, no day-of-month arithmetic)
    must find it.
    """
    actuals = _cleaned_actuals([
        ("DECQ", "2000-03-31", "2000-04-15", 1.00, "QTR"),
        ("DECQ", "2000-06-30", "2000-07-15", 1.10, "QTR"),
        ("DECQ", "2000-09-30", "2000-10-15", 1.20, "QTR"),
        ("DECQ", "2000-12-31", "2001-01-15", 1.30, "QTR"),
        ("DECQ", "2000-12-31", "2001-01-15", 4.60, "ANN"),  # exact sum of the 4 QTR values above
    ])

    result = clean.validate_qtr_ann_consistency(actuals)

    assert result["n_cases"] == 1
    assert result["n_passed"] == 1
    assert result["n_cases_december"] == 1


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
    """permno IS present here -- only target_date is null -- so this must
    land on the specific "unavailable_no_target_date" status, not the
    "unavailable_no_permno" status reserved for a missing permno (see
    test_resolve_share_basis_null_permno_is_unavailable for that case)."""
    step = _step_table([(100, "2000-01-01", 1.0)])
    value, status = clean.resolve_share_basis(step, pd.Series(["100"]), pd.Series([pd.NaT]))

    assert pd.isna(value.iloc[0])
    assert status.iloc[0] == "unavailable_no_target_date"


def test_attach_share_basis_classifies_no_change_vs_adjusted():
    """attach_share_basis() no longer sets eps_actual_adj (that moved to
    resolve_eps_basis(), which needs cfacshr_eps_basis to exist first) --
    see attach_share_basis's docstring. This test only checks the columns
    attach_share_basis is still responsible for."""
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

    assert "eps_actual_adj" not in result.columns

    assert result.loc[0, "share_basis_status"] == "no_change"
    assert result.loc[0, "cfacshr_anndats"] == 1.0

    assert result.loc[1, "share_basis_status"] == "adjusted"
    assert result.loc[1, "cfacshr_anndats"] == 2.0
    assert result.loc[1, "cfacshr_statpers"] == 4.0

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
    assert stats["unavailable_no_permno"] == 1


def test_real_nine_named_split_events_confirm_eps_basis_discriminator(real_event_table):
    """Regression test for the eps_basis discriminator's precision fix: 9
    real, independently-verified split events, three of them different
    fiscal quarters of the SAME ticker (TRH) -- proving the discriminator
    separates events on their own evidence rather than keying off a
    ticker-level artifact.

    Five are anndats_basis_confirmed (the original unconditional post-step
    assumption was right) -- UNCHANGED by the precision fix, since
    confirming the post-step basis is the one direction this discriminator
    still ever asserts (measured ~99.13% precise against Compustat `niq`).

    The other four (FAMR 1998-06-30, TRH 1997-06-30, IVCR 1995-09-30, VRLN
    1997-10-31) used to be prestep_basis_confirmed. These four are
    independently confirmed via Compustat `niq` to genuinely BE pre-step
    cases -- the OLD code's answer was actually right for these specific
    four, unlike AMSY/ASF/AEOS (see the negative-control test below, where
    the identical evidence pattern was wrong). But the rule that got these
    four right is only ~18.9% precise in general and cannot reliably tell
    them apart from AMSY/ASF/AEOS using only this ticker's own prior-
    quarter data, so the pipeline now correctly withholds an assertion here
    rather than guessing -- intentionally giving up four correct answers to
    eliminate many more wrong ones.

    Runs the FULL real pipeline (via the real_event_table fixture) rather
    than a hand-built mini-panel, because the discriminator needs each
    ticker's own PRIOR-quarter history to have anything to discriminate
    against.
    """
    panel, _stats = real_event_table

    # ticker, fpedats, expected evidence ratio, expected cfacshr_eps_basis,
    # expected sue_analyst BEFORE this fix (i.e. under commit 7dc0c83's
    # unconditional-cfacshr_anndats formula -- not the fully-unadjusted raw
    # value), expected sue_analyst AFTER. Unchanged from before the
    # precision fix.
    confirmed_events = [
        ("FEMP", "2000-09-30", 24.64, 1.0, 0.8750, 0.8750),
        ("MBI", "1997-09-30", 10.50, 1.5, -16.0000, -16.0000),
        ("BGH", "2009-09-30", 28.75, 1.0, 10.8000, 10.8000),
        ("TRH", "2001-06-30", float("inf"), 1.25, -1.0000, -1.0000),
        ("TRH", "2004-06-30", 5.60, 1.0, 0.1667, 0.1667),
    ]
    for ticker, fped, exp_ratio, exp_basis, exp_before, exp_after in confirmed_events:
        mask = (panel["ibes_ticker"] == ticker) & (panel["fpedats"] == pd.Timestamp(fped))
        assert mask.sum() == 1, f"expected exactly one real row for {ticker} {fped}"
        row = panel.loc[mask].iloc[0]

        assert row["eps_basis_status"] == "anndats_basis_confirmed", f"{ticker} {fped}: eps_basis_status"
        if np.isinf(exp_ratio):
            assert np.isinf(row["eps_basis_evidence_ratio"]), f"{ticker} {fped}: expected an inf evidence ratio"
        else:
            assert row["eps_basis_evidence_ratio"] == pytest.approx(exp_ratio, abs=0.01), f"{ticker} {fped}: evidence ratio"
        assert row["cfacshr_eps_basis"] == pytest.approx(exp_basis, abs=0.001), f"{ticker} {fped}: cfacshr_eps_basis"

        # "Before" = the immediately-prior code's sue_analyst (the
        # unconditional cfacshr_anndats-basis assumption this fix corrects)
        # -- independently re-derived from this row's own raw columns
        # rather than hardcoded from the review's summary numbers.
        if pd.notna(row["cfacshr_statpers"]) and pd.notna(row["cfacshr_anndats"]):
            r_c_old = row["cfacshr_statpers"] / row["cfacshr_anndats"]
        else:
            r_c_old = 1.0
        sue_before = clean.compute_sue_analyst(
            pd.Series([row["eps_actual"] * r_c_old]), pd.Series([row["consensus_meanest"]]), pd.Series([row["consensus_stdev"]])
        ).iloc[0]
        assert sue_before == pytest.approx(exp_before, abs=0.01), f"{ticker} {fped}: sue_analyst before this fix"
        assert row["sue_analyst"] == pytest.approx(exp_after, abs=0.01), f"{ticker} {fped}: sue_analyst after this fix"

    # ticker, fpedats, expected evidence ratio -- still reported (via
    # eps_basis_evidence_ratio) for transparency even though it is no
    # longer acted on to set a basis.
    now_undetermined_events = [
        ("FAMR", "1998-06-30", 5.80),
        ("TRH", "1997-06-30", 14.95),
        ("IVCR", "1995-09-30", 7.67),
        ("VRLN", "1997-10-31", 4.25),
    ]
    for ticker, fped, exp_ratio in now_undetermined_events:
        mask = (panel["ibes_ticker"] == ticker) & (panel["fpedats"] == pd.Timestamp(fped))
        assert mask.sum() == 1, f"expected exactly one real row for {ticker} {fped}"
        row = panel.loc[mask].iloc[0]

        assert row["eps_basis_status"] == "basis_undetermined_ambiguous", f"{ticker} {fped}: eps_basis_status"
        assert row["eps_basis_evidence_ratio"] == pytest.approx(exp_ratio, abs=0.01), f"{ticker} {fped}: evidence ratio"
        assert pd.isna(row["cfacshr_eps_basis"]), f"{ticker} {fped}: cfacshr_eps_basis must now be null"
        assert pd.isna(row["sue_analyst"]), f"{ticker} {fped}: sue_analyst must now be null"


def test_real_negative_controls_amsy_asf_aeos_no_longer_fabricate_prestep_surprises(real_event_table):
    """Negative controls -- the most important new tests for this fix.

    AMSY (fpedats 1995-12-31), ASF (fpedats 2000-09-30), and AEOS (fpedats
    2001-01-31) are three real events, independently arbitrated against
    Compustat's basis-free net income (`niq`, never seen by this pipeline),
    where the OLD discriminator's prestep_basis_confirmed call was WRONG:
    the ticker's own prior-quarter evidence decisively favored the
    pre-step candidate, but `niq` confirms the actual was genuinely still
    on the post-step basis -- exactly the "strong growth quarter looks
    identical to a stale pre-split value" confound this fix's removal is
    based on. Under the old code, ASF's sue_analyst was fabricated as
    -11.50 and AEOS's as -15.00 -- extreme, fake surprises. This fix must
    null all three rows outright, not merely change their value.
    """
    panel, _stats = real_event_table
    events = [("AMSY", "1995-12-31"), ("ASF", "2000-09-30"), ("AEOS", "2001-01-31")]
    old_fabricated_sue = {"ASF": -11.50, "AEOS": -15.00}

    for ticker, fped in events:
        mask = (panel["ibes_ticker"] == ticker) & (panel["fpedats"] == pd.Timestamp(fped))
        assert mask.sum() == 1, f"expected exactly one real row for {ticker} {fped}"
        row = panel.loc[mask].iloc[0]

        assert row["eps_basis_status"] == "basis_undetermined_ambiguous", f"{ticker} {fped}: eps_basis_status"
        assert pd.isna(row["cfacshr_eps_basis"]), f"{ticker} {fped}: cfacshr_eps_basis must be null"
        assert pd.isna(row["eps_actual_adj"]), f"{ticker} {fped}: eps_actual_adj must be null"
        # pd.isna already implies "not close to" any specific finite old
        # fabricated value, but assert it explicitly too for documentation:
        # a null sue_analyst can never resemble a specific fabricated number.
        assert pd.isna(row["sue_analyst"]), f"{ticker} {fped}: sue_analyst must be null"
        if ticker in old_fabricated_sue:
            fabricated = old_fabricated_sue[ticker]
            assert not (pd.notna(row["sue_analyst"]) and abs(row["sue_analyst"] - fabricated) < 1.0), (
                f"{ticker} {fped}: sue_analyst must not resemble the old fabricated value {fabricated}"
            )


def test_real_cfacshr_eps_basis_never_adopts_prestep_when_it_differs(real_event_table):
    """Structural invariant -- a strong guard against ever reintroducing
    this bug class: no row in the full real output may have
    cfacshr_eps_basis equal to cfacshr_prestep in any case where
    cfacshr_prestep differs from cfacshr_anndats and is non-null. I.e. the
    pipeline must never adopt the pre-step basis, structurally, not just
    "doesn't today."
    """
    panel, _stats = real_event_table

    prestep_differs = (
        panel["cfacshr_prestep"].notna()
        & panel["cfacshr_anndats"].notna()
        & ~np.isclose(panel["cfacshr_prestep"], panel["cfacshr_anndats"])
    )
    candidates = panel[prestep_differs & panel["cfacshr_eps_basis"].notna()]
    adopted_prestep = np.isclose(candidates["cfacshr_eps_basis"], candidates["cfacshr_prestep"])
    assert not adopted_prestep.any(), (
        f"{int(adopted_prestep.sum())} row(s) have cfacshr_eps_basis == cfacshr_prestep despite "
        "cfacshr_prestep differing from cfacshr_anndats -- the pipeline adopted the pre-step "
        "basis, which this fix is supposed to make structurally impossible."
    )


def test_real_null_cfacshr_eps_basis_propagates_to_all_derived_columns(real_event_table):
    """Null-propagation invariant: every row with a null cfacshr_eps_basis
    must also have null eps_actual_adj, sue_analyst, and sue_seasonal -- an
    undetermined basis must never silently leave a derived surprise measure
    looking computed.
    """
    panel, _stats = real_event_table
    undetermined = panel["cfacshr_eps_basis"].isna()
    assert panel.loc[undetermined, "eps_actual_adj"].isna().all()
    assert panel.loc[undetermined, "sue_analyst"].isna().all()
    assert panel.loc[undetermined, "sue_seasonal"].isna().all()


def test_resolve_eps_basis_multi_candidate_disagreement_does_not_default_to_assumed():
    """Fix 3 regression test: a synthetic multi-candidate-permno row where
    the candidates disagree on the resolved step, and at least one of them
    has a real step inside this row's own (fpedats, anndats] window, must
    NOT come out as anndats_basis_assumed. A genuine step-date ambiguity
    must never silently default to "no basis question here" just because
    more than one candidate permno was on the table.
    """
    panel = pd.DataFrame({
        "ibes_ticker": ["MULTI"],
        "permno": ["900,901"],
        "fpedats": [pd.Timestamp("2000-01-01")],
        "anndats": [pd.Timestamp("2000-02-01")],
        "eps_actual": [1.0],
        "cfacshr_anndats": [1.0],
        "share_basis_status": ["no_change"],
    })
    # candidate 900: real in-window step (prestep 2.0); candidate 901: also
    # a real in-window step, but a DIFFERENT prestep (3.0) -- disagreement.
    step = _step_table([
        (900, "1990-01-01", 2.0), (900, "2000-01-15", 1.0),
        (901, "1990-01-01", 3.0), (901, "2000-01-20", 1.0),
    ])

    result, _stats = clean.resolve_eps_basis(panel, step)

    assert result.iloc[0]["eps_basis_status"] != "anndats_basis_assumed"
    assert result.iloc[0]["eps_basis_status"] == "basis_undetermined_ambiguous"
    assert pd.isna(result.iloc[0]["cfacshr_eps_basis"])
    assert pd.isna(result.iloc[0]["cfacshr_step_date"])  # no single value survived the disagreement


def test_resolve_eps_basis_multi_candidate_agreement_proceeds_through_normal_logic():
    """Companion to the disagreement test above: when every CRSP-covered
    candidate permno agrees on both the resolved step date and
    prev_cfacshr, that agreed value is used and the row proceeds through
    the exact same reference-based logic as an ordinary single-permno row
    -- it is not automatically forced into basis_undetermined_ambiguous
    just because the permno happened to be multi-candidate.
    """
    panel = pd.DataFrame({
        "ibes_ticker": ["AGREE", "AGREE"],
        "permno": ["902", "902,903"],
        "fpedats": [pd.Timestamp("1999-10-01"), pd.Timestamp("2000-01-01")],
        "anndats": [pd.Timestamp("1999-10-20"), pd.Timestamp("2000-02-01")],
        "eps_actual": [2.0, 2.0],
        "cfacshr_anndats": [1.0, 1.0],
        "share_basis_status": ["no_change", "no_change"],
    })
    # Both 902 and 903 agree: a real in-window step on 2000-01-15 from 2.0
    # to 1.0. Candidate 903 has no CRSP coverage before that (never
    # observed), so it contributes no vote -- agreement is still exact.
    step = _step_table([(902, "1990-01-01", 2.0), (902, "2000-01-15", 1.0), (903, "2000-01-15", 1.0)])

    result, stats = clean.resolve_eps_basis(panel, step)
    current = result[result["fpedats"] == pd.Timestamp("2000-01-01")].iloc[0]

    assert pd.notna(current["cfacshr_step_date"])
    assert current["cfacshr_prestep"] == pytest.approx(2.0)
    # With a resolved value, this row proceeds through the exact same
    # reference-based logic as any single-permno row: the prior quarter's
    # own eps_actual/cfacshr_anndats (2.0/1.0 = 2.0) exactly matches the
    # post-step candidate (2.0/1.0 = 2.0) and not the pre-step candidate
    # (2.0/2.0 = 1.0), decisively confirming the post-step basis.
    assert current["eps_basis_status"] == "anndats_basis_confirmed"
    assert current["eps_basis_status"] != "anndats_basis_assumed"
    # Confirms this took the NORMAL reference-based path, not Fix 3's
    # special-cased disagreement route (there was no disagreement here).
    assert stats["multi_candidate_forced_ambiguous_count"] == 0


def test_resolve_eps_basis_multi_candidate_forced_ambiguous_prior_row_excluded_as_reference():
    """Regression test for a gap found in code review while building Fix 3:
    a quarter whose OWN basis is genuinely in question via the multi-
    candidate-disagreement route (basis_undetermined_ambiguous, but with
    W=_step_in_report_window False -- no single cfacshr_step_date survived
    the disagreement) must NOT be usable as a later quarter's "clean"
    prior-quarter reference, exactly like a reference-based ambiguous prior
    row already cannot be (see
    test_resolve_eps_basis_prior_row_itself_in_window_excludes_reference).

    Before this fix, the prior-quarter-reference safety check only read
    _step_in_report_window, which is False by construction for a Fix-3
    forced-ambiguous row -- so such a row would pass as a "clean" reference
    for the very next quarter, relocating exactly the kind of fabricated-
    surprise risk this whole discriminator exists to eliminate, one quarter
    later. Verified latent on the real 1995-2014 pull today (zero of the 3
    real forced-ambiguous rows have a following quarter that is itself
    in-window), but a real, reachable gap on different data -- fixed
    regardless, the same "cheap, correct, add it regardless" reasoning as
    Fix 2's positivity guard.
    """
    panel = pd.DataFrame({
        "ibes_ticker": ["CHAIN", "CHAIN"],
        "permno": ["800,801", "802"],
        "fpedats": [pd.Timestamp("2000-01-01"), pd.Timestamp("2000-04-01")],
        "anndats": [pd.Timestamp("2000-02-01"), pd.Timestamp("2000-04-20")],
        "eps_actual": [5.0, 5.0],
        "cfacshr_anndats": [1.0, 1.0],
        "share_basis_status": ["no_change", "no_change"],
    })
    step = _step_table([
        (800, "1990-01-01", 2.0), (800, "2000-01-15", 1.0),  # candidate 800: in-window step, prestep 2.0
        (801, "1990-01-01", 3.0), (801, "2000-01-20", 1.0),  # candidate 801: in-window step, prestep 3.0 -- disagrees
        (802, "1990-01-01", 2.0), (802, "2000-04-10", 1.0),  # quarter 2's own single-candidate in-window step
    ])

    result, stats = clean.resolve_eps_basis(panel, step)

    prior = result[result["fpedats"] == pd.Timestamp("2000-01-01")].iloc[0]
    current = result[result["fpedats"] == pd.Timestamp("2000-04-01")].iloc[0]

    # Sanity check the fixture actually exercises what it claims to: the
    # prior quarter must be the Fix-3 forced-ambiguous case, not the
    # ordinary reference-based one.
    assert prior["eps_basis_status"] == "basis_undetermined_ambiguous"
    assert pd.isna(prior["cfacshr_step_date"])  # W was False for it -- disagreement, not a resolved value
    assert stats["multi_candidate_forced_ambiguous_count"] == 1

    # The real assertion: quarter 2 must NOT have used quarter 1 (eps_actual
    # 5.0 / cfacshr_anndats 1.0 = a reference of 5.0) to decisively confirm
    # its own basis -- eps_actual is also 5.0 here specifically so that an
    # exact-match (ratio=inf, decisive) confirmation would result if the
    # bug were still present. With the prior quarter correctly excluded and
    # no other prior row available, quarter 2 must land on
    # basis_undetermined_no_reference instead.
    assert current["eps_basis_status"] == "basis_undetermined_no_reference"
    assert pd.isna(current["cfacshr_eps_basis"])


def test_real_step_in_report_window_structural_identity(real_event_table):
    """Structural identity, independently recomputed from the panel's own
    columns -- not merely re-reading the stats dict the production code
    itself already reports these numbers from.

    Before Fix 3, the four in-window eps_basis_status counts summed
    exactly to a single strict "step falls in this row's own window"
    boolean. Fix 3 deliberately breaks that single equality: it recovers a
    handful of multi-candidate-permno rows into basis_undetermined_
    ambiguous WITHOUT ever resolving a single cfacshr_step_date for them
    (no value survived the candidates' disagreement). This test checks two
    more precise sub-identities instead of one blunt total:

    1. Every anndats_basis_confirmed/basis_undetermined_no_reference row,
       and every basis_undetermined_ambiguous row with a non-null
       cfacshr_step_date, satisfies the strict single-valued step-in-window
       condition recomputed here from final output columns alone.
    2. The remaining basis_undetermined_ambiguous rows (null
       cfacshr_step_date) are exactly Fix 3's multi-candidate-disagreement
       rows, matching resolve_eps_basis's own internal count.

    On the real pull the strict in-window count is 1,644 and the
    multi-candidate-forced-without-a-resolved-step count is 3, summing to
    the reported step_in_report_window_total of 1,647. A mismatch here is a
    stop-and-report condition, not something to silently adjust.
    """
    panel, stats = real_event_table

    unavailable = panel["share_basis_status"].astype(str).str.startswith("unavailable_")
    strict_w = (
        panel["cfacshr_step_date"].notna()
        & (panel["cfacshr_step_date"] > panel["fpedats"])
        & (panel["cfacshr_step_date"] <= panel["anndats"])
        & panel["cfacshr_prestep"].notna()
        & (panel["cfacshr_prestep"] > 0)
        & panel["cfacshr_anndats"].notna()
        & ~np.isclose(panel["cfacshr_prestep"], panel["cfacshr_anndats"])
        & ~unavailable
    )

    confirmed_or_no_ref = panel["eps_basis_status"].isin(["anndats_basis_confirmed", "basis_undetermined_no_reference"])
    assert strict_w[confirmed_or_no_ref].all(), (
        "found an anndats_basis_confirmed/basis_undetermined_no_reference row that does not "
        "satisfy the independently-recomputed strict step-in-window condition"
    )
    assert panel.loc[strict_w, "eps_basis_status"].isin(
        ["anndats_basis_confirmed", "basis_undetermined_no_reference", "basis_undetermined_ambiguous"]
    ).all(), "found a strictly-in-window row landing on a status other than the three expected outcomes"

    ambiguous = panel["eps_basis_status"] == "basis_undetermined_ambiguous"
    ambiguous_with_step = ambiguous & panel["cfacshr_step_date"].notna()
    ambiguous_without_step = ambiguous & panel["cfacshr_step_date"].isna()
    assert (ambiguous_with_step == (ambiguous & strict_w)).all()
    assert int(ambiguous_without_step.sum()) == stats["eps_basis"]["multi_candidate_forced_ambiguous_count"]

    measured_strict = int(strict_w.sum())
    measured_total = measured_strict + int(ambiguous_without_step.sum())
    assert measured_total == stats["eps_basis"]["step_in_report_window_total"]
    assert measured_strict == 1644, (
        f"measured strict step-in-window count is {measured_strict}, expected 1644 -- "
        "stop and report rather than silently adjusting this expectation."
    )
    assert measured_total == 1647, (
        f"measured total in-window-or-flagged count is {measured_total}, expected 1647 -- "
        "stop and report rather than silently adjusting this expectation."
    )


def test_real_eps_basis_status_counts_match_fix_spec(real_event_table):
    """The exact six-value eps_basis_status count table after the
    precision fix (delete the pre-step-confirming direction; split the
    unavailable_ambiguous_permno case out of basis_unverified_no_crsp into
    its own basis_undetermined_ambiguous_permno status). A mismatch here
    (beyond ordinary small drift from re-pulling underlying data) is a
    stop-and-report condition, not something to paper over.
    """
    _panel, stats = real_event_table
    counts = stats["eps_basis"]
    expected = {
        "anndats_basis_assumed": 281002,
        "basis_unverified_no_crsp": 3063,
        "basis_undetermined_ambiguous_permno": 2132,
        "anndats_basis_confirmed": 689,
        "basis_undetermined_ambiguous": 723,
        "basis_undetermined_no_reference": 235,
    }
    for status, expected_count in expected.items():
        assert counts.get(status, 0) == expected_count, f"{status}: expected {expected_count}, got {counts.get(status, 0)}"
    assert sum(expected.values()) == 287844


# ---------------------------------------------------------------------------
# resolve_eps_basis -- the eps_basis discriminator fix, synthetic cases.
# ---------------------------------------------------------------------------


def test_build_cfacshr_step_table_sets_is_change_and_prev_cfacshr():
    """is_change is False (and prev_cfacshr null) on each permno's FIRST
    observation -- there is no "before" to compare it to -- and True only
    on a genuine change, with prev_cfacshr carrying the value in force
    immediately before it."""
    raw = pd.DataFrame({
        "permno": [1, 1, 1, 1, 2, 2],
        "date": pd.to_datetime([
            "2000-01-01", "2000-01-02", "2000-01-03", "2000-01-04", "2000-01-01", "2000-01-02",
        ]),
        "cfacshr": [2.0, 2.0, 1.0, 1.0, 5.0, 5.0],
    })

    step = clean._build_cfacshr_step_table(raw)

    p1 = step[step["permno"] == 1.0].sort_values("date").reset_index(drop=True)
    assert pd.isna(p1.loc[0, "prev_cfacshr"])
    assert p1.loc[0, "is_change"] == False  # noqa: E712
    assert p1.loc[1, "prev_cfacshr"] == pytest.approx(2.0)
    assert p1.loc[1, "is_change"] == True  # noqa: E712

    p2 = step[step["permno"] == 2.0].reset_index(drop=True)
    assert len(p2) == 1  # permno 2 never changes -- only its first-obs row survives
    assert pd.isna(p2.loc[0, "prev_cfacshr"])
    assert p2.loc[0, "is_change"] == False  # noqa: E712


def test_resolve_eps_basis_decisiveness_gate_boundary():
    """Ratio exactly 1.99 (just below EPS_BASIS_DECISIVE_MIN_RATIO=2.0)
    must be basis_undetermined_ambiguous; 2.01 (just above) must be a
    confirmed status -- the >= boundary in step 4.6 is inclusive on the
    confirmed side."""
    prestep_199 = 1.0 / 1.99
    prestep_201 = 1.0 / 2.01
    panel = pd.DataFrame({
        "ibes_ticker": ["R199", "R199", "R201", "R201"],
        "permno": ["500", "500", "501", "501"],
        "fpedats": [pd.Timestamp("2000-01-01"), pd.Timestamp("2000-04-01"),
                    pd.Timestamp("2000-01-01"), pd.Timestamp("2000-04-01")],
        "anndats": [pd.Timestamp("2000-01-20"), pd.Timestamp("2000-04-20"),
                    pd.Timestamp("2000-01-20"), pd.Timestamp("2000-04-20")],
        "eps_actual": [0.0, 1.0, 0.0, 1.0],
        "cfacshr_anndats": [1.0, 1.0, 1.0, 1.0],
        "share_basis_status": ["no_change"] * 4,
    })
    step = _step_table([
        (500, "1990-01-01", prestep_199), (500, "2000-04-10", 1.0),
        (501, "1990-01-01", prestep_201), (501, "2000-04-10", 1.0),
    ])

    result, _stats = clean.resolve_eps_basis(panel, step)

    # Each ticker has two rows (a prior quarter plus the in-window test
    # quarter) sharing the same ibes_ticker value -- select the in-window
    # (second, 2000-04-01) row explicitly rather than relying on row order.
    r199_row = result[(result["ibes_ticker"] == "R199") & (result["fpedats"] == pd.Timestamp("2000-04-01"))].iloc[0]
    r201_row = result[(result["ibes_ticker"] == "R201") & (result["fpedats"] == pd.Timestamp("2000-04-01"))].iloc[0]

    assert r199_row["eps_basis_evidence_ratio"] == pytest.approx(1.99)
    assert r199_row["eps_basis_status"] == "basis_undetermined_ambiguous"

    assert r201_row["eps_basis_evidence_ratio"] == pytest.approx(2.01)
    assert r201_row["eps_basis_status"] == "anndats_basis_confirmed"


def test_resolve_eps_basis_exact_match_gives_infinite_ratio_and_confirmed_status():
    """dmin == 0 (an exact match on one hypothesis) must give ratio == inf
    and a confirmed status -- not a crash, and not a null ratio. This is
    the mechanism behind the real TRH 2001-06-30 case."""
    panel = pd.DataFrame({
        "ibes_ticker": ["INFQ", "INFQ"],
        "permno": ["600", "600"],
        "fpedats": [pd.Timestamp("2000-01-01"), pd.Timestamp("2000-04-01")],
        "anndats": [pd.Timestamp("2000-01-20"), pd.Timestamp("2000-04-20")],
        "eps_actual": [1.0, 1.0],
        "cfacshr_anndats": [1.0, 1.0],
        "share_basis_status": ["no_change", "no_change"],
    })
    step = _step_table([(600, "1990-01-01", 2.0), (600, "2000-04-10", 1.0)])

    result, _stats = clean.resolve_eps_basis(panel, step)
    row = result.iloc[1]

    assert np.isinf(row["eps_basis_evidence_ratio"])
    assert row["eps_basis_status"] == "anndats_basis_confirmed"


def test_resolve_eps_basis_step_at_fpedats_out_of_scope_step_at_anndats_in_scope():
    """A step landing exactly AT fpedats is out of scope (the condition is
    strictly > fpedats: financials prepared entirely post-step have no
    ambiguity to resolve). A step landing exactly AT anndats IS in scope
    (the condition is <= anndats, inclusive)."""
    panel = pd.DataFrame({
        "ibes_ticker": ["ATFP", "ATAN"],
        "permno": ["700", "701"],
        "fpedats": [pd.Timestamp("2000-04-01"), pd.Timestamp("2000-04-01")],
        "anndats": [pd.Timestamp("2000-04-20"), pd.Timestamp("2000-04-20")],
        "eps_actual": [1.0, 1.0],
        "cfacshr_anndats": [1.0, 1.0],
        "share_basis_status": ["no_change", "no_change"],
    })
    step = _step_table([
        (700, "1990-01-01", 2.0), (700, "2000-04-01", 1.0),   # step exactly AT fpedats
        (701, "1990-01-01", 2.0), (701, "2000-04-20", 1.0),   # step exactly AT anndats
    ])

    result, _stats = clean.resolve_eps_basis(panel, step)
    by_ticker = result.set_index("ibes_ticker")

    assert by_ticker.loc["ATFP", "eps_basis_status"] == "anndats_basis_assumed"
    # In scope, but with no prior-quarter row at all to reference -- lands
    # in basis_undetermined_no_reference rather than being silently
    # dropped back to "assumed".
    assert by_ticker.loc["ATAN", "eps_basis_status"] == "basis_undetermined_no_reference"


def test_resolve_eps_basis_prior_row_itself_in_window_excludes_reference():
    """If the immediately-preceding row is ITSELF basis-ambiguous (its own
    step_in_report_window is True), it cannot be trusted as a reference for
    the current row -- the current row must land in
    basis_undetermined_no_reference even though a numeric prior eps/basis
    value technically exists."""
    step = _step_table([
        (710, "1990-01-01", 2.0), (710, "2000-01-15", 5.0), (710, "2000-04-10", 1.0),
    ])
    panel = pd.DataFrame({
        "ibes_ticker": ["PRVW", "PRVW"],
        "permno": ["710", "710"],
        "fpedats": [pd.Timestamp("2000-01-01"), pd.Timestamp("2000-04-01")],
        "anndats": [pd.Timestamp("2000-01-20"), pd.Timestamp("2000-04-20")],
        "eps_actual": [1.0, 1.0],
        "cfacshr_anndats": [5.0, 1.0],
        "share_basis_status": ["adjusted", "adjusted"],
    })

    result, _stats = clean.resolve_eps_basis(panel, step)

    # Row 0 (the prior quarter) is itself in-window (a step lands in its
    # own (fpedats, anndats]) -- confirm the fixture actually exercises
    # what it claims to before trusting row 1's result.
    assert result.iloc[0]["eps_basis_status"] != "anndats_basis_assumed"
    assert result.iloc[1]["eps_basis_status"] == "basis_undetermined_no_reference"


def test_resolve_eps_basis_prior_gap_day_count_boundaries():
    """Gap of 44 and 136 days (just outside [45, 135]) both yield
    basis_undetermined_no_reference; 45 and 135 (the inclusive boundary)
    are accepted as a usable reference."""
    rows = []
    step_rows = []
    for i, gap in enumerate([44, 45, 135, 136]):
        ticker = f"GAP{gap}"
        permno = 900 + i
        prior_fped = pd.Timestamp("2000-01-01")
        cur_fped = prior_fped + pd.DateOffset(days=int(gap))
        cur_anndats = cur_fped + pd.DateOffset(days=10)
        step_date = cur_fped + pd.DateOffset(days=5)
        rows.append({
            "ibes_ticker": ticker, "permno": str(permno), "fpedats": prior_fped,
            "anndats": prior_fped + pd.DateOffset(days=5), "eps_actual": 0.0,
            "cfacshr_anndats": 100.0, "share_basis_status": "no_change",
        })
        rows.append({
            "ibes_ticker": ticker, "permno": str(permno), "fpedats": cur_fped,
            "anndats": cur_anndats, "eps_actual": 1.0,
            "cfacshr_anndats": 1.0, "share_basis_status": "no_change",
        })
        step_rows.append((permno, "1990-01-01", 100.0))
        step_rows.append((permno, step_date.strftime("%Y-%m-%d"), 1.0))

    panel = pd.DataFrame(rows)
    step = _step_table(step_rows)

    result, _stats = clean.resolve_eps_basis(panel, step)
    by_ticker_current = result[result["eps_actual"] == 1.0].set_index("ibes_ticker")

    assert by_ticker_current.loc["GAP44", "eps_basis_status"] == "basis_undetermined_no_reference"
    assert by_ticker_current.loc["GAP136", "eps_basis_status"] == "basis_undetermined_no_reference"
    assert by_ticker_current.loc["GAP45", "eps_basis_status"] != "basis_undetermined_no_reference"
    assert by_ticker_current.loc["GAP135", "eps_basis_status"] != "basis_undetermined_no_reference"


def test_resolve_eps_basis_unavailable_share_basis_status_fixes_sue_seasonal_fail_closed():
    """Regression test for the sue_seasonal fail-closed bug: before this
    fix, a row whose share_basis_status started with "unavailable_" left
    cfacshr_anndats (and so eps_actual_adj) null, which would silently
    propagate forward and null out eps_diff_yoy/sue_seasonal for every
    LATER quarter that looked this one up as its own year-ago reference --
    failing CLOSED rather than computing a seasonal surprise sue_analyst
    also couldn't provide. The fix gives an unresolvable basis
    cfacshr_eps_basis=1.0 (no rescaling, but a DEFINED divisor), so
    eps_actual_adj -- and everything built on top of it -- stays
    computable.
    """
    eps_values = [1.00, 1.05, 0.98, 1.10, 1.20, 1.12, 1.30, 1.25, 1.40, 1.35, 1.50, 1.45]
    quarters = pd.date_range("2000-03-31", periods=len(eps_values), freq="QE")
    panel = pd.DataFrame({
        "ibes_ticker": ["FCLS"] * len(eps_values),
        "permno": ["800"] * len(eps_values),
        "fpedats": quarters,
        "anndats": quarters + datetime.timedelta(days=15),
        "eps_actual": eps_values,
        "cfacshr_anndats": [np.nan] + [1.0] * (len(eps_values) - 1),
        "share_basis_status": ["unavailable_no_crsp_coverage"] + ["no_change"] * (len(eps_values) - 1),
    })
    step_table = _empty_step_table()  # no CRSP coverage at all for this permno

    result, _stats = clean.resolve_eps_basis(panel, step_table)

    first = result.iloc[0]
    assert first["eps_basis_status"] == "basis_unverified_no_crsp"
    assert first["cfacshr_eps_basis"] == pytest.approx(1.0)
    assert pd.notna(first["eps_actual_adj"])
    assert first["eps_actual_adj"] == pytest.approx(first["eps_actual"])

    seasonal = clean.compute_sue_seasonal(
        result, ticker_col="ibes_ticker", eps_col="eps_actual_adj", min_history=4
    ).sort_values("fpedats").reset_index(drop=True)

    # Index 4 is exactly one year after index 0 (the unavailable_ row) --
    # its eps_diff_yoy depends directly on index 0's eps_actual_adj being
    # non-null. Under the old fail-closed bug this would be null.
    assert pd.notna(seasonal.loc[4, "eps_year_ago"])
    assert pd.notna(seasonal.loc[4, "eps_diff_yoy"])
    # By index 8 there is enough prior history for sue_seasonal itself
    # (matches the shape of the existing min-history-gate test).
    assert seasonal.loc[8:, "sue_seasonal"].notna().all()


def test_compute_sue_seasonal_raises_clear_error_on_repeated_call():
    """Merge-suffix guard (small fix #1): calling compute_sue_seasonal a
    second time on a frame that already carries one of its own output
    columns must raise a clear ValueError naming the column, not silently
    produce merge-suffixed columns and fail downstream with a confusing
    bare KeyError."""
    panel = _panel([("AAA", "2000-03-31", 1.00), ("AAA", "2000-06-30", 1.10)])
    panel["sue_seasonal"] = [np.nan, np.nan]

    with pytest.raises(ValueError, match="sue_seasonal"):
        clean.compute_sue_seasonal(panel)


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
