"""Cleans and normalizes the raw CRSP pull (data/raw_wrds/crsp/) into
data/normalized_equity/ -- Phase I, Slice 2 ("Equity data ingestion &
cleaning") of memory/phase1-equity-pead-plan.md.

Every date column in the raw WRDS pull landed as VARCHAR, not a real date
type (a known wrinkle of that pull -- see wrds_pull_lib.py's docstring).
This script's main job is turning that raw, string-dated, CRSP-convention-
laden pull into one row per (permno, date) with real dates, cleaned prices/
returns, and a documented split/dividend-adjustment finding, plus a market-
benchmark series derived from crsp.dsi. It also cross-validates against
data/raw_equity/master_stock.csv wherever the two sources actually overlap
(2011-2013 -- master_stock.csv doesn't start until 2011-01-03, well short of
this project's 1996-2013 archive window) as an independent, secondary check.

This is a **local, read-only-input batch transform** -- no WRDS connection,
no network access, nothing rate-limited. Every run recomputes its output
from scratch from files already on disk, so unlike the WRDS pull scripts it
does not need --force flags, a fingerprint/manifest system, or atomic
write-then-rename ceremony: if a run is interrupted, the fix is just to run
it again, and every output file is fully regenerable (see this plan's own
Risk/Rollback notes).

Deliberately reuses wrds_pull_lib.load_resolved_filter_lists() (and its
comma-joined-PERMNO-column parsing convention) rather than re-deriving the
resolved PERMNO list a second, slightly-different way -- crsp.dsf and
crsp.dsedelist were already pulled filtered to exactly this list, so the
only job left here is carrying that same identifier through cleanly, not
re-filtering.

Usage:
    python scripts/normalize_equity_data.py
Reads from data/raw_wrds/crsp/, data/metadata/resolved_universe_ids.parquet,
and data/raw_equity/master_stock.csv (all read-only). Writes to
data/normalized_equity/.
"""

import datetime as dt
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402

REPO_ROOT = lib.REPO_ROOT
RAW_CRSP_DIR = lib.RAW_DIR / "crsp"
RAW_EQUITY_DIR = REPO_ROOT / "data" / "raw_equity"
METADATA_DIR = lib.METADATA_DIR
OUT_DIR = REPO_ROOT / "data" / "normalized_equity"
MASTER_STOCK_PATH = RAW_EQUITY_DIR / "master_stock.csv"

# The archive's native window (see wrds_pull_lib.START_DATE/END_DATE) --
# DISTINCT from years[0]/years[-1] (the dsf_<year>.parquet files actually on
# disk, 1995-2014), which additionally include a one-year lookback buffer
# and a mid-2014 forward-return buffer around this core 1996-2013 window.
# ARCHIVE_END_DATE is reused (rather than a second hardcoded "2013-12-31"
# literal) as the upper bound for the master_stock.csv cross-validation
# window -- master_stock.csv itself runs through 2025, but only its overlap
# with this project's actual 1996-2013 window of interest is relevant to
# validate against. ARCHIVE_START_DATE is used only for describing that
# same window of interest in FINDINGS.md -- using years[0] (1995) there
# would describe the buffer year as part of "the window this project cares
# about," which it isn't.
ARCHIVE_START_DATE = lib.START_DATE
ARCHIVE_END_DATE = lib.END_DATE

# CRSP's documented missing-return sentinel codes for dsf.ret / dsf.retx
# (see e.g. the CRSP Stock File Guide). A real daily return can never be
# <= -1 (a -100% return is a total loss), so these large-magnitude codes
# can never collide with a genuine return value -- matching on exact
# equality is safe. Applied defensively: an empirical scan of all 20
# dsf_<year>.parquet files (20,478,337 rows) found zero occurrences of any
# of these codes in this particular WRDS pull (see FINDINGS.md), which is
# plausible for a modern WRDS Postgres-hosted export (nulls may already
# arrive as SQL NULL rather than the legacy SAS sentinel-float convention)
# -- but the mapping costs nothing and protects against a future re-pull
# (or a different WRDS delivery path) that does carry them.
CRSP_MISSING_RETURN_CODES = (-66.0, -77.0, -88.0, -99.0)

# Exactly the columns each cleaning function below actually reads from its
# raw input. Used two ways: (1) as a Parquet column projection, so a
# dsf_<year>.parquet read doesn't materialize bid/ask/bidlo/askhi/openprc/
# numtrd just to drop them (dsf carries 15 columns; only 9 are used here);
# (2) as a fail-fast check (see require_columns) run immediately after
# each raw load, so a pull that's genuinely missing one of the OPTIONAL
# columns wrds_pull_lib.py allows (retx/vol on dsf; vwretx/ewretd/ewretx on
# dsi; dlstcd on dsedelist) aborts with a clear message before any
# equity_<year>.parquet is written, instead of a bare KeyError partway
# through the year loop.
DSF_CONSUMED_COLUMNS = ["permno", "date", "ret", "retx", "prc", "shrout", "cfacpr", "cfacshr", "vol"]
DSI_CONSUMED_COLUMNS = ["date", "vwretd", "sprtrn", "vwretx", "ewretd", "ewretx"]
DSEDELIST_CONSUMED_COLUMNS = ["permno", "dlstdt", "dlret", "dlstcd"]

# Known 2-for-1 stock splits used as named, human-checkable evidence for the
# split-adjustment finding (see split_spot_check / FINDINGS.md). AAPL permno
# is looked up from stocknames rather than hardcoded twice. Keyed by year
# since main() processes one dsf_<year>.parquet file at a time.
AAPL_TICKER = "AAPL"
AAPL_SPLIT_YEAR_DATES = {2000: "2000-06-21", 2005: "2005-02-28"}
MSFT_TICKER = "MSFT"
MSFT_DIVIDEND_SPOT_DATE = "2003-02-19"  # ex-date of MSFT's first regular quarterly dividend

# A single-day return difference bigger than this between CRSP and
# master_stock.csv is not plausibly "the same real trading day, measured
# slightly differently" -- it's used only to separate out a small number of
# implausible master_stock.csv values (see cross_validate_master_stock) when
# reporting a trimmed correlation alongside the untrimmed one.
EXTREME_RETURN_DIFF_THRESHOLD = 0.10

# Per-ticker alignment gate for cross_validate_master_stock (see its
# docstring for why this has to be per ticker, not a single pooled check).
# On this project's real data, the two populations are cleanly separated
# with an empty gap between them: every genuinely-matched ticker has a
# median day-over-day return difference on the order of 1e-6 to 1e-7
# against master_stock.csv (the worst legitimately-matching ticker measured
# at ~7.0e-4), while every ticker where master_stock.csv's current ticker
# label evidently points at a different company than CRSP's point-in-time
# one measured at >= 2.4e-3. 1e-3 sits in that empty gap. A correlation-
# based gate was tried and rejected: it wrongly quarantines tickers that
# really are the same company but have a handful of noisy/outlier days,
# which a median (robust to outliers) does not.
TICKER_ALIGNMENT_MAX_MEDIAN_RET_DIFF = 1e-3
# A ticker with fewer than this many comparable days doesn't have enough
# data for the median above to mean anything -- treated as unverifiable
# and quarantined rather than trusted on a tiny sample.
TICKER_ALIGNMENT_MIN_DAYS = 3


# ---------------------------------------------------------------------------
# Small, pure cleaning functions -- each independently unit-testable.
# ---------------------------------------------------------------------------


def safe_concat(frames, **kwargs) -> pd.DataFrame:
    """pd.concat(frames, **kwargs), but filtering out empty frames first.

    Under this project's pinned pandas/NumPy versions, pd.concat over a
    mix of empty and non-empty frames that share a datetime64 column
    triggers a spurious DeprecationWarning ("The 'generic' unit for NumPy
    timedelta is deprecated ... will raise an error in the future") even
    though the empty frame(s) contribute zero rows either way -- and it
    still triggers even if EVERY frame is empty (verified: not just a
    mixed-empty/non-empty case). This shows up in practice, not just in
    theory: main()'s per-year event-detection loop concatenates one
    events-found DataFrame per year, and most years find zero adjustment
    events, so `pd.concat(event_frames_by_year, ...)` mixes empty and
    non-empty frames on every real run.

    Behavior otherwise matches plain pd.concat: an empty `frames` still
    raises pd.concat's own "No objects to concatenate" ValueError, and if
    every frame is empty, one of them (matching dtypes/columns) is
    returned rather than a dtype-inference concat neither needs.
    """
    frames = list(frames)
    non_empty = [f for f in frames if len(f) > 0]
    if non_empty:
        return pd.concat(non_empty, **kwargs)
    if frames:
        return frames[0].copy() if not kwargs.get("ignore_index") else frames[0].reset_index(drop=True).copy()
    return pd.concat(frames, **kwargs)


def require_columns(df: pd.DataFrame, columns, source_name: str) -> None:
    """Fail fast, with a clear message naming the source and the missing
    column(s), rather than a bare KeyError partway through processing.
    Several columns this script reads (retx/vol on dsf; vwretx/ewretd/
    ewretx on dsi; dlstcd on dsedelist) are declared OPTIONAL by
    wrds_pull_lib.py's pull_table(), so a pull that legitimately omitted
    one on the WRDS side would otherwise crash deep inside a per-year loop
    after other years' output has already been written."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise SystemExit(
            f"FATAL: {source_name} is missing required column(s) {missing} "
            f"-- has {list(df.columns)}. If this column is legitimately "
            f"optional in the WRDS pull (see wrds_pull_lib.py), this "
            f"script needs updating to handle its absence; if not, re-run "
            f"the relevant pull_wrds_crsp.py step."
        )


def read_parquet_with_projection(path, columns) -> pd.DataFrame:
    """pd.read_parquet(path, columns=columns), but a missing column raises
    a clear, actionable SystemExit (naming the file and the missing
    column(s)) instead of a raw pyarrow.lib.ArrowInvalid traceback. Reads
    only `columns` from disk -- for dsf_<year>.parquet specifically, this
    is the difference between reading all 15 columns (6 of which,
    bid/ask/bidlo/askhi/openprc/numtrd, are never used below) and reading
    only the 9 this script needs, roughly halving that read's memory
    footprint."""
    try:
        return pd.read_parquet(path, columns=list(columns))
    except Exception as e:
        available = pq.ParquetFile(path).schema_arrow.names  # schema only, no data read
        missing = [c for c in columns if c not in available]
        if missing:
            raise SystemExit(
                f"FATAL: {path} is missing required column(s) {missing} "
                f"-- has {list(available)}. If this column is legitimately "
                f"optional in the WRDS pull (see wrds_pull_lib.py), this "
                f"script needs updating to handle its absence; if not, "
                f"re-run the relevant pull_wrds_*.py step."
            ) from e
        raise


def discover_dsf_years(raw_crsp_dir=None):
    """Return the sorted list of years with a dsf_<year>.parquet file
    present under raw_crsp_dir, discovered from what's actually on disk
    rather than assumed from a hardcoded date range."""
    raw_crsp_dir = Path(raw_crsp_dir) if raw_crsp_dir is not None else RAW_CRSP_DIR
    years = []
    for p in sorted(raw_crsp_dir.glob("dsf_*.parquet")):
        suffix = p.stem.split("_", 1)[1]
        if suffix.isdigit():
            years.append(int(suffix))
    return sorted(years)


def parse_iso_date_column(series: pd.Series) -> pd.Series:
    """Parse a VARCHAR 'YYYY-MM-DD' column (CRSP's on-disk date
    representation in this project's WRDS pull) into pandas
    datetime64[ns]. Fails loudly (errors='raise') rather than silently
    coercing bad values to NaT -- a parse failure here means the raw pull's
    date format assumption (verified directly against the real files
    before writing this module) no longer holds, and cleaning code built
    on that assumption should not proceed as if nothing happened."""
    return pd.to_datetime(series, format="%Y-%m-%d", errors="raise")


def clean_negative_prices(series: pd.Series):
    """CRSP convention: a negative `prc` means the reported value is minus
    the average of the closing bid and ask (no actual trade recorded that
    day), not a real trade price. Returns (abs_valued_prices,
    is_bidask_avg_flag) so the information is preserved (as a boolean
    column) rather than silently discarded by the abs()."""
    is_bidask_avg = (series < 0).fillna(False)
    return series.abs(), is_bidask_avg


def map_missing_return_codes(series: pd.Series):
    """Replace CRSP's documented missing-return sentinel codes (see
    CRSP_MISSING_RETURN_CODES) with a real null. Returns (cleaned_series,
    n_replaced)."""
    mask = series.isin(CRSP_MISSING_RETURN_CODES)
    cleaned = series.where(~mask, other=np.nan)
    return cleaned, int(mask.sum())


def load_resolved_permnos(resolved_ids_path=None):
    """The resolved universe's distinct PERMNO list, reconstructed via
    wrds_pull_lib's own comma-joined-column parser -- exactly the same
    list crsp.dsf/crsp.dsedelist were pulled filtered by, so cleaning code
    built on it can't drift from what was actually pulled."""
    permnos, _gvkeys, _ibes_tickers = lib.load_resolved_filter_lists(resolved_ids_path)
    return permnos


def load_stocknames_for_ticker_lookup(stocknames_path):
    """Load crsp.stocknames' permno/namedt/nameenddt/ticker columns, with
    namedt/nameenddt parsed to real dates and permno normalized to plain
    int64 (the raw Parquet file uses pandas' nullable Int64 here, while
    dsf's permno is plain int64 -- pd.merge_asof requires matching dtypes),
    sorted for merge_asof."""
    sn = pd.read_parquet(stocknames_path, columns=["permno", "namedt", "nameenddt", "ticker"])
    sn = sn.copy()
    sn["permno"] = sn["permno"].astype("int64")
    sn["namedt"] = parse_iso_date_column(sn["namedt"])
    sn["nameenddt"] = parse_iso_date_column(sn["nameenddt"])
    return sn.sort_values(["namedt", "permno"]).reset_index(drop=True)


def attach_point_in_time_ticker(df: pd.DataFrame, stocknames: pd.DataFrame) -> pd.Series:
    """For each (permno, date) row in df, return the CRSP ticker on record
    AS OF that date -- the stocknames segment where namedt <= date <=
    nameenddt -- not today's/most-recent ticker. Tickers get reassigned
    over a company's life (e.g. permno 90298 traded as TOPT through
    2008-01-02 and as TOPS from 2008-01-03 on), so attaching a single
    current ticker to every historical row would misattribute history and
    would also break the master_stock.csv cross-validation join, which
    matches on ticker. Rows that fall in a gap in CRSP's own recorded name
    history (rare; stocknames itself has occasional null-ticker segments)
    get a null ticker rather than a guess.

    Implemented with pd.merge_asof(direction="backward"), which requires
    both frames sorted by the 'on' column (date) -- both df and stocknames
    are sorted here, unconditionally, so callers don't need to pre-sort
    (load_stocknames_for_ticker_lookup already returns stocknames sorted,
    but this function doesn't rely on that -- any caller passing an
    unsorted stocknames frame would otherwise get a cryptic pandas
    "right keys must be sorted" error instead of a correct result). The
    original row order (and index, including a named one) of df is
    restored before returning.
    """
    # A fixed, private column name for the round-tripped index -- rather
    # than plain reset_index(drop=False), which names the new column after
    # df.index.name (or "index" if unnamed). A caller passing a frame with
    # a named index (or one that already happens to have an "index"
    # column) would otherwise make the later `.set_index("index")` raise
    # KeyError: "None of ['index'] are in the columns".
    left = df[["permno", "date"]].copy()
    orig_index = left.index
    left = left.reset_index(drop=True)
    left["_orig_row"] = orig_index
    left = left.sort_values(["date", "permno"])
    right = stocknames.sort_values(["namedt", "permno"])
    merged = pd.merge_asof(
        left, right,
        left_on="date", right_on="namedt", by="permno", direction="backward",
    )
    out_of_range = merged["nameenddt"].isna() | (merged["date"] > merged["nameenddt"])
    merged.loc[out_of_range, "ticker"] = pd.NA
    merged = merged.set_index("_orig_row").sort_index()
    merged.index.name = orig_index.name
    return merged["ticker"]


# ---------------------------------------------------------------------------
# crsp.dsf: the main per-(permno, date) equity panel.
# ---------------------------------------------------------------------------


def clean_dsf_year(raw: pd.DataFrame, resolved_permnos, stocknames: pd.DataFrame):
    """Clean one year's raw crsp.dsf DataFrame (as loaded straight from
    dsf_<year>.parquet) into the normalized_equity schema. Returns
    (cleaned_df, stats_dict). Pure function of its inputs (no file I/O),
    for unit testing against small synthetic frames."""
    stats = {}
    df = raw.reset_index(drop=True).copy()
    df["permno"] = df["permno"].astype("int64")
    df["date"] = parse_iso_date_column(df["date"])

    stats["input_rows"] = len(df)

    resolved_set = set(resolved_permnos)
    not_resolved = ~df["permno"].isin(resolved_set)
    stats["rows_permno_not_in_resolved_universe"] = int(not_resolved.sum())
    df = df.loc[~not_resolved].reset_index(drop=True)

    dup = df.duplicated(subset=["permno", "date"])
    stats["duplicate_permno_date_rows_dropped"] = int(dup.sum())
    df = df.loc[~dup].reset_index(drop=True)

    stats["nonnull_prc_rows"] = int(df["prc"].notna().sum())
    prc_clean, is_bidask = clean_negative_prices(df["prc"])
    stats["negative_prc_rows"] = int(is_bidask.sum())
    df["prc"] = prc_clean
    df["prc_is_bidask_avg"] = is_bidask

    ret_clean, n_ret_sentinel = map_missing_return_codes(df["ret"])
    retx_clean, n_retx_sentinel = map_missing_return_codes(df["retx"])
    stats["ret_sentinel_rows_mapped_to_null"] = n_ret_sentinel
    stats["retx_sentinel_rows_mapped_to_null"] = n_retx_sentinel
    df["ret"] = ret_clean
    df["retx"] = retx_clean

    cfacpr = df["cfacpr"].astype("float64")
    # No derived split-adjusted price column is stored here (see
    # FINDINGS.md's adjustment-status section): cfacpr is a CUMULATIVE
    # factor normalized to the last date in whatever CRSP file was pulled,
    # not to the end of this project's sample, so prc / cfacpr for an old
    # row silently bakes in every split between that row's date and the
    # pull date (e.g. AAPL's cfacpr reflects an August-2020 split, six
    # years past this pull's 2014-06-30 end) -- a point-in-time violation
    # if ever used for a historical price-level screen. prc (raw) and
    # cfacpr (as pulled) are both still in the output for anyone who
    # explicitly wants prc / cfacpr with that caveat in view; ret/retx
    # (already split- and dividend-adjusted by CRSP -- see FINDINGS.md)
    # are the point-in-time-safe way to compare across a split.
    df["cfacpr"] = cfacpr
    stats["cfacpr_zero_rows"] = int((cfacpr == 0).sum())

    df["ticker"] = attach_point_in_time_ticker(df[["permno", "date"]], stocknames)
    stats["rows_with_no_ticker_on_record"] = int(df["ticker"].isna().sum())

    df["shrout"] = df["shrout"].astype("float64")
    df["vol"] = df["vol"].astype("float64")
    df["cfacshr"] = df["cfacshr"].astype("float64")

    out = df[[
        "permno", "date", "ticker",
        "prc", "prc_is_bidask_avg",
        "ret", "retx",
        "shrout", "vol", "cfacpr", "cfacshr",
    ]].reset_index(drop=True)
    stats["output_rows"] = len(out)
    return out, stats


def aggregate_year_stats(stats_by_year: dict) -> dict:
    """Sum every per-year stats dict (from clean_dsf_year) into one set of
    whole-pull totals for the findings report."""
    keys = next(iter(stats_by_year.values())).keys()
    return {key: int(sum(s[key] for s in stats_by_year.values())) for key in keys}


# ---------------------------------------------------------------------------
# crsp.dsi: the market-benchmark series.
# ---------------------------------------------------------------------------


def clean_dsi(raw: pd.DataFrame):
    """Clean crsp.dsi (one row per trading day, market-wide) into a typed-
    date benchmark series. sprtrn (S&P 500 return) and vwretd (CRSP
    value-weighted market return, dividends included) are the two primary
    market-adjustment benchmark candidates; vwretx/ewretd/ewretx are carried
    through too since the table is tiny and later slices may want them."""
    stats = {}
    df = raw.reset_index(drop=True).copy()
    df["date"] = parse_iso_date_column(df["date"])

    # dsi is one row per trading day market-wide; a duplicated date would
    # silently fan out every downstream market-adjustment join (each
    # equity row on that date would match more than one benchmark row).
    # Mirrors clean_dsf_year's duplicate-(permno,date) guard.
    dup = df.duplicated(subset=["date"])
    stats["duplicate_date_rows_dropped"] = int(dup.sum())
    df = df.loc[~dup].reset_index(drop=True)

    value_cols = ["vwretd", "sprtrn", "vwretx", "ewretd", "ewretx"]
    for col in value_cols:
        df[col] = df[col].astype("float64")
    stats["rows"] = len(df)
    stats["null_value_rows"] = int(df[value_cols].isna().any(axis=1).sum())
    stats["date_min"] = df["date"].min()
    stats["date_max"] = df["date"].max()
    out = df[["date"] + value_cols].reset_index(drop=True)
    return out, stats


# ---------------------------------------------------------------------------
# crsp.dsedelist: context only (delisting-return handling stays out of scope
# for this pilot slice, per the plan -- see FINDINGS.md).
# ---------------------------------------------------------------------------


def summarize_dsedelist(dsedelist_path, resolved_permnos):
    """Summarize crsp.dsedelist for the findings report only -- this
    slice does not merge delisting returns into the equity panel (an
    explicit, documented scope decision; see FINDINGS.md and the plan's
    own Scope Out list)."""
    de = pd.read_parquet(dsedelist_path)
    require_columns(de, DSEDELIST_CONSUMED_COLUMNS, str(dsedelist_path))
    de = de.copy()
    de["permno"] = de["permno"].astype("int64")
    de["dlstdt"] = parse_iso_date_column(de["dlstdt"])
    resolved_set = set(resolved_permnos)

    stats = {}
    stats["rows"] = len(de)
    stats["distinct_permnos"] = int(de["permno"].nunique())
    stats["rows_outside_resolved_universe"] = int((~de["permno"].isin(resolved_set)).sum())
    stats["date_min"] = de["dlstdt"].min()
    stats["date_max"] = de["dlstdt"].max()
    stats["dlret_null_rows"] = int(de["dlret"].isna().sum())
    top_codes = de["dlstcd"].value_counts().head(5)
    stats["top_dlstcd_codes"] = [(int(code), int(count)) for code, count in top_codes.items()]
    return stats


# ---------------------------------------------------------------------------
# Split/dividend-adjustment finding: broad quantitative check + named,
# human-checkable spot checks (Apple's two splits, Microsoft's first
# dividend).
# ---------------------------------------------------------------------------


# Columns detect_adjustment_events needs -- shared by main()'s per-year
# incremental pass and last_row_per_permno below.
EVENT_CHECK_COLUMNS = ["permno", "date", "prc", "ret", "cfacpr"]


def last_row_per_permno(df: pd.DataFrame) -> pd.DataFrame:
    """The most recent (by date) row per permno in df. Used by main() to
    carry each permno's last-seen row across a calendar-year boundary into
    the next year's detect_adjustment_events call, so finding adjustment
    events doesn't require concatenating and holding the full 20+ year,
    20M-row panel in memory at once -- only ever one year's data plus a
    small (<= number of distinct permnos) carryover frame. A permno with a
    trading gap spanning more than one year (delisted, then relisted) is
    still handled correctly: it simply keeps its old carryover row until
    it reappears, exactly matching what a single groupby(permno).shift(1)
    over the whole concatenated panel would have used as "the previous
    row" regardless of how many calendar years separate the two rows."""
    return df.sort_values(["permno", "date"]).groupby("permno", as_index=False).tail(1)


def update_event_carryover(event_carryover, year_event_cols: pd.DataFrame) -> pd.DataFrame:
    """Merge this year's last-row-per-permno (see last_row_per_permno) into
    the running carryover state passed to the next year: exactly one row
    per permno seen so far, using each permno's most recent appearance.

    Uses safe_concat (see its docstring) rather than plain pd.concat:
    whenever every permno in the old carryover also appears in this year's
    data, filtering them out of the old carryover leaves an empty
    "remainder" frame, and that's not a rare corner case here -- it's the
    common case once the carryover has accumulated most of the universe.
    """
    if event_carryover is None:
        return last_row_per_permno(year_event_cols)
    new_last = last_row_per_permno(year_event_cols)
    remainder = event_carryover[~event_carryover["permno"].isin(new_last["permno"])]
    return safe_concat([remainder, new_last], ignore_index=True)


def detect_adjustment_events(df: pd.DataFrame, min_change: float = 0.01) -> pd.DataFrame:
    """Given a cleaned multi-year CRSP frame (permno, date, prc, ret,
    cfacpr), find every (permno, date) where cfacpr changed by more than
    `min_change` (fractional) from the immediately preceding trading day
    for the same permno -- i.e. every split or other distribution CRSP's
    own adjustment machinery recognized. For each event, also computes the
    "naive" unadjusted return implied by the raw price alone (today's prc
    over yesterday's prc, minus one), so it can be compared against the
    actual `ret` CRSP reported for that same day. One row per event."""
    d = df.sort_values(["permno", "date"]).reset_index(drop=True)
    g = d.groupby("permno", sort=False)
    prev_prc = g["prc"].shift(1)
    prev_cfacpr = g["cfacpr"].shift(1)
    naive_ret = d["prc"].abs() / prev_prc.abs() - 1
    cfacpr_ratio = d["cfacpr"] / prev_cfacpr
    # prev_prc > 0 / prev_cfacpr > 0 (not just .notna()): cfacpr is 0 for a
    # small number of real rows (see clean_dsf_year's cfacpr_zero_rows
    # stat), and a 0 -> positive transition would otherwise pass the
    # `.notna()` checks and produce an infinite cfacpr_ratio/naive_ret --
    # not a real split, and not evidence that belongs in this function's
    # naive-vs-actual-return comparison.
    is_event = (
        (prev_cfacpr > 0) & (prev_prc > 0) & d["ret"].notna()
        & ((cfacpr_ratio - 1).abs() > min_change)
    )

    events = d.loc[is_event, ["permno", "date", "prc", "ret", "cfacpr"]].copy()
    events["naive_ret"] = naive_ret.loc[is_event]
    events["cfacpr_ratio"] = cfacpr_ratio.loc[is_event]
    return events.reset_index(drop=True)


def summarize_adjustment_events(events: pd.DataFrame) -> dict:
    stats = {"n_events": int(len(events))}
    if len(events) == 0:
        return stats
    stats["naive_ret_abs_median"] = float(events["naive_ret"].abs().median())
    stats["naive_ret_abs_mean"] = float(events["naive_ret"].abs().mean())
    stats["actual_ret_abs_median"] = float(events["ret"].abs().median())
    stats["actual_ret_abs_mean"] = float(events["ret"].abs().mean())
    stats["frac_naive_ret_gt_20pct"] = float((events["naive_ret"].abs() > 0.20).mean())
    stats["frac_actual_ret_gt_20pct"] = float((events["ret"].abs() > 0.20).mean())
    return stats


def summarize_prc_adj_lookahead_risk(first_year_df: pd.DataFrame, final_cfacpr_per_permno: pd.DataFrame, price_threshold: float = 5.0) -> dict:
    """Quantifies, concretely, why this script does NOT ship a derived
    split-adjusted price column (`prc / cfacpr`) -- see FINDINGS.md's
    adjustment-status section. `cfacpr` is a CUMULATIVE factor normalized
    to the last date in whatever CRSP file was pulled, not to the end of
    this project's sample, so `prc / cfacpr` for an early row silently
    bakes in every split between that row's date and the pull date.

    Demonstrates the effect with a simple "at least `price_threshold`"
    screen (the kind a liquidity/penny-stock filter might use) applied to
    `first_year_df`'s actual traded `prc` and, separately, to the
    look-ahead-tainted `prc / cfacpr` for the same rows -- counting rows
    that land on OPPOSITE sides of the threshold under the two, which is
    exactly what a point-in-time violation looks like: a screen believing
    something about a stock's price on a given day that could not have
    been known on that day.

    `final_cfacpr_per_permno` (one row per permno, its most-recently-seen
    cfacpr across the whole pull) additionally reports how many permnos
    even have this problem at all: cfacpr == 1 for a permno with no
    splits/distributions in the whole pulled window, so `prc / cfacpr`
    for its rows would be a no-op adjustment regardless. Both quantities
    are computed fresh from whatever's actually on disk, not hardcoded.
    """
    d = first_year_df.dropna(subset=["prc", "cfacpr", "permno"])
    d = d.loc[d["cfacpr"] > 0]
    would_be_adj = d["prc"] / d["cfacpr"]
    raw_pass = d["prc"] >= price_threshold
    adj_pass = would_be_adj >= price_threshold
    disagree = raw_pass != adj_pass
    return {
        "price_threshold": price_threshold,
        "wrongly_excluded_rows": int((raw_pass & ~adj_pass).sum()),
        "wrongly_included_rows": int((~raw_pass & adj_pass).sum()),
        "permnos_affected": int(d.loc[disagree, "permno"].nunique()),
        "n_permnos_cfacpr_ne_1_at_window_end": int((final_cfacpr_per_permno["cfacpr"] != 1).sum()),
        "n_permnos_total": int(len(final_cfacpr_per_permno)),
    }


def split_spot_check(df: pd.DataFrame, permno: int, around_date: str, window_days: int = 7) -> pd.DataFrame:
    """The rows for `permno` within `window_days` calendar days of
    `around_date` -- used to embed a concrete, named, human-checkable
    example (e.g. Apple's 2005-02-28 2-for-1 split) into FINDINGS.md."""
    # dt.timedelta rather than pd.Timedelta: under this project's pinned
    # pandas/numpy versions, pd.Timedelta(days=int) on its own triggers a
    # spurious "generic unit" DeprecationWarning from NumPy's timedelta64
    # handling; a plain stdlib timedelta sidesteps it entirely.
    lo = pd.Timestamp(around_date) - dt.timedelta(days=window_days)
    hi = pd.Timestamp(around_date) + dt.timedelta(days=window_days)
    sub = df[(df["permno"] == permno) & (df["date"] >= lo) & (df["date"] <= hi)]
    cols = [c for c in ["date", "prc", "ret", "retx", "shrout", "cfacpr"] if c in sub.columns]
    return sub.sort_values("date")[cols].reset_index(drop=True)


def lookup_permno_for_ticker(stocknames: pd.DataFrame, ticker: str) -> int:
    """Most recent permno on record for `ticker` -- used only to find the
    named spot-check permnos (AAPL, MSFT) without hardcoding them twice."""
    matches = stocknames.loc[stocknames["ticker"] == ticker]
    if matches.empty:
        raise ValueError(f"no stocknames row found for ticker {ticker!r}")
    return int(matches.sort_values("namedt").iloc[-1]["permno"])


# ---------------------------------------------------------------------------
# master_stock.csv: cleaning + cross-validation (secondary check).
# ---------------------------------------------------------------------------


def read_master_stock_raw(csv_path, end_date=None) -> pd.DataFrame:
    """Read master_stock.csv's Tic/Date/Price columns as plain strings via
    DuckDB, with an explicit quote character, a NAME-based column
    selection, and (when `end_date` is given) predicate pushdown so this
    project's 2011-2013-window read doesn't materialize all ~13M rows just
    to discard the ~85% outside the window a moment later.

    DuckDB's CSV dialect sniffer samples the file's early rows to guess a
    dialect, and this file's normal per-share prices never need quoting
    (e.g. `30.31781584315815`), so the sniffer locks in quote='' (no quote
    character at all). A block of non-stock rows appended at the very end
    of the file DOES need quoting (thousand-separated values like
    `"13,444.90"`), and under the sniffed no-quote dialect those rows split
    into the wrong number of fields -- DuckDB then refuses to read the file
    at all ("Expected Number of Columns: 3 Found: 4"). Forcing quote='"'
    explicitly (rather than trusting the sniffer) reads every row, including
    the ones that broke a strict/auto-detected read, without dropping or
    silently skipping any of them.

    Uses `all_varchar=true` (every column read as text, uniformly) plus a
    NAME-based `SELECT Tic AS ..., Date AS ..., Price AS ...` rather than
    the `columns={'Tic': 'VARCHAR', ...}` override this function used
    previously: that override is POSITIONAL, not name-based -- DuckDB uses
    the dict only to assign types and names to the columns in file order,
    ignoring what the header row actually says. A master_stock.csv refresh
    with its three columns in a different order would then silently
    mislabel every field (e.g. Date's text ending up in the `ticker`
    column) with no error at all. `all_varchar=true` plus selecting by
    name means a reordered header is handled correctly, and a RENAMED
    column raises a clear "column not found" error instead.

    The `end_date` predicate is pushed down as a lexical string comparison
    on the still-unparsed date text (`Date <= '<end_date>'`), which is only
    valid for genuinely ISO-formatted ('YYYY-MM-DD') dates -- safe here
    because it's combined with the blank-ticker filter, and every
    non-ISO-formatted (mm/dd/yyyy) date in this file is confirmed (see
    profile_master_stock_file / FINDINGS.md) to fall inside the
    blank-ticker rows this same WHERE clause already excludes. Any row this
    pushdown filter can't confidently place (a future file with a
    non-ISO-formatted date on a non-blank ticker) still reaches
    clean_master_stock_frame's own, non-pushed-down date-format handling
    unfiltered by year, since the lexical comparison only discards rows
    it's sure sort before `end_date`; clean_master_stock_frame is the
    correctness boundary, this is purely a volume-reduction optimization
    ahead of it.
    """
    con = duckdb.connect()
    try:
        where_clause = "WHERE ticker IS NOT NULL AND trim(ticker) <> ''"
        params = []
        if end_date is not None:
            where_clause += " AND date_text <= ?"
            params.append(str(end_date))
        return con.execute(
            f"""
            SELECT ticker, date_text, price_text
            FROM (
                SELECT Tic AS ticker, Date AS date_text, Price AS price_text
                FROM read_csv(
                    {duckdb_quote_literal(str(csv_path))},
                    header=True, quote='"', escape='"', all_varchar=true
                )
            )
            {where_clause}
            """,
            params,
        ).fetchdf()
    finally:
        con.close()


def duckdb_quote_literal(value: str) -> str:
    """Single-quote a string for embedding in a DuckDB SQL literal,
    doubling any embedded single quote (the standard SQL escape) --
    csv_path is a local, repo-controlled file path, not untrusted input,
    but there is no reason to string-format it into SQL unescaped."""
    return "'" + value.replace("'", "''") + "'"


def clean_master_stock_frame(raw: pd.DataFrame, end_date=None):
    """Pure-pandas cleaning of an already-loaded master_stock raw frame
    (columns ticker/date_text/price_text, all strings) -- split out from
    the DuckDB read step so this logic is unit-testable against small
    synthetic frames without needing the real 303MB CSV.

    Handles, in order:
      1. Blank/null ticker rows (a single ~5,450-row block appended at the
         end of the real file turns out to be a non-stock series, not a
         per-ticker data-quality problem -- see FINDINGS.md).
      2. Mixed date formats (ISO and mm/dd/yyyy both appear in the raw
         file, though empirically only among the same blank-ticker rows
         removed in step 1 -- see FINDINGS.md).
      3. Unparseable/missing prices (empirically always a genuinely empty
         field, not garbled text), plus non-finite (inf/-inf) and negative
         prices found in the real file that step 1 doesn't already remove
         and that a real market price can never legitimately be -- unlike
         CRSP's well-documented bid/ask-average negative-price convention,
         there is no known, citable reason for a negative or infinite value
         here, so these are treated as invalid/null rather than sign-
         flipped.

    Returns (cleaned_df[ticker, date, price], stats_dict).
    """
    stats = {"input_rows": len(raw)}

    ticker = raw["ticker"]
    blank_ticker = ticker.isna() | (ticker.astype("string").str.strip() == "")
    stats["blank_ticker_rows_dropped"] = int(blank_ticker.sum())
    df = raw.loc[~blank_ticker].copy()

    date_text = df["date_text"].astype("string")
    iso_mask = date_text.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
    mdy_mask = date_text.str.match(r"^\d{2}/\d{2}/\d{4}$", na=False)
    stats["iso_date_rows"] = int(iso_mask.sum())
    stats["mdy_date_rows"] = int(mdy_mask.sum())
    stats["unrecognized_date_format_rows"] = int(len(df) - iso_mask.sum() - mdy_mask.sum())

    # errors="coerce" (not "raise"): a value can match the mm/dd/yyyy or
    # ISO shape regex above and still not be a real calendar date (month
    # 13, day 45, ...) -- this is an external, unvalidated file, so a
    # shape-matched-but-invalid date becomes NaT (and is dropped just below
    # like any other unparseable date) instead of crashing the whole run.
    parsed_date = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    if iso_mask.any():
        parsed_date.loc[iso_mask] = pd.to_datetime(date_text.loc[iso_mask], format="%Y-%m-%d", errors="coerce")
    if mdy_mask.any():
        parsed_date.loc[mdy_mask] = pd.to_datetime(date_text.loc[mdy_mask], format="%m/%d/%Y", errors="coerce")
    df["date"] = parsed_date
    stats["shape_matched_but_invalid_date_rows"] = int((iso_mask | mdy_mask).sum() - parsed_date.notna().sum())
    df = df.loc[df["date"].notna()].copy()

    price_text = df["price_text"].astype("string").str.replace(",", "", regex=False)
    price = pd.to_numeric(price_text, errors="coerce")
    is_null_field = df["price_text"].isna() | (df["price_text"].astype("string").str.strip() == "")
    stats["missing_price_rows"] = int((price.isna() & is_null_field).sum())
    stats["unparseable_text_price_rows"] = int((price.isna() & ~is_null_field).sum())

    # np.isinf(nan) is False, so NaN (missing/unparseable) prices are
    # correctly excluded here without any extra masking.
    is_inf = pd.Series(np.isinf(price.to_numpy(dtype="float64")), index=price.index)
    stats["infinite_price_rows"] = int(is_inf.sum())
    is_negative = (price < 0) & ~is_inf
    stats["negative_price_rows"] = int(is_negative.sum())

    invalid_price = price.isna() | is_inf | is_negative
    df["price"] = price.where(~invalid_price, other=np.nan)
    stats["valid_price_rows"] = int((~invalid_price).sum())

    stats["distinct_tickers"] = int(df["ticker"].nunique())
    stats["date_min"] = df["date"].min()
    stats["date_max"] = df["date"].max()

    if end_date is not None:
        df = df.loc[df["date"] <= pd.Timestamp(end_date)].copy()
    stats["rows_after_window_filter"] = len(df)

    out = df[["ticker", "date", "price"]].reset_index(drop=True)
    return out, stats


def compute_return_from_prices(df: pd.DataFrame, group_col: str, date_col: str, price_col: str, max_gap_days: int = 4) -> pd.Series:
    """Day-over-day simple return from a price-level series, grouped by
    `group_col` and computed only across gaps of at most `max_gap_days`
    calendar days (covers ordinary weekends/holidays) -- a longer gap means
    a row is missing from the source for that stretch, and naively calling
    pct_change() across it would silently mislabel a multi-day return as a
    one-day return."""
    ordered = df.sort_values([group_col, date_col])
    ret = ordered.groupby(group_col)[price_col].pct_change(fill_method=None)
    gap_days = ordered.groupby(group_col)[date_col].diff().dt.days
    ret = ret.where(gap_days <= max_gap_days)
    return ret.reindex(df.index)


def cross_validate_master_stock(crsp_df: pd.DataFrame, master_df: pd.DataFrame, window_start, window_end):
    """Compare CRSP-derived prices/returns against master_stock.csv's for
    every (ticker, date) pair present in both within [window_start,
    window_end]. Returns (stats_dict, merged_df).

    crsp_df must have columns permno/ticker/date/prc/ret. Rows where the
    same (ticker, date) key appears more than once on EITHER side are
    dropped from that side before joining, rather than silently fanned
    out into a many-to-many join:

    - On the CRSP side, this is a ticker-reuse collision or an artifact of
      an ambiguous PERMNO resolution (see resolved_universe_ids.parquet's
      permno_ambiguous flag).
    - On the master_stock.csv side, this is a genuine, confirmed data bug
      in the source file: some tickers (e.g. CMCSA) carry exactly two rows
      for every date, at two different, unrelated prices, across the
      file's entire history -- not a rare glitch. A naive inner join would
      silently double-count (or mismatch) every one of those dates rather
      than compare like with like.

    A THIRD alignment problem only shows up after joining: master_stock.csv
    evidently labels its whole price history with a security's CURRENT
    (as-of-download) ticker, not the ticker it traded under on that date --
    unlike the CRSP side, which is deliberately point-in-time (see
    attach_point_in_time_ticker). A ticker that has been reassigned to a
    different company over the years (e.g. "WOLF": Great Wolf Resorts in
    CRSP's history for this permno, Wolfspeed/Cree in master_stock.csv's
    current labeling) then joins two unrelated companies' prices on the
    same symbol. This is caught PER TICKER, after the join: for each
    matched ticker, the median absolute day-over-day return difference
    against master_stock.csv is computed, and any ticker whose median
    exceeds TICKER_ALIGNMENT_MAX_MEDIAN_RET_DIFF (or that has too few
    comparable days to judge) is quarantined out of the pooled statistics.
    A median (not a correlation) is used deliberately: a per-ticker
    correlation gate would also catch and wrongly discard tickers that
    really are the same company but happen to have a handful of noisy/
    outlier days -- the per-ticker median cleanly separates "two different
    companies" (median difference of several percentage points or more)
    from "same company, matches almost exactly every day" (median
    difference on the order of 1e-6) with an empty gap between the two
    groups in this data.
    """
    stats = {"window_start": window_start, "window_end": window_end}

    crsp_w = crsp_df.loc[
        (crsp_df["date"] >= window_start) & (crsp_df["date"] <= window_end) & crsp_df["ticker"].notna()
    ].copy()
    master_w = master_df.loc[
        (master_df["date"] >= window_start) & (master_df["date"] <= window_end)
    ].copy()

    crsp_dup = crsp_w.duplicated(subset=["ticker", "date"], keep=False)
    stats["crsp_ticker_date_collisions_dropped"] = int(crsp_dup.sum())
    crsp_w = crsp_w.loc[~crsp_dup]

    master_dup = master_w.duplicated(subset=["ticker", "date"], keep=False)
    stats["master_ticker_date_collisions_dropped"] = int(master_dup.sum())
    stats["master_ticker_date_collision_tickers"] = int(master_w.loc[master_dup, "ticker"].nunique())
    master_w = master_w.loc[~master_dup]

    stats["crsp_distinct_tickers_in_window"] = int(crsp_w["ticker"].nunique())
    stats["master_distinct_tickers_in_window"] = int(master_w["ticker"].nunique())

    # Computed on master_w BEFORE the join (not on the joined frame): a gap
    # in the JOINED frame can be created by the join itself (a date present
    # in master_stock.csv but missing from the CRSP side, or vice versa),
    # which would make an otherwise-adjacent pair of master_stock.csv rows
    # look like a real multi-day gap when it isn't one on master_stock.csv's
    # own calendar.
    master_w = master_w.sort_values(["ticker", "date"]).copy()
    master_w["master_ret"] = compute_return_from_prices(master_w, "ticker", "date", "price")

    merged = pd.merge(
        crsp_w[["permno", "ticker", "date", "prc", "ret"]],
        master_w[["ticker", "date", "price", "master_ret"]].rename(columns={"price": "master_price"}),
        on=["ticker", "date"], how="inner",
    )
    stats["matched_rows"] = int(len(merged))
    stats["matched_distinct_tickers"] = int(merged["ticker"].nunique())

    ret_cmp = merged.dropna(subset=["ret", "master_ret"]).copy()
    stats["return_comparison_rows"] = int(len(ret_cmp))
    if len(ret_cmp) == 0:
        return stats, merged

    ret_cmp["abs_diff"] = (ret_cmp["ret"] - ret_cmp["master_ret"]).abs()

    # Per-ticker alignment gate -- see docstring. Applied BEFORE any pooled
    # statistic so a handful of misaligned tickers can't distort (or, worse,
    # be diagnosed as a master_stock.csv "data error" rather than what it
    # actually is: a ticker matched to the wrong company).
    per_ticker = ret_cmp.groupby("ticker")["abs_diff"].agg(n_days="count", median_abs_diff="median")
    misaligned = (
        (per_ticker["median_abs_diff"] > TICKER_ALIGNMENT_MAX_MEDIAN_RET_DIFF)
        | (per_ticker["n_days"] < TICKER_ALIGNMENT_MIN_DAYS)
    )
    misaligned_tickers = set(per_ticker.index[misaligned])
    stats["tickers_quarantined_misaligned"] = len(misaligned_tickers)

    quarantined_mask = ret_cmp["ticker"].isin(misaligned_tickers)
    stats["rows_quarantined_misaligned"] = int(quarantined_mask.sum())
    if misaligned_tickers:
        examples = []
        top = ret_cmp.loc[quarantined_mask, "ticker"].value_counts().head(5)
        for ticker, n_rows in top.items():
            sub = merged.loc[merged["ticker"] == ticker]
            examples.append((
                ticker, int(n_rows),
                float(sub["prc"].median()), float(sub["master_price"].median()),
            ))
        stats["quarantined_ticker_examples"] = examples

    retained = ret_cmp.loc[~quarantined_mask]
    stats["return_comparison_rows_retained"] = int(len(retained))
    if len(retained) == 0:
        return stats, merged

    # Mean/median/within-tolerance are well-defined for even a single
    # matched day; correlation needs at least two points to mean anything,
    # so it (and everything derived from it below) is gated on a separate,
    # stricter threshold rather than reusing this one -- a lone matched day
    # must not silently produce a "correlation" field that's actually NaN.
    stats["return_mean_abs_diff"] = float(retained["abs_diff"].mean())
    stats["return_median_abs_diff"] = float(retained["abs_diff"].median())
    stats["return_frac_within_1pct"] = float((retained["abs_diff"] < 0.01).mean())

    if len(ret_cmp) > 1:
        stats["return_correlation_before_ticker_gate"] = float(ret_cmp["ret"].corr(ret_cmp["master_ret"]))
        stats["return_frac_within_1pct_before_ticker_gate"] = float((ret_cmp["abs_diff"] < 0.01).mean())
    if len(retained) > 1:
        stats["return_correlation"] = float(retained["ret"].corr(retained["master_ret"]))

        # A residual, per-DAY extreme-difference check on top of the
        # per-ticker gate above -- catches an isolated bad day for an
        # otherwise well-aligned ticker (e.g. a one-off data-entry error),
        # which the per-ticker median (dominated by hundreds of good days)
        # would not flag on its own.
        extreme = retained["abs_diff"] > EXTREME_RETURN_DIFF_THRESHOLD
        stats["return_extreme_diff_threshold"] = EXTREME_RETURN_DIFF_THRESHOLD
        stats["return_extreme_diff_rows"] = int(extreme.sum())
        stats["return_extreme_diff_frac"] = float(extreme.mean())
        trimmed = retained.loc[~extreme]
        if len(trimmed) > 1:
            stats["return_correlation_excl_extreme"] = float(trimmed["ret"].corr(trimmed["master_ret"]))

    return stats, merged


def profile_master_stock_file(csv_path) -> dict:
    """One DuckDB aggregate scan over the WHOLE master_stock.csv file (all
    years, 2011-2025+, not just this project's 2011-2013 window) for every
    whole-file count FINDINGS.md reports: the blank-ticker block's size and
    date-format split, non-blank-ticker coverage (distinct tickers, date
    range), and non-blank-ticker price-quality counts (missing/infinite/
    negative). Kept as ONE query (rather than the several separate scans
    an earlier version of this script ran) so profiling the 303 MB file
    costs one pass, not several -- and kept SEPARATE from
    read_master_stock_raw's windowed, pushed-down read below, since that
    read intentionally only sees this project's 2011-2013 window and can no
    longer answer whole-file questions like "how many mm/dd/yyyy-formatted
    dates exist anywhere in this file."

    Named `..._file` (not `..._dates`, its previous name) since it now
    returns much more than a date range.
    """
    con = duckdb.connect()
    try:
        row = con.execute(
            f"""
            WITH raw AS (
                SELECT Tic AS ticker, Date AS date_text, Price AS price_text
                FROM read_csv(
                    {duckdb_quote_literal(str(csv_path))},
                    header=True, quote='"', escape='"', all_varchar=true
                )
            ),
            classified AS (
                SELECT
                    ticker, date_text, price_text,
                    (ticker IS NULL OR trim(ticker) = '') AS is_blank_ticker,
                    regexp_matches(date_text, '^\\d{{4}}-\\d{{2}}-\\d{{2}}$') AS is_iso_date,
                    regexp_matches(date_text, '^\\d{{2}}/\\d{{2}}/\\d{{4}}$') AS is_mdy_date,
                    try_cast(replace(price_text, ',', '') AS DOUBLE) AS price_num
                FROM raw
            )
            SELECT
                count(*),
                sum(CASE WHEN is_blank_ticker THEN 1 ELSE 0 END),
                sum(CASE WHEN is_blank_ticker AND is_iso_date THEN 1 ELSE 0 END),
                sum(CASE WHEN is_blank_ticker AND is_mdy_date THEN 1 ELSE 0 END),
                count(DISTINCT CASE WHEN NOT is_blank_ticker THEN ticker END),
                min(CASE WHEN NOT is_blank_ticker THEN date_text END),
                max(CASE WHEN NOT is_blank_ticker THEN date_text END),
                sum(CASE WHEN NOT is_blank_ticker AND price_num IS NULL
                         AND (price_text IS NULL OR trim(price_text) = '') THEN 1 ELSE 0 END),
                sum(CASE WHEN NOT is_blank_ticker AND isinf(price_num) THEN 1 ELSE 0 END),
                sum(CASE WHEN NOT is_blank_ticker AND price_num < 0
                         AND NOT isinf(price_num) THEN 1 ELSE 0 END)
            FROM classified
            """
        ).fetchone()
        (
            total_rows, blank_ticker_rows, blank_ticker_iso_rows, blank_ticker_mdy_rows,
            distinct_tickers, min_date_text, max_date_text,
            missing_price_rows, infinite_price_rows, negative_price_rows,
        ) = row
        return {
            "total_rows": int(total_rows),
            "blank_ticker_rows": int(blank_ticker_rows),
            "blank_ticker_iso_rows": int(blank_ticker_iso_rows),
            "blank_ticker_mdy_rows": int(blank_ticker_mdy_rows),
            "non_blank_ticker_rows": int(total_rows - blank_ticker_rows),
            "distinct_tickers": int(distinct_tickers),
            "min_date_text": min_date_text,
            "max_date_text": max_date_text,
            "missing_price_rows": int(missing_price_rows),
            "infinite_price_rows": int(infinite_price_rows),
            "negative_price_rows": int(negative_price_rows),
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# FINDINGS.md: a plain-language writeup of what this script found, for a
# reader with no finance background (per the project's reporting
# convention -- see memory/phase1-equity-pead-plan.md's Reports section).
# ---------------------------------------------------------------------------


def _fmt_int(n) -> str:
    if n is None or (isinstance(n, float) and np.isnan(n)):
        return "n/a"
    return f"{int(n):,}"


def _fmt_pct(x, decimals=2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x * 100:.{decimals}f}%"


def _safe_div(numerator, denominator):
    """numerator / denominator, or None if denominator is 0 -- a pull with
    zero non-null prices (or zero of whatever else is being divided into)
    is a degenerate edge case this report should say "n/a" for, not crash
    on with a ZeroDivisionError."""
    if not denominator:
        return None
    return numerator / denominator


def _fmt_date(d) -> str:
    return pd.Timestamp(d).date().isoformat()


def _markdown_table(df: pd.DataFrame, formats: dict = None) -> str:
    """Hand-rolled Markdown table formatter -- avoids adding the `tabulate`
    dependency that pandas.DataFrame.to_markdown() needs, for what is only
    ever a handful of small spot-check tables in this file."""
    formats = formats or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            fmt = formats.get(c)
            cells.append(fmt(row[c]) if fmt else str(row[c]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_findings_markdown(ctx: dict) -> str:
    dsf = ctx["dsf_stats"]
    dsi = ctx["dsi_stats"]
    delist = ctx["delist_stats"]
    events = ctx["event_stats"]
    master_full = ctx["master_full_stats"]
    master_clean = ctx["master_clean_stats"]
    crossval = ctx["crossval_stats"]

    aapl_fmt = {
        "date": _fmt_date, "prc": lambda v: f"{v:.2f}", "ret": lambda v: f"{v * 100:+.2f}%",
        "shrout": lambda v: _fmt_int(v), "cfacpr": lambda v: f"{v:.1f}",
    }
    aapl_2000_table = _markdown_table(ctx["aapl_2000"], aapl_fmt)
    aapl_2005_table = _markdown_table(ctx["aapl_2005"], aapl_fmt)

    msft_fmt = {
        "date": _fmt_date, "prc": lambda v: f"{v:.2f}",
        "ret": lambda v: f"{v * 100:+.4f}%", "retx": lambda v: f"{v * 100:+.4f}%",
        "shrout": lambda v: _fmt_int(v), "cfacpr": lambda v: f"{v:.1f}",
    }
    msft_table = _markdown_table(ctx["msft_div"], msft_fmt)

    top_delist = ", ".join(
        f"code {code} ({_fmt_int(count)} securities)" for code, count in delist["top_dlstcd_codes"][:3]
    )

    lines = []
    lines.append("# Equity data: cleaning, adjustment status, and cross-validation findings")
    lines.append("")
    lines.append(
        "This document explains what we started with, what we found wrong with it, what we "
        "fixed, and how confident we are in the result. It's written for a reader with no "
        "finance background, so a few terms are explained inline rather than assumed."
    )
    lines.append("")

    # ---- Source data & what "normalized" means -----------------------
    lines.append("## What this covers")
    lines.append("")
    lines.append(
        f"The source is CRSP (Center for Research in Security Prices), a widely used, "
        f"paid academic database of US stock prices and trading data, already pulled from "
        f"WRDS into `data/raw_wrds/crsp/` for {_fmt_int(dsf['input_rows'])} (permno, date) "
        f"rows across {ctx['n_years']} years ({ctx['year_min']}-{ctx['year_max']}). A "
        f"\"permno\" is CRSP's permanent numeric ID for one stock (it never gets reused or "
        f"changed, unlike a ticker symbol, which a company can change or which can be "
        f"reassigned to a different company later). This run covers every permno in "
        f"`data/metadata/resolved_universe_ids.parquet` -- the full OptionMetrics-linked "
        f"universe ({_fmt_int(len(ctx['resolved_permnos']))} distinct permnos) -- not a "
        f"narrower pilot list, since the pilot universe filter is a separate, not-yet-decided "
        f"piece of work."
    )
    lines.append("")
    lines.append(
        "Every date in the raw files was stored as plain text (e.g. the 10 characters "
        "`2010-01-04`), not as an actual date. All of them are now parsed into real dates."
    )
    lines.append("")

    # ---- CRSP conventions ----------------------------------------------
    lines.append("## Cleaning CRSP's own data conventions")
    lines.append("")
    lines.append(
        f"CRSP uses a couple of specific conventions that look like data errors if you don't "
        f"know about them. We checked for both directly in this pull:"
    )
    lines.append("")
    lines.append(
        f"- **Negative prices mean \"no trade happened.\"** When a stock doesn't trade on a "
        f"given day, CRSP records the average of the closing bid and ask price instead, with "
        f"a minus sign in front of it as a marker that it isn't a real trade price. We found "
        f"{_fmt_int(dsf['negative_prc_rows'])} rows like this out of "
        f"{_fmt_int(dsf['nonnull_prc_rows'])} rows with a price at all "
        f"({_fmt_pct(_safe_div(dsf['negative_prc_rows'], dsf['nonnull_prc_rows']))}). We took the "
        f"absolute value so the price is usable, and kept a separate `prc_is_bidask_avg` "
        f"flag column so this is never silently hidden -- anyone using the cleaned data can "
        f"still tell which rows were an estimate rather than a real trade."
    )
    lines.append(
        f"- **Specific negative numbers mean \"missing,\" not \"a huge loss.\"** CRSP "
        f"documents four special codes (-66, -77, -88, -99) that sometimes appear in place "
        f"of a return to mean \"we don't have this,\" for various reasons (no valid prior "
        f"price, the security wasn't trading, etc.) -- not a literal -6,600% return. We "
        f"scanned every one of the {_fmt_int(dsf['input_rows'])} rows for these codes and "
        f"found **{_fmt_int(dsf['ret_sentinel_rows_mapped_to_null'])}** in the return column "
        f"and **{_fmt_int(dsf['retx_sentinel_rows_mapped_to_null'])}** in the dividend-"
        f"excluded return column. The cleaning code still maps them to a proper blank value "
        f"if they ever show up (e.g. in a future re-pull), but in this particular pull, "
        f"missing returns already arrive as a genuine blank rather than one of these old "
        f"placeholder codes."
    )
    lines.append(
        f"- **A handful of rows have an adjustment factor of exactly zero.** "
        f"{_fmt_int(dsf['cfacpr_zero_rows'])} rows have `cfacpr == 0` instead of a positive "
        f"number. This doesn't affect the raw price or return columns, which are unrelated "
        f"fields; it only means `cfacpr` itself can't be used as a divisor for those "
        f"specific rows (see the next section for why we don't do that division at all)."
    )
    lines.append(
        f"- **A small fraction of rows have no ticker symbol on record.** "
        f"{_fmt_int(dsf['rows_with_no_ticker_on_record'])} rows "
        f"({_fmt_pct(_safe_div(dsf['rows_with_no_ticker_on_record'], dsf['output_rows']))} of "
        f"the output) fall inside a stretch of a permno's history where CRSP's own name "
        f"records (`crsp.stocknames`) don't have a ticker on file at all -- this happens for "
        f"example to a company mid-bankruptcy, before it's relisted under a new symbol or "
        f"delisted for good. These rows keep everything else (price, return, and so on); only "
        f"`ticker` is blank, since making one up would be worse than leaving it blank."
    )
    lines.append("")

    # ---- Adjustment finding ---------------------------------------------
    lines.append("## Are prices and returns already adjusted for stock splits and dividends?")
    lines.append("")
    lines.append(
        "This question matters a lot for anything downstream. A stock split (e.g. \"2-for-1\") "
        "doubles the number of shares and roughly halves the price without changing what the "
        "company is actually worth or what an investor actually earned. A dividend is a cash "
        "payment to shareholders, which is part of an investor's real return even though it "
        "doesn't show up as a change in the share price. If a price series isn't adjusted for "
        "these events, a split looks exactly like a 50% crash, and a return series that "
        "ignores dividends understates how much money was actually made. Answering this "
        "wrong would quietly corrupt every return calculation later in this project, so we "
        "checked it directly rather than assuming."
    )
    lines.append("")
    lines.append(
        f"**Short answer: `ret`/`retx` (the return columns) are already adjusted for both "
        f"splits and dividends. `prc` (the raw price column) is not adjusted for splits at "
        f"all -- it shows the actual traded price level, which jumps on a split date.** CRSP "
        f"also provides a cumulative price-adjustment factor, `cfacpr`, which is carried "
        f"through unchanged in the output -- but we deliberately do NOT compute and store a "
        f"derived \"adjusted price\" (`prc / cfacpr`) column. The reason is `cfacpr` itself: "
        f"it's cumulative up to the LAST date in whatever CRSP file we happened to pull, not "
        f"up to the end of this project's own sample. Apple, for example, has "
        f"`cfacpr` = 4.0 on our last pulled date, 2014-06-30 -- but that's because of a "
        f"4-for-1 split in **August 2020**, six years after our data ends. Dividing Apple's "
        f"1995 price by that same 4.0 would misrepresent 1995 in terms of a stock split that "
        f"hadn't happened yet, and wasn't knowable, at any point in this project's window. "
        f"That's exactly the kind of mistake this project has to avoid: using information "
        f"that wasn't actually available at the time."
    )
    lines.append("")
    lines.append(
        f"We can show concretely how much this would matter. Taking "
        f"{ctx['prc_adj_risk']['n_permnos_total']:,} permnos with data anywhere in the pull, "
        f"{_fmt_int(ctx['prc_adj_risk']['n_permnos_cfacpr_ne_1_at_window_end'])} of them end "
        f"the window with `cfacpr` different from 1 -- meaning a re-pull of CRSP a few years "
        f"from now (picking up whatever splits happen between now and then) would silently "
        f"change a derived adjusted price for rows that are supposed to be permanent history. "
        f"And applying a simple \"stock must be at least "
        f"${ctx['prc_adj_risk']['price_threshold']:.0f}\" screen (the kind a liquidity filter "
        f"might use) to {ctx['year_min']} data specifically: using the actual traded price, "
        f"versus using `prc / cfacpr`, "
        f"{_fmt_int(ctx['prc_adj_risk']['wrongly_excluded_rows'])} rows would be wrongly "
        f"excluded and {_fmt_int(ctx['prc_adj_risk']['wrongly_included_rows'])} would be "
        f"wrongly included -- {_fmt_int(ctx['prc_adj_risk']['permnos_affected'])} different "
        f"stocks affected, in {ctx['year_min']} alone. `prc` (the actual price) and `cfacpr` "
        f"(as CRSP reported it) are both still in the output for anyone who wants "
        f"`prc / cfacpr` with that caveat in view; for comparing returns across a split "
        f"safely, use `ret`/`retx`, which don't have this problem (see the evidence below)."
    )
    lines.append("")
    lines.append("**Evidence, whole-universe check.** Every time CRSP's own adjustment factor changes "
                  "for a stock (a signal that a split or similar event happened), we compared two "
                  "numbers for that same day: the \"naive\" return you'd get from just dividing "
                  "the raw price by the previous day's raw price, and CRSP's actual reported "
                  "return.")
    lines.append("")
    lines.append(
        f"- {_fmt_int(events['n_events'])} such adjustment events happened across the whole "
        f"universe and window."
    )
    if events["n_events"]:
        lines.append(
            f"- The naive (unadjusted) return on those days has a median size of "
            f"{_fmt_pct(events['naive_ret_abs_median'])}, and "
            f"{_fmt_pct(events['frac_naive_ret_gt_20pct'])} of them look like a move bigger "
            f"than 20% in a single day -- exactly what you'd expect if you treated a stock "
            f"split as if it were a real price crash or jump."
        )
        lines.append(
            f"- CRSP's actual reported return on those same days has a median size of only "
            f"{_fmt_pct(events['actual_ret_abs_median'])}, and just "
            f"{_fmt_pct(events['frac_actual_ret_gt_20pct'])} exceed 20% -- ordinary daily "
            f"return territory. This is the direct evidence that the return column already "
            f"backs out the split."
        )
    lines.append("")
    lines.append(
        "**Evidence, two named examples.** Apple (permno "
        f"{ctx['aapl_permno']}) did 2-for-1 stock splits on 2000-06-21 and 2005-02-28. In "
        "both cases, the raw price roughly halves overnight and the number of shares "
        "outstanding roughly doubles, while the reported return that day stays a normal, "
        "small daily move:"
    )
    lines.append("")
    lines.append(aapl_2000_table)
    lines.append("")
    lines.append(aapl_2005_table)
    lines.append("")
    lines.append(
        f"Microsoft (permno {ctx['msft_permno']}) paid its first-ever regular quarterly "
        f"dividend, with an ex-dividend date of 2003-02-19 (the first day a buyer no longer "
        f"receives that payment). On that day, `ret` (total return, dividends included) is "
        f"noticeably higher than `retx` (price return only) -- the gap is the dividend's "
        f"contribution to the day's return. (The table also shows `shrout` doubling and "
        f"`cfacpr` halving a day earlier, on 2003-02-18 -- Microsoft's last-ever 2-for-1 "
        f"stock split happened to take effect the day before this dividend, so both events "
        f"are visible in the same short window; this is real history, not a mistake.)"
    )
    lines.append("")
    lines.append(msft_table)
    lines.append("")
    lines.append(
        "No case of unadjusted returns was found anywhere in this check, so there is nothing "
        "here to escalate as a surprise finding -- this is the standard, well-documented CRSP "
        "convention, confirmed directly against this specific pull rather than assumed."
    )
    lines.append("")

    # ---- Benchmark -------------------------------------------------------
    lines.append("## Market benchmark (crsp.dsi)")
    lines.append("")
    if dsi["null_value_rows"] == 0 and dsi["duplicate_date_rows_dropped"] == 0:
        quality_note = "with no missing values in any of its return columns and no duplicated trading days"
    else:
        parts = []
        if dsi["null_value_rows"]:
            parts.append(f"{_fmt_int(dsi['null_value_rows'])} row(s) with a missing return value")
        if dsi["duplicate_date_rows_dropped"]:
            parts.append(f"{_fmt_int(dsi['duplicate_date_rows_dropped'])} duplicated trading-day row(s) dropped")
        quality_note = "with " + " and ".join(parts)
    lines.append(
        f"`crsp.dsi` is one row per trading day, market-wide (not per stock), covering "
        f"{_fmt_int(dsi['rows'])} trading days from {_fmt_date(dsi['date_min'])} to "
        f"{_fmt_date(dsi['date_max'])}, {quality_note}. "
        f"It carries `sprtrn` (the S&P 500 index's own return) and `vwretd` (CRSP's own "
        f"value-weighted return across the whole market, dividends included), along with "
        f"three related variants. Per an earlier decision recorded in the project plan, this "
        f"is now the **primary** benchmark for market-adjusted returns going forward -- it "
        f"covers the entire 1996-2013 window, unlike `data/raw_equity/S&P 500.csv`, which "
        f"only starts in 2010 and is kept only as a narrow secondary cross-check, not used in "
        f"this slice. All five return columns are carried through to "
        f"`market_benchmark.parquet` unchanged aside from the date parsing, since the table "
        f"is small and a later slice may want more than just `sprtrn`."
    )
    lines.append("")

    # ---- Delisting ---------------------------------------------------------
    lines.append("## Delisting data (not used yet)")
    lines.append("")
    if delist["rows_outside_resolved_universe"] == 0:
        universe_note = "all within the resolved universe"
    else:
        universe_note = (
            f"{_fmt_int(delist['rows_outside_resolved_universe'])} of them outside the "
            f"resolved universe (unexpected, since crsp.dsedelist was pulled pre-filtered to "
            f"it -- worth a second look if this number is ever nonzero)"
        )
    lines.append(
        f"`crsp.dsedelist` records {_fmt_int(delist['rows'])} delistings (a company's stock "
        f"leaving CRSP's coverage, usually because of a merger, acquisition, bankruptcy, or "
        f"exchange delisting) between {_fmt_date(delist['date_min'])} and "
        f"{_fmt_date(delist['date_max'])}, {universe_note}. The most common "
        f"reasons on record are {top_delist}. A delisting often comes with one final "
        f"return -- sometimes a large gain or loss -- that isn't part of the regular daily "
        f"price series, and handling that correctly needs its own careful treatment. Per the "
        f"project plan, that treatment is explicitly out of scope for this pilot slice; this "
        f"script only profiles the table for context here. It is a documented limitation, not "
        f"an oversight: any stock that gets delisted mid-study will simply stop appearing in "
        f"the cleaned data on its last trading day, with no final delisting return added."
    )
    lines.append("")

    # ---- master_stock.csv --------------------------------------------------
    lines.append("## Cross-check against master_stock.csv")
    lines.append("")
    lines.append(
        "`data/raw_equity/master_stock.csv` is a separate, non-CRSP daily price file (one row "
        "per ticker per day) that predates this project's WRDS access. Comparing it against "
        "CRSP is a useful independent sanity check, on the logic that if two differently-"
        "sourced price series agree, that's good evidence neither one has a scaling or "
        "adjustment mistake baked in."
    )
    lines.append("")
    window_start = crossval.get("window_start")
    window_end = crossval.get("window_end")
    window_desc = (
        f"{_fmt_date(window_start)} to {_fmt_date(window_end)}"
        if window_start is not None and window_end is not None
        else "a narrow window"
    )
    archive_start_year = pd.Timestamp(ARCHIVE_START_DATE).year
    archive_end_year = pd.Timestamp(ARCHIVE_END_DATE).year
    archive_n_years = archive_end_year - archive_start_year + 1
    lines.append(
        f"**It only overlaps CRSP for a few of the {archive_n_years} years this project "
        f"ultimately cares about ({archive_start_year}-{archive_end_year}).** The file's own "
        f"dates only start {master_full['min_date_text']}, and the archive window we care "
        f"about ends {ARCHIVE_END_DATE}, so this check can only speak to {window_desc} -- not "
        f"the full {archive_start_year}-{archive_end_year} window. That's a real limitation on "
        f"how much this check can prove, not something to gloss over."
    )
    lines.append("")
    lines.append(
        f"**The file also has real data-quality problems, all confined to one place.** "
        f"Looking at the whole file (not just the {window_desc} window above -- it actually "
        f"runs through {master_full['max_date_text']}), there are "
        f"{_fmt_int(master_full['non_blank_ticker_rows'])} rows with a real ticker and "
        f"{_fmt_int(master_full['blank_ticker_rows'])} rows with a blank ticker. Every single "
        f"blank-ticker row turned out to be one contiguous block appended at the very end of "
        f"the file (after the last real ticker's data): "
        f"{_fmt_int(master_full['blank_ticker_mdy_rows'])} of them use "
        f"`mm/dd/yyyy`-formatted dates and the remaining "
        f"{_fmt_int(master_full['blank_ticker_iso_rows'])} use ISO dates, with prices whose "
        f"scale (a handful of cents in 2011 rising into the tens of thousands of dollars by "
        f"the 2020s) doesn't resemble any single stock -- it looks like a stray, unrelated "
        f"price series (an index or similar) was accidentally appended to a single-stock "
        f"file. Dropping every blank-ticker row removes this entire block cleanly, with "
        f"nothing left over to special-case."
    )
    lines.append(
        f"Among the real, non-blank-ticker rows, across the whole file, "
        f"{_fmt_int(master_full['missing_price_rows'])} have a genuinely empty price field "
        f"(typically a ticker that existed in the file before that company's price history "
        f"actually starts), {_fmt_int(master_full['infinite_price_rows'])} contain the "
        f"literal text `inf`/`-inf` (a handful of tickers, apparently from a division-by-zero "
        f"somewhere upstream), and {_fmt_int(master_full['negative_price_rows'])} have a "
        f"negative price with no documented reason the way CRSP's bid/ask convention has one. "
        f"Narrowing to just the {window_desc} window this check actually uses: "
        f"{_fmt_int(master_clean['missing_price_rows'])} missing, "
        f"{_fmt_int(master_clean['infinite_price_rows'])} infinite, and "
        f"{_fmt_int(master_clean['negative_price_rows'])} negative. All of these are treated "
        f"as invalid and left blank rather than guessed at."
    )
    lines.append("")
    if crossval.get("skipped"):
        lines.append(
            "**This run had no overlapping data to compare.** Either the CRSP data on hand "
            "doesn't cover any of the cross-validation years, or master_stock.csv had no "
            "valid rows left in that window after cleaning -- so this cross-check was "
            "skipped rather than run against an empty or misleading sample."
        )
        lines.append("")
        lines.append(
            "**Verdict: this is a secondary, non-blocking check that could not run this "
            "time.** CRSP remains the primary, full-window source regardless."
        )
        lines.append("")
    else:
        lines.extend(_crossval_detail_lines(crossval, archive_start_year))
    lines.append("")

    # ---- Output description -------------------------------------------------
    lines.append("## Output")
    lines.append("")
    lines.append(
        f"`data/normalized_equity/equity_<year>.parquet`, one file per calendar year "
        f"({ctx['year_min']}-{ctx['year_max']}, matching the raw pull's own per-year "
        f"partitioning), each with one row per (permno, date):"
    )
    lines.append("")
    lines.append(
        "- `permno`, `date` (a real date now), `ticker` (CRSP's ticker on record for that "
        "permno as of that specific date, not today's ticker -- tickers change over a "
        "company's life, so this avoids mislabeling old history with a later symbol)\n"
        "- `prc` (cleaned raw price, always positive), `prc_is_bidask_avg` (flag for CRSP's "
        "no-trade convention)\n"
        "- `ret`, `retx` (total and price-only returns; both already split/dividend-adjusted "
        "by CRSP, see above)\n"
        "- `shrout` (shares outstanding, in thousands -- CRSP's convention), `vol` (shares "
        "traded that day), `cfacpr`, `cfacshr` (CRSP's own price/share adjustment factors, "
        "not divided into the price -- see above), carried through for anyone who needs them "
        "later"
    )
    lines.append("")
    lines.append(
        "`data/normalized_equity/market_benchmark.parquet` has one row per trading day: "
        "`date`, `vwretd`, `sprtrn`, `vwretx`, `ewretd`, `ewretx`."
    )
    lines.append("")
    lines.append(
        "Per-year Parquet files (rather than one single file) were chosen simply because "
        "that's how the raw pull already arrives, one year per WRDS query -- there's no "
        "benefit to repartitioning data that's already naturally chunked, and every "
        "downstream tool this project uses (pandas, DuckDB) reads a directory of "
        "year-partitioned Parquet files just as easily as one big file."
    )
    lines.append("")

    # ---- Limitations ---------------------------------------------------------
    lines.append("## What's deferred, not done here")
    lines.append("")
    lines.append(
        "- Delisting returns (see above) -- explicitly out of scope for this pilot slice.\n"
        "- The pilot universe filter -- a separate, not-yet-decided piece of work; this "
        "output covers the full resolved universe so it doesn't need to be re-run once that "
        "filter is settled.\n"
        "- `S&P 500.csv` as a secondary benchmark cross-check (2010-2013 only) -- not built "
        "in this slice; `crsp.dsi` is the primary benchmark and covers the full window.\n"
        "- Compustat and I/B/E/S cleaning -- separate slices.\n"
        "- A derived split-adjusted price level (`prc / cfacpr`) -- deliberately not "
        "provided; see the adjustment-status section above for why it would be unsafe."
    )
    lines.append("")

    return "\n".join(lines)


def _crossval_detail_lines(crossval: dict, archive_start_year) -> list:
    """The master_stock.csv cross-validation's detailed findings (matched
    tickers, the per-ticker misalignment gate, and the resulting verdict)
    -- split out from build_findings_markdown into its own function only
    so the "no comparable data this run" early exit doesn't have to
    duplicate or awkwardly re-indent this whole block."""
    lines = []
    lines.append(
        f"One more master_stock.csv problem only shows up at this step: "
        f"{_fmt_int(crossval['master_ticker_date_collisions_dropped'])} rows, across "
        f"{_fmt_int(crossval['master_ticker_date_collision_tickers'])} tickers, have "
        f"**two different prices recorded for the same ticker on the same day** (e.g. "
        f"one ticker has exactly two rows, at two unrelated price levels, for every "
        f"single trading day in its history). There's no way to tell which of the two "
        f"is right, so both are dropped from this comparison rather than guessing."
    )
    lines.append(
        f"**{_fmt_int(crossval['matched_rows'])} (ticker, date) rows matched** across "
        f"{_fmt_int(crossval['matched_distinct_tickers'])} tickers, out of "
        f"{_fmt_int(crossval['crsp_distinct_tickers_in_window'])} CRSP tickers and "
        f"{_fmt_int(crossval['master_distinct_tickers_in_window'])} master_stock tickers "
        f"active in the window -- only "
        f"{_fmt_pct(_safe_div(crossval['matched_distinct_tickers'], crossval['crsp_distinct_tickers_in_window']))} "
        f"of CRSP's tickers found a match. Some of that is real ticker churn (renames, "
        f"reused symbols), but there's a structural reason to expect an undercount too: "
        f"master_stock.csv only has tickers that still existed when it was downloaded, so "
        f"this check is skewed toward company survivors and can say nothing about names "
        f"that didn't make it that far."
    )
    lines.append("")
    lines.append(
        "**A second, more serious alignment problem surfaced after joining, and it's the "
        "single most important finding of this cross-check.** master_stock.csv appears to "
        "label its entire price history with each ticker's CURRENT (as-of-download) symbol, "
        "not the symbol it traded under on that historical date -- unlike the CRSP side of "
        "this comparison, which is deliberately point-in-time (see the ticker note above). "
        "A ticker that's been reassigned to a different company since 2011-2013 then "
        "compares two completely unrelated companies' prices under the same symbol."
    )
    n_quarantined = crossval.get("tickers_quarantined_misaligned", 0)
    rows_quarantined = crossval.get("rows_quarantined_misaligned", 0)
    lines.append(
        f"We catch this per ticker (not with one overall statistic, which can't see a "
        f"problem confined to a handful of names): for every matched ticker, we look at the "
        f"typical (median) size of the day-to-day return disagreement between the two "
        f"sources. A ticker that really is the same company matches almost exactly every "
        f"day; a ticker pointing at two different companies does not, even though it might "
        f"still occasionally agree by coincidence. On this data the two groups are cleanly "
        f"separated with a gap between them, so any ticker whose typical disagreement "
        f"exceeds {_fmt_pct(TICKER_ALIGNMENT_MAX_MEDIAN_RET_DIFF, 2)} (or that has too few "
        f"comparable days to judge) is set aside rather than pooled in with the rest: "
        f"**{_fmt_int(n_quarantined)} tickers ({_fmt_int(rows_quarantined)} rows) set aside "
        f"this way.**"
    )
    examples = crossval.get("quarantined_ticker_examples") or []
    if examples:
        example_bits = "; ".join(
            f"`{t}` (CRSP price around ${crsp_p:,.2f}, master_stock.csv price around "
            f"${master_p:,.2f})"
            for t, _n, crsp_p, master_p in examples[:3]
        )
        lines.append(
            f"For example: {example_bits}. These aren't small rounding differences -- they're "
            f"different companies."
        )
    lines.append("")

    retained_rows = crossval.get("return_comparison_rows_retained", 0)
    if retained_rows:
        lines.append(
            f"**Comparing day-over-day returns for the remaining, correctly-aligned "
            f"tickers** (the more meaningful comparison to begin with, since the two files "
            f"use different price-adjustment conventions and raw price levels aren't "
            f"directly comparable) over {_fmt_int(retained_rows)} matched days: a median "
            f"absolute difference of {crossval['return_median_abs_diff']:.2e}, and "
            f"{_fmt_pct(crossval['return_frac_within_1pct'])} of days agreeing to within 1 "
            f"percentage point."
        )
        if crossval["return_median_abs_diff"] < 1e-4:
            lines.append(
                "That difference is small enough (a few hundred-thousandths of a "
                "percentage point or less) to be worth a second thought: two genuinely "
                "independent data vendors measuring the same real-world price rarely agree "
                "to that many decimal places purely by chance. It's plausible that "
                "master_stock.csv was itself built from CRSP or a common upstream source at "
                "some point, rather than being fully independent -- which would make this "
                "check better evidence of \"these two files consistently apply the same "
                "convention\" than of \"two unrelated measurements of the real world agree,\" "
                "and is worth keeping in mind rather than treating this as full independent "
                "confirmation."
            )
        if crossval.get("return_correlation") is None:
            lines.append(
                "Too few matched days to compute a meaningful correlation, though the "
                "difference above is still informative."
            )
        else:
            corr = crossval["return_correlation"]
            corr_before = crossval.get("return_correlation_before_ticker_gate")
            lines.append(
                f"Correlation across the retained, correctly-aligned tickers is "
                f"{corr:.4f}"
                + (
                    f" (versus {corr_before:.4f} before setting the misaligned tickers "
                    f"aside -- most of that gap is exactly the {_fmt_int(n_quarantined)} "
                    f"misaligned tickers above, not a broad problem with the rest of the "
                    f"data)."
                    if corr_before is not None else "."
                )
            )
            extreme_rows = crossval.get("return_extreme_diff_rows", 0)
            if extreme_rows:
                corr_trimmed = crossval.get("return_correlation_excl_extreme")
                lines.append(
                    f"A residual {_fmt_int(extreme_rows)} days "
                    f"({_fmt_pct(crossval.get('return_extreme_diff_frac', 0.0))}) still show "
                    f"a difference bigger than {_fmt_pct(EXTREME_RETURN_DIFF_THRESHOLD, 0)} "
                    f"even among the correctly-aligned tickers -- an isolated bad day here "
                    f"and there rather than a wrong-company problem. Excluding those specific "
                    f"days moves the correlation to "
                    + (f"{corr_trimmed:.4f}." if corr_trimmed is not None else "a similar figure.")
                )
    else:
        lines.append(
            "There wasn't enough matched, correctly-aligned, non-blank data to compute a "
            "meaningful return comparison."
        )
    lines.append("")

    positive_verdict = (
        retained_rows >= 30
        and crossval.get("return_frac_within_1pct") is not None
        and crossval["return_frac_within_1pct"] >= 0.95
    )
    if positive_verdict:
        lines.append(
            f"**Verdict: this is a secondary, non-blocking check, and once the misaligned "
            f"tickers and master_stock.csv's own row-level data-quality problems (both "
            f"documented above) are set aside, it holds up well for the years it can "
            f"actually test** ({_fmt_pct(crossval['return_frac_within_1pct'])} of retained "
            f"days agree within 1 percentage point). It cannot say anything about "
            f"{archive_start_year}-2010, which is most of this project's window -- CRSP "
            f"remains the primary, and only full-window, source."
        )
    else:
        lines.append(
            "**Verdict: this check is inconclusive for this run.** Either too few days "
            "survived cleaning and the per-ticker alignment gate to say anything, or the "
            "agreement among the ones that did survive wasn't strong. This is a secondary, "
            "non-blocking check either way -- CRSP remains the primary, full-window source "
            "regardless of this check's outcome."
        )
    return lines


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

CROSSVAL_YEARS = (2011, 2012, 2013)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Every equity_<year>.parquet this run is about to write is about to be
    # replaced -- but a YEAR NOT IN THIS RUN's discover_dsf_years() (e.g. a
    # later re-pull that covers fewer years, or a renamed/removed raw file)
    # would otherwise leave a stale prior-run file behind that a downstream
    # equity_*.parquet glob would silently mix in alongside the current
    # run's files. Pruned unconditionally up front rather than tracked with
    # a completeness marker/manifest -- this is a fully regenerable local
    # transform (see module docstring), so "stale files never survive a
    # fresh run" is a simpler invariant than "check a marker before
    # trusting what's on disk."
    for stale in OUT_DIR.glob("equity_*.parquet"):
        stale.unlink()

    print("Loading resolved universe PERMNO list ...", flush=True)
    resolved_permnos = load_resolved_permnos()
    print(f"  {len(resolved_permnos):,} resolved PERMNOs.", flush=True)

    print("Loading crsp.stocknames for point-in-time ticker lookup ...", flush=True)
    stocknames = load_stocknames_for_ticker_lookup(RAW_CRSP_DIR / "stocknames.parquet")
    aapl_permno = lookup_permno_for_ticker(stocknames, AAPL_TICKER)
    msft_permno = lookup_permno_for_ticker(stocknames, MSFT_TICKER)

    years = discover_dsf_years()
    if not years:
        raise SystemExit(f"FATAL: no dsf_<year>.parquet files found under {RAW_CRSP_DIR}")
    print(f"Found dsf year files: {years[0]}-{years[-1]} ({len(years)} years).", flush=True)

    empty_aapl_spot = pd.DataFrame(columns=["date", "prc", "ret", "shrout", "cfacpr"])
    empty_msft_spot = pd.DataFrame(columns=["date", "prc", "ret", "retx", "cfacpr"])

    stats_by_year = {}
    event_frames_by_year = []
    event_carryover = None  # this permno's last-seen row so far; see last_row_per_permno
    crossval_frames = []
    aapl_spot_by_year = {}  # year -> spot-check DataFrame, keyed explicitly (not positionally)
    msft_spot = empty_msft_spot
    first_year_prc_cfacpr = None  # captured on years[0] only, for the prc_adj look-ahead illustration

    for year in years:
        dsf_path = RAW_CRSP_DIR / f"dsf_{year}.parquet"
        raw = read_parquet_with_projection(dsf_path, DSF_CONSUMED_COLUMNS)
        cleaned, stats = clean_dsf_year(raw, resolved_permnos, stocknames)
        stats_by_year[year] = stats

        out_path = OUT_DIR / f"equity_{year}.parquet"
        cleaned.to_parquet(out_path, index=False)
        print(f"  wrote {out_path.name}: {len(cleaned):,} rows", flush=True)

        if year == years[0]:
            first_year_prc_cfacpr = cleaned[["permno", "prc", "cfacpr"]].copy()

        # Adjustment-event detection, one year at a time (see
        # last_row_per_permno's docstring for why this is equivalent to
        # running detect_adjustment_events once over the whole 20-year,
        # 20M-row panel, without ever holding that whole panel in memory):
        # only the carryover row for a permno not seen since a prior year
        # persists across iterations.
        year_event_cols = cleaned[EVENT_CHECK_COLUMNS]
        combined = year_event_cols if event_carryover is None else safe_concat(
            [event_carryover, year_event_cols], ignore_index=True
        )
        event_frames_by_year.append(detect_adjustment_events(combined))
        event_carryover = update_event_carryover(event_carryover, year_event_cols)

        if year in CROSSVAL_YEARS:
            crossval_frames.append(cleaned[["permno", "ticker", "date", "prc", "ret"]])
        if year in AAPL_SPLIT_YEAR_DATES and (cleaned["permno"] == aapl_permno).any():
            aapl_spot_by_year[year] = split_spot_check(cleaned, aapl_permno, AAPL_SPLIT_YEAR_DATES[year])
        if year == 2003 and (cleaned["permno"] == msft_permno).any():
            msft_spot = split_spot_check(cleaned, msft_permno, MSFT_DIVIDEND_SPOT_DATE)

    dsf_stats = aggregate_year_stats(stats_by_year)

    print("Checking split/dividend-adjustment evidence ...", flush=True)
    events = safe_concat(event_frames_by_year, ignore_index=True)
    event_stats = summarize_adjustment_events(events)
    aapl_spot_2000 = aapl_spot_by_year.get(2000, empty_aapl_spot)
    aapl_spot_2005 = aapl_spot_by_year.get(2005, empty_aapl_spot)
    # event_carryover now holds each permno's single most-recently-seen row
    # across the whole pull (see last_row_per_permno) -- exactly what's
    # needed as "this permno's cfacpr at the end of the window we pulled."
    prc_adj_risk_stats = summarize_prc_adj_lookahead_risk(first_year_prc_cfacpr, event_carryover[["permno", "cfacpr"]])

    print("Cleaning crsp.dsi market benchmark ...", flush=True)
    dsi_path = RAW_CRSP_DIR / "dsi.parquet"
    dsi_raw = pd.read_parquet(dsi_path)
    require_columns(dsi_raw, DSI_CONSUMED_COLUMNS, str(dsi_path))
    benchmark, dsi_stats = clean_dsi(dsi_raw)
    benchmark.to_parquet(OUT_DIR / "market_benchmark.parquet", index=False)
    print(f"  wrote market_benchmark.parquet: {len(benchmark):,} rows", flush=True)

    print("Summarizing crsp.dsedelist (context only, not written to output) ...", flush=True)
    delist_stats = summarize_dsedelist(RAW_CRSP_DIR / "dsedelist.parquet", resolved_permnos)

    print("Cleaning and cross-validating data/raw_equity/master_stock.csv ...", flush=True)
    master_full_stats = profile_master_stock_file(MASTER_STOCK_PATH)
    master_raw = read_master_stock_raw(MASTER_STOCK_PATH, end_date=ARCHIVE_END_DATE)
    master_clean, master_clean_stats = clean_master_stock_frame(master_raw, end_date=ARCHIVE_END_DATE)

    # Guarded rather than assumed: a CRSP pull that happens not to cover any
    # of CROSSVAL_YEARS (e.g. a partial/older re-pull), or a master_stock.csv
    # with no valid rows left after cleaning, would otherwise crash this
    # step (pd.concat([]) on an empty list raises) instead of just skipping
    # a secondary, non-blocking check.
    if crossval_frames and len(master_clean) > 0:
        crsp_crossval = safe_concat(crossval_frames, ignore_index=True)
        window_start = max(pd.Timestamp(master_clean["date"].min()), pd.Timestamp(crsp_crossval["date"].min()))
        window_end = pd.Timestamp(ARCHIVE_END_DATE)
        crossval_stats, _crossval_merged = cross_validate_master_stock(
            crsp_crossval, master_clean, window_start, window_end
        )
    else:
        print(
            "  skipping master_stock.csv cross-validation: no CRSP data for "
            f"{CROSSVAL_YEARS} and/or no valid master_stock.csv rows in window.",
            flush=True,
        )
        crossval_stats = {"skipped": True}

    ctx = {
        "n_years": len(years), "year_min": years[0], "year_max": years[-1],
        "resolved_permnos": resolved_permnos,
        "dsf_stats": dsf_stats,
        "dsi_stats": dsi_stats,
        "delist_stats": delist_stats,
        "event_stats": event_stats,
        "aapl_permno": aapl_permno, "msft_permno": msft_permno,
        "aapl_2000": aapl_spot_2000,
        "aapl_2005": aapl_spot_2005,
        "msft_div": msft_spot,
        "master_full_stats": master_full_stats,
        "master_clean_stats": master_clean_stats,
        "crossval_stats": crossval_stats,
        "prc_adj_risk": prc_adj_risk_stats,
    }

    findings_md = build_findings_markdown(ctx)
    findings_path = OUT_DIR / "FINDINGS.md"
    findings_path.write_text(findings_md)
    print(f"Wrote {findings_path}", flush=True)
    print("DONE.", flush=True)


if __name__ == "__main__":
    main()
