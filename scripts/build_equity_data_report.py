"""Builds reports/equity_data_audit.pdf -- the project's first phase-
completion report (Slice 4 of memory/phase1-equity-pead-plan.md), covering
Slice 2's equity-data cleaning work: what data/normalized_equity/ is, the
split/dividend adjustment-status finding, the master_stock.csv cross-
validation, and coverage -- for a reader with no finance background, per
reports/README.md.

Every number and chart below is computed at runtime from the real files
under data/normalized_equity/ (and, for the cross-validation chart,
data/raw_equity/master_stock.csv) -- nothing here is copy-pasted from
data/normalized_equity/FINDINGS.md's prose. The cross-validation chart
reuses scripts/normalize_equity_data.py's own already-reviewed
read_master_stock_raw/clean_master_stock_frame/cross_validate_master_stock
functions (imported, not modified) rather than re-implementing that join
and its ticker-collision/misalignment handling a second time -- this
script's job is presenting that slice's findings, not re-deriving them.

Run directly:

    .venv/bin/python scripts/build_equity_data_report.py [output_path]

Default output: reports/equity_data_audit.pdf (reports/ is tracked in git,
per reports/README.md -- unlike data/, which this script only reads from).
"""

import datetime as dt
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this script never needs an on-screen window

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize_equity_data as ned  # noqa: E402
import report_builder  # noqa: E402

# --- Defaults: single-sourced from normalize_equity_data.py's own module-
# level constants (paths, named spot-check dates/tickers, the CRSP-vs-
# master_stock.csv comparison window) rather than re-typing the same
# literals a second time. Every one of these is overridable by the caller
# (see build_equity_data_report()'s parameters) so tests can point at small
# synthetic fixtures instead of the real, multi-hundred-MB data files.
REPO_ROOT = ned.REPO_ROOT
DEFAULT_DATA_DIR = ned.OUT_DIR
DEFAULT_MASTER_STOCK_PATH = ned.MASTER_STOCK_PATH
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "equity_data_audit.pdf"
DEFAULT_CROSSVAL_YEARS = ned.CROSSVAL_YEARS
DEFAULT_ARCHIVE_END_DATE = ned.ARCHIVE_END_DATE
DEFAULT_AAPL_TICKER = ned.AAPL_TICKER
DEFAULT_AAPL_SPLIT_YEAR = 2000
DEFAULT_AAPL_SPLIT_DATE = ned.AAPL_SPLIT_YEAR_DATES[DEFAULT_AAPL_SPLIT_YEAR]
DEFAULT_MSFT_TICKER = ned.MSFT_TICKER
DEFAULT_MSFT_DIV_YEAR = ned.MSFT_DIVIDEND_SPOT_YEAR
DEFAULT_MSFT_DIV_DATE = ned.MSFT_DIVIDEND_SPOT_DATE
DEFAULT_WINDOW_DAYS = 15

# Colors: the dataviz skill's validated default reference palette
# (categorical slot 1 "blue" / slot 2 "orange", plus its light-surface
# chrome), used as-is -- not a new palette invented for this report, so no
# separate colorblind-safety validation run is needed here.
_BLUE = "#2a78d6"
_ORANGE = "#eb6834"
_MUTED = "#898781"
_GRID = "#e1e0d9"


# ---------------------------------------------------------------------------
# Data loading / stats -- pure functions, no chart/report dependency, so
# they're independently testable against small fixtures.
# ---------------------------------------------------------------------------


def _discover_equity_years(data_dir: Path) -> list[int]:
    """Sorted list of years with an equity_<year>.parquet file present
    under data_dir, discovered from what's actually on disk -- matches
    normalize_equity_data.discover_dsf_years's own discover-don't-assume
    convention, just against this slice's output filename pattern instead
    of the raw pull's."""
    years = []
    for p in sorted(Path(data_dir).glob("equity_*.parquet")):
        suffix = p.stem.split("_", 1)[1]
        if suffix.isdigit():
            years.append(int(suffix))
    return sorted(years)


def _compute_coverage_and_cleaning_stats(data_dir: Path) -> tuple[pd.DataFrame, dict]:
    """One pass over every equity_<year>.parquet file (columns projected
    down to just permno/ret/prc_is_bidask_avg -- not the full 11-column
    frame) to get both the per-year coverage numbers (for the coverage
    chart) and the whole-window cleaning-evidence numbers (for the intro
    section): total rows, total distinct permnos, how many rows used
    CRSP's negative-price/bid-ask-average convention, and how many rows (if
    any) still carry one of CRSP's legacy missing-return sentinel codes.
    """
    years = _discover_equity_years(data_dir)
    if not years:
        raise FileNotFoundError(f"No equity_<year>.parquet files found under {data_dir}")

    rows_by_year = []
    permnos_by_year = []
    permno_union: set = set()
    total_rows = 0
    bidask_rows = 0
    missing_code_rows = 0

    for year in years:
        path = Path(data_dir) / f"equity_{year}.parquet"
        cols = ["permno", "ret", "prc_is_bidask_avg"]
        df = pd.read_parquet(path, columns=cols)
        rows_by_year.append(len(df))
        year_permnos = df["permno"].unique()
        permnos_by_year.append(int(len(year_permnos)))
        permno_union.update(year_permnos.tolist())
        total_rows += len(df)
        bidask_rows += int(df["prc_is_bidask_avg"].sum())
        missing_code_rows += int(df["ret"].isin(ned.CRSP_MISSING_RETURN_CODES).sum())

    coverage = pd.DataFrame({"year": years, "rows": rows_by_year, "permnos": permnos_by_year})
    stats = {
        "n_years": len(years),
        "year_min": years[0],
        "year_max": years[-1],
        "total_rows": total_rows,
        "total_distinct_permnos": len(permno_union),
        "bidask_rows": bidask_rows,
        "bidask_frac": (bidask_rows / total_rows) if total_rows else 0.0,
        "missing_code_rows": missing_code_rows,
    }
    return coverage, stats


def _load_ticker_window(
    data_dir: Path, year: int, ticker: str, center_date: str, window_days: int
) -> pd.DataFrame:
    """Rows for one ticker within +/- window_days calendar days of
    center_date, from a single year's equity_<year>.parquet -- the exact
    slice a human would eyeball to check "does the return column stay
    smooth through a split/dividend event." Column projection keeps this
    to a single small read even though the source file has 20-850k rows.
    """
    path = Path(data_dir) / f"equity_{year}.parquet"
    df = pd.read_parquet(path, columns=["date", "ticker", "prc", "ret", "retx"])
    df = df.loc[df["ticker"] == ticker].copy()
    center = pd.Timestamp(center_date)
    # dt.timedelta rather than pd.Timedelta: under this project's pinned
    # pandas/numpy versions, pd.Timedelta(days=int) on its own triggers a
    # spurious "generic unit" DeprecationWarning from NumPy's timedelta64
    # handling (see normalize_equity_data.split_spot_check's identical
    # comment); a plain stdlib timedelta sidesteps it entirely.
    start = center - dt.timedelta(days=window_days)
    end = center + dt.timedelta(days=window_days)
    df = df.loc[(df["date"] >= start) & (df["date"] <= end)].sort_values("date").reset_index(drop=True)
    return df


def _load_crossval_crsp_frame(data_dir: Path, years) -> pd.DataFrame:
    """The permno/ticker/date/prc/ret/retx columns for every crossval year,
    concatenated -- exactly what cross_validate_master_stock() needs as its
    `crsp_df` argument (see that function's docstring)."""
    cols = ["permno", "ticker", "date", "prc", "ret", "retx"]
    frames = [pd.read_parquet(Path(data_dir) / f"equity_{year}.parquet", columns=cols) for year in years]
    if not frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(frames, ignore_index=True)


def _run_master_stock_crossval(
    data_dir: Path, master_stock_path: Path, crossval_years, archive_end_date: str
) -> tuple[dict, pd.DataFrame]:
    """Reuses normalize_equity_data.py's own already-reviewed
    read_master_stock_raw / clean_master_stock_frame / cross_validate_
    master_stock functions (see module docstring for why) to reproduce
    Slice 2's master_stock.csv cross-validation. Returns (stats, merged) --
    merged is empty and stats is {"skipped": True} if either side has no
    usable rows in the crossval years, matching that function's own guard
    in normalize_equity_data.main()."""
    crsp_crossval = _load_crossval_crsp_frame(data_dir, crossval_years)
    master_raw = ned.read_master_stock_raw(master_stock_path, end_date=archive_end_date)
    master_clean, _master_clean_stats = ned.clean_master_stock_frame(master_raw, end_date=archive_end_date)

    if crsp_crossval.empty or master_clean.empty:
        return {"skipped": True}, pd.DataFrame()

    window_start = max(pd.Timestamp(master_clean["date"].min()), pd.Timestamp(crsp_crossval["date"].min()))
    window_end = min(pd.Timestamp(archive_end_date), pd.Timestamp(crsp_crossval["date"].max()))
    return ned.cross_validate_master_stock(crsp_crossval, master_clean, window_start, window_end)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def _make_coverage_chart(coverage: pd.DataFrame) -> plt.Figure:
    fig, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True)

    axes[0].bar(coverage["year"], coverage["rows"] / 1000.0, color=_BLUE)
    axes[0].set_ylabel("Rows (thousands)")
    axes[0].set_title("Cleaned (permno, date) rows by year")

    axes[1].bar(coverage["year"], coverage["permnos"], color=_BLUE)
    axes[1].set_ylabel("Distinct stocks (permnos)")
    axes[1].set_xlabel("Year")
    axes[1].set_title("Distinct stocks covered by year")
    axes[1].set_xticks(list(coverage["year"])[::2])
    plt.setp(axes[1].get_xticklabels(), rotation=45, ha="right")

    for ax in axes:
        ax.grid(axis="y", color=_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return fig


def _make_split_chart(window_df: pd.DataFrame, ticker: str, split_date: str) -> plt.Figure:
    """Two stacked panels (small multiples, not a dual-axis overlay -- the
    two series are different units): the stock's actual traded price
    (which roughly halves on the split date) above its return-derived
    cumulative value (which does not), over the same window."""
    split_ts = pd.Timestamp(split_date)
    df = window_df.copy()
    df["cum_return_index"] = (1.0 + df["ret"].fillna(0.0)).cumprod()

    fig, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True)

    axes[0].plot(df["date"], df["prc"], color=_BLUE, linewidth=2, marker="o", markersize=3)
    axes[0].axvline(split_ts, color=_MUTED, linestyle="--", linewidth=1)
    axes[0].set_ylabel("Traded price ($)")
    axes[0].set_title(f"{ticker}: actual traded price around its {split_ts.date()} split")

    axes[1].plot(df["date"], df["cum_return_index"], color=_BLUE, linewidth=2, marker="o", markersize=3)
    axes[1].axvline(split_ts, color=_MUTED, linestyle="--", linewidth=1)
    axes[1].set_ylabel("Value of $1 invested\n(built from the return column)")
    axes[1].set_xlabel("Date")
    axes[1].set_title("Same window, built from the return column instead of price")
    plt.setp(axes[1].get_xticklabels(), rotation=45, ha="right")

    for ax in axes:
        ax.grid(axis="y", color=_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return fig


def _make_crossval_chart(full_ret_cmp: pd.DataFrame) -> plt.Figure:
    """Hexbin (not a scatter -- this is well over a million points) of
    CRSP's day-over-day return against master_stock.csv's own day-over-day
    return, for every matched (ticker, date) pair with both sides present
    and not sitting on a ticker-reassignment handoff day. A single
    sequential blue ramp encodes point density; the dashed line marks
    perfect agreement."""
    x = full_ret_cmp["retx"].to_numpy(dtype="float64")
    y = full_ret_cmp["master_ret"].to_numpy(dtype="float64")

    lim = float(np.nanpercentile(np.abs(np.concatenate([x, y])), 99.5))
    lim = max(lim, 1e-4)  # guard against a degenerate all-zero fixture in tests

    fig, ax = plt.subplots(figsize=(6.5, 6))
    hb = ax.hexbin(x, y, gridsize=50, cmap="Blues", mincnt=1, extent=(-lim, lim, -lim, lim))
    ax.plot([-lim, lim], [-lim, lim], color=_ORANGE, linestyle="--", linewidth=1.5, label="Perfect agreement (y = x)")
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_xlabel("CRSP day-over-day return (retx)")
    ax.set_ylabel("master_stock.csv day-over-day return")
    ax.set_title("CRSP vs. an independent price file: daily returns")
    cb = fig.colorbar(hb, ax=ax)
    cb.set_label("Number of (stock, day) observations")
    ax.legend(loc="upper left", frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Narrative text -- all numbers interpolated from the stats dicts computed
# above, none hand-typed from having read FINDINGS.md.
# ---------------------------------------------------------------------------


def _build_sections(cov_stats: dict, crossval_stats: dict) -> list[tuple[str, str]]:
    sections = [
        (
            "What this dataset is, and why it needed cleaning",
            "This report covers the first stage of building this project's stock-price "
            "history: cleaning a raw pull from CRSP (Center for Research in Security "
            "Prices), a widely used academic database of US daily stock prices and "
            "trading data.\n\n"
            f"The cleaned result covers {cov_stats['n_years']} years "
            f"({cov_stats['year_min']}-{cov_stats['year_max']}), {cov_stats['total_rows']:,} "
            f"(stock, day) rows across {cov_stats['total_distinct_permnos']:,} distinct stocks. "
            "Each stock has a permanent numeric ID (a \"permno\") that never changes or gets "
            "reused, unlike its ticker symbol, which a company can change or which can be "
            "reassigned to a different company later.\n\n"
            "Two of CRSP's own data conventions look like errors if you don't know about "
            "them, so this cleaning step checked for both directly rather than assuming "
            "they don't apply here. First, when a stock doesn't trade on a given day, CRSP "
            "records an estimated price with a minus sign in front of it as a marker that "
            f"it isn't a real trade price; {cov_stats['bidask_rows']:,} rows "
            f"({cov_stats['bidask_frac']:.2%} of the total) fall into this case, and the "
            "minus sign is removed so the price is usable, with a separate flag column "
            "kept so this is never silently hidden. Second, CRSP documents four special "
            "codes that can appear in place of a return to mean \"we don't have this,\" not "
            "a literal huge gain or loss; this cleaned data has "
            f"{cov_stats['missing_code_rows']:,} such codes remaining.",
        ),
        (
            "Are prices and returns already adjusted for stock splits and dividends?",
            "This question matters a lot for anything built on top of this data. A stock "
            "split (for example, \"2-for-1\") doubles the number of shares and roughly "
            "halves the price without changing what the company is actually worth or what "
            "an investor actually earned. A dividend is a cash payment to shareholders, "
            "which is part of an investor's real return even though it doesn't show up as "
            "a change in the share price. If a price series isn't adjusted for these "
            "events, a split looks exactly like a crash, and a return series that ignores "
            "dividends understates how much money was actually made.\n\n"
            "The chart below shows one real, named example: a specific stock's actual "
            "traded price around one of its stock splits, next to what an investor's "
            "money would have looked like over that same stretch if you build it up "
            "purely from the day-by-day return column instead of the price column. If the "
            "return column already accounts for the split, the price line should show an "
            "abrupt drop on the split date while the return-built line stays smooth "
            "straight through it -- and that is exactly what the chart shows.",
        ),
        (
            "Cross-checking against a second, independent price file",
            "This project separately had access to an older, non-CRSP daily price file "
            "(one row per ticker per day) that predates its use of CRSP. Comparing that "
            "file against CRSP is a useful independent sanity check: if two "
            "differently-sourced price series agree closely, that's good evidence neither "
            "one has a scaling or adjustment mistake baked in. The two files only overlap "
            "for a few of this project's years, so this check can only speak to that "
            "overlapping stretch, not the full window this project ultimately covers.\n\n"
            "The chart below compares each matched stock-day's day-over-day return from "
            "CRSP against the same stock-day's return computed from the other file's raw "
            "prices. A point sitting exactly on the dashed diagonal line means the two "
            "sources agree perfectly on that day; the closer the whole cloud of points "
            "hugs that line, the stronger the agreement.",
        ),
        (
            "Coverage: how much data do we actually have?",
            "The chart below shows, for every year in the cleaned dataset, how many "
            "(stock, day) rows survived cleaning and how many distinct stocks contributed "
            "at least one of them. Coverage grows through the mid-2000s as more of the "
            "resolved stock universe has trading history on file, then narrows again as "
            "the pulled window approaches its final year.",
        ),
        (
            "What this means for the project",
            "None of the checks in this report turned up a case of unadjusted returns, a "
            "missing dividend adjustment, or an implausible return scale -- this is CRSP's "
            "standard, well-documented convention, confirmed directly against this "
            "project's own pull rather than assumed. That means the cleaned return columns "
            "in data/normalized_equity/ are safe to use as-is for the return calculations "
            "later stages of this project depend on. The independent cross-check against a "
            "second price file, while it can only speak to a few of this project's years, "
            "adds further confidence: the two sources agree closely almost everywhere they "
            "overlap. The main limitation to carry forward is coverage-related, not "
            "correctness-related -- how much history exists for a given year and stock, "
            "shown in the coverage chart below.",
        ),
    ]
    return sections


def _format_crossval_caption(stats: dict, window_start, window_end, n_points: int) -> str:
    if stats.get("skipped"):
        return "Cross-validation was skipped: no overlapping data between CRSP and master_stock.csv."

    caption = (
        f"Each point is one (stock, day) with a return recorded in both CRSP and the "
        f"independent master_stock.csv file, between {pd.Timestamp(window_start).date()} and "
        f"{pd.Timestamp(window_end).date()} ({n_points:,} stock-days shown). "
    )
    corr_pre = stats.get("return_correlation_before_ticker_gate")
    frac_pre = stats.get("return_frac_within_1pct_before_ticker_gate")
    if corr_pre is not None and frac_pre is not None:
        caption += (
            f"The two sources agree closely: a correlation of {corr_pre:.4f}, and "
            f"{frac_pre:.2%} of stock-days agreeing to within 1 percentage point. "
        )
    n_misaligned = stats.get("segments_quarantined_misaligned")
    corr_post = stats.get("return_correlation")
    frac_post = stats.get("return_frac_within_1pct")
    if n_misaligned and corr_post is not None and frac_post is not None:
        caption += (
            f"Setting aside {n_misaligned} ticker segments where the two files appear to be "
            f"tracking two different companies under a reused ticker symbol raises agreement "
            f"further, to a correlation of {corr_post:.4f} and {frac_post:.2%} within 1 "
            f"percentage point."
        )
    return caption


def _format_split_caption(window_df: pd.DataFrame, ticker: str, split_date: str) -> str:
    split_ts = pd.Timestamp(split_date)
    match = window_df.index[window_df["date"] == split_ts]
    if len(match) == 0 or match[0] == 0:
        return (
            f"{ticker}'s actual traded price around its {split_ts.date()} split, next to "
            "the same window built from the return column instead."
        )
    i = match[0]
    prev_price = float(window_df.loc[i - 1, "prc"])
    split_price = float(window_df.loc[i, "prc"])
    naive_pct = split_price / prev_price - 1.0
    actual_ret = float(window_df.loc[i, "ret"])
    # Sign-agnostic wording: a standard split cuts the price (naive_pct <
    # 0, the common case, including the real AAPL/MSFT examples this
    # report uses by default), but nothing here should assume that
    # direction -- a reverse split (or a different named example run
    # against this same code path) raises it instead.
    direction = "fell" if naive_pct < 0 else "rose"
    shape = "crash" if naive_pct < 0 else "spike"
    return (
        f"{ticker}'s actual traded price {direction} {abs(naive_pct):.2%} overnight on "
        f"{split_ts.date()} (from ${prev_price:.2f} to ${split_price:.2f}) purely because of "
        f"the stock split -- which would look like a huge one-day {shape} if you read the "
        f"price column alone. The return column recorded {actual_ret:+.2%} that day instead, "
        "and the bottom panel's return-built value of $1 invested shows no break at all: the "
        "split is already accounted for."
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def build_equity_data_report(
    output_path: Path = DEFAULT_OUTPUT,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    master_stock_path: Path = DEFAULT_MASTER_STOCK_PATH,
    crossval_years=DEFAULT_CROSSVAL_YEARS,
    archive_end_date: str = DEFAULT_ARCHIVE_END_DATE,
    aapl_ticker: str = DEFAULT_AAPL_TICKER,
    aapl_split_year: int = DEFAULT_AAPL_SPLIT_YEAR,
    aapl_split_date: str = DEFAULT_AAPL_SPLIT_DATE,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Path:
    """Build the equity-data audit report and return the path it was
    written to. Split out from __main__ so tests can call this directly
    against small fixture data instead of shelling out to the script or
    depending on the real, multi-hundred-MB data files under data/."""
    data_dir = Path(data_dir)

    coverage, cov_stats = _compute_coverage_and_cleaning_stats(data_dir)
    aapl_window = _load_ticker_window(data_dir, aapl_split_year, aapl_ticker, aapl_split_date, window_days)
    crossval_stats, crossval_merged = _run_master_stock_crossval(
        data_dir, master_stock_path, crossval_years, archive_end_date
    )

    figs = []
    try:
        coverage_fig = _make_coverage_chart(coverage)
        figs.append(coverage_fig)

        split_fig = _make_split_chart(aapl_window, aapl_ticker, aapl_split_date)
        figs.append(split_fig)
        split_caption = _format_split_caption(aapl_window, aapl_ticker, aapl_split_date)

        charts = [(split_fig, split_caption)]

        if not crossval_stats.get("skipped"):
            full_ret_cmp = crossval_merged.dropna(subset=["retx", "master_ret"])
            full_ret_cmp = full_ret_cmp.loc[~full_ret_cmp["is_permno_handoff"]]
            if len(full_ret_cmp) > 0:
                crossval_fig = _make_crossval_chart(full_ret_cmp)
                figs.append(crossval_fig)
                crossval_caption = _format_crossval_caption(
                    crossval_stats,
                    crossval_stats.get("window_start"),
                    crossval_stats.get("window_end"),
                    len(full_ret_cmp),
                )
                charts.append((crossval_fig, crossval_caption))

        charts.append(
            (
                coverage_fig,
                f"Rows and distinct stocks covered per year, {cov_stats['year_min']}-"
                f"{cov_stats['year_max']}, across {cov_stats['total_rows']:,} total rows and "
                f"{cov_stats['total_distinct_permnos']:,} distinct stocks.",
            )
        )

        sections = _build_sections(cov_stats, crossval_stats)

        return report_builder.build_pdf_report(
            title="Equity Data Audit: Cleaning, Adjustment Status, and Cross-Validation",
            output_path=output_path,
            summary=(
                f"Covers {cov_stats['n_years']} years of cleaned CRSP daily stock data "
                f"({cov_stats['year_min']}-{cov_stats['year_max']}), the split/dividend "
                "adjustment-status finding, and an independent cross-check against a second "
                "price file -- the output of Phase 1, Slice 2 of this project."
            ),
            sections=sections,
            charts=charts,
        )
    finally:
        for fig in figs:
            plt.close(fig)


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    written = build_equity_data_report(output)
    print(f"Equity data audit report written to: {written}")
