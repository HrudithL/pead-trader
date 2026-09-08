"""Charts for the aggressive-variant section of the showcase report: baseline-vs-aggressive NAV
curves, a grouped return/Sharpe/drawdown comparison, and the sizing-sweep tradeoff curve
(return/Sharpe/drawdown vs. base_unit_fraction multiplier) for Strategy 6 -- the same style of
chart 16_leverage_sweep.png already uses to justify a cap choice, applied here to justify where the
aggressive sizing search stopped.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, FIGURES_DIR
from common.plotting import apply_style
import matplotlib.pyplot as plt

DATA = DATA_DIR
FIG = FIGURES_DIR
apply_style()

PAIRS = [
    ("extreme",            "extreme_aggressive",            "1. Extreme decile L/S",        "#8fae8f"),
    ("rankweighted",       "rankweighted_aggressive",        "2. Rank-weighted",             "#6f9fc7"),
    ("balanced",           "balanced_aggressive",            "3. Balanced",                  "#c79f6f"),
    ("strategy4",          "strategy4_aggressive",           "4. Trimmed/tilted",            "#c76f8f"),
    ("strategy5",          "strategy5_aggressive",           "5. + Sector-neutral",          "#2f6f4f"),
    ("strategy6",          "strategy6_aggressive",           "6. + Leverage cap 2.5x",       "#3f6fa8"),
    ("strategy6_beta050",  "strategy6_beta_aggressive",      "6+beta. Beta overlay",         "#a83f3f"),
    ("strategy7",          "strategy7_aggressive",           "7. Unconstrained net exp.",    "#7f3fa8"),
]

# ---------- NAV curves: baseline (solid, muted) vs aggressive (dashed, bold) ----------
fig, ax = plt.subplots(figsize=(9.4, 5.6))
for base_key, agg_key, label, color in PAIRS:
    base = pd.read_csv(DATA / f"backtest_v2_{base_key}.csv", parse_dates=["date"])
    agg = pd.read_csv(DATA / f"backtest_v2_{agg_key}.csv", parse_dates=["date"])
    base = base[base["date"] >= "1996-01-01"]
    agg = agg[agg["date"] >= "1996-01-01"]
    ax.plot(base["date"], base["nav"] / 1e6, linewidth=1.0, color=color, alpha=0.55, label=f"{label} (baseline)")
    ax.plot(agg["date"], agg["nav"] / 1e6, linewidth=1.7, color=color, linestyle="--", label=f"{label} (aggressive)")
ax.axhline(10, color="#888888", linewidth=0.8, linestyle=":")
ax.set_xlabel("Date")
ax.set_ylabel("NAV ($M, trailing-NAV compounding, $10M start)")
ax.set_title("Baseline vs. Aggressive Variant: NAV Curves, 1996-2013", loc="left", fontsize=13)
ax.legend(loc="upper left", fontsize=6.8, frameon=False, ncol=2)
fig.tight_layout()
fig.savefig(FIG / "27_v2_aggressive_nav_curves.png", dpi=200)
plt.close(fig)

# ---------- Grouped comparison bars: baseline vs aggressive, all 8 pairs ----------
base_summary = pd.read_csv(DATA / "results_summary_v2_FINAL.csv").set_index("strategy")
agg_summary = pd.read_csv(DATA / "results_summary_v2_aggressive_FINAL.csv").set_index("strategy")
short_labels = ["1. Extreme", "2. Rank-wtd", "3. Balanced", "4. Tilted", "5. Sector-neut",
                "6. Lev 2.5x", "6+beta", "7. Net exp"]

fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.3))
metrics = [("ann_return", "Annualized return", True), ("sharpe", "Sharpe ratio", False),
           ("max_drawdown", "Max drawdown", True)]
x = np.arange(len(PAIRS))
width = 0.36
for ax, (col, title, pct) in zip(axes, metrics):
    base_vals = np.array([base_summary.loc[b, col] for b, a, l, c in PAIRS])
    agg_vals = np.array([agg_summary.loc[a, col] for b, a, l, c in PAIRS])
    if pct:
        base_vals, agg_vals = base_vals * 100, agg_vals * 100
    ax.bar(x - width / 2, base_vals, width, color="#9fb0a8", label="Baseline")
    ax.bar(x + width / 2, agg_vals, width, color="#a83f3f", label="Aggressive")
    ax.set_title(title, fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(short_labels, rotation=55, fontsize=7.6, ha="right")
    ax.axhline(0, color="#888888", linewidth=0.7)
axes[0].legend(loc="upper left", fontsize=8, frameon=False)
fig.suptitle("Baseline vs. Aggressive Variant, All 8 Strategies", fontsize=12.5, x=0.02, ha="left")
fig.tight_layout(rect=[0, 0, 1, 0.93])
fig.savefig(FIG / "28_v2_aggressive_comparison_bars.png", dpi=200)
plt.close(fig)

# ---------- Sizing-sweep tradeoff curve (Strategy 6, representative of the methodology) ----------
sweep = pd.read_csv(DATA / "sweep_aggressive_strategy6.csv")
fig, ax1 = plt.subplots(figsize=(7.8, 4.3))
ax1.plot(sweep["buf_multiplier"], sweep["sharpe"], marker="o", color="#2f6f4f", linewidth=1.8, label="Sharpe ratio")
ax1.axhline(0.6, color="#2f6f4f", linewidth=0.8, linestyle=":")
ax1.set_xlabel("Position-sizing multiplier (x Strategy 6's own base_unit_fraction)")
ax1.set_ylabel("Sharpe ratio", color="#2f6f4f")
ax1.tick_params(axis="y", labelcolor="#2f6f4f")
ax2 = ax1.twinx()
ax2.plot(sweep["buf_multiplier"], sweep["annualized_return"] * 100, marker="s", color="#a85f3f",
         linewidth=1.8, label="Ann. return")
ax2.plot(sweep["buf_multiplier"], -sweep["max_drawdown"] * 100, marker="^", color="#7f3fa8",
         linewidth=1.4, linestyle="--", label="Max drawdown (abs.)")
ax2.set_ylabel("Annualized return / |Max drawdown| (%)", color="#a85f3f")
ax2.tick_params(axis="y", labelcolor="#a85f3f")
ax1.axvline(4.0, color="#888888", linewidth=0.8, linestyle="--")
ax1.set_title("Strategy 6's Sizing-Sweep Tradeoff: Where \"More Aggressive\" Stops Paying Off",
              loc="left", fontsize=11.5)
lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="center left", fontsize=8, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "29_v2_aggressive_sizing_sweep.png", dpi=200)
plt.close(fig)

print("wrote 27_v2_aggressive_nav_curves.png, 28_v2_aggressive_comparison_bars.png, "
      "29_v2_aggressive_sizing_sweep.png")
