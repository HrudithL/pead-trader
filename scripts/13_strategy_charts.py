"""Charts for the strategy backtest section."""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

DATA = Path("/root/pead_report/data")
FIG = Path("/root/pead_report/figures")

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.edgecolor": "#444444", "axes.labelcolor": "#222222", "text.color": "#222222",
    "xtick.color": "#444444", "ytick.color": "#444444", "font.size": 10.5,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#e5e5e5", "grid.linewidth": 0.7,
})

STRATS = [("extreme", "Extreme decile L/S", "#2f6f4f"),
          ("rankweighted", "Rank-weighted", "#3f6fa8"),
          ("balanced", "Extreme decile, size-balanced", "#a85f3f")]

# ---------- Chart 8: NAV curves ----------
fig, ax = plt.subplots(figsize=(8.2, 4.8))
for key, label, color in STRATS:
    df = pd.read_csv(DATA / f"backtest_{key}.csv", parse_dates=["date"])
    ax.plot(df["date"], df["nav"] / 1e6, linewidth=1.6, color=color, label=label)
ax.axhline(10, color="#888888", linewidth=0.8, linestyle="--")
ax.set_xlabel("Date")
ax.set_ylabel("NAV ($M, on $10M fixed capital base)")
ax.set_title("Strategy NAV Curves, 1996-2013\n(net of transaction costs and liquidity caps)",
             loc="left", fontsize=12)
ax.legend(loc="upper left", fontsize=9, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "08_nav_curves.png", dpi=200)
plt.close(fig)

# ---------- Chart 9: drawdown ----------
fig, ax = plt.subplots(figsize=(8.2, 3.6))
for key, label, color in STRATS:
    df = pd.read_csv(DATA / f"backtest_{key}.csv", parse_dates=["date"])
    running_max = df["nav"].cummax()
    dd = (df["nav"] - running_max) / running_max * 100
    ax.fill_between(df["date"], dd, 0, color=color, alpha=0.35, label=label)
    ax.plot(df["date"], dd, linewidth=0.8, color=color)
ax.set_xlabel("Date")
ax.set_ylabel("Drawdown from prior peak (%)")
ax.set_title("Drawdowns", loc="left", fontsize=12)
ax.legend(loc="lower left", fontsize=9, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "09_drawdowns.png", dpi=200)
plt.close(fig)

# ---------- Chart 10: concurrent open positions (portfolio activity) ----------
fig, ax = plt.subplots(figsize=(8.2, 3.4))
for key, label, color in STRATS:
    df = pd.read_csv(DATA / f"backtest_{key}.csv", parse_dates=["date"])
    ax.plot(df["date"], df["n_open_positions"], linewidth=1.0, color=color, label=label)
ax.set_xlabel("Date")
ax.set_ylabel("Concurrently open positions")
ax.set_title("Portfolio Activity: Number of Open Positions Over Time", loc="left", fontsize=12)
ax.legend(loc="upper left", fontsize=9, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "10_open_positions.png", dpi=200)
plt.close(fig)

# ---------- Chart 11: decay curves (marginal return by decile) ----------
marg = pd.read_csv(DATA / "ff_marginal_returns.csv")
fig, ax = plt.subplots(figsize=(8.2, 4.8))
cmap = plt.get_cmap("RdYlGn")
for d in range(1, 11):
    sub = marg[marg["decile"] == d].sort_values("horizon")
    color = cmap(0.05 + 0.9 * (d - 1) / 9)
    ax.plot(sub["horizon"], sub["marginal_per_day"] * 10000, marker="o", markersize=3,
            linewidth=1.6, color=color, label=f"D{d}")
ax.axhline(0, color="#888888", linewidth=0.8)
ax.set_xlabel("Horizon (trading days)")
ax.set_ylabel("Marginal return per additional day (bps), size+BM adjusted")
ax.set_title("Where Does Each Decile's Drift Stop Paying? (Decay Curves)", loc="left", fontsize=12)
ax.legend(loc="upper right", ncol=2, fontsize=8, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "11_decay_curves.png", dpi=200)
plt.close(fig)

print("wrote 08_nav_curves.png, 09_drawdowns.png, 10_open_positions.png, 11_decay_curves.png")
