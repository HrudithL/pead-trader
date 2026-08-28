"""Build the options-PEAD report's charts as PNG files in reports/figures/."""
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
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "#444444",
    "axes.labelcolor": "#222222",
    "text.color": "#222222",
    "xtick.color": "#444444",
    "ytick.color": "#444444",
    "font.size": 10.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": "#e5e5e5",
    "grid.linewidth": 0.7,
})

decile_stats = pd.read_csv(DATA / "option_decile_horizon_stats.csv")
spread_stats = pd.read_csv(DATA / "option_spread_horizon_stats.csv")
cov = pd.read_csv(DATA / "option_coverage_stats.csv")
panel = pd.read_parquet(DATA / "event_options" / "option_event_panel.parquet")

cmap = plt.get_cmap("RdYlGn")
colors = {d: cmap(0.05 + 0.9 * (d - 1) / 9) for d in range(1, 11)}
N_TOTAL = panel["decile"].notna().sum()

# ---------- Charts 18/19: decile drift curves, call and put ----------
for i, cp in enumerate(["call", "put"], start=18):
    sub = decile_stats[decile_stats.cp_type == cp]
    pivot = sub.pivot(index="horizon", columns="decile", values="mean").sort_index()
    n_cp = int(sub.groupby("decile")["n_events"].max().sum())
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    for d in range(1, 11):
        if d not in pivot.columns:
            continue
        ax.plot(pivot.index, pivot[d] * 100, marker="o", markersize=3.5, linewidth=1.8,
                color=colors[d], label=f"D{d}")
    ax.axhline(0, color="#888888", linewidth=0.8)
    ax.set_xlabel("Trading days after announcement (day 0 = entry)")
    ax.set_ylabel(f"Cumulative near-ATM {cp} return (%)")
    label = "Call (long the underlying, leveraged)" if cp == "call" else "Put (short the underlying, leveraged)"
    ax.set_title(f"Options-Market PEAD: {label}\nDecile-sorted by SUE, 1996-2013 ex-2011, N={n_cp:,} {cp} positions at 1d", loc="left", fontsize=12)
    ax.legend(loc="upper left" if cp == "call" else "lower left", ncol=2, fontsize=8.5,
              frameon=False, title="Decile (D1=most negative surprise)")
    ax.set_xticks(pivot.index)
    fig.tight_layout()
    fig.savefig(FIG / f"{i}_option_{cp}_decile_drift.png", dpi=200)
    plt.close(fig)

# ---------- Chart 20: straddle (directionless) decile drift, as a robustness contrast ----------
sub = decile_stats[decile_stats.cp_type == "straddle"]
if len(sub):
    pivot = sub.pivot(index="horizon", columns="decile", values="mean").sort_index()
    n_cp = int(sub.groupby("decile")["n_events"].max().sum())
    fig_, ax = plt.subplots(figsize=(8.5, 5.2))
    for d in range(1, 11):
        if d not in pivot.columns:
            continue
        ax.plot(pivot.index, pivot[d] * 100, marker="o", markersize=3.5, linewidth=1.8,
                color=colors[d], label=f"D{d}")
    ax.axhline(0, color="#888888", linewidth=0.8)
    ax.set_xlabel("Trading days after announcement (day 0 = entry)")
    ax.set_ylabel("Cumulative straddle (call+put) return (%)")
    ax.set_title(f"Robustness Check: Straddle Return by Decile\n(directionless; should be flat across deciles if the call/put drift is real, N={n_cp:,})", loc="left", fontsize=11.5)
    ax.legend(loc="upper left", ncol=2, fontsize=8.5, frameon=False, title="Decile (D1=most negative surprise)")
    ax.set_xticks(pivot.index)
    fig_.tight_layout()
    fig_.savefig(FIG / "20_option_straddle_decile_drift.png", dpi=200)
    plt.close(fig_)

# ---------- Chart 20b: D10-D1 spread by horizon, call vs put ----------
fig, ax = plt.subplots(figsize=(8.2, 4.8))
width = 0.35
horizons = sorted(spread_stats["horizon"].unique())
x = np.arange(len(horizons))
for offset, cp, color in [(-width/2, "call", "#2f6f4f"), (width/2, "put", "#a83f3f")]:
    sub = spread_stats[spread_stats.cp_type == cp].set_index("horizon").loc[horizons]
    ax.bar(x + offset, sub["mean_spread"] * 100, width=width, color=color, label=cp.capitalize())
    ax.errorbar(x + offset, sub["mean_spread"] * 100, yerr=1.96 * sub["se"] * 100,
                fmt="none", ecolor="#1a1a1a", capsize=3, linewidth=1.0)
ax.axhline(0, color="#888888", linewidth=0.8)
ax.set_xticks(x)
ax.set_xticklabels([str(h) for h in horizons])
ax.set_xlabel("Horizon (trading days)")
ax.set_ylabel("D10 - D1 spread (%)")
ax.set_title("Options-Market PEAD Spread: Call vs. Put\nwith 95% CI, clustered by announcement quarter", loc="left", fontsize=12)
ax.legend(frameon=False, loc="upper left")
fig.tight_layout()
fig.savefig(FIG / "21_option_spread_call_vs_put.png", dpi=200)
plt.close(fig)

# ---------- Chart 21: equity vs option (call) spread side-by-side, same events ----------
eq_spread = pd.read_csv(DATA / "spread_horizon_stats.csv")
fig, ax = plt.subplots(figsize=(8.2, 4.8))
call_spread = spread_stats[spread_stats.cp_type == "call"].set_index("horizon").loc[horizons]
eq_sub = eq_spread.set_index("horizon").loc[horizons]
ax.bar(x - width/2, eq_sub["mean_spread"] * 100, width=width, color="#3f6fa8", label="Underlying stock")
ax.bar(x + width/2, call_spread["mean_spread"] * 100, width=width, color="#2f6f4f", label="Near-ATM call (leveraged)")
ax.set_xticks(x)
ax.set_xticklabels([str(h) for h in horizons])
ax.set_xlabel("Horizon (trading days)")
ax.set_ylabel("D10 - D1 spread (%)")
ax.set_title("PEAD Drift: Underlying Stock vs. Leveraged Option Exposure\n(same SUE deciles, full equity universe vs. option-matched subset)", loc="left", fontsize=11.5)
ax.legend(frameon=False, loc="upper left")
fig.tight_layout()
fig.savefig(FIG / "22_equity_vs_option_spread.png", dpi=200)
plt.close(fig)

# ---------- Chart 23: coverage over time ----------
entries = pd.read_parquet(DATA / "event_options" / "entry_contracts_all.parquet")
entries["year"] = entries["day0_date"].dt.year
events = pd.read_parquet(DATA / "event_options" / "decile_events_secid.parquet",
                          columns=["day0_date"])
events["year"] = events["day0_date"].dt.year
by_year = pd.DataFrame({
    "linkable_events": events.groupby("year").size(),
    "matched_events": entries.groupby("year").size(),
}).fillna(0)
fig, ax = plt.subplots(figsize=(8.2, 4.5))
ax.bar(by_year.index.astype(str), by_year["linkable_events"], color="#cccccc", label="Linked to a secid (in OM coverage window)")
ax.bar(by_year.index.astype(str), by_year["matched_events"], color="#2f6f4f", label="Matched to a valid near-ATM call or put")
ax.set_xlabel("Year")
ax.set_ylabel("Number of events")
ax.set_title("Options-Market Sample Coverage by Year\n(2011 excluded: no opprcd file in this OptionMetrics pull)", loc="left", fontsize=11.5)
ax.legend(frameon=False, loc="upper left")
plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
fig.tight_layout()
fig.savefig(FIG / "23_option_coverage_by_year.png", dpi=200)
plt.close(fig)

print("wrote figures 18-23 to", FIG)
