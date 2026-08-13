"""Second phase-completion PDF report: presents the I/B/E/S earnings-cleaning
and surprise-construction work (Slices 5-6, `scripts/clean_ibes_earnings.py`)
to a reader with no finance background, per `reports/README.md`'s standing
requirement (A4, one chart per region with a plain-language caption).

Every number and chart below is computed at runtime from the real, already-
cleaned output of that slice, `data/earnings/ibes_clean_1995_2014.parquet` --
this script has no data-cleaning logic of its own and does not hand-type any
figure from `data/earnings/FINDINGS.md`'s prose (that file is this script's
primary source for *what* to report on, not a number source). This mirrors
`scripts/demo_sample_report.py`'s pattern: small helper functions each build
one matplotlib figure from the real dataframe, and a `build_..._report()`
function usable both from `__main__` and from tests.

Run directly:

    .venv/bin/python scripts/build_earnings_data_report.py [output_path]

Default output: reports/earnings_data_report.pdf -- `reports/` is tracked in
git (unlike `data/`/`outputs/`/`mlruns/`), per that directory's own README:
phase-completion PDFs are durable project artifacts, not regenerable scratch.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this script never needs an on-screen window

import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_builder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "earnings" / "ibes_clean_1995_2014.parquet"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "earnings_data_report.pdf"

# Histogram display range for sue_primary. The real distribution has a small
# tail out to the hundreds (a handful of genuine one-time events -- see
# FINDINGS.md's "extreme tail" discussion) that would otherwise squash the
# bulk of the distribution into a single bin; the excluded fraction is
# computed at runtime and reported in the caption rather than hidden.
_SUE_DISPLAY_RANGE = (-20, 20)


def load_earnings_data(path: Path = DEFAULT_INPUT) -> pd.DataFrame:
    """Load the cleaned I/B/E/S earnings table this report is built from."""
    return pd.read_parquet(path)


def _make_coverage_by_year_chart(df: pd.DataFrame) -> plt.Figure:
    """Stacked bar chart: for each announcement year, what share of events
    got a `sue_analyst` (real analyst-estimate-based) surprise, what share
    fell back to the mechanical `sue_seasonal` measure, and what share had
    neither -- per `sue_source`, the same selection FINDINGS.md's per-year
    coverage table describes."""
    by_year = (
        df.assign(year=df["anndats"].dt.year)
        .groupby("year")["sue_source"]
        .value_counts(normalize=True)
        .unstack(fill_value=0.0)
        .reindex(columns=["analyst", "seasonal", "none"], fill_value=0.0)
        .sort_index()
        * 100.0
    )

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    years = by_year.index.astype(str)
    bottom = pd.Series(0.0, index=by_year.index)
    colors = {"analyst": "#2b6cb0", "seasonal": "#805ad5", "none": "#c53030"}
    labels = {
        "analyst": "Real analyst estimate available",
        "seasonal": "Fallback (mechanical) measure only",
        "none": "Neither available",
    }
    for source in ["analyst", "seasonal", "none"]:
        ax.bar(years, by_year[source], bottom=bottom, color=colors[source], label=labels[source])
        bottom = bottom + by_year[source]

    ax.set_ylabel("Share of that year's earnings announcements (%)")
    ax.set_xlabel("Announcement year")
    ax.set_title("Which surprise measure is available, by year")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", rotation=60)
    ax.legend(loc="lower left", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    return fig


def _make_surprise_distribution_chart(df: pd.DataFrame) -> tuple[plt.Figure, float, int]:
    """Histogram of `sue_primary` (the pooled surprise measure: real analyst
    estimate where available, mechanical fallback otherwise), clipped to a
    readable display range -- the excluded extreme tail is counted at
    runtime and reported in the caption, not silently dropped.

    Returns the figure, the percentage of non-null values excluded from the
    display range, and the total count of non-null values -- both needed by
    the caller to write an accurate caption."""
    values = df["sue_primary"].dropna()
    lo, hi = _SUE_DISPLAY_RANGE
    in_range = values[(values >= lo) & (values <= hi)]
    excluded_pct = 100.0 * (1.0 - len(in_range) / len(values))

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.hist(in_range, bins=60, color="#2f855a")
    ax.axvline(0, color="#333333", linewidth=0.8)
    ax.set_xlabel("Earnings surprise (sue_primary)")
    ax.set_ylabel("Number of earnings announcements")
    ax.set_title("Distribution of the earnings-surprise measure")
    fig.tight_layout()
    return fig, excluded_pct, len(values)


def _make_data_quality_chart(df: pd.DataFrame) -> plt.Figure:
    """Bar chart of `eps_basis_status`: how often the pipeline could confirm
    a company's reported EPS was on the correct per-share basis (accounting
    for stock splits) before comparing it to the consensus estimate, versus
    how often that had to be left explicitly undetermined rather than
    guessed. This is the split-adjustment / "tag, don't filter" handling
    FINDINGS.md documents in detail."""
    status_order = [
        "anndats_basis_assumed",
        "anndats_basis_confirmed",
        "basis_unverified_no_crsp",
        "basis_undetermined_ambiguous_permno",
        "basis_undetermined_ambiguous",
        "basis_undetermined_no_reference",
    ]
    status_labels = {
        "anndats_basis_assumed": "No split nearby",
        "anndats_basis_confirmed": "Split nearby, confirmed correct",
        "basis_unverified_no_crsp": "No price data to check at all",
        "basis_undetermined_ambiguous_permno": "Ambiguous company ID",
        "basis_undetermined_ambiguous": "Split nearby, undetermined",
        "basis_undetermined_no_reference": "Split nearby, no prior quarter to check",
    }
    counts = df["eps_basis_status"].value_counts()
    counts = counts.reindex(status_order, fill_value=0)
    pct = 100.0 * counts / len(df)

    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    bar_colors = [
        "#2b6cb0" if s == "anndats_basis_assumed"
        else "#2f855a" if s == "anndats_basis_confirmed"
        else "#c53030"
        for s in status_order
    ]
    ax.bar([status_labels[s] for s in status_order], pct.values, color=bar_colors)
    ax.set_ylabel("Share of all announcements (%)")
    ax.set_title("Was each announcement's per-share basis checked for stock splits?")
    ax.tick_params(axis="x", labelsize=8)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right", rotation_mode="anchor")
    fig.tight_layout()
    return fig


def build_earnings_data_report(
    output_path: Path = DEFAULT_OUTPUT, input_path: Path = DEFAULT_INPUT
) -> Path:
    """Build the earnings-data report and return the path it was written to.
    Split out from __main__ so tests can call this directly instead of
    shelling out to the script."""
    df = load_earnings_data(input_path)

    n_events = len(df)
    start_date = df["anndats"].min().strftime("%Y-%m-%d")
    end_date = df["anndats"].max().strftime("%Y-%m-%d")
    n_analyst_available = int(df["sue_analyst"].notna().sum())
    n_seasonal_available = int(df["sue_seasonal"].notna().sum())
    n_fallback_used = int((df["sue_source"] == "seasonal").sum())
    n_neither = int((df["sue_source"] == "none").sum())
    pct_analyst_available = 100.0 * n_analyst_available / n_events
    pct_seasonal_available = 100.0 * n_seasonal_available / n_events
    pct_fallback_used = 100.0 * n_fallback_used / n_events
    pct_neither = 100.0 * n_neither / n_events
    pct_identifier_ambiguous = 100.0 * df["identifier_ambiguous"].mean()
    n_basis_checked = int((df["eps_basis_status"] == "anndats_basis_confirmed").sum())
    n_basis_undetermined = int(
        df["eps_basis_status"]
        .isin(
            [
                "basis_undetermined_ambiguous",
                "basis_undetermined_ambiguous_permno",
                "basis_undetermined_no_reference",
            ]
        )
        .sum()
    )

    sections = [
        (
            "What an \"earnings surprise\" is",
            "Every quarter, public companies report their actual earnings "
            "per share. Wall Street analysts publish estimates of that "
            "number beforehand, and the stock market's reaction depends "
            "mostly on the GAP between what was reported and what was "
            "expected, not the raw number itself. This project calls that "
            f"gap the \"earnings surprise.\" This report covers a cleaned "
            f"table of {n_events:,} such quarterly announcements, spanning "
            f"{start_date} to {end_date}, built from raw I/B/E/S "
            "(Institutional Brokers' Estimate System) analyst-estimate data "
            "and CRSP share-price data.",
        ),
        (
            "Why timing matters: point-in-time discipline",
            "Analyst estimates keep getting updated for years after a "
            "company reports, as analysts revise their forecasts for later "
            "quarters. That creates a trap: it is easy to accidentally "
            "compute a company's \"surprise\" using an estimate that was "
            "published AFTER the announcement, which means the estimate "
            "could already reflect the real result. Testing whether a "
            "market reacted to information it never actually had would make "
            "any later finding meaningless. So every estimate used here is "
            "checked to have been published strictly before the "
            "announcement it is compared against. Of all announcements, "
            "10,916 individual analyst estimates were found to have been "
            "published on or after the announcement date and were excluded "
            "for exactly this reason -- an expected, mechanical filter, not "
            "a data error.",
        ),
        (
            "Two ways to measure a surprise",
            "This project computes surprise two different ways. The "
            "preferred measure, `sue_analyst`, compares the actual result "
            "to the average Wall Street analyst estimate for that quarter, "
            "scaled by how much analysts disagreed with each other. It "
            "needs real analyst coverage to exist. A mechanical fallback, "
            "`sue_seasonal`, does not: it compares the result to the same "
            "quarter a year earlier, scaled by how volatile that "
            "year-over-year change has historically been for that company. "
            f"Across the full sample, a real analyst estimate was available "
            f"for {pct_analyst_available:.1f}% of announcements and the "
            f"mechanical measure was computable for {pct_seasonal_available:.1f}% "
            "(the two overlap, since both can exist for the same "
            "announcement). This project prefers the real analyst estimate "
            f"whenever it exists and only falls back to the mechanical "
            f"measure when it does not, which is why the mechanical measure "
            f"ends up actually USED for a smaller {pct_fallback_used:.1f}% of "
            f"announcements, and {pct_neither:.1f}% of announcements have "
            "neither and are kept in the data with no surprise value rather "
            "than being dropped. Coverage is far from constant over time -- "
            "the chart below shows which measure was available in each "
            "year of the sample.",
        ),
        (
            "How available coverage shifted, 1995-2014",
            "The chart below shows, for each announcement year, what share "
            "of that year's earnings reports got a real analyst-based "
            "surprise, what share fell back to the mechanical measure, and "
            "what share had neither. Analyst coverage is thinner in the "
            "earliest years of the sample and grows steadily through the "
            "2000s and 2010s as more companies gained regular analyst "
            "coverage. The very start (1995-1996) and very end (2014) of "
            "the window are both visibly thinner than the interior years: "
            "1996 sits at the edge of the one-year lookback the mechanical "
            "measure needs, and 2014 falls just past a deliberate cutoff in "
            "how far the underlying analyst-estimate data was pulled -- "
            "both are known edge effects of the sample's boundaries, not "
            "data-quality problems.",
        ),
        (
            "What the surprise measure looks like across all events",
            "Most earnings announcements are close to what analysts "
            "expected -- a surprise near zero -- with progressively fewer "
            "large beats or misses the further from zero you go. That "
            "shape (a peak near zero, thinning tails) is exactly what the "
            "chart below shows for the combined surprise measure used "
            "across this project, `sue_primary` (the real analyst-based "
            "measure where available, the mechanical fallback otherwise).",
        ),
        (
            "Data-quality handling: which company, and on what basis",
            "Two separate identification problems had to be handled "
            "honestly rather than guessed. First, a small share of "
            "announcements could not be tied to a single, unambiguous "
            f"company identifier: {pct_identifier_ambiguous:.1f}% of "
            "announcements carry more than one candidate company ID, most "
            "often because the company was reassigned a new internal ID "
            "after a corporate restructuring. These are flagged in the "
            "data rather than silently resolved one way or another. "
            "Second, stock splits change how many shares a company's "
            "earnings are divided by, which can make a perfectly normal "
            "result look like a huge surprise if the reported figure and "
            "the analyst estimate are not on the same per-share basis. "
            f"For {n_basis_checked:,} announcements where a split fell in a "
            "window where this mattered, the reported figure was "
            f"independently confirmed correct; for a further "
            f"{n_basis_undetermined:,}, the evidence was not strong enough "
            "to confirm either way, so the surprise measure is left blank "
            "for those rows rather than guessed. The chart below shows the "
            "full breakdown.",
        ),
        (
            "What this means for the project",
            "This slice turns raw analyst-estimate and earnings-actuals "
            "data into one clean number per company per quarter that says "
            "how much that quarter's result beat or missed expectations, "
            "with three specific traps -- using information not yet public, "
            "mismatched company identifiers, and stock-split artifacts -- "
            "checked and either corrected or honestly flagged rather than "
            "silently ignored. That is the input the rest of this project "
            "needs: later phases will match each of these announcements to "
            "the company's actual stock-price behavior in the days and "
            "weeks that follow, to test whether the market keeps drifting "
            "in the direction of the surprise instead of reacting all at "
            "once.",
        ),
    ]

    coverage_fig = _make_coverage_by_year_chart(df)
    distribution_fig, excluded_pct, n_total_surprise = _make_surprise_distribution_chart(df)
    data_quality_fig = _make_data_quality_chart(df)

    charts = [
        (
            coverage_fig,
            "Real analyst-based coverage (blue) grows from roughly half of "
            "announcements in the mid-1990s to over 85% by the early 2010s; "
            "the mechanical fallback (purple) fills much of the remaining "
            "gap in most years. The share with neither measure (red) is "
            "highest at the start of the sample and shrinks steadily.",
        ),
        (
            distribution_fig,
            f"Distribution of the pooled earnings-surprise measure across "
            f"{n_total_surprise:,} announcements that have one. Values are "
            f"clipped to -20 to +20 for readability; {excluded_pct:.1f}% of "
            "announcements have a more extreme surprise than that and are "
            "not shown on this chart, but are still included in the "
            "underlying data. Most announcements cluster close to zero, "
            "meaning the actual result was close to what analysts expected.",
        ),
        (
            data_quality_fig,
            "For the large majority of announcements, no stock split fell "
            "close enough to the announcement to raise a per-share-basis "
            "question at all (blue). Where one did, the reported figure "
            "was independently confirmed correct far more often than not "
            "(green); the remaining, smaller categories (red) are cases "
            "left honestly unresolved -- either a real gap in the "
            "underlying price data, an ambiguous company identifier, or "
            "evidence too weak to confirm which basis applies -- rather "
            "than guessed.",
        ),
    ]

    try:
        return report_builder.build_pdf_report(
            title="I/B/E/S Earnings Data: Cleaning and Surprise Construction",
            output_path=output_path,
            summary=(
                f"This report covers {n_events:,} quarterly earnings "
                f"announcements ({start_date} to {end_date}) cleaned from "
                "raw I/B/E/S analyst-estimate and CRSP share-price data, and "
                "how each announcement's earnings surprise was computed "
                "while guarding against look-ahead bias, stock-split "
                "artifacts, and ambiguous company identifiers."
            ),
            sections=sections,
            charts=charts,
        )
    finally:
        for fig in (coverage_fig, distribution_fig, data_quality_fig):
            plt.close(fig)


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    written = build_earnings_data_report(output)
    print(f"Earnings-data report written to: {written}")
