"""Third and final Phase-1 PDF report: presents the event-study statistical
result -- the actual H0 answer for whether post-earnings-announcement drift
(PEAD) shows up in this pilot data (Slice 9, `scripts/build_event_study_stats.py`)
-- to a reader with no finance background, per `reports/README.md`'s standing
requirement (A4, plain-language prose, per this project's phase-completion
report convention).

This is sub-slice 10a: a scaffold containing the script skeleton, data-
loading, and the two prose-only sections (Data & methodology, Limitations).
The Results section and its charts land in sub-slice 10b -- this sub-slice
deliberately ships `charts=[]` so the pipeline is proven end-to-end before
the more expensive chart work begins.

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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_builder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_STATS = REPO_ROOT / "data" / "results" / "event_study_stats.json"
DEFAULT_INPUT_EVENTS = REPO_ROOT / "data" / "events" / "equity_events_1996_2013.parquet"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "event_study_result.pdf"

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


def _build_methodology_section(stats: dict, scope: dict) -> tuple[str, str]:
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
    n_none = int(n_by_source.get("none", 0))
    n_sue_available = int(meta["n_sue_available"])

    # Scale dispersion between the two surprise measures. Comparing sds is
    # the classic motivation for the rank normalization, and the actual
    # ratio is data-derived -- a rerun where the two sds equalize should
    # not leave the prose claiming they're materially different, and a
    # rerun where one flips from larger to smaller should not leave the
    # prose describing the wrong source as the wider one.
    sd_analyst = float(meta["sue_scale_by_source"]["analyst"]["sd"])
    sd_seasonal = float(meta["sue_scale_by_source"]["seasonal"]["sd"])
    if sd_seasonal > 0 and sd_analyst > 0:
        ratio = max(sd_analyst, sd_seasonal) / min(sd_analyst, sd_seasonal)
        if sd_analyst > sd_seasonal:
            wider, narrower = "analyst", "seasonal"
            scale_clause = (
                f"the {wider}-benchmarked surprise has sample sd {max(sd_analyst, sd_seasonal):.3f} "
                f"and the {narrower}-benchmarked surprise sd {min(sd_analyst, sd_seasonal):.3f}, "
                f"a {ratio:.2f}x dispersion gap"
            )
        elif sd_seasonal > sd_analyst:
            wider, narrower = "seasonal", "analyst"
            scale_clause = (
                f"the {wider}-benchmarked surprise has sample sd {max(sd_analyst, sd_seasonal):.3f} "
                f"and the {narrower}-benchmarked surprise sd {min(sd_analyst, sd_seasonal):.3f}, "
                f"a {ratio:.2f}x dispersion gap"
            )
        else:
            # Equal sds (exact tie -- rare on real data, but a hardcoded
            # "wider is X" claim here would falsely imply a 1.00x gap that
            # isn't there. Print the two numbers without a direction word.
            scale_clause = (
                f"both the analyst-benchmarked and the seasonal-benchmarked surprises "
                f"have the same sample sd {sd_analyst:.3f}, so there is no dispersion "
                f"gap between them in this run"
            )
    else:
        # Defensive: a zero sd on either side would make the ratio
        # undefined; the sentence is dropped in that (implausible) case
        # rather than printed as "inf" or "nan".
        scale_clause = (
            f"the analyst-benchmarked surprise has sample sd {sd_analyst:.3f} and the "
            f"seasonal-benchmarked surprise sd {sd_seasonal:.3f}"
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
        f"`data/events/equity_events_1996_2013.parquet` -- {scope['n_events_total']:,} "
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
        f"measure, {n_seasonal:,} fell back to the seasonal measure, and "
        f"{n_none:,} had neither and carry no surprise value. The two measures "
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
    n_none = int(n_by_source.get("none", 0))
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
        population_gap_clause = (
            f"The per-horizon summary-statistics table (in the Results section, "
            f"sub-slice 10b) runs on the full {n_day0_ok:,}-event day-0-ok "
            f"population, while the quintile sort and the Spearman IC further "
            f"below run on the {n_sue_available:,}-event subset with a non-null "
            f"surprise -- a difference of {gap:,} events, comprising the "
            f"{n_none:,} announcements with neither an analyst-based nor a "
            f"seasonal-based surprise value. Any comparison of \"the mean at "
            f"horizon h\" from one table with \"the Q5-Q1 spread at horizon h\" "
            f"from the other implicitly conditions on that missing subset."
        )
    else:
        population_gap_clause = (
            f"The per-horizon summary-statistics table, the quintile sort, and "
            f"the Spearman IC all run on the same {n_day0_ok:,}-event "
            f"population; no subsetting to a rankable-surprise slice was needed "
            f"in this run."
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
        "quintile-index-vs-quintile-mean-return Spearman rho reported alongside "
        "the quintile sort (in sub-slice 10b) is computed over only 5 quintile "
        "means and, more fundamentally, those 5 means are not 5 independent "
        "draws -- so no p-value is reported next to it, and its value should "
        "be read as a compact summary of monotonicity, not a significance test. "
        "The real inferential test for a monotone relationship in this report "
        "is the pooled per-event Spearman information coefficient (also in "
        "sub-slice 10b), which uses the full ranked-event sample."
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


def build_final_event_study_report(
    output_path: Path = DEFAULT_OUTPUT,
    stats_path: Path = DEFAULT_INPUT_STATS,
    events_path: Path = DEFAULT_INPUT_EVENTS,
) -> Path:
    """Build the final event-study report (scaffold, sub-slice 10a) and
    return the path it was written to.

    This scaffold ships two prose sections (Data & methodology, Limitations)
    and NO charts -- `charts=[]` is deliberate; the Results section and its
    charts are the job of sub-slice 10b. Split out from __main__ so tests
    can call this directly instead of shelling out to the script.
    """
    stats = load_stats(stats_path)
    scope = load_events_scope(events_path)

    summary = _build_summary(stats, scope)
    sections = [
        _build_methodology_section(stats, scope),
        _build_limitations_section(stats),
    ]

    return report_builder.build_pdf_report(
        title="Event Study Result: Does Post-Earnings-Announcement Drift Show Up?",
        output_path=output_path,
        summary=summary,
        sections=sections,
        charts=[],
    )


def main() -> None:
    written = build_final_event_study_report()
    print(f"Final event-study report written to: {written}")


if __name__ == "__main__":
    main()
