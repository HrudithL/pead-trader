"""Build charts for the options strategy report: NAV curves and a cross-strategy comparison
against the 8 equity strategies from PEAD_Strategy_Showcase.pdf."""
import json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIG = ROOT / "reports" / "figures"
FIG.mkdir(exist_ok=True, parents=True)

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "axes.edgecolor": "#444444",
    "axes.labelcolor": "#222222", "text.color": "#222222", "xtick.color": "#444444",
    "ytick.color": "#444444", "font.size": 10.5, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": "#e5e5e5",
    "grid.linewidth": 0.7,
})

q = pd.read_csv(DATA / "options_strategy_quarterly.csv", index_col=0)
with open(DATA / "options_strategy_summary.json") as f:
    summary = json.load(f)

LABELS = {
    "O0_raw": "Naked options, gross",
    "O0_net": "Naked options, net of cost",
    "O1_raw": "Risk reversal, gross",
    "O1_mktadj": "Risk reversal, mkt-adj.",
    "O1_net_mktadj": "Risk reversal, mkt-adj. + net of cost",
}
COLORS = {
    "O0_raw": "#cfa93f", "O0_net": "#a8763f",
    "O1_raw": "#8fb894", "O1_mktadj": "#4f8f6f", "O1_net_mktadj": "#1a5c3a",
}

# ---------- Figure 24: naked-option fixed-notional NAV, gross vs net (shows cost impact) ----------
fig, ax = plt.subplots(figsize=(8.5, 5))
for name in ["O0_raw", "O0_net"]:
    nav = 1 + q[name].cumsum()
    ax.plot(range(len(nav)), nav, label=LABELS[name], color=COLORS[name], linewidth=1.8)
ax.axhline(1, color="#888888", linewidth=0.8)
ax.set_xlabel("Announcement quarter (1996 Q1 -> 2013 Q3)")
ax.set_ylabel("Fixed-notional cumulative P&L (NAV, start=1)")
ax.set_title("Naked Long Options on D10/D1: Cost Impact Is Severe\n(fixed-notional basis -- see script 42 for why compounding this book is misleading)", loc="left", fontsize=11.5)
ax.legend(frameon=False, loc="upper left")
fig.tight_layout()
fig.savefig(FIG / "24_naked_options_nav_cost_impact.png", dpi=200)
plt.close(fig)

# ---------- Figure 25: risk-reversal NAV, all 3 flavors ----------
fig, ax = plt.subplots(figsize=(8.5, 5))
for name in ["O1_raw", "O1_mktadj", "O1_net_mktadj"]:
    nav = 1 + q[name].cumsum()
    ax.plot(range(len(nav)), nav, label=LABELS[name], color=COLORS[name], linewidth=1.8)
ax.axhline(1, color="#888888", linewidth=0.8)
ax.set_xlabel("Announcement quarter (1996 Q1 -> 2013 Q3)")
ax.set_ylabel("Fixed-notional cumulative P&L (NAV, start=1)")
ax.set_title("Risk-Reversal Book: Stock-Like Scale, Compounds Sanely\n(D10 long risk reversal / D1 short risk reversal, equal capital)", loc="left", fontsize=11.5)
ax.legend(frameon=False, loc="upper left")
fig.tight_layout()
fig.savefig(FIG / "25_risk_reversal_nav.png", dpi=200)
plt.close(fig)

# ---------- Figure 26: Sharpe ratio, all option strategies + all 8 equity strategies ----------
eq = pd.read_csv(DATA / "results_summary_v2_FINAL.csv")
opt_rows = [(LABELS[k], summary[k]["sharpe"], "option") for k in
            ["O0_net", "O1_raw", "O1_mktadj", "O1_net_mktadj"]]
eq_rows = [(row["label"], row["sharpe"], "equity") for _, row in eq.iterrows()]
comp = pd.DataFrame(opt_rows + eq_rows, columns=["label", "sharpe", "kind"]).sort_values("sharpe")

fig, ax = plt.subplots(figsize=(8.5, 6.5))
colors = ["#2f6f4f" if k == "option" else "#3f6fa8" for k in comp["kind"]]
y_pos = np.arange(len(comp))
ax.barh(y_pos, comp["sharpe"], color=colors)
ax.set_yticks(y_pos)
ax.set_yticklabels(comp["label"], fontsize=9)
ax.axvline(0, color="#888888", linewidth=0.8)
ax.set_xlabel("Sharpe ratio")
ax.set_title("Sharpe Ratio: Options Strategies (green) vs. Equity Strategies (blue)\nfrom PEAD_Strategy_Showcase.pdf, for context", loc="left", fontsize=11.5)
fig.tight_layout()
fig.savefig(FIG / "26_sharpe_comparison_all_strategies.png", dpi=200)
plt.close(fig)

# ---------- Figure 27: risk-reversal decile drift (market-adjusted, 60d) ----------
rr_decile = pd.read_csv(DATA / "option_rr_decile_horizon_stats.csv")
sub = rr_decile[rr_decile.kind == "rr_ret_mktadj_fwd"]
pivot = sub.pivot(index="horizon", columns="decile", values="mean").sort_index()
cmap = plt.get_cmap("RdYlGn")
dcolors = {d: cmap(0.05 + 0.9 * (d - 1) / 9) for d in range(1, 11)}
fig, ax = plt.subplots(figsize=(8.5, 5.2))
for d in range(1, 11):
    if d not in pivot.columns:
        continue
    ax.plot(pivot.index, pivot[d] * 100, marker="o", markersize=3.5, linewidth=1.8,
            color=dcolors[d], label=f"D{d}")
ax.axhline(0, color="#888888", linewidth=0.8)
ax.set_xlabel("Trading days after announcement (day 0 = entry)")
ax.set_ylabel("Market-adjusted risk-reversal return (% of underlying notional)")
ax.set_title("Risk-Reversal Return by Decile, Market-Adjusted\n(combined delta ~1.0 -- this is the cleanest single view of the tradeable PEAD signal)", loc="left", fontsize=11.5)
ax.legend(loc="upper left", ncol=2, fontsize=8.5, frameon=False, title="Decile (D1=most negative surprise)")
ax.set_xticks(pivot.index)
fig.tight_layout()
fig.savefig(FIG / "27_risk_reversal_decile_drift.png", dpi=200)
plt.close(fig)

print("wrote figures 24-27 to", FIG)
