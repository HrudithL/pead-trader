"""Event-study statistics: summary stats, quantile sort + monotonicity, and
Spearman IC -- Phase I, Slice 9 ("Event-study statistics") of
memory/phase1-equity-pead-plan.md.

Reads data/events/equity_events_1996_2013.parquet (built by
scripts/build_equity_events.py, read-only input -- see that script's own
FINDINGS.md for how the event table's identifiers, day-0 alignment, and
forward returns were constructed) and computes the project's first real
statistical test of post-earnings-announcement drift (PEAD): is there a
systematic, surprise-conditional drift in returns after an earnings
announcement.

Key decisions:

1. Restriction. Every statistic here is computed on `day0_status == "ok"`
   rows only (a real day 0 exists) -- pushed down at the DuckDB read so the
   full 32-column table is never materialized wider or taller than needed.
   Within that set, each PER-HORIZON statistic is further restricted to
   rows where THAT horizon's own return column (raw or market-adjusted) is
   non-null -- coverage differs slightly by horizon (see
   data/events/FINDINGS.md's "Forward-return coverage by horizon" table),
   so there is no single universal row count shared across all six
   horizons. "Tag, don't filter" from the event table does not change here:
   nothing is dropped for any reason other than "this specific number is
   not computable from a null input."
2. Rank-normalizing the surprise measure. `sue_primary` pools `sue_analyst`
   and `sue_seasonal`, which data/earnings/FINDINGS.md and this project's
   own review history establish are on different scales -- the analyst
   measure's sample standard deviation is roughly 2.7x the seasonal
   measure's (computed at runtime below, not asserted). Sorting or
   correlating raw `sue_primary` across both sources would let whichever
   source has fatter tails dominate the ranking. `sue_rank_pct` fixes this
   by construction: the percentile rank (0 to 1) of `sue_primary` (rounded
   to 8 decimals before ranking -- see below), computed SEPARATELY within
   each (`sue_source`, announcement calendar quarter) group, so each
   source contributes its own 0-1 rank distribution regardless of its raw
   scale, AND no event's rank depends on the surprise distribution of a
   later period -- an event announced in 2004Q1 only ranks against other
   2004Q1 events from the same source, never against 2013 events. Ranking
   against the full 1996-2013 sample (an earlier version of this function)
   was a real look-ahead bug: a 1996 event's quintile depended on data from
   17 years later. Rounding to 8 decimals before ranking also fixes a
   separate real defect -- `sue_analyst` has a large exact-tie block (over
   11% of analyst-sourced events, all economically "0.0" surprise) that
   differed only at the ~1e-13 floating-point-noise level, which `rank()`
   was silently using as a real ordering; rounding makes those ties honest
   (see FINDINGS.md for the measured counts). Both the quantile sort and
   the Spearman IC use `sue_rank_pct`, never raw `sue_primary`, for
   anything that pools across sources.
3. Quantile sort. Quintiles (5 groups, `pd.qcut` on `sue_rank_pct`) computed
   PER HORIZON, on that horizon's own valid subset (non-null
   `sue_rank_pct` AND non-null return column) -- not one global cut reused
   across horizons with different missingness. Market-adjusted return is
   the primary metric per the plan; raw is also reported for completeness.
   Monotonicity is reported two ways and never smoothed into a single
   verdict: a non-decreasing check (is the quintile-mean sequence
   non-decreasing, ties allowed, from Q1, most negative surprise, to Q5,
   most positive -- NOT a claim of strict/ties-free monotonicity) and a
   softer, purely descriptive Spearman rank correlation between quintile
   index (1-5) and quintile mean return (not a significance test -- 5
   quintile means are not 5 independent draws, so no p-value is reported
   for it; the real inferential test is the pooled Spearman IC below). A
   Q5-Q1 spread statistic (the actual headline PEAD test) is also reported
   per horizon, using the achieved lowest/highest quantile codes and a
   Welch-style two-group comparison (see `quantile_spread_stats`).
4. Spearman IC. The pooled, firm-level (not quantile-level) Spearman rank
   correlation between `sue_rank_pct` and the forward return, per horizon,
   for both raw and market-adjusted returns -- `scipy.stats.spearmanr`,
   which supplies both the correlation and its p-value. This p-value has a
   real per-event n (on the order of 235k) and is statistically valid --
   unlike the descriptive quintile-mean Spearman rho in Decision 3, this is
   the slice's real significance test of PEAD.
5. No clustering, no bootstrap, no multiple-testing correction. Standard
   errors here are plain i.i.d. (`sd / sqrt(n)`), even though earnings
   cluster heavily in fiscal-quarter "seasons" -- a known limitation this
   slice's scope does not address; see FINDINGS.md and
   memory/phase1-full-equity-pead-plan.md for the later, more rigorous
   phase that does. Duplicate firm-days (`n_events_same_permno_day0`) and
   contested permnos (`permno_contested`) are tagged, not filtered or
   deduped, matching the event table's own convention -- their rates are
   reported in FINDINGS.md as a disclosed caveat on the independence
   assumption behind every standard error here, not corrected for.

Usage:
    python scripts/build_event_study_stats.py
Reads data/events/equity_events_1996_2013.parquet (read-only). Writes
data/results/event_study_stats.json and data/results/FINDINGS.md.
"""

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats as sps

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = REPO_ROOT / "data" / "events" / "equity_events_1996_2013.parquet"
OUTPUT_DIR = REPO_ROOT / "data" / "results"
OUTPUT_PATH = OUTPUT_DIR / "event_study_stats.json"
FINDINGS_PATH = OUTPUT_DIR / "FINDINGS.md"

HORIZONS = (1, 5, 10, 20, 40, 60)
# (label, column-name suffix). "mktadj" is the primary metric for the
# quantile sort and IC per the plan; "raw" is reported everywhere too, for
# completeness -- see module docstring Decision 3.
RETURN_KINDS = (("raw", ""), ("mktadj", "_mktadj"))
N_QUANTILES = 5
CONFIDENCE = 0.95

EVENT_LOAD_COLUMNS = ["sue_primary", "sue_source", "anndats", "permno_contested", "n_events_same_permno_day0"] + [
    f"ret_fwd_{h}d{suffix}" for h in HORIZONS for _, suffix in RETURN_KINDS
]


def return_col(horizon: int, suffix: str) -> str:
    return f"ret_fwd_{horizon}d{suffix}"


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------


def load_day0_ok_events(path=EVENTS_PATH) -> pd.DataFrame:
    """Reads only the columns this module needs, restricted to
    `day0_status == "ok"` at the DuckDB read (projection AND predicate
    pushdown -- the 32-column, 268k-row table is never materialized wider
    or with more rows than every downstream statistic here actually uses).
    """
    col_list = ", ".join(EVENT_LOAD_COLUMNS)
    query = f"select {col_list} from read_parquet(?) where day0_status = 'ok'"
    with duckdb.connect() as con:
        return con.execute(query, [str(path)]).df()


# ---------------------------------------------------------------------------
# Decision 2: rank-normalizing the surprise measure.
# ---------------------------------------------------------------------------


def compute_sue_rank_pct(
    df: pd.DataFrame,
    sue_col: str = "sue_primary",
    source_col: str = "sue_source",
    anndats_col: str = "anndats",
) -> pd.Series:
    """Percentile rank (0 to 1) of `sue_col`, ROUNDED to 8 decimals, computed
    separately within each (`source_col`, announcement-calendar-quarter)
    group -- see module docstring Decision 2. Two real bugs fixed at once:

    (a) Look-ahead. Ranking `sue_col` against the FULL sample (an earlier
        version of this function) meant a 1996 event's quintile depended on
        the surprise distribution of 2013 events -- data that did not exist
        yet as of the 1996 event. Grouping by announcement quarter (derived
        from `anndats_col` via `.dt.to_period("Q")`, kept as a string so the
        intermediate stays JSON-safe) means an event only ranks against
        other events announced in the SAME calendar quarter, from the same
        `source_col`. This is not a claim of a strictly real-time-
        implementable cutoff -- the ranking still uses that whole quarter's
        cross-section (up to ~3 months of same-quarter data), which is the
        standard PEAD construction, not a point-in-time-as-of-announcement
        one -- but it removes the multi-year look-ahead that full-sample
        ranking introduced.
    (b) Float-noise ties. `sue_analyst` has a large block of events that are
        the same "0.0" surprise economically but differ at the ~1e-13
        floating-point level -- ranking the raw, unrounded value orders that
        block by noise rather than treating it as a real tie. Rounding to 8
        decimals before ranking (`sue_col.round(8)`) makes those ties honest
        (`rank(pct=True)`'s default tie-handling averages tied ranks). 8
        decimals was chosen empirically: representation noise at this
        data's scale is far below 1e-8, the smallest real gap between
        distinct surprise values is far above it, and round(8)/round(10)/
        round(12) all give identical results while round(4) would destroy
        real distinct values -- see FINDINGS.md.

    A row whose `sue_col` is null gets a null rank (pandas' default `rank`
    behavior, `na_option="keep"`), which is correct: there is nothing to
    rank. A (`source_col`, quarter) group that is entirely null (e.g.
    `sue_source == "none"`, where `sue_primary` is null by construction)
    ranks to all-null, not an error -- such rows simply never enter the
    quantile sort or IC, which both require a non-null `sue_rank_pct`.

    No minimum-cell-size guard is applied to small (source, quarter) groups
    -- deliberately: see FINDINGS.md for the measured cell-size
    distribution, which shows this is not worth the added complexity for
    this data.
    """
    period = df[anndats_col].dt.to_period("Q").astype(str)
    return df[sue_col].round(8).groupby([df[source_col], period]).rank(pct=True)


# ---------------------------------------------------------------------------
# Decision 1: per-horizon summary statistics.
# ---------------------------------------------------------------------------


def summary_stats(x: np.ndarray, confidence: float = CONFIDENCE) -> dict:
    """n, mean, median, sample sd (ddof=1), se, t-stat for H0: mean=0, and a
    two-sided CI at `confidence` using the t-distribution (df = n-1) -- the
    more defensible finite-sample default over a normal approximation, per
    the plan. `x` must already contain no NaNs; the caller is responsible
    for restricting to that specific column's own non-null rows first (see
    module docstring Decision 1) -- this function is a pure numeric
    computation, independently testable against hand-computable values.

    sd/se/t_stat/ci are None when undefined: n < 2 (sample variance has no
    meaning at n=1) or se == 0 (a real 0/0, not a meaningful t-stat) --
    mirrors scripts/build_equity_events.py's welch_group_stats guard.
    """
    n = int(len(x))
    result = {
        "n": n,
        "mean": float(np.mean(x)) if n > 0 else None,
        "median": float(np.median(x)) if n > 0 else None,
        "sd": None,
        "se": None,
        "t_stat": None,
        "ci_low": None,
        "ci_high": None,
        "confidence": confidence,
    }
    if n < 2:
        return result
    sd = float(np.std(x, ddof=1))
    se = sd / np.sqrt(n)
    result["sd"] = sd
    result["se"] = se
    if se > 0:
        t_stat = result["mean"] / se
        t_crit = float(sps.t.ppf(0.5 + confidence / 2, df=n - 1))
        result["t_stat"] = t_stat
        result["ci_low"] = result["mean"] - t_crit * se
        result["ci_high"] = result["mean"] + t_crit * se
    return result


def build_summary_stats_table(df: pd.DataFrame) -> list[dict]:
    """One record per (horizon, return_type) -- see summary_stats()."""
    records = []
    for h in HORIZONS:
        for kind, suffix in RETURN_KINDS:
            col = return_col(h, suffix)
            x = df[col].dropna().to_numpy(dtype="float64")
            record = {"horizon": h, "return_type": kind, "column": col}
            record.update(summary_stats(x))
            records.append(record)
    return records


# ---------------------------------------------------------------------------
# Decision 3: quantile sort + monotonicity.
# ---------------------------------------------------------------------------


def monotonicity_check(quintile_means: list[float]) -> dict:
    """Non-decreasing check (Q1 -> Qk, ties allowed -- NOT a strict/
    ties-free monotonicity claim; the `strict_monotonic` key name predates
    this clarification and is kept as-is) and a softer Spearman correlation
    between quintile index (1..k) and quintile mean return. Both are
    reported plainly -- this project's convention is to disclose the real
    result rather than assert a clean monotonic story the data doesn't
    show (see module docstring Decision 3).

    `spearman_rho` is descriptive only, not a significance test: there are
    only k (typically 5) quintile means, and they are not independent
    draws, so no p-value is computed or reported for this correlation (an
    earlier version of this function did compute one via
    `scipy.stats.spearmanr`'s asymptotic p-value, which is meaningless at
    n=5 -- it returned ~1e-24 for a perfect correlation, when the true
    minimum achievable EXACT p-value at n=5 is 2/120 ~ 0.017). The real
    inferential significance test for PEAD in this slice is the pooled
    Spearman IC (`spearman_ic` / `build_spearman_ic_table`), computed on
    per-event data with a valid sample size.

    `spearman_rho` is None when fewer than 2 quintiles exist (a correlation
    needs at least 2 points).
    """
    k = len(quintile_means)
    strict_monotonic = all(quintile_means[i] <= quintile_means[i + 1] for i in range(k - 1))
    rho = None
    if k >= 2:
        rho_raw, _ = sps.spearmanr(np.arange(1, k + 1), quintile_means)
        rho = None if not np.isfinite(rho_raw) else float(rho_raw)
    return {
        "n_quantiles": k,
        "quintile_means": [float(m) for m in quintile_means],
        "strict_monotonic": bool(strict_monotonic),
        "spearman_rho": rho,
    }


def quantile_spread_stats(x_low: np.ndarray, x_high: np.ndarray) -> dict:
    """Welch-style two-group comparison for the Q5-Q1 (highest quantile
    minus lowest quantile) spread -- the actual headline PEAD test: is the
    most-positive-surprise group's mean return higher than the
    most-negative-surprise group's. Matches the formula in
    `scripts/build_equity_events.py`'s `welch_group_stats` (this file does
    not import that one -- it is a separate module's read-only-input
    convention, so this is a small local reimplementation of the same
    formula, not a duplicate dependency): `spread = mean_high - mean_low`,
    `se = sqrt(var_high/n_high + var_low/n_low)` with sample variance
    (`ddof=1`), `t_stat = spread / se`. No p-value is reported, matching
    this file's own `summary_stats` convention of t-stat + CI without a
    p-value (this function reports t-stat only, no CI, since there is no
    natural single mean to build a CI around for a *difference*).

    `se`/`t_stat` are None when either group has n < 2 (sample variance
    undefined) or `se == 0` (a real 0/0, not a meaningful t-stat).
    """
    n_low, n_high = int(len(x_low)), int(len(x_high))
    mean_low = float(np.mean(x_low)) if n_low > 0 else None
    mean_high = float(np.mean(x_high)) if n_high > 0 else None
    spread = mean_high - mean_low if (mean_low is not None and mean_high is not None) else None
    se = None
    t_stat = None
    if n_low >= 2 and n_high >= 2:
        var_low = float(np.var(x_low, ddof=1))
        var_high = float(np.var(x_high, ddof=1))
        se_val = float(np.sqrt(var_high / n_high + var_low / n_low))
        if se_val > 0:
            se = se_val
            t_stat = spread / se
    return {
        "n_low": n_low,
        "n_high": n_high,
        "mean_low": mean_low,
        "mean_high": mean_high,
        "spread": spread,
        "se": se,
        "t_stat": t_stat,
    }


def quantile_sort(
    df: pd.DataFrame, rank_col: str, value_col: str, n_quantiles: int = N_QUANTILES
) -> tuple[list[dict], dict, dict]:
    """Sorts `value_col`'s own valid subset (non-null `rank_col` AND
    non-null `value_col`) into `n_quantiles` groups by `rank_col`, Q1 =
    lowest rank (most negative surprise) to Qk = highest (most positive) --
    `pd.qcut(..., labels=False)` returns codes in that same increasing
    order. `duplicates="drop"` guards against `rank_col` having enough tied
    values at a bin edge to collapse two bins into one (dormant in this
    project's real data -- checked directly: it still yields 5 distinct
    quintiles at every horizon -- but a defensive default rather than a
    crash if a much smaller or more tied sample is ever passed in); the
    achieved quantile count is reported via monotonicity_check's
    `n_quantiles` rather than assumed to always equal the requested count.

    Also computes the Q5-Q1 (achieved-lowest-vs-achieved-highest, NOT
    hardcoded quantile codes 1/5 -- if `duplicates="drop"` ever collapsed a
    bin, the achieved codes might not span 1..5) spread via
    `quantile_spread_stats`, the headline PEAD test for this (horizon,
    return_type).

    Returns (per-quintile records, monotonicity dict, spread dict).
    """
    subset = df.dropna(subset=[rank_col, value_col])
    codes = pd.qcut(subset[rank_col], n_quantiles, labels=False, duplicates="drop")
    quintile = codes.to_numpy() + 1

    records = []
    means = []
    groups = {}
    for q in sorted(pd.unique(quintile)):
        x = subset.loc[quintile == q, value_col].to_numpy(dtype="float64")
        groups[int(q)] = x
        n = len(x)
        mean = float(np.mean(x))
        record = {
            "quintile": int(q),
            "n": n,
            "mean": mean,
            "median": float(np.median(x)),
            "se": float(np.std(x, ddof=1) / np.sqrt(n)) if n >= 2 else None,
        }
        records.append(record)
        means.append(mean)

    achieved_codes = sorted(groups)
    q_low, q_high = achieved_codes[0], achieved_codes[-1]
    spread = {"quantile_low": q_low, "quantile_high": q_high}
    spread.update(quantile_spread_stats(groups[q_low], groups[q_high]))
    return records, monotonicity_check(means), spread


def build_quantile_sort_results(df: pd.DataFrame, rank_col: str = "sue_rank_pct") -> dict:
    """{"quantile_sort": [...], "monotonicity": [...], "quantile_spread": [...]}
    across every (horizon, return_type) -- see quantile_sort() for the
    per-horizon, per-return-type restriction."""
    quantile_records, monotonicity_records, spread_records = [], [], []
    for h in HORIZONS:
        for kind, suffix in RETURN_KINDS:
            col = return_col(h, suffix)
            per_quintile, mono, spread = quantile_sort(df, rank_col, col, N_QUANTILES)
            for rec in per_quintile:
                quantile_records.append({"horizon": h, "return_type": kind, **rec})
            monotonicity_records.append({"horizon": h, "return_type": kind, **mono})
            spread_records.append({"horizon": h, "return_type": kind, **spread})
    return {
        "quantile_sort": quantile_records,
        "monotonicity": monotonicity_records,
        "quantile_spread": spread_records,
    }


# ---------------------------------------------------------------------------
# Decision 4: Spearman IC.
# ---------------------------------------------------------------------------


def spearman_ic(x: np.ndarray, y: np.ndarray) -> dict:
    """Spearman rank correlation (IC) and its p-value between two equal-
    length, already-non-null arrays -- a pure numeric wrapper around
    `scipy.stats.spearmanr`, independently testable against a hand/scipy
    cross-checked value. `n < 2` has no defined correlation."""
    n = int(len(x))
    if n < 2:
        return {"n": n, "ic": None, "p_value": None}
    rho, p = sps.spearmanr(x, y)
    ic = None if not np.isfinite(rho) else float(rho)
    p_value = None if (not np.isfinite(p) or ic is None) else float(p)
    return {"n": n, "ic": ic, "p_value": p_value}


def build_spearman_ic_table(df: pd.DataFrame, rank_col: str = "sue_rank_pct") -> list[dict]:
    """One record per (horizon, return_type): the pooled, firm-level
    Spearman IC between `rank_col` and that horizon's return column, on
    that column's own valid (non-null rank AND non-null return) subset."""
    records = []
    for h in HORIZONS:
        for kind, suffix in RETURN_KINDS:
            col = return_col(h, suffix)
            subset = df.dropna(subset=[rank_col, col])
            record = {"horizon": h, "return_type": kind, "column": col}
            record.update(spearman_ic(subset[rank_col].to_numpy(), subset[col].to_numpy(dtype="float64")))
            records.append(record)
    return records


# ---------------------------------------------------------------------------
# Assembly + caveat stats.
# ---------------------------------------------------------------------------


def _rank_cell_stats(df: pd.DataFrame, sue_col: str = "sue_primary", source_col: str = "sue_source") -> dict:
    """Runtime-computed disclosure stats for the (source, announcement-
    quarter) ranking groups used by `compute_sue_rank_pct` -- see fix 1-2's
    FINDINGS.md disclosure requirement. Restricted to rows with a non-null
    `sue_col` (a `source_col` group like "none" contributes no real ranking
    cells since it is all-null). Also finds the single largest exact-tie
    block (on the 8-decimal-rounded value, i.e. the value actually used for
    ranking) across all sources, and what fraction of THAT source's
    non-null population it represents -- this is what fix 1-2's rounding
    makes tie honestly instead of being ordered by float noise.
    """
    available = df[df[source_col] != "none"].copy()
    period = available["anndats"].dt.to_period("Q").astype(str)
    cell_sizes = available.groupby([available[source_col], period]).size()

    rounded = available[sue_col].round(8)
    largest_block = {"source": None, "value": None, "count": 0, "fraction_of_source": None}
    for source, g in available.groupby(source_col):
        rounded_g = rounded.loc[g.index]
        counts = rounded_g.value_counts()  # NaN dropped by default
        if len(counts) == 0:
            continue
        non_null_g = int(rounded_g.notna().sum())
        top_value, top_count = counts.index[0], int(counts.iloc[0])
        if top_count > largest_block["count"]:
            largest_block = {
                "source": str(source),
                "value": float(top_value),
                "count": top_count,
                "fraction_of_source": top_count / non_null_g,
            }

    return {
        "n_cells": int(len(cell_sizes)),
        "median_cell_size": float(cell_sizes.median()),
        "min_cell_size": int(cell_sizes.min()),
        "largest_tied_value_block": largest_block,
    }


def build_results(df: pd.DataFrame) -> dict:
    """Pure function of an already day0_status=="ok"-restricted dataframe
    (see load_day0_ok_events). Adds `sue_rank_pct` and computes every
    statistic plus the caveat counts FINDINGS.md discloses."""
    if len(df) == 0:
        raise ValueError("build_results: empty (day0_status=='ok') input -- nothing to compute")
    df = df.copy()
    df["sue_rank_pct"] = compute_sue_rank_pct(df)

    sue_scale_by_source = {
        str(source): {"n": int(g["sue_primary"].notna().sum()), "sd": float(g["sue_primary"].std(ddof=1))}
        for source, g in df.groupby("sue_source")
        if g["sue_primary"].notna().sum() >= 2
    }

    n_day0_ok = len(df)
    n_contested = int(df["permno_contested"].sum())
    dup_size = df["n_events_same_permno_day0"]
    n_duplicate_rows = int((dup_size > 1).sum())

    # Fix 5: disclose sue_source composition and the summary-stats-vs-
    # quantile-sort/IC row-count gap -- the latter two run only on the
    # subset with a non-null surprise measure (sue_source != "none"),
    # while summary_stats runs on the full day0_ok population.
    n_by_sue_source = {str(k): int(v) for k, v in df["sue_source"].value_counts().items()}
    n_sue_available = int(df["sue_rank_pct"].notna().sum())

    quantile_results = build_quantile_sort_results(df)

    return {
        "meta": {
            "n_day0_ok": n_day0_ok,
            "horizons": list(HORIZONS),
            "n_quantiles_requested": N_QUANTILES,
            "confidence": CONFIDENCE,
            "sue_scale_by_source": sue_scale_by_source,
            "n_permno_contested": n_contested,
            "n_duplicate_firm_day_rows": n_duplicate_rows,
            "n_by_sue_source": n_by_sue_source,
            "n_sue_available": n_sue_available,
            "rank_cell_stats": _rank_cell_stats(df),
        },
        "summary_stats": build_summary_stats_table(df),
        "quantile_sort": quantile_results["quantile_sort"],
        "monotonicity": quantile_results["monotonicity"],
        "quantile_spread": quantile_results["quantile_spread"],
        "spearman_ic": build_spearman_ic_table(df),
    }


# ---------------------------------------------------------------------------
# Findings.
# ---------------------------------------------------------------------------


def _fmt_pct(x, digits=2):
    return "n/a" if x is None else f"{x:.{digits}%}"


def _fmt_num(x, digits=4):
    return "n/a" if x is None else f"{x:.{digits}f}"


def _fmt_ci(low, high, digits=2):
    if low is None or high is None:
        return "n/a"
    return f"[{low:.{digits}%}, {high:.{digits}%}]"


def render_findings(results: dict) -> str:
    meta = results["meta"]
    n = meta["n_day0_ok"]
    lines = []
    lines.append("# Event-study statistics: summary stats, quantile sort, Spearman IC\n")
    n_available = meta["n_sue_available"]
    lines.append(
        f"Computed on the {n:,} `day0_status == \"ok\"` rows of "
        "`data/events/equity_events_1996_2013.parquet` (a real day 0 exists for every row used "
        "here). Each per-horizon statistic below is further restricted to that horizon's own "
        "non-null return column -- coverage differs slightly by horizon (see "
        "`data/events/FINDINGS.md`'s forward-return coverage table), so there is no single row "
        "count shared across all six horizons. This is the project's first statistical test of "
        "whether post-earnings-announcement drift (PEAD) shows up in this data.\n"
    )
    by_source = meta["n_by_sue_source"]
    by_source_str = ", ".join(f"`{k}`: {v:,}" for k, v in by_source.items())
    lines.append(
        f"**Population note.** The {n:,}-row `day0_status == \"ok\"` population breaks down by "
        f"`sue_source` as {by_source_str}. The per-horizon summary statistics table below runs on "
        f"this full population (restricted only to that horizon's own non-null return). The "
        f"quantile sort and Spearman IC further below run on the {n_available:,}-row subset with a "
        f"non-null surprise measure (`sue_source != \"none\"`, i.e. `sue_rank_pct` is non-null) -- "
        f"that is why a given horizon's summary-stat n and its quantile-sort/IC n differ.\n"
    )

    lines.append("## Rank-normalizing the surprise measure\n")
    scale = meta["sue_scale_by_source"]
    analyst = scale.get("analyst")
    seasonal = scale.get("seasonal")
    scale_sentence = ""
    if analyst and seasonal and seasonal["sd"] > 0:
        ratio = analyst["sd"] / seasonal["sd"]
        scale_sentence = (
            f" Measured here: `sue_analyst` (n={analyst['n']:,}) has sample sd {analyst['sd']:.3f}; "
            f"`sue_seasonal` (n={seasonal['n']:,}) has sample sd {seasonal['sd']:.3f} -- a "
            f"{ratio:.2f}x difference in dispersion, not a small rounding gap."
        )
    lines.append(
        "`sue_primary` pools `sue_analyst` and `sue_seasonal`, which "
        "`data/earnings/FINDINGS.md` and this project's own review history establish are on "
        "different scales.{} Sorting or correlating raw `sue_primary` across both sources would "
        "let whichever source has fatter tails dominate any pooled ranking or quantile cut -- a "
        "seasonal-sourced event with a modest raw surprise could land in the same raw-magnitude "
        "bucket as an analyst-sourced event with a much more extreme one, purely because of scale, "
        "not because the two are economically comparable. `sue_rank_pct` fixes this by "
        "construction: the percentile rank (0 to 1) of `sue_primary`, computed separately within "
        "each (`sue_source`, announcement calendar quarter) group, so each source contributes its "
        "own 0-1 rank distribution regardless of its raw scale, AND no event's rank depends on the "
        "surprise distribution of a later period -- an event announced in, say, 2004Q1 only ranks "
        "against other 2004Q1 events from the same source, never against events from 2013. The "
        "quantile sort and the Spearman IC below both use `sue_rank_pct`, never raw `sue_primary`, "
        "for anything that pools across sources.\n".format(scale_sentence)
    )
    cells = meta["rank_cell_stats"]
    lines.append(
        f"An earlier version of this rank ranked `sue_primary` against the FULL 1996-2013 sample "
        f"within each `sue_source` -- a real look-ahead bug, since a 1996 event's quintile then "
        f"depended on the surprise distribution of 2013 events. Grouping by announcement quarter "
        f"fixes that: each of the {cells['n_cells']:,} (`sue_source`, quarter) cells used for "
        f"ranking has a median of {cells['median_cell_size']:,.0f} rows, smallest {cells['min_cell_size']:,} "
        f"-- no minimum-cell-size guard is applied (see the module docstring for why: at this data's "
        f"scale, cells below 100 rows are a small enough share that the added complexity of a "
        f"guard isn't worth it).\n"
    )
    block = cells["largest_tied_value_block"]
    if block["source"] is not None:
        lines.append(
            f"Ranking now also rounds `sue_primary` to 8 decimals before ranking, so exact ties "
            f"(down to floating-point noise at the ~1e-13 level) rank as real ties instead of being "
            f"ordered by that noise. The largest such tied-value block is {block['count']:,} "
            f"`{block['source']}`-sourced events all at surprise {block['value']:.4f} -- "
            f"{block['fraction_of_source']:.1%} of that source's non-null events -- which the "
            f"unrounded rank used to order by noise alone.\n"
        )

    lines.append("## Per-horizon summary statistics\n")
    lines.append(
        "Mean/median/t-stat/95% CI (t-distribution, df = n-1) for the null that the mean return "
        "is zero, for both raw and market-adjusted (abnormal) forward returns. Standard errors "
        "here are plain i.i.d. (`sd / sqrt(n)`) -- see Caveats below; they are not clustered by "
        "firm or announcement date, so they understate the true uncertainty to the extent nearby "
        "events are correlated.\n"
    )
    lines.append("| horizon | return type | n | mean | median | sd | se | t-stat | 95% CI |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for rec in results["summary_stats"]:
        lines.append(
            f"| +{rec['horizon']}d | {rec['return_type']} | {rec['n']:,} | {_fmt_pct(rec['mean'])} | "
            f"{_fmt_pct(rec['median'])} | {_fmt_pct(rec['sd'])} | {_fmt_pct(rec['se'])} | "
            f"{_fmt_num(rec['t_stat'], 2)} | {_fmt_ci(rec['ci_low'], rec['ci_high'])} |"
        )
    lines.append("")

    lines.append("## Quantile sort + monotonicity\n")
    lines.append(
        "Quintiles of `sue_rank_pct` (pooled across `sue_source`, since it is now a comparable "
        "0-1 rank), cut separately within each horizon's own valid subset -- not one global "
        "assignment reused across horizons with different missingness. Q1 = most negative "
        "surprise, Q5 = most positive. Monotonicity is reported two ways, both stated plainly per "
        "horizon rather than smoothed into one verdict: a non-decreasing check (does the "
        "quintile-mean sequence go up or stay flat, ties allowed, from Q1 to Q5 -- not a claim "
        "that it is strictly increasing) and a softer Spearman correlation between quintile index "
        "and quintile mean return. That Spearman rho is descriptive only, not a significance test "
        "-- there are only 5 quintile means, and they are not 5 independent draws, so no p-value "
        "is reported next to it. The real inferential significance test for PEAD in this slice is "
        "the pooled Spearman IC further below, computed on per-event data with a valid sample "
        "size.\n"
    )
    mono_by_key = {(m["horizon"], m["return_type"]): m for m in results["monotonicity"]}
    quantile_by_key = {}
    for rec in results["quantile_sort"]:
        quantile_by_key.setdefault((rec["horizon"], rec["return_type"]), []).append(rec)

    for h in HORIZONS:
        lines.append(f"### +{h}d\n")
        lines.append("| return type | quintile | n | mean | median | se |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for kind, _ in RETURN_KINDS:
            for rec in quantile_by_key.get((h, kind), []):
                lines.append(
                    f"| {kind} | Q{rec['quintile']} | {rec['n']:,} | {_fmt_pct(rec['mean'])} | "
                    f"{_fmt_pct(rec['median'])} | {_fmt_pct(rec['se'])} |"
                )
        mono_lines = []
        for kind, _ in RETURN_KINDS:
            mono = mono_by_key.get((h, kind))
            if mono is None:
                continue
            verdict = "non-decreasing" if mono["strict_monotonic"] else "NOT non-decreasing"
            mono_lines.append(
                f"{kind}: Q1..Q{mono['n_quantiles']} is **{verdict}** (means "
                f"{', '.join(_fmt_pct(m) for m in mono['quintile_means'])}); Spearman(quintile "
                f"index, quintile mean) = {_fmt_num(mono['spearman_rho'], 3)} (descriptive only, "
                "no p-value -- see preamble above)."
            )
        lines.append("")
        lines.append(" ".join(mono_lines) + "\n")

    lines.append("## Quantile spread (Q5-Q1): the headline PEAD test\n")
    lines.append(
        "The Welch-style two-group spread between the achieved highest and lowest quantile codes "
        "from the sort above (not hardcoded quantile labels \"1\"/\"5\" -- `duplicates=\"drop\"` "
        "could in principle collapse a bin, though it does not in this data) -- `spread = "
        "mean_high - mean_low`, `se = sqrt(var_high/n_high + var_low/n_low)` (sample variance, "
        "ddof=1), `t_stat = spread / se`. No p-value is reported, matching this file's own "
        "`summary_stats` convention of t-stat without a p-value.\n"
    )
    spread_by_key = {(r["horizon"], r["return_type"]): r for r in results["quantile_spread"]}
    lines.append("| horizon | return type | low..high | n low | n high | mean low | mean high | spread | se | t-stat |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for h in HORIZONS:
        for kind, _ in RETURN_KINDS:
            r = spread_by_key.get((h, kind))
            if r is None:
                continue
            lines.append(
                f"| +{h}d | {kind} | Q{r['quantile_low']}..Q{r['quantile_high']} | {r['n_low']:,} | "
                f"{r['n_high']:,} | {_fmt_pct(r['mean_low'])} | {_fmt_pct(r['mean_high'])} | "
                f"{_fmt_pct(r['spread'])} | {_fmt_pct(r['se'])} | {_fmt_num(r['t_stat'], 2)} |"
            )
    lines.append("")

    lines.append("## Spearman IC\n")
    lines.append(
        "Pooled, firm-level (not quantile-level) Spearman rank correlation between "
        "`sue_rank_pct` and the forward return, per horizon.\n"
    )
    lines.append("| horizon | return type | n | IC (rho) | p-value |")
    lines.append("| --- | --- | --- | --- | --- |")
    for rec in results["spearman_ic"]:
        lines.append(
            f"| +{rec['horizon']}d | {rec['return_type']} | {rec['n']:,} | "
            f"{_fmt_num(rec['ic'], 4)} | {_fmt_num(rec['p_value'], 4)} |"
        )
    lines.append("")

    n_contested = meta["n_permno_contested"]
    n_dup = meta["n_duplicate_firm_day_rows"]
    lines.append("## Caveats\n")
    lines.append(
        "**No clustering.** Every standard error, t-stat, and CI above is plain i.i.d. "
        "(`sd / sqrt(n)`), not clustered by firm or by announcement date, even though earnings "
        "cluster heavily in fiscal-quarter \"seasons\" -- a known limitation this slice's scope "
        "does not address. A later, more rigorous phase "
        "(`memory/phase1-full-equity-pead-plan.md`) handles clustered/bootstrapped inference; "
        "these numbers should be read as a first look, not a final significance verdict.\n"
    )
    lines.append(
        "**Attrition/survivorship.** `data/events/FINDINGS.md` already discloses that longer "
        "horizons condition on panel survival, and that this conditioning is somewhat "
        "non-random (delisting-correlated). These statistics inherit that same conditioning, and "
        "it is worse at longer horizons -- the summary-statistics table above shows n falling as "
        "horizon lengthens, which is exactly that attrition showing up here, not re-derived "
        "separately.\n"
    )
    lines.append(
        f"**Duplicate firm-days and contested permnos.** {n_dup:,} of the {n:,} rows used here "
        f"({n_dup / n:.1%}) share a `(permno, day0_date)` pair with at least one other row (tagged "
        f"via `n_events_same_permno_day0`), and {n_contested:,} ({n_contested / n:.1%}) carry a "
        "contested permno (`permno_contested`) -- neither is deduped or filtered here, matching "
        "the event table's tag-don't-filter convention, but both mean a handful of observations "
        "are not strictly independent of each other, which the i.i.d. standard errors above do "
        "not account for.\n"
    )
    lines.append(
        "**Look-ahead.** `sue_rank_pct` is computed within each (`sue_source`, announcement "
        "calendar quarter) group (see \"Rank-normalizing the surprise measure\" above), so no "
        "event's rank -- and therefore no quintile assignment or IC contribution -- depends on the "
        "surprise distribution of a later period. An earlier version of this rank pooled the FULL "
        "1996-2013 sample within each `sue_source`, which WAS a real look-ahead bug (a 1996 "
        "event's quintile depended on 2013 data); that version has been replaced, not patched "
        "around. The residual, honestly stated: ranking still uses the whole announcement "
        "quarter's cross-section (up to ~3 months of same-quarter data, including events "
        "announced after the event being ranked), which is the standard PEAD construction, not a "
        "strictly real-time-as-of-announcement cutoff -- this is a known, disclosed simplification, "
        "not a claim of a live-tradable ranking. Separately: every restriction above is a non-null "
        "check on the specific column being summarized (per Decision 1) -- no row is dropped based "
        "on the VALUE of a non-null return. Rows whose return IS null are dropped from that "
        "horizon's statistics, and that missingness is not random: it is exactly the "
        "delisting-correlated attrition the paragraph above already concedes (attrited events "
        "have a significantly different average surprise than retained ones, per "
        "`data/events/FINDINGS.md`). These two caveats describe the same underlying fact from two "
        "angles and are not in tension with each other.\n"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _to_jsonable(obj):
    """Recursively converts numpy scalar types to native Python types so
    `json.dump` doesn't choke on them -- summary_stats/spearman_ic/etc.
    already cast to float/int, but pandas group keys (e.g. numpy.int64 from
    a groupby) can still leak through untouched in a few spots."""
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def main():
    import json

    events = load_day0_ok_events(EVENTS_PATH)
    results = build_results(events)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(_to_jsonable(results), indent=2))
    print(f"Wrote results to {OUTPUT_PATH}", flush=True)

    findings = render_findings(results)
    FINDINGS_PATH.write_text(findings)
    print(f"Wrote findings to {FINDINGS_PATH}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
