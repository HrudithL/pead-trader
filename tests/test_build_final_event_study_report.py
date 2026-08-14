"""Tests for scripts/build_final_event_study_report.py.

Slice 10 sub-slice A ("final-report-scaffold") shipped the JSON schema,
load helpers, methodology and limitations sections, and a charts=[]
scaffold. Sub-slice B ("final-report-charts") adds the Results section
plus three market-adjusted charts (quintile mean by horizon, Q5-Q1
spread with 95% CI, Spearman IC by horizon with 95% band). These tests
cover both.
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
    two horizons, both return_types, five quintile records per (horizon,
    return_type), and Q5-Q1 spread + Spearman IC rows for both raw and
    mktadj. This is the shared fixture across the scaffold (10a) tests
    and the charts+Results (10b) tests.

    Design of the mktadj quintile means below: at both horizons the sort
    is strictly increasing Q1..Q5 and Q5-Q1 is positive; the Q5-Q1 spread
    row's t_stat is >> 1.96 at both horizons; and each mktadj Spearman IC
    is positive with p << 0.05. Together those inputs put the Results-
    section conclusion into the 'all-positive' branch by default -- 10b's
    branch-flip tests deep-copy this fixture and mutate one or two fields
    at a time to push the conclusion into each of the other three branches
    without having to rebuild the whole dict."""
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
            # raw: 5 rows per horizon so len == 5 (fixture completeness only;
            # nothing in 10a or 10b reads the raw quintile rows for prose or
            # charts, but Slice 9's schema always has them, and the
            # `_mktadj_by_horizon_and_quintile` helper filters by return_type
            # anyway).
            {"horizon": 5, "return_type": "raw", "quintile": 1, "n": 18,
             "mean": -0.003, "median": -0.002, "se": 0.001},
            {"horizon": 5, "return_type": "raw", "quintile": 2, "n": 18,
             "mean": -0.001, "median": -0.001, "se": 0.001},
            {"horizon": 5, "return_type": "raw", "quintile": 3, "n": 18,
             "mean":  0.000, "median":  0.000, "se": 0.001},
            {"horizon": 5, "return_type": "raw", "quintile": 4, "n": 18,
             "mean":  0.002, "median":  0.001, "se": 0.001},
            {"horizon": 5, "return_type": "raw", "quintile": 5, "n": 18,
             "mean":  0.005, "median":  0.003, "se": 0.001},
            # mktadj at +5d: strictly increasing Q1..Q5, positive Q5-Q1.
            {"horizon": 5, "return_type": "mktadj", "quintile": 1, "n": 18,
             "mean": -0.004, "median": -0.003, "se": 0.001},
            {"horizon": 5, "return_type": "mktadj", "quintile": 2, "n": 18,
             "mean": -0.002, "median": -0.001, "se": 0.001},
            {"horizon": 5, "return_type": "mktadj", "quintile": 3, "n": 18,
             "mean":  0.000, "median":  0.000, "se": 0.001},
            {"horizon": 5, "return_type": "mktadj", "quintile": 4, "n": 18,
             "mean":  0.002, "median":  0.001, "se": 0.001},
            {"horizon": 5, "return_type": "mktadj", "quintile": 5, "n": 18,
             "mean":  0.004, "median":  0.003, "se": 0.001},
            # mktadj at +20d: strictly increasing, wider Q5-Q1 than +5d.
            {"horizon": 20, "return_type": "mktadj", "quintile": 1, "n": 18,
             "mean": -0.010, "median": -0.008, "se": 0.002},
            {"horizon": 20, "return_type": "mktadj", "quintile": 2, "n": 18,
             "mean": -0.005, "median": -0.004, "se": 0.002},
            {"horizon": 20, "return_type": "mktadj", "quintile": 3, "n": 18,
             "mean":  0.000, "median":  0.000, "se": 0.002},
            {"horizon": 20, "return_type": "mktadj", "quintile": 4, "n": 18,
             "mean":  0.005, "median":  0.004, "se": 0.002},
            {"horizon": 20, "return_type": "mktadj", "quintile": 5, "n": 18,
             "mean":  0.012, "median":  0.010, "se": 0.002},
        ],
        "quantile_spread": [
            {"horizon": 5, "return_type": "raw", "quantile_low": 1, "quantile_high": 5,
             "n_low": 18, "n_high": 18, "mean_low": -0.003, "mean_high": 0.005,
             "spread": 0.008, "se": 0.002, "t_stat": 4.0},
            # mktadj Q5-Q1: t >> 1.96 at both horizons; +20d spread > +5d
            # so the caption's 'widens with horizon' branch fires by default.
            {"horizon": 5, "return_type": "mktadj", "quantile_low": 1, "quantile_high": 5,
             "n_low": 18, "n_high": 18, "mean_low": -0.004, "mean_high": 0.004,
             "spread": 0.008, "se": 0.001, "t_stat": 8.0},
            {"horizon": 20, "return_type": "mktadj", "quantile_low": 1, "quantile_high": 5,
             "n_low": 18, "n_high": 18, "mean_low": -0.010, "mean_high": 0.012,
             "spread": 0.022, "se": 0.002, "t_stat": 11.0},
        ],
        "spearman_ic": [
            {"horizon": 5, "return_type": "raw", "column": "ret_fwd_5d",
             "n": 90, "ic": 0.06, "p_value": 1e-5},
            {"horizon": 20, "return_type": "raw", "column": "ret_fwd_20d",
             "n": 90, "ic": 0.05, "p_value": 1e-4},
            # mktadj IC: positive and p << 0.05 at both horizons; magnitude
            # at +20d > +5d so the caption's 'grows' branch fires by default.
            {"horizon": 5, "return_type": "mktadj", "column": "ret_fwd_5d_mktadj",
             "n": 90, "ic": 0.07, "p_value": 1e-6},
            {"horizon": 20, "return_type": "mktadj", "column": "ret_fwd_20d_mktadj",
             "n": 90, "ic": 0.10, "p_value": 1e-8},
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


# --- Slice 10b chart + caption + Results-section helpers ---------------------


def test_quintile_mean_chart_and_caption_data_derived_direction_and_flip():
    """Caption's non-decreasing count and its longest-horizon Q1/Q5
    anchor numbers must follow the data. Flipping the mktadj sort at one
    horizon so it's NOT non-decreasing must flip the caption's count.
    Chart Figure smoke-test: axes have >0 lines and a data-derived title."""
    stats = _make_synthetic_stats()
    # Default fixture: mktadj sort is strictly increasing at both horizons,
    # so non-decreasing count == 2 of 2.
    caption = report._quintile_mean_caption(stats)
    assert "non-decreasing from Q1 to Q5 at all 2 horizons" in caption
    # +20d is the longest horizon; Q1=-0.010 -> -1.00%; Q5=+0.012 -> +1.20%.
    assert "At the longest horizon shown (20d)" in caption
    assert "Q1's mean is -1.00%" in caption
    assert "Q5's mean is +1.20%" in caption

    # Flip: break monotonicity at +5d by making Q3 > Q4 in that horizon's
    # mktadj rows. The +20d mktadj sort is left intact so exactly 1 of 2
    # horizons remain non-decreasing.
    flipped = copy.deepcopy(stats)
    for row in flipped["quantile_sort"]:
        if row["horizon"] == 5 and row["return_type"] == "mktadj" and row["quintile"] == 4:
            row["mean"] = -0.001  # < Q3's 0.000, breaks non-decreasing
    caption_flipped = report._quintile_mean_caption(flipped)
    assert "non-decreasing at 1 of 2 horizons" in caption_flipped
    assert "the remaining 1 contain a Q_i > Q_(i+1) reversal" in caption_flipped
    # Flip: break monotonicity at both horizons.
    fully_broken = copy.deepcopy(stats)
    for row in fully_broken["quantile_sort"]:
        if row["return_type"] == "mktadj" and row["quintile"] == 5:
            row["mean"] = -0.020  # Q5 well below Q4 at both horizons
    caption_broken = report._quintile_mean_caption(fully_broken)
    assert "non-decreasing at none of the 2 horizons" in caption_broken

    # Chart smoke test: title carries the horizon x quintile shape claim
    # derived from the data, not a hardcoded interpretive string.
    fig = report._make_quintile_mean_chart(stats)
    try:
        (ax,) = fig.axes
        assert "2 horizons x 5 quintiles" in ax.get_title()
        # One line per horizon.
        assert len(ax.lines) >= 2
    finally:
        import matplotlib.pyplot as _plt
        _plt.close(fig)


def test_q5_q1_spread_chart_and_caption_data_derived_and_direction_flips():
    """Caption's widens/narrows/flat direction and its significance-count
    must follow the data. Flipping the +20d spread below the +5d spread
    must flip the direction claim; setting every t_stat < 1.96 must move
    the count to zero."""
    stats = _make_synthetic_stats()
    # Default fixture: +20d spread 0.022 > +5d spread 0.008, both t > 1.96.
    caption = report._q5_q1_spread_caption(stats)
    assert "widens with horizon" in caption
    assert "all 2 horizons" in caption
    assert "+0.80%" in caption  # +5d = 0.008 -> 0.80%
    assert "+2.20%" in caption  # +20d = 0.022 -> 2.20%

    # Flip: swap the two spreads so +5d > +20d, which must flip
    # widens->narrows.
    narrower = copy.deepcopy(stats)
    for row in narrower["quantile_spread"]:
        if row["return_type"] != "mktadj":
            continue
        if row["horizon"] == 5:
            row["spread"] = 0.022
        elif row["horizon"] == 20:
            row["spread"] = 0.008
    caption_narr = report._q5_q1_spread_caption(narrower)
    assert "narrows with horizon" in caption_narr
    assert "widens with horizon" not in caption_narr

    # Flat: identical spreads at both endpoints -> 'roughly flat'.
    flat = copy.deepcopy(stats)
    for row in flat["quantile_spread"]:
        if row["return_type"] == "mktadj":
            row["spread"] = 0.010
    caption_flat = report._q5_q1_spread_caption(flat)
    assert "roughly flat" in caption_flat

    # Significance count: knock every t_stat below 1.96.
    no_sig = copy.deepcopy(stats)
    for row in no_sig["quantile_spread"]:
        if row["return_type"] == "mktadj":
            row["t_stat"] = 0.5
    caption_ns = report._q5_q1_spread_caption(no_sig)
    assert "none of the 2 horizons" in caption_ns

    # Chart smoke: title carries data-derived significance count.
    fig = report._make_q5_q1_spread_chart(stats)
    try:
        (ax,) = fig.axes
        assert "2 horizons" in ax.get_title()
        assert "2 significant" in ax.get_title()  # all 2 are significant in default fixture
    finally:
        import matplotlib.pyplot as _plt
        _plt.close(fig)


def test_ic_by_horizon_chart_and_caption_data_derived_and_magnitude_flips():
    """Caption's grows/shrinks/flat direction and its p<0.05 count must
    follow the data. Flipping the +20d IC magnitude below +5d's must
    flip the direction claim; setting every p >= 0.05 must move the
    count to zero."""
    stats = _make_synthetic_stats()
    # Default fixture: mkt-adj IC 0.07 at +5d, 0.10 at +20d -> grows.
    caption = report._ic_by_horizon_caption(stats)
    assert "the magnitude grows from |IC|=0.070" in caption
    assert "to 0.100 at 20d" in caption
    assert "all 2 horizons" in caption

    # Flip: swap the two ICs so +5d > +20d -> shrinks.
    shrunk = copy.deepcopy(stats)
    for row in shrunk["spearman_ic"]:
        if row["return_type"] != "mktadj":
            continue
        if row["horizon"] == 5:
            row["ic"] = 0.10
        elif row["horizon"] == 20:
            row["ic"] = 0.07
    caption_shr = report._ic_by_horizon_caption(shrunk)
    assert "the magnitude shrinks from |IC|=0.100" in caption_shr
    assert "grows" not in caption_shr

    # Flat magnitudes -> 'flat at both endpoints'.
    flat = copy.deepcopy(stats)
    for row in flat["spearman_ic"]:
        if row["return_type"] == "mktadj":
            row["ic"] = 0.08
    caption_flat = report._ic_by_horizon_caption(flat)
    assert "flat at |IC|=0.080" in caption_flat

    # Significance-count flip: knock every mkt-adj p above 0.05.
    no_sig = copy.deepcopy(stats)
    for row in no_sig["spearman_ic"]:
        if row["return_type"] == "mktadj":
            row["p_value"] = 0.5
    caption_ns = report._ic_by_horizon_caption(no_sig)
    assert "none of the 2 horizons" in caption_ns

    # Chart smoke: title carries data-derived p<0.05 count.
    fig = report._make_ic_by_horizon_chart(stats)
    try:
        (ax,) = fig.axes
        assert "2 horizons" in ax.get_title()
        assert "2 with p < 0.05" in ax.get_title()
    finally:
        import matplotlib.pyplot as _plt
        _plt.close(fig)


def test_results_section_all_positive_branch_by_default():
    """Default fixture puts the conclusion into the all-positive branch:
    every horizon is non-decreasing, every Q5-Q1 spread is positive-and-
    significant, every IC is positive-and-significant."""
    stats = _make_synthetic_stats()
    heading, body = report._build_results_section(stats)
    assert "Results (does PEAD show up?)" == heading
    # Per-horizon block for both horizons is present with non-decreasing +
    # significant markers.
    assert "At +5d:" in body
    assert "At +20d:" in body
    assert "quintile sort is non-decreasing" in body
    assert "significant at 5%" in body
    # Conclusion sentence: all-positive branch.
    assert "supports a positive PEAD monotonicity signal at every horizon" in body


def test_results_section_degenerate_branch_when_nothing_significant():
    """When no horizon has a significant spread or IC in either
    direction, the conclusion must switch to the degenerate branch."""
    stats = _make_synthetic_stats()
    for row in stats["quantile_spread"]:
        if row["return_type"] == "mktadj":
            row["t_stat"] = 0.2
    for row in stats["spearman_ic"]:
        if row["return_type"] == "mktadj":
            row["p_value"] = 0.7
    _, body = report._build_results_section(stats)
    assert "does not support a PEAD signal in this population" in body
    assert "positive PEAD monotonicity signal at every horizon" not in body


def test_results_section_reversal_branch_when_all_signs_negative():
    """When no horizon is non-decreasing AND every significant spread
    and every significant IC is negatively signed, the conclusion must
    switch to the reversal branch."""
    stats = _make_synthetic_stats()
    # Reverse each mktadj quintile sort so Q1..Q5 is strictly decreasing;
    # non-decreasing check fails at both horizons.
    for row in stats["quantile_sort"]:
        if row["return_type"] != "mktadj":
            continue
        row["mean"] = -row["mean"]
    for row in stats["quantile_spread"]:
        if row["return_type"] == "mktadj":
            row["spread"] = -row["spread"]
            row["t_stat"] = -row["t_stat"]  # still |t| > 1.96, but negative
    for row in stats["spearman_ic"]:
        if row["return_type"] == "mktadj":
            row["ic"] = -row["ic"]
    _, body = report._build_results_section(stats)
    assert "supports a REVERSAL signal" in body
    assert "not the standard positive-PEAD hypothesis" in body


def test_results_section_mixed_branch_when_only_one_horizon_passes_all():
    """When at least one but not all horizons clear all three positive-
    PEAD checks, the conclusion must switch to the mixed branch."""
    stats = _make_synthetic_stats()
    # Break just the +5d mktadj non-decreasing check; leave +20d intact and
    # leave the Q5-Q1 spread + IC significance at both horizons -- so +5d
    # fails the non-decreasing leg while +20d still passes all three. That
    # puts 1-of-2 into the all-three-pass column.
    for row in stats["quantile_sort"]:
        if (row["horizon"] == 5 and row["return_type"] == "mktadj"
                and row["quintile"] == 5):
            row["mean"] = -0.05  # Q5 well below Q4, breaks non-decreasing
    _, body = report._build_results_section(stats)
    assert "picture is mixed: 1 of 2 horizons" in body
    assert "partial, horizon-dependent positive-PEAD signal" in body
    assert "supports a positive PEAD monotonicity signal at every horizon" not in body


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


def test_build_final_event_study_report_passes_three_charts_and_ordered_sections(tmp_path):
    """Sub-slice 10b invariant: three charts (quintile means, Q5-Q1
    spread, Spearman IC) and three sections in strict order (Data &
    methodology -> Results -> Limitations). Guarding chart-count and
    section-order here so a stray addition/removal is caught by tests,
    not review."""
    stats_path, events_path = _write_synthetic_fixtures(tmp_path)
    out = tmp_path / "final_report.pdf"

    with mock.patch.object(report.report_builder, "build_pdf_report") as mock_build:
        mock_build.return_value = out
        report.build_final_event_study_report(out, stats_path, events_path)

    assert mock_build.called
    kwargs = mock_build.call_args.kwargs
    charts = list(kwargs["charts"])
    assert len(charts) == 3, f"expected 3 charts, got {len(charts)}"
    # Each chart is a (figure, caption) pair; captions are non-empty.
    for i, (fig, caption) in enumerate(charts):
        assert hasattr(fig, "savefig"), f"chart {i} isn't a matplotlib-like Figure"
        assert isinstance(caption, str) and caption.strip(), f"chart {i} caption is empty"

    sections = list(kwargs["sections"])
    assert len(sections) == 3, f"expected 3 sections, got {len(sections)}"
    headings = [heading for heading, _ in sections]
    # Strict order: methodology -> results -> limitations.
    assert "methodology" in headings[0].lower()
    assert "results" in headings[1].lower()
    assert "limitations" in headings[2].lower()
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
