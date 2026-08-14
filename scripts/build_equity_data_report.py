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

import matplotlib.colors
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
    down to permno/date/prc/ret/retx/cfacpr/prc_is_bidask_avg -- not the
    full 11-column frame) to get the per-year coverage numbers (for the
    coverage chart), the whole-window cleaning-evidence numbers (for the
    intro section), and the same split/dividend/return-scale adjustment-
    status evidence normalize_equity_data.py's own main() computes for
    FINDINGS.md -- reusing its exact per-year event-detection/carryover
    pattern (detect_adjustment_events/update_event_carryover/safe_concat)
    and its classify_adjustment_status verdict, rather than re-deriving a
    second, potentially-drifting version of that check here. Single pass,
    single parquet read per year -- the event-detection and prc/cfacpr
    look-ahead-risk bookkeeping ride along inside the same per-year loop
    that already reads coverage/cleaning numbers, not a second read.
    """
    years = _discover_equity_years(data_dir)
    if not years:
        raise FileNotFoundError(f"No equity_<year>.parquet files found under {data_dir}")

    cols = ["permno", "date", "prc", "ret", "retx", "cfacpr", "prc_is_bidask_avg"]

    rows_by_year = []
    permnos_by_year = []
    date_max_by_year = []
    permno_union: set = set()
    total_rows = 0
    bidask_rows = 0
    prc_rows = 0
    missing_code_rows = 0
    ret_nonnull_rows = 0
    ret_and_retx_nonnull_rows = 0
    ret_ne_retx_rows = 0
    ret_lt_retx_rows = 0
    ret_abs_gt_big_move_rows = 0
    # Round-2 fix 8b: `cfacpr` is already in this loop's column projection
    # (needed for first_year_prc_cfacpr/prc_adj_stats below), so this is
    # one more accumulator riding along the same per-year read, not a new
    # data pass -- rows where cfacpr is exactly 0 make `prc / cfacpr`
    # undefined (not just look-ahead-tainted), a real, disclosed caveat on
    # top of the look-ahead one.
    cfacpr_zero_rows = 0

    event_frames = []
    event_carryover = None  # see normalize_equity_data.last_row_per_permno's docstring
    first_year_prc_cfacpr = None  # captured on years[0] only, for the prc_adj look-ahead illustration

    for year in years:
        path = Path(data_dir) / f"equity_{year}.parquet"
        df = pd.read_parquet(path, columns=cols)

        rows_by_year.append(len(df))
        year_permnos = df["permno"].unique()
        permnos_by_year.append(int(len(year_permnos)))
        permno_union.update(year_permnos.tolist())
        date_max_by_year.append(df["date"].max())
        total_rows += len(df)
        bidask_rows += int(df["prc_is_bidask_avg"].sum())
        prc_rows += int(df["prc"].notna().sum())
        # Both ret and retx checked (Fix 12c): retx carries the same legacy
        # sentinel-code risk ret does, and retx is now in this projection
        # too.
        missing_code_rows += int(
            (df["ret"].isin(ned.CRSP_MISSING_RETURN_CODES) | df["retx"].isin(ned.CRSP_MISSING_RETURN_CODES)).sum()
        )

        # Pull-wide dividend/return-scale evidence for classify_adjustment_
        # status -- computed exactly as normalize_equity_data.clean_dsf_year
        # does (same masks, same constants), just accumulated across years
        # here instead of within one year's cleaning pass.
        ret_nonnull = df["ret"].notna()
        both_nonnull = ret_nonnull & df["retx"].notna()
        ret_minus_retx = df["ret"] - df["retx"]
        ret_nonnull_rows += int(ret_nonnull.sum())
        ret_and_retx_nonnull_rows += int(both_nonnull.sum())
        ret_ne_retx_rows += int((both_nonnull & (ret_minus_retx.abs() > ned.DIVIDEND_EVIDENCE_MIN_DIFF)).sum())
        ret_lt_retx_rows += int((both_nonnull & (-ret_minus_retx > ned.DIVIDEND_EVIDENCE_MIN_DIFF)).sum())
        ret_abs_gt_big_move_rows += int((ret_nonnull & (df["ret"].abs() > ned.NAIVE_RET_BIG_MOVE_THRESHOLD)).sum())
        cfacpr_zero_rows += int((df["cfacpr"] == 0).sum())

        if year == years[0]:
            first_year_prc_cfacpr = df[["permno", "prc", "cfacpr"]].copy()

        # Adjustment-event detection, one year at a time, with cross-year
        # carryover -- mirrors normalize_equity_data.main()'s own loop
        # exactly (see its comments for why this is equivalent to running
        # detect_adjustment_events once over the whole multi-year panel
        # without ever holding it all in memory at once).
        year_event_cols = df[ned.EVENT_CHECK_COLUMNS]
        combined = (
            year_event_cols
            if event_carryover is None
            else ned.safe_concat([event_carryover, year_event_cols], ignore_index=True)
        )
        event_frames.append(ned.detect_adjustment_events(combined))
        event_carryover = ned.update_event_carryover(event_carryover, year_event_cols)

    coverage = pd.DataFrame({
        "year": years,
        "rows": rows_by_year,
        "permnos": permnos_by_year,
        "date_max": date_max_by_year,
    })

    # The five counters classify_adjustment_status's dsf_stats argument
    # needs -- also exposed flat on `stats` below (existing consumers of
    # this dict are unaffected; nothing existing is removed).
    dsf_like_stats = {
        "ret_nonnull_rows": ret_nonnull_rows,
        "ret_and_retx_nonnull_rows": ret_and_retx_nonnull_rows,
        "ret_ne_retx_rows": ret_ne_retx_rows,
        "ret_lt_retx_rows": ret_lt_retx_rows,
        "ret_abs_gt_big_move_rows": ret_abs_gt_big_move_rows,
    }
    event_stats = ned.summarize_adjustment_events(ned.safe_concat(event_frames, ignore_index=True))
    # event_carryover now holds each permno's single most-recently-seen row
    # across the whole pull -- exactly what's needed as "this permno's
    # cfacpr at the end of the window this report covers."
    prc_adj_stats = ned.summarize_prc_adj_lookahead_risk(first_year_prc_cfacpr, event_carryover[["permno", "cfacpr"]])
    verdict = ned.classify_adjustment_status(event_stats, dsf_like_stats)

    stats = {
        "n_years": len(years),
        "year_min": years[0],
        "year_max": years[-1],
        "total_rows": total_rows,
        "total_distinct_permnos": len(permno_union),
        "bidask_rows": bidask_rows,
        "prc_rows": prc_rows,
        # Fix 12d: denominator is prc_rows (rows with a price at all), not
        # total_rows -- comparable to FINDINGS.md's own framing of this
        # same statistic.
        "bidask_frac": (bidask_rows / prc_rows) if prc_rows else 0.0,
        "missing_code_rows": missing_code_rows,
        **dsf_like_stats,
        "event_stats": event_stats,
        "prc_adj_stats": prc_adj_stats,
        "cfacpr_zero_rows": cfacpr_zero_rows,
        "verdict": verdict,
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

    Fix 12b: also loads `permno` and resolves to a single one if the
    ticker+date window happens to straddle a ticker reassignment (a ticker
    symbol can be reassigned to a different company over time -- this
    report's own intro text makes exactly this point to the reader --
    while a permno, CRSP's permanent per-company ID, never is). Without
    this, a window spanning a reassignment could silently plot two
    unrelated companies' prices/returns as one continuous series.

    Known, accepted limitation (round-2 review, declined as out of scope
    for a final mechanical fix pass): this function only ever reads a
    single year's equity_<year>.parquet file. A `center_date` near a
    calendar-year boundary (early January or late December) can therefore
    get a one-sided window -- e.g. a window that should extend 15 days
    into the prior December instead starts at January 1st, because the
    prior year's rows live in a different file this function never opens.
    This produces no wrong number: _format_split_caption's numeric branch
    already requires a same-year row immediately before the split date to
    report a computed price move at all, and gracefully falls back to its
    vaguer, number-free wording when that row isn't present in the window
    -- so a one-sided window degrades the caption's specificity, not its
    correctness. Fixing this properly would mean reading two adjacent
    years' files whenever a window straddles a boundary, a real design
    change out of scope here.
    """
    path = Path(data_dir) / f"equity_{year}.parquet"
    df = pd.read_parquet(path, columns=["date", "ticker", "permno", "prc", "ret", "retx"])
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

    distinct_permnos = df["permno"].dropna().unique()
    if len(distinct_permnos) > 1:
        # Prefer the permno on the row whose date exactly equals the
        # window's own center date, when such a row exists (the center
        # date is the one this window was explicitly built to examine);
        # otherwise raise ValueError if multiple permnos share the ticker
        # and the center_date row is missing (cannot disambiguate).
        center_rows = df.loc[df["date"] == center]
        if len(center_rows) > 0:
            # Also raise if the center_date row itself has multiple permnos --
            # sweep the same silent-misattribution shape as the missing-row case.
            if center_rows["permno"].nunique() > 1:
                unique_permnos = sorted(center_rows["permno"].unique().tolist())
                raise ValueError(
                    f"_load_ticker_window: ticker {ticker!r} has multiple permnos "
                    f"({unique_permnos}) on center_date {center_date}; cannot disambiguate"
                )
            chosen = center_rows["permno"].iloc[0]
        else:
            unique_permnos = sorted(distinct_permnos.tolist())
            raise ValueError(
                f"_load_ticker_window: ticker {ticker!r} has multiple permnos in window "
                f"({unique_permnos}) and no row on center_date {center_date}; cannot disambiguate"
            )
        df = df.loc[df["permno"] == chosen].reset_index(drop=True)
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
) -> tuple[dict, pd.DataFrame, dict]:
    """Reuses normalize_equity_data.py's own already-reviewed
    read_master_stock_raw / clean_master_stock_frame / cross_validate_
    master_stock functions (see module docstring for why) to reproduce
    Slice 2's master_stock.csv cross-validation. Returns (stats, merged,
    master_clean_stats) -- merged is empty, stats is {"skipped": True},
    and master_clean_stats is whatever clean_master_stock_frame actually
    returned (still real, even on the skip path -- see Fix 9) if either
    side has no usable rows in the crossval years, matching that
    function's own guard in normalize_equity_data.main().

    master_clean_stats (Fix 9) is master_stock.csv's own cleaning-stage
    stats -- previously computed here and then discarded, leaving no way
    for the report to disclose the second file's own data-quality problems
    (blank/infinite/negative prices, etc.) before comparing it to CRSP.
    """
    crsp_crossval = _load_crossval_crsp_frame(data_dir, crossval_years)
    master_raw = ned.read_master_stock_raw(master_stock_path, end_date=archive_end_date)
    master_clean, master_clean_stats = ned.clean_master_stock_frame(master_raw, end_date=archive_end_date)

    if crsp_crossval.empty or master_clean.empty:
        return {"skipped": True}, pd.DataFrame(), master_clean_stats

    window_start = max(pd.Timestamp(master_clean["date"].min()), pd.Timestamp(crsp_crossval["date"].min()))
    window_end = min(pd.Timestamp(archive_end_date), pd.Timestamp(crsp_crossval["date"].max()))
    crossval_stats, crossval_merged = ned.cross_validate_master_stock(
        crsp_crossval, master_clean, window_start, window_end
    )
    return crossval_stats, crossval_merged, master_clean_stats


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def _is_partial_final_year(date_max, year: int, slack_days: int = 7) -> bool:
    """Round-2 fix 6: single-sourced "is this year partial" check, used by
    both _make_coverage_chart (the annotation) and _build_coverage_section
    (the prose) so the two can't disagree. `date_max` is that year's
    actual last observed date; a straight `date_max == December 31st`
    comparison breaks for a genuinely complete year whose real last
    trading day isn't Dec 31 (a weekend/holiday) -- verified live on the
    real data: 2011's last trading day is 2011-12-30, a fully complete
    year only 1 day short of Dec 31. `slack_days` (default one calendar
    week) absorbs that without also absorbing a real truncation -- the
    real data's actual partial final year, 2014, ends 2014-06-30, nowhere
    close to Dec 31 either way."""
    # dt.timedelta rather than pd.Timedelta: under this project's pinned
    # pandas/numpy versions, pd.Timedelta(days=int) on its own triggers a
    # spurious "generic unit" DeprecationWarning from NumPy's timedelta64
    # handling (see _load_ticker_window's identical comment above); a
    # plain stdlib timedelta sidesteps it entirely.
    year_end = pd.Timestamp(year=year, month=12, day=31)
    return pd.Timestamp(date_max) < year_end - dt.timedelta(days=slack_days)


def _make_coverage_chart(coverage: pd.DataFrame) -> plt.Figure:
    fig, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True)

    years_list = [int(y) for y in coverage["year"]]
    # Fix 11: always include the LAST year's tick, not just every-other-
    # year starting from the first -- the original years[::2] logic could
    # (and, depending on parity, does) drop the final bar's own label
    # entirely, which is exactly the bar most likely to need a caveat
    # (see the partial-year annotation below).
    ticks = sorted(set(years_list[::2]) | {years_list[-1]})

    axes[0].bar(coverage["year"], coverage["rows"] / 1000.0, color=_BLUE)
    axes[0].set_ylabel("Rows (thousands)")
    axes[0].set_title("Cleaned (permno, date) rows by year")

    axes[1].bar(coverage["year"], coverage["permnos"], color=_BLUE)
    axes[1].set_ylabel("Distinct stocks (permnos)")
    axes[1].set_xlabel("Year")
    axes[1].set_title("Distinct stocks covered by year")
    axes[1].set_xticks(ticks)
    plt.setp(axes[1].get_xticklabels(), rotation=45, ha="right")

    # Fix 11 / round-2 fix 5: if the final year's own last observed date
    # isn't December 31st (checked with several days' slack -- see
    # _is_partial_final_year -- so a weekend-adjacent complete year isn't
    # misflagged), its bar covers only part of a year and isn't directly
    # comparable to the full-year bars beside it -- annotate that
    # concretely rather than leaving a reader to assume every bar is a
    # full year.
    final_year = years_list[-1]
    final_date_max = pd.Timestamp(coverage["date_max"].iloc[-1])
    if _is_partial_final_year(final_date_max, final_year):
        # The row-count data across years is essentially flat -- there is
        # no genuinely empty region to anchor into, so this needs real
        # headroom, not just a corner of the existing axes: widen the
        # y-axis well above the tallest bar first, then anchor the label
        # in axes-fraction coordinates at the top-right corner (not a
        # data-coordinate x position, which pushed the box past the right
        # edge for a final year) with a thin connector back down to the
        # final bar's own top. Single-line text keeps the box small.
        top_rows_k = float(coverage["rows"].max()) / 1000.0
        axes[0].set_ylim(0, top_rows_k * 1.35)
        axes[0].annotate(
            f"Partial year: through {final_date_max.date()}",
            xy=(final_year, float(coverage["rows"].iloc[-1]) / 1000.0),
            xycoords=("data", "data"),
            xytext=(0.98, 0.98),
            textcoords="axes fraction",
            ha="right",
            va="top",
            fontsize=8,
            color=_MUTED,
            arrowprops=dict(arrowstyle="-", color=_MUTED, linewidth=0.8),
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=_GRID, alpha=0.9),
        )

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

    fig, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True)

    if len(df) == 0:
        # Guard against an empty window (Fix 12a) -- e.g. a ticker/date
        # combination with no rows in this year's file -- rather than
        # crashing on an empty series a moment later.
        for ax in axes:
            ax.set_title("No data in this window")
        fig.tight_layout()
        return fig

    # First plotted cumulative-return point is 1.0 (representing $1
    # invested pre-window) except when the split date IS the window's first
    # row, in which case the first plotted point already reflects that
    # day's return so the discontinuity is visually preserved.
    working_ret = df["ret"].copy()
    split_mask = df["date"] == split_ts
    # A NaN return on the split date itself is set to 0.0 so cumprod
    # continues past the split date, with a visual marker (see below)
    # showing where the missing observation was. Every OTHER missing
    # return in this small, hand-picked window is also filled with 0%
    # (a non-event), same as before.
    split_ret_is_nan = bool(split_mask.any() and df.loc[split_mask, "ret"].isna().any())
    if split_ret_is_nan:
        working_ret.loc[split_mask] = 0.0
    fill_mask = working_ret.isna() & ~split_mask
    working_ret.loc[fill_mask] = 0.0
    # Only zero out the first row if the split date is not the first date.
    if split_ts != df["date"].iloc[0]:
        working_ret.iloc[0] = 0.0
    df["cum_return_index"] = (1.0 + working_ret).cumprod()

    axes[0].plot(df["date"], df["prc"], color=_BLUE, linewidth=2, marker="o", markersize=3)
    axes[0].axvline(split_ts, color=_MUTED, linestyle="--", linewidth=1)
    axes[0].set_ylabel("Traded price ($)")
    axes[0].set_title(f"{ticker}: actual traded price around its {split_ts.date()} split")

    axes[1].plot(df["date"], df["cum_return_index"], color=_BLUE, linewidth=2, marker="o", markersize=3)
    axes[1].axvline(split_ts, color=_MUTED, linestyle="--", linewidth=1)
    if split_ret_is_nan:
        # Visual marker for the missing return observation at the split date
        axes[1].axvline(split_ts, color=_ORANGE, linestyle=":", alpha=0.5)
    axes[1].set_ylabel("Value of $1 invested\n(built from the return column)")
    axes[1].set_xlabel("Date")
    title = "Same window, built from the return column instead of price"
    if split_ret_is_nan:
        title += " (gap: no return recorded for the split date itself)"
    axes[1].set_title(title)
    plt.setp(axes[1].get_xticklabels(), rotation=45, ha="right")

    for ax in axes:
        ax.grid(axis="y", color=_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return fig


def _make_crossval_chart(full_ret_cmp: pd.DataFrame) -> tuple[plt.Figure, int, int]:
    """Hexbin (not a scatter -- this is well over a million points) of
    CRSP's day-over-day return against master_stock.csv's own day-over-day
    return, for every matched (ticker, date) pair with both sides present,
    on a retained (post-alignment-gate) segment, and not sitting on a
    ticker-reassignment handoff day. A single sequential blue ramp, on a
    LOG color scale (Fix 7 -- a linear scale makes almost every hexagon
    look like the same faint color once a handful of bins near the origin
    dominate the count, hiding the density structure everywhere else),
    encodes point density; the dashed line marks perfect agreement.

    Returns (figure, n_plotted, n_excluded): n_plotted is how many of
    full_ret_cmp's rows actually got binned/plotted by matplotlib's own
    hexbin call -- read back from the returned collection's per-hexagon
    counts (Round-2 fix 7), not from a separate `|x| <= lim & |y| <= lim`
    square-boundary inequality test. That separate test undercounts: a
    hexagon's own boundary extends slightly past the nominal `extent`
    passed below, so some near-boundary points the square test calls
    "excluded" are, in fact, plotted (verified: a couple hundred rows'
    worth on the real data). n_excluded is `len(full_ret_cmp) - n_plotted`,
    i.e. how many of the largest disagreements are genuinely invisible on
    the chart -- the caption must report both rather than implying every
    row is shown.
    """
    x = full_ret_cmp["retx"].to_numpy(dtype="float64")
    y = full_ret_cmp["master_ret"].to_numpy(dtype="float64")

    lim = float(np.nanpercentile(np.abs(np.concatenate([x, y])), 99.5))
    lim = max(lim, 1e-4)  # guard against a degenerate all-zero fixture in tests

    fig, ax = plt.subplots(figsize=(6.5, 6))
    hb = ax.hexbin(
        x, y, gridsize=50, cmap="Blues", mincnt=1,
        norm=matplotlib.colors.LogNorm(),
        extent=(-lim, lim, -lim, lim),
    )
    # What matplotlib actually drew, not a geometric range test -- see the
    # docstring above.
    n_plotted = int(hb.get_array().sum())
    n_excluded = int(len(x) - n_plotted)
    ax.plot([-lim, lim], [-lim, lim], color=_ORANGE, linestyle="--", linewidth=1.5, label="Perfect agreement (y = x)")
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_xlabel("CRSP day-over-day return (retx)")
    ax.set_ylabel("master_stock.csv day-over-day return")
    ax.set_title("CRSP vs. a second price file: daily returns")
    cb = fig.colorbar(hb, ax=ax)
    cb.set_label("Number of (stock, day) observations")
    ax.legend(loc="upper left", frameon=False)
    if n_excluded > 0:
        ax.annotate(
            f"{n_excluded:,} points outside\nthis plotted range",
            xy=(0.98, 0.02),
            xycoords="axes fraction",
            ha="right",
            va="bottom",
            fontsize=8,
            color=_MUTED,
        )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return fig, n_plotted, n_excluded


# ---------------------------------------------------------------------------
# Narrative text -- all numbers interpolated from the stats dicts computed
# above, none hand-typed from having read FINDINGS.md.
# ---------------------------------------------------------------------------


def _pct_or_na(numerator, denominator) -> str:
    """Formats numerator/denominator as a percentage, or "n/a" against a
    zero (or missing) denominator -- narrative text below reads real
    numbers straight out of stats dicts that can legitimately be all-zero
    against a tiny synthetic test fixture."""
    if not denominator:
        return "n/a"
    return f"{numerator / denominator:.2%}"


def _crossval_agreement_caveat_applies(crossval_stats: dict) -> bool:
    """Fix 3: single-sourced so the crossval section's caveat paragraph and
    the closing section's "though see the caveat above" back-reference
    can't drift apart -- both call this instead of each computing (or, in
    the closing section's former case, silently assuming) the same
    condition independently. True when the two files' median return
    difference is small enough to be worth flagging as suspiciously close
    (see ned.CROSSVAL_SUSPICIOUSLY_CLOSE_MEDIAN_DIFF's own docstring)."""
    median_diff = crossval_stats.get("return_median_abs_diff")
    return median_diff is not None and median_diff < ned.CROSSVAL_SUSPICIOUSLY_CLOSE_MEDIAN_DIFF


def _build_sections(
    cov_stats: dict,
    coverage: pd.DataFrame,
    crossval_stats: dict,
    master_clean_stats: dict,
    crossval_has_comparison: bool,
    chart_numbers: dict,
) -> list[tuple[str, str]]:
    """chart_numbers: {"split": int, "crossval": int | None, "coverage":
    int} -- the actual 1-based position of each chart in the `charts` list
    passed to report_builder.build_pdf_report (Fix 12e: report_builder.py
    itself is out of scope, so every "Chart N" reference below is
    generated here from this dict rather than a hardcoded "the chart
    below," which would misdescribe a chart's position once section text
    and chart blocks are laid out on separate pages)."""
    verdict = cov_stats["verdict"]
    event_stats = cov_stats["event_stats"]
    threshold = ned.NAIVE_RET_BIG_MOVE_THRESHOLD

    n_big = event_stats.get("n_events_with_big_naive_move", 0)
    frac_matches_naive = event_stats.get("frac_big_naive_events_ret_equals_naive")
    n_both = cov_stats["ret_and_retx_nonnull_rows"]
    n_ne = cov_stats["ret_ne_retx_rows"]
    n_wrong_dir = cov_stats["ret_lt_retx_rows"]
    n_ret_nonnull = cov_stats["ret_nonnull_rows"]
    n_big_ret = cov_stats["ret_abs_gt_big_move_rows"]

    prc_adj = cov_stats["prc_adj_stats"]
    n_ne1 = prc_adj.get("n_permnos_cfacpr_ne_1_at_window_end", 0)
    n_total_permnos = prc_adj.get("n_permnos_total", 0)
    wrongly_excluded = prc_adj.get("wrongly_excluded_rows", 0)
    wrongly_included = prc_adj.get("wrongly_included_rows", 0)
    permnos_affected = prc_adj.get("permnos_affected", 0)
    price_threshold = prc_adj.get("price_threshold", 5.0)
    n_cfacpr_zero = cov_stats.get("cfacpr_zero_rows", 0)

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
            f"({_pct_or_na(cov_stats['bidask_rows'], cov_stats['prc_rows'])} of rows that have "
            "a price at all) fall into this case, and the minus sign is removed so the price "
            "is usable, with a separate flag column kept so this is never silently hidden. "
            "Second, CRSP documents four special codes that can appear in place of a return, "
            "in either the `ret` or `retx` column, to mean \"we don't have this,\" not a "
            f"literal huge gain or loss; this cleaned data has {cov_stats['missing_code_rows']:,} "
            "rows with one of those codes remaining in either column.",
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
            "Three things are checked directly against this pull's own numbers, across "
            "every year in the dataset -- not assumed, and not standing in for each other. "
            f"Splits and distributions: {n_big:,} adjustment events had a raw price move "
            f"bigger than {threshold:.0%} (consistent with a real split or distribution, "
            "not day-to-day noise); on "
            + (f"{frac_matches_naive:.2%}" if frac_matches_naive is not None else "an unmeasured share")
            + " of those events, CRSP's actual reported return still matched that same raw, "
            "unadjusted move -- a fully unadjusted return column would measure close to "
            f"100% here. Dividends: of {n_both:,} rows with both `ret` and `retx` "
            f"populated, they disagree on {n_ne:,} ({_pct_or_na(n_ne, n_both)}) -- consistent "
            f"with real, sporadic dividend payments, not a mislabeled column -- and only "
            f"{n_wrong_dir:,} of those disagreements run in the direction a real dividend (a "
            f"cash inflow) essentially never should. Return scale: across {n_ret_nonnull:,} "
            f"non-null `ret` values, only {n_big_ret:,} ({_pct_or_na(n_big_ret, n_ret_nonnull)}) "
            f"exceed {threshold:.0%} in size -- a small share is what a correctly-scaled "
            "fractional daily return column (e.g. 0.05 meaning 5%) should look like; a "
            "column mistakenly left in percent units (e.g. 5 meaning 5%) would instead put "
            "nearly all of its values above that same threshold.\n\n"
            f"Chart {chart_numbers['split']} shows one specific, named example ON TOP OF "
            "these population-level figures, not the basis for the verdict above: a "
            "specific stock's actual traded price around one of its stock splits, next to "
            "what an investor's money would have looked like over that same stretch if you "
            "build it up purely from the day-by-day return column instead of the price "
            "column. If the return column already accounts for the split, the price line "
            "should show an abrupt, split-sized drop on the split date while the return-built "
            "line shows no such split-sized break -- just the ordinary day's return, the same "
            f"size as any other day's -- and that is exactly what Chart {chart_numbers['split']} "
            "shows.",
        ),
        (
            "Why this data has no split-adjusted price column",
            "CRSP also provides `cfacpr`, a cumulative split-adjustment factor that could, "
            "in principle, be divided into the raw price column (`prc`) to get a "
            "split-adjusted price level. This report deliberately does not do that, and "
            "does not provide such a column: `cfacpr` is cumulative to the LAST date in "
            "whatever file this project happened to pull, not to the end of this project's "
            "own sample window. Dividing an early row's price by that same `cfacpr` would "
            "silently bake in every split that happened between that row's date and the "
            "pull's own end date -- splits that hadn't happened yet, and couldn't have been "
            "known, at that earlier point in history. That is exactly the kind of "
            "look-ahead mistake this project has to avoid, and it holds regardless of the "
            "adjustment-status finding above: `cfacpr`'s own definition is the problem, not "
            "whether `ret` happens to already be adjusted. Plainly: `cfacpr` must not be "
            "used as a divisor.\n\n"
            f"Concretely: of {n_total_permnos:,} stocks with data anywhere in this window, "
            f"{n_ne1:,} end the window with `cfacpr` different from 1 -- meaning a future "
            "re-pull (picking up whatever splits happen between now and then) would "
            "silently change any derived adjusted price for rows that are supposed to be "
            f"permanent history. Applying a simple \"at least ${price_threshold:.0f}\" price "
            "screen to the first year's data: using the actual traded price versus using "
            f"`prc / cfacpr` for the same rows, {wrongly_excluded:,} rows would be wrongly "
            f"excluded and {wrongly_included:,} would be wrongly included -- "
            f"{permnos_affected:,} different stocks affected. One more real caveat on top of "
            f"the look-ahead one above: {n_cfacpr_zero:,} rows have `cfacpr` exactly equal to "
            "0, where `prc / cfacpr` is mathematically undefined (a division by zero), not "
            "just look-ahead-tainted -- another reason not to divide by it without guarding "
            "first. `prc` and `cfacpr` are both still in the output for anyone who explicitly "
            "wants `prc / cfacpr` with these caveats in view; for comparing returns across a "
            "split safely, use `ret`/`retx` instead (see the evidence above), which don't "
            "have this particular problem.",
        ),
        _build_crossval_section(crossval_stats, master_clean_stats, crossval_has_comparison, chart_numbers),
        _build_coverage_section(coverage, chart_numbers),
        _build_closing_section(verdict, crossval_has_comparison, crossval_stats, chart_numbers),
    ]
    return sections


def _build_crossval_section(
    crossval_stats: dict, master_clean_stats: dict, crossval_has_comparison: bool, chart_numbers: dict
) -> tuple[str, str]:
    """Fix 3: no "independent" -- reworded to "a second price file" /
    "a cross-check" throughout, plus the caveat that the two files agree
    to far more decimal places than genuinely independent vendors
    plausibly would. Fix 9: discloses the second file's own cleaning-stage
    data-quality problems (previously computed and discarded) and why the
    chart compares retx rather than ret. Fix 10/round-2 fix 3: when there's
    no actual comparison to report this build, this becomes a short
    paragraph saying so -- no chart reference, no agreement claim, none of
    Fix 9's content. Gated on `crossval_has_comparison` (whether
    `return_comparison_rows_retained` is actually present and positive in
    crossval_stats), NOT on whether the CRSP/master_stock.csv input frames
    were merely non-empty -- non-empty inputs that share zero (ticker,
    date) keys still produce no usable comparison, and must land on this
    same fallback rather than falsely claiming agreement beside no
    statistic at all."""
    heading = "Cross-checking against a second price file"
    if not crossval_has_comparison:
        return (
            heading,
            "The cross-check against a second price file did not run in this build: no "
            "overlapping rows were found between CRSP and master_stock.csv for the years "
            "this check covers, so there is nothing to report here this run.",
        )

    intro = (
        "This project separately had access to an older, non-CRSP daily price file (one "
        "row per ticker per day) that predates its use of CRSP. Comparing that file "
        "against CRSP is a useful cross-check: if two differently-sourced price series "
        "agree closely, that's good evidence neither one has a scaling or adjustment "
        "mistake baked in. The two files only overlap for a few of this project's years, "
        "so this check can only speak to that overlapping stretch, not the full window "
        "this project ultimately covers."
    )

    blank_dropped = master_clean_stats.get("blank_ticker_rows_dropped", 0)
    missing_price = master_clean_stats.get("missing_price_rows", 0)
    infinite_price = master_clean_stats.get("infinite_price_rows", 0)
    negative_price = master_clean_stats.get("negative_price_rows", 0)
    crsp_collisions = crossval_stats.get("crsp_ticker_date_collisions_dropped", 0)
    master_collisions = crossval_stats.get("master_ticker_date_collisions_dropped", 0)
    matched_tickers = crossval_stats.get("matched_distinct_tickers", 0)
    crsp_tickers_window = crossval_stats.get("crsp_distinct_tickers_in_window", 0)

    # Round-2 fix 4a: read_master_stock_raw's own SQL already filters
    # blank-ticker rows before clean_master_stock_frame (and this report)
    # ever sees them, so blank_dropped is structurally always 0 on real
    # data here -- narrating "after dropping 0 blank-ticker rows" every
    # run would describe a non-event as if it were a finding. Only
    # mention it when there's actually something to disclose.
    if blank_dropped:
        lead_in = (
            "The second file has real data-quality problems of its own, disclosed here "
            f"rather than glossed over: after dropping {blank_dropped:,} blank-ticker rows "
            "(a non-stock series that turned out to be appended to the file, not a "
            "per-ticker problem), it separately has"
        )
    else:
        lead_in = (
            "The second file has real data-quality problems of its own, disclosed here "
            "rather than glossed over: it has"
        )

    # Round-2 fix 4b: missing_price/infinite_price/negative_price are
    # counted only through this same windowed clean_master_stock_frame
    # call (bounded above by archive_end_date), not over the whole file --
    # data/normalized_equity/FINDINGS.md separately reports a whole-file
    # count for the same three fields (the file itself runs years past
    # this project's sample window), so leaving the date range unstated
    # would make it impossible for a reader to tell which count this is.
    quality_date_min = master_clean_stats.get("date_min")
    quality_date_max = master_clean_stats.get("date_max")
    if quality_date_min is not None and quality_date_max is not None:
        window_note = (
            f" (counted from {pd.Timestamp(quality_date_min).date()} to "
            f"{pd.Timestamp(quality_date_max).date()}, not the whole file, which runs "
            "years further)"
        )
    else:
        window_note = ""

    quality_para = (
        f"{lead_in} {missing_price:,} rows with a genuinely empty price "
        f"field, {infinite_price:,} with an infinite (`inf`/`-inf`) price, and "
        f"{negative_price:,} with a negative price with no documented convention behind it "
        f"(unlike CRSP's own bid/ask-average convention){window_note} -- all treated as "
        f"invalid and left blank rather than guessed at. Ticker-date price collisions (more "
        f"than one price recorded for the same ticker on the same day) drop a further "
        f"{master_collisions:,} rows on the second file's side and {crsp_collisions:,} rows "
        f"on CRSP's side. Only {_pct_or_na(matched_tickers, crsp_tickers_window)} of CRSP's "
        f"{crsp_tickers_window:,} in-window tickers found any match at all in the second "
        "file -- the second file only contains tickers that still existed when it was "
        "downloaded, so this whole check skews toward companies that survived, and says "
        "nothing about ones that didn't."
    )

    ret_vs_retx_para = (
        f"Chart {chart_numbers['crossval']}, below, compares `retx` (CRSP's price-only "
        "return) rather than `ret` (established above as the dividend-adjusted column): "
        "the second file's own prices show no sign of dividend reinvestment, so `retx` is "
        "the honest like-for-like comparison -- using `ret` instead would make the "
        "disagreement look much larger specifically on ex-dividend days"
    ) if chart_numbers.get("crossval") else (
        "This run compares `retx` (CRSP's price-only return) rather than `ret` "
        "(established above as the dividend-adjusted column): the second file's own "
        "prices show no sign of dividend reinvestment, so `retx` is the honest "
        "like-for-like comparison -- using `ret` instead would make the disagreement look "
        "much larger specifically on ex-dividend days"
    )
    n_ex_div = crossval_stats.get("n_ex_dividend_days")
    ex_div_ret_diff = crossval_stats.get("ex_dividend_median_abs_diff_ret")
    ex_div_retx_diff = crossval_stats.get("ex_dividend_median_abs_diff_retx")
    if n_ex_div and ex_div_ret_diff is not None and ex_div_retx_diff is not None:
        ret_vs_retx_para += (
            f" (on this run's {n_ex_div:,} ex-dividend days for retained segments, median "
            f"absolute difference against the second file is {ex_div_ret_diff:.2e} using `ret` "
            f"versus {ex_div_retx_diff:.2e} using `retx`)."
        )
    else:
        ret_vs_retx_para += "."

    caveat = ""
    if _crossval_agreement_caveat_applies(crossval_stats):
        caveat = (
            " Worth flagging: the two files agree to far more decimal places than two "
            "genuinely independent, unrelated vendors plausibly would on separate "
            "measurements of the same real-world price. That's better evidence that the "
            "two files apply the same underlying convention than that two unrelated "
            "measurements of reality agree -- so this should not be read as full "
            "independent confirmation."
        )

    body = intro + "\n\n" + quality_para + "\n\n" + ret_vs_retx_para + caveat
    if chart_numbers.get("crossval"):
        body += (
            f" A point sitting exactly on Chart {chart_numbers['crossval']}'s dashed "
            "diagonal line means the two sources agree perfectly on that day; the closer "
            "the whole cloud hugs that line, the stronger the agreement."
        )
    return heading, body


def _build_coverage_section(coverage: pd.DataFrame, chart_numbers: dict) -> tuple[str, str]:
    """Fix 11: every sentence traces to a specific computed value (peak
    year, row-count range, permno-count range, and -- only if the final
    year is partial -- a note about it) rather than a hand-written
    narrative shape claim ("grows... then narrows again") that isn't
    re-derived from the data on every run."""
    peak_idx = coverage["rows"].idxmax()
    peak_year = int(coverage.loc[peak_idx, "year"])
    peak_rows = int(coverage.loc[peak_idx, "rows"])
    min_rows = int(coverage["rows"].min())
    max_rows = int(coverage["rows"].max())
    min_permnos = int(coverage["permnos"].min())
    max_permnos = int(coverage["permnos"].max())
    final_year = int(coverage["year"].iloc[-1])
    final_date_max = pd.Timestamp(coverage["date_max"].iloc[-1])
    is_partial_final_year = _is_partial_final_year(final_date_max, final_year)

    body = (
        f"Chart {chart_numbers['coverage']} shows, for every year in the cleaned dataset, "
        "how many (stock, day) rows survived cleaning and how many distinct stocks "
        f"contributed at least one of them. {peak_year} has the most rows of any year, "
        f"{peak_rows:,}; across all years, rows per year range from {min_rows:,} to "
        f"{max_rows:,}, and distinct stocks per year range from {min_permnos:,} to "
        f"{max_permnos:,}."
    )
    if is_partial_final_year:
        body += (
            f" {final_year}'s bar only covers data through {final_date_max.date()}, not "
            "the full calendar year, so it isn't directly comparable to the full-year bars "
            "beside it."
        )
    return "Coverage: how much data do we actually have?", body


def _build_closing_section(
    verdict: dict, crossval_has_comparison: bool, crossval_stats: dict, chart_numbers: dict
) -> tuple[str, str]:
    """Fix 1: the "None of the checks..." sentence is only emitted when
    `verdict["overall"]` actually says "adjusted" -- otherwise this states
    verdict["reasons"] directly rather than a hardcoded alternate claim.
    Fix 10 / round-2 fix 3: the cross-check sentence is entirely omitted
    when there's no actual comparison to report (gated on the same
    `crossval_has_comparison` signal _build_crossval_section uses, not
    merely on whether the input frames were non-empty), and its "see the
    caveat above" back-reference only appears when
    _crossval_agreement_caveat_applies(crossval_stats) says that caveat
    was actually emitted in that section -- rather than assuming it always
    was, which is how this sentence and that section's caveat could
    previously drift apart. Fix 2: names the three real limitations
    (coverage, delisting returns, the cfacpr look-ahead trap) instead of
    the old "the main limitation is coverage-related" claim."""
    if verdict["overall"] == "adjusted":
        adjustment_clause = (
            "None of the checks in this report turned up a case of unadjusted returns, a "
            "missing dividend adjustment, or an implausible return scale -- this is CRSP's "
            "standard, well-documented convention, confirmed directly against this "
            "project's own pull rather than assumed. That means the cleaned return columns "
            "in data/normalized_equity/ are safe to use as-is for the return calculations "
            "later stages of this project depend on."
        )
    else:
        reasons = verdict.get("reasons") or []
        reasons_text = "; ".join(reasons) if reasons else "no further detail was recorded"
        adjustment_clause = (
            f"This run's checks did not cleanly confirm full split/dividend adjustment and "
            f"return-scale plausibility (overall status: {verdict['overall']}): "
            f"{reasons_text}. Any return calculation built on this data should be "
            "revisited in that light."
        )

    if crossval_has_comparison:
        caveat_back_reference = (
            " -- though see the caveat above about just how closely, which is itself "
            "informative about how independent the two files really are"
            if _crossval_agreement_caveat_applies(crossval_stats)
            else ""
        )
        crossval_clause = (
            " The cross-check against a second price file, while it can only speak to a "
            "few of this project's years, adds further confidence where it applies: the "
            "two sources agree closely almost everywhere they overlap"
            + caveat_back_reference
            + "."
        )
    else:
        crossval_clause = ""

    body = (
        adjustment_clause + crossval_clause + "\n\n"
        "Three real limitations carry forward from here, not one: coverage (which years "
        f"and stocks are actually present, shown in Chart {chart_numbers['coverage']}); "
        "delisting returns (a stock that gets delisted mid-study simply stops appearing "
        "in the cleaned data on its last trading day -- no final delisting return is "
        "captured, which is explicitly out of scope for this slice); and the `cfacpr` "
        "look-ahead trap described above (do not use `prc / cfacpr` as a divisor)."
    )
    return "What this means for the project", body


def _format_crossval_caption(stats: dict, window_start, window_end, n_plotted: int, n_excluded: int) -> str:
    """Caption for _make_crossval_chart's hexbin. Only ever called when
    the crossval actually ran and produced a non-empty chart (see Fix 10's
    crossval_ran signal in build_equity_data_report()), so this doesn't
    need its own "skipped" branch.

    Fix 12f: describes the actual binning (many observations grouped per
    hexagon, shaded by count), not "each point is one (stock, day)".
    Fix 7: states both the plotted count and how many of the largest
    disagreements fall outside the plotted range, rather than implying
    every row is shown. Fix 5: the post-gate correlation/agreement
    sentence is gated on those stats actually being present (not on
    whether the misaligned-segment count happens to be nonzero), and the
    two quarantine reasons are reported separately, each only when its
    own count is greater than zero.
    """
    # Guard against None/NaT window bounds
    if window_start is None or pd.isna(window_start) or window_end is None or pd.isna(window_end):
        date_range_phrase = "(window bounds unavailable)"
    else:
        date_range_phrase = f"between {pd.Timestamp(window_start).date()} and {pd.Timestamp(window_end).date()}"
    
    caption = (
        f"Each hexagon groups together many (stock, day) observations, with a return "
        f"recorded in both CRSP and the second price file, {date_range_phrase}, "
        f"colored by how many observations fall in it (darker/more intense = more) -- "
        f"{n_plotted:,} stock-days plotted"
    )
    caption += (
        f"; {n_excluded:,} of the largest disagreements fall outside this plotted range and "
        "aren't shown. "
        if n_excluded > 0
        else "; all of them fall within this plotted range. "
    )

    corr_pre = stats.get("return_correlation_before_ticker_gate")
    frac_pre = stats.get("return_frac_within_1pct_before_ticker_gate")
    if corr_pre is not None and frac_pre is not None:
        caption += (
            f"The two sources agree closely overall: a correlation of {corr_pre:.4f}, and "
            f"{frac_pre:.2%} of stock-days agreeing to within 1 percentage point. "
        )

    corr_post = stats.get("return_correlation")
    frac_post = stats.get("return_frac_within_1pct")
    if corr_post is not None and frac_post is not None:
        n_misaligned = stats.get("segments_quarantined_misaligned", 0)
        rows_misaligned = stats.get("rows_quarantined_misaligned", 0)
        n_too_few = stats.get("segments_quarantined_too_few_prices", 0)
        rows_too_few = stats.get("rows_quarantined_too_few_prices", 0)
        reason_bits = []
        if n_misaligned:
            reason_bits.append(
                f"{n_misaligned} segment(s) ({rows_misaligned:,} rows) where the two files "
                "appear to track different companies under a reused ticker symbol"
            )
        if n_too_few:
            reason_bits.append(
                f"{n_too_few} segment(s) ({rows_too_few:,} rows) with too few usable "
                "overlapping days to judge either way"
            )
        if reason_bits:
            caption += "Setting aside " + "; and ".join(reason_bits) + ", agreement rises "
        else:
            caption += "Among the retained segments, agreement is "
        caption += f"to a correlation of {corr_post:.4f} and {frac_post:.2%} within 1 percentage point."
    return caption


def _format_split_caption(window_df: pd.DataFrame, ticker: str, split_date: str) -> str:
    """Fix 6: states both numbers honestly (the price move AND the
    smaller-magnitude return the return column actually recorded) rather
    than framing the price move as happening "purely because of the stock
    split" in a way that reads as if there's nothing left to explain about
    the return column's own, different number. Sign-agnostic throughout
    (see `direction`/`shape` below) -- must read correctly for a reverse
    split (price rises) as well as an ordinary one (price falls). Fix 12a:
    also covers the case where the split date's own `ret` is missing
    rather than silently treating that as a normal small return."""
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
    # Guard against zero prev_price or near-zero naive_pct -- skip the
    # numeric branch and fall through to the numberless branch.
    if prev_price <= 0:
        return (
            f"{ticker}'s actual traded price around its {split_ts.date()} split, next to "
            "the same window built from the return column instead."
        )
    naive_pct = split_price / prev_price - 1.0
    if abs(naive_pct) < 1e-4:
        return (
            f"{ticker}'s actual traded price around its {split_ts.date()} split, next to "
            "the same window built from the return column instead."
        )
    actual_ret = window_df.loc[i, "ret"]
    # Sign-agnostic wording: a standard split cuts the price (naive_pct <
    # 0, the common case, including the real AAPL/MSFT examples this
    # report uses by default), but nothing here should assume that
    # direction -- a reverse split (or a different named example run
    # against this same code path) raises it instead.
    direction = "fell" if naive_pct < 0 else "rose"
    shape = "crash" if naive_pct < 0 else "spike"
    price_move = (
        f"{ticker}'s actual traded price {direction} {abs(naive_pct):.2%} overnight on "
        f"{split_ts.date()} (from ${prev_price:.2f} to ${split_price:.2f})"
    )
    if pd.isna(actual_ret):
        return (
            f"{price_move}. The return column has no recorded value for the split date "
            "itself, so the bottom panel's return-built line shows a genuine gap there rather "
            "than a smoothed-over guess. The stock split accounts for the price move: reading "
            f"the price column alone would look like a one-day {shape} that didn't really "
            "happen."
        )
    return (
        f"{price_move}, while the return column recorded {float(actual_ret):+.2%} that same "
        "day -- a much smaller-magnitude number reflecting the stock's real return, not the "
        "raw price change. The stock split accounts for the gap between the two: reading the "
        f"price column alone would look like a one-day {shape} that didn't really happen. The "
        "bottom panel's return-built value of $1 invested has no split-sized break in it -- it "
        f"still steps by that same ordinary {float(actual_ret):+.2%}, it just isn't distorted "
        "by the split itself the way the price panel is."
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
    crossval_stats, crossval_merged, master_clean_stats = _run_master_stock_crossval(
        data_dir, master_stock_path, crossval_years, archive_end_date
    )
    # Whether _run_master_stock_crossval even attempted a real comparison
    # this build -- False only on its {"skipped": True} short-circuit (no
    # usable rows in the configured crossval years on one side or the
    # other). Used purely to gate whether it's safe to index into
    # crossval_merged below; it does NOT by itself mean there's anything
    # to report -- see crossval_has_comparison just below for that.
    crossval_inputs_present = not crossval_stats.get("skipped", False)
    # Round-2 fix 3: whether the cross-check actually produced a usable
    # comparison this build, not merely whether its input frames were
    # non-empty -- non-empty CRSP and master_stock.csv frames that share
    # zero (ticker, date) keys still leave crossval_stats with no
    # `return_comparison_rows_retained` (and none of the mean/median-diff
    # or frac-within-1pct stats that key's presence guarantees; see
    # cross_validate_master_stock's early returns). Every crossval-
    # dependent narrative sentence below (Fix 9's disclosure paragraph,
    # the closing note's cross-check sentence) is gated on this signal,
    # not on crossval_inputs_present.
    crossval_has_comparison = crossval_stats.get("return_comparison_rows_retained", 0) > 0

    figs = []
    try:
        coverage_fig = _make_coverage_chart(coverage)
        figs.append(coverage_fig)

        split_fig = _make_split_chart(aapl_window, aapl_ticker, aapl_split_date)
        figs.append(split_fig)
        split_caption = "Chart 1. " + _format_split_caption(aapl_window, aapl_ticker, aapl_split_date)

        charts = [(split_fig, split_caption)]

        crossval_chart_num = None
        if crossval_inputs_present:
            # Fix 8: the plotted/compared set is the gated (ticker, permno)
            # comparison set cross_validate_master_stock's own pooled
            # statistics use -- is_retained_segment (additive column) and
            # not a handoff row -- rather than re-deriving that filter here
            # a second time.
            full_ret_cmp = crossval_merged.loc[
                crossval_merged["is_retained_segment"] & ~crossval_merged["is_permno_handoff"]
            ].dropna(subset=["retx", "master_ret"])
            if len(full_ret_cmp) > 0:
                crossval_chart_num = len(charts) + 1
                crossval_fig, n_plotted, n_excluded = _make_crossval_chart(full_ret_cmp)
                figs.append(crossval_fig)
                # n_plotted + n_excluded == len(full_ret_cmp), which in turn
                # matches crossval_stats["return_comparison_rows_retained"]
                # (both filtered through the same is_retained_segment /
                # ~is_permno_handoff gate) -- the caption's point counts
                # come from this same plotted frame, not a separately
                # tracked stats value, so the two can't drift apart.
                crossval_caption = f"Chart {crossval_chart_num}. " + _format_crossval_caption(
                    crossval_stats,
                    crossval_stats.get("window_start"),
                    crossval_stats.get("window_end"),
                    n_plotted,
                    n_excluded,
                )
                charts.append((crossval_fig, crossval_caption))

        coverage_chart_num = len(charts) + 1
        charts.append(
            (
                coverage_fig,
                f"Chart {coverage_chart_num}. Rows and distinct stocks covered per year, "
                f"{cov_stats['year_min']}-{cov_stats['year_max']}, across "
                f"{cov_stats['total_rows']:,} total rows and "
                f"{cov_stats['total_distinct_permnos']:,} distinct stocks.",
            )
        )

        # Fix 12e: computed from where each chart actually landed in this
        # `charts` list (report_builder.py itself is out of scope), not
        # hardcoded -- the coverage chart's own number depends on whether
        # the crossval chart was included above.
        chart_numbers = {"split": 1, "crossval": crossval_chart_num, "coverage": coverage_chart_num}

        sections = _build_sections(
            cov_stats, coverage, crossval_stats, master_clean_stats, crossval_has_comparison, chart_numbers
        )

        return report_builder.build_pdf_report(
            title="Equity Data Audit: Cleaning, Adjustment Status, and Cross-Validation",
            output_path=output_path,
            summary=(
                f"Covers {cov_stats['n_years']} years of cleaned CRSP daily stock data "
                f"({cov_stats['year_min']}-{cov_stats['year_max']}), the split/dividend "
                "adjustment-status finding, and a cross-check against a second price file "
                "-- the output of Phase 1, Slice 2 of this project."
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
