"""Second phase-completion PDF report: presents the I/B/E/S earnings-cleaning
and surprise-construction work (Slices 5-6, `scripts/clean_ibes_earnings.py`)
to a reader with no finance background, per `reports/README.md`'s standing
requirement (A4, one chart per region with a plain-language caption).

Every number and chart below is computed at runtime from the real, already-
cleaned output of that slice, `data/earnings/ibes_clean_1995_2014.parquet` --
this script has no data-cleaning logic of its own. With one explicit, named
exception (`_BASIS_CONFIRM_PRECISION_PCT`, a precision figure that can only
come from `data/earnings/FINDINGS.md`'s own cross-check against Compustat
data, not from this table alone), this script does not hand-type any other
figure from that file's prose -- every other number in this report is
computed at runtime from the table itself; FINDINGS.md is otherwise this
script's source for *what* to report on, not a number source. This mirrors
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
import numpy as np
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

# The statuses where split timing was never checked at all, for reasons
# unrelated to whether a split actually landed nearby (no CRSP price data to
# even attempt the check, or a company-identifier ambiguity that has nothing
# to do with split timing) -- as distinct from the in-window statuses above,
# where a split-timing check was actually attempted and either confirmed or
# left undetermined.
_NEVER_CHECKED_STATUSES = ["basis_unverified_no_crsp", "basis_undetermined_ambiguous_permno"]

# All six eps_basis_status values, in the fixed display order used by both
# the data-quality chart and its underlying breakdown.
_BASIS_STATUS_ORDER = [
    "anndats_basis_assumed",
    "anndats_basis_confirmed",
    "basis_unverified_no_crsp",
    "basis_undetermined_ambiguous_permno",
    "basis_undetermined_ambiguous",
    "basis_undetermined_no_reference",
]
_BASIS_STATUS_LABELS = {
    "anndats_basis_assumed": "No split nearby",
    "anndats_basis_confirmed": "Split nearby, confirmed correct",
    "basis_unverified_no_crsp": "No price data to check at all",
    "basis_undetermined_ambiguous_permno": "Ambiguous company ID",
    "basis_undetermined_ambiguous": "Split nearby, undetermined",
    "basis_undetermined_no_reference": "Split nearby, no prior quarter to check",
}

# Carried by hand from data/earnings/FINDINGS.md's own cross-check of
# `anndats_basis_confirmed` rows against Compustat `niq` -- this is the one
# figure in this whole script that genuinely cannot be recomputed from this
# table alone (there is no Compustat column here to check against), so it is
# named once, here, rather than typed inline anywhere it is used.
_BASIS_CONFIRM_PRECISION_PCT = 99.13

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
    if not df["sue_source"].notna().all():
        raise ValueError(
            "sue_source has null value(s) that would silently drop out of the per-year coverage shares"
        )
    unexpected = set(df["sue_source"].dropna().unique()) - {"analyst", "seasonal", "none"}
    if unexpected:
        raise ValueError(f"unexpected sue_source value(s) found: {sorted(unexpected)}")
    if not df["anndats"].notna().all():
        raise ValueError(
            "anndats has null value(s) that would silently drop out of the per-year coverage chart "
            "while still being counted in the overall event total elsewhere in the report"
        )

    by_year = (
        df.groupby(df["anndats"].dt.year.rename("year"))["sue_source"]
        .value_counts(normalize=True)
        .unstack(fill_value=0.0)
        .reindex(columns=["analyst", "seasonal", "none"], fill_value=0.0)
        .sort_index()
        * 100.0
    )
    return by_year


def _basis_status_breakdown(df: pd.DataFrame) -> dict:
    """Single source of truth for every `eps_basis_status` count and
    percentage used across the data-quality chart, its caption, and the
    section-6 prose. Computing this exactly once, here, is what keeps those
    three from drifting apart from each other -- the round-1 fix corrected
    the counts but left the caption/prose computing their own, and they
    drifted again; this helper is the fix for that class of bug, not just
    an instance of it.

    Distinguishes three different populations that eps_basis_status's six
    values conflate if read carelessly:
    - "no question at all" (`anndats_basis_assumed`): no split fell in this
      row's window, nothing to check.
    - "in window" (`anndats_basis_confirmed` plus the two undetermined-in-
      window statuses): a split actually landed close enough to raise a
      real split-timing question, which was either confirmed or left
      undetermined. This is the narrow, real split-timing population --
      about 0.6% of announcements, NOT the ~2.4% below.
    - "never checked at all" (`basis_unverified_no_crsp` plus
      `basis_undetermined_ambiguous_permno`): split timing was never even
      attempted, for reasons that have nothing to do with split timing (no
      CRSP price data, or a company-identifier ambiguity).
    "Some kind of basis question" (~2.4%) is the union of the second and
    third populations above -- conflating it with the second population
    alone is exactly the bug this helper exists to prevent.
    """
    if not df["eps_basis_status"].notna().all():
        raise ValueError(
            "eps_basis_status has null value(s) that would silently drop out of the data-quality chart"
        )
    unexpected = set(df["eps_basis_status"].dropna().unique()) - set(_BASIS_STATUS_ORDER)
    if unexpected:
        raise ValueError(f"unexpected eps_basis_status value(s) found: {sorted(unexpected)}")

    n_total = len(df)
    counts = df["eps_basis_status"].value_counts().reindex(_BASIS_STATUS_ORDER, fill_value=0)
    pct = 100.0 * counts / n_total

    n_assumed = int(counts["anndats_basis_assumed"])
    n_confirmed = int(counts["anndats_basis_confirmed"])
    n_in_window_undetermined = int(counts[_IN_WINDOW_UNDETERMINED_STATUSES].sum())
    n_in_window = n_confirmed + n_in_window_undetermined
    n_no_crsp = int(counts["basis_unverified_no_crsp"])
    n_ambiguous_permno = int(counts["basis_undetermined_ambiguous_permno"])
    n_never_checked = n_no_crsp + n_ambiguous_permno
    n_no_crsp_with_sue = int(
        ((df["eps_basis_status"] == "basis_unverified_no_crsp") & df["sue_primary"].notna()).sum()
    )

    pct_no_question = float(pct["anndats_basis_assumed"])
    pct_some_question = 100.0 - pct_no_question
    pct_in_window = 100.0 * n_in_window / n_total
    pct_never_checked = 100.0 * n_never_checked / n_total

    return {
        "n_total": n_total,
        "counts": counts,
        "pct": pct,
        "n_assumed": n_assumed,
        "n_confirmed": n_confirmed,
        "n_in_window_undetermined": n_in_window_undetermined,
        "n_in_window": n_in_window,
        "n_no_crsp": n_no_crsp,
        "n_ambiguous_permno": n_ambiguous_permno,
        "n_never_checked": n_never_checked,
        "n_no_crsp_with_sue": n_no_crsp_with_sue,
        "pct_no_question": pct_no_question,
        "pct_some_question": pct_some_question,
        "pct_in_window": pct_in_window,
        "pct_never_checked": pct_never_checked,
    }


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
    -- or reporting one pooled statistic across both -- would misleadingly
    compare unlike things. Both panels are clipped to the same display range
    for a fair visual comparison; the excluded extreme tail is counted per
    panel at runtime and reported in the caption, not silently dropped.

    Each panel's histogram is weighted to show percent-of-that-panel's-own-
    row-count rather than a raw count, so the two panels (194k analyst rows
    vs. 59k seasonal rows) are visually comparable on a shared vertical
    scale despite the large sample-size difference.

    Returns the figure and a stats dict with `n_total` (all non-null
    sue_primary rows, pooled -- a scale-free count, safe to pool) plus, for
    each of `analyst`/`seasonal`: `n_{source}` (panel sample size),
    `median_{source}`, `low_tail_{source}` (0.1th percentile),
    `high_tail_{source}` (99.9th percentile), and `excluded_pct_{source}`
    (share of that source's full subset falling outside the display clip
    range). No pooled median/tail/excluded-pct is returned -- those are
    scale-sensitive and must be reported per source, never pooled."""
    non_null = df["sue_primary"].dropna()
    if len(non_null) == 0:
        raise ValueError("No non-null sue_primary values available to build the surprise-distribution chart.")

    lo, hi = _SUE_DISPLAY_RANGE

    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.2))
    panel_specs = [
        ("analyst", "Measured against analyst expectations", "#2f855a"),
        ("seasonal", "Measured against the same quarter a year earlier", "#805ad5"),
    ]
    stats = {"n_total": len(non_null)}
    for ax, (source, title, color) in zip(axes, panel_specs):
        full_subset = df.loc[df["sue_source"] == source, "sue_primary"].dropna()
        if len(full_subset) == 0:
            raise ValueError(
                f"No non-null sue_primary values for sue_source={source!r}; cannot build that panel."
            )
        subset_in_range = full_subset[(full_subset >= lo) & (full_subset <= hi)]
        excluded_pct_source = 100.0 * (1.0 - len(subset_in_range) / len(full_subset))
        weights = np.full(len(subset_in_range), 100.0 / len(full_subset))
        ax.hist(subset_in_range, bins=60, weights=weights, color=color)
        ax.axvline(0, color="#333333", linewidth=0.8)
        ax.set_xlim(lo, hi)
        ax.set_xlabel("Earnings surprise (sue_primary)")
        ax.set_ylabel("% of that benchmark's announcements")
        ax.set_title(title)
        stats[f"n_{source}"] = len(full_subset)
        stats[f"median_{source}"] = float(full_subset.median())
        stats[f"low_tail_{source}"] = float(full_subset.quantile(0.001))
        stats[f"high_tail_{source}"] = float(full_subset.quantile(0.999))
        stats[f"excluded_pct_{source}"] = excluded_pct_source

    fig.suptitle("Distribution of the earnings-surprise measure, by benchmark")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    return fig, stats


def _make_data_quality_chart(df: pd.DataFrame) -> plt.Figure:
    """Two-panel bar chart of `eps_basis_status`, built entirely from
    `_basis_status_breakdown`'s single-sourced counts: whether each
    announcement's per-share basis raised any kind of question at all
    (left), and -- among the slice that did -- the detailed breakdown
    (right, its own y-axis scale, since those bars are all under ~1.1% of
    all rows and unreadable pooled with the left panel).

    The right panel deliberately mixes two different kinds of thing under
    one "some basis question" umbrella, distinguished by color: the real
    in-window split-timing outcomes (confirmed correct in green, left
    undetermined in red -- a split actually landed close enough to check),
    and the never-checked-at-all categories (grey -- split timing was never
    even attempted, for reasons unrelated to split timing: no CRSP price
    data, or an identifier ambiguity). Collapsing that distinction is
    exactly the population-conflation bug this chart, its caption, and the
    section-6 prose were all corrected to avoid.

    This is the split-adjustment / "tag, don't filter" handling FINDINGS.md
    documents in detail."""
    breakdown = _basis_status_breakdown(df)
    counts, pct = breakdown["counts"], breakdown["pct"]
    pct_no_question = breakdown["pct_no_question"]
    pct_some_question = breakdown["pct_some_question"]
    non_assumed = [s for s in _BASIS_STATUS_ORDER if s != "anndats_basis_assumed"]

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(9.5, 4.8), gridspec_kw={"width_ratios": [1, 2.5]}
    )

    left_labels = ["No basis\nquestion", "Some kind of\nbasis question"]
    left_values = [pct_no_question, pct_some_question]
    left_counts = [breakdown["n_assumed"], breakdown["n_total"] - breakdown["n_assumed"]]
    left_bars = ax_left.bar(left_labels, left_values, color=["#2b6cb0", "#dd6b20"])
    for bar, count in zip(left_bars, left_counts):
        ax_left.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"n={count:,}",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    ax_left.set_ylabel("Share of all announcements (%)")
    # Headroom above 100 so the tall bar's "n=" label (fix 13) doesn't
    # visually crowd the plot's top border.
    ax_left.set_ylim(0, 108)
    ax_left.set_title("Almost all announcements\nhad no basis question")
    ax_left.tick_params(axis="x", labelsize=8)

    right_colors = [
        "#2f855a"
        if s == "anndats_basis_confirmed"
        else "#718096"
        if s in _NEVER_CHECKED_STATUSES
        else "#c53030"
        for s in non_assumed
    ]
    right_bars = ax_right.bar(
        [_BASIS_STATUS_LABELS[s] for s in non_assumed], pct[non_assumed].values, color=right_colors
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
    ax_right.set_title(
        f"The {pct_some_question:.1f}% with some kind of basis question, in detail\n"
        "(note the different scale)"
    )

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
    pct_identifier_ambiguous = 100.0 * df["identifier_ambiguous"].fillna(False).sum() / n_events

    n_lookahead_excluded = int(df["n_lookahead_excluded"].sum())
    median_staleness = df["consensus_staleness_days"].median()
    max_staleness = df["consensus_staleness_days"].max()

    # eps_basis_status breakdown -- single-sourced (fix for the finding-1
    # bug class: the caption and this prose used to each compute their own
    # counts and drifted apart from each other). See _basis_status_breakdown
    # for the three populations this distinguishes.
    basis = _basis_status_breakdown(df)
    n_basis_checked = basis["n_confirmed"]
    n_in_window_undetermined = basis["n_in_window_undetermined"]
    n_ambiguous_permno = basis["n_ambiguous_permno"]
    n_no_crsp = basis["n_no_crsp"]
    n_no_crsp_with_sue = basis["n_no_crsp_with_sue"]
    n_in_window = basis["n_in_window"]
    n_never_checked = basis["n_never_checked"]
    pct_no_question = basis["pct_no_question"]
    pct_some_question = basis["pct_some_question"]
    pct_in_window = basis["pct_in_window"]
    pct_never_checked = basis["pct_never_checked"]

    # %-positive-of-non-null: scale-free (a share, not a level), so pooling
    # it across sue_source is fine -- unlike the median/tail stats below,
    # which are computed per source in _make_surprise_distribution_chart and
    # must never be re-pooled (see that function's docstring).
    sue_non_null = df["sue_primary"].dropna()
    pct_positive = 100.0 * (sue_non_null > 0).mean()
    n_seasonal_pooled = int((df.loc[df["sue_primary"].notna(), "sue_source"] == "seasonal").sum())
    pct_seasonal_pooled = 100.0 * n_seasonal_pooled / len(sue_non_null)

    # Charts (and the per-source distribution stats fix 5/6 need for their
    # prose) are built before `sections` below, so the narrative text can
    # read the exact same numbers the charts themselves show -- computing
    # them separately is exactly what let the prose and charts drift apart
    # from each other in round 1.
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

    n_total_surprise = dist_stats["n_total"]
    n_analyst_panel = dist_stats["n_analyst"]
    n_seasonal_panel = dist_stats["n_seasonal"]
    median_analyst = dist_stats["median_analyst"]
    low_tail_analyst = dist_stats["low_tail_analyst"]
    high_tail_analyst = dist_stats["high_tail_analyst"]
    excluded_pct_analyst = dist_stats["excluded_pct_analyst"]
    median_seasonal = dist_stats["median_seasonal"]
    low_tail_seasonal = dist_stats["low_tail_seasonal"]
    high_tail_seasonal = dist_stats["high_tail_seasonal"]
    excluded_pct_seasonal = dist_stats["excluded_pct_seasonal"]
    lo, hi = _SUE_DISPLAY_RANGE

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
            f"How coverage shifted, {start_date[:4]}-{end_date[:4]}",
            "Chart 1, at the end of this report, shows, for each "
            "announcement year, what share of that year's earnings reports "
            "got a real analyst-based surprise, what share fell back to "
            "the mechanical measure, and what share had neither. Analyst "
            "coverage is thinner in the earliest years of the sample, stays "
            "roughly flat (with a dip) through the late 1990s and early "
            "2000s, and then rises through the later 2000s and 2010s as "
            "more companies gained regular analyst coverage. The very "
            f"start ({start_date[:4]}-{int(start_date[:4]) + 1}) and very "
            f"end ({end_date[:4]}) of the window are both visibly thinner "
            "than the interior years: the second year sits at the edge of "
            "the one-year lookback the mechanical measure needs, and the "
            "final year falls just past a deliberate cutoff in how far the "
            "underlying analyst-estimate data was pulled -- both are known "
            "edge effects of the sample's boundaries, not data-quality "
            "problems.",
        ),
        (
            "What the surprise measure looks like across all events",
            "Most earnings announcements are close to what was expected -- "
            "a surprise near zero -- with progressively fewer large beats "
            "or misses the further from zero you go. That shape (a peak "
            "near zero, thinning tails) is real, and Chart 2, at the end "
            "of this report, shows it clearly for both benchmarks. Of the "
            "announcements that have a surprise value at all, "
            f"{pct_positive:.1f}% land on the positive (beat) side -- a "
            "well-documented tendency for reported results to land "
            "slightly above expectations. But the two benchmarks are on "
            "different scales and neither is simply symmetric around zero, "
            "so each needs its own numbers rather than one pooled figure: "
            f"against analyst expectations, the median surprise is "
            f"{median_analyst:+.2f}, and the most extreme 0.1% of "
            f"announcements reach as low as {low_tail_analyst:.1f} on the "
            f"downside versus only {high_tail_analyst:.1f} on the upside -- "
            "the negative tail reaches further from zero than the positive "
            f"one, though the two tails carry equal probability mass by "
            f"construction (each is a 0.1% cutoff). Against the mechanical "
            f"seasonal benchmark, the median surprise is "
            f"{median_seasonal:+.2f}, with its own most-extreme 0.1% "
            f"reaching {low_tail_seasonal:.1f} on the downside and "
            f"{high_tail_seasonal:.1f} on the upside -- a similar shape, on "
            "a different scale. This combined measure, `sue_primary`, is "
            "also not uniformly benchmarked against what analysts "
            f"expected: {pct_seasonal_pooled:.1f}% of the announcements "
            f"with a value ({n_seasonal_pooled:,} of {len(sue_non_null):,}) "
            "are instead benchmarked against that same company's own "
            "result a year earlier (the mechanical fallback measure), not "
            "against any analyst estimate.",
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
            "the analyst estimate are not on the same per-share basis. Only "
            f"{pct_in_window:.1f}% of announcements ({n_in_window:,}) "
            "actually had a stock split land close enough to the "
            "announcement to raise this question at all. Of those, "
            f"{n_basis_checked:,} were confirmed correct using the "
            "company's own prior-quarter figure -- a check measured about "
            f"{_BASIS_CONFIRM_PRECISION_PCT:.2f}% accurate against "
            "Compustat per this project's underlying data notes -- while "
            f"the remaining {n_in_window_undetermined:,} are left "
            "undetermined because the split-timing evidence was too weak "
            "to confirm either way, so the surprise measure is left blank "
            "for those rows rather than guessed. Separately, a further "
            f"{pct_never_checked:.1f}% of announcements ({n_never_checked:,}) "
            "never had split timing checked at all, for reasons unrelated "
            f"to split timing itself: {n_no_crsp:,} had no CRSP price data "
            "at all to even check, so the pipeline assumes no split "
            "adjustment was needed for these (a disclosed tradeoff that is "
            f"sometimes wrong, and {n_no_crsp_with_sue:,} of them still "
            f"carry a surprise value computed under that assumption), and "
            f"{n_ambiguous_permno:,} are the identifier-ambiguous rows "
            "already discussed above, where competing candidate company "
            "IDs disagree about whether a split happened at all. Put "
            f"together, {pct_some_question:.1f}% of announcements have "
            "some kind of per-share-basis question, of one sort or "
            "another; Chart 3, at the end of this report, shows the full "
            "breakdown.",
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
            f"Chart 2 -- Distribution of the earnings-surprise measure "
            f"(`sue_primary`) across {n_total_surprise:,} announcements "
            "that have one, split by which benchmark produced it: analyst "
            f"expectations (left, {n_analyst_panel:,} announcements) or "
            f"the same quarter a year earlier (right, the mechanical "
            f"fallback, {n_seasonal_panel:,} announcements). Each panel's "
            "bars show that panel's own share of ITS OWN announcements "
            "(not a raw count), so the two panels are visually comparable "
            f"despite the very different sample sizes. Values are clipped "
            f"to {lo} to {hi} for readability; {excluded_pct_analyst:.1f}% "
            f"of the analyst-benchmarked panel and {excluded_pct_seasonal:.1f}% "
            "of the seasonal-benchmarked panel have a more extreme "
            "surprise than that and are not shown here, but are still "
            "included in the underlying data. The two measures are on "
            "different scales (analyst disagreement vs. a company's own "
            "year-over-year volatility) and should not be pooled for "
            "anything scale-sensitive.",
        ),
        (
            data_quality_fig,
            f"Chart 3 -- Left: {pct_no_question:.1f}% of all announcements "
            f"had no per-share-basis question at all; the remaining "
            f"{pct_some_question:.1f}% had some kind of basis question "
            "(right, note the different scale). Of that "
            f"{pct_some_question:.1f}%, only {pct_in_window:.1f}% "
            f"({n_in_window:,} announcements) actually had a stock split "
            "land close enough to the announcement to raise a real "
            f"split-timing question: {n_basis_checked:,} were confirmed "
            "correct using the company's own prior-quarter figure (a check "
            f"measured about {_BASIS_CONFIRM_PRECISION_PCT:.2f}% accurate "
            f"against Compustat), and {n_in_window_undetermined:,} are "
            "left undetermined because the split-timing evidence was too "
            "weak either way. The two grey bars are a different, unrelated "
            f"kind of gap, where split timing was never checked at all: "
            f"{n_ambiguous_permno:,} announcements where competing "
            "candidate company IDs disagree about whether a split "
            f"happened at all (an identifier problem, not a split-timing "
            f"one), and {n_no_crsp:,} with no CRSP price data to check at "
            "all, for which the pipeline assumes no split adjustment was "
            f"needed -- a disclosed tradeoff that is sometimes wrong -- "
            f"and {n_no_crsp_with_sue:,} of those still carry a surprise "
            "value computed under that assumption. Together, those two "
            f"never-checked-at-all categories account for "
            f"{pct_never_checked:.1f}% of all announcements.",
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
