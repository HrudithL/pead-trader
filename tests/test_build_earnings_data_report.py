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

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd
import pytest
import weasyprint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_earnings_data_report as report  # noqa: E402

PDF_MAGIC = b"%PDF"

REAL_DATA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "earnings" / "ibes_clean_1995_2014.parquet"
)


def _make_synthetic_df(n_per_year: int = 20) -> pd.DataFrame:
    """A small, hand-computable stand-in for the real cleaned earnings
    table -- covers the columns the report script actually reads, with
    values chosen so per-year/per-status shares are easy to check by hand."""
    rows = []
    rng_values = [-15.0, -3.0, -1.0, 0.0, 0.5, 1.0, 2.5, 5.0, 8.0, 30.0]
    basis_statuses = [
        "anndats_basis_assumed",
        "anndats_basis_confirmed",
        "basis_unverified_no_crsp",
        "basis_undetermined_ambiguous_permno",
        "basis_undetermined_ambiguous",
        "basis_undetermined_no_reference",
    ]
    for year in (2000, 2001):
        for i in range(n_per_year):
            # Half the rows each year get a real analyst estimate, a
            # quarter get only the seasonal fallback, the rest get neither
            # -- an easy-to-verify split.
            if i % 4 < 2:
                source, sue_analyst, sue_seasonal = "analyst", rng_values[i % len(rng_values)], None
            elif i % 4 == 2:
                source, sue_analyst, sue_seasonal = "seasonal", None, rng_values[i % len(rng_values)]
            else:
                source, sue_analyst, sue_seasonal = "none", None, None
            sue_primary = sue_analyst if sue_analyst is not None else sue_seasonal
            rows.append(
                {
                    "anndats": pd.Timestamp(year=year, month=(i % 12) + 1, day=1),
                    "sue_analyst": sue_analyst,
                    "sue_seasonal": sue_seasonal,
                    "sue_primary": sue_primary,
                    "sue_source": source,
                    "identifier_ambiguous": i % 5 == 0,
                    "eps_basis_status": basis_statuses[i % len(basis_statuses)],
                    "n_lookahead_excluded": i % 3,
                    "consensus_staleness_days": float(3 + (i % 20)),
                }
            )
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
    figure from the synthetic dataframe (whose per-year analyst/seasonal/
    none split is known by construction: 50%/25%/25%) and confirm the
    actual bar heights, in draw order, match that shape for both years."""
    df = _make_synthetic_df()
    fig = report._make_coverage_by_year_chart(df)
    try:
        assert fig is not None
        ax = fig.axes[0]
        # Two years in the synthetic data -> six bar segments (3 sources x
        # 2 years) stacked into 2 x-axis positions. Draw order is all
        # "analyst" bars, then all "seasonal" bars, then all "none" bars
        # (one bar per year within each source), matching the loop order in
        # _make_coverage_by_year_chart.
        assert len(ax.patches) == 6
        heights = [patch.get_height() for patch in ax.patches]
        assert heights == pytest.approx([50.0, 50.0, 25.0, 25.0, 25.0, 25.0])
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_surprise_distribution_chart_reports_correct_excluded_fraction():
    """One synthetic value (30.0) sits outside the [-20, 20] display range
    used by _make_surprise_distribution_chart; confirm the excluded
    percentage returned is computed from the real data, not a placeholder."""
    df = _make_synthetic_df()
    non_null = df["sue_primary"].dropna()
    expected_excluded_pct = 100.0 * (non_null.abs() > 20).sum() / len(non_null)

    fig, stats = report._make_surprise_distribution_chart(df)
    try:
        assert stats["n_total"] == len(non_null)
        assert stats["excluded_pct"] == pytest.approx(expected_excluded_pct)
        assert stats["excluded_pct"] > 0  # sanity: the synthetic outlier is actually excluded
    finally:
        import matplotlib.pyplot as plt

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
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_data_quality_chart_covers_all_basis_statuses():
    """Regression guard against hardcoding: with the synthetic fixture's
    i % 6 status cycle over 40 rows (8/8/6/6/6/6 per status), confirm the
    two-panel chart's actual bar heights match the hand-computed shares."""
    df = _make_synthetic_df()
    fig = report._make_data_quality_chart(df)
    try:
        assert len(fig.axes) == 2
        left_ax, right_ax = fig.axes
        # Left panel: 2 bars -- "no basis question" (assumed, 8/40) vs.
        # "some basis question" (the other 32/40), as % of all 40 rows.
        assert len(left_ax.patches) == 2
        left_heights = [patch.get_height() for patch in left_ax.patches]
        assert left_heights == pytest.approx([20.0, 80.0])
        # Right panel: the 5 non-assumed statuses, each as % of all 40 rows
        # (confirmed=8, the other four=6 each).
        assert len(right_ax.patches) == 5
        right_heights = [patch.get_height() for patch in right_ax.patches]
        assert right_heights == pytest.approx([20.0, 15.0, 15.0, 15.0, 15.0])
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
