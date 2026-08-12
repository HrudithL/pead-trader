"""Standalone demo: builds a small sample PDF report from synthetic data to
prove scripts/report_builder.py's rendering end-to-end, per Slice 1's
acceptance criterion (memory/phase1-equity-pead-plan.md): "given a sample
chart + caption list, produces a valid A4 PDF with charts in the right
regions and readable captions."

Run directly:

    .venv/bin/python scripts/demo_sample_report.py [output_path]

Default output: outputs/sample_report.pdf. `outputs/` is already gitignored
(see .gitignore) -- this is a demo artifact for eyeballing the rendering,
not a tracked phase-completion report (those live in `reports/`, per that
directory's own README).

Every number and chart below is synthetic (numpy random data), by design:
this slice has no data dependency of its own, and the point is only to
exercise the layout -- title, summary, narrative sections, multiple charts
with captions -- the way a real future report (data-audit, earnings-data,
event-study-result) will.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this script never needs an on-screen window

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_builder  # noqa: E402

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "outputs" / "sample_report.pdf"


def _make_price_drift_chart() -> plt.Figure:
    rng = np.random.default_rng(seed=7)
    days = np.arange(-10, 41)
    noise = rng.normal(0, 0.8, size=days.size).cumsum() * 0.15
    drift = np.where(days >= 0, days * 0.06, 0.0) + noise
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(days, drift, color="#2b6cb0", linewidth=2)
    ax.axvline(0, color="#999999", linestyle="--", linewidth=1)
    ax.set_xlabel("Trading days since earnings announcement")
    ax.set_ylabel("Cumulative abnormal return (%)")
    ax.set_title("Synthetic example: price drift after an earnings surprise")
    fig.tight_layout()
    return fig


def _make_quantile_bar_chart() -> plt.Figure:
    rng = np.random.default_rng(seed=11)
    quantiles = ["Q1 (most negative)", "Q2", "Q3", "Q4 (most positive)"]
    returns = np.array([-1.4, -0.2, 0.5, 2.1]) + rng.normal(0, 0.1, size=4)
    fig, ax = plt.subplots(figsize=(7, 4))
    colors = ["#c53030" if v < 0 else "#2f855a" for v in returns]
    ax.bar(quantiles, returns, color=colors)
    ax.axhline(0, color="#333333", linewidth=0.8)
    ax.set_ylabel("Average 60-day forward return (%)")
    ax.set_title("Synthetic example: return by earnings-surprise quantile")
    fig.tight_layout()
    return fig


def _make_distribution_chart() -> plt.Figure:
    rng = np.random.default_rng(seed=3)
    surprises = rng.normal(0, 1, size=2000)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(surprises, bins=40, color="#805ad5")
    ax.set_xlabel("Standardized earnings surprise")
    ax.set_ylabel("Number of events")
    ax.set_title("Synthetic example: distribution of earnings surprises")
    fig.tight_layout()
    return fig


def build_sample_report(output_path: Path = DEFAULT_OUTPUT) -> Path:
    """Build the demo report and return the path it was written to. Split
    out from __main__ so tests can call this directly instead of shelling
    out to the script."""
    sections = [
        (
            "Why this report exists",
            "This is a demonstration report, not a real research finding. It "
            "uses made-up numbers to show what the shared PDF-report module "
            "looks like once a future report drops in real chart and caption "
            "data.\n\n"
            "Every stage of this project ends in a report like this one: a "
            "record of what was tried, what was found, and why it matters, "
            "written for someone with no finance background.",
        ),
        (
            "What \"post-earnings drift\" means, in plain terms",
            "When a company announces earnings that beat or miss what "
            "investors expected, the stock price sometimes keeps drifting in "
            "the same direction for weeks afterward, instead of settling "
            "immediately. That slow drift is the effect this project is "
            "built to measure. The three charts below are stand-ins for the "
            "kind of evidence a real report would show.",
        ),
    ]

    price_drift_fig = _make_price_drift_chart()
    quantile_fig = _make_quantile_bar_chart()
    distribution_fig = _make_distribution_chart()
    charts = [
        (
            price_drift_fig,
            "Example only, synthetic data: a stock's price often keeps "
            "drifting upward or downward for weeks after an earnings "
            "surprise, instead of adjusting all at once.",
        ),
        (
            quantile_fig,
            "Example only, synthetic data: sorting companies into four "
            "groups by the size of their earnings surprise and comparing "
            "average returns is one of the main checks this project runs.",
        ),
        (
            distribution_fig,
            "Example only, synthetic data: most earnings surprises cluster "
            "near zero (roughly in line with expectations), with fewer "
            "large beats or misses at the extremes.",
        ),
    ]

    try:
        return report_builder.build_pdf_report(
            title="Sample Report -- Reporting Infrastructure Demo",
            output_path=output_path,
            summary=(
                "This is a synthetic demo of the shared report-rendering "
                "module built in Slice 1, not a real result -- every chart "
                "below uses randomly generated data."
            ),
            sections=sections,
            charts=charts,
        )
    finally:
        for fig in (price_drift_fig, quantile_fig, distribution_fig):
            plt.close(fig)


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    written = build_sample_report(output)
    print(f"Sample report written to: {written}")
