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
# Derived from the date above (not a second literal): main() uses this to
# decide which dsf_<year>.parquet file to look in, so a future edit to
# MSFT_DIVIDEND_SPOT_DATE can't silently leave a stale, disconnected year
# behind (AAPL's equivalent check already avoids this via dict keys --
# see AAPL_SPLIT_YEAR_DATES).
MSFT_DIVIDEND_SPOT_YEAR = pd.Timestamp(MSFT_DIVIDEND_SPOT_DATE).year

# classify_adjustment_status's thresholds (see its docstring for the
# measured justification: this pull's real value is 1.86e-04, two clear
# orders of magnitude below even a 10%-unadjusted synthetic pull's 0.099).
NAIVE_RET_BIG_MOVE_THRESHOLD = 0.20
UNADJUSTED_RET_MATCH_TOL = 0.01
ADJUSTMENT_VERDICT_MIN_EVENTS = 30
# detect_adjustment_events' min_change default -- a cfacpr move smaller
# than this is rounding drift, not a real split/distribution.
CFACPR_EVENT_MIN_CHANGE = 0.01

# classify_adjustment_status's dividend and return-scale discriminators
# (added after round-3 review found the single split-based discriminator
# above could not speak to dividends at all, or to a mis-scaled `ret`
# column -- see classify_adjustment_status's docstring for the measured
# margins behind each threshold below).
#
# cfacpr never changes for an ordinary cash dividend (it's a split/
# distribution factor), so the split discriminator above is structurally
# blind to dividends -- the only pull-wide dividend signal in this data is
# `ret` disagreeing with `retx` at all. And separately, "is `ret` adjusted"
# is a different question from "is `ret` even a plausible fractional daily
# return" -- a discriminator built only around "does ret match the naive
# move" cannot catch a `ret` column delivered in the wrong units (e.g.
# percent, 1.0 meaning +100%), since that failure mode is maximally FAR
# from the naive move either way.
PULL_WIDE_VERDICT_MIN_ROWS = 100_000  # this pull: 20,244,672 non-null ret/retx rows, 202x over
DIVIDEND_EVIDENCE_MIN_DIFF = 1e-9  # also used by cross_validate_master_stock's ex_div filter
DIVIDEND_EVIDENCE_MIN_FRACTION = 1e-4  # this pull: 6.706e-03 (0.67%), 67x over; a ret-copied-from-retx pull measures exactly 0
# Wrong-direction ceiling for the dividend check (round-4 review finding:
# the disagreement-rate check above is direction-blind by construction, so
# it cannot tell "ret genuinely includes dividends" from "ret and retx are
# swapped/mislabeled" -- both produce plenty of ret != retx rows). A
# dividend is a cash inflow, so `ret` (total return) should essentially
# never be LESS than `retx` (price-only): this pull measures EXACTLY 0
# such rows out of the 135,756 disagreements (0%), 100x below this floor;
# a pull with ret/retx swapped measures 1.0 (100%) -- every disagreement
# in the wrong direction.
DIVIDEND_EVIDENCE_MAX_WRONG_DIRECTION_FRACTION = 0.01
RET_PLAUSIBLE_MIN_FRAC_ABS_GT_BIG_MOVE = 1e-4  # this pull: 3.842e-03 (0.38%), 38x over; an all-zero/constant ret column measures 0
RET_PLAUSIBLE_MAX_FRAC_ABS_GT_BIG_MOVE = 0.05  # this pull: 3.842e-03, 13x under; a percent-units ret column (1.0 == 100%) measures ~0.8-1.0
RET_PLAUSIBLE_MAX_EVENT_ABS_MEDIAN = 0.10  # this pull's event-day median |ret|: 0.0222, 4.5x under; percent-units measures ~1.0

# A single-day return difference bigger than this between CRSP and
# master_stock.csv is not plausibly "the same real trading day, measured
# slightly differently" -- it's used only to separate out a small number of
# implausible master_stock.csv values (see cross_validate_master_stock) when
# reporting a trimmed correlation alongside the untrimmed one.
EXTREME_RETURN_DIFF_THRESHOLD = 0.10

# Below this, retx and master_ret agree closer than two independent
# vendors plausibly would by chance -- this pull: 2.51e-07.
CROSSVAL_SUSPICIOUSLY_CLOSE_MEDIAN_DIFF = 1e-4

# Per-ticker alignment gate for cross_validate_master_stock (see its
# docstring for why this has to be per ticker, not a single pooled check).
#
# This is a PRICE-RATIO-STEP test, not a return-difference test: for each
# ticker, over consecutive matched (date-adjacent) rows, compare CRSP's
# day-over-day price change to master_stock.csv's. A version of this gate
# that compared day-over-day RETURNS (ret vs. a master return computed
# with a calendar-day gap tolerance) was tried first and produced three
# confirmed false positives -- HVT, CENTA, and MKC were quarantined even
# though their CRSP/master price ratio is EXACTLY constant (1.000, 1.000,
# and 0.500 respectively) on every single matched day, because a handful
# of their comparable "return" pairs actually spanned more than one
# trading day (master_stock.csv has sparser date coverage than CRSP for
# these three), which the return-based gate had no way to distinguish
# from a real mismatch. The price-ratio-step test sidesteps this
# entirely: both sides of each step are computed over the exact same pair
# of matched dates, so a coverage gap can never masquerade as a price
# disagreement.
#
# A plain ratio-stability test (e.g. coefficient of variation of
# master_price / prc) was also tried and rejected: CRSP's prc is NOT
# split-adjusted (see the adjustment-status section) while
# master_stock.csv's appears to be, so a real split during the window
# makes a legitimately-matching ticker's ratio step by exactly 2x/3x/5x/
# 10x mid-window (e.g. one correctly-matching ticker's ratio has a
# max-to-min spread of 19x while still agreeing on returns to machine
# precision) -- a ratio-level test would wrongly flag every such ticker.
# Taking the STEP (pct_change) of each side cancels out a constant or
# piecewise-constant scale factor the same way a return does, without
# reusing the return columns.
#
# On this project's real data the two populations are cleanly separated:
# every genuinely-matched ticker has a median price-step disagreement of
# 6.9e-04 or less (worst case measured), while every ticker where
# master_stock.csv's current ticker label evidently points at a
# different company measures at 3.8e-03 or more. 1e-3 sits in that gap.
TICKER_ALIGNMENT_MAX_MEDIAN_PRICE_STEP_DIFF = 1e-3
# A ticker with fewer than this many comparable (consecutive-date) price
# pairs doesn't have enough data for the median above to mean anything --
# treated as unverifiable and quarantined separately from "misaligned"
# rather than folded into the same headline count.
TICKER_ALIGNMENT_MIN_PRICE_PAIRS = 2

# _crossval_detail_lines' positive/negative verdict gate -- a sample-size
# floor, numerically the same as ADJUSTMENT_VERDICT_MIN_EVENTS today but a
# distinct constant since the two thresholds guard unrelated checks and
# have no reason to move together.
CROSSVAL_VERDICT_MIN_RETAINED_ROWS = 30
# Sanity floor, not a precision target: frac-within-1pct alone can't rule
# out a pathological case (high agreement-within-1pt with near-zero
# correlation); this pull measures 0.9998, 2x over.
CROSSVAL_VERDICT_MIN_CORRELATION = 0.5


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
        # The diagnostic schema probe below can itself raise (e.g. path is
        # corrupt/truncated, not just missing a column) -- in that case
        # re-raise the ORIGINAL exception `e`, not the probe's, so a
        # corrupt file surfaces its own real error instead of being masked
        # by a confusing failure from the code that only exists to produce
        # a nicer message for the missing-column case.
        try:
            available = pq.ParquetFile(path).schema_arrow.names  # schema only, no data read
        except Exception:
            raise e from None
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

    # Pull-wide dividend and return-scale evidence for classify_adjustment_
    # status (see its docstring): computed here, on the post-sentinel-
    # mapping, post-dedup frame, so a sentinel code or a duplicate row can
    # never fake a ret/retx disagreement or a big-move count.
    ret_nonnull = df["ret"].notna()
    both_nonnull = ret_nonnull & df["retx"].notna()
    ret_minus_retx = df["ret"] - df["retx"]
    stats["ret_nonnull_rows"] = int(ret_nonnull.sum())
    stats["ret_and_retx_nonnull_rows"] = int(both_nonnull.sum())
    stats["ret_ne_retx_rows"] = int((both_nonnull & (ret_minus_retx.abs() > DIVIDEND_EVIDENCE_MIN_DIFF)).sum())
    # Directional sanity check: a real dividend is a cash inflow, so `ret`
    # (total return) should very rarely be LESS than `retx` (price-only) --
    # a sizeable count here would mean ret/retx are mislabeled or swapped.
    stats["ret_lt_retx_rows"] = int((both_nonnull & (-ret_minus_retx > DIVIDEND_EVIDENCE_MIN_DIFF)).sum())
    stats["ret_abs_gt_big_move_rows"] = int((ret_nonnull & (df["ret"].abs() > NAIVE_RET_BIG_MOVE_THRESHOLD)).sum())

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

    # Same CRSP missing-return sentinel mapping clean_dsf_year applies to
    # dsf.ret/dsf.retx (see CRSP_MISSING_RETURN_CODES) -- dsi's return
    # columns use the same CRSP convention, and market_benchmark.parquet is
    # the series every downstream abnormal-return calculation subtracts, so
    # a sentinel surviving here is strictly worse than one surviving in
    # dsf. Applied before null_value_rows below so a sentinel is counted as
    # the missing value it actually represents.
    value_cols = ["vwretd", "sprtrn", "vwretx", "ewretd", "ewretx"]
    n_sentinel = 0
    for col in value_cols:
        df[col] = df[col].astype("float64")
        cleaned_col, n = map_missing_return_codes(df[col])
        df[col] = cleaned_col
        n_sentinel += n
    stats["sentinel_rows_mapped_to_null"] = n_sentinel
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


def detect_adjustment_events(df: pd.DataFrame, min_change: float = CFACPR_EVENT_MIN_CHANGE) -> pd.DataFrame:
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
    """Summarize detect_adjustment_events' output. `n_events` describes
    the WHOLE events table (every cfacpr change, however small); every
    other stat below is scoped to the "big naive move" subset specifically
    (naive_ret bigger than NAIVE_RET_BIG_MOVE_THRESHOLD) and is only
    present when that subset is non-empty.

    Round-4 review finding: an earlier version of this function computed
    naive_ret_abs_median/actual_ret_abs_median/frac_..._gt_20pct over ALL
    events, but both the report and classify_adjustment_status's scale
    check consumed them as if they described the big-move subset
    specifically -- for a fraction whose event IS "bigger than 20%", that
    subset's own frac-over-20% is 100% by construction, so a stat
    computed over the wrong population and presented as the right one
    produced a sentence that couldn't be true of its own stated subject.
    Scoping every one of these stats to the same `big` mask used to
    define the subset in the first place makes that impossible rather
    than merely wrong-by-omission. (naive_ret_abs_mean/actual_ret_abs_mean
    were also dropped entirely here: computed but never read by any
    report text or verdict logic, only echoed back by a test fixture.)
    """
    stats = {"n_events": int(len(events))}
    if len(events) == 0:
        return stats

    # The actual discriminator classify_adjustment_status uses: among
    # events where the RAW price alone implies a big move (a real
    # split/distribution, not noise), how often does CRSP's reported
    # `ret` land close to that same big move (meaning `ret` did NOT back
    # the split out -- i.e. NOT adjusted) versus land nowhere near it
    # (meaning it DID -- i.e. adjusted)?
    big = events["naive_ret"].abs() > NAIVE_RET_BIG_MOVE_THRESHOLD
    stats["n_events_with_big_naive_move"] = int(big.sum())
    if big.any():
        big_events = events.loc[big]
        stats["naive_ret_abs_median"] = float(big_events["naive_ret"].abs().median())
        stats["actual_ret_abs_median"] = float(big_events["ret"].abs().median())
        stats["frac_actual_ret_gt_big_move"] = float((big_events["ret"].abs() > NAIVE_RET_BIG_MOVE_THRESHOLD).mean())
        matches_naive = (big_events["ret"] - big_events["naive_ret"]).abs() <= UNADJUSTED_RET_MATCH_TOL
        stats["frac_big_naive_events_ret_equals_naive"] = float(matches_naive.mean())
    return stats


def classify_adjustment_status(event_stats: dict, dsf_stats: dict) -> dict:
    """Whether CRSP's returns in THIS pull appear split-adjusted,
    dividend-adjusted, AND scaled as an ordinary fractional daily return --
    three separate claims, each derived from its own evidence rather than
    assumed, and rather than one discriminator standing in for all three
    (round-3 review finding: a single split-based check cannot say anything
    about dividends, and cannot catch a `ret` column delivered in
    nonsensical units, since that failure mode is maximally FAR from the
    split discriminator's "matches the naive move" signal either way).

    Returns a dict:
      - "split_status": "adjusted" / "not_adjusted" / "mixed" / "inconclusive"
      - "dividend_status": "adjusted" / "not_adjusted" / "inconclusive"
      - "ret_scale_status": "plausible" / "implausible" / "inconclusive"
      - "overall": "adjusted" only if all three of the above confirm
        cleanly; "flagged" if any of them actively contradicts adjustment
        or plausibility; "inconclusive" otherwise (not enough evidence
        either way on at least one check, but nothing actively wrong).
      - "reasons": one human-readable string per check that did not
        confirm "adjusted"/"plausible", naming the check and the number
        behind it.

    1. split_status. The discriminator is frac_big_naive_events_ret_equals_
       naive: among events where the raw price alone implies a move bigger
       than NAIVE_RET_BIG_MOVE_THRESHOLD (a real split/distribution, not
       day-to-day noise), what fraction of the time does CRSP's actual
       `ret` land within UNADJUSTED_RET_MATCH_TOL of that same raw move?
       If `ret` is already adjusted, it should almost never land there
       (this project's real pull: 1 of 5,363 such events, 1.86e-04); if
       `ret` were simply the raw, unadjusted price change, it would land
       there essentially always (a synthetic ret=naive_ret pull measures
       1.0); a pull that's a 50/50 mix of adjusted and unadjusted names
       measures 0.504; even a pull that's just 10% unadjusted measures
       0.099 -- two clear orders of magnitude above the real, fully-
       adjusted pull's 1.86e-04. ADJUSTMENT_VERDICT_MIN_EVENTS guards
       against a small pull (or a data window with few real corporate
       actions) producing a confident-sounding verdict off a handful of
       events.

    2. dividend_status. `cfacpr` never changes for an ordinary cash
       dividend (it's a split/distribution factor), so split_status's
       event-based check above is structurally blind to dividends -- the
       only pull-wide dividend signal in this data is `ret` disagreeing
       with `retx` at all. If `ret` truly reinvests dividends and `retx`
       truly excludes them, the two columns must disagree on genuine
       ex-dividend days and agree everywhere else. This project's real
       pull: they disagree on 0.6706% of rows with both populated -- 67x
       DIVIDEND_EVIDENCE_MIN_FRACTION's floor. A pull where `ret` was
       mistakenly populated with `retx`'s values (i.e. NOT dividend-
       adjusted) measures exactly 0 here. A SECOND, independent dividend
       check (round-4 review finding): the disagreement-rate check above
       is direction-blind by construction, so it cannot distinguish "ret
       genuinely includes dividends" from "ret and retx are swapped or
       mislabeled" -- both produce plenty of ret != retx rows. A dividend
       is a cash inflow, so `ret` should essentially never be LESS than
       `retx`; this pull's real wrong-direction rate is exactly 0% (0 of
       135,756 disagreements), while a fully swapped ret/retx pull
       measures 100% -- DIVIDEND_EVIDENCE_MAX_WRONG_DIRECTION_FRACTION
       sits far below that.

    3. ret_scale_status. Independent of whether `ret` is adjusted, is it
       even a plausible fractional daily return? Two checks: the fraction
       of all `ret` values bigger than NAIVE_RET_BIG_MOVE_THRESHOLD must
       sit in a plausible band (this pull: 0.384%; a percent-units column
       would blow past the ceiling, an all-zero column below the floor);
       and, when split_status is judgeable, the event-day |ret| median
       must not be absurdly large (this pull: 2.22%; percent-units would
       be ~100x too big).
    """
    reasons = []

    n_events = event_stats.get("n_events", 0)
    n_big = event_stats.get("n_events_with_big_naive_move", 0)
    if n_events == 0 or n_big < ADJUSTMENT_VERDICT_MIN_EVENTS:
        split_status = "inconclusive"
        reasons.append(
            f"split check: only {n_big} big-raw-move adjustment event(s) found across the "
            f"whole pull (need at least {ADJUSTMENT_VERDICT_MIN_EVENTS}), so split-adjustment "
            f"status could not be checked directly"
        )
    else:
        frac = event_stats["frac_big_naive_events_ret_equals_naive"]
        if frac <= 0.01:
            split_status = "adjusted"
        elif frac >= 0.99:
            split_status = "not_adjusted"
            reasons.append(
                f"split check: `ret` matches the raw, unadjusted price move on {frac:.1%} of "
                f"big-raw-move events -- consistent with `ret` NOT being split-adjusted"
            )
        else:
            split_status = "mixed"
            reasons.append(
                f"split check: `ret` matches the raw, unadjusted price move on {frac:.1%} of "
                f"big-raw-move events -- neither cleanly adjusted nor cleanly unadjusted"
            )

    both_nonnull = dsf_stats.get("ret_and_retx_nonnull_rows", 0)
    if both_nonnull < PULL_WIDE_VERDICT_MIN_ROWS:
        dividend_status = "inconclusive"
        reasons.append(
            f"dividend check: only {both_nonnull} row(s) with both `ret` and `retx` populated "
            f"(need at least {PULL_WIDE_VERDICT_MIN_ROWS}), so dividend-adjustment status "
            f"could not be checked directly"
        )
    else:
        n_ne = dsf_stats.get("ret_ne_retx_rows", 0)
        frac_ne = n_ne / both_nonnull
        # n_ne could be 0 (frac_ne < DIVIDEND_EVIDENCE_MIN_FRACTION always
        # true in that case, short-circuiting before this divides by it).
        frac_wrong_dir = (dsf_stats.get("ret_lt_retx_rows", 0) / n_ne) if n_ne else 0.0
        if frac_ne < DIVIDEND_EVIDENCE_MIN_FRACTION:
            dividend_status = "not_adjusted"
            reasons.append(
                f"dividend check: `ret` and `retx` disagree on only {frac_ne:.4%} of rows with "
                f"both populated -- too rare to be real dividends, consistent with `ret` NOT "
                f"including dividends"
            )
        elif frac_wrong_dir > DIVIDEND_EVIDENCE_MAX_WRONG_DIRECTION_FRACTION:
            dividend_status = "not_adjusted"
            reasons.append(
                f"dividend check: on {frac_wrong_dir:.1%} of the {n_ne} row(s) where `ret` and "
                f"`retx` disagree, `retx` is the LARGER of the two -- a dividend is a cash "
                f"inflow, so `ret` should essentially never be less than `retx`. This is "
                f"consistent with the two columns being swapped or mislabeled, not with `ret` "
                f"genuinely including dividends"
            )
        else:
            dividend_status = "adjusted"

    ret_nonnull = dsf_stats.get("ret_nonnull_rows", 0)
    if ret_nonnull < PULL_WIDE_VERDICT_MIN_ROWS:
        ret_scale_status = "inconclusive"
        reasons.append(
            f"scale check: only {ret_nonnull} non-null `ret` row(s) (need at least "
            f"{PULL_WIDE_VERDICT_MIN_ROWS}), so `ret`'s scale could not be checked directly"
        )
    else:
        frac_big_ret = dsf_stats.get("ret_abs_gt_big_move_rows", 0) / ret_nonnull
        if not (RET_PLAUSIBLE_MIN_FRAC_ABS_GT_BIG_MOVE <= frac_big_ret <= RET_PLAUSIBLE_MAX_FRAC_ABS_GT_BIG_MOVE):
            ret_scale_status = "implausible"
            reasons.append(
                f"scale check: {frac_big_ret:.4%} of all `ret` values are bigger than "
                f"{NAIVE_RET_BIG_MOVE_THRESHOLD:.0%} in size, outside the "
                f"[{RET_PLAUSIBLE_MIN_FRAC_ABS_GT_BIG_MOVE:.2%}, "
                f"{RET_PLAUSIBLE_MAX_FRAC_ABS_GT_BIG_MOVE:.0%}] band a plausible fractional "
                f"daily return column should fall in"
            )
        elif (
            split_status != "inconclusive"
            and event_stats.get("actual_ret_abs_median", 0.0) > RET_PLAUSIBLE_MAX_EVENT_ABS_MEDIAN
        ):
            ret_scale_status = "implausible"
            reasons.append(
                f"scale check: on the big-raw-move event days above, `ret`'s own median size "
                f"is {event_stats['actual_ret_abs_median']:.1%}, beyond the "
                f"{RET_PLAUSIBLE_MAX_EVENT_ABS_MEDIAN:.0%} an ordinary daily return (even on a "
                f"split date) should stay under"
            )
        else:
            ret_scale_status = "plausible"

    if split_status == "adjusted" and dividend_status == "adjusted" and ret_scale_status == "plausible":
        overall = "adjusted"
    elif split_status in ("not_adjusted", "mixed") or dividend_status == "not_adjusted" or ret_scale_status == "implausible":
        overall = "flagged"
    else:
        overall = "inconclusive"

    return {
        "split_status": split_status,
        "dividend_status": dividend_status,
        "ret_scale_status": ret_scale_status,
        "overall": overall,
        "reasons": reasons,
    }


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
    on the still-unparsed date text (`Date <= '<end_date>'`), restricted to
    rows whose date text actually matches the ISO ('YYYY-MM-DD') shape --
    a lexical comparison is only chronologically meaningful for that shape.
    This restriction matters: an EARLIER version of this pushdown applied
    the comparison to every row regardless of format, on the reasoning
    that "it only discards rows it's sure sort before end_date." That
    reasoning was backwards -- verified against this exact file, a
    dd/mm/yyyy date like `31/12/2012`, a single-digit-month date like
    `9/5/2012`, or a textual date like `Dec 31 2012` can lexically sort
    BEFORE a numeric end_date string and get silently dropped despite
    being in-window, before clean_master_stock_frame ever sees the row to
    count it as unparseable. Restricting the pushdown to ISO-shaped dates
    only means every non-ISO-shaped row (regardless of end_date) flows
    through to clean_master_stock_frame's own, non-pushed-down date
    handling, which does the real parsing and correctly counts whatever it
    can't handle -- that function is the correctness boundary; this
    pushdown is purely a volume-reduction optimization ahead of it, and
    now a conservative one. (On the actual file as of this writing, zero
    non-blank-ticker rows have a non-ISO date, so this costs nothing.)
    """
    con = duckdb.connect()
    try:
        where_clause = "WHERE ticker IS NOT NULL AND trim(ticker) <> ''"
        params = []
        if end_date is not None:
            where_clause += " AND (NOT regexp_matches(date_text, '^\\d{4}-\\d{2}-\\d{2}$') OR date_text <= ?)"
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

    # The end_date window filter runs HERE -- before the price-quality
    # counters just below, not after them -- so that missing_price_rows /
    # infinite_price_rows / negative_price_rows (and distinct_tickers /
    # date_min / date_max) genuinely describe only the window this check
    # actually uses, matching how FINDINGS.md describes them. An earlier
    # version of this function computed those counters BEFORE this filter
    # while still labeling them "narrowed to just the window" -- benign
    # only by coincidence, since read_master_stock_raw's own ISO-shaped
    # pushdown (see its docstring) already excludes most out-of-window
    # rows before this function ever sees them. The date-FORMAT counters
    # above (iso_date_rows/mdy_date_rows/unrecognized_date_format_rows/
    # shape_matched_but_invalid_date_rows) deliberately stay computed
    # before this filter: they describe every non-blank-ticker row the
    # read returned, not just the window, since a date has to be
    # classified before a date-based filter can even be applied to it.
    if end_date is not None:
        df = df.loc[df["date"] <= pd.Timestamp(end_date)].copy()

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

    crsp_df must have columns permno/ticker/date/prc/ret/retx. Rows where
    the same (ticker, date) key appears more than once on EITHER side are
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
    same symbol -- concretely including a ticker that maps to two
    DIFFERENT CRSP permnos sequentially within this very window (e.g.
    "SAN": Banco Santander Chile, then Banco Santander S.A., a >10x price-
    level difference). This is caught PER (TICKER, PERMNO) SEGMENT, not
    per ticker alone -- CRSP's own identity for a security is the permno,
    not the mutable ticker label, so a per-ticker-only median would just
    average a wrong-company segment together with a right-company one and
    let the larger segment hide the smaller one entirely (verified: this
    happened in round-3 review for exactly this reason). The gate itself
    is a price-ratio-step test -- see
    TICKER_ALIGNMENT_MAX_MEDIAN_PRICE_STEP_DIFF's module-level comment for
    what this measures and why it replaced an earlier return-based version
    of this same gate (three confirmed false positives). Segments failing
    that test, and segments with too few consecutive-date price pairs to
    judge at all, are both quarantined out of the pooled statistics below,
    tracked as two separate reasons. The single day where a ticker hands
    off from one permno to another is additionally excluded from the
    return-agreement statistics regardless of either segment's own
    verdict (see `is_permno_handoff` below): master_stock.csv's own
    day-over-day return on that specific day is computed across the
    handoff itself (it has no permno of its own to key on) and so
    describes neither company individually.

    The return-agreement statistics themselves compare `retx` (CRSP's
    price-only return), not `ret` (CRSP's total return, dividends
    reinvested): master_stock.csv's own numbers are a plain price series
    with no evidence of dividend reinvestment, so comparing against `ret`
    introduces a real, systematic, avoidable bias on ex-dividend days
    (measured on this data: an ~25,000x larger median disagreement on
    ex-dividend days when comparing `ret` instead of `retx`). `ret` is
    still reported once, briefly, as corroborating evidence for that
    conclusion (see the `_total_ret`/`ex_dividend_*` stats) -- not as the
    headline metric.
    """
    stats = {"window_start": window_start, "window_end": window_end}

    def _in_segments(df, segments):
        """Boolean mask: does each row's (ticker, permno) pair appear in
        `segments` (a set of (ticker, permno) tuples)? Isolated as a
        helper since this exact membership test is needed against four
        different frames below (merged, full_ret_cmp, retained's source,
        retained_merged)."""
        if not segments:
            return pd.Series(False, index=df.index)
        return pd.Series(
            pd.MultiIndex.from_frame(df[["ticker", "permno"]]).isin(list(segments)),
            index=df.index,
        )

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

    # Diagnostics for WHY every colliding row is dropped, not just how
    # many (round-4 review finding: the report previously described every
    # collision as "exactly two rows, no way to tell which is right" and
    # "two unrelated price levels" unconditionally -- neither is true of
    # every group). Computed on the collision rows before they're dropped.
    #
    # A tempting refinement: for a group with only ONE valid (non-null)
    # price, there's nothing ambiguous to resolve, so keep that row
    # instead of dropping it. Tried and rejected: master_stock.csv has no
    # other row for that ticker/date to fall back on, so the NEXT kept
    # day's day-over-day return then silently spans the dropped day
    # instead of one real trading day, understating the gap the same way
    # a naively-computed return already has a max_gap_days guard against
    # (see compute_return_from_prices) -- except this gap is INTERIOR
    # (a missing row between two present ones), which that day-count guard
    # cannot see. Measured on this pull: recovering those rows raises
    # matched rows by 3,564 but also raises the number of returns silently
    # spanning more than one trading day from 18 to 1,144, and NET WORSENS
    # agreement (return_frac_within_1pct 99.94% -> 99.90%, and 0 -> 6 new
    # extreme-difference days) -- recovering a single isolated day out of a
    # longer dropped run is exactly the failure mode this avoids elsewhere
    # in this same function. Dropping the whole group, unconditionally, is
    # the better methodology despite giving up some recoverable rows.
    dup_groups = master_w.loc[master_dup]
    group_sizes = dup_groups.groupby(["ticker", "date"]).size()
    stats["master_collision_groups"] = int(len(group_sizes))
    stats["master_collision_groups_2_rows"] = int((group_sizes == 2).sum())
    stats["master_collision_groups_3plus_rows"] = int((group_sizes >= 3).sum())

    # count() (not size()) only counts non-null prices -- a group with a
    # (valid, NaN) pair isn't genuinely ambiguous (there's only one usable
    # candidate), unlike a group with two-or-more differing valid prices.
    valid_price_counts = dup_groups.groupby(["ticker", "date"])["price"].count()
    one_valid_keys = valid_price_counts.index[valid_price_counts == 1]
    stats["master_collision_groups_one_valid_price"] = int(len(one_valid_keys))
    stats["master_collision_rows_one_valid_price_group"] = (
        int(group_sizes.loc[one_valid_keys].sum()) if len(one_valid_keys) else 0
    )

    two_valid_keys = valid_price_counts.index[valid_price_counts == 2]
    if len(two_valid_keys) > 0:
        two_valid_prices = dup_groups.set_index(["ticker", "date"]).loc[two_valid_keys, "price"].dropna()
        price_range = two_valid_prices.groupby(level=[0, 1]).agg(["min", "max"])
        # A group where one of its two valid prices is exactly 0 would
        # divide by zero below -- clean_master_stock_frame already
        # excludes negative prices as invalid but not a literal 0.0, so
        # guard defensively (matches the >0 guard the price-ratio-step
        # gate below already uses for the same reason).
        price_range = price_range.loc[price_range["min"] > 0]
        rel_diff = (price_range["max"] - price_range["min"]) / price_range["min"]
        stats["master_collision_two_valid_price_groups"] = int(len(price_range))
        stats["master_collision_two_valid_price_groups_median_rel_diff"] = _nan_to_none(rel_diff.median())
        stats["master_collision_two_valid_price_groups_within_1pct"] = int((rel_diff < 0.01).sum())
    else:
        stats["master_collision_two_valid_price_groups"] = 0
        stats["master_collision_two_valid_price_groups_median_rel_diff"] = None
        stats["master_collision_two_valid_price_groups_within_1pct"] = 0

    master_w = master_w.loc[~master_dup]

    stats["crsp_distinct_tickers_in_window"] = int(crsp_w["ticker"].nunique())
    stats["master_distinct_tickers_in_window"] = int(master_w["ticker"].nunique())

    # Computed on master_w BEFORE the join (not on the joined frame): a gap
    # in the JOINED frame can be created by the join itself (a date present
    # in master_stock.csv but missing from the CRSP side, or vice versa),
    # which would make an otherwise-adjacent pair of master_stock.csv rows
    # look like a real multi-day gap when it isn't one on master_stock.csv's
    # own calendar. This return series is used only for the final
    # return-agreement statistics below, NOT for the ticker-alignment gate
    # (which uses price steps directly -- see that section).
    master_w = master_w.sort_values(["ticker", "date"]).copy()
    master_w["master_ret"] = compute_return_from_prices(master_w, "ticker", "date", "price")

    merged = pd.merge(
        crsp_w[["permno", "ticker", "date", "prc", "ret", "retx"]],
        master_w[["ticker", "date", "price", "master_ret"]].rename(columns={"price": "master_price"}),
        on=["ticker", "date"], how="inner",
    )
    merged = merged.sort_values(["ticker", "date"]).reset_index(drop=True)
    stats["matched_rows"] = int(len(merged))
    stats["matched_distinct_tickers"] = int(merged["ticker"].nunique())

    # A ticker whose matched rows span more than one permno within this
    # window (see docstring) -- flagged here so the report can name it
    # explicitly, and so the one row where the permno changes ("the
    # handoff day") can be excluded from the return-agreement stats below.
    segments_per_ticker = merged[["ticker", "permno"]].drop_duplicates().groupby("ticker").size()
    stats["tickers_spanning_multiple_permnos"] = int((segments_per_ticker > 1).sum())
    prev_permno = merged.groupby("ticker")["permno"].shift(1)
    merged["is_permno_handoff"] = prev_permno.notna() & (merged["permno"] != prev_permno)
    stats["segment_boundary_rows_excluded"] = int(merged["is_permno_handoff"].sum())

    # --- Per-(ticker, permno) alignment gate: price-ratio-step test -------
    # Both sides of each step span the exact same pair of matched dates, so
    # a master_stock.csv coverage gap can never masquerade as a price
    # disagreement the way it could in a return-based gate (see module
    # comment). Only positive prices are used (a price of 0 or a sign
    # convention on either side would make pct_change meaningless).
    # Grouping by permno as well as ticker (rather than ticker alone) is
    # what lets this test see a wrong-company segment that's a MINORITY of
    # a ticker's matched rows -- see docstring.
    pairs = merged.dropna(subset=["prc", "master_price"])
    pairs = pairs.loc[(pairs["prc"] > 0) & (pairs["master_price"] > 0)].sort_values(["ticker", "permno", "date"])
    g = pairs.groupby(["ticker", "permno"])
    step_diff = (g["master_price"].pct_change(fill_method=None) - g["prc"].pct_change(fill_method=None)).abs()
    step_cmp = pairs.assign(step_diff=step_diff).dropna(subset=["step_diff"])
    per_segment = step_cmp.groupby(["ticker", "permno"])["step_diff"].agg(n_pairs="count", median_diff="median")

    all_matched_segments = set(map(tuple, merged[["ticker", "permno"]].drop_duplicates().itertuples(index=False, name=None)))
    judged_segments = set(per_segment.index)
    # A segment with ZERO valid consecutive-date pairs (e.g. only one row
    # total, or no two rows close enough together) never appears in
    # per_segment at all -- unjudgeable the same way as "too few pairs".
    zero_pair_segments = all_matched_segments - judged_segments

    # The n_pairs guard on misaligned_mask (not just too_few_mask) matters:
    # without it, a segment with a single price pair whose one step
    # happens to disagree would be classified "misaligned" (a confident-
    # sounding, wrong-company claim) rather than "too few to judge" -- see
    # TICKER_ALIGNMENT_MIN_PRICE_PAIRS's docstring. Guarding here means the
    # two resulting sets are disjoint by construction, with no need to
    # subtract one from the other.
    misaligned_mask = (per_segment["median_diff"] > TICKER_ALIGNMENT_MAX_MEDIAN_PRICE_STEP_DIFF) & (
        per_segment["n_pairs"] >= TICKER_ALIGNMENT_MIN_PRICE_PAIRS
    )
    too_few_mask = per_segment["n_pairs"] < TICKER_ALIGNMENT_MIN_PRICE_PAIRS
    misaligned_segments = set(per_segment.index[misaligned_mask])
    too_few_segments = set(per_segment.index[too_few_mask]) | zero_pair_segments

    stats["segments_quarantined_misaligned"] = len(misaligned_segments)
    stats["segments_quarantined_too_few_prices"] = len(too_few_segments)
    stats["rows_quarantined_misaligned"] = int(_in_segments(merged, misaligned_segments).sum())
    stats["rows_quarantined_too_few_prices"] = int(_in_segments(merged, too_few_segments).sum())
    # How thin is the gap TICKER_ALIGNMENT_MAX_MEDIAN_PRICE_STEP_DIFF sits
    # in, on this actual pull -- surfaced (not just a code comment) so a
    # future refresh can see whether the margin has narrowed.
    retained_mask = ~misaligned_mask & ~too_few_mask
    stats["ticker_gate_worst_retained_median_diff"] = (
        _nan_to_none(per_segment.loc[retained_mask, "median_diff"].max()) if retained_mask.any() else None
    )
    stats["ticker_gate_closest_quarantined_median_diff"] = (
        _nan_to_none(per_segment.loc[misaligned_mask, "median_diff"].min()) if misaligned_mask.any() else None
    )

    if misaligned_segments:
        examples = []
        top = per_segment.loc[sorted(misaligned_segments), "median_diff"].sort_values(ascending=False).head(5)
        for (ticker, permno), metric in top.items():
            sub = merged.loc[(merged["ticker"] == ticker) & (merged["permno"] == permno)]
            examples.append((
                ticker, int(permno), float(metric),
                _nan_to_none(sub["prc"].median()), _nan_to_none(sub["master_price"].median()),
            ))
        stats["quarantined_segment_examples"] = examples

    quarantined_segments = misaligned_segments | too_few_segments
    # Handoff rows are excluded here too (not just from full_ret_cmp below):
    # a handoff row's OWN segment might be perfectly well-aligned, but its
    # master_ret still reflects a step across the boundary into a
    # different company's price level -- see docstring.
    retained_merged = merged.loc[~_in_segments(merged, quarantined_segments) & ~merged["is_permno_handoff"]]

    # --- Return agreement, on the retained (correctly-aligned) segments ---
    full_ret_cmp = merged.dropna(subset=["retx", "master_ret"]).copy()
    full_ret_cmp = full_ret_cmp.loc[~full_ret_cmp["is_permno_handoff"]].copy()
    stats["return_comparison_rows"] = int(len(full_ret_cmp))
    if len(full_ret_cmp) == 0:
        return stats, merged
    full_ret_cmp["abs_diff"] = (full_ret_cmp["retx"] - full_ret_cmp["master_ret"]).abs()

    retained = full_ret_cmp.loc[~_in_segments(full_ret_cmp, quarantined_segments)]
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

    if len(full_ret_cmp) > 1:
        stats["return_correlation_before_ticker_gate"] = float(full_ret_cmp["retx"].corr(full_ret_cmp["master_ret"]))
        stats["return_frac_within_1pct_before_ticker_gate"] = float((full_ret_cmp["abs_diff"] < 0.01).mean())
    if len(retained) > 1:
        stats["return_correlation"] = float(retained["retx"].corr(retained["master_ret"]))

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
            stats["return_correlation_excl_extreme"] = float(trimmed["retx"].corr(trimmed["master_ret"]))

    # Corroborating evidence for using retx (see docstring): the
    # total-return `ret` comparison on the SAME retained tickers, and how
    # much worse it gets specifically on days CRSP recorded a dividend
    # (ret != retx that day).
    ret_cmp_also = retained_merged.dropna(subset=["ret", "master_ret"])
    if len(ret_cmp_also) > 0:
        stats["return_mean_abs_diff_total_ret"] = float((ret_cmp_also["ret"] - ret_cmp_also["master_ret"]).abs().mean())
    ex_div = retained_merged.dropna(subset=["ret", "retx", "master_ret"])
    ex_div = ex_div.loc[(ex_div["ret"] - ex_div["retx"]).abs() > DIVIDEND_EVIDENCE_MIN_DIFF]
    stats["n_ex_dividend_days"] = int(len(ex_div))
    if len(ex_div) > 0:
        stats["ex_dividend_median_abs_diff_ret"] = float((ex_div["ret"] - ex_div["master_ret"]).abs().median())
        stats["ex_dividend_median_abs_diff_retx"] = float((ex_div["retx"] - ex_div["master_ret"]).abs().median())

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
        # row_number() OVER () below has no explicit ORDER BY, so its
        # meaning as "position in the file" depends on DuckDB preserving
        # the CSV reader's natural scan order through the window function
        # -- true by default, but pinned explicitly here (rather than
        # relied on implicitly) since the blank-ticker-block-is-
        # contiguous-at-the-end claim in FINDINGS.md is load-bearing.
        con.execute("SET preserve_insertion_order = true")
        row = con.execute(
            f"""
            WITH raw AS (
                SELECT Tic AS ticker, Date AS date_text, Price AS price_text,
                       row_number() OVER () AS row_pos
                FROM read_csv(
                    {duckdb_quote_literal(str(csv_path))},
                    header=True, quote='"', escape='"', all_varchar=true
                )
            ),
            classified AS (
                SELECT
                    ticker, date_text, price_text, row_pos,
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
                         AND NOT isinf(price_num) THEN 1 ELSE 0 END),
                min(CASE WHEN is_blank_ticker THEN row_pos END),
                max(CASE WHEN is_blank_ticker THEN row_pos END)
            FROM classified
            """
        ).fetchone()
        (
            total_rows, blank_ticker_rows, blank_ticker_iso_rows, blank_ticker_mdy_rows,
            distinct_tickers, min_date_text, max_date_text,
            missing_price_rows, infinite_price_rows, negative_price_rows,
            blank_ticker_first_row, blank_ticker_last_row,
        ) = row
        # "One contiguous block at the very end of the file" is a specific,
        # checkable claim, not just an assumption from the counts above:
        # true only if every blank-ticker row's position falls in one
        # unbroken run (no gaps -- last minus first plus one equals the
        # count) AND that run ends at the file's last row.
        is_contiguous_at_end = (
            blank_ticker_rows > 0
            and blank_ticker_last_row == total_rows
            and (blank_ticker_last_row - blank_ticker_first_row + 1) == blank_ticker_rows
        )
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
            "blank_ticker_first_row": int(blank_ticker_first_row) if blank_ticker_first_row is not None else None,
            "blank_ticker_last_row": int(blank_ticker_last_row) if blank_ticker_last_row is not None else None,
            "blank_ticker_is_contiguous_at_end": is_contiguous_at_end,
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


def _fmt_pct(x, decimals=2, signed=False) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    sign = "+" if signed else ""
    return f"{x * 100:{sign}.{decimals}f}%"


def _safe_div(numerator, denominator):
    """numerator / denominator, or None if denominator is 0 -- a pull with
    zero non-null prices (or zero of whatever else is being divided into)
    is a degenerate edge case this report should say "n/a" for, not crash
    on with a ZeroDivisionError."""
    if not denominator:
        return None
    return numerator / denominator


def _nan_to_none(x):
    """A plain float, or None if x is NaN -- e.g. a quarantined ticker
    whose prc or master_price happens to be all-null for the matched
    rows would otherwise carry a NaN into a report string as a literal
    "$nan" instead of "n/a" (see _fmt_usd)."""
    return None if pd.isna(x) else float(x)


def _fmt_usd(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"${x:,.2f}"


def _fmt_date(d) -> str:
    if d is None or pd.isna(d):
        return "n/a"
    return pd.Timestamp(d).date().isoformat()


def _fmt_float(v, decimals=2) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return f"{v:.{decimals}f}"


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
        "date": _fmt_date, "prc": lambda v: _fmt_float(v, 2),
        "ret": lambda v: _fmt_pct(v, 2, signed=True), "retx": lambda v: _fmt_pct(v, 2, signed=True),
        "shrout": lambda v: _fmt_int(v), "cfacpr": lambda v: _fmt_float(v, 1),
    }
    aapl_2000_table = _markdown_table(ctx["aapl_2000"], aapl_fmt)
    aapl_2005_table = _markdown_table(ctx["aapl_2005"], aapl_fmt)

    msft_fmt = {
        "date": _fmt_date, "prc": lambda v: _fmt_float(v, 2),
        "ret": lambda v: _fmt_pct(v, 4, signed=True), "retx": lambda v: _fmt_pct(v, 4, signed=True),
        "shrout": lambda v: _fmt_int(v), "cfacpr": lambda v: _fmt_float(v, 1),
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
        f"scanned every one of the {_fmt_int(dsf['output_rows'])} cleaned rows for these codes and "
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
    lines.append(
        f"- **Row accounting.** The raw pull had {_fmt_int(dsf['input_rows'])} (permno, date) "
        f"rows; {_fmt_int(dsf['output_rows'])} made it into the cleaned output. The "
        f"difference is exactly {_fmt_int(dsf['rows_permno_not_in_resolved_universe'])} rows "
        f"for a permno outside the resolved universe (unexpected, since crsp.dsf was pulled "
        f"pre-filtered to it -- worth a second look if this number is ever nonzero) plus "
        f"{_fmt_int(dsf['duplicate_permno_date_rows_dropped'])} duplicate (permno, date) "
        f"rows -- no other rows are dropped anywhere in this cleaning step."
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
    adjustment = ctx["adjustment_verdict"]
    overall = adjustment["overall"]

    lines.append(
        "Three separate things have to be true for `ret`/`retx` to be safe to use as-is "
        "downstream: splits and other distributions have to be backed out, dividends have "
        "to be handled consistently between the two columns, and `ret` itself has to "
        "actually be a plausible fractional daily return (not, say, delivered in the wrong "
        "units). Each is checked independently below, against this pull's actual numbers, "
        "regardless of which way the verdict that follows comes out."
    )
    lines.append("")

    # --- Check 1: splits and distributions (see classify_adjustment_status) --
    n_events = events.get("n_events", 0)
    n_big = events.get("n_events_with_big_naive_move", 0)
    # Gate on the verdict's own split_status, not a separate n_events/n_big
    # re-check -- a second, looser gate here previously let the report
    # sound conclusive on as few as 1 event while the verdict (needs 30)
    # called it inconclusive two paragraphs later.
    if adjustment["split_status"] != "inconclusive":
        actual_median = events.get("actual_ret_abs_median", 0.0)
        if actual_median <= RET_PLAUSIBLE_MAX_EVENT_ABS_MEDIAN:
            scale_clause = "ordinary daily return territory, consistent with a plausible fractional return"
        else:
            scale_clause = (
                f"far too large to be an ordinary daily return -- a plausible fractional "
                f"daily return's typical size, even on a split date, should stay well under "
                f"{_fmt_pct(RET_PLAUSIBLE_MAX_EVENT_ABS_MEDIAN, 0)}"
            )
        lines.append(
            f"- **Splits and distributions.** Every time CRSP's own adjustment factor "
            f"(`cfacpr`) moved by more than {_fmt_pct(CFACPR_EVENT_MIN_CHANGE, 0)} from a "
            f"stock's previous trading day (with a usable prior price and a reported return) "
            f"-- a signal that a split or similar event happened -- we compared two numbers "
            f"for that day: the \"naive\" return you'd get from just dividing the raw price "
            f"by the previous day's raw price, and CRSP's actual reported return. "
            f"{_fmt_int(n_events)} such adjustment events "
            f"happened across the whole universe and window; {_fmt_int(n_big)} of them had a "
            f"naive move bigger than {_fmt_pct(NAIVE_RET_BIG_MOVE_THRESHOLD, 0)} -- consistent "
            f"with a real split or distribution, not day-to-day noise (median naive move size "
            f"{_fmt_pct(events['naive_ret_abs_median'])} on exactly those days). Among exactly "
            f"those big-move days, CRSP's actual reported `ret` lands within "
            f"{_fmt_pct(UNADJUSTED_RET_MATCH_TOL, 0)} of that same naive move "
            f"{_fmt_pct(events.get('frac_big_naive_events_ret_equals_naive', 0.0))} of the "
            f"time -- a fully unadjusted return column would measure close to 100% here. "
            f"This pull's actual `ret` has its own median size of "
            f"{_fmt_pct(actual_median, 3)} on those same days, and "
            f"{_fmt_pct(events.get('frac_actual_ret_gt_big_move', 0.0), 3)} of those same days "
            f"have an actual `ret` bigger than {_fmt_pct(NAIVE_RET_BIG_MOVE_THRESHOLD, 0)} in "
            f"size -- {scale_clause}."
        )
    else:
        lines.append(
            f"- **Splits and distributions.** Only {_fmt_int(n_big)} big-raw-move adjustment "
            f"event(s) found across the whole pull (need at least "
            f"{ADJUSTMENT_VERDICT_MIN_EVENTS} to draw a conclusion), so this check could not "
            f"run."
        )

    # --- Check 2: dividends (cfacpr can't see them -- see docstring) ---------
    both_nonnull = dsf.get("ret_and_retx_nonnull_rows", 0)
    if both_nonnull >= PULL_WIDE_VERDICT_MIN_ROWS:
        n_ne = dsf.get("ret_ne_retx_rows", 0)
        frac_ne = _safe_div(n_ne, both_nonnull)
        lines.append(
            f"- **Dividends.** `cfacpr` never changes for an ordinary cash dividend, so the "
            f"split check above can't see dividends at all -- this needs its own evidence. "
            f"If `ret` truly reinvests dividends and `retx` truly excludes them, the two "
            f"columns must disagree on genuine ex-dividend days and agree everywhere else. "
            f"Across {_fmt_int(both_nonnull)} rows with both populated, they disagree on "
            f"{_fmt_int(n_ne)} ({_fmt_pct(frac_ne, 4)}) -- a pull where `ret` was mistakenly "
            f"populated with `retx`'s values (i.e. NOT dividend-adjusted) would measure "
            f"exactly 0% here. As a directional sanity check (a dividend is a cash inflow, "
            f"so `ret` should almost never be LESS than `retx`), `retx` exceeds `ret` on "
            f"{_fmt_int(dsf.get('ret_lt_retx_rows', 0))} of those rows."
        )
    else:
        lines.append(
            f"- **Dividends.** Only {_fmt_int(both_nonnull)} row(s) have both `ret` and "
            f"`retx` populated (need at least {_fmt_int(PULL_WIDE_VERDICT_MIN_ROWS)}), so "
            f"this check could not run."
        )

    # --- Check 3: is `ret` even a plausible daily return? --------------------
    ret_nonnull = dsf.get("ret_nonnull_rows", 0)
    if ret_nonnull >= PULL_WIDE_VERDICT_MIN_ROWS:
        frac_big_ret = _safe_div(dsf.get("ret_abs_gt_big_move_rows", 0), ret_nonnull)
        frac_big_ret_desc = _fmt_pct(frac_big_ret, 4) if frac_big_ret is not None else "an unknown fraction"
        lines.append(
            f"- **Is `ret` even a plausible daily return?** Independent of whether it's "
            f"adjusted, `ret` has to actually be a small fractional daily return -- not, "
            f"say, delivered in percent units, where +2% would show up as `2.0` instead of "
            f"`0.02`. Across {_fmt_int(ret_nonnull)} non-null values, {frac_big_ret_desc} "
            f"exceed {_fmt_pct(NAIVE_RET_BIG_MOVE_THRESHOLD, 0)} in size -- a percent-units "
            f"column would put nearly all of them above that line; an all-zero or constant "
            f"column would put none."
        )
    else:
        lines.append(
            f"- **Is `ret` even a plausible daily return?** Only {_fmt_int(ret_nonnull)} "
            f"non-null `ret` row(s) (need at least {_fmt_int(PULL_WIDE_VERDICT_MIN_ROWS)}), "
            f"so this check could not run."
        )
    lines.append("")

    if overall == "adjusted":
        lines.append(
            "**Short answer: `ret`/`retx` (the return columns) are already adjusted for both "
            "splits and dividends, and both check out as plausible fractional daily "
            "returns.** `prc` (the raw price column) is not adjusted for splits at all -- it "
            "shows the actual traded price level, which jumps on a split date."
        )
    elif overall == "flagged":
        lines.append(
            "> **FLAG FOR ESCALATION: in this pull, `ret`/`retx` do not cleanly pass every "
            "check above.** This contradicts CRSP's standard, well-documented convention (or "
            "reveals a data-quality problem specific to this pull) and must be resolved "
            "(confirm with WRDS/CRSP documentation for this specific pull, or apply a manual "
            "fix) before any return calculation downstream of this slice is trusted. "
            "Specifically:"
        )
        for reason in adjustment["reasons"]:
            lines.append(f"> - {reason}")
    else:
        lines.append(
            "**This pull did not have enough evidence to confirm adjustment status and "
            "return-scale plausibility on one or more of the checks above** (see whichever "
            "of them fell short just above), so this is reported as unverified for this run "
            "rather than assumed. CRSP's own documentation states that `ret`/`retx` are "
            "split- and dividend-adjusted as a matter of standard convention, and `prc` is "
            "not adjusted for splits -- but that's what we'd expect to find, not what this "
            "run actually confirmed. Specifically:"
        )
        for reason in adjustment["reasons"]:
            lines.append(f"- {reason}")
    lines.append("")
    if ctx.get("aapl_last_cfacpr") is not None and ctx["aapl_last_cfacpr"] != 1:
        aapl_cfacpr_example = (
            f"Apple, for example, has `cfacpr` = {ctx['aapl_last_cfacpr']:.1f} on our last "
            f"pulled date, {_fmt_date(ctx['aapl_last_date'])} -- but that's because of at "
            f"least one split after this pull's own last date (for Apple, a 4-for-1 in "
            f"**August 2020**), an external fact this pull cannot see from its own rows. "
            f"Dividing an early Apple price by that same {ctx['aapl_last_cfacpr']:.1f} would "
            f"misrepresent that early history in terms of a stock split that hadn't happened "
            f"yet, and wasn't knowable, at any point in this project's window."
        )
    else:
        aapl_cfacpr_example = (
            "A stock that split at any point between its earliest row and the last date "
            "this project happened to pull would show exactly this problem: dividing an "
            "early price by a `cfacpr` that already reflects a later split misrepresents "
            "that early history in terms of a stock split that hadn't happened yet."
        )
    lines.append(
        f"CRSP also provides a cumulative price-adjustment factor, `cfacpr`, which is "
        f"carried through unchanged in the output -- but we deliberately do NOT compute and "
        f"store a derived \"adjusted price\" (`prc / cfacpr`) column. The reason is `cfacpr` "
        f"itself: it's cumulative up to the LAST date in whatever CRSP file we happened to "
        f"pull, not up to the end of this project's own sample. {aapl_cfacpr_example} "
        f"That's exactly the kind of mistake this project has to avoid: using information "
        f"that wasn't actually available at the time. (This reasoning holds regardless of "
        f"the adjustment-status finding above -- `cfacpr`'s own definition is the problem, "
        f"not whether `ret` happens to already be adjusted.)"
    )
    lines.append("")
    if overall == "adjusted":
        ret_retx_safe_clause = (
            "for comparing returns across a split safely, use `ret`/`retx`, which don't "
            "have this particular problem (see the evidence above)"
        )
    else:
        ret_retx_safe_clause = (
            "`ret`/`retx` don't have this particular look-ahead problem either way -- but "
            "see the evidence above for this run's actual adjustment-status finding before "
            "treating them as safe to use as-is"
        )
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
        f"`prc / cfacpr` with that caveat in view; {ret_retx_safe_clause}."
    )
    lines.append("")

    aapl_tables_populated = len(ctx["aapl_2000"]) > 0 or len(ctx["aapl_2005"]) > 0
    if aapl_tables_populated:
        lines.append(
            "**Named example: Apple's two splits.** Apple (permno "
            f"{ctx['aapl_permno']}) did 2-for-1 stock splits on 2000-06-21 and 2005-02-28. "
            "The rows actually pulled for this run, around each split date, are shown below "
            "-- read the `prc` and `ret` columns directly rather than taking a description "
            "of what they \"should\" show: if the return column is truly split-adjusted, "
            "`ret` stays a normal small daily move straight through the split even though "
            "`prc` roughly halves and `shrout` roughly doubles."
        )
        lines.append("")
        if len(ctx["aapl_2000"]) > 0:
            lines.append(aapl_2000_table)
            lines.append("")
        if len(ctx["aapl_2005"]) > 0:
            lines.append(aapl_2005_table)
            lines.append("")
    if len(ctx["msft_div"]) > 0:
        msft_split_note = ""
        if len(ctx["msft_div"]) > 1:
            m = ctx["msft_div"].sort_values("date").reset_index(drop=True)
            both_changed = (m["cfacpr"].diff() < 0) & (m["shrout"].diff() > 0)
            idx = m.index[both_changed]
            if len(idx) > 0:
                i = idx[0]
                prev_row, cur_row = m.loc[i - 1], m.loc[i]
                msft_split_note = (
                    f" (It also shows `shrout` rising from {_fmt_int(prev_row['shrout'])} to "
                    f"{_fmt_int(cur_row['shrout'])} and `cfacpr` falling from "
                    f"{_fmt_float(prev_row['cfacpr'], 1)} to "
                    f"{_fmt_float(cur_row['cfacpr'], 1)} a day earlier, on "
                    f"{_fmt_date(cur_row['date'])} -- Microsoft's last-ever 2-for-1 stock "
                    f"split happened to take effect the day before this dividend, so both "
                    f"events are visible in the same short window; this is real history, "
                    f"not a mistake.)"
                )
        lines.append(
            "**Named example: Microsoft's first dividend.** "
            f"Microsoft (permno {ctx['msft_permno']}) paid its first-ever regular quarterly "
            f"dividend, with an ex-dividend date of 2003-02-19 (the first day a buyer no "
            f"longer receives that payment). If `ret` truly includes dividends and `retx` "
            f"truly excludes them, `ret` should be noticeably higher than `retx` on exactly "
            f"that one day and identical every other day -- again, read the table directly."
            f"{msft_split_note}"
        )
        lines.append("")
        lines.append(msft_table)
        lines.append("")

    if overall == "adjusted":
        closing = (
            "No case of unadjusted returns, missing dividend adjustment, or an implausible "
            "return scale was found anywhere in this check, so there is nothing here to "
            "escalate as a surprise finding -- this is the standard, well-documented CRSP "
            "convention, confirmed directly against this specific pull rather than assumed."
        )
    elif overall == "flagged":
        closing = (
            "**This finding is flagged for escalation above and should be resolved before "
            "trusting any return calculation downstream of this slice.**"
        )
    else:
        closing = (
            "Adjustment status could not be verified directly in this run for lack of "
            "sufficient evidence on one or more of the checks above -- treated as an open "
            "question, not assumed to be fine."
        )
    lines.append(closing)
    lines.append("")

    # ---- Benchmark -------------------------------------------------------
    lines.append("## Market benchmark (crsp.dsi)")
    lines.append("")
    if (
        dsi["null_value_rows"] == 0
        and dsi["duplicate_date_rows_dropped"] == 0
        and dsi.get("sentinel_rows_mapped_to_null", 0) == 0
    ):
        quality_note = "with no missing values in any of its return columns and no duplicated trading days"
    else:
        parts = []
        if dsi["null_value_rows"]:
            parts.append(f"{_fmt_int(dsi['null_value_rows'])} row(s) with a missing return value")
        if dsi.get("sentinel_rows_mapped_to_null"):
            parts.append(
                f"{_fmt_int(dsi['sentinel_rows_mapped_to_null'])} of those from CRSP's "
                f"documented missing-return sentinel codes (same convention as dsf -- see "
                f"above), mapped to null rather than kept as a literal value"
            )
        if dsi["duplicate_date_rows_dropped"]:
            parts.append(f"{_fmt_int(dsi['duplicate_date_rows_dropped'])} duplicated trading-day row(s) dropped")
        quality_note = "with " + " and ".join(parts)
    archive_start_ts = pd.Timestamp(ARCHIVE_START_DATE)
    archive_end_ts = pd.Timestamp(ARCHIVE_END_DATE)
    dsi_covers_archive_window = dsi["date_min"] <= archive_start_ts and dsi["date_max"] >= archive_end_ts
    if dsi_covers_archive_window:
        coverage_clause = (
            f"it covers the entire {archive_start_ts.year}-{archive_end_ts.year} window this "
            f"project cares about"
        )
    else:
        coverage_clause = (
            f"it covers {_fmt_date(dsi['date_min'])} to {_fmt_date(dsi['date_max'])}, which "
            f"does NOT fully cover the {archive_start_ts.year}-{archive_end_ts.year} window "
            f"this project cares about -- a gap worth investigating before relying on this "
            f"benchmark for the full window"
        )
    lines.append(
        f"`crsp.dsi` is one row per trading day, market-wide (not per stock), covering "
        f"{_fmt_int(dsi['rows'])} trading days from {_fmt_date(dsi['date_min'])} to "
        f"{_fmt_date(dsi['date_max'])}, {quality_note}. "
        f"It carries `sprtrn` (the S&P 500 index's own return) and `vwretd` (CRSP's own "
        f"value-weighted return across the whole market, dividends included), along with "
        f"three related variants. Per an earlier decision recorded in the project plan, this "
        f"is now the **primary** benchmark for market-adjusted returns going forward -- "
        f"{coverage_clause}, unlike `data/raw_equity/S&P 500.csv`, which only starts in 2010 "
        f"and is kept only as a narrow secondary cross-check, not used in this slice. All "
        f"five return columns are carried through to `market_benchmark.parquet` unchanged "
        f"aside from the date parsing, since the table is small and a later slice may want "
        f"more than just `sprtrn`."
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
        f"ultimately cares about ({archive_start_year}-{archive_end_year}).** This script "
        f"only loads and compares {CROSSVAL_YEARS[0]}-{CROSSVAL_YEARS[-1]} of CRSP data for "
        f"this specific check -- chosen because the file's own dates currently start "
        f"{master_full['min_date_text']}, right around there, but that's a hardcoded choice "
        f"this script does NOT re-derive from the file automatically: a future refresh of "
        f"master_stock.csv with meaningfully earlier coverage would still only be checked "
        f"against {CROSSVAL_YEARS[0]}-{CROSSVAL_YEARS[-1]} until that choice is widened by "
        f"hand. So this check can only speak to {window_desc} -- not the full "
        f"{archive_start_year}-{archive_end_year} window. That's a real limitation on how "
        f"much this check can prove, not something to gloss over."
    )
    lines.append("")
    if master_full.get("blank_ticker_is_contiguous_at_end"):
        block_desc = "one contiguous block appended at the very end of the file (after the last real ticker's data)"
    elif master_full["blank_ticker_rows"] > 0:
        block_desc = (
            f"spread across the file rather than confined to one place (first at row "
            f"{_fmt_int(master_full['blank_ticker_first_row'])}, last at row "
            f"{_fmt_int(master_full['blank_ticker_last_row'])} of "
            f"{_fmt_int(master_full['total_rows'])} total) -- a different situation than a "
            f"single appended block, worth a closer look before assuming it's confined the "
            f"way an earlier version of this file was"
        )
    else:
        block_desc = "not present at all in this file"
    lines.append(
        f"**The file also has real data-quality problems.** "
        f"Looking at the whole file (not just the {window_desc} window above -- it actually "
        f"runs through {master_full['max_date_text']}), there are "
        f"{_fmt_int(master_full['non_blank_ticker_rows'])} rows with a real ticker and "
        f"{_fmt_int(master_full['blank_ticker_rows'])} rows with a blank ticker, which are "
        f"{block_desc}: "
        f"{_fmt_int(master_full['blank_ticker_mdy_rows'])} of them use "
        f"`mm/dd/yyyy`-formatted dates and the remaining "
        f"{_fmt_int(master_full['blank_ticker_iso_rows'])} use ISO dates, with prices whose "
        f"scale doesn't resemble any single stock -- it looks like at least one stray, "
        f"unrelated price series (an index or similar) was accidentally appended to a "
        f"single-stock file. Dropping every blank-ticker row removes this entire block "
        f"cleanly, with nothing left over to special-case."
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
    lines.append(
        f"A few more integrity checks, expected to read as zero or near it -- the point of "
        f"naming them is that they'd stop being zero if a future refresh of this file "
        f"changed shape in a way this cleaning code doesn't already handle. These two are "
        f"counted across every non-blank-ticker row this read returned, not just the "
        f"{window_desc} window (a date has to be classified before it can be filtered by "
        f"window at all, and read_master_stock_raw's own pushdown -- see its docstring -- "
        f"already excludes most out-of-window rows before this function ever sees them): "
        f"{_fmt_int(master_clean['unrecognized_date_format_rows'])} rows with a date "
        f"in neither ISO nor `mm/dd/yyyy` format, and "
        f"{_fmt_int(master_clean['shape_matched_but_invalid_date_rows'])} rows with a date "
        f"that LOOKS like one of those two formats but isn't a real calendar date (e.g. "
        f"month 13). This one, by contrast, IS narrowed to just the {window_desc} window: "
        f"{_fmt_int(master_clean['unparseable_text_price_rows'])} rows with "
        f"a price field that has text in it that isn't a number at all (distinct from a "
        f"field that's simply empty, already counted above)."
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
    ret_retx_output_desc = (
        "both already split/dividend-adjusted by CRSP, see above"
        if overall == "adjusted"
        else "see above for this run's actual adjustment-status finding before assuming so"
    )
    lines.append(
        "- `permno`, `date` (a real date now), `ticker` (CRSP's ticker on record for that "
        "permno as of that specific date, not today's ticker -- tickers change over a "
        "company's life, so this avoids mislabeling old history with a later symbol)\n"
        "- `prc` (cleaned raw price, never negative), `prc_is_bidask_avg` (flag for CRSP's "
        "no-trade convention)\n"
        f"- `ret`, `retx` (total and price-only returns; {ret_retx_output_desc})\n"
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
        f"{_fmt_int(crossval['master_ticker_date_collision_tickers'])} tickers, form "
        f"{_fmt_int(crossval.get('master_collision_groups', 0))} groups where **more than one "
        f"price is recorded for the same ticker on the same day** -- "
        f"{_fmt_int(crossval.get('master_collision_groups_2_rows', 0))} groups of two rows and "
        f"{_fmt_int(crossval.get('master_collision_groups_3plus_rows', 0))} of three or more "
        f"(not uniformly \"exactly two,\" and mostly multi-share-class tickers recording more "
        f"than one class's price under one symbol). Among the "
        f"{_fmt_int(crossval.get('master_collision_two_valid_price_groups', 0))} groups with "
        f"exactly two individually-valid prices, the two prices are a median "
        f"{_fmt_pct(crossval.get('master_collision_two_valid_price_groups_median_rel_diff'), 1)} "
        f"apart, and "
        f"{_fmt_int(crossval.get('master_collision_two_valid_price_groups_within_1pct', 0))} of "
        f"those pairs sit within 1% of each other, so \"two unrelated price levels\" "
        f"doesn't describe every one of them either. Either way, master_stock.csv itself gives "
        f"no signal for which candidate (if either) is the real one, so every row in a "
        f"colliding group is dropped rather than guessed at -- including "
        f"{_fmt_int(crossval.get('master_collision_rows_one_valid_price_group', 0))} rows from "
        f"{_fmt_int(crossval.get('master_collision_groups_one_valid_price', 0))} groups that end "
        f"up with only ONE usable (non-null) price after cleaning. Keeping those single-valid-"
        f"price rows was tried and rejected: doing so lets the next kept day's return silently "
        f"span the dropped day (master_stock.csv has no other row for that date to fall back "
        f"on), which measured WORSE agreement against CRSP overall, not better -- the isolated "
        f"recovered days behave like exactly the kind of hidden multi-day gap the alignment "
        f"gate elsewhere in this check exists to avoid. The CRSP side has a smaller version of "
        f"the same problem: "
        f"{_fmt_int(crossval['crsp_ticker_date_collisions_dropped'])} rows dropped there too "
        f"(a ticker symbol reused by more than one permno, or an ambiguous identifier "
        f"resolution), so this comparison isn't silently one-sided about it."
    )
    lines.append(
        f"**{_fmt_int(crossval['matched_rows'])} (ticker, date) rows matched** across "
        f"{_fmt_int(crossval['matched_distinct_tickers'])} tickers, out of "
        f"{_fmt_int(crossval['crsp_distinct_tickers_in_window'])} CRSP tickers and "
        f"{_fmt_int(crossval['master_distinct_tickers_in_window'])} master_stock tickers "
        f"with usable rows in the window (both counted after the colliding rows above were "
        f"dropped) -- only "
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
        "compares two completely unrelated companies' prices under the same symbol -- "
        "concretely including a ticker that maps to two DIFFERENT CRSP permnos "
        "sequentially within this very window, so a per-ticker-only check could average "
        "the wrong-company segment together with the right-company one and miss the "
        "problem entirely if the right company happens to be the majority of the matched "
        "days."
    )
    n_misaligned = crossval.get("segments_quarantined_misaligned", 0)
    rows_misaligned = crossval.get("rows_quarantined_misaligned", 0)
    n_too_few = crossval.get("segments_quarantined_too_few_prices", 0)
    rows_too_few = crossval.get("rows_quarantined_too_few_prices", 0)
    worst_retained = crossval.get("ticker_gate_worst_retained_median_diff")
    closest_quarantined = crossval.get("ticker_gate_closest_quarantined_median_diff")
    # Not "the two groups are cleanly separated" -- that's unfalsifiable
    # (retained/misaligned are complementary masks on the same threshold,
    # so a kept segment is always <= it and a set-aside one always >, for
    # ANY data). What's actually informative is the ratio between them.
    if worst_retained is not None and closest_quarantined is not None:
        if worst_retained > 0:
            margin_clause = (
                f" How much room the threshold has here: the worst kept segment measures "
                f"{worst_retained:.2e}, the closest set-aside one {closest_quarantined:.2e} "
                f"-- {closest_quarantined / worst_retained:.1f}x apart."
            )
        else:
            margin_clause = (
                f" Every kept segment on this run measured exactly 0; the closest "
                f"set-aside one measures {closest_quarantined:.2e}."
            )
    else:
        margin_clause = ""
    lines.append(
        f"We catch this per **(ticker, permno) segment** -- not per ticker alone, and not "
        f"with one overall statistic, either of which could miss a problem confined to a "
        f"handful of names or hidden inside a ticker that changes hands mid-window: for "
        f"every matched segment, we compare how much CRSP's price moved from one matched "
        f"day to the next against how much master_stock.csv's price moved over that SAME "
        f"pair of days, and take the typical (median) size of the disagreement. Using the "
        f"day-to-day price move rather than a pre-computed return keeps this test "
        f"unaffected by which return convention either side uses; grouping by permno as "
        f"well as ticker is what lets it see a wrong-company segment that's a MINORITY of "
        f"a ticker's matched rows, since CRSP's own identity for a security is the permno, "
        f"not the mutable ticker label. A segment that really is the same company moves "
        f"the same amount, in the same direction, on both sides, almost every time; one "
        f"pointing at a different company does not. Any segment whose typical disagreement "
        f"exceeds {_fmt_pct(TICKER_ALIGNMENT_MAX_MEDIAN_PRICE_STEP_DIFF, 2)} is set aside as "
        f"not comparable: **{_fmt_int(n_misaligned)} segments ({_fmt_int(rows_misaligned)} "
        f"rows)**.{margin_clause} A further **{_fmt_int(n_too_few)} segments "
        f"({_fmt_int(rows_too_few)} rows)** don't have enough usable price PAIRS to judge -- "
        f"too few overlapping days, or too few days where both sides actually have a price "
        f"(a missing/infinite/negative master_stock.csv price, already counted above, lands "
        f"here even with perfect date overlap) -- and are set aside separately, not counted "
        f"as evidence of either problem."
    )
    n_spanning = crossval.get("tickers_spanning_multiple_permnos", 0)
    n_boundary = crossval.get("segment_boundary_rows_excluded", 0)
    if n_spanning:
        lines.append(
            f"**{_fmt_int(n_spanning)} matched ticker(s) actually span more than one permno "
            f"within this window** (the ticker symbol was reassigned to a different company "
            f"partway through) -- each permno is judged, and quarantined, on its own rather "
            f"than pooled. The single day where a ticker hands off from one permno to the "
            f"next is also excluded from the return-agreement numbers below regardless of "
            f"either segment's own verdict, since master_stock.csv's own day-over-day return "
            f"on that specific day is computed across the handoff itself and describes "
            f"neither company individually: {_fmt_int(n_boundary)} such row(s)."
        )
    examples = crossval.get("quarantined_segment_examples") or []
    if examples:
        example_bits = "; ".join(
            f"`{t}` (permno {p}; typical day-to-day price moves disagree by "
            f"{_fmt_pct(metric, 2)}; CRSP price around {_fmt_usd(crsp_p)}, "
            f"master_stock.csv price around {_fmt_usd(master_p)})"
            for t, p, metric, crsp_p, master_p in examples[:3]
        )
        lines.append(
            f"For example: {example_bits}. These aren't small rounding differences. For some "
            f"segments the two sources are genuinely different companies sharing a recycled "
            f"ticker; for others they may be the same security whose master_stock.csv history "
            f"was retroactively re-based for a later corporate action this test has no way to "
            f"know about. This test can't tell those two cases apart, and doesn't need to: "
            f"neither is comparable day-to-day against CRSP as-is, so both are excluded from "
            f"the pooled figures below."
        )
    lines.append("")

    retained_rows = crossval.get("return_comparison_rows_retained", 0)
    if retained_rows:
        lines.append(
            f"**Comparing day-over-day returns for the remaining, correctly-aligned "
            f"segments.** The comparison uses `retx` (CRSP's price-only return), not `ret` "
            f"(CRSP's total return, dividends reinvested) -- master_stock.csv's own prices "
            f"show no sign of dividend reinvestment, so `retx` is the honest like-for-like "
            f"comparison. Over {_fmt_int(retained_rows)} matched days: a median absolute "
            f"difference of {crossval['return_median_abs_diff']:.2e}, and "
            f"{_fmt_pct(crossval['return_frac_within_1pct'])} of days agreeing to within 1 "
            f"percentage point."
        )
        n_ex_div = crossval.get("n_ex_dividend_days", 0)
        if n_ex_div and crossval.get("ex_dividend_median_abs_diff_ret") is not None:
            lines.append(
                f"For comparison, using the total-return `ret` instead: mean absolute "
                f"difference of {crossval.get('return_mean_abs_diff_total_ret', float('nan')):.2e} "
                f"(versus {crossval['return_mean_abs_diff']:.2e} for `retx`). The gap is "
                f"concentrated exactly where dividends would cause one: on the "
                f"{_fmt_int(n_ex_div)} days CRSP recorded a dividend for a retained segment "
                f"(`ret` differs from `retx`), the median difference against master_stock.csv "
                f"is {crossval['ex_dividend_median_abs_diff_ret']:.2e} using `ret` versus "
                f"{crossval['ex_dividend_median_abs_diff_retx']:.2e} using `retx` -- itself "
                f"further evidence that master_stock.csv's series is price-only."
            )
        if crossval["return_median_abs_diff"] < CROSSVAL_SUSPICIOUSLY_CLOSE_MEDIAN_DIFF:
            lines.append(
                "That difference is small enough to be worth a second thought: two "
                "genuinely independent data vendors measuring the same real-world price "
                "rarely agree to that many decimal places purely by chance. It's plausible that "
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
            frac_before = crossval.get("return_frac_within_1pct_before_ticker_gate")
            lines.append(
                f"Correlation across the retained, correctly-aligned segments is "
                f"{corr:.4f}"
                + (
                    f" (versus {corr_before:.4f} before setting the misaligned/unjudgeable "
                    f"segments aside)."
                    if corr_before is not None else "."
                )
            )
            if frac_before is not None:
                lines.append(
                    f"To be clear about how much of this the ticker-alignment gate is "
                    f"actually responsible for: agreement within 1 percentage point was "
                    f"already {_fmt_pct(frac_before)} of matched days BEFORE setting any "
                    f"segment aside, versus {_fmt_pct(crossval['return_frac_within_1pct'])} "
                    f"after. The gate mainly removes a small number of confidently-wrong "
                    f"segments rather than manufacturing the headline agreement rate."
                )
            extreme_rows = crossval.get("return_extreme_diff_rows", 0)
            if extreme_rows:
                corr_trimmed = crossval.get("return_correlation_excl_extreme")
                lines.append(
                    f"A residual {_fmt_int(extreme_rows)} days "
                    f"({_fmt_pct(crossval.get('return_extreme_diff_frac', 0.0))}) still show "
                    f"a difference bigger than {_fmt_pct(EXTREME_RETURN_DIFF_THRESHOLD, 0)} "
                    f"even among the correctly-aligned segments -- an isolated bad day here "
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

    # frac-within-1pct alone can't rule out a degenerate case (high
    # agreement-within-1pt alongside near-zero correlation) -- the
    # correlation floor is a sanity check, not a precision target, so it
    # only fires when correlation is None (couldn't be computed at all).
    corr = crossval.get("return_correlation")
    positive_verdict = (
        retained_rows >= CROSSVAL_VERDICT_MIN_RETAINED_ROWS
        and crossval.get("return_frac_within_1pct") is not None
        and crossval["return_frac_within_1pct"] >= 0.95
        and (corr is None or corr >= CROSSVAL_VERDICT_MIN_CORRELATION)
    )
    pre_window_end = CROSSVAL_YEARS[0] - 1
    coverage_gap_clause = (
        f" It cannot say anything about {archive_start_year}-{pre_window_end}, which is "
        f"most of this project's window -- CRSP remains the primary, and only full-window, "
        f"source."
        if pre_window_end >= archive_start_year
        else " CRSP remains the primary, and only full-window, source."
    )
    if positive_verdict:
        lines.append(
            f"**Verdict: this is a secondary, non-blocking check, and once the misaligned "
            f"tickers and master_stock.csv's own row-level data-quality problems (both "
            f"documented above) are set aside, it holds up well for the years it can "
            f"actually test** ({_fmt_pct(crossval['return_frac_within_1pct'])} of retained "
            f"days agree within 1 percentage point).{coverage_gap_clause}"
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

# Which CRSP years get loaded into memory and joined against master_stock.csv
# for the cross-validation (see cross_validate_master_stock / _crossval_
# detail_lines). Chosen to match master_stock.csv's own coverage (its
# dates currently start 2011-01-03, per profile_master_stock_file's
# min_date_text -- see FINDINGS.md) -- but deliberately NOT derived from
# that measurement automatically (round-4 review finding): doing so would
# mean a future master_stock.csv refresh could silently widen how many
# years of CRSP get held in memory for this secondary, non-blocking check.
# If master_stock.csv's own coverage ever moves earlier (or later), widen
# (or narrow) this tuple by hand to match -- the report already states
# whatever this tuple happens to be, so a stale value degrades to "the
# check covers a narrower window than it could," not a wrong report.
CROSSVAL_YEARS = (2011, 2012, 2013)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading resolved universe PERMNO list ...", flush=True)
    resolved_permnos = load_resolved_permnos()
    print(f"  {len(resolved_permnos):,} resolved PERMNOs.", flush=True)

    print("Loading crsp.stocknames for point-in-time ticker lookup ...", flush=True)
    stocknames = load_stocknames_for_ticker_lookup(RAW_CRSP_DIR / "stocknames.parquet")
    # AAPL/MSFT are named, human-checkable spot-check examples only (see
    # split_spot_check) -- cosmetic evidence for FINDINGS.md, not load-
    # bearing for the cleaning pipeline itself. A stocknames pull that
    # happens not to include one of them degrades to skipping that one
    # example (build_findings_markdown already handles an empty spot-check
    # frame) rather than aborting an otherwise-successful run -- and,
    # per the pruning comment below, this lookup deliberately happens
    # BEFORE any prior run's output is deleted, so a failure here leaves
    # the last good output in place.
    try:
        aapl_permno = lookup_permno_for_ticker(stocknames, AAPL_TICKER)
    except ValueError as exc:
        print(f"  WARNING: {exc} -- skipping the Apple split spot-check.", flush=True)
        aapl_permno = None
    try:
        msft_permno = lookup_permno_for_ticker(stocknames, MSFT_TICKER)
    except ValueError as exc:
        print(f"  WARNING: {exc} -- skipping the Microsoft dividend spot-check.", flush=True)
        msft_permno = None

    years = discover_dsf_years()
    if not years:
        raise SystemExit(f"FATAL: no dsf_<year>.parquet files found under {RAW_CRSP_DIR}")
    print(f"Found dsf year files: {years[0]}-{years[-1]} ({len(years)} years).", flush=True)

    # Every equity_<year>.parquet this run is about to write is about to be
    # replaced -- but a YEAR NOT IN THIS RUN's discover_dsf_years() (e.g. a
    # later re-pull that covers fewer years, or a renamed/removed raw file)
    # would otherwise leave a stale prior-run file behind that a downstream
    # equity_*.parquet glob would silently mix in alongside the current
    # run's files. Pruned unconditionally up front (but only once the
    # inputs above are confirmed to exist -- see the try/excepts and the
    # discover_dsf_years check above) rather than tracked with a
    # completeness marker/manifest -- this is a fully regenerable local
    # transform (see module docstring), so "stale files never survive a
    # fresh run" is a simpler invariant than "check a marker before
    # trusting what's on disk."
    #
    # market_benchmark.parquet and FINDINGS.md are pruned the same way, for
    # the same reason: if a run fails partway through (e.g. one year's raw
    # file is corrupt), the equity_<year>.parquet files that DID succeed
    # would otherwise sit next to a PRIOR run's FINDINGS.md confidently
    # describing full coverage that no longer exists on disk. Better to
    # leave an obviously-incomplete output directory (missing files) than
    # a complete-looking but stale one.
    for stale in OUT_DIR.glob("equity_*.parquet"):
        stale.unlink()
    (OUT_DIR / "market_benchmark.parquet").unlink(missing_ok=True)
    (OUT_DIR / "FINDINGS.md").unlink(missing_ok=True)

    empty_aapl_spot = pd.DataFrame(columns=["date", "prc", "ret", "retx", "shrout", "cfacpr"])
    empty_msft_spot = pd.DataFrame(columns=["date", "prc", "ret", "retx", "shrout", "cfacpr"])

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
            crossval_frames.append(cleaned[["permno", "ticker", "date", "prc", "ret", "retx"]])
        if year in AAPL_SPLIT_YEAR_DATES and aapl_permno is not None and (cleaned["permno"] == aapl_permno).any():
            aapl_spot_by_year[year] = split_spot_check(cleaned, aapl_permno, AAPL_SPLIT_YEAR_DATES[year])
        if year == MSFT_DIVIDEND_SPOT_YEAR and msft_permno is not None and (cleaned["permno"] == msft_permno).any():
            msft_spot = split_spot_check(cleaned, msft_permno, MSFT_DIVIDEND_SPOT_DATE)

    dsf_stats = aggregate_year_stats(stats_by_year)

    print("Checking split/dividend-adjustment evidence ...", flush=True)
    events = safe_concat(event_frames_by_year, ignore_index=True)
    del event_frames_by_year  # superseded by `events`; see finding 10's memory note in the PR
    event_stats = summarize_adjustment_events(events)
    adjustment_verdict = classify_adjustment_status(event_stats, dsf_stats)
    if adjustment_verdict["overall"] != "adjusted":
        # Loud and impossible to miss: FINDINGS.md is gitignored, so a
        # human running this script locally needs to see this on the
        # terminal, not just in a report file they may not open.
        reasons_block = "\n".join(f"  - {r}" for r in adjustment_verdict["reasons"])
        print(
            f"\n{'!' * 70}\n"
            f"ADJUSTMENT STATUS: {adjustment_verdict['overall'].upper()} -- see FINDINGS.md's "
            f"'Are prices and returns already adjusted' section before trusting any "
            f"return calculation downstream of this slice.\n"
            f"{reasons_block}\n"
            f"{'!' * 70}\n",
            flush=True,
        )
    aapl_spot_2000 = aapl_spot_by_year.get(2000, empty_aapl_spot)
    aapl_spot_2005 = aapl_spot_by_year.get(2005, empty_aapl_spot)
    # event_carryover now holds each permno's single most-recently-seen row
    # across the whole pull (see last_row_per_permno) -- exactly what's
    # needed as "this permno's cfacpr at the end of the window we pulled."
    prc_adj_risk_stats = summarize_prc_adj_lookahead_risk(first_year_prc_cfacpr, event_carryover[["permno", "cfacpr"]])
    aapl_last_row = (
        event_carryover.loc[event_carryover["permno"] == aapl_permno]
        if aapl_permno is not None else event_carryover.iloc[0:0]
    )
    aapl_last_cfacpr = float(aapl_last_row["cfacpr"].iloc[0]) if len(aapl_last_row) else None
    aapl_last_date = aapl_last_row["date"].iloc[0] if len(aapl_last_row) else None

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
    del master_raw  # superseded by master_clean; see finding 10's memory note in the PR

    # Guarded rather than assumed: a CRSP pull that happens not to cover any
    # of CROSSVAL_YEARS (e.g. a partial/older re-pull), or a master_stock.csv
    # with no valid rows left after cleaning, would otherwise crash this
    # step (pd.concat([]) on an empty list raises) instead of just skipping
    # a secondary, non-blocking check.
    if crossval_frames and len(master_clean) > 0:
        crsp_crossval = safe_concat(crossval_frames, ignore_index=True)
        del crossval_frames  # superseded by crsp_crossval; see finding 10's memory note in the PR
        window_start = max(pd.Timestamp(master_clean["date"].min()), pd.Timestamp(crsp_crossval["date"].min()))
        # min(...), not just ARCHIVE_END_DATE: bounds the reported window by
        # what CRSP actually contributed to this comparison, symmetric with
        # window_start just above -- otherwise the report could claim to
        # have validated through ARCHIVE_END_DATE while the joined CRSP
        # data actually stopped earlier (or vice versa, understate a
        # window that moved later without updating this literal).
        window_end = min(pd.Timestamp(ARCHIVE_END_DATE), pd.Timestamp(crsp_crossval["date"].max()))
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
        # Computed once, here -- not recomputed a second time inside
        # build_findings_markdown (round-4 review: classify_adjustment_
        # status was being called twice per run from the same inputs).
        # Harmless for a pure function, but this is the mechanism that has
        # needed a fix in every one of the last three review rounds, so a
        # single source of truth removes one way a future change to it
        # could accidentally only be applied in one of the two call sites.
        "adjustment_verdict": adjustment_verdict,
        "aapl_permno": aapl_permno, "msft_permno": msft_permno,
        "aapl_2000": aapl_spot_2000,
        "aapl_2005": aapl_spot_2005,
        "msft_div": msft_spot,
        "master_full_stats": master_full_stats,
        "master_clean_stats": master_clean_stats,
        "crossval_stats": crossval_stats,
        "prc_adj_risk": prc_adj_risk_stats,
        "aapl_last_cfacpr": aapl_last_cfacpr,
        "aapl_last_date": aapl_last_date,
    }

    findings_md = build_findings_markdown(ctx)
    findings_path = OUT_DIR / "FINDINGS.md"
    findings_path.write_text(findings_md)
    print(f"Wrote {findings_path}", flush=True)
    print("DONE.", flush=True)


if __name__ == "__main__":
    main()
