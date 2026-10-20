"""
Strategy 6 + a volatility-TARGETED (rather than static) 0.5x market beta overlay. Same
quarterly-rebalance, separate-synthetic-index-position construction as
scripts/25_strategy6_beta_overlay.py (the alpha book is completely untouched) -- the only change
is that the overlay's notional is no longer pinned to a fixed BETA_TARGET x trailing NAV every
quarter. Instead it's scaled by TARGET_VOL / trailing_realized_market_vol each quarter
(Moreira & Muir-style volatility-managed exposure -- the same "130/30 + risk-managed beta"
literature already cited for the static overlay), clipped to [MIN_SCALAR, MAX_SCALAR] so it can't
delever to near-zero in a crisis (defeating the point of holding the overlay at all) or lever up
unrealistically in an unusually calm quarter.

The day-to-day scaling decision only ever uses market volatility realized strictly before the
quarter it applies to (rolling 63-trading-day std, shifted one day, sampled once at each
quarter's first day) -- no lookahead in that decision. TARGET_VOL itself is set to the
full-sample average of that trailing-vol series, so the overlay's AVERAGE notional over the whole
backtest matches the static design's 0.5x almost exactly -- this keeps the comparison to the
static overlay a genuine risk-timing question (same average exposure, redistributed across time)
rather than a comparison against a different average market exposure. Calibrating the target
level to the sample's own average realized vol is a standard simplification in this literature
(Moreira & Muir do the same); it fixes the LEVEL of the target using the full sample, not the
day-to-day scaling decision, which is disclosed here rather than presented as an
out-of-sample-safe choice.

Output: data/backtest_v2_strategy6_beta050_voltarget.csv,
        data/backtest_v2_strategy6_beta050_voltarget_summary.json
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
# Untracked WRDS-derived data lives only in the main checkout on this machine, not in this
# worktree (see .gitignore) -- absolute path rather than duplicating ~1.3GB of raw data.
RAW = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data/normalized_equity")

INITIAL_CAPITAL = 10_000_000.0
BETA_TARGET = 0.5
OVERLAY_COST_BPS = 2.0
VOL_LOOKBACK_DAYS = 63
MIN_SCALAR, MAX_SCALAR = 0.5, 2.0

strat = pd.read_csv(DATA / "backtest_v2_strategy6.csv", parse_dates=["date"])
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
mkt["date"] = pd.to_datetime(mkt["date"])
df = strat.merge(mkt, on="date", how="left")
df["vwretd"] = df["vwretd"].fillna(0.0)

# the alpha book's own TRUE daily percentage return (see note in scripts/25: the stored
# `daily_return` column is P&L / fixed initial capital, not a true compounding return -- use
# nav.pct_change() instead)
df["alpha_true_ret"] = df["nav"].pct_change().fillna(0.0)
df["quarter"] = df["date"].dt.to_period("Q")

# ---------- market vol-targeting scalar, trailing-only, sampled at each quarter's start ----------
trailing_vol = df["vwretd"].rolling(VOL_LOOKBACK_DAYS, min_periods=VOL_LOOKBACK_DAYS).std() * np.sqrt(252)
trailing_vol = trailing_vol.shift(1)  # strictly prior-day information only
TARGET_VOL = trailing_vol.mean(skipna=True)
scalar_raw = TARGET_VOL / trailing_vol
scalar = scalar_raw.clip(MIN_SCALAR, MAX_SCALAR).fillna(1.0)  # 1.0 (static-equivalent) before a
                                                                # full lookback window exists
df["vol_scalar"] = scalar.to_numpy()

nav_arr = df["nav"].to_numpy()  # alpha-only nav, used only to recover the alpha book's OWN pnl
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
out.to_csv(DATA / "backtest_v2_strategy6_beta050_voltarget.csv", index=False)

years = len(df) / 252.0
total_ret = (combined_nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
ann_ret = (1 + total_ret) ** (1 / years) - 1
ann_vol = pd.Series(combined_ret).std(ddof=1) * np.sqrt(252)
sharpe = (pd.Series(combined_ret).mean() * 252) / ann_vol
running_max = np.maximum.accumulate(combined_nav)
max_dd = ((combined_nav - running_max) / running_max).min()

summary = dict(
    name="strategy6_beta050_voltarget_overlay", beta_target=BETA_TARGET,
    vol_lookback_days=VOL_LOOKBACK_DAYS, target_vol=float(TARGET_VOL),
    min_scalar=MIN_SCALAR, max_scalar=MAX_SCALAR, overlay_cost_bps=OVERLAY_COST_BPS,
    annualized_return=ann_ret, annualized_vol=ann_vol, sharpe=sharpe, max_drawdown=max_dd,
    avg_vol_scalar=float(np.mean(scalar_series)), final_nav=float(combined_nav[-1]),
)
with open(DATA / "backtest_v2_strategy6_beta050_voltarget_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"strategy6 + vol-targeted beta overlay: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} "
      f"Sharpe={sharpe:.3f} maxDD={max_dd:.2%} avg_scalar={summary['avg_vol_scalar']:.2f} "
      f"final_nav=${combined_nav[-1]:,.0f}")
