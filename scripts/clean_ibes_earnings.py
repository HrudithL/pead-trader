"""Cleans and joins the WRDS I/B/E/S pull (data/raw_wrds/ibes/actu_epsus.parquet
and statsumu_epsus.parquet) into a single point-in-time-safe earnings/surprise
table, one row per (I/B/E/S ticker, fiscal quarter):

    data/earnings/ibes_clean_1995_2014.parquet

Scope: this cleans at the FULL resolved-universe level (every I/B/E/S ticker
that resolved from the 8,358-secid OptionMetrics-linked universe -- see
data/metadata/resolved_universe_ids.parquet), not filtered to any pilot
list. The pilot-universe filter is a separate, still-open slice and is
applied downstream, later, at event-table-assembly time -- see
memory/phase1-equity-pead-plan.md's 2026-08-12 "Slice 5 is decoupled from
slice 3" update.

Filename note: the plan's own working title for this file was
"ibes_clean_1996_2013.parquet" (the project's core phase window). The
actual pulled announcement-date (anndats) range turned out to be
1995-01-03 through 2014-06-30 -- a deliberate one-year lookback / six-month
forward buffer around the 1996-2013 core window (see wrds_pull_lib.py's
LOOKBACK_START_DATE/RETURN_END_DATE), not a pull error. This module keeps
every row that buffer produced (a later slice can filter to any date window
it needs; restricting here would just mean re-deriving history this module
already has) and names the file after what is actually on disk.

Two WRDS-pull schema wrinkles drive most of the logic below (both are
covered in detail in data/earnings/FINDINGS.md, written by this script's
main()):

1. Every I/B/E/S date column landed as VARCHAR, not a real date type --
   parsed explicitly here (see _parse_date/_parse_time).
2. ibes.actu_epsus was pulled without a period-type indicator column
   (I/B/E/S's actuals table can carry BOTH a quarterly and an annual
   actual EPS under the exact same ticker/period-end/announcement-date --
   a fiscal Q4 and fiscal year-end are the same calendar date -- and
   without that column there is no direct way to tell them apart). See
   resolve_actual_duplicates() for how this is handled without silently
   guessing.

Usage:
    python scripts/clean_ibes_earnings.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_IBES_DIR = REPO_ROOT / "data" / "raw_wrds" / "ibes"
METADATA_DIR = REPO_ROOT / "data" / "metadata"
OUTPUT_DIR = REPO_ROOT / "data" / "earnings"

ACTU_EPSUS_PATH = RAW_IBES_DIR / "actu_epsus.parquet"
STATSUMU_EPSUS_PATH = RAW_IBES_DIR / "statsumu_epsus.parquet"
RESOLVED_UNIVERSE_PATH = METADATA_DIR / "resolved_universe_ids.parquet"

OUTPUT_PATH = OUTPUT_DIR / "ibes_clean_1995_2014.parquet"
FINDINGS_PATH = OUTPUT_DIR / "FINDINGS.md"

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M:%S"

# The plan calls for "at least ~4-5 prior quarters of actual EPS history"
# before trusting sue_seasonal's scaling std dev; 4 is the low end of that
# range. See FINDINGS.md for how much coverage this threshold produces.
MIN_SEASONAL_DIFF_HISTORY = 4

# Trailing window (in quarters) used to compute sue_seasonal's scaling std
# dev, matching the classic Foster/Olsen/Shevlin (1984) seasonal-random-walk
# SUE construction: recent volatility, not a company's entire multi-decade
# history, is what should set the scale. An all-history expanding window
# was tried first and rejected after it produced fragile results across
# reporting gaps/regime changes -- e.g. a std dev computed from a company's
# early sub-cent-EPS years staying in scope to scale a post-restructuring
# dollar-scale surprise a decade later, producing a nonsensical
# several-hundred-sigma result. See FINDINGS.md.
ROLLING_SEASONAL_WINDOW = 8

# A standard deviation smaller than this (about 1/100th of a cent of EPS) is
# treated as zero dispersion, not real variation. This matters because of a
# genuine floating-point trap, not just a style preference: when a ticker's
# inputs to a std-dev calculation are identical (e.g. four straight
# year-over-year EPS diffs that all round to exactly 0.14), the true
# variance is exactly zero, but summing squared floating-point deviations
# from a floating-point mean can leave a tiny nonzero residual (observed in
# this data down to ~1e-17) instead of an exact 0.0 -- catastrophic
# cancellation, not a data error. An exact `!= 0` check does not catch that
# residual, and dividing by it produces nonsense standardized surprises in
# the trillions. Confirmed against the real pull: this fires for a small
# number of young/steadily-growing tickers whose EPS history happens to
# increase by an identical rounded amount every year.
_ZERO_STDEV_EPSILON = 1e-6

FINAL_COLUMNS = [
    "ibes_ticker", "permno", "gvkey", "identifier_matched", "identifier_ambiguous",
    "fpedats", "anndats", "anntims", "eps_actual", "eps_actual_dup_flag",
    "consensus_statpers", "consensus_meanest", "consensus_medest", "consensus_stdev", "consensus_numest",
    "sue_analyst",
    "eps_year_ago", "eps_diff_yoy", "seasonal_n_prior_diffs", "seasonal_diff_stdev", "sue_seasonal",
    "sue_primary", "sue_source",
]


# ---------------------------------------------------------------------------
# Date/time parsing.
# ---------------------------------------------------------------------------


def _parse_date(series: pd.Series) -> pd.Series:
    """Parses a VARCHAR YYYY-MM-DD column (as pulled from WRDS) into a real
    datetime64 column. Every I/B/E/S date column landed as a string, not a
    date type -- see memory/phase1-equity-pead-plan.md's 2026-08-12 update.
    `errors="raise"` is deliberate: a value that doesn't match is a pull/
    schema problem worth crashing loudly on, not silently coercing to NaT.
    """
    return pd.to_datetime(series, format=DATE_FORMAT, errors="raise")


def _parse_time(series: pd.Series) -> pd.Series:
    """Parses a VARCHAR HH:MM:SS column into a datetime.time. anntims is
    never null in actu_epsus (confirmed during the WRDS pull audit)."""
    return pd.to_datetime(series, format=TIME_FORMAT, errors="raise").dt.time


# ---------------------------------------------------------------------------
# Per-table cleaning.
# ---------------------------------------------------------------------------


def clean_actuals(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Parses dates/times and applies the currency + non-null-value filters
    to the raw actu_epsus pull. Does NOT yet resolve (ticker, fpedats)
    duplicates -- that needs the cleaned consensus table too (see
    resolve_actual_duplicates()).

    Returns (cleaned, stats). `cleaned` has columns
    [ticker, fpedats, anndats, anntims, eps_actual] and may still have
    multiple rows per (ticker, fpedats) -- real duplication, not yet
    resolved. `stats` records how many rows were dropped at each step, for
    FINDINGS.md.
    """
    stats = {"raw_rows": len(raw)}
    df = raw.copy()

    df["anndats"] = _parse_date(df["anndats"])
    df["pends"] = _parse_date(df["pends"])
    df["anntims"] = _parse_time(df["anntims"])

    # Late/restated filings of pre-1995 fiscal periods, announced in-window
    # -- not a pull bug (the pull filtered on anndats, not pends). Counted
    # here, not dropped: see resolve_actual_duplicates()/FINDINGS.md for why
    # keeping them is safe as long as the point-in-time filter always keys
    # off anndats, never pends.
    stats["pre_1995_pends_rows"] = int((df["pends"] < pd.Timestamp("1995-01-01")).sum())

    # This project is US-equity-centric; a handful of foreign-currency
    # echoes of the same USD-reported figure exist for cross-listed/ADR
    # names (see FINDINGS.md) and would otherwise show up as spurious
    # (ticker, fpedats) duplicates with wildly different "surprise" values
    # driven by FX, not earnings.
    non_usd = int((df["curr_act"] != "USD").sum())
    stats["non_usd_rows_dropped"] = non_usd
    df = df[df["curr_act"] == "USD"]

    null_value = int(df["value"].isna().sum())
    stats["null_actual_eps_rows_dropped"] = null_value
    df = df[df["value"].notna()]

    df = df.rename(columns={"pends": "fpedats", "value": "eps_actual"})
    df = df[["ticker", "fpedats", "anndats", "anntims", "eps_actual"]]

    before_exact_dedup = len(df)
    # anntims is deliberately excluded from the dedup key: two rows that
    # agree on ticker/fpedats/anndats/eps_actual and differ only in a
    # sub-day timestamp are the same observation for this project's
    # purposes.
    df = df.drop_duplicates(subset=["ticker", "fpedats", "anndats", "eps_actual"]).reset_index(drop=True)
    stats["exact_duplicate_rows_dropped"] = before_exact_dedup - len(df)

    stats["cleaned_rows"] = len(df)
    stats["distinct_ticker_fpedats_groups"] = df.drop_duplicates(["ticker", "fpedats"]).shape[0]
    return df, stats


def clean_consensus(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Filters the raw statsumu_epsus pull down to the current-quarter
    analyst consensus (fpi='6', measure='EPS', fiscalp='QTR' -- confirmed
    against the real schema to be a clean, redundant filter: every fpi='6'
    row already has measure='EPS' and fiscalp='QTR', so this is a defensive
    triple-check, not three independent cuts) and USD-denominated rows,
    then parses statpers/fpedats to real dates.
    """
    stats = {"raw_rows": len(raw)}
    df = raw[(raw["fpi"] == "6") & (raw["measure"] == "EPS") & (raw["fiscalp"] == "QTR")].copy()
    stats["fpi6_qtr_eps_rows"] = len(df)

    non_usd = int((df["curcode"] != "USD").sum())
    stats["non_usd_rows_dropped"] = non_usd
    df = df[df["curcode"] == "USD"]

    df["statpers"] = _parse_date(df["statpers"])
    df["fpedats"] = _parse_date(df["fpedats"])

    df = df[["ticker", "statpers", "fpedats", "meanest", "medest", "stdev", "numest"]].reset_index(drop=True)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["ticker", "statpers", "fpedats"], keep="last").reset_index(drop=True)
    stats["duplicate_snapshot_rows_dropped"] = before_dedup - len(df)

    stats["cleaned_rows"] = len(df)
    return df, stats


# ---------------------------------------------------------------------------
# Point-in-time consensus selection -- the load-bearing correctness piece.
# ---------------------------------------------------------------------------


def select_point_in_time_consensus(keys: pd.DataFrame, consensus: pd.DataFrame) -> pd.DataFrame:
    """For each (ticker, fpedats, anndats) row in `keys`, finds the most
    recent analyst-consensus snapshot that was ALREADY PUBLIC before the
    announcement -- i.e. the consensus row for the same (ticker, fpedats)
    with the maximum STATPERS strictly less than anndats.

    This is the point-in-time filter. A consensus snapshot computed on or
    after the announcement date could only exist because it was compiled
    from analyst estimates recorded on or after that date -- using it to
    compute a "surprise" would silently hand the model information no
    market participant actually had at announcement time (look-ahead
    bias), which would invalidate any PEAD result built on top of it. The
    filter is the strict inequality `statpers < anndats` below -- nothing
    implicit.

    `keys` must have columns [ticker, fpedats, anndats], one row per event
    needing a match (duplicate (ticker, fpedats) keys are fine -- anndats
    travels with each row independently, which is what
    resolve_actual_duplicates() relies on to disambiguate before a single
    anndats per group is settled).

    Returns keys.reset_index(drop=True) with added columns:
      consensus_statpers, consensus_meanest, consensus_medest,
      consensus_stdev, consensus_numest -- the selected snapshot's fields
        (all null if no eligible snapshot exists).
      n_candidates -- how many consensus rows share this (ticker, fpedats)
        at all, eligible or not.
      n_lookahead_excluded -- of those, how many had statpers >= anndats
        and were correctly excluded from selection. Summed across a whole
        table, this is a concrete measure of how much look-ahead
        contamination the filter is actually preventing.
    """
    keys = keys.reset_index(drop=True).copy()
    keys["_row_id"] = np.arange(len(keys))

    merged = keys.merge(consensus, on=["ticker", "fpedats"], how="left")

    has_candidate = merged["statpers"].notna()
    candidate_counts = merged.loc[has_candidate].groupby("_row_id").size()

    eligible = has_candidate & (merged["statpers"] < merged["anndats"])
    eligible_rows = merged.loc[eligible]
    eligible_counts = eligible_rows.groupby("_row_id").size()

    lookahead_excluded = candidate_counts.sub(eligible_counts, fill_value=0)

    best_idx = eligible_rows.groupby("_row_id")["statpers"].idxmax()
    best = eligible_rows.loc[best_idx].set_index("_row_id")

    out = keys.set_index("_row_id")
    out["consensus_statpers"] = best["statpers"]
    out["consensus_meanest"] = best["meanest"]
    out["consensus_medest"] = best["medest"]
    out["consensus_stdev"] = best["stdev"]
    out["consensus_numest"] = best["numest"]
    out["n_candidates"] = candidate_counts.reindex(out.index, fill_value=0).astype(int)
    out["n_lookahead_excluded"] = lookahead_excluded.reindex(out.index, fill_value=0).astype(int)

    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Resolving actu_epsus's (ticker, fpedats) duplicates.
# ---------------------------------------------------------------------------


def resolve_actual_duplicates(actuals: pd.DataFrame, consensus: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Collapses `actuals` (still possibly multiple rows per (ticker,
    fpedats) -- see clean_actuals) down to exactly one row per (ticker,
    fpedats), per this module's docstring's item 2.

    Two distinct kinds of duplication show up, resolved in this order:

    1. Genuinely different announcement dates for the same fiscal period
       (a small minority -- true re-announcements/restatements). Keeps the
       EARLIEST anndats: that is the date the market actually first
       learned the result, which is what a PEAD study needs to key off of.

    2. Same (ticker, fpedats, anndats) but a different EPS value. This is
       overwhelmingly I/B/E/S's actuals table carrying BOTH the quarterly
       and the full fiscal-year actual under the same period-end date (a
       company's Q4 and its fiscal year-end fall on the same calendar
       date), conflated here because the pulled column set has no
       period-type indicator to tell them apart. Resolved by picking
       whichever candidate value sits closest to the point-in-time-safe
       quarterly analyst consensus mean for that (ticker, fpedats) -- that
       consensus was only ever asked to forecast the QUARTERLY figure, so
       it sits far closer to the true quarterly actual than to a several-
       times-larger annual one. Where no consensus is available at all to
       compare against, falls back to the smaller-magnitude candidate
       (quarterly EPS is smaller in magnitude than annual EPS for the same
       company in the large majority of cases). Both paths are recorded in
       eps_actual_dup_flag so the choice is auditable, not hidden.
    """
    stats = {}
    df = actuals.copy()
    stats["rows_before_dedup"] = len(df)
    stats["groups_before_dedup"] = df.drop_duplicates(["ticker", "fpedats"]).shape[0]

    min_anndats = df.groupby(["ticker", "fpedats"])["anndats"].transform("min")
    stats["rows_dropped_for_later_anndats"] = int((df["anndats"] != min_anndats).sum())
    df = df[df["anndats"] == min_anndats].copy()

    dup_mask = df.duplicated(subset=["ticker", "fpedats"], keep=False)
    unique_part = df.loc[~dup_mask].copy()
    dup_part = df.loc[dup_mask].copy()

    stats["ambiguous_value_groups"] = dup_part.drop_duplicates(["ticker", "fpedats"]).shape[0]
    stats["ambiguous_value_rows"] = len(dup_part)

    unique_part["eps_actual_dup_flag"] = "unique"

    if len(dup_part) == 0:
        # Short-circuit rather than concatenating with a genuinely empty
        # 0-row frame: pandas' concat internals hit a deprecated NumPy
        # datetime64 NA-inference path on an empty datetime64 block (a
        # pandas/NumPy version wart, not a data issue), and there is
        # nothing to resolve anyway.
        stats["ambiguous_resolved_via_consensus"] = 0
        stats["ambiguous_resolved_via_magnitude_fallback"] = 0
        stats["rows_after_dedup"] = len(unique_part)
        return unique_part, stats

    keys = dup_part[["ticker", "fpedats", "anndats"]]
    matched = select_point_in_time_consensus(keys, consensus)
    dup_part = dup_part.reset_index(drop=True)
    consensus_meanest = matched["consensus_meanest"].to_numpy()

    has_ref = ~np.isnan(consensus_meanest)
    eps_actual_values = dup_part["eps_actual"].to_numpy()
    dist = np.where(
        has_ref,
        np.abs(eps_actual_values - consensus_meanest),
        np.abs(eps_actual_values),
    )
    dup_part["_dist"] = dist
    dup_part["eps_actual_dup_flag"] = np.where(has_ref, "consensus_disambiguated", "magnitude_fallback")

    best_idx = dup_part.groupby(["ticker", "fpedats"])["_dist"].idxmin()
    resolved = dup_part.loc[best_idx].drop(columns=["_dist"]).reset_index(drop=True)

    stats["ambiguous_resolved_via_consensus"] = int((resolved["eps_actual_dup_flag"] == "consensus_disambiguated").sum())
    stats["ambiguous_resolved_via_magnitude_fallback"] = int((resolved["eps_actual_dup_flag"] == "magnitude_fallback").sum())

    # Same empty-frame concat wart as above, mirrored: if every row turned
    # out to be part of an ambiguous group, unique_part is the empty side
    # this time.
    out = resolved if len(unique_part) == 0 else pd.concat([unique_part, resolved], ignore_index=True)
    stats["rows_after_dedup"] = len(out)
    if out.duplicated(["ticker", "fpedats"]).any():
        raise AssertionError("resolve_actual_duplicates postcondition violated: duplicates remain")
    return out, stats


# ---------------------------------------------------------------------------
# Surprise measures.
# ---------------------------------------------------------------------------


def compute_sue_analyst(eps_actual: pd.Series, meanest: pd.Series, stdev: pd.Series) -> pd.Series:
    """Standardized unexpected earnings vs. the real analyst consensus:
    (actual - mean estimate) / dispersion (stdev across analysts).

    Null whenever stdev is null (statsumu_epsus reports stdev as null when
    numest==1: a single analyst has no defined dispersion) or effectively
    zero, i.e. at or below _ZERO_STDEV_EPSILON (numest>1 but every analyst
    submitted the same or a near-identical estimate -- dividing by zero
    dispersion is undefined, not "an infinite surprise"; see
    _ZERO_STDEV_EPSILON's docstring for why an exact `!= 0` check is not
    safe here). Both are real, documented data limits -- see FINDINGS.md
    for counts.
    """
    stdev_safe = stdev.where(stdev.notna() & (stdev.abs() > _ZERO_STDEV_EPSILON))
    return (eps_actual - meanest) / stdev_safe


def compute_sue_seasonal(
    panel: pd.DataFrame,
    min_history: int = MIN_SEASONAL_DIFF_HISTORY,
    window: int = ROLLING_SEASONAL_WINDOW,
) -> pd.DataFrame:
    """Adds eps_year_ago, eps_diff_yoy, seasonal_n_prior_diffs,
    seasonal_diff_stdev, and sue_seasonal to `panel` (one row per (ticker,
    fpedats) -- see resolve_actual_duplicates).

    sue_seasonal = (EPS this quarter - EPS the same fiscal quarter one year
    ago) / (std dev of that same difference over this ticker's OWN recent
    history). "Same fiscal quarter one year ago" is matched by requiring an
    EXACT fpedats-minus-one-year hit for the same ticker; about 88% of
    quarters have one (the rest -- mostly 52/53-week fiscal calendars that
    drift by a few days -- are left null rather than guessed at with a
    fuzzy date window that risks quietly comparing the wrong quarter; see
    FINDINGS.md).

    The scaling std dev only ever looks at the trailing `window` PRIOR
    quarters (`.shift(1)` before `.rolling(window)`), so a quarter's own
    diff is never used to help define its own scale, and a quarter needs at
    least `min_history` valid prior diffs within that window before
    sue_seasonal is computed at all -- otherwise it is left null (per the
    plan: null, not zero, since zero would silently claim perfect
    predictability rather than admit the scale is unknown).
    """
    df = panel.sort_values(["ticker", "fpedats"]).reset_index(drop=True).copy()

    prior = df[["ticker", "fpedats", "eps_actual"]].rename(
        columns={"fpedats": "prior_fpedats", "eps_actual": "eps_year_ago"}
    )
    df["prior_fpedats"] = df["fpedats"] - pd.DateOffset(years=1)
    df = df.merge(prior, on=["ticker", "prior_fpedats"], how="left").drop(columns=["prior_fpedats"])

    df["eps_diff_yoy"] = df["eps_actual"] - df["eps_year_ago"]

    grouped_diff = df.groupby("ticker")["eps_diff_yoy"]
    prior_count = grouped_diff.transform(lambda s: s.shift(1).rolling(window, min_periods=1).count())
    prior_std = grouped_diff.transform(lambda s: s.shift(1).rolling(window, min_periods=1).std())

    df["seasonal_n_prior_diffs"] = prior_count.astype(int)
    # Explicit min-history gate, independent of pandas' own min_periods/NaN
    # interaction: a std computed from fewer than min_history valid prior
    # diffs is nulled out here regardless of what rolling(...).std() itself
    # would have returned.
    df["seasonal_diff_stdev"] = prior_std.where(prior_count >= min_history)

    stdev_safe = df["seasonal_diff_stdev"].where(
        df["seasonal_diff_stdev"].notna() & (df["seasonal_diff_stdev"].abs() > _ZERO_STDEV_EPSILON)
    )
    df["sue_seasonal"] = df["eps_diff_yoy"] / stdev_safe
    return df


# ---------------------------------------------------------------------------
# Identifiers.
# ---------------------------------------------------------------------------


def _union_tokens(raw_values) -> tuple:
    """Splits every comma-joined value in `raw_values` (resolved_universe_
    ids.parquet's own convention for an ambiguous resolution) and returns
    the sorted union as (joined_string_or_None, is_ambiguous)."""
    tokens = sorted({
        tok
        for val in raw_values
        if pd.notna(val)
        for tok in str(val).split(",")
        if tok
    })
    joined = ",".join(tokens) if tokens else None
    return joined, len(tokens) > 1


def build_identifier_crosswalk(resolved: pd.DataFrame) -> pd.DataFrame:
    """Builds an ibes_ticker -> (permno, gvkey) lookup from
    resolved_universe_ids.parquet, which is one row per OptionMetrics
    secid (8,358 rows), not per I/B/E/S ticker.

    resolved_universe_ids records an ambiguous resolution as a
    comma-joined string within a single row (e.g. ibes_ticker="AGT,CHO")
    rather than silently picking one candidate. This function honors that
    same convention one level up: after exploding those comma lists into
    real, individually-joinable I/B/E/S ticker values, a single ticker can
    still end up associated with more than one distinct PERMNO/GVKEY --
    most commonly because the SAME company was reassigned a new PERMNO/
    GVKEY after a corporate restructuring, occasionally because the ticker
    was reused by an unrelated company later. Where that happens, every
    distinct value found is kept, comma-joined, and identifier_ambiguous
    is set True rather than guessing -- a later slice with announcement-
    date context to disambiguate against equity history can resolve it
    properly; this one cannot.
    """
    df = resolved[["ibes_ticker", "permno", "gvkey"]].copy()
    df = df[df["ibes_ticker"].notna() & (df["ibes_ticker"] != "")]
    df = df.assign(ibes_ticker=df["ibes_ticker"].str.split(",")).explode("ibes_ticker")

    rows = []
    for ticker, group in df.groupby("ibes_ticker"):
        permno_value, permno_ambiguous = _union_tokens(group["permno"])
        gvkey_value, gvkey_ambiguous = _union_tokens(group["gvkey"])
        rows.append({
            "ibes_ticker": ticker,
            "permno": permno_value,
            "gvkey": gvkey_value,
            "n_secids": len(group),
            "identifier_ambiguous": bool(permno_ambiguous or gvkey_ambiguous),
        })
    return pd.DataFrame(rows)


def attach_identifiers(panel: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Left-joins the ibes_ticker -> permno/gvkey crosswalk onto `panel`
    (keyed on `ticker`), renaming `ticker` to `ibes_ticker` on the way out
    to make clear which ticker namespace it is (I/B/E/S's own, not
    OptionMetrics' or any other vendor's)."""
    df = panel.merge(crosswalk, left_on="ticker", right_on="ibes_ticker", how="left")
    df["identifier_matched"] = df["n_secids"].notna() & (df["n_secids"] > 0)
    ambiguous = df["identifier_ambiguous"]
    df["identifier_ambiguous"] = ambiguous.where(ambiguous.notna(), False).astype(bool)
    # Drop the crosswalk's OWN ibes_ticker column, not panel's `ticker` --
    # the crosswalk's copy is null for any unmatched row (nothing on the
    # right side of the left join to populate it with), while panel's
    # `ticker` is always populated, so it is the one renamed and kept.
    df = df.drop(columns=["ibes_ticker", "n_secids"]).rename(columns={"ticker": "ibes_ticker"})
    return df


# ---------------------------------------------------------------------------
# Preemptive check: prefer real analyst data, fall back to seasonal.
# ---------------------------------------------------------------------------


def assign_sue_primary(df: pd.DataFrame) -> pd.DataFrame:
    """The preemptive-check the plan calls for: prefer the real analyst-
    consensus surprise when it is available for this (ticker, fiscal
    quarter); fall back to the seasonal/mechanical measure only where
    analyst coverage is missing; and if neither is computable, say so
    explicitly (sue_source="none") rather than leaving an unexplained
    null."""
    df = df.copy()
    has_analyst = df["sue_analyst"].notna()
    has_seasonal = df["sue_seasonal"].notna()
    df["sue_primary"] = np.where(has_analyst, df["sue_analyst"], df["sue_seasonal"])
    df["sue_source"] = np.select([has_analyst, has_seasonal], ["analyst", "seasonal"], default="none")
    return df


# ---------------------------------------------------------------------------
# End-to-end pipeline.
# ---------------------------------------------------------------------------


def build_event_table(
    actu_raw: pd.DataFrame, statsumu_raw: pd.DataFrame, resolved_raw: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """Runs the full cleaning pipeline over already-loaded raw DataFrames
    (see main() for the disk I/O). Returns (event_table, stats), where
    `stats` collects every count referenced in FINDINGS.md."""
    stats = {}

    actuals, actu_stats = clean_actuals(actu_raw)
    stats["actuals"] = actu_stats

    consensus, consensus_stats = clean_consensus(statsumu_raw)
    stats["consensus"] = consensus_stats

    actuals_resolved, dedup_stats = resolve_actual_duplicates(actuals, consensus)
    stats["dedup"] = dedup_stats

    keys = actuals_resolved[["ticker", "fpedats", "anndats"]]
    matched = select_point_in_time_consensus(keys, consensus)

    panel = actuals_resolved.reset_index(drop=True).copy()
    panel["consensus_statpers"] = matched["consensus_statpers"]
    panel["consensus_meanest"] = matched["consensus_meanest"]
    panel["consensus_medest"] = matched["consensus_medest"]
    panel["consensus_stdev"] = matched["consensus_stdev"]
    panel["consensus_numest"] = matched["consensus_numest"]

    stats["point_in_time"] = {
        "events_with_any_candidate_consensus": int((matched["n_candidates"] > 0).sum()),
        "events_with_a_lookahead_candidate_excluded": int((matched["n_lookahead_excluded"] > 0).sum()),
        "total_lookahead_snapshots_excluded": int(matched["n_lookahead_excluded"].sum()),
        "events_with_point_in_time_safe_consensus": int(panel["consensus_meanest"].notna().sum()),
    }

    panel["sue_analyst"] = compute_sue_analyst(panel["eps_actual"], panel["consensus_meanest"], panel["consensus_stdev"])

    panel = compute_sue_seasonal(panel)

    crosswalk = build_identifier_crosswalk(resolved_raw)
    panel = attach_identifiers(panel, crosswalk)

    panel = assign_sue_primary(panel)

    stats["coverage"] = {
        "total_events": len(panel),
        "sue_analyst_available": int(panel["sue_analyst"].notna().sum()),
        "sue_seasonal_available": int(panel["sue_seasonal"].notna().sum()),
        "sue_primary_from_analyst": int((panel["sue_source"] == "analyst").sum()),
        "sue_primary_from_seasonal": int((panel["sue_source"] == "seasonal").sum()),
        "sue_primary_none": int((panel["sue_source"] == "none").sum()),
        "identifier_matched": int(panel["identifier_matched"].sum()),
        "identifier_ambiguous": int(panel["identifier_ambiguous"].sum()),
        "identifier_unmatched": int((~panel["identifier_matched"]).sum()),
        "sue_analyst_extreme": int((panel["sue_analyst"].abs() > 100).sum()),
        "sue_seasonal_extreme": int((panel["sue_seasonal"].abs() > 100).sum()),
    }

    panel = panel[FINAL_COLUMNS].sort_values(["ibes_ticker", "fpedats"]).reset_index(drop=True)
    return panel, stats


# ---------------------------------------------------------------------------
# FINDINGS.md rendering.
# ---------------------------------------------------------------------------


def render_findings(panel: pd.DataFrame, stats: dict) -> str:
    actu = stats["actuals"]
    cons = stats["consensus"]
    dedup = stats["dedup"]
    pit = stats["point_in_time"]
    cov = stats["coverage"]

    anndats_min = panel["anndats"].min()
    anndats_max = panel["anndats"].max()

    pct = lambda n, d: f"{100 * n / d:.1f}%" if d else "n/a"

    return f"""# I/B/E/S Earnings Data: Findings

This note documents how the raw WRDS I/B/E/S pull was turned into a clean
earnings-surprise table, one row per (company, fiscal quarter), and what was
found along the way. It assumes no finance background.

## What "surprise" and "point-in-time" mean here

When a company reports quarterly earnings (its "actual EPS" -- earnings per
share), the stock market's reaction depends on whether that number beat or
missed what investors were expecting, not on the raw number itself. "Surprise"
is a measure of that gap: actual EPS minus an expectation, scaled so
surprises are comparable across companies and time. This project computes two
versions:

- `sue_analyst`: the expectation is the average Wall Street analyst estimate
  for that quarter (from I/B/E/S), and the scaling is how much analysts
  disagreed with each other (their estimates' standard deviation). This is
  the more faithful measure when analyst coverage exists.
- `sue_seasonal`: a mechanical fallback that does not need analyst data at
  all -- the expectation is simply "the same quarter's result a year ago,"
  scaled by how volatile that year-over-year change has historically been for
  that specific company.

"Point-in-time" means: only use information that was actually public at the
time being studied. Analyst consensus estimates get updated continuously --
including AFTER a company reports -- so it is easy to accidentally compute a
"surprise" using an estimate that was itself informed by the real result
(because it was calculated after the announcement). That is called
look-ahead bias, and it would make any later PEAD result meaningless, since
it would be testing whether the market reacts to information it never
actually had. Every consensus estimate used below is checked to have been
published strictly before the announcement it is being compared against.

## Coverage found

- Cleaned announcement dates (`anndats`) span **{anndats_min.date()} to
  {anndats_max.date()}**. The output file is named `ibes_clean_1995_2014.parquet`
  to match -- the plan's working title referenced the project's core
  1996-2013 window, but the actual WRDS pull included a one-year lookback
  and roughly six-month forward buffer around it (by design, so that
  quarters right at each edge of the core window still have prior-year
  history and prior consensus snapshots to draw on). Nothing was trimmed to
  force-fit the original filename.
- This cleans I/B/E/S at the full resolved-universe level (every ticker that
  resolved from the 8,358-secid OptionMetrics-linked universe), not filtered
  to any pilot list -- the pilot filter is a separate, later slice.
- Raw `actu_epsus.parquet`: {actu['raw_rows']:,} rows. After dropping {actu['non_usd_rows_dropped']:,} non-USD-denominated rows (foreign-currency echoes of the same figure for cross-listed/ADR names) and {actu['null_actual_eps_rows_dropped']:,} rows with no reported EPS value at all, {actu['cleaned_rows']:,} usable rows remained ({actu['distinct_ticker_fpedats_groups']:,} distinct company/fiscal-quarter combinations before duplicate resolution).
- Raw `statsumu_epsus.parquet`: {cons['raw_rows']:,} rows. Filtered to the current-quarter consensus (`fpi='6'`, which turned out to already imply `measure='EPS'` and `fiscalp='QTR'` for every single row -- a clean filter, not three independent judgment calls) and USD-denominated: {cons['fpi6_qtr_eps_rows']:,} rows matched the fpi filter, and {cons['non_usd_rows_dropped']:,} of those were also non-USD and dropped, leaving {cons['cleaned_rows']:,} usable consensus snapshots.

## A significant data-quality finding: quarterly vs. annual actuals were conflated

The raw `actu_epsus` pull has no column distinguishing a quarterly actual EPS
from a full fiscal-year actual EPS (WRDS's I/B/E/S actuals table has one, but
it was not part of this project's pulled column set). Because a company's
fiscal Q4 and its fiscal year-end fall on the exact same calendar date, this
means the SAME (ticker, period-end date) can carry two genuinely different,
both-valid EPS figures -- the quarter alone, and the full year -- with no
column saying which is which.

This is not a rare edge case: after removing currency/null-value noise, **{dedup['ambiguous_value_groups']:,} of {actu['distinct_ticker_fpedats_groups']:,} company/fiscal-quarter combinations ({pct(dedup['ambiguous_value_groups'], actu['distinct_ticker_fpedats_groups'])})** still had more than one distinct reported EPS value under an identical ticker/period-end/announcement-date. These are concentrated overwhelmingly in December period-ends (roughly three-quarters of the ambiguous cases, versus about a quarter of period-ends generally), which is exactly what this quarterly/annual conflation theory predicts, since December is by far the most common fiscal year-end among US public companies.

**How it was resolved:** for each ambiguous case, the point-in-time-safe quarterly analyst consensus (the same one used for `sue_analyst`) was looked up, and whichever of the candidate EPS values sits closer to that consensus was kept -- analysts covering a stock's quarterly estimate are forecasting the quarterly figure, so their consensus sits far closer to the true quarterly actual than to a several-times-larger annual one. {dedup['ambiguous_resolved_via_consensus']:,} rows were resolved this way. Where no consensus was available at all to compare against (no analyst coverage for that quarter), the smaller-magnitude candidate was kept instead, on the same reasoning (a single quarter's EPS is smaller in magnitude than a full year's EPS for the same company in the large majority of cases): {dedup['ambiguous_resolved_via_magnitude_fallback']:,} rows used this fallback. Every row's resolution method is recorded in the output's `eps_actual_dup_flag` column, so this choice is auditable rather than hidden. Separately, {dedup['rows_dropped_for_later_anndats']:,} rows were dropped for being a later, second announcement of the same period (a true restatement/re-announcement) -- the earliest announcement date was kept in each case, since that is when the market actually first learned the result.

## The point-in-time filter's measured effect

Of the {cov['total_events']:,} (company, fiscal-quarter) events, {pit['events_with_any_candidate_consensus']:,} had at least one fpi=6 consensus snapshot for that fiscal quarter at all (eligible or not). Of those, **{pit['events_with_a_lookahead_candidate_excluded']:,} events had at least one candidate consensus snapshot that would have been look-ahead contamination** (published on or after the announcement date) had it not been explicitly filtered out. Across all events, **{pit['total_lookahead_snapshots_excluded']:,} individual consensus snapshots** were excluded on exactly this basis. This is expected and mechanical, not a data error: I/B/E/S keeps publishing new consensus snapshots for years after any given historical quarter (as analysts revise their NEXT-quarter forecasts), so "the last available snapshot for this fiscal quarter" is almost always a post-announcement one -- which is precisely why an explicit `statpers < anndats` filter matters rather than simply taking the latest available row per fiscal quarter.

After the filter, {pit['events_with_point_in_time_safe_consensus']:,} of {cov['total_events']:,} events ({pct(pit['events_with_point_in_time_safe_consensus'], cov['total_events'])}) have a usable, point-in-time-safe consensus snapshot at all.

## How the {actu['pre_1995_pends_rows']:,} pre-1995 pends rows were handled

`actu_epsus` has rows whose fiscal period-end (`pends`) predates 1995 even
though the pull window starts at 1995 -- these are late or restated reports
of old fiscal periods, not a pull bug (the pull filtered on announcement
date, not period-end date). They were kept, not dropped: a late or restated
result is still real information, and the point-in-time filter above is
exactly what keeps this safe regardless of how old the underlying period is
-- it compares the analyst consensus's STATPERS against this row's own
`anndats` (when the restated figure actually became public), never against
the ancient `pends`. A restated 1994 actual announced in 1996, for example,
can only ever be matched against a consensus snapshot published before that
1996 announcement, never against a stale 1994-era consensus.

## Analyst vs. seasonal coverage

- `sue_analyst` computed (usable consensus with non-null, non-negligible dispersion, i.e. more than one analyst who did not all submit the same or a near-identical estimate): {cov['sue_analyst_available']:,} of {cov['total_events']:,} events ({pct(cov['sue_analyst_available'], cov['total_events'])}).
- `sue_seasonal` computed (an exact same-fiscal-quarter-one-year-ago match existed, and at least {MIN_SEASONAL_DIFF_HISTORY} valid diffs were available within the trailing {ROLLING_SEASONAL_WINDOW} quarters to scale it): {cov['sue_seasonal_available']:,} of {cov['total_events']:,} events ({pct(cov['sue_seasonal_available'], cov['total_events'])}). The scaling std dev uses a trailing {ROLLING_SEASONAL_WINDOW}-quarter window (matching the standard seasonal-random-walk SUE construction from the academic literature) rather than a company's entire available history -- an all-history version was tried first and rejected after it let a stale, no-longer-relevant volatility estimate from years earlier (e.g. from before a stock split, restructuring, or a multi-year reporting gap) scale a present-day surprise, occasionally by several hundredfold.
- Per the plan's preemptive-check design (prefer real analyst data, fall back to seasonal only where analyst coverage is missing): `sue_primary` came from `sue_analyst` for {cov['sue_primary_from_analyst']:,} events, from `sue_seasonal` for {cov['sue_primary_from_seasonal']:,} events, and neither measure was computable for {cov['sue_primary_none']:,} events ({pct(cov['sue_primary_none'], cov['total_events'])}) -- these rows are kept in the output (not dropped) with `sue_source="none"`, since a future slice may still want the actual EPS/announcement-date fields even without a surprise measure.
- Both measures have a small tail of extreme values: {cov['sue_analyst_extreme']:,} `sue_analyst` values and {cov['sue_seasonal_extreme']:,} `sue_seasonal` values exceed a magnitude of 100. Spot-checking the largest of these (e.g. a company reporting an EPS of -91.60 against a tightly-clustered analyst consensus of -0.44) shows they are genuine -- large one-time write-offs, bankruptcies, and similar real events that also happen to have had unusually low estimate disagreement or historical volatility beforehand -- not computation errors. Deciding whether and how to winsorize or cap these for statistical analysis is left to whichever later slice actually runs event-study statistics on this data, not this cleaning slice.

## Identifiers

`resolved_universe_ids.parquet` is one row per OptionMetrics secid (8,358 rows), not one row per I/B/E/S ticker, and a handful of secids resolve ambiguously to more than one candidate PERMNO/GVKEY/ticker (recorded there as a comma-joined string rather than a silently-picked guess). This file's `ibes_ticker` -> PERMNO/GVKEY crosswalk was built by exploding those comma-joined ticker values into individually-joinable tickers and unioning whatever PERMNO/GVKEY values map to each one: {cov['identifier_matched']:,} of {cov['total_events']:,} events ({pct(cov['identifier_matched'], cov['total_events'])}) matched to at least one PERMNO/GVKEY. That match rate is expected to be at or near 100% (not a sign the check is trivial): the raw I/B/E/S pull was itself already filtered, at pull time, to tickers that resolved from this same universe, so nearly every ticker `actu_epsus` could possibly contain was, by construction, already in this crosswalk.

Of the matched events, {cov['identifier_ambiguous']:,} ({pct(cov['identifier_ambiguous'], cov['identifier_matched'])} of matched events) carry more than one distinct candidate PERMNO or GVKEY (comma-joined in the output, `identifier_ambiguous=True`) -- most often because the same company was reassigned a new PERMNO/GVKEY after a corporate restructuring. This is flagged, not resolved, here: resolving it correctly needs announcement-date-aware matching against equity history, which belongs in the later event-table-assembly slice, not this one. {cov['identifier_unmatched']:,} events had no PERMNO/GVKEY match at all.

## Output

`data/earnings/ibes_clean_1995_2014.parquet` -- {len(panel):,} rows, one per (I/B/E/S ticker, fiscal quarter). Columns: {', '.join(FINAL_COLUMNS)}.
"""


def main():
    actu_raw = pd.read_parquet(ACTU_EPSUS_PATH)
    statsumu_raw = pd.read_parquet(STATSUMU_EPSUS_PATH)
    resolved_raw = pd.read_parquet(RESOLVED_UNIVERSE_PATH)

    panel, stats = build_event_table(actu_raw, statsumu_raw, resolved_raw)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUTPUT_PATH, index=False)
    print(f"Wrote {len(panel):,} rows to {OUTPUT_PATH}", flush=True)

    findings = render_findings(panel, stats)
    FINDINGS_PATH.write_text(findings)
    print(f"Wrote findings to {FINDINGS_PATH}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
