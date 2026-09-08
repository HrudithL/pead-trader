"""Aggressive variant of Strategy 6 + market beta overlay: same mechanism as
25_strategy6_beta_overlay.py (Strategy 6's alpha book left completely untouched, a separate
synthetic index position sized to BETA_TARGET x trailing NAV held alongside it, 2bps overlay
cost, quarterly rebalance) applied on top of STRATEGY 6 AGGRESSIVE's own NAV series (from
36_aggressive_variants.py) instead of baseline Strategy 6 -- i.e. both dials this project has ever
used to add more return (bigger/more-levered alpha book, bigger beta overlay) pushed together,
each already justified independently.

BETA_TARGET is swept and the same selection rule as script 36 applies: maximize annualized return
subject to Sharpe >= 0.6 AND max_drawdown >= -40%. Requires 36_aggressive_variants.py to have been
run first (reads its backtest_v2_strategy6_aggressive.csv output).

Output: data/backtest_v2_strategy6_beta_aggressive.csv,
data/backtest_v2_strategy6_beta_aggressive_summary.json, data/sweep_aggressive_beta_overlay.csv,
and appends a row to data/results_summary_v2_aggressive_FINAL.csv.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, RAW_EQUITY_DIR, INITIAL_CAPITAL

DATA = DATA_DIR
RAW = RAW_EQUITY_DIR
OVERLAY_COST_BPS = 2.0
MIN_SHARPE = 0.6
MAX_DRAWDOWN_FLOOR = -0.40
BETA_TARGETS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]

alpha_path = DATA / "backtest_v2_strategy6_aggressive.csv"
if not alpha_path.exists():
    raise SystemExit("run 36_aggressive_variants.py first -- backtest_v2_strategy6_aggressive.csv not found")

strat = pd.read_csv(alpha_path, parse_dates=["date"])
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
mkt["date"] = pd.to_datetime(mkt["date"])
df = strat.merge(mkt, on="date", how="left")
df["vwretd"] = df["vwretd"].fillna(0.0)
df["quarter"] = df["date"].dt.to_period("Q")

nav_arr = df["nav"].to_numpy()
alpha_pnl = np.diff(nav_arr, prepend=INITIAL_CAPITAL)
vwretd = df["vwretd"].to_numpy()
quarters = df["quarter"].to_numpy()


def run_overlay(beta_target):
    combined_nav = np.zeros(len(df))
    daily_pnl = np.zeros(len(df))
    trailing_nav_running = INITIAL_CAPITAL
    overlay_notional = 0.0
    prev_q = None
    for i in range(len(df)):
        q = quarters[i]
        if q != prev_q:
            new_overlay_notional = beta_target * trailing_nav_running
            rebalance_cost = abs(new_overlay_notional - overlay_notional) * (OVERLAY_COST_BPS / 10_000.0)
            trailing_nav_running -= rebalance_cost
            overlay_notional = new_overlay_notional
            prev_q = q
        overlay_pnl_today = overlay_notional * vwretd[i]
        total_pnl_today = alpha_pnl[i] + overlay_pnl_today
        trailing_nav_running += total_pnl_today
        combined_nav[i] = trailing_nav_running
        daily_pnl[i] = total_pnl_today

    combined_ret = np.diff(combined_nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], combined_nav[:-1]])
    years = len(df) / 252.0
    total_ret = (combined_nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    if combined_nav.min() <= 0 or total_ret <= -1.0:
        ann_ret = -1.0
    else:
        ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = pd.Series(combined_ret).std(ddof=1) * np.sqrt(252)
    sharpe = (pd.Series(combined_ret).mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(combined_nav)
    max_dd = ((combined_nav - running_max) / running_max).min()
    return dict(beta_target=beta_target, annualized_return=float(ann_ret), annualized_vol=float(ann_vol),
                sharpe=float(sharpe), max_drawdown=float(max_dd), final_nav=float(combined_nav[-1])), \
        combined_nav, daily_pnl, combined_ret


rows = []
cache = {}
for bt in BETA_TARGETS:
    summary, nav, daily_pnl, ret = run_overlay(bt)
    rows.append(summary)
    cache[bt] = (summary, nav, daily_pnl, ret)
sweep = pd.DataFrame(rows)
sweep.to_csv(DATA / "sweep_aggressive_beta_overlay.csv", index=False)

viable = sweep[(sweep["sharpe"] >= MIN_SHARPE) & (sweep["max_drawdown"] >= MAX_DRAWDOWN_FLOOR)]
if len(viable):
    winner_row = viable.loc[viable["annualized_return"].idxmax()]
else:
    safe = sweep[sweep["max_drawdown"] >= MAX_DRAWDOWN_FLOOR]
    winner_row = (safe if len(safe) else sweep.nsmallest(1, "beta_target")).loc[
        (safe if len(safe) else sweep.nsmallest(1, "beta_target"))["sharpe"].idxmax()]
beta_final = float(winner_row["beta_target"])
summary, nav, daily_pnl, ret = cache[beta_final]
print(f"picked beta_target={beta_final}x: ann.ret={summary['annualized_return']:.2%} "
      f"ann.vol={summary['annualized_vol']:.2%} Sharpe={summary['sharpe']:.3f} "
      f"maxDD={summary['max_drawdown']:.2%} final_nav=${summary['final_nav']:,.0f}")

out = pd.DataFrame({"date": df["date"], "daily_pnl": daily_pnl, "nav": nav, "daily_return": ret})
out.to_csv(DATA / "backtest_v2_strategy6_beta_aggressive.csv", index=False)
summary_full = dict(name="strategy6_beta_aggressive_overlay",
                     overlay_cost_bps=OVERLAY_COST_BPS, **summary)
with open(DATA / "backtest_v2_strategy6_beta_aggressive_summary.json", "w") as f:
    json.dump(summary_full, f, indent=2)

# ---- append to the aggressive results table ----
agg_path = DATA / "results_summary_v2_aggressive_FINAL.csv"
agg = pd.read_csv(agg_path)
agg = agg[agg["strategy"] != "strategy6_beta_aggressive"]
new_row = pd.DataFrame([dict(strategy="strategy6_beta_aggressive",
                              label="Strategy 6 + Beta Overlay -- Aggressive",
                              ann_return=summary["annualized_return"], ann_vol=summary["annualized_vol"],
                              sharpe=summary["sharpe"], max_drawdown=summary["max_drawdown"],
                              final_nav=summary["final_nav"])])
agg = pd.concat([agg, new_row], ignore_index=True)
agg.to_csv(agg_path, index=False)
print(f"appended strategy6_beta_aggressive to {agg_path} ({len(agg)} rows total)")
