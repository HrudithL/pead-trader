"""Charts for the Fama-French-style characteristic-adjustment robustness section."""
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

raw = pd.read_csv(DATA / "decile_horizon_stats.csv")
ff = pd.read_csv(DATA / "ff_decile_horizon_stats.csv")

# ---------- Chart 6: D1 and D10 paths, raw market-adjusted vs characteristic-adjusted ----------
fig, ax = plt.subplots(figsize=(7.6, 4.8))
for d, color, label in [(1, "#b0271e", "D1"), (10, "#1a6b3c", "D10")]:
    r = raw[raw["decile"] == d].sort_values("horizon")
    f = ff[ff["decile"] == d].sort_values("horizon")
    ax.plot(r["horizon"], r["mean"] * 100, marker="o", linewidth=2.0, color=color,
            label=f"{label}, market-adjusted")
    ax.plot(f["horizon"], f["mean"] * 100, marker="s", linestyle="--", linewidth=2.0,
            color=color, alpha=0.65, label=f"{label}, size+BM adjusted")
ax.axhline(0, color="#888888", linewidth=0.8)
ax.set_xlabel("Trading days after announcement")
ax.set_ylabel("Cumulative return (%)")
ax.set_title("D1 and D10 Paths: Raw Market-Adjusted vs. Size+Book-to-Market Adjusted",
             loc="left", fontsize=11.7)
ax.legend(loc="upper left", fontsize=8.7, frameon=False)
ax.set_xticks(sorted(raw["horizon"].unique()))
fig.tight_layout()
fig.savefig(FIG / "06_ff_d1_d10_paths.png", dpi=200)
plt.close(fig)

# ---------- Chart 7: spread comparison raw vs FF-adjusted ----------
raw_s = pd.read_csv(DATA / "spread_horizon_stats.csv")
ff_s = pd.read_csv(DATA / "ff_spread_horizon_stats.csv")

fig, ax = plt.subplots(figsize=(7.2, 4.5))
x = np.arange(len(raw_s))
width = 0.36
ax.bar(x - width/2, raw_s["mean_spread"] * 100, width, yerr=1.96 * raw_s["se"] * 100,
       label="Raw market-adjusted", color="#2f6f4f", ecolor="#1a1a1a", capsize=3)
ax.bar(x + width/2, ff_s["mean_spread"] * 100, width, yerr=1.96 * ff_s["se"] * 100,
       label="Size + BM adjusted", color="#8f6f2f", ecolor="#1a1a1a", capsize=3)
ax.set_xticks(x)
ax.set_xticklabels([f"+{int(h)}d" for h in raw_s["horizon"]])
ax.set_xlabel("Horizon")
ax.set_ylabel("D10 - D1 spread (%)")
ax.set_title("PEAD Spread Survives Size/Value Risk Adjustment\n(95% CI, clustered by announcement quarter)",
             loc="left", fontsize=11.7)
ax.legend(loc="upper left", fontsize=9, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "07_ff_spread_comparison.png", dpi=200)
plt.close(fig)

print("wrote 06_ff_d1_d10_paths.png, 07_ff_spread_comparison.png")
