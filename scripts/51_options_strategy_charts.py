"""
Charts for reports/PEAD_Options_Strategy_Report.pdf -- the options-strategy GPU roadmap report
(scripts 40, 46-50). Three figures: NAV curves comparing the equity book (Strategy 6) against both
options strategies (Tier 1 near-ATM, Tier 1.5 Kelly-optimal), a return/Sharpe-by-horizon comparison
bar chart for the two options tiers, and the put-share-by-decile validation chart that shows the
Kelly-optimal selector discovering the PEAD direction from data alone, with no call/put rule
hard-coded anywhere in its logic.
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIG = ROOT / "reports" / "figures"

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.edgecolor": "#444444", "axes.labelcolor": "#222222", "text.color": "#222222",
    "xtick.color": "#444444", "ytick.color": "#444444", "font.size": 10.5,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#e5e5e5", "grid.linewidth": 0.7,
})

# ---------- Figure 24: NAV curves, equity Strategy 6 vs. options Tier 1 vs. Tier 1.5 (60d) ----------
fig, ax = plt.subplots(figsize=(9.0, 5.2))

eq = pd.read_csv(DATA / "backtest_v2_strategy6.csv", parse_dates=["date"])
ax.plot(eq["date"], eq["nav"] / 1e6, linewidth=1.5, color="#3f6fa8", label="Equity Strategy 6")

t1 = pd.read_csv(DATA / "backtest_options_v1_h60d.csv", parse_dates=["date"])
ax.plot(t1["date"], t1["nav"] / 1e6, linewidth=1.5, color="#c76f8f", label="Options Tier 1 (near-ATM, 60d)")

t15 = pd.read_csv(DATA / "backtest_options_optimal_h60d.csv", parse_dates=["date"])
ax.plot(t15["date"], t15["nav"] / 1e6, linewidth=1.5, color="#2f6f4f",
        label="Options Tier 1.5 (Kelly-optimal, walk-forward, 60d)")

ax.axhline(10, color="#888888", linewidth=0.8, linestyle="--")
ax.set_xlabel("Date")
ax.set_ylabel("NAV ($M, $10M start)")
ax.set_yscale("log")
ax.set_title("Equity vs. Options Strategies: NAV Curves (log scale)", loc="left", fontsize=13)
ax.legend(loc="upper left", fontsize=9, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "24_options_strategy_nav_curves.png", dpi=200)
plt.close(fig)

# ---------- Figure 25: return/Sharpe by horizon, Tier 1 vs Tier 1.5 ----------
t1_comp = pd.read_csv(DATA / "backtest_options_v1_comparison.csv")
t15_comp = pd.read_csv(DATA / "backtest_options_optimal_comparison.csv")
horizons = t1_comp["hold_horizon_days"].tolist()

fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6))
x = np.arange(len(horizons))
width = 0.35

axes[0].bar(x - width/2, t1_comp["annualized_return"] * 100, width, color="#c76f8f", label="Tier 1 (near-ATM)")
axes[0].bar(x + width/2, t15_comp["annualized_return"] * 100, width, color="#2f6f4f", label="Tier 1.5 (Kelly-optimal)")
axes[0].set_xticks(x)
axes[0].set_xticklabels([f"{h}d" for h in horizons])
axes[0].set_ylabel("Annualized return (%)")
axes[0].set_title("Return by hold horizon", loc="left", fontsize=11.5)
axes[0].axhline(0, color="#888888", linewidth=0.8)
axes[0].legend(fontsize=8.5, frameon=False)

axes[1].bar(x - width/2, t1_comp["sharpe"], width, color="#c76f8f", label="Tier 1 (near-ATM)")
axes[1].bar(x + width/2, t15_comp["sharpe"], width, color="#2f6f4f", label="Tier 1.5 (Kelly-optimal)")
axes[1].set_xticks(x)
axes[1].set_xticklabels([f"{h}d" for h in horizons])
axes[1].set_ylabel("Sharpe ratio")
axes[1].set_title("Sharpe by hold horizon", loc="left", fontsize=11.5)
axes[1].axhline(0, color="#888888", linewidth=0.8)

fig.suptitle("Tier 1 vs. Tier 1.5, by hold horizon", fontsize=13, x=0.02, ha="left")
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(FIG / "25_tier1_vs_tier15_by_horizon.png", dpi=200)
plt.close(fig)

# ---------- Figure 26: put-share by decile -- the "does the math discover direction" validation ----------
picks = pd.read_parquet(DATA / "event_options" / "optimal_contracts.parquet")
put_share = picks.groupby("decile")["kelly_side"].apply(lambda s: (s == "P").mean() * 100)

fig, ax = plt.subplots(figsize=(7.8, 4.6))
ax.bar(put_share.index, put_share.values, color="#2f6f4f", width=0.65)
ax.set_xlabel("SUE decile")
ax.set_ylabel("Put share of Kelly-optimal picks (%)")
ax.set_xticks(range(1, 11))
ax.set_title("The selector discovers the PEAD direction from data alone", loc="left", fontsize=12.5)
for d, v in put_share.items():
    ax.annotate(f"{v:.0f}%", (d, v), textcoords="offset points", xytext=(0, 4),
                ha="center", fontsize=8.5, color="#333333")
fig.tight_layout()
fig.savefig(FIG / "26_optimal_selector_put_share_by_decile.png", dpi=200)
plt.close(fig)

print("wrote figures 24, 25, 26 to", FIG)
