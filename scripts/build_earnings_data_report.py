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

# The eps_basis_status statuses that count as "in window" for split-timing
# purposes (per FINDINGS.md's own "in-window" total) -- deliberately
# excludes basis_undetermined_ambiguous_permno, which fires purely off
# disagreeing candidate identifiers, independent of split timing.
_IN_WINDOW_UNDETERMINED_STATUSES = ["basis_undetermined_ambiguous", "basis_undetermined_no_reference"]

# Only the columns this script actually reads anywhere below -- read via an
# explicit Parquet column projection so the full (wider) file is never
# materialized in memory just to throw most of it away.
_USED_COLUMNS = [
    "anndats",
    "sue_analyst",
    "sue_seasonal",
    "sue_primary",
    "sue_source",
    "identifier_ambiguous",
    "eps_basis_status",
    "n_lookahead_excluded",
    "consensus_staleness_days",
]


def load_earnings_data(path: Path = DEFAULT_INPUT) -> pd.DataFrame:
    """Load the cleaned I/B/E/S earnings table this report is built from."""
    return pd.read_parquet(path, columns=_USED_COLUMNS)


def _coverage_shares_by_year(df: pd.DataFrame) -> pd.DataFrame:
    """Per-year share (%) of each `sue_source` outcome (analyst / seasonal /
    none), reindexed to a fixed column order. Shared by the coverage-by-year
    chart and the caption/prose that describes its trend, so both always
    read the exact same numbers off the exact same computation."""
    assert df["sue_source"].notna().all(), (
        "sue_source has null value(s) that would silently drop out of the per-year coverage shares"
    )
    unexpected = set(df["sue_source"].dropna().unique()) - {"analyst", "seasonal", "none"}
    assert not unexpected, f"unexpected sue_source value(s) found: {sorted(unexpected)}"

    by_year = (
        df.groupby(df["anndats"].dt.year.rename("year"))["sue_source"]
        .value_counts(normalize=True)
        .unstack(fill_value=0.0)
        .reindex(columns=["analyst", "seasonal", "none"], fill_value=0.0)
        .sort_index()
        * 100.0
    )
    return by_year


def _make_coverage_by_year_chart(df: pd.DataFrame) -> plt.Figure:
    """Stacked bar chart: for each announcement year, what share of events
    got a `sue_analyst` (real analyst-estimate-based) surprise, what share
    fell back to the mechanical `sue_seasonal` measure, and what share had
    neither -- per `sue_source`, the same selection FINDINGS.md's per-year
    coverage table describes. This shows which measure ENDED UP BEING USED
    (sue_source is a mutually-exclusive outcome), not raw availability."""
    by_year = _coverage_shares_by_year(df)

    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    years = by_year.index.astype(str)
    bottom = pd.Series(0.0, index=by_year.index)
    colors = {"analyst": "#2b6cb0", "seasonal": "#805ad5", "none": "#c53030"}
    labels = {
        "analyst": "Real analyst estimate used",
        "seasonal": "Fallback (mechanical) measure used",
        "none": "Neither used",
    }
    for source in ["analyst", "seasonal", "none"]:
        ax.bar(years, by_year[source], bottom=bottom, color=colors[source], label=labels[source])
        bottom = bottom + by_year[source]

    ax.set_ylabel("Share of that year's earnings announcements (%)")
    ax.set_xlabel("Announcement year")
    ax.set_title("Which surprise measure was used, by year")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", rotation=60)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=3, fontsize=8, frameon=False)
    fig.tight_layout()
    return fig


def _make_surprise_distribution_chart(df: pd.DataFrame) -> tuple[plt.Figure, dict]:
    """Two-panel histogram of `sue_primary`, split by `sue_source`: analyst-
    benchmarked surprises (left) and seasonal-benchmarked surprises (right).
    The two measures are on different scales (analyst disagreement vs. a
    company's own year-over-year volatility) and pooling them into one panel
    would misleadingly compare unlike things on one axis. Both panels are
    clipped to the same display range for a fair visual comparison; the
    excluded extreme tail is counted at runtime and reported in the caption,
    not silently dropped.

    Returns the figure and a stats dict (`n_total`, `excluded_pct`, and
    `n_analyst`/`n_seasonal` sample sizes per panel) the caller needs to
    write an accurate caption and prose."""
    non_null = df["sue_primary"].dropna()
    if len(non_null) == 0:
        raise ValueError("No non-null sue_primary values available to build the surprise-distribution chart.")

    lo, hi = _SUE_DISPLAY_RANGE
    in_range_all = non_null[(non_null >= lo) & (non_null <= hi)]
    excluded_pct = 100.0 * (1.0 - len(in_range_all) / len(non_null))

    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.2))
    panel_specs = [
        ("analyst", "Measured against analyst expectations", "#2f855a"),
        ("seasonal", "Measured against the same quarter a year earlier", "#805ad5"),
    ]
    stats = {"n_total": len(non_null), "excluded_pct": excluded_pct}
    for ax, (source, title, color) in zip(axes, panel_specs):
        subset = df.loc[df["sue_source"] == source, "sue_primary"].dropna()
        if len(subset) == 0:
            raise ValueError(
                f"No non-null sue_primary values for sue_source={source!r}; cannot build that panel."
            )
        subset_in_range = subset[(subset >= lo) & (subset <= hi)]
        ax.hist(subset_in_range, bins=60, color=color)
        ax.axvline(0, color="#333333", linewidth=0.8)
        ax.set_xlim(lo, hi)
        ax.set_xlabel("Earnings surprise (sue_primary)")
        ax.set_ylabel("Number of earnings announcements")
        ax.set_title(title)
        stats[f"n_{source}"] = len(subset)

    fig.suptitle("Distribution of the earnings-surprise measure, by benchmark")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    return fig, stats


def _make_data_quality_chart(df: pd.DataFrame) -> plt.Figure:
    """Two-panel bar chart of `eps_basis_status`: how often a stock split
    fell close enough to an announcement to raise a per-share-basis question
    at all (left), and -- among the narrow slice that did -- how that
    question was resolved (right, its own y-axis scale, since those bars are
    all under ~1.1% of all rows and unreadable pooled with the left panel).
    This is the split-adjustment / "tag, don't filter" handling FINDINGS.md
    documents in detail."""
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

    assert df["eps_basis_status"].notna().all(), (
        "eps_basis_status has null value(s) that would silently drop out of the data-quality chart"
    )
    unexpected = set(df["eps_basis_status"].dropna().unique()) - set(status_order)
    assert not unexpected, f"unexpected eps_basis_status value(s) found: {sorted(unexpected)}"

    n_total = len(df)
    counts = df["eps_basis_status"].value_counts().reindex(status_order, fill_value=0)
    pct = 100.0 * counts / n_total

    pct_no_question = float(pct["anndats_basis_assumed"])
    pct_some_question = 100.0 - pct_no_question
    non_assumed = [s for s in status_order if s != "anndats_basis_assumed"]

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(9.5, 4.8), gridspec_kw={"width_ratios": [1, 2.5]}
    )

    ax_left.bar(
        ["No per-share-basis\nquestion", "Some basis\nquestion"],
        [pct_no_question, pct_some_question],
        color=["#2b6cb0", "#dd6b20"],
    )
    ax_left.set_ylabel("Share of all announcements (%)")
    ax_left.set_ylim(0, 100)
    ax_left.set_title("Almost all announcements\nhad nothing to check")

    right_colors = [
        "#2f855a" if s == "anndats_basis_confirmed" else "#c53030" for s in non_assumed
    ]
    right_bars = ax_right.bar(
        [status_labels[s] for s in non_assumed], pct[non_assumed].values, color=right_colors
    )
    for bar, status in zip(right_bars, non_assumed):
        count = int(counts[status])
        ax_right.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"n={count:,}",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    ax_right.set_ylabel("Share of all announcements (%)")
    ax_right.tick_params(axis="x", labelsize=8)
    plt.setp(ax_right.get_xticklabels(), rotation=30, ha="right", rotation_mode="anchor")
    ax_right.set_title(f"The {pct_some_question:.1f}% that did, in detail\n(note the different scale)")

    fig.suptitle("Was each announcement's per-share basis checked for stock splits?")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
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

    n_lookahead_excluded = int(df["n_lookahead_excluded"].sum())
    median_staleness = df["consensus_staleness_days"].median()
    max_staleness = df["consensus_staleness_days"].max()

    # eps_basis_status breakdown, split into its real, unrelated causes
    # (see FINDINGS.md's "six statuses" table) rather than pooled into one
    # "undetermined" bucket that mixes a split-timing question with an
    # unrelated identifier-ambiguity question.
    n_assumed = int((df["eps_basis_status"] == "anndats_basis_assumed").sum())
    n_basis_checked = int((df["eps_basis_status"] == "anndats_basis_confirmed").sum())
    n_in_window_undetermined = int(df["eps_basis_status"].isin(_IN_WINDOW_UNDETERMINED_STATUSES).sum())
    n_ambiguous_permno = int((df["eps_basis_status"] == "basis_undetermined_ambiguous_permno").sum())
    n_no_crsp = int((df["eps_basis_status"] == "basis_unverified_no_crsp").sum())
    n_no_crsp_with_sue = int(
        ((df["eps_basis_status"] == "basis_unverified_no_crsp") & df["sue_primary"].notna()).sum()
    )
    pct_no_question = 100.0 * n_assumed / n_events
    pct_some_question = 100.0 - pct_no_question

    # Distribution-shape stats (fix 7): the pooled sue_primary shape is a
    # real near-zero peak with real, asymmetric tails -- computed here so
    # the prose states the actual direction/size found, not an assumption.
    sue_non_null = df["sue_primary"].dropna()
    median_sue = sue_non_null.median()
    pct_positive = 100.0 * (sue_non_null > 0).mean()
    low_tail = sue_non_null.quantile(0.001)
    high_tail = sue_non_null.quantile(0.999)
    n_seasonal_pooled = int((df.loc[df["sue_primary"].notna(), "sue_source"] == "seasonal").sum())
    pct_seasonal_pooled = 100.0 * n_seasonal_pooled / len(sue_non_null)

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
            f"announcement it is compared against. Of all announcements, "
            f"{n_lookahead_excluded:,} consensus snapshots (each one an "
            "average across many analysts) were found to have been "
            "published on or after the announcement date and were excluded "
            "for exactly this reason -- an expected, mechanical filter, not "
            "a data error. But publishing beforehand is not the same as "
            "being fresh: a matched estimate can still be old, with a "
            f"median gap of {median_staleness:.0f} days between the "
            f"estimate and the announcement, running as high as "
            f"{max_staleness:,.0f} days for late or restated results. That "
            "gap (`consensus_staleness_days`) is recorded on every row with "
            "a matched estimate, not filtered on -- whether to require a "
            "fresher estimate for some later analysis is left to a later "
            "slice.",
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
            f"Across the full sample, a real analyst estimate was usable "
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
            "Chart 1, at the end of this report, shows which measure ended "
            "up being used in each year of the sample.",
        ),
        (
            "How coverage shifted, 1995-2014",
            "Chart 1, at the end of this report, shows, for each "
            "announcement year, what share of that year's earnings reports "
            "got a real analyst-based surprise, what share fell back to "
            "the mechanical measure, and what share had neither. Analyst "
            "coverage is thinner in the earliest years of the sample and "
            "grows steadily through the 2000s and 2010s as more companies "
            "gained regular analyst coverage. The very start (1995-1996) "
            "and very end (2014) of the window are both visibly thinner "
            "than the interior years: 1996 sits at the edge of the "
            "one-year lookback the mechanical measure needs, and 2014 "
            "falls just past a deliberate cutoff in how far the underlying "
            "analyst-estimate data was pulled -- both are known edge "
            "effects of the sample's boundaries, not data-quality "
            "problems.",
        ),
        (
            "What the surprise measure looks like across all events",
            "Most earnings announcements are close to what was expected -- "
            "a surprise near zero -- with progressively fewer large beats "
            "or misses the further from zero you go. That shape (a peak "
            "near zero, thinning tails) is real, and Chart 2, at the end "
            "of this report, shows it clearly for both benchmarks. But the "
            f"distribution is not simply symmetric around zero: the median "
            f"surprise is {median_sue:+.2f}, and {pct_positive:.1f}% of "
            "announcements land on the positive (beat) side -- a "
            "well-documented tendency for reported results to land "
            f"slightly above expectations. The tails are not symmetric "
            f"either: the most extreme 0.1% of announcements on the low "
            f"side reach as low as {low_tail:.1f}, while the most extreme "
            f"0.1% on the high side only reach {high_tail:.1f} -- the "
            "negative tail is longer and heavier than the positive one. "
            "This combined measure, `sue_primary`, is also not uniformly "
            f"benchmarked against what analysts expected: {pct_seasonal_pooled:.1f}% "
            f"of the announcements with a value ({n_seasonal_pooled:,} of "
            f"{len(sue_non_null):,}) are instead benchmarked against that "
            "same company's own result a year earlier (the mechanical "
            "fallback measure), not against any analyst estimate.",
        ),
        (
            "Data-quality handling: which company, and on what basis",
            "Two separate identification problems had to be handled "
            "honestly rather than guessed, and one shades into the other. "
            "First, a small share of announcements could not be tied to a "
            "single, unambiguous company identifier: "
            f"{pct_identifier_ambiguous:.1f}% of announcements carry more "
            "than one candidate company ID, most often because the company "
            "was reassigned a new internal ID after a corporate "
            "restructuring. These are flagged in the data rather than "
            f"silently resolved one way or another. {n_ambiguous_permno:,} "
            "of these are specifically cases where the competing candidate "
            "company IDs disagree about whether a stock split happened at "
            "all -- so those rows' surprise values are blank for the "
            "identifier reason, not because of any split-timing question. "
            "Second, stock splits change how many shares a company's "
            "earnings are divided by, which can make a perfectly normal "
            "result look like a huge surprise if the reported figure and "
            f"the analyst estimate are not on the same per-share basis. For "
            f"{pct_no_question:.1f}% of announcements, no split fell close "
            f"enough to the announcement to raise this question at all. Of "
            f"the remaining {pct_some_question:.1f}% that did, the split is "
            f"NOT mostly confirmed correct: {n_basis_checked:,} were "
            "confirmed correct using the company's own prior-quarter "
            "figure -- a check measured about 99% accurate per this "
            f"project's underlying data notes -- while a further "
            f"{n_in_window_undetermined:,} are left undetermined because "
            "the split-timing evidence was too weak to confirm either way, "
            "so the surprise measure is left blank for those rows rather "
            f"than guessed. Separately, for {n_no_crsp:,} announcements "
            "there was no CRSP price data at all to even check: the "
            "pipeline assumes no split adjustment was needed for these (a "
            f"disclosed tradeoff that is sometimes wrong), and "
            f"{n_no_crsp_with_sue:,} of them still carry a surprise value "
            "computed under that assumption. Chart 3, at the end of this "
            "report, shows the full breakdown.",
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
    distribution_fig, dist_stats = _make_surprise_distribution_chart(df)
    data_quality_fig = _make_data_quality_chart(df)

    by_year = _coverage_shares_by_year(df)
    analyst_share = by_year["analyst"]
    none_share = by_year["none"]
    year_first, year_last = analyst_share.index[0], analyst_share.index[-1]
    pct_analyst_first, pct_analyst_last = analyst_share.iloc[0], analyst_share.iloc[-1]
    year_max = analyst_share.idxmax()
    pct_analyst_max = analyst_share.loc[year_max]
    pct_none_first = none_share.iloc[0]
    year_none_min = none_share.idxmin()
    pct_none_min = none_share.loc[year_none_min]

    excluded_pct = dist_stats["excluded_pct"]
    n_total_surprise = dist_stats["n_total"]
    n_analyst_panel = dist_stats["n_analyst"]
    n_seasonal_panel = dist_stats["n_seasonal"]
    lo, hi = _SUE_DISPLAY_RANGE

    charts = [
        (
            coverage_fig,
            f"Chart 1 -- Real analyst-based coverage (blue) starts at "
            f"{pct_analyst_first:.1f}% of announcements in {year_first}, "
            f"peaks at {pct_analyst_max:.1f}% in {year_max}, and ends the "
            f"sample at {pct_analyst_last:.1f}% in {year_last} (see the "
            "pull-cutoff edge effect noted above for that final year). The "
            "mechanical fallback (purple) fills much of the remaining gap "
            f"in most years. The share with neither measure (red) falls "
            f"from {pct_none_first:.1f}% in {year_first} to a low of "
            f"{pct_none_min:.1f}% in {year_none_min}, though not without "
            "some up-years along the way.",
        ),
        (
            distribution_fig,
            f"Chart 2 -- Distribution of the pooled earnings-surprise "
            f"measure across {n_total_surprise:,} announcements that have "
            f"one, split by which benchmark produced it: analyst "
            f"expectations (left, {n_analyst_panel:,} announcements) or "
            f"the same quarter a year earlier (right, the mechanical "
            f"fallback, {n_seasonal_panel:,} announcements). Values are "
            f"clipped to {lo} to {hi} for readability; {excluded_pct:.1f}% "
            "of announcements across both panels combined have a more "
            "extreme surprise than that and are not shown here, but are "
            "still included in the underlying data. The two measures are "
            "on different scales (analyst disagreement vs. a company's own "
            "year-over-year volatility) and should not be pooled for "
            "anything scale-sensitive.",
        ),
        (
            data_quality_fig,
            f"Chart 3 -- Left: {pct_no_question:.1f}% of all announcements "
            "had no split near enough the announcement to raise a "
            f"per-share-basis question at all; the remaining "
            f"{pct_some_question:.1f}% did. Right: among that "
            f"{pct_some_question:.1f}%, the split is NOT mostly confirmed "
            f"correct -- {n_basis_checked:,} were confirmed correct using "
            "the company's own prior-quarter figure (a check measured "
            f"about 99% accurate), {n_in_window_undetermined:,} are left "
            "undetermined because the split-timing evidence was too weak "
            f"either way, {n_ambiguous_permno:,} are left undetermined "
            "because competing candidate company IDs disagree about "
            "whether a split happened at all (an identifier problem, not "
            f"a split-timing one), and {n_no_crsp:,} have no CRSP price "
            "data to check at all -- for these last, the pipeline assumes "
            "no split adjustment was needed (a disclosed tradeoff that is "
            f"sometimes wrong), and {n_no_crsp_with_sue:,} of them still "
            "carry a surprise value computed under that assumption.",
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
