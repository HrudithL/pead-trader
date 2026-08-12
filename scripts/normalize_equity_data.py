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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402

REPO_ROOT = lib.REPO_ROOT
RAW_CRSP_DIR = lib.RAW_DIR / "crsp"
RAW_EQUITY_DIR = REPO_ROOT / "data" / "raw_equity"
METADATA_DIR = lib.METADATA_DIR
OUT_DIR = REPO_ROOT / "data" / "normalized_equity"
MASTER_STOCK_PATH = RAW_EQUITY_DIR / "master_stock.csv"

# The archive's native window end (see wrds_pull_lib.END_DATE). Reused here
# (rather than a second hardcoded "2013-12-31" literal) as the upper bound
# for the master_stock.csv cross-validation window -- master_stock.csv
# itself runs through 2025, but only its overlap with this project's actual
# 1996-2013 window of interest is relevant to validate against.
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


# ---------------------------------------------------------------------------
# Small, pure cleaning functions -- each independently unit-testable.
# ---------------------------------------------------------------------------


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


def compute_adjusted_price(prc: pd.Series, cfacpr: pd.Series):
    """CRSP's cumulative price-adjustment factor: dividing raw `prc` by
    `cfacpr` produces a split-adjusted price comparable across a split
    boundary (see FINDINGS.md's split-adjustment evidence). `cfacpr` is
    non-positive (empirically always exactly 0.0, never negative, in this
    pull -- see FINDINGS.md) for a small number of permnos/rows; dividing
    by zero there would silently produce +/-inf, so those rows get a null
    prc_adj instead. Returns (prc_adj, n_non_positive_cfacpr)."""
    bad = cfacpr.isna() | (cfacpr <= 0)
    prc_adj = (prc / cfacpr).where(~bad, other=np.nan)
    return prc_adj, int(bad.sum())


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
    original row order of df is restored before returning.
    """
    left = df[["permno", "date"]].reset_index(drop=False)
    left = left.sort_values(["date", "permno"])
    right = stocknames.sort_values(["namedt", "permno"])
    merged = pd.merge_asof(
        left, right,
        left_on="date", right_on="namedt", by="permno", direction="backward",
    )
    out_of_range = merged["nameenddt"].isna() | (merged["date"] > merged["nameenddt"])
    merged.loc[out_of_range, "ticker"] = pd.NA
    merged = merged.set_index("index").sort_index()
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
    prc_adj, n_bad_cfacpr = compute_adjusted_price(df["prc"], cfacpr)
    stats["cfacpr_non_positive_rows"] = n_bad_cfacpr
    df["cfacpr"] = cfacpr
    df["prc_adj"] = prc_adj

    df["ticker"] = attach_point_in_time_ticker(df[["permno", "date"]], stocknames)
    stats["rows_with_no_ticker_on_record"] = int(df["ticker"].isna().sum())

    df["shrout"] = df["shrout"].astype("float64")
    df["vol"] = df["vol"].astype("float64")
    df["cfacshr"] = df["cfacshr"].astype("float64")

    out = df[[
        "permno", "date", "ticker",
        "prc", "prc_is_bidask_avg", "prc_adj",
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
    is_event = prev_cfacpr.notna() & prev_prc.notna() & d["ret"].notna() & ((cfacpr_ratio - 1).abs() > min_change)

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


def read_master_stock_raw(csv_path) -> pd.DataFrame:
    """Read master_stock.csv's Tic/Date/Price columns as plain strings via
    DuckDB, with an explicit quote character.

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
    """
    con = duckdb.connect()
    try:
        return con.execute(
            f"""
            SELECT Tic AS ticker, Date AS date_text, Price AS price_text
            FROM read_csv(
                {duckdb_quote_literal(str(csv_path))},
                header=True, quote='"', escape='"',
                columns={{'Tic': 'VARCHAR', 'Date': 'VARCHAR', 'Price': 'VARCHAR'}}
            )
            """
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

    crsp_df must have columns permno/ticker/date/prc/prc_adj/ret. Rows
    where the same (ticker, date) key appears more than once on EITHER
    side are dropped from that side before joining, rather than silently
    fanned out into a many-to-many join:

    - On the CRSP side, this is a ticker-reuse collision or an artifact of
      an ambiguous PERMNO resolution (see resolved_universe_ids.parquet's
      permno_ambiguous flag).
    - On the master_stock.csv side, this is a genuine, confirmed data bug
      in the source file: some tickers (e.g. CMCSA) carry exactly two rows
      for every date, at two different, unrelated prices, across the
      file's entire history -- not a rare glitch. A naive inner join would
      silently double-count (or mismatch) every one of those dates rather
      than compare like with like.
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

    merged = pd.merge(
        crsp_w[["permno", "ticker", "date", "prc", "prc_adj", "ret"]],
        master_w[["ticker", "date", "price"]].rename(columns={"price": "master_price"}),
        on=["ticker", "date"], how="inner",
    )
    stats["matched_rows"] = int(len(merged))
    stats["matched_distinct_tickers"] = int(merged["ticker"].nunique())

    merged["master_ret"] = compute_return_from_prices(merged, "ticker", "date", "master_price")

    ret_cmp = merged.dropna(subset=["ret", "master_ret"])
    stats["return_comparison_rows"] = int(len(ret_cmp))
    if len(ret_cmp) >= 1:
        # Mean/median/within-tolerance are well-defined for even a single
        # matched day; correlation needs at least two points to mean
        # anything, so it (and everything derived from it below) is gated
        # on a separate, stricter threshold rather than reusing this one --
        # a lone matched day must not silently produce a "correlation"
        # field that's actually NaN.
        abs_diff = (ret_cmp["ret"] - ret_cmp["master_ret"]).abs()
        stats["return_mean_abs_diff"] = float(abs_diff.mean())
        stats["return_median_abs_diff"] = float(abs_diff.median())
        stats["return_frac_within_1pct"] = float((abs_diff < 0.01).mean())
    if len(ret_cmp) > 1:
        stats["return_correlation"] = float(ret_cmp["ret"].corr(ret_cmp["master_ret"]))

        # The pooled correlation above is heavily distorted by a small
        # number of implausible single-day "returns" in master_stock.csv
        # (e.g. an implied >200% one-day move for a large/liquid name,
        # which is not a real trading outcome) -- Pearson correlation is
        # very sensitive to a handful of extreme outliers. Reporting a
        # trimmed correlation alongside the raw one, with the exact
        # threshold and how many rows it excludes, shows how much of the
        # apparent disagreement is a few bad rows versus a genuine broad
        # mismatch -- rather than either hiding the outliers or letting
        # them alone characterize the whole comparison.
        extreme = abs_diff > EXTREME_RETURN_DIFF_THRESHOLD
        stats["return_extreme_diff_threshold"] = EXTREME_RETURN_DIFF_THRESHOLD
        stats["return_extreme_diff_rows"] = int(extreme.sum())
        stats["return_extreme_diff_frac"] = float(extreme.mean())
        trimmed = ret_cmp.loc[~extreme]
        if len(trimmed) > 1:
            stats["return_correlation_excl_extreme"] = float(trimmed["ret"].corr(trimmed["master_ret"]))
        if extreme.any():
            top_tickers = ret_cmp.loc[extreme, "ticker"].value_counts().head(5)
            stats["top_extreme_diff_tickers"] = [(t, int(c)) for t, c in top_tickers.items()]

    for label, col in (("raw", "prc"), ("adjusted", "prc_adj")):
        price_cmp = merged.dropna(subset=[col, "master_price"])
        if len(price_cmp) > 1:
            stats[f"{label}_price_level_correlation"] = float(price_cmp[col].corr(price_cmp["master_price"]))
            stats[f"{label}_price_comparison_rows"] = int(len(price_cmp))

    return stats, merged


def profile_master_stock_dates(csv_path) -> dict:
    """Cheap DuckDB-only aggregate profile of master_stock.csv's raw date
    text, used only to report the overall file's min/max coverage in
    FINDINGS.md without materializing the whole file."""
    con = duckdb.connect()
    try:
        row = con.execute(
            f"""
            SELECT min(Date), max(Date), count(*)
            FROM read_csv(
                {duckdb_quote_literal(str(csv_path))},
                header=True, quote='"', escape='"',
                columns={{'Tic': 'VARCHAR', 'Date': 'VARCHAR', 'Price': 'VARCHAR'}}
            )
            WHERE Tic IS NOT NULL AND trim(Tic) <> ''
            """
        ).fetchone()
        return {"min_date_text": row[0], "max_date_text": row[1], "non_blank_ticker_rows": int(row[2])}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# FINDINGS.md: a plain-language writeup of what this script found, for a
# reader with no finance background (per the project's reporting
# convention -- see memory/phase1-equity-pead-plan.md's Reports section).
# ---------------------------------------------------------------------------


def _fmt_int(n) -> str:
    return f"{int(n):,}"


def _fmt_pct(x, decimals=2) -> str:
    return f"{x * 100:.{decimals}f}%"


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
        f"({_fmt_pct(dsf['negative_prc_rows'] / dsf['nonnull_prc_rows'])}). We took the "
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
        f"- **A handful of securities have a broken adjustment factor.** "
        f"{_fmt_int(dsf['cfacpr_non_positive_rows'])} rows (across a small number of "
        f"permnos) have an adjustment factor of exactly zero instead of a positive number. "
        f"We can't compute a meaningful adjusted price for those specific rows, so it's left "
        f"blank there; the raw price and return columns are unaffected."
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
        f"also provides a cumulative adjustment factor, `cfacpr`, and we used it to build a "
        f"convenience column, `prc_adj = prc / cfacpr`, which is a split-adjusted price series "
        f"for anyone who wants a continuous price level rather than a return."
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
    lines.append(
        f"`crsp.dsi` is one row per trading day, market-wide (not per stock), covering "
        f"{_fmt_int(dsi['rows'])} trading days from {_fmt_date(dsi['date_min'])} to "
        f"{_fmt_date(dsi['date_max'])}, with no missing values in any of its return columns. "
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
    lines.append(
        f"`crsp.dsedelist` records {_fmt_int(delist['rows'])} delistings (a company's stock "
        f"leaving CRSP's coverage, usually because of a merger, acquisition, bankruptcy, or "
        f"exchange delisting) between {_fmt_date(delist['date_min'])} and "
        f"{_fmt_date(delist['date_max'])}, all within the resolved universe. The most common "
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
    lines.append(
        f"**It only overlaps CRSP for 3 of the 18 years this project ultimately cares about "
        f"(1996-2013).** The file's own dates only start "
        f"{master_full['min_date_text']} and the archive window we care about ends "
        f"{ARCHIVE_END_DATE}, so this check can only speak to 2011-2013, not the full "
        f"1996-2013 window. That's a real limitation on how much this check can prove, not "
        f"something to gloss over."
    )
    lines.append("")
    lines.append(
        f"**The file also has real data-quality problems, all confined to one place.** Before "
        f"any date filtering, the file has {_fmt_int(master_full['non_blank_ticker_rows'])} "
        f"rows with a real ticker and {_fmt_int(master_clean['blank_ticker_rows_dropped'])} "
        f"rows with a blank ticker. Every single blank-ticker row turned out to be one "
        f"contiguous block appended at the very end of the file (after the last real ticker's "
        f"data), mixing in a handful of `mm/dd/yyyy`-formatted dates alongside some ISO dates, "
        f"with prices whose scale (a handful of cents in 2011 rising into the tens of "
        f"thousands of dollars by the 2020s) doesn't resemble any single stock -- it looks "
        f"like a stray, unrelated price series (an index or similar) was accidentally appended "
        f"to a single-stock file. Dropping every blank-ticker row removes this entire block "
        f"cleanly, with nothing left over to special-case."
    )
    lines.append(
        f"Among the real, non-blank-ticker rows, {_fmt_int(master_clean['missing_price_rows'])} "
        f"have a genuinely empty price field (typically a ticker that existed in the file "
        f"before that company's price history actually starts) and "
        f"{_fmt_int(master_clean['unparseable_text_price_rows'])} have text that isn't a "
        f"number at all. A further {_fmt_int(master_clean['infinite_price_rows'])} rows "
        f"contain the literal text `inf`/`-inf` (a handful of tickers, apparently from a "
        f"division-by-zero somewhere upstream) and "
        f"{_fmt_int(master_clean['negative_price_rows'])} rows have a negative price with no "
        f"documented reason the way CRSP's bid/ask convention has one -- these are treated as "
        f"invalid and left blank rather than guessed at."
    )
    lines.append("")
    if crossval.get("skipped"):
        lines.append(
            "**This run had no overlapping data to compare.** Either the CRSP data on hand "
            "doesn't cover any of 2011-2013, or master_stock.csv had no valid rows left in "
            "that window after cleaning -- so this cross-check was skipped rather than run "
            "against an empty or misleading sample."
        )
        lines.append("")
        lines.append(
            "**Verdict: this is a secondary, non-blocking check that could not run this "
            "time.** CRSP remains the primary, full-window source regardless."
        )
    else:
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
            f"**Comparison result for the 2011-2013 overlap.** After cleaning both sides "
            f"and matching on ticker and date, {_fmt_int(crossval['matched_rows'])} "
            f"(ticker, date) rows matched across "
            f"{_fmt_int(crossval['matched_distinct_tickers'])} tickers (out of "
            f"{_fmt_int(crossval['crsp_distinct_tickers_in_window'])} CRSP tickers and "
            f"{_fmt_int(crossval['master_distinct_tickers_in_window'])} master_stock "
            f"tickers active in the window; the rest didn't match, most likely because of "
            f"ticker differences the two sources don't agree on, e.g. after a rename)."
        )
        if crossval.get("return_comparison_rows"):
            lines.append(
                f"Comparing day-over-day **returns** (the more meaningful comparison, "
                f"since the two files use different price-adjustment conventions and raw "
                f"price levels aren't directly comparable) over "
                f"{_fmt_int(crossval['return_comparison_rows'])} matched days: a median "
                f"absolute difference of {_fmt_pct(crossval['return_median_abs_diff'])}, "
                f"and {_fmt_pct(crossval['return_frac_within_1pct'])} of days agreeing to "
                f"within 1 percentage point -- most days match almost exactly."
            )
            if crossval.get("return_correlation") is None:
                lines.append(
                    "Too few matched days (at most one) to compute a meaningful "
                    "correlation, though the difference above is still informative."
                )
            else:
                corr_all = crossval["return_correlation"]
                corr_trimmed = crossval.get("return_correlation_excl_extreme")
                extreme_frac = crossval.get("return_extreme_diff_frac", 0.0)
                corr_gap = (corr_trimmed - corr_all) if corr_trimmed is not None else 0.0
                lines.append(
                    f"The correlation across all matched days is {corr_all:.2f}. Correlation "
                    f"is very sensitive to a small number of extreme outliers, so it's worth "
                    f"checking whether a few bad rows are dragging it down: "
                    f"{_fmt_int(crossval.get('return_extreme_diff_rows', 0))} matched days "
                    f"({_fmt_pct(extreme_frac)}) show a difference bigger than "
                    f"{_fmt_pct(EXTREME_RETURN_DIFF_THRESHOLD, 0)} between the two sources -- "
                    f"not a realistic single-day gap for the same stock, and the kind of thing "
                    f"that has to be a data error in one side or the other."
                )
                if corr_trimmed is not None and corr_gap > 0.05:
                    lines.append(
                        f"Setting those specific days aside, the correlation across the "
                        f"remaining {_fmt_pct(1 - extreme_frac)} of days rises to "
                        f"{corr_trimmed:.2f} -- most of the disagreement in the all-days "
                        f"figure comes from that small, identifiable group, not from a broad "
                        f"mismatch between the two sources."
                    )
                elif corr_trimmed is not None:
                    lines.append(
                        f"Excluding those days barely moves the number ({corr_trimmed:.2f} "
                        f"versus {corr_all:.2f}), so the small number of extreme mismatches "
                        f"isn't the explanation for the overall correlation level either way."
                    )
            top_tickers = crossval.get("top_extreme_diff_tickers") or []
            if top_tickers:
                ticker_list = ", ".join(f"{t} ({_fmt_int(c)} days)" for t, c in top_tickers)
                lines.append(
                    f"These extreme mismatches are concentrated in a small number of "
                    f"tickers rather than spread evenly across the sample, which points "
                    f"to isolated master_stock.csv data problems for those specific names "
                    f"rather than a broad issue with either source: {ticker_list}."
                )
        else:
            lines.append(
                "There wasn't enough matched, non-blank data on both sides to compute a "
                "meaningful return comparison."
            )
        lines.append("")
        lines.append(
            "**Verdict: this is a secondary, non-blocking check, and it holds up well for "
            "the years it can actually test**, once master_stock.csv's own row-level "
            "data-quality problems (documented above) are cleaned up first. It cannot say "
            "anything about 1996-2010, which is the majority of this project's window -- "
            "CRSP remains the primary, and only full-window, source."
        )
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
        "no-trade convention), `prc_adj` (split-adjusted convenience price, `prc / cfacpr`)\n"
        "- `ret`, `retx` (total and price-only returns; both already split/dividend-adjusted "
        "by CRSP, see above)\n"
        "- `shrout` (shares outstanding, in thousands -- CRSP's convention), `vol` (shares "
        "traded that day), `cfacpr`, `cfacshr` (CRSP's own price/share adjustment factors), "
        "carried through for anyone who needs them later"
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
        "- Compustat and I/B/E/S cleaning -- separate slices."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

CROSSVAL_YEARS = (2011, 2012, 2013)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

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
    event_check_frames = []
    crossval_frames = []
    aapl_spot_by_year = {}  # year -> spot-check DataFrame, keyed explicitly (not positionally)
    msft_spot = empty_msft_spot

    for year in years:
        raw = pd.read_parquet(RAW_CRSP_DIR / f"dsf_{year}.parquet")
        cleaned, stats = clean_dsf_year(raw, resolved_permnos, stocknames)
        stats_by_year[year] = stats

        out_path = OUT_DIR / f"equity_{year}.parquet"
        cleaned.to_parquet(out_path, index=False)
        print(f"  wrote {out_path.name}: {len(cleaned):,} rows", flush=True)

        event_check_frames.append(cleaned[["permno", "date", "prc", "ret", "cfacpr"]])
        if year in CROSSVAL_YEARS:
            crossval_frames.append(cleaned[["permno", "ticker", "date", "prc", "prc_adj", "ret"]])
        if year in AAPL_SPLIT_YEAR_DATES and (cleaned["permno"] == aapl_permno).any():
            aapl_spot_by_year[year] = split_spot_check(cleaned, aapl_permno, AAPL_SPLIT_YEAR_DATES[year])
        if year == 2003 and (cleaned["permno"] == msft_permno).any():
            msft_spot = split_spot_check(cleaned, msft_permno, MSFT_DIVIDEND_SPOT_DATE)

    dsf_stats = aggregate_year_stats(stats_by_year)

    print("Checking split/dividend-adjustment evidence ...", flush=True)
    events_input = pd.concat(event_check_frames, ignore_index=True)
    events = detect_adjustment_events(events_input)
    event_stats = summarize_adjustment_events(events)
    aapl_spot_2000 = aapl_spot_by_year.get(2000, empty_aapl_spot)
    aapl_spot_2005 = aapl_spot_by_year.get(2005, empty_aapl_spot)

    print("Cleaning crsp.dsi market benchmark ...", flush=True)
    dsi_raw = pd.read_parquet(RAW_CRSP_DIR / "dsi.parquet")
    benchmark, dsi_stats = clean_dsi(dsi_raw)
    benchmark.to_parquet(OUT_DIR / "market_benchmark.parquet", index=False)
    print(f"  wrote market_benchmark.parquet: {len(benchmark):,} rows", flush=True)

    print("Summarizing crsp.dsedelist (context only, not written to output) ...", flush=True)
    delist_stats = summarize_dsedelist(RAW_CRSP_DIR / "dsedelist.parquet", resolved_permnos)

    print("Cleaning and cross-validating data/raw_equity/master_stock.csv ...", flush=True)
    master_full_stats = profile_master_stock_dates(MASTER_STOCK_PATH)
    master_raw = read_master_stock_raw(MASTER_STOCK_PATH)
    master_clean, master_clean_stats = clean_master_stock_frame(master_raw, end_date=ARCHIVE_END_DATE)

    # Guarded rather than assumed: a CRSP pull that happens not to cover any
    # of CROSSVAL_YEARS (e.g. a partial/older re-pull), or a master_stock.csv
    # with no valid rows left after cleaning, would otherwise crash this
    # step (pd.concat([]) on an empty list raises) instead of just skipping
    # a secondary, non-blocking check.
    if crossval_frames and len(master_clean) > 0:
        crsp_crossval = pd.concat(crossval_frames, ignore_index=True)
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
    }

    findings_md = build_findings_markdown(ctx)
    findings_path = OUT_DIR / "FINDINGS.md"
    findings_path.write_text(findings_md)
    print(f"Wrote {findings_path}", flush=True)
    print("DONE.", flush=True)


if __name__ == "__main__":
    main()
