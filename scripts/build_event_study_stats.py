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
   measure's population standard deviation is roughly 2.7x the seasonal
   measure's (computed at runtime below, not asserted). Sorting or
   correlating raw `sue_primary` across both sources would let whichever
   source has fatter tails dominate the ranking. `sue_rank_pct` fixes this
   by construction: the percentile rank (0 to 1) of `sue_primary`, computed
   SEPARATELY within each `sue_source` group, so each source contributes
   its own 0-1 rank distribution regardless of its raw scale. Both the
   quantile sort and the Spearman IC use `sue_rank_pct`, never raw
   `sue_primary`, for anything that pools across sources.
3. Quantile sort. Quintiles (5 groups, `pd.qcut` on `sue_rank_pct`) computed
   PER HORIZON, on that horizon's own valid subset (non-null
   `sue_rank_pct` AND non-null return column) -- not one global cut reused
   across horizons with different missingness. Market-adjusted return is
   the primary metric per the plan; raw is also reported for completeness.
   Monotonicity is reported two ways and never smoothed into a single
   verdict: a strict check (is the quintile-mean sequence non-decreasing
   from Q1, most negative surprise, to Q5, most positive) and a softer
   Spearman rank correlation between quintile index (1-5) and quintile mean
   return.
4. Spearman IC. The pooled, firm-level (not quantile-level) Spearman rank
   correlation between `sue_rank_pct` and the forward return, per horizon,
   for both raw and market-adjusted returns -- `scipy.stats.spearmanr`,
   which supplies both the correlation and its p-value.
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

EVENT_LOAD_COLUMNS = ["sue_primary", "sue_source", "permno_contested", "n_events_same_permno_day0"] + [
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
    con = duckdb.connect()
    col_list = ", ".join(EVENT_LOAD_COLUMNS)
    query = f"select {col_list} from read_parquet(?) where day0_status = 'ok'"
    return con.execute(query, [str(path)]).df()


# ---------------------------------------------------------------------------
# Decision 2: rank-normalizing the surprise measure.
# ---------------------------------------------------------------------------


def compute_sue_rank_pct(df: pd.DataFrame, sue_col: str = "sue_primary", source_col: str = "sue_source") -> pd.Series:
    """Percentile rank (0 to 1) of `sue_col`, computed separately within
    each `source_col` group -- see module docstring Decision 2. A row whose
    `sue_col` is null gets a null rank (pandas' default `rank` behavior,
    `na_option="keep"`), which is correct: there is nothing to rank. A
    `source_col` group that is entirely null (e.g. `sue_source == "none"`,
    where `sue_primary` is null by construction) ranks to all-null, not an
    error -- such rows simply never enter the quantile sort or IC, which
    both require a non-null `sue_rank_pct`.
    """
    return df.groupby(source_col)[sue_col].rank(pct=True)


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
    """Strict check (non-decreasing Q1 -> Qk) and a softer Spearman
    correlation between quintile index (1..k) and quintile mean return.
    Both are reported plainly -- this project's convention is to disclose
    the real result rather than assert a clean monotonic story the data
    doesn't show (see module docstring Decision 3). `spearman_rho`/`p` are
    None when fewer than 2 quintiles exist (a correlation needs at least 2
    points).
    """
    k = len(quintile_means)
    strict_monotonic = all(quintile_means[i] <= quintile_means[i + 1] for i in range(k - 1))
    rho, p = (None, None)
    if k >= 2:
        rho, p = sps.spearmanr(np.arange(1, k + 1), quintile_means)
        rho, p = float(rho), float(p)
    return {
        "n_quantiles": k,
        "quintile_means": [float(m) for m in quintile_means],
        "strict_monotonic": bool(strict_monotonic),
        "spearman_rho": rho,
        "spearman_p": p,
    }


def quantile_sort(df: pd.DataFrame, rank_col: str, value_col: str, n_quantiles: int = N_QUANTILES) -> tuple[list[dict], dict]:
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

    Returns (per-quintile records, monotonicity dict).
    """
    subset = df.dropna(subset=[rank_col, value_col])
    codes = pd.qcut(subset[rank_col], n_quantiles, labels=False, duplicates="drop")
    quintile = codes.to_numpy() + 1

    records = []
    means = []
    for q in sorted(pd.unique(quintile)):
        x = subset.loc[quintile == q, value_col].to_numpy(dtype="float64")
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
    return records, monotonicity_check(means)


def build_quantile_sort_results(df: pd.DataFrame, rank_col: str = "sue_rank_pct") -> dict:
    """{"quantile_sort": [...], "monotonicity": [...]} across every
    (horizon, return_type) -- see quantile_sort() for the per-horizon,
    per-return-type restriction."""
    quantile_records, monotonicity_records = [], []
    for h in HORIZONS:
        for kind, suffix in RETURN_KINDS:
            col = return_col(h, suffix)
            per_quintile, mono = quantile_sort(df, rank_col, col, N_QUANTILES)
            for rec in per_quintile:
                quantile_records.append({"horizon": h, "return_type": kind, **rec})
            monotonicity_records.append({"horizon": h, "return_type": kind, **mono})
    return {"quantile_sort": quantile_records, "monotonicity": monotonicity_records}


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
    return {"n": n, "ic": float(rho), "p_value": float(p)}


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


def build_results(df: pd.DataFrame) -> dict:
    """Pure function of an already day0_status=="ok"-restricted dataframe
    (see load_day0_ok_events). Adds `sue_rank_pct` and computes every
    statistic plus the caveat counts FINDINGS.md discloses."""
    assert len(df) > 0, "build_results: empty (day0_status=='ok') input -- nothing to compute"
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
        },
        "summary_stats": build_summary_stats_table(df),
        "quantile_sort": quantile_results["quantile_sort"],
        "monotonicity": quantile_results["monotonicity"],
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
    lines.append(
        f"Computed on the {n:,} `day0_status == \"ok\"` rows of "
        "`data/events/equity_events_1996_2013.parquet` (a real day 0 exists for every row used "
        "here). Each per-horizon statistic below is further restricted to that horizon's own "
        "non-null return column -- coverage differs slightly by horizon (see "
        "`data/events/FINDINGS.md`'s forward-return coverage table), so there is no single row "
        "count shared across all six horizons. This is the project's first statistical test of "
        "whether post-earnings-announcement drift (PEAD) shows up in this data.\n"
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
        "each `sue_source` group, so each source contributes its own 0-1 rank distribution "
        "regardless of its raw scale. The quantile sort and the Spearman IC below both use "
        "`sue_rank_pct`, never raw `sue_primary`, for anything that pools across sources.\n".format(scale_sentence)
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
        "surprise, Q5 = most positive. Monotonicity is reported two ways: a strict check (is the "
        "quintile-mean sequence non-decreasing from Q1 to Q5) and a softer Spearman correlation "
        "between quintile index and quintile mean return -- both stated plainly per horizon, "
        "including where the strict check fails, rather than smoothed into one verdict.\n"
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
            verdict = "monotonic" if mono["strict_monotonic"] else "NOT monotonic"
            mono_lines.append(
                f"{kind}: strict check is **{verdict}** (Q1..Q{mono['n_quantiles']} means "
                f"{', '.join(_fmt_pct(m) for m in mono['quintile_means'])}); Spearman(quintile "
                f"index, quintile mean) = {_fmt_num(mono['spearman_rho'], 3)} "
                f"(p = {_fmt_num(mono['spearman_p'], 3)})."
            )
        lines.append("")
        lines.append(" ".join(mono_lines) + "\n")

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
        "**Look-ahead.** This slice joins no new time-varying data, so it introduces no new "
        "look-ahead risk beyond what `data/events/FINDINGS.md` already discloses for the event "
        "table itself. Every restriction above is a non-null check on the specific column being "
        "summarized (per Decision 1) -- no row is dropped or reweighted based on how its surprise "
        "or return outcome \"turned out.\"\n"
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
