"""Build the report's charts as PNG files in figures/."""
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
FIG.mkdir(exist_ok=True, parents=True)
apply_style()

# ---------- Chart 1: decile drift curves ----------
decile_stats = pd.read_csv(DATA / "decile_horizon_stats.csv")
pivot = decile_stats.pivot(index="horizon", columns="decile", values="mean").sort_index()

cmap = plt.get_cmap("RdYlGn")
colors = {d: cmap(0.05 + 0.9 * (d - 1) / 9) for d in range(1, 11)}

fig, ax = plt.subplots(figsize=(8.5, 5.2))
for d in range(1, 11):
    ax.plot(pivot.index, pivot[d] * 100, marker="o", markersize=3.5, linewidth=1.8,
            color=colors[d], label=f"D{d}")
ax.axhline(0, color="#888888", linewidth=0.8)
ax.set_xlabel("Trading days after announcement (day 0 excluded)")
ax.set_ylabel("Cumulative market-adjusted return (%)")
ax.set_title("Post-Earnings-Announcement Drift by Surprise Decile\nAnalyst-sourced SUE, 1996-2013, N=182,712 events", loc="left", fontsize=12)
ax.legend(loc="upper left", ncol=2, fontsize=8.5, frameon=False, title="Decile (D1=most negative surprise)")
ax.set_xticks(pivot.index)
fig.tight_layout()
fig.savefig(FIG / "01_decile_drift.png", dpi=200)
plt.close(fig)

# ---------- Chart 2: D10-D1 spread across horizons with quarter-clustered CI ----------
spread_stats = pd.read_csv(DATA / "spread_horizon_stats.csv")
fig, ax = plt.subplots(figsize=(7.5, 4.5))
x = spread_stats["horizon"]
y = spread_stats["mean_spread"] * 100
err = 1.96 * spread_stats["se"] * 100
ax.bar(x.astype(str), y, color="#2f6f4f", width=0.6)
ax.errorbar(x.astype(str), y, yerr=err, fmt="none", ecolor="#1a1a1a", capsize=4, linewidth=1.2)
ax.set_xlabel("Horizon (trading days)")
ax.set_ylabel("D10 - D1 spread, market-adjusted (%)")
ax.set_title("PEAD Spread (Extreme Positive Surprise minus Extreme Negative)\nwith 95% CI, clustered by announcement quarter", loc="left", fontsize=12)
fig.tight_layout()
fig.savefig(FIG / "02_spread_by_horizon.png", dpi=200)
plt.close(fig)

# ---------- Chart 3: sector subsample ----------
sec = pd.read_csv(DATA / "subsample_sector.csv").sort_values("mean_spread")
fig, ax = plt.subplots(figsize=(7.5, 5))
y_pos = np.arange(len(sec))
ax.barh(y_pos, sec["mean_spread"] * 100, xerr=1.96 * sec["se"] * 100,
        color="#3f6fa8", ecolor="#1a1a1a", capsize=3)
ax.set_yticks(y_pos)
ax.set_yticklabels(sec["group"])
ax.axvline(0, color="#888888", linewidth=0.8)
ax.set_xlabel("D10 - D1 spread at +60d, market-adjusted (%)")
ax.set_title("PEAD Spread by Fama-French 12 Industry Sector\n(ANOVA across sectors: not statistically distinguishable, p=0.99)", loc="left", fontsize=11.5)
fig.tight_layout()
fig.savefig(FIG / "03_sector_spread.png", dpi=200)
plt.close(fig)

# ---------- Chart 4: size quintile subsample ----------
size = pd.read_csv(DATA / "subsample_size.csv")
size["group"] = size["group"].astype(int)
size = size.sort_values("group")
fig, ax = plt.subplots(figsize=(6.5, 4.3))
ax.bar(size["group"].astype(str), size["mean_spread"] * 100,
       yerr=1.96 * size["se"] * 100, color="#a85f3f", ecolor="#1a1a1a", capsize=4)
ax.set_xlabel("Firm-size quintile (1 = smallest, 5 = largest)")
ax.set_ylabel("D10 - D1 spread at +60d (%)")
ax.set_title("PEAD Spread by Firm Size\n(monotonically decreasing; ANOVA p<0.001)", loc="left", fontsize=12)
fig.tight_layout()
fig.savefig(FIG / "04_size_spread.png", dpi=200)
plt.close(fig)

# ---------- Chart 5: era subsample ----------
era = pd.read_csv(DATA / "subsample_era.csv").sort_values("group")
fig, ax = plt.subplots(figsize=(7.2, 4.3))
ax.bar(era["group"], era["mean_spread"] * 100, yerr=1.96 * era["se"] * 100,
       color="#6f4f8f", ecolor="#1a1a1a", capsize=4)
ax.set_xlabel("Era")
ax.set_ylabel("D10 - D1 spread at +60d (%)")
ax.set_title("PEAD Spread Over Time\n(ANOVA across eras: p=0.14, suggestive but not conclusive decay)", loc="left", fontsize=11.5)
fig.tight_layout()
fig.savefig(FIG / "05_era_spread.png", dpi=200)
plt.close(fig)

print("wrote 5 figures to", FIG)
