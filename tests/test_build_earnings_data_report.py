"""Tests for scripts/build_earnings_data_report.py -- the second
phase-completion PDF report, covering Slices 5-6's I/B/E/S earnings-cleaning
and surprise-construction work.

Mirrors tests/test_demo_sample_report.py's pattern: an end-to-end test that
the real report-building function produces a valid, non-trivial, multi-page
PDF from the real cleaned dataset, plus a couple of targeted checks that the
report's numbers are actually derived from the dataframe at runtime rather
than hardcoded (per this project's "every number computed at runtime"
convention), by building the report against a small synthetic dataframe
whose expected figures can be computed by hand.
"""

import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import pytest
import weasyprint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_earnings_data_report as report  # noqa: E402

PDF_MAGIC = b"%PDF"

REAL_DATA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "earnings" / "ibes_clean_1995_2014.parquet"
)


def _make_synthetic_df() -> pd.DataFrame:
    """A small, hand-computable stand-in for the real cleaned earnings
    table -- covers the columns the report script actually reads, built so
    that every value a test cares about is unambiguous by construction:

    - All six `eps_basis_status` categories get distinct counts (13/9/7/6/
      3/2, summing to 40) -- so a mislabeled or reordered bar in the
      data-quality chart is actually detectable by a test (tied counts
      would let two bars swap without any test noticing).
    - The two years' `sue_source` splits differ from each other (2000 is
      60% analyst/25% seasonal/15% none; 2001 is 40%/40%/20%) -- so a
      year/source mixup in the coverage-by-year chart is detectable too.
    - The analyst and seasonal `sue_primary` subsets sit on visibly
      different scales/signs (analyst ~1 to 25, seasonal ~-1 to -30), each
      with exactly one value outside the +/-20 display-clip range -- so
      the per-source median/tail/excluded-pct stats (and the pooled-vs-
      per-source distinction they test) are meaningfully different, not
      coincidentally similar.
    """
    # Six distinct counts, summing to 40 -- assigned to rows independently
    # of source/value below, since eps_basis_status is a separate axis.
    basis_statuses_expanded = (
        ["anndats_basis_assumed"] * 13
        + ["anndats_basis_confirmed"] * 9
        + ["basis_unverified_no_crsp"] * 7
        + ["basis_undetermined_ambiguous_permno"] * 6
        + ["basis_undetermined_ambiguous"] * 3
        + ["basis_undetermined_no_reference"] * 2
    )
    assert len(basis_statuses_expanded) == 40

    year_specs = {
        2000: {
            "analyst": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 25.0],
            "seasonal": [-1.0, -2.0, -3.0, -4.0, -5.0],
            "none": [None, None, None],
        },
        2001: {
            "analyst": [12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0],
            "seasonal": [-6.0, -7.0, -8.0, -9.0, -10.0, -11.0, -12.0, -30.0],
            "none": [None, None, None, None],
        },
    }

    rows = []
    i = 0
    for year, by_source in year_specs.items():
        for source in ("analyst", "seasonal", "none"):
            for value in by_source[source]:
                rows.append(
                    {
                        "anndats": pd.Timestamp(year=year, month=(i % 12) + 1, day=1),
                        "sue_analyst": value if source == "analyst" else None,
                        "sue_seasonal": value if source == "seasonal" else None,
                        "sue_primary": value,
                        "sue_source": source,
                        "identifier_ambiguous": i % 5 == 0,
                        "eps_basis_status": basis_statuses_expanded[i],
                        "n_lookahead_excluded": i % 3,
                        "consensus_staleness_days": float(3 + (i % 20)),
                    }
                )
                i += 1
    assert i == 40
    return pd.DataFrame(rows)


def test_build_report_produces_valid_multi_chart_pdf_from_synthetic_data(tmp_path):
    df = _make_synthetic_df()
    input_path = tmp_path / "synthetic_earnings.parquet"
    df.to_parquet(input_path)

    out = report.build_earnings_data_report(tmp_path / "earnings_report.pdf", input_path)

    assert out.exists()
    data = out.read_bytes()
    assert data[:4] == PDF_MAGIC
    assert data.rstrip().endswith(b"%%EOF")
    # Three real charts plus several narrative sections is substantially
    # more content than an empty report -- a cheap sanity check that the
    # charts actually made it into the PDF, not just the text.
    assert out.stat().st_size > 50_000

    pages = weasyprint.HTML(filename=str(out)).render().pages
    assert len(pages) >= 2


def test_coverage_by_year_chart_reflects_real_per_year_shares():
    """Regression guard against hardcoding: build the coverage-by-year
    figure from the synthetic dataframe and confirm every (year, source)
    bar's actual height matches that same (year, source) share computed
    directly from the fixture's own value_counts -- a mapping check, not a
    positional list of hardcoded numbers, so a year/source mislabel or
    reorder in the chart actually fails this test."""
    df = _make_synthetic_df()
    fig = report._make_coverage_by_year_chart(df)
    try:
        ax = fig.axes[0]
        years = sorted(df["anndats"].dt.year.unique())
        sources = ["analyst", "seasonal", "none"]
        n_years = len(years)
        assert len(ax.patches) == len(sources) * n_years

        expected = {
            (year, source): 100.0 * (df.loc[df["anndats"].dt.year == year, "sue_source"] == source).mean()
            for source in sources
            for year in years
        }
        # Draw order: _make_coverage_by_year_chart loops sources in a fixed
        # order (analyst, seasonal, none) and calls ax.bar(years, ...) once
        # per source, so ax.patches is source-major, year-minor.
        actual = {
            (year, source): ax.patches[src_idx * n_years + year_idx].get_height()
            for src_idx, source in enumerate(sources)
            for year_idx, year in enumerate(years)
        }
        for key, expected_height in expected.items():
            assert actual[key] == pytest.approx(expected_height), key

        # Sanity: the two years' splits must actually differ by
        # construction, or a year mixup wouldn't be detectable at all.
        assert expected[(years[0], "analyst")] != pytest.approx(expected[(years[1], "analyst")])
    finally:
        plt.close(fig)


def test_surprise_distribution_chart_reports_correct_excluded_fraction():
    """Each source's excluded fraction (share of that source's full
    sue_primary subset falling outside the [-20, 20] display range) must be
    computed from that source's own data, not a pooled or hardcoded value.
    The synthetic fixture has exactly one out-of-range value in each of the
    analyst and seasonal subsets."""
    df = _make_synthetic_df()

    fig, stats = report._make_surprise_distribution_chart(df)
    try:
        assert stats["n_total"] == df["sue_primary"].notna().sum()
        for source in ("analyst", "seasonal"):
            full_subset = df.loc[df["sue_source"] == source, "sue_primary"].dropna()
            expected_excluded_pct = 100.0 * (full_subset.abs() > 20).sum() / len(full_subset)
            assert stats[f"excluded_pct_{source}"] == pytest.approx(expected_excluded_pct)
            # Sanity: the synthetic outlier in each source is actually excluded.
            assert stats[f"excluded_pct_{source}"] > 0
        # The two sources' excluded fractions must not coincidentally match
        # (1/20 = 5% for analyst vs. 1/13 ~= 7.7% for seasonal), guarding
        # against a future re-pooling of this stat.
        assert stats["excluded_pct_analyst"] != pytest.approx(stats["excluded_pct_seasonal"])
    finally:
        plt.close(fig)


def test_surprise_distribution_chart_splits_sample_sizes_by_source():
    """The two histogram panels (analyst-benchmarked, seasonal-benchmarked)
    must each report the real per-source sample size from the data, not a
    pooled or hardcoded count."""
    df = _make_synthetic_df()
    expected_n_analyst = int(
        ((df["sue_source"] == "analyst") & df["sue_primary"].notna()).sum()
    )
    expected_n_seasonal = int(
        ((df["sue_source"] == "seasonal") & df["sue_primary"].notna()).sum()
    )

    fig, stats = report._make_surprise_distribution_chart(df)
    try:
        assert stats["n_analyst"] == expected_n_analyst
        assert stats["n_seasonal"] == expected_n_seasonal
        # Sanity: both panels are non-trivial in the synthetic fixture, so
        # this test would actually fail if the split logic were wrong.
        assert expected_n_analyst > 0
        assert expected_n_seasonal > 0
    finally:
        plt.close(fig)


def test_surprise_distribution_chart_reports_correct_per_source_stats():
    """Fix 5's per-source median/tail stats must each match a direct
    computation from that source's own full subset in the fixture, and the
    analyst and seasonal values must NOT be equal to each other -- guarding
    against a future re-pooling of these scale-sensitive statistics (the
    exact bug class round 2 found: a chart split by source, but prose/stats
    computed pooled)."""
    df = _make_synthetic_df()

    fig, stats = report._make_surprise_distribution_chart(df)
    try:
        for source in ("analyst", "seasonal"):
            full_subset = df.loc[df["sue_source"] == source, "sue_primary"].dropna()
            assert stats[f"median_{source}"] == pytest.approx(float(full_subset.median()))
            assert stats[f"low_tail_{source}"] == pytest.approx(float(full_subset.quantile(0.001)))
            assert stats[f"high_tail_{source}"] == pytest.approx(float(full_subset.quantile(0.999)))

        assert stats["median_analyst"] != pytest.approx(stats["median_seasonal"])
        assert stats["low_tail_analyst"] != pytest.approx(stats["low_tail_seasonal"])
        assert stats["high_tail_analyst"] != pytest.approx(stats["high_tail_seasonal"])
        # No pooled variant of any of these should be returned at all.
        assert "median_sue" not in stats
        assert "low_tail" not in stats
        assert "high_tail" not in stats
        assert "excluded_pct" not in stats
    finally:
        plt.close(fig)


def test_data_quality_chart_covers_all_basis_statuses():
    """Regression guard against hardcoding: build the two-panel chart from
    the synthetic fixture (whose six eps_basis_status categories all have
    distinct counts by construction) and confirm every bar's label maps to
    the correct height, via a label -> height mapping built from
    `_basis_status_breakdown` itself rather than a positional list of
    hardcoded numbers -- so a mislabeled/reordered bar actually fails."""
    df = _make_synthetic_df()
    breakdown = report._basis_status_breakdown(df)

    # Sanity: this is what makes a swapped bar detectable at all.
    counts = breakdown["counts"]
    assert len(set(counts.values.tolist())) == len(counts)

    fig = report._make_data_quality_chart(df)
    try:
        assert len(fig.axes) == 2
        left_ax, right_ax = fig.axes

        left_expected = {
            "No basis\nquestion": breakdown["pct_no_question"],
            "Some kind of\nbasis question": breakdown["pct_some_question"],
        }
        left_actual = {
            tick.get_text(): patch.get_height()
            for tick, patch in zip(left_ax.get_xticklabels(), left_ax.patches)
        }
        assert left_actual.keys() == left_expected.keys()
        for label, expected_pct in left_expected.items():
            assert left_actual[label] == pytest.approx(expected_pct)

        non_assumed = [s for s in report._BASIS_STATUS_ORDER if s != "anndats_basis_assumed"]
        right_expected = {
            report._BASIS_STATUS_LABELS[s]: float(breakdown["pct"][s]) for s in non_assumed
        }
        right_actual = {
            tick.get_text(): patch.get_height()
            for tick, patch in zip(right_ax.get_xticklabels(), right_ax.patches)
        }
        assert right_actual.keys() == right_expected.keys()
        for label, expected_pct in right_expected.items():
            assert right_actual[label] == pytest.approx(expected_pct)
    finally:
        plt.close(fig)


def test_basis_status_breakdown_partitions_are_internally_consistent():
    """Regression guard against finding 1's bug class recurring: the
    in-window (real split-timing) population and the never-checked-at-all
    population must be mutually exclusive, correctly composed of their
    constituent statuses, and -- together with the assumed count -- must
    account for every row."""
    df = _make_synthetic_df()
    breakdown = report._basis_status_breakdown(df)

    assert breakdown["n_in_window"] == breakdown["n_confirmed"] + breakdown["n_in_window_undetermined"]

    counts = breakdown["counts"]
    # n_in_window must equal EXACTLY the sum of the three real in-window
    # statuses -- if it accidentally also included basis_unverified_no_crsp
    # or basis_undetermined_ambiguous_permno (the round-1 bug class), this
    # equality would fail since those two statuses have nonzero, distinct
    # counts (7 and 6) in the fixture.
    assert breakdown["n_in_window"] == (
        int(counts["anndats_basis_confirmed"])
        + int(counts["basis_undetermined_ambiguous"])
        + int(counts["basis_undetermined_no_reference"])
    )

    assert breakdown["n_never_checked"] == (
        int(counts["basis_unverified_no_crsp"]) + int(counts["basis_undetermined_ambiguous_permno"])
    )

    assert breakdown["n_in_window"] + breakdown["n_never_checked"] + breakdown["n_assumed"] == len(df)


@pytest.mark.skipif(
    not REAL_DATA_PATH.exists(),
    reason="real data/earnings/ibes_clean_1995_2014.parquet not present in this checkout",
)
def test_build_report_runs_end_to_end_against_real_data(tmp_path):
    """The acceptance-criterion check: running the script against the real,
    already-cleaned earnings table produces a valid, non-trivial PDF."""
    out = report.build_earnings_data_report(tmp_path / "real_earnings_report.pdf")

    assert out.exists()
    data = out.read_bytes()
    assert data[:4] == PDF_MAGIC
    assert data.rstrip().endswith(b"%%EOF")
    assert out.stat().st_size > 50_000

    pages = weasyprint.HTML(filename=str(out)).render().pages
    assert len(pages) >= 3


def test_chart_numbering_and_cross_references_are_internally_consistent(tmp_path, monkeypatch):
    """Every chart's caption must start with the correct sequential
    "Chart N -- " prefix matching its position in the charts list, and
    every "Chart <number>" reference anywhere in the section bodies must
    point to a chart that actually exists. Intercepts the call to
    report_builder.build_pdf_report so this can be checked without
    actually rendering a PDF."""
    df = _make_synthetic_df()
    input_path = tmp_path / "synthetic_earnings.parquet"
    df.to_parquet(input_path)

    captured = {}

    def _fake_build_pdf_report(*, title, output_path, summary=None, sections=(), charts=(), dpi=None):
        captured["sections"] = sections
        captured["charts"] = charts
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4\n%%EOF")
        return out_path

    monkeypatch.setattr(report.report_builder, "build_pdf_report", _fake_build_pdf_report)

    report.build_earnings_data_report(tmp_path / "report.pdf", input_path)

    charts = captured["charts"]
    sections = captured["sections"]
    assert len(charts) >= 1

    for i, (_, caption) in enumerate(charts, start=1):
        assert caption.startswith(f"Chart {i} -- "), caption[:40]

    chart_refs = set()
    for _, body in sections:
        chart_refs.update(int(n) for n in re.findall(r"Chart (\d+)", body))
    assert chart_refs, "expected at least one 'Chart N' cross-reference in the section prose"
    assert chart_refs <= set(range(1, len(charts) + 1)), chart_refs


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
