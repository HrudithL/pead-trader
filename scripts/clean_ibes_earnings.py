"""Cleans and joins the WRDS I/B/E/S pull (data/raw_wrds/ibes/actu_epsus.parquet
and statsumu_epsus.parquet), together with CRSP daily share-adjustment
factors (data/raw_wrds/crsp/dsf_*.parquet), into a single point-in-time-safe
earnings/surprise table, one row per (I/B/E/S ticker, fiscal quarter):

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

Several WRDS-pull schema wrinkles and data-quality issues drive most of the
logic below (all covered in detail in data/earnings/FINDINGS.md, written by
this script's main()):

1. Every I/B/E/S date column landed as VARCHAR, not a real date type --
   parsed explicitly here (see _parse_date/_parse_time). CRSP's `date`
   column has the same VARCHAR wrinkle and is parsed the same way.
2. ibes.actu_epsus can carry BOTH a quarterly and an annual actual EPS
   under the exact same ticker/period-end/announcement-date (a fiscal Q4
   and fiscal year-end are the same calendar date). An earlier version of
   this pull lacked a period-type column and had to guess which was which
   by proximity to analyst consensus -- a research-invalidating bug, since
   that guess used the very quantity (the surprise) being computed. The
   pull now includes a real `pdicity` (period-type: QTR/ANN/SAN) column,
   and resolve_actual_duplicates() uses it directly instead of guessing.
   See resolve_actual_duplicates() for the current logic.
3. I/B/E/S actuals and consensus estimates are split-unadjusted. A stock
   split between a consensus snapshot and the announcement (or between two
   fiscal years, for the seasonal measure) puts the two EPS-like numbers on
   different per-share bases, producing a "surprise" that is really just a
   units artifact. See attach_share_basis()/compute_sue_analyst()/
   compute_sue_seasonal() for how this is corrected using CRSP's cumulative
   share-adjustment factor (cfacshr).
4. attach_share_basis() itself, per (3), unconditionally assumed a reported
   eps_actual is already on the POST-step basis whenever a cfacshr step
   falls between a consensus snapshot and the announcement. An adversarial
   review's initial small-sample estimate put this wrong on ~30% of the
   narrow subset of rows where a step instead falls inside the row's own
   (fpedats, anndats] reporting window; a later, more rigorous check
   arbitrated against Compustat's basis-free net income (`niq`) found the
   real base rate is much lower, ~5.4%. resolve_eps_basis() discriminates
   that narrow subset using each ticker's own prior-quarter actual as
   evidence, never a future quarter -- but only ever CONFIRMS the
   post-step direction (measured ~99% precise); the direction that would
   confirm the pre-step basis was measured only ~19% precise (companies
   routinely split stock right after a strong growth quarter, which looks
   identical to a stale pre-split value using only this evidence) and was
   removed entirely rather than tuned. Rows where the evidence doesn't
   clear the bar for the post-step direction are left explicitly
   undetermined (null), never guessed. See resolve_eps_basis() and
   FINDINGS.md's `eps_basis_status` section.

Usage:
    python scripts/clean_ibes_earnings.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_IBES_DIR = REPO_ROOT / "data" / "raw_wrds" / "ibes"
RAW_CRSP_DIR = REPO_ROOT / "data" / "raw_wrds" / "crsp"
METADATA_DIR = REPO_ROOT / "data" / "metadata"
OUTPUT_DIR = REPO_ROOT / "data" / "earnings"

ACTU_EPSUS_PATH = RAW_IBES_DIR / "actu_epsus.parquet"
STATSUMU_EPSUS_PATH = RAW_IBES_DIR / "statsumu_epsus.parquet"
RESOLVED_UNIVERSE_PATH = METADATA_DIR / "resolved_universe_ids.parquet"

OUTPUT_PATH = OUTPUT_DIR / "ibes_clean_1995_2014.parquet"
FINDINGS_PATH = OUTPUT_DIR / "FINDINGS.md"

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M:%S"

# Only these columns of statsumu_epsus.parquet (19 total) are ever used
# below -- passed explicitly to pd.read_parquet as a projection pushdown so
# the ~5.6M-row full table is never materialized in memory.
STATSUMU_USECOLS = [
    "ticker", "statpers", "fpedats", "fpi", "measure", "fiscalp", "curcode",
    "meanest", "medest", "stdev", "numest",
]

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

# This window is row-count-based (the trailing `ROLLING_SEASONAL_WINDOW`
# PANEL ROWS for a ticker), not calendar-bounded -- a ticker with reporting
# gaps can have up to 8 prior rows spanning many YEARS of calendar time
# rather than 8 recent quarters. seasonal_diff_stdev/sue_seasonal are
# nulled out whenever the actual calendar span of the contributing prior
# diffs exceeds this many days (~3 years -- enough slack for 52/53-week
# fiscal calendars and one missed quarter, while still excluding genuine
# multi-year-gap cases, some of which run up to 15 years in this data). See
# compute_sue_seasonal()/FINDINGS.md.
MAX_SEASONAL_WINDOW_SPAN_DAYS = 1095

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

# --- eps_basis discriminator (Step 2 follow-up fix) -------------------------
#
# The original split-adjustment fix (attach_share_basis, below) unconditionally
# assumed a reported eps_actual is on the POST-step (cfacshr_anndats) basis.
# An adversarial review's initial small-sample estimate put that assumption
# wrong on ~30% of the narrow subset of rows where a real cfacshr step falls
# inside the row's own (fpedats, anndats] reporting window -- fabricating new
# fake extreme surprises as bad as the ones the original fix corrected.
# resolve_eps_basis() below discriminates those in-window rows using each
# ticker's own PRIOR-quarter actual (never a future quarter, to avoid
# introducing a new look-ahead violation).
#
# A later, independent arbitration against Compustat's basis-free net income
# (`niq`, never seen anywhere else in this pipeline) found the discriminator's
# two directions are NOT equally trustworthy: confirming the POST-step basis
# is ~99.13% precise (effectively reliable), but confirming the PRE-step
# basis is only ~18.9% precise -- wrong about 4 times out of 5. Root cause: a
# ticker's own prior-quarter level cannot tell "this is a stale pre-split
# value" apart from "this company's earnings genuinely grew a lot this
# quarter," and companies routinely split their stock right after a strong
# growth quarter -- the two look identical from this evidence alone. The same
# analysis found the TRUE base rate of genuinely-pre-step cases is only
# ~5.4% of the in-window population (not the ~30% the first, less rigorous
# review assumed -- that ~30% was itself an artifact of the same
# growth/stale-split confound). At a ~5.4% base rate, no discriminator built
# only from this ticker's own prior-quarter EPS can reliably confirm the
# pre-step direction -- a classic low-base-rate screening problem, not a
# threshold-tuning problem, and verified mathematically infeasible rather
# than merely difficult. The pre-step-confirming direction was therefore
# REMOVED entirely rather than tuned; only the post-step-confirming direction
# is ever asserted. Everything else in-window is tagged undetermined
# (`basis_undetermined_ambiguous` / `basis_undetermined_no_reference` /
# `basis_undetermined_ambiguous_permno`) rather than guessed, per this
# project's "tag, don't filter" principle -- see
# memory/phase1-equity-pead-plan.md.

# Minimum ratio between the losing and winning candidate-basis distances
# before the prior-quarter evidence is treated as decisive enough to CONFIRM
# the post-step basis. Left at 2.0, unchanged from the original
# discriminator: its ~99.13% precision was measured at exactly this
# threshold, so it is not re-tuned here. (This ratio is not what made the
# pre-step direction unreliable, and raising or lowering it would not fix
# that direction either -- no threshold choice can separate "stale
# pre-split value" from "genuine quarter-over-quarter earnings growth" using
# only this ticker's own prior-quarter EPS as evidence, which is why that
# direction was removed instead of adjusted.)
EPS_BASIS_DECISIVE_MIN_RATIO = 2.0

# Day-count bounds for accepting the immediately-preceding panel row as
# the prior-quarter reference. Wide enough for 52/53-week fiscal
# calendars, tight enough to reject a skipped quarter.
EPS_BASIS_PRIOR_GAP_MIN_DAYS = 45
EPS_BASIS_PRIOR_GAP_MAX_DAYS = 135

# Gap bounds (days) for validate_qtr_ann_consistency's ordered
# prior-QTR-row lookups at lag 1, 2, 3.
QTR_ANN_GAP_BOUNDS = {1: (60, 120), 2: (150, 210), 3: (240, 300)}

FINAL_COLUMNS = [
    "ibes_ticker", "permno", "gvkey", "identifier_matched", "identifier_ambiguous",
    "fpedats", "anndats", "anntims",
    "eps_actual", "eps_actual_dup_flag", "eps_actual_multiple_anndats",
    "cfacshr_anndats", "cfacshr_statpers", "eps_actual_adj", "share_basis_status",
    "cfacshr_step_date", "cfacshr_prestep", "cfacshr_eps_basis", "eps_basis_status", "eps_basis_evidence_ratio",
    "consensus_statpers", "consensus_meanest", "consensus_medest", "consensus_stdev", "consensus_numest",
    "consensus_staleness_days", "n_candidates", "n_lookahead_excluded",
    "sue_analyst",
    "eps_year_ago", "eps_diff_yoy", "seasonal_n_prior_diffs", "seasonal_diff_stdev",
    "seasonal_window_span_days", "sue_seasonal",
    "sue_primary", "sue_source",
]


# ---------------------------------------------------------------------------
# Date/time parsing.
# ---------------------------------------------------------------------------


def _parse_date(series: pd.Series) -> pd.Series:
    """Parses a VARCHAR YYYY-MM-DD column (as pulled from WRDS, both I/B/E/S
    and CRSP) into a real datetime64 column. `errors="raise"` is deliberate:
    a value that doesn't match is a pull/schema problem worth crashing
    loudly on, not silently coercing to NaT.
    """
    return pd.to_datetime(series, format=DATE_FORMAT, errors="raise")


def _parse_time(series: pd.Series) -> pd.Series:
    """Parses a VARCHAR HH:MM:SS column into a datetime.time. anntims is
    never null in actu_epsus (confirmed during the WRDS pull audit)."""
    return pd.to_datetime(series, format=TIME_FORMAT, errors="raise").dt.time


def _parquet_row_count(path: Path) -> int:
    """Row count read from a Parquet file's footer metadata only -- no row
    data is scanned off disk, so this does not undermine the projection/
    predicate pushdown used elsewhere when actually loading a file. Used
    purely for FINDINGS.md's narrative about how large the raw file is.
    """
    return pq.ParquetFile(path).metadata.num_rows


# ---------------------------------------------------------------------------
# Per-table cleaning.
# ---------------------------------------------------------------------------


def clean_actuals(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Parses dates/times and applies the measure/currency/non-null-value
    filters to the raw actu_epsus pull. Does NOT yet resolve (ticker,
    fpedats) duplicates -- see resolve_actual_duplicates().

    Hard-fails if `pdicity` (the period-type column: QTR/ANN/SAN) is
    absent from the raw frame -- resolve_actual_duplicates() depends on it
    to tell quarterly and annual actuals apart, and silently degrading to
    a guess here is exactly the bug this column was re-pulled to fix.

    Returns (cleaned, stats). `cleaned` has columns
    [ticker, fpedats, anndats, anntims, eps_actual, pdicity] and may still
    have multiple rows per (ticker, fpedats) -- real duplication, not yet
    resolved. `stats` records how many rows were dropped at each step, for
    FINDINGS.md.
    """
    stats = {"raw_rows": len(raw)}
    df = raw.copy()

    if "pdicity" not in df.columns:
        raise ValueError(
            "clean_actuals: raw actu_epsus pull is missing the 'pdicity' "
            "period-type column. resolve_actual_duplicates() requires it to "
            "distinguish quarterly from annual actuals; without it, the "
            "only alternative is guessing from the surprise measure itself "
            "(a research-invalidating bug this column was re-pulled to "
            "fix). Failing loudly rather than silently degrading -- see "
            "IBES_ACTU_EPSUS_OPTIONAL_COLUMNS in wrds_pull_lib.py."
        )

    # Almost every row is measure='EPS'; a lone EPSPAR (EPS before
    # extraordinary items, or similar I/B/E/S variant) row has been
    # observed in the real pull and is dropped here rather than silently
    # mixed in with EPS actuals.
    non_eps_mask = df["measure"] != "EPS"
    stats["non_eps_rows_dropped"] = int(non_eps_mask.sum())
    df = df[~non_eps_mask].copy()
    assert (df["measure"] == "EPS").all(), "postcondition: only measure=='EPS' rows should remain"

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
    # driven by FX, not earnings. This is a real universe reduction (a
    # small number of tickers have ZERO usd rows and disappear entirely),
    # not de-duplication -- see FINDINGS.md for the ticker-level count.
    non_usd = int((df["curr_act"] != "USD").sum())
    stats["non_usd_rows_dropped"] = non_usd
    df = df[df["curr_act"] == "USD"]

    null_value = int(df["value"].isna().sum())
    stats["null_actual_eps_rows_dropped"] = null_value
    df = df[df["value"].notna()]

    df = df.rename(columns={"pends": "fpedats", "value": "eps_actual"})
    df = df[["ticker", "fpedats", "anndats", "anntims", "eps_actual", "pdicity"]]

    before_exact_dedup = len(df)
    # anntims is deliberately excluded from the dedup key: two rows that
    # agree on ticker/fpedats/anndats/eps_actual/pdicity and differ only in
    # a sub-day timestamp are the same observation for this project's
    # purposes. pdicity IS part of the key -- a QTR and an ANN row must
    # never be collapsed into one just because they happen to report an
    # identical EPS value.
    df = df.drop_duplicates(subset=["ticker", "fpedats", "anndats", "eps_actual", "pdicity"]).reset_index(drop=True)
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
    # Deterministic sort before keep="last": sorting purely by the dedup
    # key (ticker, statpers, fpedats) doesn't itself break ties among rows
    # that share that key but disagree on the estimate columns, so the
    # value columns are included as stable tiebreakers -- keep="last" then
    # always resolves to the same specific row for a given input, not
    # whatever order the raw WRDS pull happened to return.
    df = df.sort_values(["ticker", "statpers", "fpedats", "meanest", "medest", "stdev", "numest"]).reset_index(drop=True)
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
    implicit. This does NOT bound how STALE an eligible snapshot can be
    (see consensus_staleness_days in build_event_table -- a restated actual
    can still be matched against a very old but technically-eligible
    consensus; that is tagged, not filtered, here).

    `keys` must have columns [ticker, fpedats, anndats], one row per event
    needing a match (duplicate (ticker, fpedats) keys are fine -- anndats
    travels with each row independently).

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


def resolve_actual_duplicates(
    actuals: pd.DataFrame,
    max_qtr_loss_fraction: float = 0.05,
    max_ambiguous_conflict_fraction: float = 0.01,
) -> tuple[pd.DataFrame, dict]:
    """Collapses `actuals` (still possibly multiple rows per (ticker,
    fpedats) -- see clean_actuals) down to exactly one row per (ticker,
    fpedats), using the real `pdicity` period-type column.

    `max_qtr_loss_fraction`/`max_ambiguous_conflict_fraction` are the two
    safety-check thresholds described below, exposed as parameters (rather
    than hardcoded) purely so small, deliberately-conflict-heavy test
    fixtures can raise them -- a handful of manufactured conflicting rows
    is a huge PERCENTAGE of a tiny fixture even though it is a tiny
    percentage of the real ~291K-group dataset the defaults were verified
    against. Production code should not need to override either default.

    An earlier version of this function had no period-type column to work
    with (the pull hadn't included one yet) and resolved quarterly-vs-
    annual conflation by picking whichever candidate value sat closest to
    the point-in-time analyst consensus -- selecting on the dependent
    variable of the very surprise measure being computed, which an
    adversarial review found to systematically strip large genuine
    surprises. That logic is gone. Resolution now proceeds in two
    unrelated stages:

    1. Keep only pdicity == 'QTR' rows (drops ANN/SAN reporters and any
       ANN/SAN duplicate of a QTR row for the same period-end). This is a
       real, small universe reduction -- but most of the dropped GROUPS
       belong to tickers that DO report quarterly elsewhere; a genuine
       annual/semiannual-only reporter (one that vanishes from the universe
       entirely) is the minority case. See groups_dropped_ticker_reports_
       qtr_elsewhere/groups_dropped_december in `stats` -- most of what's
       dropped is a systematic Q4 coverage gap (I/B/E/S carrying only an ANN
       row for that one fiscal-year-end quarter), not "annual-only
       reporters." Reported, not hidden either way. On the real pull this
       loss is 1.1% of groups, below the spec's ~2% expectation and well
       under the 5% safety threshold below; if a future re-pull ever drops
       more than 5% of (ticker, fpedats) groups, that is far enough off the
       verified expectation that this function stops rather than proceeding
       on data that doesn't look like what was checked.

    2. Among the QTR-only rows, remaining (ticker, fpedats) duplicates are
       classified by (n_anndats, n_values) computed BEFORE dropping
       anything:
         - n_anndats > 1: genuinely different announcement dates for the
           same fiscal quarter (e.g. a restatement) -- keep the row with
           the EARLIEST anndats (the date the market actually first learned
           the result) and flag it via eps_actual_multiple_anndats.
         - n_anndats == 1 and n_values > 1: the SAME announcement date
           reporting more than one distinct EPS value -- a genuine value
           conflict, not just a restatement. Flagged
           eps_actual_dup_flag="ambiguous_same_anndats_conflict" and
           resolved deterministically (sort by eps_actual ascending, keep
           the first) -- never by proximity to consensus or magnitude. If
           this affects more than 1% of groups, this function stops rather
           than silently resolving unexpectedly-widespread conflicts.
         - otherwise: eps_actual_dup_flag="unique".
    """
    stats = {}
    df = actuals.copy()

    stats["rows_by_period_type"] = {str(k): int(v) for k, v in df["pdicity"].value_counts(dropna=False).items()}

    stats["rows_before_period_filter"] = len(df)
    stats["groups_before_period_filter"] = df.drop_duplicates(["ticker", "fpedats"]).shape[0]
    tickers_before = df["ticker"].nunique()

    non_qtr_mask = df["pdicity"] != "QTR"
    stats["non_qtr_rows_dropped"] = int(non_qtr_mask.sum())

    pre_filter_groups = df.drop_duplicates(["ticker", "fpedats"])[["ticker", "fpedats"]]
    df = df[df["pdicity"] == "QTR"].copy()

    stats["groups_after_period_filter"] = df.drop_duplicates(["ticker", "fpedats"]).shape[0]
    stats["groups_dropped_by_period_filter"] = (
        stats["groups_before_period_filter"] - stats["groups_after_period_filter"]
    )
    stats["tickers_vanished_after_qtr_filter"] = tickers_before - df["ticker"].nunique()

    # Which of the dropped GROUPS belong to a ticker that still reports QTR
    # somewhere else, vs. a ticker that vanishes from the universe entirely
    # (see the docstring above) -- and how many of the dropped groups are
    # specifically a December (Q4/fiscal-year-end) period-end, the concrete
    # mechanism behind "I/B/E/S only carried an ANN row for that one
    # quarter." Computed here (not deferred to FINDINGS-render time) since
    # this is the one place both the pre- and post-filter group sets exist.
    post_filter_groups = df.drop_duplicates(["ticker", "fpedats"])[["ticker", "fpedats"]]
    dropped_groups = pre_filter_groups.merge(
        post_filter_groups, on=["ticker", "fpedats"], how="left", indicator=True
    )
    dropped_groups = dropped_groups[dropped_groups["_merge"] == "left_only"]
    tickers_with_qtr_elsewhere = set(post_filter_groups["ticker"])
    stats["groups_dropped_ticker_reports_qtr_elsewhere"] = int(
        dropped_groups["ticker"].isin(tickers_with_qtr_elsewhere).sum()
    )
    stats["groups_dropped_december"] = int((dropped_groups["fpedats"].dt.month == 12).sum())

    loss_fraction = (
        stats["groups_dropped_by_period_filter"] / stats["groups_before_period_filter"]
        if stats["groups_before_period_filter"]
        else 0.0
    )
    stats["period_filter_loss_fraction"] = loss_fraction
    if loss_fraction > max_qtr_loss_fraction:
        raise RuntimeError(
            f"resolve_actual_duplicates: the QTR-only filter dropped "
            f"{loss_fraction:.1%} of (ticker, fpedats) groups, exceeding "
            f"the {max_qtr_loss_fraction:.0%} safety threshold -- well above "
            f"the spec's ~2% expectation (the real pull measured 1.1%, "
            f"itself below that expectation) -- stopping rather than "
            f"silently proceeding on data that looks different than what "
            f"was checked. Stats so far: {stats}"
        )

    # Deterministic sort before every drop_duplicates below: ticker/fpedats
    # group the rows, anndats picks out the restatement case's "earliest"
    # row, and eps_actual (ascending) both breaks ties within a shared
    # anndats and implements the "sort ascending, take the first row" rule
    # for genuine same-anndats value conflicts.
    df = df.sort_values(["ticker", "fpedats", "anndats", "eps_actual"]).reset_index(drop=True)

    group_keys = ["ticker", "fpedats"]
    n_anndats = df.groupby(group_keys)["anndats"].transform("nunique")
    n_values = df.groupby(group_keys)["eps_actual"].transform("nunique")

    multiple_anndats_mask = n_anndats > 1
    same_anndats_conflict_mask = (n_anndats == 1) & (n_values > 1)
    unique_mask = ~multiple_anndats_mask & ~same_anndats_conflict_mask

    total_groups = stats["groups_after_period_filter"]
    stats["groups_multiple_anndats"] = df.loc[multiple_anndats_mask].drop_duplicates(group_keys).shape[0]
    stats["groups_ambiguous_same_anndats_conflict"] = (
        df.loc[same_anndats_conflict_mask].drop_duplicates(group_keys).shape[0]
    )
    stats["groups_unique_no_conflict"] = df.loc[unique_mask].drop_duplicates(group_keys).shape[0]

    ambiguous_fraction = (
        stats["groups_ambiguous_same_anndats_conflict"] / total_groups if total_groups else 0.0
    )
    stats["ambiguous_same_anndats_conflict_fraction"] = ambiguous_fraction
    if ambiguous_fraction > max_ambiguous_conflict_fraction:
        raise RuntimeError(
            f"resolve_actual_duplicates: {ambiguous_fraction:.2%} of "
            f"(ticker, fpedats) groups have a genuine same-anndats value "
            f"conflict, exceeding the {max_ambiguous_conflict_fraction:.0%} safety threshold -- stopping "
            f"rather than silently resolving an unexpectedly large number "
            f"of conflicts at scale. Stats so far: {stats}"
        )

    unique_part = df.loc[unique_mask].copy()
    unique_part["eps_actual_dup_flag"] = "unique"
    unique_part["eps_actual_multiple_anndats"] = False

    # df is already sorted by [ticker, fpedats, anndats, eps_actual], so
    # keep="first" per group deterministically keeps the earliest anndats
    # (ties broken by the smallest eps_actual).
    multi_anndats_part = df.loc[multiple_anndats_mask].drop_duplicates(group_keys, keep="first").copy()
    multi_anndats_part["eps_actual_dup_flag"] = "unique"
    multi_anndats_part["eps_actual_multiple_anndats"] = True

    # n_anndats == 1 within this mask, so the group is already sorted
    # purely by eps_actual ascending -- keep="first" implements "sort
    # ascending, take the first row" exactly.
    conflict_part = df.loc[same_anndats_conflict_mask].drop_duplicates(group_keys, keep="first").copy()
    conflict_part["eps_actual_dup_flag"] = "ambiguous_same_anndats_conflict"
    conflict_part["eps_actual_multiple_anndats"] = False

    # Concatenating only the non-empty parts avoids a pandas/NumPy wart on
    # empty datetime64 blocks (a deprecated NA-inference path that pandas
    # warns will become a hard error in a future version) -- any one or
    # two of these three parts is routinely empty for a given input (e.g.
    # a ticker set with no restatements at all), and there is nothing to
    # concatenate with in that case anyway.
    non_empty_parts = [part for part in (unique_part, multi_anndats_part, conflict_part) if len(part) > 0]
    out = pd.concat(non_empty_parts, ignore_index=True) if non_empty_parts else unique_part
    stats["rows_after_dedup"] = len(out)

    if out.duplicated(["ticker", "fpedats"]).any():
        raise AssertionError(
            "resolve_actual_duplicates postcondition violated: duplicate "
            "(ticker, fpedats) rows remain after unique/multiple_anndats/"
            "ambiguous_same_anndats_conflict resolution"
        )
    return out, stats


def validate_qtr_ann_consistency(actuals: pd.DataFrame, tolerance: float = 0.05) -> dict:
    """Non-circular validation of the `pdicity` labels themselves (this
    does not use consensus or anything else derived from the surprise
    measure being built, so it cannot be fooled the way proximity-to-
    consensus disambiguation was).

    For (ticker, fpedats) groups where the prior 3 same-ticker fiscal
    quarters each have exactly one QTR row, and the current fpedats has
    both a QTR row and an ANN row (each unique) for the same ticker, the
    ANN value should approximately equal the sum of the 4 QTR values if
    `pdicity` is correctly distinguishing quarterly from annual actuals.

    The prior 3 quarters are found by taking the immediately-preceding rows
    in each ticker's own OWN chronological QTR series (ordered
    prior-row lookups at lag 1/2/3, gated by a day-count window --
    QTR_ANN_GAP_BOUNDS), not by shifting each candidate's own date forward
    by a fixed `pd.DateOffset(months=...)`. An earlier version of this
    function used the DateOffset approach and had a real, silent bug:
    `DateOffset` preserves day-of-month, so `2000-09-30 + 3 months =
    2000-12-30`, which never matches an actual `2000-12-31` row -- silently
    excluding every December fiscal-year-end (73% of the eligible sample on
    the real pull) from this check. The ordered-prior-row approach has no
    such blind spot: it only cares about the actual gap in days between
    consecutive real QTR rows, never about day-of-month arithmetic.

    `actuals` must be shaped like clean_actuals()'s output (columns
    ticker, fpedats, eps_actual, pdicity) and, importantly, must NOT yet
    have had resolve_actual_duplicates()'s QTR-only filter applied -- this
    needs to see both the QTR and ANN rows for the same (ticker, fpedats).

    Returns {"n_cases", "n_passed", "pass_rate", "n_cases_december"}
    (pass_rate is None if no qualifying cases were found at all;
    n_cases_december counts qualifying cases whose own fpedats falls in
    December, so the specific bug above stays visibly closed).
    """
    df = actuals[["ticker", "fpedats", "eps_actual", "pdicity"]].drop_duplicates()

    def _unique_by_group(sub: pd.DataFrame) -> pd.DataFrame:
        counts = sub.groupby(["ticker", "fpedats"]).size()
        unique_keys = counts[counts == 1].index
        sub_indexed = sub.set_index(["ticker", "fpedats"])
        return sub_indexed.loc[sub_indexed.index.isin(unique_keys), "eps_actual"].reset_index()

    qtr_unique = _unique_by_group(df[df["pdicity"] == "QTR"])
    ann_unique = _unique_by_group(df[df["pdicity"] == "ANN"]).rename(columns={"eps_actual": "ann_value"})

    qtr = qtr_unique.sort_values(["ticker", "fpedats"]).reset_index(drop=True)
    g = qtr.groupby("ticker")
    eligible = pd.Series(True, index=qtr.index)
    for lag, (lo, hi) in QTR_ANN_GAP_BOUNDS.items():
        qtr[f"q_m{3 * lag}"] = g["eps_actual"].shift(lag)
        gap = (qtr["fpedats"] - g["fpedats"].shift(lag)).dt.days
        eligible &= qtr[f"q_m{3 * lag}"].notna() & gap.between(lo, hi)

    candidates = (
        qtr[eligible]
        .rename(columns={"eps_actual": "q0"})
        .merge(ann_unique, on=["ticker", "fpedats"], how="inner")
    )

    n_cases = len(candidates)
    if n_cases == 0:
        return {"n_cases": 0, "n_passed": 0, "pass_rate": None, "n_cases_december": 0}

    predicted_sum = candidates["q0"] + candidates["q_m3"] + candidates["q_m6"] + candidates["q_m9"]
    passed = (candidates["ann_value"] - predicted_sum).abs() <= tolerance
    n_passed = int(passed.sum())
    n_cases_december = int((candidates["fpedats"].dt.month == 12).sum())
    return {
        "n_cases": n_cases,
        "n_passed": n_passed,
        "pass_rate": n_passed / n_cases,
        "n_cases_december": n_cases_december,
    }


# ---------------------------------------------------------------------------
# Surprise measures.
# ---------------------------------------------------------------------------


def compute_sue_analyst(eps_actual: pd.Series, meanest: pd.Series, stdev: pd.Series) -> pd.Series:
    """Standardized unexpected earnings vs. the real analyst consensus:
    (actual - mean estimate) / dispersion (stdev across analysts).

    This function is share-basis-agnostic by design: it does not know
    about stock splits at all. build_event_table() corrects for a split
    landing between the consensus snapshot and the announcement BEFORE
    calling this, by pre-multiplying `eps_actual` by R_c = cfacshr_statpers
    / cfacshr_anndats (see attach_share_basis()) -- mathematically
    equivalent to adjusting both sides to a common per-share basis, without
    this function needing to change at all.

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


SUE_SEASONAL_OUTPUT_COLUMNS = [
    "eps_year_ago", "eps_diff_yoy", "seasonal_n_prior_diffs",
    "seasonal_diff_stdev", "seasonal_window_span_days", "sue_seasonal",
]


def compute_sue_seasonal(
    panel: pd.DataFrame,
    min_history: int = MIN_SEASONAL_DIFF_HISTORY,
    window: int = ROLLING_SEASONAL_WINDOW,
    ticker_col: str = "ticker",
    eps_col: str = "eps_actual",
) -> pd.DataFrame:
    """Adds eps_year_ago, eps_diff_yoy, seasonal_n_prior_diffs,
    seasonal_diff_stdev, seasonal_window_span_days, and sue_seasonal to
    `panel` (one row per (ticker, fpedats) -- see resolve_actual_
    duplicates).

    Raises ValueError up front if `panel` already contains any of
    SUE_SEASONAL_OUTPUT_COLUMNS -- build_event_table() calls this function
    twice per run (once for the FINDINGS.md before/after comparison, once
    for real, on the split-adjusted basis), and a second call on a frame
    that already carries these columns would previously silently produce
    merge-suffixed columns (e.g. "eps_year_ago_x"/"_y") internally and then
    fail downstream with a confusing bare KeyError -- a live footgun now
    that this runs twice. Callers must pass a copy of `panel` without these
    columns (e.g. the ORIGINAL panel both times, not one call's output fed
    into the other).

    `eps_col` (default "eps_actual") is the column used as "the actual EPS"
    throughout -- build_event_table() passes "eps_actual_adj" (the
    split-adjusted actual; see attach_share_basis()) so that eps_year_ago
    and everything derived from it (eps_diff_yoy, seasonal_diff_stdev,
    sue_seasonal) are computed on ONE consistent, split-adjusted basis. A
    split landing between two fiscal years being compared year-over-year
    would otherwise produce a spurious multiple-of-the-split-ratio "diff"
    that has nothing to do with real earnings growth, and -- because the
    rolling std dev also consumes eps_diff_yoy -- would go on to poison the
    scaling denominator for several subsequent quarters too. `ticker_col`
    (default "ticker") lets build_event_table() pass "ibes_ticker" once
    identifiers have been attached and the panel's ticker column has been
    renamed.

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

    That window is row-count-based, not calendar-bounded: a ticker with
    reporting gaps can have `window` prior ROWS spanning many years rather
    than recent quarters. seasonal_window_span_days measures the actual
    calendar gap between this row's fpedats and the oldest fpedats among
    the rows that fed a real (non-null) diff into the rolling window;
    seasonal_diff_stdev (and therefore sue_seasonal) is additionally nulled
    out whenever that span exceeds MAX_SEASONAL_WINDOW_SPAN_DAYS, even if
    seasonal_n_prior_diffs already cleared min_history.
    """
    conflicting = [c for c in SUE_SEASONAL_OUTPUT_COLUMNS if c in panel.columns]
    if conflicting:
        raise ValueError(
            f"compute_sue_seasonal: panel already contains output column(s) "
            f"{conflicting} -- refusing to run again on a frame that already "
            "carries them (a second pass would silently produce merge-"
            "suffixed columns internally and then fail with a confusing "
            "KeyError). Pass a copy of the panel without these columns."
        )

    df = panel.sort_values([ticker_col, "fpedats"]).reset_index(drop=True).copy()

    prior = df[[ticker_col, "fpedats", eps_col]].rename(
        columns={"fpedats": "prior_fpedats", eps_col: "eps_year_ago"}
    )
    df["prior_fpedats"] = df["fpedats"] - pd.DateOffset(years=1)
    df = df.merge(prior, on=[ticker_col, "prior_fpedats"], how="left").drop(columns=["prior_fpedats"])

    df["eps_diff_yoy"] = df[eps_col] - df["eps_year_ago"]

    grouped_diff = df.groupby(ticker_col)["eps_diff_yoy"]
    prior_count = grouped_diff.transform(lambda s: s.shift(1).rolling(window, min_periods=1).count())
    prior_std = grouped_diff.transform(lambda s: s.shift(1).rolling(window, min_periods=1).std())

    df["seasonal_n_prior_diffs"] = prior_count.astype(int)

    # Which prior quarters actually fed a valid (non-null) diff into the
    # rolling window -- tracked so the window's REAL calendar span can be
    # measured, not just its row count (see MAX_SEASONAL_WINDOW_SPAN_DAYS).
    # pandas' rolling().min() does not support datetime64 directly (raises
    # "No numeric types to aggregate" even without any NaT present), so the
    # dates are round-tripped through int64 nanoseconds -- NaT's int64
    # sentinel is remapped to int64's MAX (rather than its actual MIN) so a
    # missing diff never wins a rolling MINIMUM, then mapped back to NaT
    # after the rolling pass.
    contributing_fpedats = df["fpedats"].where(df["eps_diff_yoy"].notna())
    nat_sentinel = np.iinfo("int64").min
    pos_sentinel = np.iinfo("int64").max
    contributing_as_int = contributing_fpedats.astype("int64")
    contributing_as_int = contributing_as_int.where(contributing_as_int != nat_sentinel, pos_sentinel)
    df["_contributing_fpedats_int"] = contributing_as_int
    oldest_contributing_int = df.groupby(ticker_col)["_contributing_fpedats_int"].transform(
        lambda s: s.shift(1).rolling(window, min_periods=1).min()
    )
    oldest_contributing = pd.to_datetime(oldest_contributing_int.where(oldest_contributing_int != pos_sentinel), unit="ns")
    df["seasonal_window_span_days"] = (df["fpedats"] - oldest_contributing).dt.days
    df = df.drop(columns=["_contributing_fpedats_int"])

    # Explicit min-history gate, independent of pandas' own min_periods/NaN
    # interaction: a std computed from fewer than min_history valid prior
    # diffs is nulled out here regardless of what rolling(...).std() itself
    # would have returned. The span gate is independent of and additional
    # to the history-count gate -- a gappy ticker can clear min_history on
    # row count alone while still spanning far more calendar time than a
    # "recent volatility" estimate should.
    window_within_span = df["seasonal_window_span_days"] <= MAX_SEASONAL_WINDOW_SPAN_DAYS
    df["seasonal_diff_stdev"] = prior_std.where((prior_count >= min_history) & window_within_span)

    stdev_safe = df["seasonal_diff_stdev"].where(
        df["seasonal_diff_stdev"].notna() & (df["seasonal_diff_stdev"].abs() > _ZERO_STDEV_EPSILON)
    )
    df["sue_seasonal"] = df["eps_diff_yoy"] / stdev_safe
    return df


# ---------------------------------------------------------------------------
# Share-basis (stock-split) normalization.
# ---------------------------------------------------------------------------


def _build_cfacshr_step_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Core compression logic for the CRSP share-adjustment "step table":
    takes an already-loaded raw DataFrame with [permno, date (datetime64),
    cfacshr] columns (one row per permno per trading day) and keeps only
    the rows where cfacshr first appears for a permno or changes from the
    immediately preceding date -- cfacshr is piecewise-constant between
    corporate actions (splits/stock dividends), so this compresses the full
    daily history down to one row per permno's first observation plus one
    per subsequent adjustment event -- on the real 1995-2014 pull this is
    ~14K rows (8,100 distinct permnos, ~39% of which have at least one real
    change) out of ~20.5M raw daily rows, a >1000x reduction.

    Separated from load_cfacshr_step_table() so tests can exercise this on
    a small synthetic or filtered-real frame without reading CRSP's full
    daily file set from disk.

    Also carries `prev_cfacshr` (the cfacshr in force immediately before
    this step row -- null on a permno's first observation, since there is
    no "before" to speak of) and `is_change` (True only for a REAL change,
    i.e. `prev_cfacshr` is non-null and differs from this row's own
    cfacshr -- False on every permno's first-observation row, which is
    kept only so the merge_asof level lookups elsewhere have a value to
    resolve against before that permno's first real change, if any).
    resolve_eps_basis() filters to `is_change` rows specifically, since a
    first-observation row is not evidence of an actual corporate action.
    """
    raw = raw.dropna(subset=["cfacshr"]).sort_values(["permno", "date"]).reset_index(drop=True)
    prev_cfacshr = raw.groupby("permno")["cfacshr"].shift(1)
    changed = prev_cfacshr.isna() | (raw["cfacshr"] != prev_cfacshr)
    step = raw.loc[changed, ["permno", "date", "cfacshr"]].reset_index(drop=True)
    step["permno"] = step["permno"].astype("float64")
    step["prev_cfacshr"] = prev_cfacshr[changed].to_numpy()  # NaN on first obs
    step["is_change"] = step["prev_cfacshr"].notna() & (step["cfacshr"] != step["prev_cfacshr"])
    return step.sort_values("date").reset_index(drop=True)


def load_cfacshr_step_table(crsp_dir: Path = RAW_CRSP_DIR) -> pd.DataFrame:
    """Reads every data/raw_wrds/crsp/dsf_*.parquet file (permno, date,
    cfacshr only -- CRSP's other ~12 daily columns are not needed here) and
    compresses the result into a step table via _build_cfacshr_step_table().
    """
    files = sorted(crsp_dir.glob("dsf_*.parquet"))
    if not files:
        raise FileNotFoundError(f"load_cfacshr_step_table: no dsf_*.parquet files found under {crsp_dir}")
    frames = [pd.read_parquet(f, columns=["permno", "date", "cfacshr"]) for f in files]
    raw = pd.concat(frames, ignore_index=True)
    raw["date"] = _parse_date(raw["date"])
    return _build_cfacshr_step_table(raw)


def resolve_share_basis(
    step_table: pd.DataFrame, permno_raw: pd.Series, target_date: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """Resolves the CRSP cumulative share-adjustment factor (cfacshr) in
    effect on or before `target_date`, for each row's `permno_raw`.

    `permno_raw` is a string PERMNO (the common case), a comma-joined
    multi-candidate PERMNO string (from an upstream ambiguous identifier
    resolution -- e.g. "10000,20000"), or null (no identifier match at
    all). For a multi-candidate row, every candidate is resolved
    independently via `step_table`; if every CRSP-covered candidate agrees
    on the resulting cfacshr, that value is used, otherwise the row is
    marked ambiguous.

    Returns (cfacshr, status), aligned to permno_raw's index. `status` is
    one of:
      "resolved" -- a usable cfacshr was found (the caller decides
        no_change vs. adjusted by comparing it to 1.0).
      "unavailable_no_permno" -- permno_raw itself is null/empty (nothing
        to resolve against, regardless of target_date).
      "unavailable_no_target_date" -- permno_raw is present, but
        target_date is null (nothing to resolve it AT).
      "unavailable_ambiguous_permno" -- more than one CRSP-covered
        candidate PERMNO, and they do not agree on cfacshr.
      "unavailable_no_crsp_coverage" -- no candidate PERMNO has a usable
        (non-null, positive) step-table entry on or before target_date.
    These were previously conflated into a single "unavailable_no_permno"
    value covering both the no-permno and no-target-date cases; split so a
    later slice can tell "nothing to resolve against" apart from "resolvable
    in principle, but this particular date is missing."
    """
    idx = permno_raw.index
    result_value = pd.Series(np.nan, index=idx, dtype="float64")

    permno_str = permno_raw.astype("string")
    has_permno = permno_str.notna() & (permno_str.str.len() > 0)
    result_status = pd.Series(
        np.where(has_permno, "unavailable_no_target_date", "unavailable_no_permno"),
        index=idx, dtype="object",
    )

    eligible = has_permno & target_date.notna()
    if not eligible.any():
        return result_value, result_status

    work = pd.DataFrame({
        "_row_id": np.asarray(idx[eligible]),
        "_permno_str": permno_str[eligible].to_numpy(),
        "_date": pd.Series(target_date[eligible].to_numpy(), dtype="datetime64[ns]"),
    })
    work = work.assign(_candidate=work["_permno_str"].str.split(",")).explode("_candidate", ignore_index=True)
    work["_candidate"] = pd.to_numeric(work["_candidate"], errors="coerce").astype("float64")
    work = work.sort_values("_date", kind="mergesort").reset_index(drop=True)

    step_renamed = step_table.rename(columns={"permno": "_candidate", "date": "_date"})[["_candidate", "_date", "cfacshr"]]
    merged = pd.merge_asof(work, step_renamed, on="_date", by="_candidate", direction="backward")

    covered = merged["cfacshr"].notna() & (merged["cfacshr"] > 0)
    merged["_covered_value"] = merged["cfacshr"].where(covered)
    merged["_covered_value_r"] = merged["_covered_value"].round(8)

    covered_only = merged.loc[covered, ["_row_id", "_covered_value", "_covered_value_r"]]
    n_distinct = covered_only.groupby("_row_id")["_covered_value_r"].nunique()
    first_value = covered_only.groupby("_row_id")["_covered_value"].first()

    all_row_ids = pd.Index(work["_row_id"].unique())
    n_distinct = n_distinct.reindex(all_row_ids, fill_value=0)

    resolved_ids = n_distinct.index[n_distinct == 1]
    ambiguous_ids = n_distinct.index[n_distinct > 1]
    no_coverage_ids = n_distinct.index[n_distinct == 0]

    result_value.loc[resolved_ids] = first_value.loc[resolved_ids].to_numpy()
    result_status.loc[resolved_ids] = "resolved"
    result_status.loc[ambiguous_ids] = "unavailable_ambiguous_permno"
    result_status.loc[no_coverage_ids] = "unavailable_no_crsp_coverage"

    return result_value, result_status


def _resolve_multi_candidate_step(
    step_table: pd.DataFrame, permno_raw: pd.Series, anndats: pd.Series, fpedats: pd.Series
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Per-candidate resolution of the most recent REAL cfacshr change (a
    `step_table` row with `is_change=True`) at or before `anndats`, for each
    row's `permno_raw` -- the same explode/merge_asof/agree pattern
    resolve_share_basis() uses above for cfacshr_anndats, adapted here to
    resolve a (step date, prev_cfacshr) PAIR instead of a single cfacshr
    value, and to separately flag rows where candidates disagree but at
    least one of them still has a real step inside this row's own
    (fpedats, anndats].

    resolve_eps_basis() used to resolve this with a single
    `pd.to_numeric(permno_raw, errors="coerce")` cast, which silently turned
    every comma-joined multi-candidate permno string to NaN and dropped it
    from the in-window check entirely -- even on rows where
    attach_share_basis()'s OWN candidate resolution (resolve_share_basis(),
    above) had already agreed on cfacshr_anndats for that same row. This
    function resolves every candidate independently instead.

    A candidate "agrees" with another only if both its resolved step date
    AND its prev_cfacshr match (prev_cfacshr rounded to 8 decimals, to avoid
    a false disagreement from floating-point noise) -- matching on only one
    of the two fields would not actually pin down a single (date,
    prev_cfacshr) pair. A candidate with no resolvable prior real change at
    all (by far the common case -- most permnos never split) is treated as
    uninformative, not as disagreeing with an informative candidate, the
    same way resolve_share_basis() treats an uncovered candidate as simply
    not contributing a vote rather than as a disagreement.

    Returns (step_date, prev_cfacshr, any_candidate_step_in_window), aligned
    to permno_raw's index:
      step_date, prev_cfacshr -- the single agreed value (both null if every
        candidate has no resolvable prior real change, if permno_raw itself
        is null/empty, or if candidates disagree -- see below).
      any_candidate_step_in_window -- True if at least one candidate's OWN
        resolved step lands inside this row's own (fpedats, anndats], even
        when candidates disagree and step_date/prev_cfacshr above are
        therefore both null. resolve_eps_basis() uses this to force
        `basis_undetermined_ambiguous` rather than silently letting a
        genuine step-date disagreement fall through to
        `anndats_basis_assumed` just because no single agreed value
        survived the disagreement.
    """
    idx = permno_raw.index
    out_step_date = pd.Series(pd.NaT, index=idx, dtype="datetime64[ns]")
    out_prev_cfacshr = pd.Series(np.nan, index=idx, dtype="float64")
    out_any_in_window = pd.Series(False, index=idx, dtype="bool")

    permno_str = permno_raw.astype("string")
    has_permno = permno_str.notna() & (permno_str.str.len() > 0)
    if not has_permno.any():
        return out_step_date, out_prev_cfacshr, out_any_in_window

    work = pd.DataFrame({
        "_row_id": np.asarray(idx[has_permno]),
        "_permno_str": permno_str[has_permno].to_numpy(),
        "_anndats": pd.Series(np.asarray(anndats[has_permno]), dtype="datetime64[ns]"),
        "_fpedats": pd.Series(np.asarray(fpedats[has_permno]), dtype="datetime64[ns]"),
    })
    work = work.assign(_candidate=work["_permno_str"].str.split(",")).explode("_candidate", ignore_index=True)
    work["_candidate"] = pd.to_numeric(work["_candidate"], errors="coerce").astype("float64")
    work = work.sort_values("_anndats", kind="mergesort")

    right = (
        step_table.loc[step_table["is_change"], ["permno", "date", "prev_cfacshr"]]
        .rename(columns={"permno": "_candidate", "date": "_step_date"})
        .sort_values("_step_date", kind="mergesort")
    )
    merged = pd.merge_asof(
        work, right, left_on="_anndats", right_on="_step_date", by="_candidate", direction="backward"
    )

    # A candidate is "covered" (has a real, usable prior step to vote with)
    # only if merge_asof found one AND it is strictly positive -- the same
    # positivity guard as Fix 2 below, applied here so a zero/negative
    # prev_cfacshr can never masquerade as a real candidate step either.
    covered = merged["_step_date"].notna() & merged["prev_cfacshr"].notna() & (merged["prev_cfacshr"] > 0)

    in_window = covered & (merged["_step_date"] > merged["_fpedats"]) & (merged["_step_date"] <= merged["_anndats"])
    any_in_window = in_window.groupby(merged["_row_id"]).any()

    covered_only = merged.loc[covered, ["_row_id", "_step_date", "prev_cfacshr"]].copy()
    covered_only["_prev_cfacshr_r"] = covered_only["prev_cfacshr"].round(8)
    # Number of DISTINCT (step date, rounded prev_cfacshr) pairs among this
    # row's covered candidates: 0 (no candidate has a resolvable prior real
    # change) and 1 (every covered candidate agrees -- this is also what a
    # single, plain (non-comma) permno always reduces to, since it can only
    # ever contribute one candidate) both resolve; 2+ is a genuine
    # disagreement.
    n_distinct = (
        covered_only.drop_duplicates(["_row_id", "_step_date", "_prev_cfacshr_r"])
        .groupby("_row_id").size()
        .reindex(pd.Index(work["_row_id"].unique()), fill_value=0)
    )

    first = covered_only.groupby("_row_id")[["_step_date", "prev_cfacshr"]].first()
    agreed_ids = n_distinct.index[n_distinct <= 1]
    disagreed_ids = n_distinct.index[n_distinct > 1]
    agreed_with_value = first.index.intersection(agreed_ids)
    out_step_date.loc[agreed_with_value] = first.loc[agreed_with_value, "_step_date"].to_numpy()
    out_prev_cfacshr.loc[agreed_with_value] = first.loc[agreed_with_value, "prev_cfacshr"].to_numpy()

    # any_candidate_step_in_window is only meaningful -- and only ever set
    # True -- for rows where candidates DISAGREED (disagreed_ids). An
    # agreed row (including every ordinary single-candidate row, which by
    # construction can never disagree with itself) already gets a single
    # resolved step_date/prev_cfacshr above, and the normal
    # step_in_report_window logic downstream handles it correctly on its
    # own -- it must NOT also be flagged here, or it would wrongly force
    # basis_undetermined_ambiguous onto rows that should instead be free to
    # land on anndats_basis_confirmed or basis_undetermined_no_reference.
    any_in_window_disagreed = any_in_window.reindex(disagreed_ids, fill_value=False)
    out_any_in_window.loc[any_in_window_disagreed.index] = any_in_window_disagreed.to_numpy()

    return out_step_date, out_prev_cfacshr, out_any_in_window


def attach_share_basis(panel: pd.DataFrame, step_table: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Adds cfacshr_anndats, cfacshr_statpers, and share_basis_status to
    `panel` (which must already have `permno`, `anndats`,
    `consensus_statpers`, and `eps_actual`).

    cfacshr_statpers is only meaningfully resolved where consensus_statpers
    is non-null (resolve_share_basis treats a null target_date as
    unavailable).

    Does NOT set `eps_actual_adj` -- an earlier version of this function
    unconditionally computed it as `eps_actual / cfacshr_anndats`, which an
    adversarial review's initial estimate found wrong on ~30% of the narrow
    subset of rows where a real cfacshr step falls inside the row's own
    (fpedats, anndats] reporting window (it assumes the reported actual is
    always already on the POST-step basis, which is not always true; a
    later check arbitrated against Compustat found the real base rate is
    ~5.4%, not 30%, but the unconditional assumption is still wrong often
    enough to be worth correcting). That assignment moved to
    resolve_eps_basis() (below, called immediately after this function in
    build_event_table), which either CONFIRMS the post-step basis for the
    in-window rows where the evidence decisively supports it, or leaves
    `cfacshr_eps_basis` null (undetermined) otherwise -- it never asserts
    the pre-step basis instead, since that direction was measured too
    imprecise to trust (~19%; see resolve_eps_basis()'s own docstring). It
    sets `eps_actual_adj = eps_actual / cfacshr_eps_basis` -- the corrected
    divisor, null where undetermined -- for every row, not just the
    in-window ones. This function still runs first because
    resolve_eps_basis() needs `cfacshr_anndats` and `share_basis_status` as
    inputs.

    share_basis_status describes the cfacshr_anndats resolution
    specifically: "no_change" if resolved and equal to 1.0 (CRSP resets
    cfacshr to 1.0 immediately after a split, so this does NOT mean the
    permno has never split, only that nothing needs rescaling as of THIS
    row's own anndats), "adjusted" if resolved and not 1.0, or one of
    resolve_share_basis()'s four "unavailable_*" statuses otherwise. Note
    this is independent of whether sue_analyst's own R_c ratio
    (cfacshr_statpers / cfacshr_eps_basis) differs from 1.0 -- a row can
    show "no_change" here (cfacshr_anndats == 1.0) while still getting a
    real R_c-driven correction if cfacshr_statpers != cfacshr_anndats (e.g.
    a split landing between the consensus snapshot and an announcement that
    happens to land exactly on CRSP's post-split reset value of 1.0). Both
    cfacshr columns are exposed directly in the output, so R_c itself can
    always be recomputed downstream rather than inferred from this status.
    """
    df = panel.copy()

    cfacshr_anndats, status_anndats = resolve_share_basis(step_table, df["permno"], df["anndats"])
    cfacshr_statpers, _ = resolve_share_basis(step_table, df["permno"], df["consensus_statpers"])

    df["cfacshr_anndats"] = cfacshr_anndats
    df["cfacshr_statpers"] = cfacshr_statpers

    is_resolved = status_anndats == "resolved"
    is_no_change = is_resolved & np.isclose(cfacshr_anndats.to_numpy(dtype="float64", na_value=np.nan), 1.0)
    df["share_basis_status"] = np.select(
        [is_no_change, is_resolved],
        ["no_change", "adjusted"],
        default=status_anndats,
    )

    stats = {str(k): int(v) for k, v in df["share_basis_status"].value_counts(dropna=False).items()}
    return df, stats


def resolve_eps_basis(panel: pd.DataFrame, step_table: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Determines the per-share basis (`cfacshr_eps_basis`) that each row's
    reported `eps_actual` is actually denominated in, correcting
    attach_share_basis()'s unconditional assumption that it is always
    already on the post-step (`cfacshr_anndats`) basis.

    That assumption is only ever in question for the narrow subset of rows
    where a real CRSP `cfacshr` step (split/stock dividend) falls inside
    the row's own reporting window `(fpedats, anndats]` -- outside that
    window there is no ambiguity to resolve, and the prior assumption is
    kept as-is (`eps_basis_status="anndats_basis_assumed"`). An adversarial
    review's initial small-sample estimate put the unconditional version
    wrong on ~30% of that narrow in-window subset, fabricating fake extreme
    surprises as bad as the ones the original split-adjustment fix
    (attach_share_basis) was fixing.

    Inside the window, this discriminates between two hypotheses -- the
    actual is already on the post-step basis (`cfacshr_anndats`), or it is
    still on the pre-step basis (`cfacshr_prestep`) because the press
    release had not yet been restated -- using each ticker's own
    PRIOR-quarter actual as independent evidence for which hypothesis is
    closer to that ticker's own recent normal scale. The prior quarter is
    used deliberately, never a future one: using the NEXT quarter to
    resolve row t's basis would let a later publication flip row t's
    surprise sign, a real look-ahead violation.

    **Only the post-step hypothesis can ever be CONFIRMED here.** An
    independent arbitration of this exact discriminator against
    Compustat's basis-free net income (`niq`) found its two directions are
    not equally trustworthy: confirming the post-step basis (decisive
    ratio, and the prior-quarter reference sits closer to the post-step
    candidate) is ~99.13% precise, but confirming the pre-step basis
    (decisive ratio, reference closer to the pre-step candidate) is only
    ~18.9% precise -- wrong about 4 times out of 5, because a ticker's own
    prior-quarter level cannot tell "this is a stale pre-split value" apart
    from "this company's earnings genuinely grew a lot this quarter," and
    companies routinely split their stock right after a strong growth
    quarter. The same arbitration found the true base rate of genuinely
    pre-step cases in this in-window population is only ~5.4% -- at that
    base rate no discriminator built only from this ticker's own
    prior-quarter EPS can reliably confirm the pre-step direction, so that
    direction has been REMOVED entirely rather than tuned (see
    EPS_BASIS_DECISIVE_MIN_RATIO's own comment -- no threshold adjustment
    fixes this). A decisive ratio that favors the pre-step candidate is now
    just one more way to land in `basis_undetermined_ambiguous`, tagged via
    `eps_basis_evidence_ratio` for transparency but never acted on. Rows
    are only ever confirmed post-step or left explicitly undetermined,
    never guessed pre-step.

    Requires `panel` to already carry permno, fpedats, anndats, eps_actual,
    cfacshr_anndats, share_basis_status, and ibes_ticker (attach_share_basis
    must run first). Returns a copy of `panel` with five new columns:
      cfacshr_step_date, cfacshr_prestep -- the most recent REAL cfacshr
        change at or before anndats for this row's permno, and the cfacshr
        in force immediately before it (both null if none/unresolvable, or
        if permno is a comma-joined multi-candidate string whose candidates
        do not all agree on both values -- see
        _resolve_multi_candidate_step()). `cfacshr_prestep` is exported for
        transparency (so a downstream user can reconstruct the pre-step
        hypothesis themselves) but is NEVER used as the divisor for
        `cfacshr_eps_basis` -- see `basis_undetermined_ambiguous` below.
      cfacshr_eps_basis -- the single divisor to use everywhere downstream
        for this row's eps_actual (null means undetermined).
      eps_basis_status -- one of six values: "anndats_basis_assumed" (out
        of window, prior behavior kept), "basis_unverified_no_crsp"
        (share_basis_status is "unavailable_no_permno",
        "unavailable_no_target_date", or "unavailable_no_crsp_coverage" --
        no rescaling attempted, cfacshr_eps_basis=1.0, explicitly tagged
        rather than silently defaulted -- see build_event_table for why
        this fail-open default is a disclosed tradeoff, not a silent one),
        "basis_undetermined_ambiguous_permno" (share_basis_status is
        specifically "unavailable_ambiguous_permno" -- multiple candidate
        permnos disagree on cfacshr_anndats itself; cfacshr_eps_basis is
        null here, NOT 1.0, since this is a real data ambiguity rather than
        "nothing to rescale"), "basis_undetermined_no_reference" (in
        window, but no usable prior-quarter reference),
        "anndats_basis_confirmed" (in window, decisive, current code was
        right), "basis_undetermined_ambiguous" (in window with a usable
        reference,
        but the evidence does not decisively confirm the post-step basis
        -- including the case where it instead decisively favors the
        pre-step candidate, which is recorded via `eps_basis_evidence_ratio`
        but not acted on, per this function's own docstring above; also
        covers a comma-joined multi-candidate permno whose candidates
        disagree on the resolved step while at least one of them has a
        real step inside this row's own window).
      eps_basis_evidence_ratio -- the decisiveness ratio (max/min distance
        to the prior-quarter reference), non-null only where a single
        candidate step was actually resolved and evaluated against a usable
        reference (i.e. the row is in-window with a usable reference) --
        null for the multi-candidate-disagreement rows above, since there
        is no single cfacshr_prestep to compute a ratio against.
    Also sets `eps_actual_adj = eps_actual / cfacshr_eps_basis` here rather
    than in attach_share_basis (which runs before cfacshr_eps_basis exists)
    -- see attach_share_basis's docstring.

    Returns (df, stats) where stats is eps_basis_status's value_counts().
    """
    df = panel.copy()

    # --- 4.1: last real cfacshr change at or before anndats, per permno ---
    # Resolved per-candidate via _resolve_multi_candidate_step() (the same
    # explode/merge_asof/agree pattern resolve_share_basis() uses for
    # cfacshr_anndats) rather than a plain pd.to_numeric(errors="coerce")
    # cast -- an earlier version of this function used that cast directly,
    # which silently turned every comma-joined multi-candidate permno
    # string to NaN and dropped it from the in-window check entirely, even
    # on rows where attach_share_basis()'s own candidate resolution had
    # already agreed on cfacshr_anndats for that same row (undercounting
    # step_in_report_window by several dozen rows on the real pull -- see
    # FINDINGS.md). `multi_candidate_ambiguous_step` (used in 4.2 below)
    # catches the residual case: candidates that disagree on the step
    # itself, where at least one of them still has a real step in window.
    step_date, prev_cfacshr, multi_candidate_ambiguous_step = _resolve_multi_candidate_step(
        step_table, df["permno"], df["anndats"], df["fpedats"]
    )
    df["cfacshr_step_date"] = step_date.to_numpy()
    df["cfacshr_prestep"] = prev_cfacshr.to_numpy()

    # --- 4.2: row scope -- does a cfacshr step land in (fpedats, anndats]? ---
    unavailable = df["share_basis_status"].astype(str).str.startswith("unavailable_")
    step_in_report_window = (
        df["cfacshr_step_date"].notna()
        & (df["cfacshr_step_date"] > df["fpedats"])       # strictly after period end
        & (df["cfacshr_step_date"] <= df["anndats"])      # at or before announcement
        & df["cfacshr_prestep"].notna()
        # Fix 2: positivity guard. _resolve_multi_candidate_step() above
        # already excludes a non-positive prev_cfacshr from ever being
        # "covered" (so any cfacshr_prestep reaching this line today is
        # already positive) -- this check is deliberately kept anyway as a
        # local, self-contained invariant on THIS mask, so its correctness
        # never silently depends on a reader tracing through that other
        # function's internals. Latent today; cheap and correct regardless.
        & (df["cfacshr_prestep"] > 0)
        & df["cfacshr_anndats"].notna()
        & ~np.isclose(df["cfacshr_prestep"], df["cfacshr_anndats"])
        & ~unavailable
    )
    df["_step_in_report_window"] = step_in_report_window
    # A multi-candidate permno row where the candidates disagree on the
    # resolved step (so no single cfacshr_step_date/cfacshr_prestep
    # survived 4.1 above, and step_in_report_window is therefore False for
    # it) but at least one candidate's OWN step still falls inside this
    # row's own (fpedats, anndats] -- gated by ~unavailable so it never
    # overrides the unavailable_* handling in 4.6, which already produces
    # an honest null-basis outcome for those rows via a different route.
    # This must never silently fall through to anndats_basis_assumed: a
    # genuine step-date ambiguity is not "no basis question here."
    df["_multi_candidate_ambiguous_step"] = (multi_candidate_ambiguous_step & ~unavailable).to_numpy()
    # Combined "this row's OWN basis is genuinely in question" flag, used
    # below (4.3) to decide whether a row is trustworthy as some LATER
    # quarter's prior-quarter reference. Before Fix 3, _step_in_report_
    # window alone was a faithful proxy for this -- every way a row's own
    # basis could be in question required W=True. Fix 3 added a second way
    # (a multi-candidate permno with a disagreeing-but-in-window step) that
    # deliberately has W=False (no single cfacshr_step_date survived the
    # disagreement), so W alone would silently let such a row be used as a
    # "clean" reference for the next quarter -- relocating exactly the kind
    # of fabricated-surprise risk this whole discriminator exists to
    # eliminate, one quarter later.
    df["_basis_in_question"] = df["_step_in_report_window"] | df["_multi_candidate_ambiguous_step"]

    # --- 4.3: prior-quarter reference (point-in-time safe: prior rows only) ---
    # The panel is NOT sorted by (ibes_ticker, fpedats) at this point in
    # build_event_table (that sort happens only at the very end) -- sort
    # explicitly here, compute, then restore the caller's row order below.
    sorted_df = df.sort_values(["ibes_ticker", "fpedats"], kind="mergesort")
    g = sorted_df.groupby("ibes_ticker")

    prev_eps = g["eps_actual"].shift(1)
    prev_fpedats = g["fpedats"].shift(1)
    prev_basis = g["cfacshr_anndats"].shift(1)
    prev_inwin = g["_basis_in_question"].shift(1)
    gap_days = (sorted_df["fpedats"] - prev_fpedats).dt.days

    reference_ok = (
        prev_eps.notna() & prev_basis.notna() & (prev_basis > 0)
        & gap_days.between(EPS_BASIS_PRIOR_GAP_MIN_DAYS, EPS_BASIS_PRIOR_GAP_MAX_DAYS)
        & (prev_inwin == False)  # noqa: E712 -- NaN (no prior row) correctly excluded
    )
    reference = prev_eps / prev_basis

    # --- 4.4: two candidate values, on a common basis ---
    cand_post = sorted_df["eps_actual"] / sorted_df["cfacshr_anndats"]  # code's current assumption
    cand_pre = sorted_df["eps_actual"] / sorted_df["cfacshr_prestep"]   # release not yet restated
    d_post = (cand_post - reference).abs()
    d_pre = (cand_pre - reference).abs()

    # --- 4.5: decisiveness ---
    dmin = np.minimum(d_post, d_pre)
    dmax = np.maximum(d_post, d_pre)
    # dmin <= 0.0 yields inf (an exact match on one hypothesis is maximally
    # decisive, not a divide-by-zero).
    ratio = pd.Series(np.where(dmin <= 0.0, np.inf, dmax / dmin), index=sorted_df.index)
    W = sorted_df["_step_in_report_window"]
    sorted_df["eps_basis_evidence_ratio"] = ratio.where(W & reference_ok)

    # --- 4.6: assign status and basis -- order matters, see docstring ---
    status = pd.Series("anndats_basis_assumed", index=sorted_df.index, dtype=object)
    basis = sorted_df["cfacshr_anndats"].copy()

    # Fix 4: share_basis_status's "unavailable_*" umbrella used to map to a
    # single blanket eps_basis_status ("basis_unverified_no_crsp",
    # cfacshr_eps_basis=1.0) regardless of WHICH unavailable_* reason
    # applied. "unavailable_ambiguous_permno" specifically means multiple
    # candidate permnos disagreed on cfacshr_anndats itself -- a real data
    # ambiguity, not "nothing to rescale" -- so it gets its own status and a
    # null basis, split out below rather than defaulting to 1.0. The other
    # three unavailable_* reasons (no permno, no target date, no CRSP
    # coverage) genuinely have no basis information to work with at all;
    # 1.0 (no rescaling) is the only sane default for them, disclosed as a
    # fail-open tradeoff in build_event_table/FINDINGS.md rather than left
    # unstated.
    share_basis_status_sorted = sorted_df["share_basis_status"].astype(str)
    unavailable_sorted = share_basis_status_sorted.str.startswith("unavailable_")
    ambiguous_permno_sorted = share_basis_status_sorted == "unavailable_ambiguous_permno"

    status[unavailable_sorted] = "basis_unverified_no_crsp"
    basis[unavailable_sorted] = 1.0
    status[ambiguous_permno_sorted] = "basis_undetermined_ambiguous_permno"
    basis[ambiguous_permno_sorted] = np.nan

    sel = W & ~reference_ok
    status[sel] = "basis_undetermined_no_reference"
    basis[sel] = np.nan

    # Fix 1: the pre-step-confirming direction is REMOVED. Only a decisive
    # ratio THAT ALSO favors the post-step candidate (d_post < d_pre) is
    # ever confirmed -- measured ~99.13% precise against Compustat `niq`.
    # Everything else in-window with a usable reference (an indecisive
    # ratio, OR a decisive ratio that instead favors the pre-step
    # candidate) collapses into a single basis_undetermined_ambiguous
    # outcome: that direction was measured only ~18.9% precise (wrong
    # about 4 times out of 5, because a strong organic growth quarter looks
    # identical to a stale pre-split value using only this ticker's own
    # prior-quarter EPS) and is tagged, not acted on -- see this function's
    # docstring and EPS_BASIS_DECISIVE_MIN_RATIO's comment for why no
    # threshold adjustment fixes that direction.
    post_step_confirmed = (ratio >= EPS_BASIS_DECISIVE_MIN_RATIO) & (d_post < d_pre)

    sel = W & reference_ok & post_step_confirmed
    status[sel] = "anndats_basis_confirmed"
    basis[sel] = sorted_df.loc[sel, "cfacshr_anndats"]

    sel = W & reference_ok & ~post_step_confirmed
    status[sel] = "basis_undetermined_ambiguous"
    basis[sel] = np.nan

    # Diagnostic only -- does not affect status or basis. Of the rows just
    # folded into basis_undetermined_ambiguous above, this is specifically
    # the subset with a DECISIVE ratio that favors the pre-step candidate --
    # i.e. exactly the population the removed prestep_basis_confirmed
    # status used to claim. Reported in FINDINGS.md so the fix's impact is
    # traceable to a concrete, freshly-measured row count.
    would_have_been_prestep_confirmed = int((W & reference_ok & (ratio >= EPS_BASIS_DECISIVE_MIN_RATIO) & ~post_step_confirmed).sum())

    # Fix 3: a comma-joined multi-candidate permno row whose candidates
    # disagree on the resolved step (so W is False for it -- no single
    # agreed cfacshr_step_date survived the disagreement in 4.1/4.2 above),
    # but at least one candidate's own step still falls inside this row's
    # own (fpedats, anndats]. Disjoint from every sel above by construction
    # (_multi_candidate_ambiguous_step is only ever True where W is False),
    # so this cannot overwrite a real confirmed/no-reference outcome -- it
    # only catches rows that would otherwise silently fall through to the
    # anndats_basis_assumed default despite a genuine step-date question.
    sel = sorted_df["_multi_candidate_ambiguous_step"]
    status[sel] = "basis_undetermined_ambiguous"
    basis[sel] = np.nan
    # Diagnostic only: how many rows landed in basis_undetermined_ambiguous
    # specifically via this Fix-3 multi-candidate-disagreement route, as
    # opposed to the reference-based route above -- reported in FINDINGS.md
    # so the fix's real magnitude is traceable to a concrete row count.
    multi_candidate_forced_ambiguous_count = int(sel.sum())

    sorted_df["eps_basis_status"] = status
    sorted_df["cfacshr_eps_basis"] = basis
    sorted_df["eps_actual_adj"] = sorted_df["eps_actual"] / sorted_df["cfacshr_eps_basis"]

    # --- restore the caller's original row order ---
    out = sorted_df.reindex(df.index).drop(
        columns=["_step_in_report_window", "_multi_candidate_ambiguous_step", "_basis_in_question"]
    )

    stats = {str(k): int(v) for k, v in out["eps_basis_status"].value_counts(dropna=False).items()}
    stats["prestep_direction_would_have_confirmed"] = would_have_been_prestep_confirmed
    stats["multi_candidate_forced_ambiguous_count"] = multi_candidate_forced_ambiguous_count
    return out, stats


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

    Always returns a frame with the full expected column set, even when
    `resolved` produces zero crosswalk rows (e.g. an empty input) -- a bare
    `pd.DataFrame([])` from an empty `rows` list would otherwise have NO
    columns at all, which crashes attach_identifiers()'s merge with a
    KeyError instead of a clear, handled empty result.
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

    if not rows:
        return pd.DataFrame(columns=["ibes_ticker", "permno", "gvkey", "n_secids", "identifier_ambiguous"])
    return pd.DataFrame(rows)


def attach_identifiers(panel: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Left-joins the ibes_ticker -> permno/gvkey crosswalk onto `panel`
    (keyed on `ticker`), renaming `ticker` to `ibes_ticker` on the way out
    to make clear which ticker namespace it is (I/B/E/S's own, not
    OptionMetrics' or any other vendor's).

    identifier_matched reflects whether a USABLE identifier resulted
    (permno or gvkey actually populated), not merely whether the ticker
    string was present in the crosswalk at all -- a ticker can appear in
    the crosswalk (n_secids > 0) with a null permno and a null gvkey if
    resolved_universe_ids itself never resolved either for that secid, and
    that is not a match.
    """
    required_cols = {"ibes_ticker", "permno", "gvkey", "n_secids", "identifier_ambiguous"}
    missing = required_cols - set(crosswalk.columns)
    if missing:
        raise ValueError(
            f"attach_identifiers: crosswalk is missing required column(s) {sorted(missing)} "
            "-- empty or malformed crosswalk input. Use build_identifier_crosswalk() to build "
            "a correctly-shaped crosswalk (including for a zero-row input)."
        )

    df = panel.merge(crosswalk, left_on="ticker", right_on="ibes_ticker", how="left")
    df["identifier_matched"] = df["permno"].notna() | df["gvkey"].notna()
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
    null.

    sue_analyst and sue_seasonal are on different scales (analyst
    disagreement vs. this ticker's own historical year-over-year
    volatility) and analyst coverage is not uniform across the sample
    window (see FINDINGS.md's per-year coverage table) -- pooling
    sue_primary across sources is convenient for a single "does a surprise
    exist" column, but any analysis sensitive to scale or to coverage
    shifting over time should condition on sue_source, not assume the pool
    is homogeneous.
    """
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
    actu_raw: pd.DataFrame,
    statsumu_raw: pd.DataFrame,
    resolved_raw: pd.DataFrame,
    cfacshr_step_table: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """Runs the full cleaning pipeline over already-loaded raw DataFrames
    (see main() for the disk I/O). Returns (event_table, stats), where
    `stats` collects every count referenced in FINDINGS.md."""
    stats = {}

    actuals, actu_stats = clean_actuals(actu_raw)
    stats["actuals"] = actu_stats

    consensus, consensus_stats = clean_consensus(statsumu_raw)
    stats["consensus"] = consensus_stats

    actuals_resolved, dedup_stats = resolve_actual_duplicates(actuals)
    stats["dedup"] = dedup_stats
    stats["qtr_ann_validation"] = validate_qtr_ann_consistency(actuals)

    keys = actuals_resolved[["ticker", "fpedats", "anndats"]]
    matched = select_point_in_time_consensus(keys, consensus)

    panel = actuals_resolved.reset_index(drop=True).copy()
    panel["consensus_statpers"] = matched["consensus_statpers"]
    panel["consensus_meanest"] = matched["consensus_meanest"]
    panel["consensus_medest"] = matched["consensus_medest"]
    panel["consensus_stdev"] = matched["consensus_stdev"]
    panel["consensus_numest"] = matched["consensus_numest"]
    panel["n_candidates"] = matched["n_candidates"]
    panel["n_lookahead_excluded"] = matched["n_lookahead_excluded"]
    panel["consensus_staleness_days"] = (panel["anndats"] - panel["consensus_statpers"]).dt.days

    stats["point_in_time"] = {
        "events_with_any_candidate_consensus": int((matched["n_candidates"] > 0).sum()),
        "events_with_a_lookahead_candidate_excluded": int((matched["n_lookahead_excluded"] > 0).sum()),
        "total_lookahead_snapshots_excluded": int(matched["n_lookahead_excluded"].sum()),
        "events_with_point_in_time_safe_consensus": int(panel["consensus_meanest"].notna().sum()),
    }

    # Identifiers (permno in particular) must be attached BEFORE the
    # share-basis join, which resolves CRSP's per-permno cfacshr.
    crosswalk = build_identifier_crosswalk(resolved_raw)
    panel = attach_identifiers(panel, crosswalk)
    # How many secids in the broader universe actually have the null-both
    # condition identifier_matched's fix targets -- reported alongside the
    # panel-level match rate, since the two can legitimately diverge (a
    # secid with no usable identifier at all is also unlikely to have
    # surviving I/B/E/S actuals data to begin with).
    stats["both_null_identifier_secids"] = int(
        (resolved_raw["permno"].isna() & resolved_raw["gvkey"].isna()).sum()
    )

    panel, share_basis_stats = attach_share_basis(panel, cfacshr_step_table)
    stats["share_basis"] = share_basis_stats
    stats["share_basis_step_table_rows"] = len(cfacshr_step_table)

    # eps_basis: discriminates the narrow subset of rows where a real
    # cfacshr step falls inside (fpedats, anndats] -- see resolve_eps_basis
    # for why attach_share_basis's unconditional post-step assumption is
    # wrong on ~5.4% of exactly that subset (an adversarial review's initial
    # small-sample estimate put this at ~30%; a later, Compustat-arbitrated
    # check found the real base rate is much lower). Sets cfacshr_eps_basis
    # (the corrected divisor used everywhere below) and eps_actual_adj. Only
    # the post-step-confirming direction is ever asserted (~99% precise);
    # the pre-step-confirming direction (~19% precise) was removed rather
    # than tuned -- see resolve_eps_basis's own docstring.
    panel, eps_basis_stats = resolve_eps_basis(panel, cfacshr_step_table)
    stats["eps_basis"] = eps_basis_stats
    # basis_undetermined_ambiguous now also covers the (disjoint, W=False)
    # multi-candidate-permno-disagreement rows from Fix 3 -- see
    # resolve_eps_basis's own docstring -- so this total is "in window, OR
    # flagged ambiguous because at least one candidate permno's own step
    # was in window but candidates disagreed," not a pure W-boolean sum.
    # basis_undetermined_ambiguous_permno is deliberately EXCLUDED: it fires
    # purely off share_basis_status (attach_share_basis's own multi-
    # candidate agreement on cfacshr_anndats), independent of whether a step
    # falls in this row's own window at all -- the same reason
    # basis_unverified_no_crsp was already excluded before this fix.
    in_window_statuses = (
        "anndats_basis_confirmed",
        "basis_undetermined_ambiguous", "basis_undetermined_no_reference",
    )
    stats["eps_basis"]["step_in_report_window_total"] = sum(
        eps_basis_stats.get(s, 0) for s in in_window_statuses
    )

    # sue_analyst: R_c rescales the raw actual onto the SAME per-share
    # basis the (possibly pre-split) consensus was formed on, leaving the
    # consensus columns themselves untouched.
    cfacshr_ratio = panel["cfacshr_statpers"] / panel["cfacshr_eps_basis"]
    # statpers unresolvable but the actual's own basis IS known: assume the
    # consensus was formed on that same basis (no rescaling).
    cfacshr_ratio = cfacshr_ratio.where(panel["cfacshr_statpers"].notna(), 1.0)
    # basis undetermined: there is no defensible sue_analyst for this row.
    cfacshr_ratio = cfacshr_ratio.where(panel["cfacshr_eps_basis"].notna(), np.nan)

    sue_analyst_unadjusted = compute_sue_analyst(panel["eps_actual"], panel["consensus_meanest"], panel["consensus_stdev"])
    panel["sue_analyst"] = compute_sue_analyst(
        panel["eps_actual"] * cfacshr_ratio, panel["consensus_meanest"], panel["consensus_stdev"]
    )
    # share_basis_status alone can understate how many events got a real
    # sue_analyst correction: a row can resolve cfacshr_anndats to exactly
    # 1.0 (share_basis_status="no_change") while cfacshr_statpers still
    # differs from it -- e.g. a split landing exactly on CRSP's post-split
    # reset value. R_c != 1.0 (excluding rows where R_c itself is
    # undetermined, i.e. cfacshr_ratio is null) is the precise, direct
    # measure of "did this row's sue_analyst actually get rescaled."
    r_c_notna = cfacshr_ratio.notna()
    sue_analyst_r_c_adjusted = int((r_c_notna & ~np.isclose(cfacshr_ratio.fillna(1.0).to_numpy(dtype="float64"), 1.0)).sum())

    # The eps_basis fix's OWN before/after tail effect -- separate from the
    # original split-fix's >100 tracking below, and at a lower |sue_analyst|
    # threshold (10, not 100), since this fix corrects a narrower, subtler
    # population than the original three-named-event fix. "Before" here
    # means the immediately-prior code state (unconditionally assuming the
    # post-step/cfacshr_anndats basis -- the bug THIS fix corrects), not the
    # pre-any-split-fix raw sue_analyst tracked elsewhere. Per the fix
    # spec's own caveat, this is reported honestly rather than oversold as
    # "shrinking the tail": the value of this fix is that the REMAINING
    # large values are real actual-vs-consensus gaps, not basis artifacts.
    cfacshr_ratio_old_unconditional = (panel["cfacshr_statpers"] / panel["cfacshr_anndats"]).fillna(1.0)
    sue_analyst_old_unconditional = compute_sue_analyst(
        panel["eps_actual"] * cfacshr_ratio_old_unconditional, panel["consensus_meanest"], panel["consensus_stdev"]
    )
    old_extreme10 = sue_analyst_old_unconditional.abs() > 10
    new_extreme10 = panel["sue_analyst"].abs() > 10
    # Reconstructed from output columns alone: a basis_undetermined_ambiguous
    # row with a DECISIVE evidence_ratio (>= EPS_BASIS_DECISIVE_MIN_RATIO)
    # must be exactly the case that favored the pre-step candidate -- had it
    # favored post-step instead, it would have been anndats_basis_confirmed,
    # not ambiguous. This is exactly the population the removed
    # prestep_basis_confirmed status used to claim -- must match
    # resolve_eps_basis's own "prestep_direction_would_have_confirmed"
    # diagnostic, asserted below as an internal consistency check.
    would_have_been_prestep_mask = (
        (panel["eps_basis_status"] == "basis_undetermined_ambiguous")
        & (panel["eps_basis_evidence_ratio"] >= EPS_BASIS_DECISIVE_MIN_RATIO)
    )
    stats["eps_basis_fix_tail"] = {
        "extreme10_left": int((old_extreme10 & ~new_extreme10).sum()),
        "extreme10_entered": int((~old_extreme10 & new_extreme10).sum()),
        # Fix 1 removed the prestep-confirming direction entirely -- these
        # rows are now NULLED (undetermined), never resolved to a specific
        # value, so there is no "after" figure to report for them anymore.
        # This is how many of them USED TO be an extreme value under the
        # immediately-prior (prestep-confirming) code, before this fix.
        "prestep_direction_nulled_count": int(would_have_been_prestep_mask.sum()),
        "prestep_direction_extreme10_before_this_fix": int((old_extreme10 & would_have_been_prestep_mask).sum()),
    }
    if stats["eps_basis_fix_tail"]["prestep_direction_nulled_count"] != eps_basis_stats["prestep_direction_would_have_confirmed"]:
        raise AssertionError(
            "build_event_table postcondition violated: the post-hoc "
            "reconstruction of the removed prestep-confirming population "
            "from output columns alone "
            f"({stats['eps_basis_fix_tail']['prestep_direction_nulled_count']}) does not match "
            "resolve_eps_basis's own internal count "
            f"({eps_basis_stats['prestep_direction_would_have_confirmed']})"
        )

    # FAMR (fpedats 1998-06-30) is FINDINGS.md's running illustrative
    # example for this section: a real, CRSP-confirmed 3-for-1 split landing
    # right at the consensus-to-announcement boundary. The immediately-prior
    # unconditional-assumption code got it badly wrong (a new entrant into
    # the |sue_analyst|>100 extreme-tail set). The (now-removed)
    # prestep-confirming direction of the discriminator happened to get FAMR
    # right (it is independently Compustat-confirmed to genuinely BE a
    # pre-step case) -- but that direction was only ~19% precise overall and
    # could not reliably tell FAMR apart from the ~4-in-5 cases where the
    # identical evidence pattern was wrong (see AMSY/ASF/AEOS in the test
    # suite), so this fix now withholds an assertion for FAMR too: one
    # correct-by-luck answer given up for the sake of overall reliability.
    # Looked up dynamically from this run's own output, not hardcoded, so
    # the narrative can never silently drift out of sync with a re-pull.
    famr_mask = (panel["ibes_ticker"] == "FAMR") & (panel["fpedats"] == pd.Timestamp("1998-06-30"))
    if famr_mask.any():
        famr_pos = panel.index[famr_mask][0]
        famr_sue_after = panel.loc[famr_pos, "sue_analyst"]
        stats["famr_example"] = {
            "sue_analyst_before_this_fix": float(sue_analyst_old_unconditional.loc[famr_pos]),
            "sue_analyst_after_this_fix": float(famr_sue_after) if pd.notna(famr_sue_after) else None,
            "eps_basis_status": panel.loc[famr_pos, "eps_basis_status"],
        }
    else:
        stats["famr_example"] = None

    # Small-stock-dividend rows (cfacshr_prestep/cfacshr_anndats within
    # [1/1.3, 1.3]) that actually landed in basis_undetermined_ambiguous:
    # the discriminator has no statistical power at that magnitude (the two
    # candidate bases are too close together relative to normal
    # quarter-to-quarter EPS noise), so the claim being verified here is
    # specifically that a ratio in this band predicts landing in
    # basis_undetermined_ambiguous -- tracked here so FINDINGS.md doesn't
    # need to claim special-case handling that doesn't exist. (A small-ratio
    # row occasionally still resolves decisively if the prior-quarter
    # reference happens to align cleanly with one hypothesis -- this count
    # deliberately excludes those, since they are not the "no statistical
    # power" case being described.)
    step_ratio = panel["cfacshr_prestep"] / panel["cfacshr_anndats"]
    small_dividend_mask = (
        (panel["eps_basis_status"] == "basis_undetermined_ambiguous") & step_ratio.between(1 / 1.3, 1.3)
    )
    stats["eps_basis"]["small_stock_dividend_ambiguous_rows"] = int(small_dividend_mask.sum())

    # Real before/after numbers for the three named split-artifact events
    # an adversarial review flagged as suspiciously extreme (see
    # FINDINGS.md) -- looked up directly from this run's own output rather
    # than hardcoded, so they stay accurate if the underlying pull changes.
    named_split_events = {}
    for ticker, fped in [("FEMP", "2000-09-30"), ("MBI", "1997-09-30"), ("BGH", "2009-09-30")]:
        mask = (panel["ibes_ticker"] == ticker) & (panel["fpedats"] == pd.Timestamp(fped))
        if mask.any():
            pos = panel.index[mask][0]
            before = sue_analyst_unadjusted.loc[pos]
            after = panel.loc[pos, "sue_analyst"]
            named_split_events[ticker] = {
                "fpedats": fped,
                "sue_analyst_before": float(before) if pd.notna(before) else None,
                "sue_analyst_after": float(after) if pd.notna(after) else None,
                "share_basis_status": panel.loc[pos, "share_basis_status"],
                "cfacshr_anndats": float(panel.loc[pos, "cfacshr_anndats"]) if pd.notna(panel.loc[pos, "cfacshr_anndats"]) else None,
                "cfacshr_statpers": float(panel.loc[pos, "cfacshr_statpers"]) if pd.notna(panel.loc[pos, "cfacshr_statpers"]) else None,
            }
    stats["named_split_events"] = named_split_events

    # The net extreme-tail count can mask a real, honest dynamic: a
    # uniform correction fixes systematically wrong-basis events but can
    # occasionally also reveal a different one, where I/B/E/S's own
    # analyst-estimate-vs-reported-actual basis convention is itself
    # inconsistent around a split (e.g. a split announced/completed right
    # at the consensus-to-announcement boundary). Tracked explicitly
    # rather than only reporting the net delta.
    before_extreme_mask = sue_analyst_unadjusted.abs() > 100
    after_extreme_mask = panel["sue_analyst"].abs() > 100
    stats["coverage_extra"] = {
        "sue_analyst_extreme_left": int((before_extreme_mask & ~after_extreme_mask).sum()),
        "sue_analyst_extreme_entered": int((~before_extreme_mask & after_extreme_mask).sum()),
    }

    # sue_seasonal: the ENTIRE seasonal path runs off eps_actual_adj (see
    # compute_sue_seasonal's docstring) so a split landing between two
    # fiscal years never shows up as a fake year-over-year "surprise" and
    # never poisons the rolling std dev for subsequent quarters either.
    seasonal_unadjusted = compute_sue_seasonal(panel, ticker_col="ibes_ticker", eps_col="eps_actual")
    panel = compute_sue_seasonal(panel, ticker_col="ibes_ticker", eps_col="eps_actual_adj")

    panel = assign_sue_primary(panel)

    # Disclosure for basis_undetermined_ambiguous_permno's null default (a
    # round-4 review finding): unlike basis_unverified_no_crsp's disclosed
    # fail-open 1.0 default above, this status nulls cfacshr_eps_basis --
    # the honest choice for a real data ambiguity, but with the same
    # sue_seasonal cascade cost the fail-open default was built to avoid.
    # Measured directly (not estimated) so this disclosure stays accurate
    # if the ambiguous-permno population ever changes size.
    ambiguous_permno_mask = panel["eps_basis_status"] == "basis_undetermined_ambiguous_permno"
    panel_by_ticker = panel.sort_values(["ibes_ticker", "fpedats"])
    prev_was_ambiguous_permno = (
        panel_by_ticker.groupby("ibes_ticker")["eps_basis_status"]
        .shift(1)
        .eq("basis_undetermined_ambiguous_permno")
    )
    downstream_seasonal_lost = (
        prev_was_ambiguous_permno.fillna(False) & panel_by_ticker["sue_seasonal"].isna()
    ).sum()
    stats["eps_basis"]["ambiguous_permno_direct_seasonal_lost"] = int(
        (ambiguous_permno_mask & panel["sue_seasonal"].isna()).sum()
    )
    stats["eps_basis"]["ambiguous_permno_downstream_seasonal_lost"] = int(downstream_seasonal_lost)

    anndats_before_fpedats = int((panel["anndats"] < panel["fpedats"]).sum())

    panel["_ann_year"] = panel["anndats"].dt.year
    coverage_by_year = (
        panel.groupby("_ann_year")["sue_source"]
        .value_counts(normalize=True)
        .unstack(fill_value=0.0)
        .reindex(columns=["analyst", "seasonal", "none"], fill_value=0.0)
        * 100
    )
    counts_by_year = panel.groupby("_ann_year").size()
    panel = panel.drop(columns=["_ann_year"])

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
        "sue_analyst_extreme_before_split_fix": int((sue_analyst_unadjusted.abs() > 100).sum()),
        "sue_seasonal_extreme_before_split_fix": int((seasonal_unadjusted["sue_seasonal"].abs() > 100).sum()),
        "anndats_before_fpedats": anndats_before_fpedats,
        "sue_analyst_r_c_adjusted": sue_analyst_r_c_adjusted,
    }
    stats["coverage_by_year"] = coverage_by_year
    stats["counts_by_year"] = counts_by_year

    panel = panel[FINAL_COLUMNS].sort_values(["ibes_ticker", "fpedats"]).reset_index(drop=True)
    return panel, stats


# ---------------------------------------------------------------------------
# FINDINGS.md rendering.
# ---------------------------------------------------------------------------


def render_findings(panel: pd.DataFrame, stats: dict, statsumu_total_rows: int | None = None) -> str:
    actu = stats["actuals"]
    cons = stats["consensus"]
    dedup = stats["dedup"]
    pit = stats["point_in_time"]
    basis = stats["share_basis"]
    cov = stats["coverage"]
    by_year = stats["coverage_by_year"]
    n_by_year = stats["counts_by_year"]
    both_null_secids = stats["both_null_identifier_secids"]
    qtr_ann = stats["qtr_ann_validation"]
    extra = stats["coverage_extra"]
    named_events = stats["named_split_events"]
    step_table_rows = stats["share_basis_step_table_rows"]
    eps_basis = stats["eps_basis"]
    eps_tail = stats["eps_basis_fix_tail"]
    famr = stats["famr_example"]

    anndats_min = panel["anndats"].min()
    anndats_max = panel["anndats"].max()

    pct = lambda n, d: f"{100 * n / d:.1f}%" if d else "n/a"
    fmt_sue = lambda v: f"{v:.2f}" if v is not None else "n/a (no point-in-time consensus)"

    basis_total = sum(basis.values())
    year_rows = "\n".join(
        f"| {year} | {by_year.loc[year, 'analyst']:.1f}% | {by_year.loc[year, 'seasonal']:.1f}% | "
        f"{by_year.loc[year, 'none']:.1f}% | {int(n_by_year.loc[year]):,} |"
        for year in sorted(by_year.index)
    )

    statsumu_total_note = (
        f"{statsumu_total_rows:,} rows in the full file (read via Parquet footer metadata only, "
        f"not scanned)"
        if statsumu_total_rows is not None
        else "not measured in this run"
    )

    if qtr_ann["pass_rate"] is not None:
        qtr_ann_note = (
            f"Across {qtr_ann['n_cases']:,} such cases found in the real data ({qtr_ann['n_cases_december']:,} "
            f"of them a December/fiscal-year-end period, now included -- see the date-arithmetic fix "
            f"below), the ANN value matched the sum of the 4 QTR values (within 0.05) in "
            f"{qtr_ann['n_passed']:,} cases -- a {qtr_ann['pass_rate']:.1%} pass rate."
        )
    else:
        qtr_ann_note = "No qualifying cases (4 consecutive clean QTR quarters plus a matching ANN row) were found in this run's data to check."

    named_event_rows = []
    for ticker, split_desc in [("FEMP", "10-for-1 forward split"), ("MBI", "2-for-1 forward split"), ("BGH", "1-for-10 reverse split")]:
        ev = named_events.get(ticker)
        if ev is None:
            named_event_rows.append(f"| {ticker} | n/a | {split_desc} | not found in this run's output | -- |")
        else:
            named_event_rows.append(
                f"| {ticker} | {ev['fpedats']} | {split_desc} | {fmt_sue(ev['sue_analyst_before'])} | {fmt_sue(ev['sue_analyst_after'])} |"
            )
    named_events_table = "\n".join(named_event_rows)

    eps_basis_status_labels = [
        ("anndats_basis_assumed", "Out of window: no cfacshr step falls in this row's own (fpedats, anndats] -- nothing to discriminate, the post-step assumption is kept as-is."),
        ("basis_unverified_no_crsp", "cfacshr itself could not be resolved for this row at all (share_basis_status is unavailable_no_permno, unavailable_no_target_date, or unavailable_no_crsp_coverage) -- no rescaling attempted; cfacshr_eps_basis is explicitly set to 1.0 rather than silently defaulting. This is a disclosed fail-open default, not a silent one -- see the caveat below."),
        ("basis_undetermined_ambiguous_permno", "share_basis_status is specifically unavailable_ambiguous_permno: multiple candidate permnos disagree on cfacshr_anndats itself. Unlike the row above, this IS a real data ambiguity, not \"nothing to rescale\" -- cfacshr_eps_basis is null here, not 1.0."),
        ("anndats_basis_confirmed", "In window, and the ticker's own prior-quarter history decisively confirms the actual is already on the post-step basis -- the original assumption was right here. Measured ~99.13% precise against Compustat `niq`; the only direction this discriminator ever asserts."),
        ("basis_undetermined_ambiguous", "In window, a prior-quarter reference exists, but the evidence does not decisively confirm the post-step basis -- either the ratio is indecisive, or it is decisive but favors the PRE-step candidate instead (that direction was measured only ~18.9% precise -- wrong about 4 times out of 5 -- and is tagged via `eps_basis_evidence_ratio` for transparency but never acted on). Also covers a comma-joined multi-candidate permno whose candidates disagree on the resolved step while at least one of them has a real step in this row's own window. Includes small stock dividends -- see below."),
        ("basis_undetermined_no_reference", "In window, but no usable prior-quarter reference exists (missing prior quarter, gap outside the accepted day-count window, or the prior quarter is itself basis-ambiguous)."),
    ]
    eps_basis_status_rows = "\n".join(
        f"| `{status}` | {eps_basis.get(status, 0):,} | {pct(eps_basis.get(status, 0), cov['total_events'])} | {desc} |"
        for status, desc in eps_basis_status_labels
    )
    eps_basis_step_in_window = eps_basis["step_in_report_window_total"]

    entrant_note = (
        " -- investigated individually rather than silently accepted; see the next section"
        if extra["sue_analyst_extreme_entered"] > 0
        else ""
    )

    if famr is not None:
        famr_before = f"sue_analyst {famr['sue_analyst_before_this_fix']:.2f}"
        famr_after = (
            f"sue_analyst {famr['sue_analyst_after_this_fix']:.2f}"
            if famr["sue_analyst_after_this_fix"] is not None
            else "sue_analyst null (undetermined, not guessed -- see below)"
        )
        famr_status = famr["eps_basis_status"]
    else:
        famr_before = "sue_analyst 248.50 (from the review that first flagged it)"
        famr_after = "not present in this run's output"
        famr_status = "n/a"

    # The OLD (pre-fix) DateOffset-based check's real historical result on
    # this same pull -- 8,471 cases, 86.2% pass rate at tolerance 0.05.
    # There is no live code path that reproduces this anymore (the buggy
    # implementation was replaced, not kept alongside the fix), so it is
    # recorded here as a fixed historical reference point; the "after" side
    # and the multiplier between them are both computed fresh from this
    # run's own `stats` so they can never silently drift out of sync with
    # what actually ran.
    OLD_QTR_ANN_N_CASES = 8471
    qtr_ann_evidence_multiple = (
        qtr_ann["n_cases"] / OLD_QTR_ANN_N_CASES if qtr_ann["n_cases"] else 0.0
    )

    # Historical reference points from immediately before the multi-
    # candidate-permno fix (Fix 3) below -- there is no live code path that
    # reproduces the old plain-pd.to_numeric behavior anymore, so these are
    # recorded as fixed baselines, same pattern as OLD_QTR_ANN_N_CASES
    # above. The "after" side and every delta are computed fresh from this
    # run's own `stats` so they can never silently drift out of sync with
    # what actually ran.
    OLD_STEP_IN_REPORT_WINDOW_TOTAL = 1583
    multi_candidate_recovered = eps_basis_step_in_window - OLD_STEP_IN_REPORT_WINDOW_TOTAL
    # Precision-weighted split of the removed pre-step-confirming calls,
    # using the ~99.13%/~18.9% figures measured by the Compustat
    # arbitration described below -- illustrative, not re-derived from this
    # run (that arbitration is not reproduced by this pipeline; see the
    # "explicitly declined" scope note).
    prestep_removed_n = eps_tail["prestep_direction_nulled_count"]
    prestep_removed_likely_right = round(prestep_removed_n * 0.189)
    prestep_removed_likely_wrong = prestep_removed_n - prestep_removed_likely_right

    return f"""# I/B/E/S Earnings Data: Findings

This note documents how the raw WRDS I/B/E/S pull (plus CRSP daily share-
adjustment factors) was turned into a clean earnings-surprise table, one row
per (company, fiscal quarter), and what was found along the way. It assumes
no finance background.

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

`sue_analyst` and `sue_seasonal` are on different scales (analyst
disagreement vs. a company's own historical year-over-year volatility), and
which one is available shifts across the sample window (see the per-year
table below) -- `sue_primary` pools whichever is available for convenience,
but any analysis sensitive to scale or to coverage composition should
condition on `sue_source` rather than treat the pool as homogeneous.

"Point-in-time" means: only use information that was actually public at the
time being studied. Analyst consensus estimates get updated continuously --
including AFTER a company reports -- so it is easy to accidentally compute a
"surprise" using an estimate that was itself informed by the real result
(because it was calculated after the announcement). That is called
look-ahead bias, and it would make any later PEAD result meaningless, since
it would be testing whether the market reacts to information it never
actually had. Every consensus estimate used below is checked to have been
published strictly before the announcement it is being compared against.
This is checked with a strict `statpers < anndats` filter, which is verified
correct and unchanged from the prior version of this pipeline. What IS new is
`consensus_staleness_days` (`anndats - consensus_statpers`): the filter alone
does not bound how OLD an eligible snapshot can be, and a restated actual
announced years after its fiscal period can still be matched against a
consensus that, while technically point-in-time-safe, is far from fresh. That
gap is tagged on every row, not filtered -- per this project's "tag, don't
filter" principle, cutting on it is a decision for whichever later slice
actually runs statistics on this data.

## Coverage found

- Cleaned announcement dates (`anndats`) span **{anndats_min.date()} to
  {anndats_max.date()}**. The output file is named `ibes_clean_1995_2014.parquet`
  to match -- the plan's working title referenced the project's core
  1996-2013 window, but the actual WRDS pull included a one-year lookback
  and roughly six-month forward buffer around it (by design, so that
  quarters right at each edge of the core window still have prior-year
  history and prior consensus snapshots to draw on). Nothing was trimmed to
  force-fit the original filename. `statsumu_epsus` was not re-pulled to
  extend past 2013-12-31 -- that cutoff is a deliberate pull boundary, not a
  bug, and it produces a real coverage cliff in 2014 in the per-year table
  below (fewer quarters have a usable consensus snapshot simply because
  fewer snapshots exist on disk that late in the window). 1996 shows a
  milder version of the same edge effect from the lookback side.
- This cleans I/B/E/S at the full resolved-universe level (every ticker that
  resolved from the 8,358-secid OptionMetrics-linked universe), not filtered
  to any pilot list -- the pilot filter is a separate, later slice.
- Raw `actu_epsus.parquet`: {actu['raw_rows']:,} rows. {actu['non_eps_rows_dropped']:,} row(s) were a non-EPS `measure` variant and dropped before anything else. After dropping {actu['non_usd_rows_dropped']:,} non-USD-denominated rows (foreign-currency echoes of the same figure for cross-listed/ADR names -- a real universe reduction, not de-duplication: a handful of tickers have zero USD rows and disappear from the universe entirely) and {actu['null_actual_eps_rows_dropped']:,} rows with no reported EPS value at all, {actu['cleaned_rows']:,} usable rows remained ({actu['distinct_ticker_fpedats_groups']:,} distinct company/fiscal-quarter combinations before duplicate resolution, across both quarterly and annual/semiannual reporters).
- Raw `statsumu_epsus.parquet`: {statsumu_total_note}. Filtered at the Parquet layer itself (column projection to the ~11 fields actually used, plus a `fpi='6'` predicate pushdown) rather than loading the full table into memory: {cons['raw_rows']:,} rows matched fpi=6 (which turned out to already imply `measure='EPS'` and `fiscalp='QTR'` for every single row -- a clean filter, not three independent judgment calls), and {cons['non_usd_rows_dropped']:,} of those were also non-USD and dropped, leaving {cons['cleaned_rows']:,} usable consensus snapshots.

## Fix: quarterly vs. annual actuals are now told apart directly, not guessed

A prior version of this pipeline had no column distinguishing a quarterly
actual EPS from a full fiscal-year actual EPS in the raw `actu_epsus` pull
(WRDS's I/B/E/S actuals table has one -- `pdicity` -- it just was not part of
the originally pulled column set). Because a company's fiscal Q4 and its
fiscal year-end fall on the exact same calendar date, the SAME (ticker,
period-end date) can carry two genuinely different, both-valid EPS figures --
the quarter alone, and the full year -- and without `pdicity` there was no
column saying which was which.

**That prior version resolved the ambiguity by picking whichever candidate
value sat closest to the point-in-time-safe analyst consensus** -- which is
the numerator of `sue_analyst`, the very quantity being computed. An
adversarial review found this systematically stripped large genuine
surprises (the exact subpopulation a PEAD study needs) and produced an
estimated several hundred wrong-signed events. **That logic has been
completely removed.** `pdicity` was re-pulled and is now used directly:

- Rows by period type: {', '.join(f"{k}: {v:,}" for k, v in dedup['rows_by_period_type'].items())}.
- Keeping only `pdicity == 'QTR'` rows dropped {dedup['non_qtr_rows_dropped']:,} rows, taking {dedup['groups_before_period_filter']:,} (ticker, fpedats) groups down to {dedup['groups_after_period_filter']:,} ({pct(dedup['groups_dropped_by_period_filter'], dedup['groups_before_period_filter'])} of groups lost entirely, below the spec's ~2% expectation and well under its 5% safety ceiling). **Most of that loss is not "annual-only reporters"** -- of the {dedup['groups_dropped_by_period_filter']:,} dropped groups, {dedup['groups_dropped_ticker_reports_qtr_elsewhere']:,} ({pct(dedup['groups_dropped_ticker_reports_qtr_elsewhere'], dedup['groups_dropped_by_period_filter'])}) belong to a ticker that reports QTR actuals just fine for OTHER fiscal quarters, and {dedup['groups_dropped_december']:,} ({pct(dedup['groups_dropped_december'], dedup['groups_dropped_by_period_filter'])}) are specifically a December period-end. The real pattern is a **systematic Q4 coverage gap**: for a fiscal-year-end quarter, I/B/E/S sometimes only carried an ANN (full-year) row rather than a separate QTR row for that one quarter, not that the ticker never reports quarterly at all. Only {dedup['tickers_vanished_after_qtr_filter']:,} tickers vanish from the universe completely (every one of their reported periods was ANN/SAN, never QTR) -- that smaller, genuine "annual/semiannual-only reporter" case.
- Among the surviving QTR-only rows, {dedup['groups_multiple_anndats']:,} (ticker, fpedats) groups had genuinely different announcement dates for the same fiscal quarter (e.g. a restatement) -- the earliest anndats was kept in each case (that is when the market actually first learned the result), flagged via `eps_actual_multiple_anndats`.
- Separately, {dedup['groups_ambiguous_same_anndats_conflict']:,} groups ({pct(dedup['groups_ambiguous_same_anndats_conflict'], dedup['groups_after_period_filter'])} of QTR groups) reported more than one distinct EPS value under the exact same announcement date -- a genuine value conflict, not a quarterly/annual mix-up. These are resolved deterministically (sort by `eps_actual` ascending, keep the first row) and flagged `eps_actual_dup_flag="ambiguous_same_anndats_conflict"` -- never by proximity to consensus or by magnitude.
- `eps_actual_dup_flag` no longer has "consensus_disambiguated" or "magnitude_fallback" values anywhere in the code or output.
- On this pull, both of the counts in the two bullets above measured exactly zero: every surviving QTR-only (ticker, fpedats) group already has exactly one row. That is itself informative, not merely a non-finding -- it confirms the QTR/ANN conflation was the ENTIRE source of the old "649 restatement rows" and "~70,979 ambiguous value groups" this document used to report, not a separate, independent restatement or same-day-conflict phenomenon coexisting alongside it. The `eps_actual_multiple_anndats` and `ambiguous_same_anndats_conflict` machinery is kept (not deleted) because a future re-pull covering a different date range or a broader ticker set could still exercise it -- it is a correctness safeguard, not something proven unreachable in general.

**Non-circular validation of the `pdicity` labels themselves:** for (ticker,
fpedats) groups where the 3 prior same-ticker fiscal quarters each had
exactly one QTR row, and the current fpedats had both a QTR and an ANN row
for the same ticker, the ANN value should approximately equal the sum of the
4 QTR values if `pdicity` is labeling things correctly. {qtr_ann_note}

**Fix: a date-arithmetic bug was silently excluding every December
fiscal-year-end from this check.** The prior version of this check found
the "3 prior same-ticker fiscal quarters" by shifting each candidate QTR
row FORWARD by `pandas.DateOffset(months=3/6/9)` and merging back onto the
current row's own `fpedats`. `DateOffset` preserves day-of-month, so
`2000-09-30 + 3 months = 2000-12-30`, which never matches an actual
`2000-12-31` row -- silently excluding every December fiscal-year-end
(73% of the eligible sample) from ever being checked at all. The fix
replaces that with ordered prior-row lookups (the immediately preceding 1st,
2nd, and 3rd QTR rows for the same ticker, gated by a day-count window wide
enough for 52/53-week fiscal calendars but tight enough to reject a skipped
quarter), which has no day-of-month blind spot. The before/after evidence
is {qtr_ann_evidence_multiple:.2f}x larger ({OLD_QTR_ANN_N_CASES:,} cases before, {qtr_ann['n_cases']:,} after) at
materially the same pass rate ({qtr_ann['pass_rate']:.1%} vs. the prior
86.2%) -- confirmatory, not a reason to second-guess the QTR/ANN
disambiguation fix above. December cases specifically go from 0 (structurally
impossible under the old arithmetic) to {qtr_ann['n_cases_december']:,}.

## Fix: I/B/E/S actuals and consensus are split-unadjusted -- now corrected

I/B/E/S reports EPS actuals and consensus estimates on whatever per-share
basis was current when each was recorded, with no adjustment for stock
splits in between. A stock split between a consensus snapshot and the
announcement it is being compared against (or between two fiscal years, for
the seasonal measure) puts the two numbers on different per-share bases --
producing a "surprise" that is really just a units artifact, not a real beat
or miss.

**Three real events an adversarial review flagged as suspiciously extreme
turned out to be exactly this** -- not, as a prior version of this document
claimed, "spot-checked and confirmed genuine." All three were splits landing
between the consensus snapshot and the announcement:

| Ticker | Fiscal quarter | Split | Before fix (`sue_analyst`) | After fix |
|---|---|---|---|---|
{named_events_table}

Correction mechanism: CRSP's daily `cfacshr` (cumulative share-adjustment
factor) is resolved for both the announcement date (`cfacshr_anndats`) and
the selected consensus's `statpers` (`cfacshr_statpers`), compressed from
CRSP's ~20M-row daily file set into a ~{step_table_rows:,}-row "step table" (one row per
permno per adjustment event) via a groupby/shift/diff, then resolved for an
arbitrary date via `merge_asof`. `sue_analyst` rescales the raw actual by
`R_c = cfacshr_statpers / cfacshr_eps_basis` before comparing it to consensus
(defaulting to no adjustment, R_c=1.0, whenever `cfacshr_statpers` cannot be
resolved; left null when `cfacshr_eps_basis` itself is undetermined -- see
below) -- mathematically equivalent to adjusting both sides to a common
basis while leaving the consensus columns themselves untouched. `sue_seasonal`
runs its ENTIRE year-over-year path (the prior-year lookup, the diff, and the
rolling std dev that scales it) off the split-adjusted actual
(`eps_actual_adj = eps_actual / cfacshr_eps_basis`) instead of the raw one, so a
split does not masquerade as a year-over-year surprise and does not go on to
poison the volatility estimate for several subsequent quarters either -- this
second effect was found only during the fix's own verification pass, and
turned out to be the larger of the two contamination sources (a ~7.75% raw
contamination rate on the year-over-year side vs. the originally-flagged
consensus-side issue, since annual gaps make splits far more likely than the
short consensus-to-announcement gap).

`share_basis_status` (whether `cfacshr_anndats` itself needed rescaling from 1.0) breakdown across all {basis_total:,} events: {', '.join(f"{k}: {v:,} ({pct(v, basis_total)})" for k, v in basis.items())}. Note "no_change" here does not mean "never split": CRSP resets `cfacshr` to 1.0 immediately after a split, so a row can show `cfacshr_anndats == 1.0` (share_basis_status="no_change") while still getting a real `sue_analyst` correction if `cfacshr_statpers` differs from it. `resolve_share_basis`'s "unavailable" case is now split into four specific statuses (previously two of them were conflated into one "unavailable_no_permno" label): no usable permno at all, a permno but no usable target date, an ambiguous multi-candidate permno, or a permno with no CRSP coverage on or before the target date -- see the module docstring for exactly which is which.

Measured effect on the extreme tail (`|sue_*| > 100`, using the raw,
completely unadjusted `sue_analyst` as the "before" baseline): `sue_analyst` had
{cov['sue_analyst_extreme_before_split_fix']:,} such events before any split-basis correction and {cov['sue_analyst_extreme']:,} after (both fixes below combined); `sue_seasonal` had
{cov['sue_seasonal_extreme_before_split_fix']:,} before and {cov['sue_seasonal_extreme']:,} after. The net `sue_analyst`
change is not one-directional: {extra['sue_analyst_extreme_left']:,} events (including all three named above) LEFT the
extreme set, while {extra['sue_analyst_extreme_entered']:,} newly ENTERED it{entrant_note} -- a reminder that a uniform
correction can occasionally reveal a DIFFERENT extreme where I/B/E/S's own analyst-estimate-vs-reported-actual basis
convention is itself inconsistent around a split, not just fix the previously-known ones. (FAMR, fpedats 1998-06-30,
was exactly this kind of new entrant under an earlier, intermediate version of this correction -- see the next
section: a later fix nulls FAMR's `sue_analyst` outright rather than resolving it to a specific value, which is why
it no longer appears in the extreme tail at all, not because it was relocated to a different bucket.)

## Fix: the post-step-basis assumption itself was wrong on part of the rows it applied to -- and the first fix for that was itself only half right

The correction above assumes the reported `eps_actual` is already on the
POST-step basis (`cfacshr_anndats`) whenever a split falls between the
consensus snapshot and the announcement. A second, later adversarial review
found that assumption applied unconditionally, and -- per that review's own
initial small-sample estimate -- wrong on roughly 30% of the narrow subset
of rows where a real `cfacshr` step falls strictly inside that row's own
reporting window -- after the fiscal period ended but at or before the
announcement (`(fpedats, anndats]`) -- fabricating new fake extreme
surprises as bad as the ones the original fix corrected. FAMR (fpedats
1998-06-30) is exactly this: a real, CRSP-confirmed 3-for-1 split landing
right at the consensus-to-announcement boundary, which the unconditional
assumption got wrong ({famr_before}).

**Why that specific window, and not simply "every row with a split
anywhere nearby":** a step BEFORE the fiscal period ended means the
financial statements were prepared entirely post-step, so the press
release must already be on the post-step basis -- no ambiguity, nothing to
check. A step AFTER the period ended but before or at the announcement is
genuinely unknown: I/B/E/S sometimes restates a just-completed quarter's
actual onto the new post-split basis before announcing it, and sometimes
does not.

**First attempt at a fix, and why it was itself wrong roughly 4 times out
of 5 in one direction.** A discriminator was built to resolve the in-window
rows using each ticker's OWN prior-quarter actual as evidence -- never a
future quarter, since using one would let a later publication flip an
earlier row's surprise sign, a real look-ahead violation. Two candidate
values are formed (actual assumed post-step vs. pre-step), compared against
that prior quarter's own EPS level, and trusted apart only when one fits at
least {EPS_BASIS_DECISIVE_MIN_RATIO:.1f}x better than the other. That
discriminator originally shipped with BOTH directions active: a decisive
ratio could confirm either the post-step basis (original code was right) or
the pre-step basis (original code was wrong, "corrected" to a specific
pre-step value).

**An independent arbitration against Compustat's basis-free net income
(`niq` -- never otherwise seen by this pipeline, and not affected by a
per-share basis question) found the two directions are not equally
trustworthy.** Confirming the post-step basis is ~99.13% precise --
effectively reliable. Confirming the pre-step basis is only ~18.9% precise
-- **wrong about 4 times out of 5.** The root cause is a confound this
ticker-only evidence cannot see through: a stale pre-split value and a
genuinely large quarter-over-quarter earnings increase look IDENTICAL to
this discriminator, and companies routinely split their stock right after a
strong growth quarter, so "the actual looks much bigger than last quarter"
is exactly as consistent with "still on the old, larger-denominator basis"
as it is with "this company just had a great quarter." The same arbitration
found the TRUE base rate of genuinely pre-step cases within this in-window
population is only ~5.4% -- not the ~30% the first, less rigorous review
assumed (that ~30% was itself an artifact of the same growth/stale-split
confound). At a ~5.4% base rate, no discriminator built only from this
ticker's own prior-quarter EPS can reliably confirm the pre-step direction:
verified mathematically infeasible, a classic low-base-rate screening
problem, not a threshold-tuning problem. Raising or lowering
{EPS_BASIS_DECISIVE_MIN_RATIO:.1f} would not fix it either -- no ratio
threshold separates "stale pre-split value" from "genuine earnings growth"
using only this evidence.

**Fix: the pre-step-confirming direction has been removed entirely, not
tuned.** Only the post-step-confirming direction (~99% precise) is ever
asserted; it is the sole reason `anndats_basis_confirmed` exists. Every
other in-window row -- a decisive-but-wrong-direction ratio, an indecisive
ratio, or (see the multi-candidate-permno fix below) an unresolvable
step-date disagreement -- is left explicitly undetermined
(`cfacshr_eps_basis` null, `eps_actual_adj` null, `sue_analyst` null,
`sue_seasonal` null) rather than guessed, per this project's "tag, don't
filter" principle. FAMR is one of the rows this costs: it is independently
Compustat-confirmed to genuinely BE a pre-step case, and the removed
direction happened to get it right ({famr_after}, `{famr_status}`) -- but
that direction could not reliably tell FAMR apart from the ~4-in-5 cases
where the identical evidence pattern was wrong, so this fix now withholds
an assertion for FAMR too, the same as every other row that direction used
to decide. This is a deliberate, evidence-based trade: giving up
{prestep_removed_n:,} calls the removed direction would have made (an
estimated ~{prestep_removed_likely_right:,} of them probably right and
~{prestep_removed_likely_wrong:,} probably wrong, at its measured ~18.9%
precision) in exchange for never again fabricating an extreme surprise like
FEMP/MBI/BGH above. {eps_tail['prestep_direction_extreme10_before_this_fix']:,}
of the {prestep_removed_n:,} nulled rows had `|sue_analyst| > 10` under the
immediately-prior code; they are honestly null now instead.

Six statuses result:

| `eps_basis_status` | Count | % of events | Meaning |
|---|---|---|---|
{eps_basis_status_rows}

{eps_basis_step_in_window:,} events ({pct(eps_basis_step_in_window, cov['total_events'])}) fall inside the ambiguity window (or are treated as if they might -- see the multi-candidate-permno fix below) -- confirming this is a narrow, targeted fix, not a wholesale rework: `anndats_basis_confirmed` ({eps_basis.get('anndats_basis_confirmed', 0):,}) means the original assumption was right; the remaining {eps_basis.get('basis_undetermined_ambiguous', 0) + eps_basis.get('basis_undetermined_no_reference', 0):,} are left explicitly undetermined rather than guessed. `basis_undetermined_ambiguous_permno` is deliberately excluded from this window total -- it fires purely off `share_basis_status` (whether multiple candidate permnos agreed on `cfacshr_anndats` at all), independent of whether a step falls in this row's own window, the same reason `basis_unverified_no_crsp` was already excluded before this fix. Because `anndats_basis_assumed` is its own distinct value, a downstream analysis that wants to drop every row with any basis question at all can do so with `eps_basis_status != "anndats_basis_assumed"`, without needing a separate boolean flag column.

Of the {eps_basis.get('basis_undetermined_ambiguous', 0):,} `basis_undetermined_ambiguous` rows, {eps_basis['small_stock_dividend_ambiguous_rows']:,} have a `cfacshr_prestep`/`cfacshr_anndats` ratio between 1/1.3 and 1.3 -- small stock dividends, not full splits. These need no special handling: the discriminator has no statistical power at that magnitude (the two candidate bases sit too close together relative to normal quarter-to-quarter EPS movement to tell apart), so they land in `basis_undetermined_ambiguous` automatically, which is the correct, honest outcome.

**Fix: `basis_unverified_no_crsp`'s fail-open default is a disclosed tradeoff, not a silent one.** Rows where cfacshr could not be resolved at all fall back to `cfacshr_eps_basis = 1.0` (no rescaling) rather than being left null -- otherwise a single unresolvable row would cascade into nulling `sue_seasonal` for every later quarter that looks it up as a year-ago reference (see the seasonal-window fix below). This IS a real, disclosed tradeoff: for a row that genuinely did straddle an unresolvable split, `1.0` is simply wrong, silently understating or overstating the true surprise. It is accepted here because these rows ({eps_basis.get('basis_unverified_no_crsp', 0):,} of them, all specifically `unavailable_no_permno`/`unavailable_no_target_date`/`unavailable_no_crsp_coverage` -- no CRSP data exists for them at all) are typically dropped downstream anyway for lack of CRSP price data to compute a return against, and the alternative (nulling `cfacshr_eps_basis` here too) would only propagate the same unresolvable gap further downstream instead of fixing it. A DIFFERENT unavailable case -- `unavailable_ambiguous_permno`, where CRSP data exists but multiple candidate permnos disagree on `cfacshr_anndats` -- does NOT get this fail-open default: it is a real data ambiguity, not "nothing to rescale," so it now gets its own status (`basis_undetermined_ambiguous_permno`, {eps_basis.get('basis_undetermined_ambiguous_permno', 0):,} rows) with `cfacshr_eps_basis` null, the same honest treatment as any other undetermined row. Nulling here (rather than also failing open to `1.0`) is the defensible choice for a real ambiguity, but it is not free: it costs `sue_seasonal` on all {eps_basis.get('ambiguous_permno_direct_seasonal_lost', 0):,} of these rows directly, plus {eps_basis.get('ambiguous_permno_downstream_seasonal_lost', 0):,} more on the following quarter of the same ticker, which loses its year-ago reference -- the identical cascade mechanism the `basis_unverified_no_crsp` fail-open default above exists to avoid, just accepted here instead of worked around, because there is no safe default to fail open to when the ambiguity is real. Every one of these rows carries `identifier_ambiguous=True` (see Identifiers, below), so a downstream analysis can see and account for them.

**Fix: a multi-candidate (comma-joined) permno row's own in-window step detection used to be silently skipped entirely.** `permno` is occasionally a comma-joined multi-candidate string (an upstream identifier resolution left more than one candidate PERMNO on the table -- see Identifiers, below). The in-window step-date lookup used to cast this string straight through `pd.to_numeric(errors="coerce")`, which turns any comma-joined value to `NaN` and drops the row from the in-window check entirely -- even on a row where `cfacshr_anndats` itself had already been resolved from the SAME candidates elsewhere in this pipeline. The fix resolves each candidate permno independently (the same explode/agree pattern used for `cfacshr_anndats`): if every CRSP-covered candidate agrees on both the step date and the pre-step `cfacshr`, that agreed value is used and the row proceeds through the exact same logic as any other row above; if candidates disagree, the row's step date/pre-step `cfacshr` are left null as before, but if at least one candidate has a real step inside this row's own window, the row is force-flagged `basis_undetermined_ambiguous` rather than silently defaulting to `anndats_basis_assumed` -- a genuine step-date question must never disappear just because more than one candidate permno was on the table. This recovers {multi_candidate_recovered:,} rows from `anndats_basis_assumed` into the in-window population ({eps_basis_step_in_window:,} vs. a previous {OLD_STEP_IN_REPORT_WINDOW_TOTAL:,}); most of them have every candidate agreeing and land wherever the normal reference-based logic above puts them, and {eps_basis['multi_candidate_forced_ambiguous_count']:,} are specifically the candidates-disagree-but-at-least-one-was-in-window case this fix's load-bearing check exists for.

**This fix's value is NOT shrinking the extreme tail -- it is making the
REMAINING large values trustworthy.** At the `|sue_analyst| > 10` threshold
(a lower bar than the `> 100` tail tracked above, chosen because this fix's
population is narrower and subtler than the original three-event fix):
{eps_tail['extreme10_left']:,} events left that tail and {eps_tail['extreme10_entered']:,} newly entered it.
That is not a bug: it means the surprise is now computed against the
correct per-share basis (or honestly left null when it cannot be), and
whatever tail remains reflects real actual-vs-consensus gaps rather than an
arithmetic artifact of comparing two numbers on different per-share bases.

**Caveat carried forward, not solved here: `cfacshr` is cumulative to the
end of the CRSP file on disk (2014-06-30), not to the end of this
project's 1996-2013 core sample.** `eps_actual_adj` and `eps_diff_yoy` are
therefore levels denominated in a per-share basis that is partly fixed by
corporate actions that happened AFTER the sample window each row belongs
to. `sue_seasonal` itself is unaffected -- it divides a year-over-year diff
and that diff's own rolling standard deviation by the same per-ticker
constant, which is scale-invariant -- but any FUTURE use of the level
columns (`eps_actual_adj`, `eps_diff_yoy`) for winsorizing, price-scaling,
or pooling across firms needs to re-base `cfacshr` to a common reference
point first. No consumer of these level columns exists yet, so re-basing
is deliberately deferred rather than built speculatively; this is a
documented caveat for whichever later slice is first to need it.

## The point-in-time filter's measured effect

Of the {cov['total_events']:,} (company, fiscal-quarter) events, {pit['events_with_any_candidate_consensus']:,} had at least one fpi=6 consensus snapshot for that fiscal quarter at all (eligible or not). Of those, **{pit['events_with_a_lookahead_candidate_excluded']:,} events had at least one candidate consensus snapshot that would have been look-ahead contamination** (published on or after the announcement date) had it not been explicitly filtered out. Across all events, **{pit['total_lookahead_snapshots_excluded']:,} individual consensus snapshots** were excluded on exactly this basis. This is expected and mechanical, not a data error: I/B/E/S keeps publishing new consensus snapshots for years after any given historical quarter (as analysts revise their NEXT-quarter forecasts), so "the last available snapshot for this fiscal quarter" is almost always a post-announcement one -- which is precisely why an explicit `statpers < anndats` filter matters rather than simply taking the latest available row per fiscal quarter.

After the filter, {pit['events_with_point_in_time_safe_consensus']:,} of {cov['total_events']:,} events ({pct(pit['events_with_point_in_time_safe_consensus'], cov['total_events'])}) have a usable, point-in-time-safe consensus snapshot at all.

**A previously-stated safety claim here was backwards and has been
corrected.** The point-in-time filter guarantees a selected consensus was
published before the announcement -- it does NOT guarantee the consensus is
fresh. A restated or late actual announced long after its fiscal period ended
can be, and in this data is, matched against a consensus snapshot that is
technically eligible but calendar-old. `consensus_staleness_days` now tags
this gap on every row with a selected consensus so a later slice can decide
whether and how to cut on it; the cleaning layer itself does not filter on
it.

## How the {actu['pre_1995_pends_rows']:,} pre-1995 pends rows were handled

`actu_epsus` has rows whose fiscal period-end (`pends`) predates 1995 even
though the pull window starts at 1995 -- these are late or restated reports
of old fiscal periods, not a pull bug (the pull filtered on announcement
date, not period-end date). They were kept, not dropped: a late or restated
result is still real information, and the point-in-time filter above is
exactly what keeps this safe regardless of how old the underlying period is
-- it compares the analyst consensus's STATPERS against this row's own
`anndats` (when the restated figure actually became public), never against
the ancient `pends`.

Separately, {cov['anndats_before_fpedats']:,} events have an announcement date recorded BEFORE their own fiscal period even ended -- these are I/B/E/S key errors (a period cannot be reported on before it ends), and for these rows the point-in-time filter is vacuous by construction (there is no meaningful "before this announcement" consensus window to speak of). Left as-is in the output rather than dropped, consistent with this pipeline's general policy of tagging data problems rather than silently filtering them.

## Analyst vs. seasonal coverage, by year

`sue_analyst` computed (usable consensus with non-null, non-negligible
dispersion): {cov['sue_analyst_available']:,} of {cov['total_events']:,} events ({pct(cov['sue_analyst_available'], cov['total_events'])}). `sue_seasonal` computed (an exact
same-fiscal-quarter-one-year-ago match existed, at least {MIN_SEASONAL_DIFF_HISTORY} valid diffs were
available within the trailing {ROLLING_SEASONAL_WINDOW} quarters, and those diffs span no more than
{MAX_SEASONAL_WINDOW_SPAN_DAYS} calendar days -- see the seasonal-window fix below): {cov['sue_seasonal_available']:,} of {cov['total_events']:,} events ({pct(cov['sue_seasonal_available'], cov['total_events'])}).

Pooled averages hide real edge-of-window effects, so here is the coverage by
announcement year (percent of that year's events, not cumulative):

| Year | Analyst % | Seasonal % | Neither % | Events |
|---|---|---|---|---|
{year_rows}

1996 and 2014 are visibly thinner than the interior years -- 1996 sits right
at the one-year seasonal-history lookback's edge (fewer tickers have a full
prior year on record yet), and 2014 sits past `statsumu_epsus`'s deliberate
2013-12-31 pull cutoff (consensus coverage thins out approaching and past
that boundary, by construction, not by bug).

Per the plan's preemptive-check design (prefer real analyst data, fall back to seasonal only where analyst coverage is missing): `sue_primary` came from `sue_analyst` for {cov['sue_primary_from_analyst']:,} events, from `sue_seasonal` for {cov['sue_primary_from_seasonal']:,} events, and neither measure was computable for {cov['sue_primary_none']:,} events ({pct(cov['sue_primary_none'], cov['total_events'])}) -- these rows are kept in the output (not dropped) with `sue_source="none"`.

Both measures still have a small tail of extreme values after the split fix ({cov['sue_analyst_extreme']:,} `sue_analyst`, {cov['sue_seasonal_extreme']:,} `sue_seasonal`, magnitude over 100) -- with the three known split-basis artifacts corrected, the remaining tail is a mix of genuine large one-time events (write-offs, bankruptcies) landing on unusually low estimate disagreement or historical volatility, and possibly further data issues not yet investigated. Deciding whether and how to winsorize or cap these for statistical analysis is left to whichever later slice actually runs event-study statistics on this data.

## Fix: the seasonal scaling window is now calendar-bounded, not just row-count-bounded

The trailing {ROLLING_SEASONAL_WINDOW}-quarter window used to scale `sue_seasonal` is defined over PANEL
ROWS for a ticker, not calendar time. A ticker with reporting gaps can have
{ROLLING_SEASONAL_WINDOW} prior rows spanning many YEARS rather than {ROLLING_SEASONAL_WINDOW} recent quarters -- verified cases in
this data span up to 15 years. `seasonal_window_span_days` now measures the
actual calendar gap between a row's fpedats and the oldest fpedats
contributing a real diff to its rolling window, and `seasonal_diff_stdev`
(and therefore `sue_seasonal`) is nulled out whenever that span exceeds
{MAX_SEASONAL_WINDOW_SPAN_DAYS} days (~3 years), even where the row-count history requirement was
already satisfied.

One known, out-of-scope asymmetry: the year-ago match uses
`fpedats - DateOffset(years=1)`, which does not special-case Feb 29 in a leap
year (a Feb 29 fpedats has no exact match one non-leap year later). This is
unchanged from the prior version of this pipeline and left as a documented
limitation rather than fixed here.

## Identifiers

`resolved_universe_ids.parquet` is one row per OptionMetrics secid (8,358 rows), not one row per I/B/E/S ticker, and a handful of secids resolve ambiguously to more than one candidate PERMNO/GVKEY/ticker (recorded there as a comma-joined string rather than a silently-picked guess). This file's `ibes_ticker` -> PERMNO/GVKEY crosswalk was built by exploding those comma-joined ticker values into individually-joinable tickers and unioning whatever PERMNO/GVKEY values map to each one: {cov['identifier_matched']:,} of {cov['total_events']:,} events ({pct(cov['identifier_matched'], cov['total_events'])}) matched to a USABLE identifier. `identifier_matched` requires permno OR gvkey to actually be populated, not merely that the ticker string appeared in the crosswalk. On THIS pull, that stricter definition is mathematically identical to plain ticker-presence: none of the {both_null_secids:,} (of 8,358) secids in the broader universe with BOTH permno and gvkey null have a surviving I/B/E/S ticker in this output at all (a secid with no usable identifier is also unlikely to have clean, surviving actuals data), so the two definitions can never disagree on this specific data. The stricter check is defense-in-depth for a FUTURE pull where that coincidence might not hold, not something that changed any number here today.

Of the matched events, {cov['identifier_ambiguous']:,} ({pct(cov['identifier_ambiguous'], cov['identifier_matched'])} of matched events) carry more than one distinct candidate PERMNO or GVKEY (comma-joined in the output, `identifier_ambiguous=True`) -- most often because the same company was reassigned a new PERMNO/GVKEY after a corporate restructuring. This is flagged, not resolved, here: resolving it correctly needs announcement-date-aware matching against equity history, which belongs in the later event-table-assembly slice, not this one. {cov['identifier_unmatched']:,} events had no usable identifier at all.

## Output

`data/earnings/ibes_clean_1995_2014.parquet` -- {len(panel):,} rows, one per (I/B/E/S ticker, fiscal quarter). Columns: {', '.join(FINAL_COLUMNS)}.
"""


def main():
    actu_raw = pd.read_parquet(ACTU_EPSUS_PATH)

    statsumu_total_rows = _parquet_row_count(STATSUMU_EPSUS_PATH)
    statsumu_raw = pd.read_parquet(
        STATSUMU_EPSUS_PATH,
        columns=STATSUMU_USECOLS,
        filters=[("fpi", "==", "6")],
    )

    resolved_raw = pd.read_parquet(RESOLVED_UNIVERSE_PATH)
    cfacshr_step_table = load_cfacshr_step_table()

    panel, stats = build_event_table(actu_raw, statsumu_raw, resolved_raw, cfacshr_step_table)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUTPUT_PATH, index=False)
    print(f"Wrote {len(panel):,} rows to {OUTPUT_PATH}", flush=True)

    findings = render_findings(panel, stats, statsumu_total_rows=statsumu_total_rows)
    FINDINGS_PATH.write_text(findings)
    print(f"Wrote findings to {FINDINGS_PATH}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
