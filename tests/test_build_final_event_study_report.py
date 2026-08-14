"""Tests for scripts/build_final_event_study_report.py -- Slice 10 sub-slice
A ("final-report-scaffold"): a scaffold PDF with data-loading, methodology,
and limitations sections, and NO charts yet (those come in sub-slice 10b).

Mirrors tests/test_build_earnings_data_report.py's pattern: unit tests for
the helper functions (JSON validation, prose derivation), one integration
test against a hand-built fixture stats JSON that exercises the full
`build_final_event_study_report` pipeline, and one real-data smoke test
that skips when the real stats JSON is not present locally.
"""

import copy
import json
import sys
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_final_event_study_report as report  # noqa: E402

PDF_MAGIC = b"%PDF"

REAL_STATS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "results" / "event_study_stats.json"
)
REAL_EVENTS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "events" / "equity_events_1996_2013.parquet"
)


def _make_synthetic_stats() -> dict:
    """A minimally-shaped stats dict matching the schema Slice 9 emits --
    two horizons, both return_types, and two quintile records per (horizon,
    return_type) is enough for the scaffold to exercise every helper, and
    small enough that every count/date this report reads back is
    unambiguous by construction."""
    return {
        "meta": {
            "n_day0_ok": 100,
            "horizons": [5, 20],
            "n_quantiles_requested": 5,
            "confidence": 0.95,
            "sue_scale_by_source": {
                "analyst": {"n": 60, "sd": 8.0},
                "seasonal": {"n": 30, "sd": 2.5},
            },
            "n_permno_contested": 3,
            "n_duplicate_firm_day_rows": 7,
            "n_by_sue_source": {"analyst": 60, "seasonal": 30, "none": 10},
            "n_sue_available": 90,
            "rank_cell_stats": {
                "n_cells": 8,
                "median_cell_size": 11.0,
                "min_cell_size": 2,
                "largest_tied_value_block": {
                    "source": "analyst", "value": 0.0, "count": 4, "fraction_of_source": 0.067,
                },
            },
        },
        "summary_stats": [
            {"horizon": 5, "return_type": "raw", "column": "ret_fwd_5d",
             "n": 100, "mean": 0.001, "median": 0.0, "sd": 0.05, "se": 0.005,
             "t_stat": 0.2, "ci_low": -0.009, "ci_high": 0.011, "confidence": 0.95},
            {"horizon": 20, "return_type": "raw", "column": "ret_fwd_20d",
             "n": 100, "mean": 0.010, "median": 0.005, "sd": 0.10, "se": 0.010,
             "t_stat": 1.0, "ci_low": -0.010, "ci_high": 0.030, "confidence": 0.95},
        ],
        "quantile_sort": [
            {"horizon": 5, "return_type": "raw", "quintile": 1, "n": 18,
             "mean": -0.003, "median": -0.002, "se": 0.001},
            {"horizon": 5, "return_type": "raw", "quintile": 5, "n": 18,
             "mean": 0.005, "median": 0.003, "se": 0.001},
        ],
        "quantile_spread": [
            {"horizon": 5, "return_type": "raw", "quantile_low": 1, "quantile_high": 5,
             "n_low": 18, "n_high": 18, "mean_low": -0.003, "mean_high": 0.005,
             "spread": 0.008, "se": 0.002, "t_stat": 4.0},
        ],
        "spearman_ic": [
            {"horizon": 5, "return_type": "raw", "column": "ret_fwd_5d",
             "n": 90, "ic": 0.06, "p_value": 1e-5},
            {"horizon": 20, "return_type": "raw", "column": "ret_fwd_20d",
             "n": 90, "ic": 0.05, "p_value": 1e-4},
        ],
    }


def _make_synthetic_scope() -> dict:
    """Matches _make_synthetic_stats's meta.n_day0_ok by construction, so
    the sanity check in _build_summary doesn't reject the fixture."""
    return {
        "n_events_total": 110,
        "n_day0_ok": 100,
        "start_date": "2001-01-15",
        "end_date": "2005-12-20",
    }


# --- load_stats: schema validation --------------------------------------------


def test_load_stats_accepts_well_shaped_json(tmp_path):
    stats = _make_synthetic_stats()
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats))
    loaded = report.load_stats(p)
    assert loaded["meta"]["n_day0_ok"] == 100
    assert loaded["meta"]["horizons"] == [5, 20]


@pytest.mark.parametrize("missing_key", ["meta", "summary_stats", "quantile_sort",
                                          "quantile_spread", "spearman_ic"])
def test_load_stats_rejects_missing_top_level_key(tmp_path, missing_key):
    stats = _make_synthetic_stats()
    del stats[missing_key]
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats))
    with pytest.raises(ValueError, match=missing_key):
        report.load_stats(p)


@pytest.mark.parametrize("missing_meta_key", ["n_day0_ok", "horizons", "n_by_sue_source",
                                               "sue_scale_by_source", "n_sue_available",
                                               "rank_cell_stats"])
def test_load_stats_rejects_missing_meta_key(tmp_path, missing_meta_key):
    stats = _make_synthetic_stats()
    del stats["meta"][missing_meta_key]
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats))
    with pytest.raises(ValueError, match=missing_meta_key):
        report.load_stats(p)


def test_load_stats_rejects_missing_source_in_scale(tmp_path):
    stats = _make_synthetic_stats()
    del stats["meta"]["sue_scale_by_source"]["analyst"]
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats))
    with pytest.raises(ValueError, match="analyst"):
        report.load_stats(p)


def test_load_stats_rejects_source_missing_sd(tmp_path):
    stats = _make_synthetic_stats()
    del stats["meta"]["sue_scale_by_source"]["seasonal"]["sd"]
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats))
    with pytest.raises(ValueError, match="sd"):
        report.load_stats(p)


# --- load_events_scope --------------------------------------------------------


def test_load_events_scope_reads_date_range_from_ok_subset(tmp_path):
    df = pd.DataFrame(
        {
            "anndats": pd.to_datetime(
                ["2001-01-15", "2003-06-10", "2005-12-20", "1999-01-01", "2010-01-01"]
            ),
            "day0_status": ["ok", "ok", "ok", "no_trading_day_at_or_after_anndats",
                            "anndats_before_permno_coverage_start"],
        }
    )
    p = tmp_path / "events.parquet"
    df.to_parquet(p)
    scope = report.load_events_scope(p)
    assert scope["n_events_total"] == 5
    assert scope["n_day0_ok"] == 3
    assert scope["start_date"] == "2001-01-15"
    assert scope["end_date"] == "2005-12-20"


def test_load_events_scope_rejects_all_non_ok(tmp_path):
    df = pd.DataFrame(
        {
            "anndats": pd.to_datetime(["2001-01-15", "2003-06-10"]),
            "day0_status": ["no_trading_day_at_or_after_anndats",
                            "anndats_before_permno_coverage_start"],
        }
    )
    p = tmp_path / "events.parquet"
    df.to_parquet(p)
    with pytest.raises(ValueError, match="day0_status"):
        report.load_events_scope(p)


# --- prose helpers ------------------------------------------------------------


def test_format_horizons_english_list():
    assert report._format_horizons([1]) == "+1d"
    assert report._format_horizons([1, 5]) == "+1d and +5d"
    assert report._format_horizons([1, 5, 10, 20, 40, 60]) == (
        "+1d, +5d, +10d, +20d, +40d and +60d"
    )


def test_format_horizons_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        report._format_horizons([])


def test_build_summary_derives_counts_and_range_from_inputs():
    stats = _make_synthetic_stats()
    scope = _make_synthetic_scope()
    summary = report._build_summary(stats, scope)
    assert "100" in summary  # n_day0_ok
    assert "2001-01-15" in summary
    assert "2005-12-20" in summary
    assert "+5d and +20d" in summary
    assert "90" in summary  # n_sue_available
    # IC covers all enumerated horizons in the fixture (both +5d and +20d)
    assert "at all 2 of those horizons" in summary


def test_build_summary_reports_partial_ic_coverage_when_ic_missing_horizons():
    """If Slice 9 emits IC on a subset of the enumerated horizons, the
    summary lede must NOT read 'across all N horizons' -- it must say
    'at K of those N horizons' with the covered subset explicit."""
    stats = _make_synthetic_stats()
    # Drop the +5d IC row, leaving only +20d
    stats["spearman_ic"] = [row for row in stats["spearman_ic"] if row["horizon"] != 5]
    scope = _make_synthetic_scope()
    summary = report._build_summary(stats, scope)
    assert "all 2 of those horizons" not in summary
    assert "at 1 of those 2 horizons" in summary
    assert "+20d" in summary


def test_build_summary_rejects_scope_mismatch():
    """The events-file n_day0_ok must match stats meta n_day0_ok -- a
    mismatch means one of the two upstream files has drifted relative to
    the other and the population claim is wrong. This is the primary
    cross-file sanity check the scaffold enforces."""
    stats = _make_synthetic_stats()
    scope = _make_synthetic_scope()
    scope["n_day0_ok"] = 999
    with pytest.raises(ValueError, match="does not match"):
        report._build_summary(stats, scope)


def test_methodology_section_dispersion_gated_on_which_source_is_wider():
    """Which source is described as 'wider' must follow the actual sds.
    Flipping the sds must flip the wording, not leave a hardcoded claim.
    Equal sds must NOT be described as a gap."""
    stats = _make_synthetic_stats()
    scope = _make_synthetic_scope()
    _, body = report._build_methodology_section(stats, scope, report.DEFAULT_INPUT_EVENTS)
    assert "analyst-benchmarked surprise has sample sd 8.000" in body
    assert "seasonal-benchmarked surprise sd 2.500" in body
    assert "3.20x dispersion gap" in body

    flipped = copy.deepcopy(stats)
    flipped["meta"]["sue_scale_by_source"]["analyst"]["sd"] = 2.5
    flipped["meta"]["sue_scale_by_source"]["seasonal"]["sd"] = 8.0
    _, body_flipped = report._build_methodology_section(flipped, scope, report.DEFAULT_INPUT_EVENTS)
    assert "seasonal-benchmarked surprise has sample sd 8.000" in body_flipped
    assert "analyst-benchmarked surprise sd 2.500" in body_flipped

    equal_sds = copy.deepcopy(stats)
    equal_sds["meta"]["sue_scale_by_source"]["analyst"]["sd"] = 5.0
    equal_sds["meta"]["sue_scale_by_source"]["seasonal"]["sd"] = 5.0
    _, body_equal = report._build_methodology_section(equal_sds, scope, report.DEFAULT_INPUT_EVENTS)
    assert "no dispersion gap between them" in body_equal
    assert "1.00x" not in body_equal
    assert "wider" not in body_equal


def test_methodology_section_pulls_rank_cell_stats():
    stats = _make_synthetic_stats()
    scope = _make_synthetic_scope()
    _, body = report._build_methodology_section(stats, scope, report.DEFAULT_INPUT_EVENTS)
    assert "8 such cells" in body
    assert "median cell size 11 rows," in body  # trailing 'rows,' pins substring so '110' can't spoof it
    assert "smallest 2 --" in body  # trailing ' --' same reason


def test_methodology_section_derives_illustrative_years_from_scope():
    """The point-in-time example should reference years from the actual
    data range, not a hardcoded 2004/2013/1996 fixture. If the scope
    shifts, the sentence must shift with it. (The events-file path in
    the same section carries the year range as a filename literal --
    that's a constant, not a data-derived claim, so we don't assert
    absence there.)"""
    stats = _make_synthetic_stats()
    scope = _make_synthetic_scope()
    # Fixture scope: 2001-01-15 to 2005-12-20 -- first=2001, last=2005, mid=2003
    _, body = report._build_methodology_section(stats, scope, report.DEFAULT_INPUT_EVENTS)
    # Isolate the point-in-time paragraph (the second half of the section
    # after 'Point-in-time discipline.') and assert only that paragraph's
    # example years follow the scope.
    pit_paragraph = body[body.index("Point-in-time discipline.") :]
    assert "2003Q1" in pit_paragraph
    assert "never against events from 2005" in pit_paragraph
    assert "a 2001 event's quintile depended on 2005 data" in pit_paragraph
    # None of the hardcoded fixture years may appear in the point-in-time
    # example (they can still appear elsewhere e.g. in the file path).
    for hardcoded in ("2013", "2004Q1", "1996"):
        assert hardcoded not in pit_paragraph, (
            f"point-in-time example still contains hardcoded {hardcoded!r}"
        )


def test_limitations_section_derives_population_gap_when_present():
    stats = _make_synthetic_stats()
    scope = _make_synthetic_scope()
    _, body = report._build_limitations_section(stats)
    # Population gap: 100 total, 90 ranked, 10 none → gap of 10.
    assert "100" in body
    assert "90" in body
    assert "10" in body
    assert "difference of 10 events" in body


def test_limitations_section_drops_gap_clause_when_no_gap():
    """A rerun where every event has a surprise (n_sue_available ==
    n_day0_ok) must not leave the prose asserting a mismatch that isn't
    there."""
    stats = _make_synthetic_stats()
    stats["meta"]["n_sue_available"] = 100
    stats["meta"]["n_by_sue_source"] = {"analyst": 70, "seasonal": 30, "none": 0}
    _, body = report._build_limitations_section(stats)
    assert "difference of" not in body
    assert "no subsetting" in body


def test_limitations_section_derives_dup_and_contested_clause():
    stats = _make_synthetic_stats()
    _, body = report._build_limitations_section(stats)
    # Tight substring pins: full-clause fragments including the count and
    # its trailing context so a stray '7' or '3' elsewhere in the body
    # (percentages, other counters) can't spoof the assertion.
    assert "7 of the 100 events used here" in body  # n_duplicate_firm_day_rows
    # n_permno_contested = 3 rendered as '3 (3.0%)' with 'carry a permno' nearby
    assert "carry a permno" in body
    assert "3 (3.0%)" in body


def test_limitations_section_drops_dup_clause_when_zero():
    stats = _make_synthetic_stats()
    stats["meta"]["n_duplicate_firm_day_rows"] = 0
    stats["meta"]["n_permno_contested"] = 0
    _, body = report._build_limitations_section(stats)
    assert "no duplicate firm-day rows or contested permnos" in body


def test_limitations_section_reports_not_recorded_when_dup_keys_missing():
    """Round-1 review finding #1: missing keys must NOT default to zero and
    produce the 'unusually clean' branch -- a schema drift that omits
    these counts is a *different* state from 'clean' and must be labelled
    as such."""
    stats = _make_synthetic_stats()
    del stats["meta"]["n_duplicate_firm_day_rows"]
    del stats["meta"]["n_permno_contested"]
    _, body = report._build_limitations_section(stats)
    assert "did not report" in body
    assert "unusually clean" not in body
    assert "cannot state" in body


def test_limitations_section_reports_not_specified_when_none_missing():
    """Round-2 class-sweep of round-1 finding #1: n_by_sue_source['none']
    is documented optional in load_stats. If a build omits it, the
    population-gap clause must NOT falsely attribute the gap to a zero
    'neither' count; it must say the per-source breakdown of the gap is
    unavailable."""
    stats = _make_synthetic_stats()
    # Drop 'none' and keep n_sue_available < n_day0_ok so the gap branch fires
    del stats["meta"]["n_by_sue_source"]["none"]
    _, body = report._build_limitations_section(stats)
    assert "per-source breakdown of that gap is not reported" in body
    assert "comprising the 0 announcements" not in body


def test_methodology_section_reports_not_specified_when_none_missing():
    """Same class as the limitations test above, one layer up: the
    methodology 'A got analyst, B got seasonal, C had neither' sentence
    must NOT print '0 had neither' when the 'none' count is absent."""
    stats = _make_synthetic_stats()
    del stats["meta"]["n_by_sue_source"]["none"]
    scope = _make_synthetic_scope()
    _, body = report._build_methodology_section(stats, scope, report.DEFAULT_INPUT_EVENTS)
    assert "0 had neither" not in body
    assert "did not report the neither-source count" in body


def test_methodology_section_pathological_zero_sd_says_so_honestly():
    """Round-2 finding #4: sd == 0 on either side is a data-integrity red
    flag, not a 'different scales' story. Prose must call it out."""
    stats = _make_synthetic_stats()
    stats["meta"]["sue_scale_by_source"]["analyst"]["sd"] = 0.0
    scope = _make_synthetic_scope()
    _, body = report._build_methodology_section(stats, scope, report.DEFAULT_INPUT_EVENTS)
    assert "zero sample dispersion" in body
    assert "data-integrity signal" in body
    # Must NOT print the standard 'different scales' framing with a 0-sd source
    assert "a 0.00x dispersion gap" not in body


def test_methodology_section_uses_derived_events_filename():
    """Round-2 finding #2: the dataset sentence should reference the
    actual events file name, not a hardcoded '1996_2013' basename."""
    stats = _make_synthetic_stats()
    scope = _make_synthetic_scope()
    custom_path = Path("data/events/equity_events_2020_2023.parquet")
    _, body = report._build_methodology_section(stats, scope, custom_path)
    assert "equity_events_2020_2023.parquet" in body
    assert "equity_events_1996_2013.parquet" not in body


# --- integration --------------------------------------------------------------


def _write_synthetic_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(json.dumps(_make_synthetic_stats()))

    events_df = pd.DataFrame(
        {
            "anndats": pd.to_datetime(
                ["2001-01-15"] + ["2003-06-10"] * 98 + ["2005-12-20"]
            ),
            "day0_status": ["ok"] * 100,
        }
    )
    events_path = tmp_path / "events.parquet"
    events_df.to_parquet(events_path)

    return stats_path, events_path


def test_build_final_event_study_report_produces_valid_pdf_from_synthetic(tmp_path):
    stats_path, events_path = _write_synthetic_fixtures(tmp_path)
    out = tmp_path / "final_report.pdf"

    written = report.build_final_event_study_report(out, stats_path, events_path)
    assert written == out
    assert out.exists()
    data = out.read_bytes()
    assert data[:4] == PDF_MAGIC
    assert data.rstrip().endswith(b"%%EOF")
    # >1KB per the plan; a real prose-only report clears this trivially.
    assert out.stat().st_size > 1024


def test_build_final_event_study_report_passes_no_charts_to_builder(tmp_path):
    """This is the scaffold sub-slice: charts=[] is deliberate. Sub-slice
    10b's job is to remove this constraint. Guarding it here so a stray
    early chart addition is caught by tests, not a review."""
    stats_path, events_path = _write_synthetic_fixtures(tmp_path)
    out = tmp_path / "final_report.pdf"

    with mock.patch.object(report.report_builder, "build_pdf_report") as mock_build:
        mock_build.return_value = out
        report.build_final_event_study_report(out, stats_path, events_path)

    assert mock_build.called
    kwargs = mock_build.call_args.kwargs
    assert list(kwargs["charts"]) == []
    # Two sections: methodology + limitations. No more, no fewer, for the scaffold.
    assert len(kwargs["sections"]) == 2
    headings = [heading for heading, _ in kwargs["sections"]]
    assert any("methodology" in h.lower() for h in headings)
    assert any("limitations" in h.lower() for h in headings)
    # Summary lede is populated (not empty/None).
    assert kwargs["summary"]
    assert "drift" in kwargs["summary"].lower()


# --- real-data smoke test -----------------------------------------------------


@pytest.mark.skipif(
    not (REAL_STATS_PATH.exists() and REAL_EVENTS_PATH.exists()),
    reason="Real event-study stats and/or events file not present locally.",
)
def test_build_final_event_study_report_runs_on_real_data(tmp_path):
    """Uses a temp output path -- must NOT overwrite the committed
    reports/event_study_result.pdf from a test run."""
    out = tmp_path / "event_study_result.pdf"
    written = report.build_final_event_study_report(
        out, REAL_STATS_PATH, REAL_EVENTS_PATH
    )
    assert written.exists()
    data = written.read_bytes()
    assert data[:4] == PDF_MAGIC
    assert data.rstrip().endswith(b"%%EOF")
    assert written.stat().st_size > 5 * 1024
