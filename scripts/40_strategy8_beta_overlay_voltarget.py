"""
Strategy 8 (dispersion filter, scripts/39) + the SAME vol-targeted 0.5x market beta overlay
construction used in scripts/33_strategy6_beta_overlay_voltarget.py -- combining this session's
two positive findings (a shallower-drawdown alpha book, and a smarter way to size the beta
overlay) to see whether the drawdown improvements compound or just overlap.

Reuses the exact market vol-targeting scalar already computed in scripts/33's output
(data/backtest_v2_strategy6_beta050_voltarget.csv's `vol_scalar` column) rather than recomputing
it -- that scalar is a pure function of trailing market volatility (vwretd), not of which alpha
book it's paired with, so reusing it keeps this directly comparable to scripts/33's result and
avoids a second, potentially-inconsistent derivation of the same quantity.

Output: data/backtest_v2_strategy8_beta050_voltarget.csv,
        data/backtest_v2_strategy8_beta050_voltarget_summary.json
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data/normalized_equity")

INITIAL_CAPITAL = 10_000_000.0
BETA_TARGET = 0.5
OVERLAY_COST_BPS = 2.0

strat = pd.read_csv(DATA / "backtest_v2_strategy8.csv", parse_dates=["date"])
overlay_src = pd.read_csv(DATA / "backtest_v2_strategy6_beta050_voltarget.csv", parse_dates=["date"])
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
mkt["date"] = pd.to_datetime(mkt["date"])

df = strat[["date", "nav"]].merge(overlay_src[["date", "vol_scalar"]], on="date", how="left")
df["vol_scalar"] = df["vol_scalar"].fillna(1.0)
df = df.merge(mkt, on="date", how="left")
df["vwretd"] = df["vwretd"].fillna(0.0)
df["alpha_true_ret"] = df["nav"].pct_change().fillna(0.0)
df["quarter"] = df["date"].dt.to_period("Q")

nav_arr = df["nav"].to_numpy()
alpha_pnl = np.diff(nav_arr, prepend=INITIAL_CAPITAL)
vwretd = df["vwretd"].to_numpy()
quarters = df["quarter"].to_numpy()
vol_scalar_arr = df["vol_scalar"].to_numpy()

combined_nav = np.zeros(len(df))
overlay_notional_series = np.zeros(len(df))
scalar_series = np.zeros(len(df))
daily_pnl = np.zeros(len(df))
running_nav = INITIAL_CAPITAL
overlay_notional = 0.0
current_scalar = 1.0
prev_q = None

for i in range(len(df)):
    q = quarters[i]
    if q != prev_q:
        current_scalar = vol_scalar_arr[i]
        new_overlay_notional = current_scalar * BETA_TARGET * running_nav
        rebalance_cost = abs(new_overlay_notional - overlay_notional) * (OVERLAY_COST_BPS / 10_000.0)
        running_nav -= rebalance_cost
        overlay_notional = new_overlay_notional
        prev_q = q
    overlay_pnl_today = overlay_notional * vwretd[i]
    total_pnl_today = alpha_pnl[i] + overlay_pnl_today
    running_nav += total_pnl_today
    combined_nav[i] = running_nav
    overlay_notional_series[i] = overlay_notional
    scalar_series[i] = current_scalar
    daily_pnl[i] = total_pnl_today

combined_ret = np.diff(combined_nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], combined_nav[:-1]])

out = pd.DataFrame({
    "date": df["date"], "daily_pnl": daily_pnl, "nav": combined_nav,
    "overlay_notional": overlay_notional_series, "vol_scalar": scalar_series,
    "daily_return": combined_ret,
})
out.to_csv(DATA / "backtest_v2_strategy8_beta050_voltarget.csv", index=False)

years = len(df) / 252.0
total_ret = (combined_nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
ann_ret = (1 + total_ret) ** (1 / years) - 1
ann_vol = pd.Series(combined_ret).std(ddof=1) * np.sqrt(252)
sharpe = (pd.Series(combined_ret).mean() * 252) / ann_vol
running_max = np.maximum.accumulate(combined_nav)
max_dd = ((combined_nav - running_max) / running_max).min()

summary = dict(
    name="strategy8_beta050_voltarget_overlay", beta_target=BETA_TARGET,
    overlay_cost_bps=OVERLAY_COST_BPS, annualized_return=ann_ret, annualized_vol=ann_vol,
    sharpe=sharpe, max_drawdown=max_dd, avg_vol_scalar=float(np.mean(scalar_series)),
    final_nav=float(combined_nav[-1]),
)
with open(DATA / "backtest_v2_strategy8_beta050_voltarget_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"strategy8 + vol-targeted beta overlay: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} "
      f"Sharpe={sharpe:.3f} maxDD={max_dd:.2%} final_nav=${combined_nav[-1]:,.0f}")
print("vs. strategy6-fully-vt (11.58% / 1.06 / -17.9%) and strategy8 alone (5.96% / 1.30 / -10.8%)")
