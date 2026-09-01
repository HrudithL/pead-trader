"""
Strategy 6 + 0.5x market beta overlay: a proper implementation, not the additive approximation
used earlier to size up the idea. The existing Strategy 6 alpha book (sector+size neutral,
trim=0.15, tilt=1.0, base_unit_fraction=0.003, leverage cap 2.5x) is left completely unchanged.
On top of it, a SEPARATE synthetic index position is held at all times, sized to
BETA_TARGET (0.5) x trailing NAV, rebalanced on the SAME quarterly cadence as the alpha book's
own trailing-NAV resizing (so the two stay consistent and the whole portfolio compounds off one
NAV series). The overlay's daily P&L is its notional x that day's market return (vwretd) -- this
is what actually holding an S&P 500 future or a broad index ETF alongside the alpha book would
produce. A small transaction cost (2bps one-way, well within normal cost for a large, liquid
index product) is charged on the overlay's quarterly rebalance turnover.

This is mechanically identical to what a real 130/30-style long-extension fund does: the alpha
book stays untouched (so the earnings-surprise signal is not distorted), and market exposure is
layered on top as an independent position.

Output: data/backtest_v2_strategy6_beta050.csv, data/backtest_v2_strategy6_beta050_summary.json
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
BETA_TARGET = 0.5
OVERLAY_COST_BPS = 2.0  # one-way, a large liquid index future/ETF -- much cheaper than single names

strat = pd.read_csv(DATA / "backtest_v2_strategy6.csv", parse_dates=["date"])
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
mkt["date"] = pd.to_datetime(mkt["date"])
df = strat.merge(mkt, on="date", how="left")
df["vwretd"] = df["vwretd"].fillna(0.0)

# the alpha book's own TRUE daily percentage return (see note in-conversation: the stored
# `daily_return` column is P&L / fixed initial capital, not a true compounding return -- use
# nav.pct_change() instead, which correctly reflects the actual compounding series)
df["alpha_true_ret"] = df["nav"].pct_change().fillna(0.0)
df["quarter"] = df["date"].dt.to_period("Q")
unique_quarters = df["quarter"].unique()

# quarterly trailing-NAV cadence for the overlay: at the start of quarter q, size the overlay off
# the COMBINED (alpha + overlay) portfolio's trailing NAV as of the end of quarter q-1 -- same
# no-same-day-feedback discipline as the rest of the project.
combined_nav = np.zeros(len(df))
overlay_notional_series = np.zeros(len(df))
trailing_nav = INITIAL_CAPITAL
overlay_notional = 0.0
prev_q = None
daily_pnl = np.zeros(len(df))

nav_arr = df["nav"].to_numpy()  # alpha-only nav, used only to recover the alpha book's OWN pnl
alpha_pnl = np.diff(nav_arr, prepend=INITIAL_CAPITAL)
vwretd = df["vwretd"].to_numpy()
quarters = df["quarter"].to_numpy()

running_nav = INITIAL_CAPITAL
for i in range(len(df)):
    q = quarters[i]
    if q != prev_q:
        # quarterly rebalance: resize the overlay off the portfolio's own trailing NAV, and pay
        # the small turnover cost on the CHANGE in overlay notional
        new_overlay_notional = BETA_TARGET * running_nav
        rebalance_cost = abs(new_overlay_notional - overlay_notional) * (OVERLAY_COST_BPS / 10_000.0)
        running_nav -= rebalance_cost
        overlay_notional = new_overlay_notional
        prev_q = q
    overlay_pnl_today = overlay_notional * vwretd[i]
    total_pnl_today = alpha_pnl[i] + overlay_pnl_today
    running_nav += total_pnl_today
    combined_nav[i] = running_nav
    overlay_notional_series[i] = overlay_notional
    daily_pnl[i] = total_pnl_today

combined_ret = np.diff(combined_nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], combined_nav[:-1]])

out = pd.DataFrame({
    "date": df["date"], "daily_pnl": daily_pnl, "nav": combined_nav,
    "overlay_notional": overlay_notional_series, "daily_return": combined_ret,
})
out.to_csv(DATA / "backtest_v2_strategy6_beta050.csv", index=False)

years = len(df) / 252.0
total_ret = (combined_nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
ann_ret = (1 + total_ret) ** (1 / years) - 1
ann_vol = pd.Series(combined_ret).std(ddof=1) * np.sqrt(252)
sharpe = (pd.Series(combined_ret).mean() * 252) / ann_vol
running_max = np.maximum.accumulate(combined_nav)
max_dd = ((combined_nav - running_max) / running_max).min()

summary = dict(
    name="strategy6_beta050_overlay", beta_target=BETA_TARGET,
    overlay_cost_bps=OVERLAY_COST_BPS, annualized_return=ann_ret, annualized_vol=ann_vol,
    sharpe=sharpe, max_drawdown=max_dd, final_nav=float(combined_nav[-1]),
)
with open(DATA / "backtest_v2_strategy6_beta050_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"strategy6 + 0.5x beta overlay: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} "
      f"Sharpe={sharpe:.3f} maxDD={max_dd:.2%} final_nav=${combined_nav[-1]:,.0f}")
