"""Slice 3 of memory/phase3-options-pead-plan.md: options data-source and
archive audit report.

Purpose
-------
Renders reports/options_data_audit.pdf: the first Phase III report, meant
for a reader with no finance background. Explains:

  - what the "WRDS OptionMetrics check" resolved to (per plan Update
    2026-08-14: no OptionMetrics on this account, Path B chosen),
  - what the archive actually contains (per-family tables, row counts,
    year coverage, field presence) -- read from Slice 2's outputs, not
    re-scanned here,
  - what "options data" even is (a bid, an ask, an expiration, a strike,
    implied volatility) in plain terms, since every later report assumes
    this vocabulary,
  - which gaps in the archive matter for Phase III and which don't, so
    the reader knows the difference between "opprcd2011 is missing --
    that hurts" and "vsurfd is missing -- doesn't matter, we don't need
    it."

Read-only against ``data/metadata/options_archive_summary.json`` and the
archive itself (only for a small field-presence spot-check on opprcd).
Never writes to the raw archive.

Usage:
    .venv/bin/python scripts/build_options_audit_report.py \\
        [--summary-json data/metadata/options_archive_summary.json] \\
        [--output reports/options_data_audit.pdf]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this script never needs an on-screen window

import matplotlib.colors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_builder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUMMARY = REPO_ROOT / "data" / "metadata" / "options_archive_summary.json"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "options_data_audit.pdf"

# Same dataviz palette Phase I's equity_data_audit.pdf uses -- no new
# palette needed for this report.
_BLUE = "#2a78d6"
_ORANGE = "#eb6834"
_MUTED = "#898781"
_GRID = "#e1e0d9"

# Families that Phase III's actual-contract analysis (per plan §6.4 and
# the H1/H2/H4 tests) actually needs. Everything else in the archive is
# informational; not having a full conversion of it does not block this
# phase. Kept as a module-level constant so the report's own logic and
# the tests both refer to the same list rather than encoding it twice.
PHASE3_REQUIRED_FAMILIES: tuple[str, ...] = (
    "opprcd",       # option prices (bid/ask/IV/Greeks by option-date)
    "opinfd",       # option contract metadata (strike/expiration/multiplier)
    "secprd",       # underlying security prices
    "secnmd",       # security name history (identifier stability)
    "securd",       # security universe
    "zerocd",       # risk-free rate curve (for QuantLib Greeks validation)
    "distrd",       # distributions (splits/dividends -- adjustment context)
)

# Plain-language descriptions of every IvyDB table family the audit
# might report on. Used in the "Archive contents" table so a reader who
# doesn't know IvyDB can still tell what a row means. Any family not in
# this dict just appears with its raw name -- explicit unknown is better
# than a made-up plausible description.
_FAMILY_DESCRIPTIONS: dict[str, str] = {
    "opprcd": "Daily option prices (bid, ask, IV, Greeks) per contract.",
    "opinfd": "Option contract metadata: strike, expiration, style, multiplier.",
    "opvold": "Aggregated option volume and open interest per security-date.",
    "optionmnames": "Historical mapping between option IDs and their standardized names.",
    "secprd": "Daily underlying-security prices.",
    "secnmd": "Historical security names / identifiers.",
    "securd": "Security universe (one row per security).",
    "securd1": "Security universe variant (alternate cut of securd).",
    "distrd": "Distributions: splits, cash dividends, spinoffs.",
    "exchgd": "Exchange codes and calendar.",
    "hvold": "Historical realized-volatility statistics per security.",
    "idxdvd": "Index dividend distributions.",
    "indexd": "Index metadata.",
    "zerocd": "Zero-coupon risk-free rate curve (input to option pricing).",
    "stdopd": "Standardized-tenor synthetic options (constant DTE/moneyness).",
    "vsurfd": "Volatility surface at standardized DTE/moneyness grid.",
}

# Families we know Phase III does NOT need, so we say so in the report
# rather than leaving the reader to wonder why we're not upset about a
# missing conversion.
PHASE3_NOT_NEEDED_FAMILIES: tuple[str, ...] = ("stdopd", "vsurfd")


def load_summary(path: Path) -> dict:
    """Read Slice 2's ``options_archive_summary.json`` from disk.

    Raises ``FileNotFoundError`` with a pointer to the audit script if
    the summary is not present -- the report is meant to run *after* the
    audit, not as a substitute for it.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Audit summary not found: {path}. Run "
            f"scripts/audit_options_archive.py first."
        )
    return json.loads(path.read_text())


def _families_frame(summary: dict) -> pd.DataFrame:
    """Turn ``summary['families']`` into a tidy DataFrame with a
    plain-language column and a Phase III relevance flag.

    Sorted by relevance-first (required families before not-needed
    before other), then alphabetically inside each group, so the report
    table reads top-down "here's what matters, then everything else."
    """
    rows: list[dict] = []
    for fam, info in summary["families"].items():
        if fam in PHASE3_REQUIRED_FAMILIES:
            relevance = "required for Phase III"
        elif fam in PHASE3_NOT_NEEDED_FAMILIES:
            relevance = "not needed for Phase III"
        else:
            relevance = "informational"
        year_range = info["year_range"]
        rows.append(
            {
                "family": fam,
                "description": _FAMILY_DESCRIPTIONS.get(fam, "(no description)"),
                "parquet_files": info["parquet_files"],
                "sas_gaps": info["sas_gaps"],
                "year_range": (
                    f"{year_range[0]}-{year_range[1]}"
                    if year_range is not None
                    else "non-yearly"
                ),
                "relevance": relevance,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    relevance_order = {
        "required for Phase III": 0,
        "not needed for Phase III": 1,
        "informational": 2,
    }
    df["_sort"] = df["relevance"].map(relevance_order)
    df = df.sort_values(["_sort", "family"]).drop(columns="_sort").reset_index(drop=True)
    return df


def _coverage_heatmap(summary: dict) -> plt.Figure:
    """Family-by-year presence grid: one row per family that has a year
    dimension, one column per year, coloured by whether that year is
    present as parquet, present as SAS-only (i.e. unconverted), or
    missing entirely.

    Non-yearly tables (opinfd, securd, ...) are omitted from the grid --
    they'd be one-cell rows with no coverage story to show. The report's
    surrounding text covers them separately.
    """
    families = summary["families"]
    # Global year range across every yearly family, so the heatmap has a
    # consistent x-axis rather than each row's own narrow range.
    all_years: list[int] = []
    for fam, info in families.items():
        yr = info["year_range"]
        if yr is not None:
            all_years.extend(range(yr[0], yr[1] + 1))
    if not all_years:
        # No yearly families -- render a placeholder rather than crashing.
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "(no yearly tables in archive)", ha="center", va="center")
        ax.set_axis_off()
        return fig
    year_min, year_max = min(all_years), max(all_years)
    year_axis = list(range(year_min, year_max + 1))

    yearly_families = sorted(
        [fam for fam, info in families.items() if info["year_range"] is not None]
    )

    # Encoding: 2 = parquet present, 1 = SAS only (gap year in gap_years),
    # 0 = family covers this year but not this year (missing_within_range
    # without a SAS reason), np.nan = family does not cover this year at
    # all. NaN gets a distinct colour so "not in this family's era" reads
    # differently from "should be here, isn't."
    grid = np.full((len(yearly_families), len(year_axis)), np.nan)
    for i, fam in enumerate(yearly_families):
        info = families[fam]
        yr = info["year_range"]
        gap_years = set(info["gap_years"])
        missing_years = set(info["missing_years_within_range"])
        for j, year in enumerate(year_axis):
            if year < yr[0] or year > yr[1]:
                continue  # NaN: outside this family's covered range
            if year in gap_years:
                grid[i, j] = 1  # SAS present but no parquet
            elif year in missing_years:
                grid[i, j] = 0  # neither SAS nor parquet
            else:
                grid[i, j] = 2  # parquet present

    fig, ax = plt.subplots(figsize=(11, max(3.5, 0.35 * len(yearly_families) + 1.5)))
    cmap = matplotlib.colors.ListedColormap(["#c8443f", "#f0c26e", _BLUE])
    bounds = [-0.5, 0.5, 1.5, 2.5]
    norm = matplotlib.colors.BoundaryNorm(bounds, cmap.N)
    ax.imshow(
        grid,
        aspect="auto",
        cmap=cmap,
        norm=norm,
        interpolation="nearest",
    )
    ax.set_yticks(range(len(yearly_families)))
    ax.set_yticklabels(yearly_families)
    step = max(1, len(year_axis) // 10)
    ax.set_xticks(range(0, len(year_axis), step))
    ax.set_xticklabels([str(year_axis[i]) for i in range(0, len(year_axis), step)])
    ax.set_xlabel("Year")
    ax.set_title("Archive coverage: parquet vs SAS-only vs missing, by table family")
    legend_handles = [
        mpatches.Patch(color=_BLUE, label="Parquet present"),
        mpatches.Patch(color="#f0c26e", label="SAS present, parquet missing"),
        mpatches.Patch(color="#c8443f", label="Neither SAS nor parquet"),
        mpatches.Patch(color="white", ec=_MUTED, label="Outside family's era"),
    ]
    ax.legend(handles=legend_handles, loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.tight_layout()
    return fig


def _sizes_chart(summary: dict) -> plt.Figure:
    """Row-count bar chart, per-family, log scale.

    Big families (opprcd at hundreds of millions of rows) and small ones
    (opinfd at ~10K, exchgd at ~140K) span too many orders of magnitude
    for a linear chart. Log-y is the least misleading axis here.
    Row counts come from parquet metadata (Slice 2 already read the
    footers); the archive is not re-scanned.
    """
    from collections import defaultdict

    # Aggregate row counts per family from Slice 2's inventory parquet.
    # If that file's not present, fall back to family-summary-only bars
    # showing "N parquet files" instead of rows.
    inv_path = REPO_ROOT / "data" / "metadata" / "options_archive_inventory.parquet"
    fig, ax = plt.subplots(figsize=(10, 5))
    if inv_path.exists():
        inv = pd.read_parquet(inv_path)
        by_family = (
            inv.dropna(subset=["num_rows"])
            .groupby("family")["num_rows"]
            .sum()
            .sort_values(ascending=False)
        )
        ax.bar(range(len(by_family)), by_family.values, color=_BLUE)
        ax.set_yscale("log")
        ax.set_xticks(range(len(by_family)))
        ax.set_xticklabels(by_family.index, rotation=45, ha="right")
        ax.set_ylabel("Rows (log scale)")
        ax.set_title("Rows per table family (log scale)")
    else:
        counts = {fam: info["parquet_files"] for fam, info in summary["families"].items()}
        counts = dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))
        ax.bar(range(len(counts)), list(counts.values()), color=_BLUE)
        ax.set_xticks(range(len(counts)))
        ax.set_xticklabels(list(counts.keys()), rotation=45, ha="right")
        ax.set_ylabel("Parquet files present")
        ax.set_title("Parquet files per table family")
    ax.grid(axis="y", color=_GRID, alpha=0.5)
    fig.tight_layout()
    return fig


def _findings_severity_summary(summary: dict) -> tuple[int, int, list[dict], list[dict]]:
    """Split findings into (error_count, warning_count, required_relevant_errors,
    required_relevant_warnings).

    Only findings involving a Phase-III-required family (per
    ``PHASE3_REQUIRED_FAMILIES``) get lifted to "relevant" -- the point
    of the split is to tell the reader whether the missing files
    actually block the phase's work or not.
    """
    errors = [f for f in summary["findings"] if f["severity"] == "error"]
    warnings = [f for f in summary["findings"] if f["severity"] == "warning"]

    def _mentions_required_family(msg: str) -> bool:
        return any(fam in msg for fam in PHASE3_REQUIRED_FAMILIES)

    relevant_errors = [f for f in errors if _mentions_required_family(f["message"])]
    relevant_warnings = [f for f in warnings if _mentions_required_family(f["message"])]
    return len(errors), len(warnings), relevant_errors, relevant_warnings


def _format_families_table(df: pd.DataFrame) -> str:
    """Render the families frame as a fixed-width text table.

    ``report_builder.build_pdf_report`` renders section bodies as plain
    text with newlines preserved, so an aligned text table lays out
    correctly without needing HTML.
    """
    if df.empty:
        return "(no families in inventory)"
    cols = ["family", "parquet_files", "sas_gaps", "year_range", "relevance", "description"]
    widths = {c: max(len(c), df[c].astype(str).map(len).max()) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    lines = [header, "  ".join("-" * widths[c] for c in cols)]
    for _, row in df.iterrows():
        lines.append("  ".join(str(row[c]).ljust(widths[c]) for c in cols))
    return "\n".join(lines)


def _summary_narrative(summary: dict) -> str:
    """One-paragraph lede for the top of the report."""
    counts = summary["counts"]
    err_total, warn_total, err_rel, warn_rel = _findings_severity_summary(summary)
    parts = [
        f"Archive audit ran against {summary['archive_root']}.",
        f"{counts['sas_files']} SAS source files, "
        f"{counts['parquet_files']} parquet files converted, "
        f"{counts['sas_without_parquet']} SAS files without a parquet twin.",
    ]
    if err_rel or warn_rel:
        parts.append(
            f"{len(err_rel)} error(s) and {len(warn_rel)} warning(s) touch tables "
            f"Phase III actually needs; the rest of the {err_total + warn_total} "
            f"finding(s) are on tables Phase III does not use."
        )
    else:
        parts.append(
            f"Of {err_total} error(s) and {warn_total} warning(s) total, "
            f"none touch tables Phase III actually needs -- every gap is on "
            f"an informational or unused table."
        )
    return " ".join(parts)


def _relevant_findings_body(summary: dict) -> str:
    """Bullet-list of the findings that actually matter for Phase III.

    A finding "matters" if it names a family in
    ``PHASE3_REQUIRED_FAMILIES``. Everything else is listed only in the
    catch-all "other findings" section so it doesn't drown the important
    signal in noise.
    """
    _, _, rel_errs, rel_warns = _findings_severity_summary(summary)
    if not rel_errs and not rel_warns:
        return (
            "No errors or warnings on the tables Phase III requires. The archive is "
            "usable for the phase's H1/H2/H4 tests as-is, subject to Slice 4/5b's "
            "schema-conformance check when the loader lands."
        )
    lines: list[str] = []
    if rel_errs:
        lines.append("Errors (require attention):")
        for f in rel_errs:
            lines.append(f"  - {f['message']}")
    if rel_warns:
        if lines:
            lines.append("")
        lines.append("Warnings (documented failures, may be recoverable):")
        for f in rel_warns:
            lines.append(f"  - {f['message']}")
    return "\n".join(lines)


def _other_findings_body(summary: dict) -> str:
    """Roll up the findings that DON'T touch Phase III's required tables
    into a short summary so the reader knows they were catalogued
    without listing 36 individual not-needed lines."""
    err_total, warn_total, rel_errs, rel_warns = _findings_severity_summary(summary)
    other_err_count = err_total - len(rel_errs)
    other_warn_count = warn_total - len(rel_warns)
    if other_err_count == 0 and other_warn_count == 0:
        return "(none)"
    lines = [
        f"{other_err_count} error(s) and {other_warn_count} warning(s) touch tables "
        f"outside Phase III's requirement list.",
    ]
    other_families: set[str] = set()
    for f in summary["findings"]:
        if any(fam in f["message"] for fam in PHASE3_REQUIRED_FAMILIES):
            continue
        for fam in summary["families"]:
            if fam in f["message"]:
                other_families.add(fam)
                break
    if other_families:
        readable = sorted(other_families)
        lines.append(
            f"Affected families: {', '.join(readable)}. These are catalogued in "
            f"data/metadata/options_archive_gaps.csv and "
            f"options_archive_summary.json for future reference; they do not "
            f"block this phase."
        )
    return "\n".join(lines)


def build_options_audit_report(
    summary_json: Path = DEFAULT_SUMMARY,
    output_path: Path = DEFAULT_OUTPUT,
) -> Path:
    """Full render: reads Slice 2's summary + inventory, builds two
    charts, hands them to ``report_builder.build_pdf_report``."""
    summary = load_summary(summary_json)
    families_df = _families_frame(summary)

    sections: list[report_builder.NarrativeSection] = [
        (
            "What we set out to answer",
            "Phase III of this project asks whether the post-earnings-announcement "
            "drift phenomenon that Phase I established in equities also shows up in "
            "options prices. To answer that, the phase needs option-level data (daily "
            "prices, bid/ask, implied volatility, and Greeks) matched to the "
            "underlying stock and to earnings-announcement events.\n\n"
            "There are two possible sources of options data for this project: (a) "
            "OptionMetrics via WRDS -- a modern, subscription-based feed running "
            "through 2024, and (b) a local OptionMetrics archive from a prior "
            "collaborator, covering 1996-2013, on an external drive. This report "
            "explains which one we're using and why, and what the archive we chose "
            "actually contains.",
        ),
        (
            "The WRDS check outcome",
            "Result: OptionMetrics is not available on this project's WRDS account. "
            "The account's list_libraries() call, saved to "
            "data/metadata/wrds_available_libraries.txt, returned only "
            "'optionmsamp_us' -- the free 30-day sample database, not the full "
            "'optionm' schema.\n\n"
            "Consequence: Phase III uses the local 1996-2013 archive at "
            "data/raw_options/OptionMetrics (symlinked to "
            "/Volumes/copyrishabh/OptionMetrics). All the H1/H2/H4 results in this "
            "phase will describe options-market behavior in that window, not the "
            "post-2015 period. The 2015+ WRDS window (called 'Path A' in the plan) "
            "is closed by unavailability, not by choice.",
        ),
        (
            "What options data actually is",
            "An option contract gives its holder the right (but not the obligation) "
            "to buy (a call) or sell (a put) 100 shares of a specific stock, at a "
            "specific price (the strike), on or before a specific date (the "
            "expiration). Every day, dozens or hundreds of contracts trade on the "
            "same underlying stock at different strikes and expirations, each with "
            "its own price.\n\n"
            "For each contract, on each trading day, the archive stores:\n"
            "  - a bid (the highest price a buyer was willing to pay at the close),\n"
            "  - an ask (the lowest price a seller was willing to accept),\n"
            "  - a volume (how many contracts changed hands that day),\n"
            "  - an open interest (how many contracts existed at the end of that day),\n"
            "  - an implied volatility (the volatility number the market implicitly "
            "used to price this contract, backed out of the option price),\n"
            "  - Greeks -- delta, gamma, vega, theta -- which measure how the price "
            "responds to changes in the stock price, in market-wide volatility, and "
            "in time.\n\n"
            "The multiplier -- normally 100 -- says how many underlying shares one "
            "contract represents. All of Phase III's later work assumes this "
            "vocabulary.",
        ),
        (
            "Summary of the archive",
            _summary_narrative(summary),
        ),
        (
            "What the archive contains, table by table",
            "Every row below is a 'table family' -- for the yearly tables "
            "(opprcd, secprd, hvold), there is one parquet file per year in the "
            "range shown. 'sas_gaps' is the number of SAS source files that were "
            "never converted to parquet (either because the SAS file was found "
            "corrupt or the conversion job was never run on it).\n\n"
            + _format_families_table(families_df),
        ),
        (
            "Findings that matter for Phase III",
            _relevant_findings_body(summary),
        ),
        (
            "Other findings (catalogued, not blocking)",
            _other_findings_body(summary),
        ),
        (
            "What comes next",
            "This report covers Slices 2 and 3 of the phase's plan. The next "
            "slices (4, 5b) define a canonical option-observation schema and a "
            "loader that maps the archive's IvyDB fields onto that schema. Slice "
            "6 wires up the data-quality gate the plan requires, and Slice 7 does "
            "an independent Greeks/IV recomputation on a stratified sample using "
            "QuantLib as a cross-check on the vendor's numbers. None of the "
            "actual event-study results (H1, H2, H4) begin until Phase II ships "
            "the equity strategy work that they either build on or need to "
            "control for.",
        ),
    ]

    charts: list[report_builder.ChartCaption] = [
        (
            _coverage_heatmap(summary),
            "Archive coverage per table family, one row per family, one column "
            "per year. Blue means 'parquet file present.' Yellow means 'we have "
            "the raw SAS file but it never got converted to parquet.' Red means "
            "'no SAS file, no parquet file -- a true coverage gap.' White means "
            "'this family does not have a year at all -- it's a non-yearly "
            "table like opinfd (contract metadata) that lives in a single "
            "file.'",
        ),
        (
            _sizes_chart(summary),
            "Rows per table family, log scale. The scale is logarithmic because "
            "the biggest table (opprcd -- daily option prices) has hundreds of "
            "millions of rows while several others (opinfd, exchgd) have tens "
            "of thousands. A linear scale would hide everything except opprcd.",
        ),
    ]

    generated_at = dt.datetime.fromisoformat(summary["generated_at"])
    lede = (
        f"Options data-source and archive audit for Phase III. Archive audited "
        f"on {generated_at.date()}. This is a plain-language explainer, not a "
        f"technical reference -- it describes what the archive is, which parts "
        f"of it Phase III actually uses, and what the known gaps mean."
    )

    result_path = report_builder.build_pdf_report(
        title="Options data-source and archive audit",
        output_path=output_path,
        summary=lede,
        sections=tuple(sections),
        charts=tuple(charts),
    )
    for fig, _ in charts:
        plt.close(fig)
    return result_path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build reports/options_data_audit.pdf from Slice 2's audit summary.",
    )
    p.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    out = build_options_audit_report(
        summary_json=args.summary_json, output_path=args.output
    )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
