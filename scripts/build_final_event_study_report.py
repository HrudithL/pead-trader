"""Third and final Phase-1 PDF report: presents the event-study statistical
result -- the actual H0 answer for whether post-earnings-announcement drift
(PEAD) shows up in this pilot data (Slice 9, `scripts/build_event_study_stats.py`)
-- to a reader with no finance background, per `reports/README.md`'s standing
requirement (A4, plain-language prose, per this project's phase-completion
report convention).

This is sub-slice 10b: adds the Results section and its three charts on top
of the 10a scaffold. Section order: Data & methodology -> Results (does
PEAD show up?) -> Limitations. Charts: quintile-mean by horizon (visual
monotonicity headline), Q5-Q1 spread with 95% CI (magnitude headline),
pooled Spearman IC by horizon with 95% band (significance-of-monotonicity
headline) -- all three on the market-adjusted return series specifically.

Relates to the other two Phase-1 reports:
  - `reports/equity_data_audit.pdf` -- Slice 4 -- the equity-price data this
    result is built on top of (what data/normalized_equity/ is; the split/
    dividend adjustment-status finding; per-year coverage).
  - `reports/earnings_data_report.pdf` -- Slices 5-6 -- the I/B/E/S earnings
    cleaning and surprise construction this result's `sue_rank_pct`
    ultimately traces back to.

Every number and prose claim below is derived at runtime from
`data/results/event_study_stats.json` (Slice 9's canonical output) and
`data/events/equity_events_1996_2013.parquet` (Slice 8's event table, read
only for sanity-check counts and the date range) -- nothing is hand-typed
from `data/results/FINDINGS.md`.

Run directly:

    .venv/bin/python scripts/build_final_event_study_report.py

Default output: `reports/event_study_result.pdf` -- `reports/` is tracked
in git (unlike `data/`/`outputs/`/`mlruns/`), per that directory's README.
"""

import json
import math
import sys
from pathlib import Path

import matplotlib
import matplotlib.figure
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_builder  # noqa: E402

matplotlib.use("Agg")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_STATS = REPO_ROOT / "data" / "results" / "event_study_stats.json"
DEFAULT_INPUT_EVENTS = REPO_ROOT / "data" / "events" / "equity_events_1996_2013.parquet"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "event_study_result.pdf"

# 5%-two-sided z threshold: used to gate "significant" claims in Results
# prose and captions. Kept as a module-level constant so both the prose
# and the charts read the same threshold; hardcoding 1.96 in three
# different places is exactly the class of drift the round-2 sweep guarded
# against.
_T_STAT_5PCT = 1.96

# Slice 9's JSON schema: the top-level keys this script requires. The
# `monotonicity` block is emitted by Slice 9 as well but is not consumed by
# THIS sub-slice (it's a Results-section input, i.e. sub-slice 10b), so
# it's deliberately not listed here -- validating on keys we don't read
# would over-constrain Slice 9 for no benefit.
_REQUIRED_STATS_KEYS = ("meta", "summary_stats", "quantile_sort", "quantile_spread", "spearman_ic")

# Meta-block keys this scaffold reads for the prose. `rank_cell_stats` is
# checked separately below because its own sub-keys are read as well.
_REQUIRED_META_KEYS = (
    "n_day0_ok",
    "horizons",
    "n_by_sue_source",
    "sue_scale_by_source",
    "n_sue_available",
    "rank_cell_stats",
)


def load_stats(path: Path = DEFAULT_INPUT_STATS) -> dict:
    """Load and shape-check Slice 9's canonical stats JSON.

    Raises ValueError with a clear message if any required top-level or
    meta-block key is missing -- so a drifted Slice 9 schema fails loudly
    here, in this scaffold, rather than silently producing a report with
    missing numbers or KeyError tracebacks buried under the PDF renderer.
    """
    with open(path, "r") as f:
        stats = json.load(f)

    missing_top = [k for k in _REQUIRED_STATS_KEYS if k not in stats]
    if missing_top:
        raise ValueError(
            f"event_study_stats.json is missing required top-level key(s): "
            f"{missing_top}. Present keys: {sorted(stats.keys())}."
        )

    meta = stats["meta"]
    missing_meta = [k for k in _REQUIRED_META_KEYS if k not in meta]
    if missing_meta:
        raise ValueError(
            f"event_study_stats.json['meta'] is missing required key(s): "
            f"{missing_meta}. Present meta keys: {sorted(meta.keys())}."
        )

    # sue_scale_by_source and n_by_sue_source: both are per-source dicts and
    # both must at minimum carry entries for the two sources this report's
    # prose talks about. `none` in n_by_sue_source is optional here -- some
    # runs could conceivably lack it if every event resolved to analyst or
    # seasonal -- but analyst/seasonal must exist to describe scale-
    # dispersion or the ranked-subset gap.
    for source in ("analyst", "seasonal"):
        if source not in meta["sue_scale_by_source"]:
            raise ValueError(
                f"event_study_stats.json['meta']['sue_scale_by_source'] is "
                f"missing source {source!r}."
            )
        if not {"n", "sd"} <= set(meta["sue_scale_by_source"][source]):
            raise ValueError(
                f"event_study_stats.json['meta']['sue_scale_by_source'][{source!r}] "
                f"is missing 'n' or 'sd'."
            )
        if source not in meta["n_by_sue_source"]:
            raise ValueError(
                f"event_study_stats.json['meta']['n_by_sue_source'] is missing "
                f"source {source!r}."
            )

    # Array-shape checks: the four per-horizon/per-quintile arrays must be
    # non-empty lists whose rows carry a `horizon` key. Anything else
    # (empty list, missing horizon per row) would only surface later as a
    # cryptic KeyError deep inside a prose helper -- fail fast here with a
    # message that names the actual malformed field.
    for key in ("summary_stats", "quantile_sort", "quantile_spread", "spearman_ic"):
        rows = stats[key]
        if not isinstance(rows, list) or len(rows) == 0:
            raise ValueError(
                f"event_study_stats.json[{key!r}] must be a non-empty list; "
                f"got {type(rows).__name__} of length "
                f"{len(rows) if hasattr(rows, '__len__') else 'unknown'}."
            )
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or "horizon" not in row:
                raise ValueError(
                    f"event_study_stats.json[{key!r}][{i}] is missing the "
                    f"'horizon' key (rows must carry the horizon they describe)."
                )

    return stats


def load_events_scope(path: Path = DEFAULT_INPUT_EVENTS) -> dict:
    """Read the events parquet only for what this report actually needs:
    the row count and the date range of the `day0_status == 'ok'` subset
    (i.e. the same population the stats meta's `n_day0_ok` counts). The
    per-event returns and surprise measures are NOT re-loaded here -- Slice
    9's stats JSON is the source of truth for every derived statistic.
    """
    import pandas as pd  # imported lazily so `load_stats` alone works without pandas

    df = pd.read_parquet(path, columns=["anndats", "day0_status"])
    ok_mask = df["day0_status"] == "ok"
    if not ok_mask.any():
        raise ValueError(
            f"Events file {path} has no rows with day0_status == 'ok'; cannot "
            f"derive the report's date-range scope."
        )
    ok = df.loc[ok_mask, "anndats"]
    return {
        "n_events_total": int(len(df)),
        "n_day0_ok": int(ok_mask.sum()),
        "start_date": ok.min().strftime("%Y-%m-%d"),
        "end_date": ok.max().strftime("%Y-%m-%d"),
    }


def _format_horizons(horizons: list[int]) -> str:
    """Render a horizons list as a plain-English list ("+1d, +5d, +10d,
    +20d, +40d and +60d") for prose. Serial-comma-free, matches the tone
    the earlier two reports use for enumerations."""
    if not horizons:
        raise ValueError("horizons list is empty; cannot describe the report's forward-return grid.")
    parts = [f"+{h}d" for h in horizons]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _build_summary(stats: dict, scope: dict) -> str:
    """One- to three-sentence lede: what population, at what horizons, and
    the ranked-subset size. Deliberately NO direction/dominance claim about
    the drift -- the numbers that answer that question live in the Results
    section (sub-slice 10b), not here."""
    meta = stats["meta"]
    n_day0_ok = int(meta["n_day0_ok"])
    horizons = list(meta["horizons"])
    n_sue_available = int(meta["n_sue_available"])
    ic_horizons = sorted({int(row["horizon"]) for row in stats["spearman_ic"]})
    if ic_horizons == sorted(int(h) for h in horizons):
        ic_coverage_clause = f"at all {len(ic_horizons)} of those horizons"
    else:
        ic_coverage_clause = (
            f"at {len(ic_horizons)} of those {len(horizons)} horizons "
            f"({', '.join(f'+{h}d' for h in ic_horizons)})"
        )

    # Sanity: the events-file day0-ok count must match the stats meta's
    # n_day0_ok. A mismatch means one of the two upstream files has drifted
    # relative to the other and the whole report's population claim is
    # wrong. Failing here rather than proceeding matches the audit-report
    # scripts' fail-loud discipline.
    if scope["n_day0_ok"] != n_day0_ok:
        raise ValueError(
            f"Events file day0_status=='ok' count ({scope['n_day0_ok']:,}) does "
            f"not match stats meta n_day0_ok ({n_day0_ok:,}). Regenerate one "
            f"of the two files so they describe the same population."
        )

    return (
        f"This report tests whether post-earnings-announcement drift shows up in a "
        f"pilot dataset of {n_day0_ok:,} U.S. equity earnings announcements "
        f"({scope['start_date']} to {scope['end_date']}), evaluated across "
        f"{len(horizons)} forward-return horizons ({_format_horizons(horizons)}) in "
        f"both raw and market-adjusted form. A rank-normalized earnings-surprise "
        f"measure -- available for the {n_sue_available:,}-event subset with a "
        f"non-null surprise -- is used for the quintile sort and the per-event "
        f"Spearman information coefficients that follow, {ic_coverage_clause}."
    )


def _build_methodology_section(
    stats: dict, scope: dict, events_path: Path
) -> tuple[str, str]:
    """The 'Data & methodology' section (heading, body).

    Explains: which dataset, how the surprise is defined (mechanical +
    analyst-based, with a rank-normalized composite), how PEAD is measured
    (forward returns at multiple horizons, raw and market-adjusted), and the
    point-in-time discipline. Every number is pulled from the JSON."""
    meta = stats["meta"]
    horizons = list(meta["horizons"])
    n_day0_ok = int(meta["n_day0_ok"])
    n_by_source = meta["n_by_sue_source"]
    n_analyst = int(n_by_source["analyst"])
    n_seasonal = int(n_by_source["seasonal"])
    # 'none' is documented optional in load_stats; gate on presence so a
    # missing key does NOT silently print '0 had neither' next to a nonzero
    # analyst+seasonal gap (round-2 class-sweep of round-1 finding #1).
    has_none = "none" in n_by_source
    n_none = int(n_by_source["none"]) if has_none else 0
    if has_none:
        no_surprise_clause = f", and {n_none:,} had neither and carry no surprise value"
    else:
        no_surprise_clause = (
            " (this stats build did not report the neither-source count "
            "separately -- any announcements with neither an analyst nor a "
            "seasonal surprise are absent from the ranked subset described "
            "below rather than accounted for here)"
        )
    n_sue_available = int(meta["n_sue_available"])

    # Scale dispersion between the two surprise measures. Comparing sds is
    # the classic motivation for the rank normalization, and the actual
    # ratio is data-derived -- a rerun where the two sds equalize should
    # not leave the prose claiming they're materially different, and a
    # rerun where one flips from larger to smaller should not leave the
    # prose describing the wrong source as the wider one.
    sd_analyst = float(meta["sue_scale_by_source"]["analyst"]["sd"])
    sd_seasonal = float(meta["sue_scale_by_source"]["seasonal"]["sd"])
    if sd_analyst > 0 and sd_seasonal > 0:
        # Non-degenerate case: pick which source is wider first, then
        # format the sentence once -- collapsing the two byte-identical
        # branches that the round-1 fix introduced (round-2 finding #6).
        if sd_analyst == sd_seasonal:
            scale_clause = (
                f"both the analyst-benchmarked and the seasonal-benchmarked surprises "
                f"have the same sample sd {sd_analyst:.3f}, so there is no dispersion "
                f"gap between them in this run"
            )
        else:
            ratio = max(sd_analyst, sd_seasonal) / min(sd_analyst, sd_seasonal)
            wider = "analyst" if sd_analyst > sd_seasonal else "seasonal"
            narrower = "seasonal" if wider == "analyst" else "analyst"
            scale_clause = (
                f"the {wider}-benchmarked surprise has sample sd {max(sd_analyst, sd_seasonal):.3f} "
                f"and the {narrower}-benchmarked surprise sd {min(sd_analyst, sd_seasonal):.3f}, "
                f"a {ratio:.2f}x dispersion gap"
            )
    else:
        # sd == 0 on either side is not a 'different scales' story; it is
        # a data-integrity red flag -- one source has zero dispersion at
        # all. Say so honestly rather than glossing it as scale mismatch
        # (round-2 finding #4).
        if sd_analyst == 0 and sd_seasonal == 0:
            degenerate = "both the analyst-benchmarked and the seasonal-benchmarked surprises have"
        elif sd_analyst == 0:
            degenerate = "the analyst-benchmarked surprise has"
        else:
            degenerate = "the seasonal-benchmarked surprise has"
        scale_clause = (
            f"{degenerate} zero sample dispersion in this run, which is a data-"
            f"integrity signal (analyst sd {sd_analyst:.3f}, seasonal sd {sd_seasonal:.3f}) "
            f"and not a normal cross-source scale story -- the rank normalization runs "
            f"anyway on whichever source has non-zero dispersion, but the reader should "
            f"treat any per-source numbers below with caution until the upstream data "
            f"is re-checked"
        )

    rank_cells = meta["rank_cell_stats"]
    n_cells = int(rank_cells["n_cells"])
    median_cell = float(rank_cells["median_cell_size"])
    min_cell = int(rank_cells["min_cell_size"])

    # Illustrative years for the point-in-time example: derive from the
    # actual data range so the sentence still reads correctly when the
    # window shifts. mid_year is a within-window example year that the
    # rank cell would have been drawn from; first/last are the endpoints
    # of the fixed look-ahead bug the sentence describes.
    first_year = int(scope["start_date"][:4])
    last_year = int(scope["end_date"][:4])
    mid_year = first_year + (last_year - first_year) // 2

    body = (
        "The dataset. This report is computed on the event table at "
        f"`data/events/{events_path.name}` -- {scope['n_events_total']:,} "
        f"U.S.-equity earnings announcements ({scope['start_date']} to "
        f"{scope['end_date']}) built by joining the cleaned I/B/E/S earnings "
        "table (see `reports/earnings_data_report.pdf` for the surprise-"
        "construction detail) to CRSP daily-panel prices (see "
        "`reports/equity_data_audit.pdf` for the price-data detail). Of those "
        f"announcements, {n_day0_ok:,} resolve to a real trading day 0 in the "
        "resolved permno's own trading history and form this report's population."
        "\n\n"
        "How \"surprise\" is defined. Every announcement can, in principle, be "
        "compared to the average Wall Street analyst estimate for that quarter "
        "(scaled by analyst disagreement -- the \"analyst\" measure) or to the "
        "same company's own result a quarter a year earlier (scaled by that "
        "company's own year-over-year volatility -- the mechanical \"seasonal\" "
        "measure, a fallback for announcements without usable analyst coverage). "
        f"In this population, {n_analyst:,} announcements got an analyst-based "
        f"measure, {n_seasonal:,} fell back to the seasonal measure"
        f"{no_surprise_clause}. The two measures "
        f"are on different scales -- {scale_clause} -- so they are never pooled "
        "on their raw scales for anything scale-sensitive. Instead, both are "
        "converted to a percentile rank (0 to 1) separately within each "
        f"(source, announcement calendar-quarter) group -- {n_cells} such cells "
        f"in total, median cell size {median_cell:,.0f} rows, smallest {min_cell} "
        "-- and the pooled rank (`sue_rank_pct`) is what the quintile sort and "
        f"the Spearman information coefficient below both use. This ranked "
        f"subset covers {n_sue_available:,} of the {n_day0_ok:,} day-0-ok events."
        "\n\n"
        "How PEAD is measured. \"Post-earnings-announcement drift\" is the "
        "hypothesis that stock prices keep drifting in the direction of a "
        "surprise for weeks after the announcement, rather than reacting all "
        "at once on day 0. This report tests it at "
        f"{len(horizons)} forward horizons -- {_format_horizons(horizons)} -- "
        "each computed two ways: the raw forward return (the stock's own "
        "compounded return over the h trading days strictly after day 0) and "
        "the market-adjusted forward return (that same window's stock return "
        "minus the CRSP value-weighted market index's total-return compound "
        "over the same window). Day 0's own reaction-day return is deliberately "
        "excluded from every horizon, so nothing being reported here is the "
        "announcement-day pop; the whole point of measuring drift is to look "
        "AFTER the reaction day has already happened. Standard errors, t-stats "
        "and 95% confidence intervals are plain i.i.d. (`sd / sqrt(n)`, "
        "t-distribution, df = n-1) -- see the Limitations section below for "
        "what that inference does and does not support."
        "\n\n"
        "Point-in-time discipline. `sue_rank_pct` is ranked within each "
        "(source, announcement calendar-quarter) group, so no event's rank "
        "depends on the surprise distribution of a later period -- an event "
        f"announced in, say, {mid_year}Q1 only ranks against other {mid_year}Q1 events "
        f"from the same source, never against events from {last_year} or any "
        "other later quarter. The forward-"
        "return computation is strictly forward from day 0 regardless of how "
        "the permno was resolved, and any missing daily return inside a "
        "window nulls that horizon rather than being silently treated as 0%. "
        "This is not a full look-ahead audit (see Limitations for what the "
        "remaining known simplifications are), but it does fix the specific "
        f"look-ahead bug an earlier version of this rank had, where a {first_year} "
        f"event's quintile depended on {last_year} data."
    )
    return ("Data and methodology", body)


def _build_limitations_section(stats: dict) -> tuple[str, str]:
    """The 'Limitations' section (heading, body). A plain-language, honest
    statement of exactly what this pilot's numbers do NOT support -- every
    limitation named here is one Slice 9's own FINDINGS.md or upstream
    audit reports already document, restated in one place for the reader.
    """
    meta = stats["meta"]
    n_day0_ok = int(meta["n_day0_ok"])
    n_sue_available = int(meta["n_sue_available"])
    n_by_source = meta["n_by_sue_source"]
    # Same class-fix as methodology: gate 'none' on presence, don't
    # silently default to zero (round-2 class-sweep of round-1 finding #1).
    has_none = "none" in n_by_source
    n_none = int(n_by_source["none"]) if has_none else 0
    # These two are optional in the JSON schema (see load_stats). Missing
    # keys are NOT the same as zero -- a stats build that didn't compute
    # them shouldn't get the "unusually clean state" false-negative.
    has_permno_contested = "n_permno_contested" in meta
    has_duplicate_firm_day = "n_duplicate_firm_day_rows" in meta
    n_permno_contested = int(meta["n_permno_contested"]) if has_permno_contested else 0
    n_duplicate_firm_day_rows = int(meta["n_duplicate_firm_day_rows"]) if has_duplicate_firm_day else 0

    # Ranked-subset gap: derived, not hardcoded, and gated on the numbers
    # actually differing. If a future rerun somehow eliminated the gap
    # (n_none == 0 and n_sue_available == n_day0_ok), the sentence would
    # falsely imply a mismatch that isn't there.
    gap = n_day0_ok - n_sue_available
    if gap > 0:
        # Whether we can name the specific 'neither' subset that comprises
        # the gap depends on whether the stats build reported it. If not,
        # the population-gap clause names the gap and stops there -- do
        # not falsely attribute the gap to a zero 'none' count.
        if has_none:
            gap_source_clause = (
                f", comprising the {n_none:,} announcements with neither an "
                f"analyst-based nor a seasonal-based surprise value"
            )
        else:
            gap_source_clause = (
                " (the specific per-source breakdown of that gap is not "
                "reported by this stats build)"
            )
        population_gap_clause = (
            f"The Results section's per-horizon lines above report the Q5-Q1 spread "
            f"and Spearman IC on the {n_sue_available:,}-event subset with a non-null "
            f"surprise -- a {gap:,}-event difference from the {n_day0_ok:,}-event "
            f"day-0-ok population{gap_source_clause}. Any per-horizon Q5-Q1 spread "
            f"or IC number in this report should therefore be read as a claim about "
            f"the ranked subset, not the full day-0-ok population."
        )
    else:
        population_gap_clause = (
            f"The Results section's per-horizon lines above run on the same "
            f"{n_day0_ok:,}-event day-0-ok population as the full sample; no "
            f"subsetting to a rankable-surprise slice was needed in this run."
        )

    # Duplicate-and-contested clause: gated on whether the stats build even
    # reported these counts, not just on the counts themselves -- a schema
    # drift that omits the keys should not silently print the "unusually
    # clean" branch (finding 1).
    if not (has_permno_contested and has_duplicate_firm_day):
        dup_clause = (
            "Non-independence: this stats build did not report the duplicate-"
            "firm-day and contested-permno counts, so this report cannot state "
            "how much of the ranked sample is strictly independent. Older "
            "builds on this pull found a small share on both counts; treat "
            "the i.i.d. standard errors above accordingly."
        )
    elif n_duplicate_firm_day_rows > 0 or n_permno_contested > 0:
        dup_pct = 100.0 * n_duplicate_firm_day_rows / n_day0_ok if n_day0_ok > 0 else 0.0
        contested_pct = 100.0 * n_permno_contested / n_day0_ok if n_day0_ok > 0 else 0.0
        dup_clause = (
            f"Non-independence: {n_duplicate_firm_day_rows:,} of the "
            f"{n_day0_ok:,} events used here ({dup_pct:.1f}%) share a "
            f"(permno, day-0-date) pair with at least one other event, and "
            f"{n_permno_contested:,} ({contested_pct:.1f}%) carry a permno "
            f"also claimed by a different I/B/E/S ticker over an overlapping "
            f"window -- neither is deduped or filtered here, matching the "
            f"upstream event table's tag-don't-filter convention, so a handful "
            f"of observations are not strictly independent of each other, which "
            f"the i.i.d. standard errors above do not account for."
        )
    else:
        dup_clause = (
            "Non-independence: no duplicate firm-day rows or contested permnos "
            "remain in this run -- an unusually clean state that a future run "
            "should not assume persists."
        )

    body = (
        "This section names, in one place, everything this pilot's numbers do "
        "NOT support. Every point below traces back to a specific decision or "
        "boundary in the upstream data, not a flaw in the statistical "
        "computation itself; the point of naming them here is to keep any "
        "\"headline\" reading of the numbers honest about their scope."
        "\n\n"
        "Standard errors are plain i.i.d. Every t-stat, standard error and "
        "confidence interval reported here uses `sd / sqrt(n)`, not clustering "
        "by firm or by announcement date, even though earnings cluster heavily "
        "in fiscal-quarter \"seasons.\" To the extent nearby events are "
        "correlated (same firm, same season, same industry shock, ...) these "
        "standard errors understate the true uncertainty; a later, more "
        "rigorous phase (see `memory/phase1-full-equity-pead-plan.md`) handles "
        "clustered/bootstrapped inference. These numbers should be read as a "
        "first look, not a final significance verdict."
        "\n\n"
        "No delisting-return handling. When a stock is delisted (going private, "
        "acquired, or dropped by the exchange) mid-horizon, its own price "
        "series simply ends -- CRSP's `dsedelist` delisting-return series is "
        "not merged into the return panel this report is built on. The "
        "longer-horizon numbers implicitly condition on survival to the horizon "
        "end. Whether attrited events differ systematically from retained ones "
        "in their earnings-surprise distribution is not audited in this report; "
        "the conservative reading is that this conditioning is not guaranteed "
        "random."
        "\n\n"
        "No transaction-cost or turnover analysis. This report measures whether "
        "a drift statistically exists on paper. It does NOT price out the "
        "commissions, spreads, and turnover cost of trading on it, and does "
        "not simulate any actual portfolio construction. A \"large\" Q5-Q1 "
        "spread here does not translate into an implementable strategy -- "
        "that is the entire scope of Phase 2 (see "
        "`memory/phase2-equity-strategy-plan.md`), not this report."
        "\n\n"
        "Pilot universe scope. The population is the resolved event universe "
        "defined and enumerated in the upstream reports (`reports/equity_data_audit.pdf` "
        "and `reports/earnings_data_report.pdf`) -- not the CRSP universe, "
        "not the full I/B/E/S coverage, and not any exchange-index index "
        "membership at any point in time. Any inference from these numbers to "
        "a specific listed-equity universe (large-cap, mid-cap, index-member, "
        "etc.) requires cross-checking against the upstream reports' own scope "
        "documentation."
        "\n\n"
        "Quintile-mean Spearman rho is descriptive, not inferential. The "
        "Results section's per-horizon lines above report a pooled per-event "
        "Spearman information coefficient (IC) with a real p-value -- that is "
        "the actual inferential test for a monotone relationship in this report, "
        "and it uses the full ranked-event sample. A separate quintile-index-"
        "vs-quintile-mean-return correlation (over 5 quintile means only) is a "
        "convenient descriptive summary of monotonicity, but 5 quintile means "
        "are not 5 independent draws, so it is not treated as a significance "
        "test and no p-value is attached to it anywhere in this report."
        "\n\n"
        "Point-in-time is fixed for the specific bug it fixed, not everything. "
        "The ranked surprise (`sue_rank_pct`) is grouped by announcement "
        "calendar quarter, so a 1996 event's rank no longer depends on 2013 "
        "surprise data -- that specific look-ahead bug is gone. But ranking "
        "still uses the whole calendar quarter's cross-section (up to ~3 "
        "months of same-quarter data, including events announced after the "
        "event being ranked), which is the standard PEAD construction, not a "
        "strictly real-time-as-of-announcement cutoff. This report does not "
        "claim to have audited every other potential look-ahead path through "
        "the upstream data."
        "\n\n"
        + population_gap_clause
        + "\n\n"
        + dup_clause
    )
    return ("Limitations (what this result does not support)", body)


def _mktadj_by_horizon_and_quintile(stats: dict) -> dict[int, list[tuple[int, float]]]:
    """Group `stats['quantile_sort']` into `{horizon: [(quintile, mean), ...]}`
    for `return_type == 'mktadj'`, with each horizon's list ordered by
    quintile ascending. Missing quintiles (e.g. a Slice 9 build that
    didn't emit Q1-Q5 for a horizon) raise loudly rather than silently
    truncating a chart or a monotonicity check."""
    by_horizon: dict[int, dict[int, float]] = {}
    for row in stats["quantile_sort"]:
        if row["return_type"] != "mktadj":
            continue
        h = int(row["horizon"])
        q = int(row["quintile"])
        by_horizon.setdefault(h, {})[q] = float(row["mean"])
    if not by_horizon:
        raise ValueError(
            "event_study_stats.json['quantile_sort'] has no return_type=='mktadj' rows; "
            "the Results section and its charts describe the market-adjusted "
            "sort specifically and cannot be built without them."
        )
    out: dict[int, list[tuple[int, float]]] = {}
    for h, qmap in by_horizon.items():
        quintiles = sorted(qmap.keys())
        if not quintiles:
            raise ValueError(f"quantile_sort has no quintile rows for horizon +{h}d (mktadj).")
        out[h] = [(q, qmap[q]) for q in quintiles]
    return out


def _mktadj_spread_by_horizon(stats: dict) -> dict[int, dict]:
    """Return `{horizon: {'spread', 'se', 't_stat', 'ci_low', 'ci_high', 'finite'}}`
    for `return_type == 'mktadj'` rows in `stats['quantile_spread']`. The
    CI half-width is derived here from `se` at the module's 5%-two-sided
    z; Slice 9's JSON does not carry an explicit CI for the spread rows,
    only `se` and `t_stat`. The `finite` flag (round-1 finding #3) is
    False when any of spread/se/t_stat is NaN or inf -- callers that
    aggregate significance flags MUST check `finite` before treating
    the row as evidence either way."""
    out: dict[int, dict] = {}
    for row in stats["quantile_spread"]:
        if row["return_type"] != "mktadj":
            continue
        h = int(row["horizon"])
        spread = float(row["spread"])
        se = float(row["se"])
        t_stat = float(row["t_stat"])
        finite = math.isfinite(spread) and math.isfinite(se) and math.isfinite(t_stat)
        half = _T_STAT_5PCT * se if math.isfinite(se) else float("nan")
        out[h] = {
            "spread": spread,
            "se": se,
            "t_stat": t_stat,
            "ci_low": spread - half,
            "ci_high": spread + half,
            "finite": finite,
        }
    if not out:
        raise ValueError(
            "event_study_stats.json['quantile_spread'] has no return_type=='mktadj' rows; "
            "the Q5-Q1 spread chart and Results section describe the market-"
            "adjusted spread specifically and cannot be built without them."
        )
    return out


def _mktadj_ic_by_horizon(stats: dict) -> dict[int, dict]:
    """Return `{horizon: {'ic', 'n', 'p_value', 'ci_low', 'ci_high', 'finite'}}` for
    `return_type == 'mktadj'` rows in `stats['spearman_ic']`. The 95% CI
    is derived from the sample size via a Fisher z-transform (rho ->
    atanh(rho); SE = 1/sqrt(n-3); CI = tanh(atanh(rho) +/- 1.96*SE)) --
    Slice 9's JSON reports the IC and its p_value but no CI, and the
    prompt's chart-3 spec calls for "95% CI or bands," so bands derived
    from n at the same 5% threshold the prose uses is the honest
    construction. The `finite` flag (round-1 finding #3) is False when
    ic/p_value/n is NaN, inf, or the Fisher z-band was undefined --
    callers that aggregate significance flags MUST check `finite`
    before treating the row as evidence either way.

    Slice 9's IC row `n` is expected to be the paired (surprise, return)
    count used in the Spearman calculation; if a future Slice 9 change
    makes `n` mean something else, the CI band's semantics silently
    change too."""
    out: dict[int, dict] = {}
    for row in stats["spearman_ic"]:
        if row["return_type"] != "mktadj":
            continue
        h = int(row["horizon"])
        ic = float(row["ic"])
        n = int(row["n"])
        p = float(row["p_value"])
        finite = math.isfinite(ic) and math.isfinite(p)
        if not finite or n <= 3 or abs(ic) >= 1.0:
            # Fisher z-transform is undefined at |rho| == 1 and needs n > 3.
            # A real PEAD sample never hits either; guard so the chart
            # doesn't crash if a degenerate synthetic fixture does. Also
            # covers NaN ic (finding #3): NaN comparisons are False, so
            # the earlier abs(ic) >= 1.0 check alone did not catch NaN.
            ci_low, ci_high = float("nan"), float("nan")
        else:
            z = math.atanh(ic)
            se_z = 1.0 / math.sqrt(n - 3)
            ci_low = math.tanh(z - _T_STAT_5PCT * se_z)
            ci_high = math.tanh(z + _T_STAT_5PCT * se_z)
        out[h] = {
            "ic": ic, "n": n, "p_value": p,
            "ci_low": ci_low, "ci_high": ci_high,
            "finite": finite,
        }
    if not out:
        raise ValueError(
            "event_study_stats.json['spearman_ic'] has no return_type=='mktadj' rows; "
            "the Spearman IC chart and Results section describe the market-"
            "adjusted IC specifically and cannot be built without them."
        )
    return out


def _assert_horizon_sets_agree(stats: dict) -> None:
    """Round-1 finding #5: assert that the three mkt-adj source tables
    this report's charts and Results section actually read (quantile_sort,
    quantile_spread, spearman_ic) agree on which horizons they cover.
    A silent mismatch would let one chart claim 'across 6 horizons' while
    another claims 'across 5' -- a report-level inconsistency this check
    prevents by failing loud at the top of build_final_event_study_report.

    summary_stats is NOT included here: 10a validates its top-level
    shape but neither the charts nor the Results-section prose read it,
    so its per-horizon mkt-adj membership is not report-level load-
    bearing (and cross-checking it here would over-constrain fixtures
    that don't need to fill it in)."""
    per_table: dict[str, set[int]] = {}
    for key in ("quantile_sort", "quantile_spread", "spearman_ic"):
        per_table[key] = {
            int(row["horizon"])
            for row in stats[key]
            if row.get("return_type") == "mktadj"
        }
    baseline = per_table["quantile_sort"]
    for key, horizons in per_table.items():
        if horizons != baseline:
            missing = baseline - horizons
            extra = horizons - baseline
            raise ValueError(
                f"Mkt-adj horizon set in {key!r} ({sorted(horizons)}) does not "
                f"match quantile_sort ({sorted(baseline)}); missing "
                f"{sorted(missing)}, extra {sorted(extra)}. Regenerate Slice 9 "
                f"so every table covers the same horizons before rebuilding this report."
            )


def _is_non_decreasing(values: list[float]) -> bool:
    """Non-decreasing means each successive quintile mean is >= the one
    before (ties allowed). NOT the same as `strict_monotonic` from the
    Slice 9 monotonicity block, which forbids ties -- the Results-section
    check the prompt asks for is the weaker non-decreasing check, so this
    is derived here directly from `quantile_sort` rather than reading
    Slice 9's precomputed flag."""
    return all(values[i + 1] >= values[i] for i in range(len(values) - 1))


def _make_quintile_mean_chart(stats: dict) -> matplotlib.figure.Figure:
    """Chart 1 -- the visual PEAD-monotonicity headline. One panel, x-axis
    quintile 1..5, y-axis mean market-adjusted forward return; one color-
    coded line per horizon. If every line slopes up from Q1 to Q5, the
    monotonicity claim is visible at a glance. Title is a data-derived
    shape claim -- how many horizons and quintiles the panel actually
    covers -- not an interpretation of the picture."""
    by_horizon = _mktadj_by_horizon_and_quintile(stats)
    horizons = sorted(by_horizon.keys())

    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    cmap = plt.get_cmap("viridis")
    n_h = len(horizons)
    for i, h in enumerate(horizons):
        rows = by_horizon[h]
        xs = [q for q, _ in rows]
        ys = [m * 100.0 for _, m in rows]  # percent
        color = cmap(i / max(1, n_h - 1))
        ax.plot(xs, ys, marker="o", color=color, linewidth=1.6, label=f"+{h}d")

    ax.axhline(0, color="#666666", linewidth=0.7, linestyle="--")
    ax.set_xlabel("Surprise quintile (1 = most negative, 5 = most positive)")
    ax.set_ylabel("Mean market-adjusted forward return (%)")
    # Data-derived structural title: names how many horizons and quintiles
    # the panel actually contains. Does NOT claim the picture is monotone.
    n_quintiles = len(next(iter(by_horizon.values())))
    ax.set_title(
        f"Mean market-adjusted forward return by surprise quintile, "
        f"{n_h} horizons x {n_quintiles} quintiles"
    )
    ax.set_xticks(sorted({q for rows in by_horizon.values() for q, _ in rows}))
    ax.legend(loc="upper left", fontsize=8, frameon=False, title="Horizon")
    fig.tight_layout()
    return fig


def _make_q5_q1_spread_chart(stats: dict) -> matplotlib.figure.Figure:
    """Chart 2 -- the numerical PEAD-magnitude headline. One panel, x-axis
    horizon, y-axis Q5-Q1 market-adjusted spread with 95% CI error bars
    (half-width 1.96 * se, matching the Q5-Q1 SE Slice 9 stored in the
    JSON). Title names how many horizons the panel covers and how many of
    them clear the 5% significance bar -- data-derived, no interpretive
    words like 'strong' or 'weak'."""
    by_horizon = _mktadj_spread_by_horizon(stats)
    horizons = sorted(by_horizon.keys())
    spreads = [by_horizon[h]["spread"] * 100.0 for h in horizons]  # percent
    err = [(by_horizon[h]["ci_high"] - by_horizon[h]["spread"]) * 100.0 for h in horizons]
    n_signif = sum(1 for h in horizons if abs(by_horizon[h]["t_stat"]) > _T_STAT_5PCT)

    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    x = list(range(len(horizons)))
    # Bar colors track sign so a hypothetical flip (negative Q5-Q1 spread)
    # is not silently rendered the same green as a positive one.
    colors = ["#2f855a" if s >= 0 else "#c53030" for s in spreads]
    ax.bar(x, spreads, color=colors, alpha=0.75, edgecolor="#333333", linewidth=0.5)
    ax.errorbar(x, spreads, yerr=err, fmt="none", ecolor="#1a1a1a", capsize=4, linewidth=1.0)
    ax.axhline(0, color="#666666", linewidth=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels([f"+{h}d" for h in horizons])
    ax.set_xlabel("Forward-return horizon")
    ax.set_ylabel("Q5 minus Q1 market-adjusted spread (%)")
    ax.set_title(
        f"Q5-Q1 market-adjusted spread with 95% CI, {len(horizons)} horizons "
        f"({n_signif} significant at 5% two-sided)"
    )
    fig.tight_layout()
    return fig


def _make_ic_by_horizon_chart(stats: dict) -> matplotlib.figure.Figure:
    """Chart 3 -- the significance-of-monotonicity headline. One panel,
    x-axis horizon, y-axis pooled Spearman IC of (rank surprise, market-
    adjusted forward return), with a 95% CI band derived from a Fisher
    z-transform of the sample size (Slice 9's JSON reports IC and
    p-value but no CI). Title names the number of horizons and how many
    have p < 0.05 -- data-derived, not an interpretation."""
    by_horizon = _mktadj_ic_by_horizon(stats)
    horizons = sorted(by_horizon.keys())
    ics = [by_horizon[h]["ic"] for h in horizons]
    lows = [by_horizon[h]["ci_low"] for h in horizons]
    highs = [by_horizon[h]["ci_high"] for h in horizons]
    n_signif = sum(1 for h in horizons if by_horizon[h]["p_value"] < 0.05)

    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    x = list(range(len(horizons)))
    ax.plot(x, ics, marker="o", color="#2b6cb0", linewidth=1.6, label="Spearman IC (mkt-adj)")
    ax.fill_between(x, lows, highs, color="#2b6cb0", alpha=0.20, label="95% CI (Fisher z from n)")
    ax.axhline(0, color="#666666", linewidth=0.7, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels([f"+{h}d" for h in horizons])
    ax.set_xlabel("Forward-return horizon")
    ax.set_ylabel("Pooled Spearman IC")
    ax.set_title(
        f"Pooled Spearman IC (rank surprise vs market-adjusted forward return), "
        f"{len(horizons)} horizons ({n_signif} with p < 0.05)"
    )
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    fig.tight_layout()
    return fig


def _quintile_mean_caption(stats: dict) -> str:
    """Data-derived caption for Chart 1. Reports how many horizons the
    panel's lines are non-decreasing across all 5 quintiles (weaker
    monotonicity), and the Q1/Q5 pair for the longest-horizon line as
    the concrete anchor number. No interpretive 'clearly PEAD-shaped'
    phrasing."""
    by_horizon = _mktadj_by_horizon_and_quintile(stats)
    horizons = sorted(by_horizon.keys())
    non_dec_count = sum(
        1 for h in horizons if _is_non_decreasing([m for _, m in by_horizon[h]])
    )
    if non_dec_count == len(horizons):
        mono_clause = f"is non-decreasing from Q1 to Q5 at all {len(horizons)} horizons"
    elif non_dec_count == 0:
        mono_clause = f"is non-decreasing at none of the {len(horizons)} horizons"
    else:
        mono_clause = (
            f"is non-decreasing at {non_dec_count} of {len(horizons)} horizons "
            f"(the remaining {len(horizons) - non_dec_count} contain a Q_i > Q_(i+1) reversal)"
        )
    longest = horizons[-1]
    q_rows = by_horizon[longest]
    q1 = q_rows[0][1] * 100.0
    q5 = q_rows[-1][1] * 100.0
    return (
        f"Each line is one forward-return horizon; the x-axis is the surprise "
        f"quintile (1 = most negative, 5 = most positive) and the y-axis is the "
        f"mean market-adjusted forward return in percent. The mkt-adj quintile "
        f"sort {mono_clause}. At the longest horizon shown ({longest}d), Q1's "
        f"mean is {q1:+.2f}% and Q5's mean is {q5:+.2f}%; the sign, ordering "
        f"and inter-line spacing here are the numbers the Q5-Q1 spread and "
        f"Spearman IC charts below quantify with confidence intervals."
    )


def _q5_q1_spread_caption(stats: dict) -> str:
    """Data-derived caption for Chart 2. Reports smallest and largest
    market-adjusted Q5-Q1 spreads across horizons, count significant at
    5%, and whether the spread widens, narrows or is flat across horizons
    -- direction gated on the actual first-vs-last comparison."""
    by_horizon = _mktadj_spread_by_horizon(stats)
    # Round-2 F2: filter to horizons whose row's data is finite before
    # any min/max/index operation, so a NaN slice-9 row doesn't crash
    # this caption at min(...) / .index(NaN).
    horizons = sorted(h for h, r in by_horizon.items() if r["finite"])
    if not horizons:
        return (
            "Each bar is the market-adjusted mean return in the top surprise quintile "
            "minus the mean in the bottom, at that horizon; error bars are the +/-1.96 "
            "standard-error 95% CI. No horizon produced finite spread statistics in "
            "this run -- the chart's bars are all missing."
        )
    spreads_pct = [by_horizon[h]["spread"] * 100.0 for h in horizons]
    t_stats = [by_horizon[h]["t_stat"] for h in horizons]
    n_signif = sum(1 for t in t_stats if abs(t) > _T_STAT_5PCT)
    h_min = horizons[spreads_pct.index(min(spreads_pct))]
    h_max = horizons[spreads_pct.index(max(spreads_pct))]

    # Direction: derive from first-vs-last, with an equality branch.
    first, last = spreads_pct[0], spreads_pct[-1]
    if last > first:
        direction = f"widens with horizon (from {first:+.2f}% at {horizons[0]}d to {last:+.2f}% at {horizons[-1]}d)"
    elif last < first:
        direction = f"narrows with horizon (from {first:+.2f}% at {horizons[0]}d to {last:+.2f}% at {horizons[-1]}d)"
    else:
        direction = f"is roughly flat across horizons ({first:+.2f}% at both endpoints)"

    if n_signif == len(horizons):
        sig_clause = f"all {len(horizons)} horizons"
    elif n_signif == 0:
        sig_clause = f"none of the {len(horizons)} horizons"
    else:
        sig_clause = f"{n_signif} of {len(horizons)} horizons"
    return (
        f"Each bar is the market-adjusted mean return in the top surprise quintile "
        f"minus the mean in the bottom, at that horizon; error bars are the +/-1.96 "
        f"standard-error 95% CI. The spread {direction}, spans "
        f"{min(spreads_pct):+.2f}% at {h_min}d to {max(spreads_pct):+.2f}% at "
        f"{h_max}d, and clears the 5% two-sided significance bar (|t| > "
        f"{_T_STAT_5PCT}) at {sig_clause}. Green marks a positive spread, red "
        f"a negative one; the sign is picked from the data, not fixed."
    )


def _ic_by_horizon_caption(stats: dict) -> str:
    """Data-derived caption for Chart 3. Reports minimum and maximum
    Spearman IC (mkt-adj) across horizons, count with p < 0.05, and
    whether the IC magnitude grows, shrinks, or stays flat first-to-last
    -- direction gated on the actual comparison so a re-run that flips
    the trajectory doesn't leave the caption asserting the wrong shape."""
    by_horizon = _mktadj_ic_by_horizon(stats)
    horizons = sorted(by_horizon.keys())
    ics = [by_horizon[h]["ic"] for h in horizons]
    p_signif = sum(1 for h in horizons if by_horizon[h]["p_value"] < 0.05)
    ic_min = min(ics)
    ic_max = max(ics)
    h_min = horizons[ics.index(ic_min)]
    h_max = horizons[ics.index(ic_max)]

    # Trajectory of the |IC| magnitude, first-to-last. Weak "grows" claim
    # is |IC[-1]| > |IC[0]|; equal magnitudes collapse to a flat branch.
    # SIGN-FLIP guard (round-1 finding #4): if the sign flips between the
    # first and last horizon (e.g. +IC at 1d, -IC at 60d), the magnitude
    # story is misleading -- report the flip explicitly before any
    # magnitude claim.
    m_first, m_last = abs(ics[0]), abs(ics[-1])
    if ics[0] * ics[-1] < 0:
        traj = (
            f"the sign flips between endpoints, from {ics[0]:+.3f} at {horizons[0]}d "
            f"to {ics[-1]:+.3f} at {horizons[-1]}d -- a reversal-shaped trajectory, "
            f"not a monotone magnitude story"
        )
    elif m_last > m_first:
        traj = f"the magnitude grows from |IC|={m_first:.3f} at {horizons[0]}d to {m_last:.3f} at {horizons[-1]}d"
    elif m_last < m_first:
        traj = f"the magnitude shrinks from |IC|={m_first:.3f} at {horizons[0]}d to {m_last:.3f} at {horizons[-1]}d"
    else:
        traj = f"the magnitude is flat at |IC|={m_first:.3f} at both endpoints"

    if p_signif == len(horizons):
        sig_clause = f"all {len(horizons)} horizons"
    elif p_signif == 0:
        sig_clause = f"none of the {len(horizons)} horizons"
    else:
        sig_clause = f"{p_signif} of {len(horizons)} horizons"
    return (
        f"Solid line: the pooled Spearman rank correlation between per-event "
        f"surprise rank (`sue_rank_pct`) and per-event market-adjusted forward "
        f"return, computed on the full ranked subset at each horizon; shaded "
        f"band: 95% CI derived from a Fisher z-transform of the sample size "
        f"(Slice 9's JSON reports IC and p-value but not an explicit CI). "
        f"IC ranges from {ic_min:+.3f} at {h_min}d to {ic_max:+.3f} at {h_max}d; "
        f"{traj}. The 5% two-sided significance bar (p < 0.05) is cleared at "
        f"{sig_clause}."
    )


def _build_results_section(stats: dict) -> tuple[str, str]:
    """The 'Results (does PEAD show up?)' section (heading, body).

    Reports, in plain language: (a) whether each mkt-adj quintile sort is
    non-decreasing, (b) each horizon's Q5-Q1 spread with t-stat and 5%
    sig-flag, (c) each horizon's Spearman IC with p-value, and (d) a
    single conclusion sentence gated on the three checks above.

    The conclusion has four disjoint branches:
      1. all-positive     -- every horizon non-decreasing AND every
                             Q5-Q1 spread significantly positive AND
                             every IC significantly positive
      2. mixed            -- at least one but not all horizons pass the
                             three-way check (some signal, some not)
      3. negative         -- no horizon non-decreasing AND every
                             significant spread is negative AND every
                             significant IC is negative
      4. degenerate       -- no horizon shows a spread OR an IC that
                             clears 5%, in either direction
    """
    by_qh = _mktadj_by_horizon_and_quintile(stats)
    by_sh = _mktadj_spread_by_horizon(stats)
    by_ih = _mktadj_ic_by_horizon(stats)
    horizons = sorted(set(by_qh) & set(by_sh) & set(by_ih))
    if not horizons:
        raise ValueError(
            "No horizon has mkt-adj rows in all three of quantile_sort, "
            "quantile_spread and spearman_ic -- cannot build the Results "
            "section against the intersection."
        )

    # Per-horizon booleans, derived rather than hardcoded. `is_signif_pos`
    # (spread) uses t_stat > +1.96 rather than abs(t) > 1.96 because the
    # positive-PEAD conclusion needs a POSITIVELY-signed significant
    # spread; a significant negative spread would be counter-evidence,
    # not confirmation.
    per_h = []
    for h in horizons:
        q_means = [m for _, m in by_qh[h]]
        non_dec = _is_non_decreasing(q_means)
        sp = by_sh[h]
        ic = by_ih[h]
        # Round-1 finding #3: guard significance flags on the row's
        # `finite` flag so a NaN spread/t-stat/ic/p-value from Slice 9
        # gets classified 'unknown' rather than silently False (NaN
        # comparisons return False, which would otherwise leak into
        # 'not significant' aggregates.)
        sp_finite = sp["finite"]
        ic_finite = ic["finite"]
        per_h.append(
            {
                "h": h,
                "non_dec": non_dec,
                "spread": sp["spread"],
                "spread_t": sp["t_stat"],
                "sp_finite": sp_finite,
                "ic_finite": ic_finite,
                "spread_sig_any": sp_finite and abs(sp["t_stat"]) > _T_STAT_5PCT,
                "spread_sig_pos": sp_finite and sp["t_stat"] > _T_STAT_5PCT,
                "spread_sig_neg": sp_finite and sp["t_stat"] < -_T_STAT_5PCT,
                "ic": ic["ic"],
                "ic_p": ic["p_value"],
                "ic_sig_any": ic_finite and ic["p_value"] < 0.05,
                "ic_sig_pos": ic_finite and ic["p_value"] < 0.05 and ic["ic"] > 0,
                "ic_sig_neg": ic_finite and ic["p_value"] < 0.05 and ic["ic"] < 0,
            }
        )

    all_non_dec = all(r["non_dec"] for r in per_h)
    none_non_dec = all(not r["non_dec"] for r in per_h)
    all_spread_sig_pos = all(r["spread_sig_pos"] for r in per_h)
    all_ic_sig_pos = all(r["ic_sig_pos"] for r in per_h)
    any_spread_sig = any(r["spread_sig_any"] for r in per_h)
    any_ic_sig = any(r["ic_sig_any"] for r in per_h)
    # Negative-side test: every significant spread is negatively signed
    # AND every significant IC is negatively signed. Silent horizons (not
    # significant either way) don't disqualify the negative reading, but
    # they also don't confirm it -- the guard against a false negative
    # branch is that at least ONE significant negative signal must exist.
    sig_spreads_all_neg = (
        any(r["spread_sig_neg"] for r in per_h)
        and all(r["spread_sig_neg"] or not r["spread_sig_any"] for r in per_h)
    )
    sig_ics_all_neg = (
        any(r["ic_sig_neg"] for r in per_h)
        and all(r["ic_sig_neg"] or not r["ic_sig_any"] for r in per_h)
    )

    if all_non_dec and all_spread_sig_pos and all_ic_sig_pos:
        conclusion = (
            f"At every one of the {len(horizons)} horizons tested, the mkt-adj "
            f"quintile sort is non-decreasing, the Q5-Q1 spread is significantly "
            f"positive at 5%, and the pooled Spearman IC is significantly "
            f"positive at 5%. The data supports a positive PEAD monotonicity "
            f"signal at every horizon in this population."
        )
    elif not any_spread_sig and not any_ic_sig:
        conclusion = (
            f"No horizon shows either a Q5-Q1 spread or a Spearman IC that "
            f"clears the 5% two-sided significance bar, in either direction. "
            f"The data does not support a PEAD signal in this population -- "
            f"any monotonicity in the quintile sort is within statistical "
            f"noise."
        )
    elif none_non_dec and sig_spreads_all_neg and sig_ics_all_neg:
        conclusion = (
            f"No horizon's mkt-adj quintile sort is non-decreasing; every "
            f"significant Q5-Q1 spread and every significant Spearman IC has "
            f"a negative sign. The data supports a REVERSAL signal, not the "
            f"standard positive-PEAD hypothesis."
        )
    else:
        # Data-directional summary (round-1 finding #1): the else branch
        # is not automatically "partial positive PEAD" -- it depends on
        # WHICH direction the significant signals point. Count both
        # signs across horizons and phrase the sentence to match.
        n_h = len(horizons)
        n_sig_pos_spread = sum(1 for r in per_h if r["spread_sig_pos"])
        n_sig_neg_spread = sum(1 for r in per_h if r["spread_sig_neg"])
        n_sig_pos_ic = sum(1 for r in per_h if r["ic_sig_pos"])
        n_sig_neg_ic = sum(1 for r in per_h if r["ic_sig_neg"])
        n_non_dec = sum(1 for r in per_h if r["non_dec"])
        n_all_three_pos = sum(
            1
            for r in per_h
            if r["non_dec"] and r["spread_sig_pos"] and r["ic_sig_pos"]
        )
        # Decide the summary direction from the balance of significant
        # signals, not from a hardcoded 'positive' fallback. Round-2 F1:
        # when both spread counts tie (typically both zero) but IC
        # signals lean unambiguously one direction, don't fall through to
        # a false 'directionally MIXED' label -- follow whichever leg has
        # a clear majority.
        spread_leans_pos = n_sig_pos_spread > n_sig_neg_spread
        spread_leans_neg = n_sig_neg_spread > n_sig_pos_spread
        spread_ties = n_sig_pos_spread == n_sig_neg_spread
        ic_leans_pos = n_sig_pos_ic > n_sig_neg_ic
        ic_leans_neg = n_sig_neg_ic > n_sig_pos_ic
        if (
            (spread_leans_pos and ic_leans_pos)
            or (spread_leans_pos and n_sig_pos_ic >= n_sig_neg_ic)
            or (spread_ties and ic_leans_pos)
        ):
            direction_summary = (
                f"the significant signals lean POSITIVE ({n_sig_pos_spread} of {n_h} "
                f"horizons show sig-positive spreads, {n_sig_neg_spread} sig-negative; "
                f"{n_sig_pos_ic} of {n_h} horizons show sig-positive ICs, {n_sig_neg_ic} "
                f"sig-negative). The data supports a partial, horizon-dependent "
                f"positive-PEAD signal but not a uniform one at every horizon"
            )
        elif (
            (spread_leans_neg and ic_leans_neg)
            or (spread_leans_neg and n_sig_neg_ic >= n_sig_pos_ic)
            or (spread_ties and ic_leans_neg)
        ):
            direction_summary = (
                f"the significant signals lean NEGATIVE ({n_sig_neg_spread} of {n_h} "
                f"horizons show sig-negative spreads, {n_sig_pos_spread} sig-positive; "
                f"{n_sig_neg_ic} of {n_h} horizons show sig-negative ICs, {n_sig_pos_ic} "
                f"sig-positive). The data does NOT support the standard positive-PEAD "
                f"hypothesis in this population; the reversal reading is closer to what "
                f"the significant horizons show"
            )
        else:
            direction_summary = (
                f"the significant signals are directionally MIXED ({n_sig_pos_spread} of {n_h} "
                f"horizons show sig-positive spreads, {n_sig_neg_spread} sig-negative; "
                f"{n_sig_pos_ic} of {n_h} horizons show sig-positive ICs, {n_sig_neg_ic} "
                f"sig-negative). The data does not support a single-direction PEAD "
                f"reading at every horizon; the pattern is horizon-dependent in sign"
            )
        conclusion = (
            f"The picture is mixed: {n_all_three_pos} of {n_h} horizons "
            f"clear all three positive-PEAD checks (non-decreasing sort, Q5-Q1 "
            f"spread significant and positive at 5%, IC significant and "
            f"positive at 5%); {n_non_dec} of {n_h} horizons are non-decreasing. "
            f"Overall {direction_summary}."
        )

    # Per-horizon line items. Formatted so grep-style substring assertions
    # in tests can pin the horizon, direction, and significance-flag
    # without over-specifying number precision.
    def _per_row_line(r: dict) -> str:
        h = r["h"]
        spread_pct = r["spread"] * 100.0
        spread_flag = "significant at 5%" if r["spread_sig_any"] else "NOT significant at 5%"
        nd_flag = "non-decreasing" if r["non_dec"] else "NOT non-decreasing"
        ic_flag = "p < 0.05" if r["ic_sig_any"] else "p >= 0.05"
        return (
            f"  * At +{h}d: mkt-adj quintile sort is {nd_flag}; Q5-Q1 spread is "
            f"{spread_pct:+.3f}% (t = {r['spread_t']:+.2f}, {spread_flag}); "
            f"Spearman IC = {r['ic']:+.4f} ({ic_flag}, p = {r['ic_p']:.2g})."
        )

    per_horizon_block = "\n".join(_per_row_line(r) for r in per_h)

    body = (
        "This section reports the three market-adjusted checks that answer "
        "the question the whole report was set up to test -- whether PEAD "
        "shows up in this pilot dataset -- and its numbers are illustrated by "
        "the three charts that follow.\n\n"
        f"Per-horizon breakdown ({len(horizons)} horizons, all mkt-adj):\n"
        f"{per_horizon_block}\n\n"
        "Read that block as three columns per row: (1) is the sort non-"
        "decreasing across Q1..Q5, i.e. does the mean return weakly rise "
        "with the surprise quintile? (2) is the Q5-Q1 spread large enough "
        "relative to its own standard error to reject a zero-spread null at "
        "5%? (3) is the pooled per-event Spearman rank correlation between "
        "surprise rank and forward return large enough relative to its own "
        "sample size to reject a zero-correlation null at 5%? All three must "
        "line up for a horizon to fully corroborate a positive-PEAD reading; "
        "any single failing check leaves that horizon partially supported at "
        "best.\n\n"
        f"Conclusion. {conclusion}"
    )
    return ("Results (does PEAD show up?)", body)


def build_final_event_study_report(
    output_path: Path = DEFAULT_OUTPUT,
    stats_path: Path = DEFAULT_INPUT_STATS,
    events_path: Path = DEFAULT_INPUT_EVENTS,
) -> Path:
    """Build the final event-study report and return the path it was
    written to.

    Sections in order: Data & methodology -> Results (does PEAD show up?)
    -> Limitations. Charts in order: quintile-mean by horizon, Q5-Q1
    spread with 95% CI, Spearman IC by horizon with 95% band -- all three
    on the market-adjusted return series specifically. Split out from
    __main__ so tests can call this directly instead of shelling out to
    the script."""
    stats = load_stats(stats_path)
    scope = load_events_scope(events_path)

    # Horizon-set consistency check (round-1 finding #5): every mkt-adj
    # source table this report uses -- summary_stats, quantile_sort,
    # quantile_spread, spearman_ic -- must agree on which horizons are
    # present. A mismatch means Slice 9 reported a horizon in one table
    # but not another, and any "across N horizons" claim in the prose
    # would silently mean different Ns in different sentences.
    _assert_horizon_sets_agree(stats)

    summary = _build_summary(stats, scope)
    sections = [
        _build_methodology_section(stats, scope, events_path),
        _build_results_section(stats),
        _build_limitations_section(stats),
    ]

    # Round-1 finding #7: build charts one at a time inside the try block
    # (rather than as a list comprehension outside it), so a failure in a
    # later chart still lets the finally-block close the earlier ones. A
    # list-comprehension form would create earlier figures, then raise
    # before they're bound to `charts`, leaking them into pyplot's global
    # figure manager.
    charts: list[tuple[matplotlib.figure.Figure, str]] = []
    try:
        # Round-2 F3: bind each figure to a local BEFORE calling its
        # caption helper, so a caption that raises (e.g. on an
        # unexpected data shape) can't leak the figure into pyplot's
        # global registry uncounted.
        fig1 = _make_quintile_mean_chart(stats)
        charts.append((fig1, _quintile_mean_caption(stats)))
        fig2 = _make_q5_q1_spread_chart(stats)
        charts.append((fig2, _q5_q1_spread_caption(stats)))
        fig3 = _make_ic_by_horizon_chart(stats)
        charts.append((fig3, _ic_by_horizon_caption(stats)))
        return report_builder.build_pdf_report(
            title="Event Study Result: Does Post-Earnings-Announcement Drift Show Up?",
            output_path=output_path,
            summary=summary,
            sections=sections,
            charts=charts,
        )
    finally:
        # Close the figures we created so the process doesn't leak them
        # across many test invocations or repeat runs.
        for fig, _ in charts:
            plt.close(fig)


def main() -> None:
    written = build_final_event_study_report()
    print(f"Final event-study report written to: {written}")


if __name__ == "__main__":
    main()

