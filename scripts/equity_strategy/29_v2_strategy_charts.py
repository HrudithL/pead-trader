"""Charts for the v2 strategy showcase and development-process reports.

Generates NAV curves, drawdowns, and return/Sharpe comparison for all 8 strategies
(the 3 originals plus Strategy 4 through Strategy 7 and the beta overlay), plus two
supporting diagnostic charts referenced in the development report: the leverage-cap
sweep that justified raising the cap in Strategy 6, and the SUE lag-4 autocorrelation
result behind the "no reversal" negative finding (script 28).
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

# All 8 strategies, in development order, with a distinct color per strategy.
STRATS = [
    ("extreme",           "1. Extreme decile L/S",              "#8fae8f"),
    ("rankweighted",      "2. Rank-weighted",                   "#6f9fc7"),
    ("balanced",          "3. Balanced (size-neutral)",         "#c79f6f"),
    ("strategy4",         "4. Trimmed/tilted, priority cap",    "#c76f8f"),
    ("strategy5",         "5. + Sector-neutral",                "#2f6f4f"),
    ("strategy6",         "6. + Leverage cap 2.5x",              "#3f6fa8"),
    ("strategy6_beta050", "6+beta. + 0.5x market beta overlay", "#a83f3f"),
    ("strategy7",         "7. Unconstrained net exposure",      "#7f3fa8"),
]

# ---------- NAV curves, all 8 ----------
fig, ax = plt.subplots(figsize=(9.0, 5.2))
for key, label, color in STRATS:
    df = pd.read_csv(DATA / f"backtest_v2_{key}.csv", parse_dates=["date"])
    df = df[df["date"] >= "1996-01-01"]
    ax.plot(df["date"], df["nav"] / 1e6, linewidth=1.5, color=color, label=label)
ax.axhline(10, color="#888888", linewidth=0.8, linestyle="--")
ax.set_xlabel("Date")
ax.set_ylabel("NAV ($M, trailing-NAV compounding, $10M start)")
ax.set_title("All 8 Strategies: NAV Curves, 1996-2013", loc="left", fontsize=13)
ax.legend(loc="upper left", fontsize=8.3, frameon=False, ncol=1)
fig.tight_layout()
fig.savefig(FIG / "12_v2_nav_curves_all.png", dpi=200)
plt.close(fig)

# ---------- NAV curves, core lineage only (5,6,6+beta) vs the v1 starting point ----------
fig, ax = plt.subplots(figsize=(8.6, 4.8))
core = [s for s in STRATS if s[0] in ("extreme", "strategy5", "strategy6", "strategy6_beta050")]
for key, label, color in core:
    df = pd.read_csv(DATA / f"backtest_v2_{key}.csv", parse_dates=["date"])
    df = df[df["date"] >= "1996-01-01"]
    ax.plot(df["date"], df["nav"] / 1e6, linewidth=1.8, color=color, label=label)
ax.axhline(10, color="#888888", linewidth=0.8, linestyle="--")
ax.set_xlabel("Date")
ax.set_ylabel("NAV ($M)")
ax.set_title("The Winning Lineage: v1 Baseline -> Strategy 5 -> Strategy 6 -> Beta Overlay",
             loc="left", fontsize=12.5)
ax.legend(loc="upper left", fontsize=9, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "13_v2_nav_curves_lineage.png", dpi=200)
plt.close(fig)

# ---------- Drawdowns, all 8 ----------
fig, ax = plt.subplots(figsize=(9.0, 4.4))
for key, label, color in STRATS:
    df = pd.read_csv(DATA / f"backtest_v2_{key}.csv", parse_dates=["date"])
    df = df[df["date"] >= "1996-01-01"]
    running_max = df["nav"].cummax()
    dd = (df["nav"] - running_max) / running_max * 100
    ax.plot(df["date"], dd, linewidth=1.0, color=color, label=label, alpha=0.9)
ax.set_xlabel("Date")
ax.set_ylabel("Drawdown from prior peak (%)")
ax.set_title("All 8 Strategies: Drawdowns, 1996-2013", loc="left", fontsize=13)
ax.legend(loc="lower left", fontsize=8, frameon=False, ncol=2)
fig.tight_layout()
fig.savefig(FIG / "14_v2_drawdowns_all.png", dpi=200)
plt.close(fig)

# ---------- Return / Vol / Sharpe comparison bars ----------
summary = pd.read_csv(DATA / "results_summary_v2_FINAL.csv")
order = ["extreme", "rankweighted", "balanced", "strategy4", "strategy5", "strategy6",
         "strategy6_beta050", "strategy7"]
summary = summary.set_index("strategy").loc[order].reset_index()
colors = [c for _, _, c in STRATS]
short_labels = ["1. Extreme", "2. Rank-wtd", "3. Balanced", "4. Tilted", "5. Sector-neut",
                "6. Lev 2.5x", "6+beta", "7. Net exp"]

fig, axes = plt.subplots(1, 3, figsize=(11.5, 4.0))
metrics = [("ann_return", "Annualized return", True), ("sharpe", "Sharpe ratio", False),
           ("max_drawdown", "Max drawdown", True)]
for ax, (col, title, pct) in zip(axes, metrics):
    vals = summary[col] * 100 if pct else summary[col]
    bars = ax.bar(short_labels, vals, color=colors)
    ax.set_title(title, fontsize=11)
    ax.tick_params(axis="x", rotation=55, labelsize=8)
    ax.axhline(0, color="#888888", linewidth=0.7)
    fmt = "{:.1f}%" if pct else "{:.2f}"
    for b, v in zip(bars, vals):
        ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 2 if v >= 0 else -10),
                    ha="center", fontsize=7.5)
fig.suptitle("Strategy Comparison: Return, Risk-Adjusted Return, and Drawdown", fontsize=12.5, x=0.02, ha="left")
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(FIG / "15_v2_comparison_bars.png", dpi=200)
plt.close(fig)

# ---------- Leverage-cap sweep (justifies Strategy 6's cap raise) ----------
lev = pd.read_csv(DATA / "sweep_strategy4_leverage.csv")
fig, ax1 = plt.subplots(figsize=(7.6, 4.2))
ax1.plot(lev["max_gross_leverage"], lev["sharpe"], marker="o", color="#2f6f4f", linewidth=1.8, label="Sharpe ratio")
ax1.set_xlabel("Leverage cap (x initial capital)")
ax1.set_ylabel("Sharpe ratio", color="#2f6f4f")
ax1.tick_params(axis="y", labelcolor="#2f6f4f")
ax2 = ax1.twinx()
ax2.plot(lev["max_gross_leverage"], lev["ann_ret"] * 100, marker="s", color="#a85f3f", linewidth=1.8, label="Ann. return")
ax2.set_ylabel("Annualized return (%)", color="#a85f3f")
ax2.tick_params(axis="y", labelcolor="#a85f3f")
ax1.axvline(2.5, color="#888888", linewidth=0.8, linestyle="--")
ax1.set_title("Where the Leverage Cap Stops Binding (Strategy 4 base)", loc="left", fontsize=12)
fig.tight_layout()
fig.savefig(FIG / "16_leverage_sweep.png", dpi=200)
plt.close(fig)

# ---------- SUE lag-4 autocorrelation (negative result: persistence, not reversal) ----------
rev = pd.read_csv(DATA / "sue_reversal_by_decile.csv")
fig, ax = plt.subplots(figsize=(7.2, 4.2))
cmap = plt.get_cmap("RdYlGn")
colors_rev = [cmap(0.05 + 0.9 * (d - 1) / 9) for d in rev["decile_lag4"]]
ax.bar(rev["decile_lag4"], rev["sue_t_mean"], color=colors_rev)
ax.set_xlabel("SUE decile, 4 quarters ago")
ax.set_ylabel("Mean SUE today")
ax.set_xticks(range(1, 11))
ax.set_title("SUE Persists, It Does Not Reverse at a 4-Quarter Lag\n(lag-4 autocorrelation = +0.05, vs. Bernard & Thomas 1990's -0.24)",
             loc="left", fontsize=11.5)
fig.tight_layout()
fig.savefig(FIG / "17_sue_reversal.png", dpi=200)
plt.close(fig)

print("wrote 12_v2_nav_curves_all.png, 13_v2_nav_curves_lineage.png, 14_v2_drawdowns_all.png, "
      "15_v2_comparison_bars.png, 16_leverage_sweep.png, 17_sue_reversal.png")
