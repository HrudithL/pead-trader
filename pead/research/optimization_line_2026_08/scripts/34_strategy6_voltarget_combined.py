"""
Strategy 6, fully volatility-managed: vol-targets the alpha book's OWN exposure the same way
scripts/33_strategy6_beta_overlay_voltarget.py vol-targets the beta overlay, then recombines with
that same vol-targeted overlay into one fully-managed portfolio.

IMPORTANT APPROXIMATION, disclosed rather than hidden: the alpha book's true position-level
engine (per-name liquidity caps, priority-based leverage trimming -- see
scripts/24_finalize_strategy6.py) is NOT re-run here. Re-sizing the actual book at different
notionals requires the position-level data regenerated in scripts/35_strategy6_sizing_sweep.py's
pipeline; this script instead scales the alpha book's own already-computed daily RETURN series
(data/backtest_v2_strategy6.csv) by a quarterly vol-targeting factor. That's mathematically
equivalent to uniformly resizing every position that quarter -- a reasonable stand-in given what
data is available, but it does NOT capture how the liquidity cap or priority-based trimming would
actually respond at a different size, and it does NOT charge any extra rebalance transaction cost
for the implied resizing itself (the existing per-position entry/exit costs are already baked
into the return series being scaled; this scaling adds no further cost on top of them). Treat a
future true position-level re-run as the more reliable number, and expect it to show a somewhat
worse net return than this approximation because of that missing cost.

Produces three variants, all run through the same combined alpha+overlay simulation:
  1. "static_alpha_static_overlay" -- reproduces scripts/25's original static/static result, as an
     internal correctness check on this script's combined-portfolio loop (see printed check below).
  2. "voltarget_alpha_static_overlay" -- isolates the effect of vol-targeting the alpha book alone.
  3. "voltarget_alpha_voltarget_overlay" -- the fully-managed candidate, compared against today's
     static/static baseline (10.08% return, Sharpe 0.97, max DD -23.2%) and against scripts/33's
     vol-targeted-overlay-only result (11.00%, Sharpe 1.03, max DD -18.5%).

Output: data/backtest_v2_strategy6_voltarget_variants_summary.json (all three variants),
        data/backtest_v2_strategy6_fully_voltarget.csv (variant 3's daily NAV series)
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
VOL_LOOKBACK_DAYS = 63
MIN_SCALAR, MAX_SCALAR = 0.5, 2.0

strat = pd.read_csv(DATA / "backtest_v2_strategy6.csv", parse_dates=["date"])
overlay_src = pd.read_csv(DATA / "backtest_v2_strategy6_beta050_voltarget.csv", parse_dates=["date"])

df = strat[["date", "nav"]].merge(overlay_src[["date", "vol_scalar"]], on="date", how="left")
df = df.rename(columns={"vol_scalar": "market_vol_scalar"})
df["market_vol_scalar"] = df["market_vol_scalar"].fillna(1.0)
df["alpha_true_ret"] = df["nav"].pct_change().fillna(0.0)
df["quarter"] = df["date"].dt.to_period("Q")

mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
mkt["date"] = pd.to_datetime(mkt["date"])
df = df.merge(mkt, on="date", how="left")
df["vwretd"] = df["vwretd"].fillna(0.0)

# ---------- alpha book's OWN vol-targeting scalar (mirrors script 33's construction exactly,
# just measured off the alpha book's own trailing realized vol instead of the market's) ----------
alpha_trailing_vol = df["alpha_true_ret"].rolling(VOL_LOOKBACK_DAYS, min_periods=VOL_LOOKBACK_DAYS).std() * np.sqrt(252)
alpha_trailing_vol = alpha_trailing_vol.shift(1)
ALPHA_TARGET_VOL = alpha_trailing_vol.mean(skipna=True)
alpha_scalar_raw = ALPHA_TARGET_VOL / alpha_trailing_vol
alpha_scalar = alpha_scalar_raw.clip(MIN_SCALAR, MAX_SCALAR).fillna(1.0)
df["alpha_vol_scalar"] = alpha_scalar.to_numpy()

quarters = df["quarter"].to_numpy()
vwretd = df["vwretd"].to_numpy()
market_vol_scalar_arr = df["market_vol_scalar"].to_numpy()
alpha_vol_scalar_arr = df["alpha_vol_scalar"].to_numpy()
alpha_true_ret_arr = df["alpha_true_ret"].to_numpy()


def build_alpha_pnl(use_voltarget):
    """Rebuild the alpha book's own dollar P&L series, optionally rescaled quarterly by the
    alpha vol-targeting scalar (sampled once per quarter at the rebalance day, matching the
    project's quarterly no-same-day-feedback cadence elsewhere)."""
    scaled_ret = np.zeros(len(df))
    current_scalar = 1.0
    prev_q = None
    for i in range(len(df)):
        if quarters[i] != prev_q:
            current_scalar = alpha_vol_scalar_arr[i] if use_voltarget else 1.0
            prev_q = quarters[i]
        scaled_ret[i] = current_scalar * alpha_true_ret_arr[i]
    nav = INITIAL_CAPITAL * np.cumprod(1 + scaled_ret)
    pnl = np.diff(nav, prepend=INITIAL_CAPITAL)
    return pnl


def run_combined(alpha_pnl, overlay_scalar_arr):
    """Same combined alpha+overlay simulation as scripts/25 and /33, parameterized on which
    alpha pnl series and which overlay vol-scalar series (all-ones == static) to use."""
    combined_nav = np.zeros(len(df))
    daily_pnl = np.zeros(len(df))
    running_nav = INITIAL_CAPITAL
    overlay_notional = 0.0
    prev_q = None
    for i in range(len(df)):
        if quarters[i] != prev_q:
            new_overlay_notional = overlay_scalar_arr[i] * BETA_TARGET * running_nav
            rebalance_cost = abs(new_overlay_notional - overlay_notional) * (OVERLAY_COST_BPS / 10_000.0)
            running_nav -= rebalance_cost
            overlay_notional = new_overlay_notional
            prev_q = quarters[i]
        overlay_pnl_today = overlay_notional * vwretd[i]
        total_pnl_today = alpha_pnl[i] + overlay_pnl_today
        running_nav += total_pnl_today
        combined_nav[i] = running_nav
        daily_pnl[i] = total_pnl_today
    combined_ret = np.diff(combined_nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], combined_nav[:-1]])
    return combined_nav, daily_pnl, combined_ret


def summarize(name, combined_nav, combined_ret, extra=None):
    years = len(df) / 252.0
    total_ret = (combined_nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = pd.Series(combined_ret).std(ddof=1) * np.sqrt(252)
    sharpe = (pd.Series(combined_ret).mean() * 252) / ann_vol
    running_max = np.maximum.accumulate(combined_nav)
    max_dd = ((combined_nav - running_max) / running_max).min()
    s = dict(name=name, annualized_return=float(ann_ret), annualized_vol=float(ann_vol),
              sharpe=float(sharpe), max_drawdown=float(max_dd), final_nav=float(combined_nav[-1]))
    if extra:
        s.update(extra)
    return s


static_alpha_pnl = build_alpha_pnl(use_voltarget=False)
voltarget_alpha_pnl = build_alpha_pnl(use_voltarget=True)
ones = np.ones(len(df))

nav1, pnl1, ret1 = run_combined(static_alpha_pnl, ones)
nav2, pnl2, ret2 = run_combined(voltarget_alpha_pnl, ones)
nav3, pnl3, ret3 = run_combined(voltarget_alpha_pnl, market_vol_scalar_arr)

results = [
    summarize("static_alpha_static_overlay", nav1, ret1),
    summarize("voltarget_alpha_static_overlay", nav2, ret2,
              extra=dict(alpha_target_vol=float(ALPHA_TARGET_VOL),
                         avg_alpha_scalar=float(df["alpha_vol_scalar"].mean()))),
    summarize("voltarget_alpha_voltarget_overlay", nav3, ret3,
              extra=dict(alpha_target_vol=float(ALPHA_TARGET_VOL),
                         avg_alpha_scalar=float(df["alpha_vol_scalar"].mean()),
                         avg_market_scalar=float(df["market_vol_scalar"].mean()))),
]

with open(DATA / "backtest_v2_strategy6_voltarget_variants_summary.json", "w") as f:
    json.dump(results, f, indent=2)

# Individually-named CSV/summary files for variants 2 and 3, matching this project's
# backtest_v2_<key>.csv / backtest_v2_<key>_summary.json convention (scripts/29 and /30 read
# strategies this way) -- variant 1 reproduces the already-tracked strategy6_beta050 result
# (see the printed check below) so it isn't re-published as a separate entry.
out2 = pd.DataFrame({"date": df["date"], "daily_pnl": pnl2, "nav": nav2, "daily_return": ret2})
out2.to_csv(DATA / "backtest_v2_strategy6_voltarget_alpha_staticoverlay.csv", index=False)
with open(DATA / "backtest_v2_strategy6_voltarget_alpha_staticoverlay_summary.json", "w") as f:
    json.dump(results[1], f, indent=2)

out3 = pd.DataFrame({"date": df["date"], "daily_pnl": pnl3, "nav": nav3, "daily_return": ret3})
out3.to_csv(DATA / "backtest_v2_strategy6_fully_voltarget.csv", index=False)
with open(DATA / "backtest_v2_strategy6_fully_voltarget_summary.json", "w") as f:
    json.dump(results[2], f, indent=2)

for r in results:
    print(f"{r['name']}: ann.ret={r['annualized_return']:.2%} ann.vol={r['annualized_vol']:.2%} "
          f"Sharpe={r['sharpe']:.3f} maxDD={r['max_drawdown']:.2%} final_nav=${r['final_nav']:,.0f}")
print("\n[check] variant 1 should closely match the tracked static/static result "
      "(10.08% / Sharpe 0.97 / -23.2% DD) as a correctness check on this script's combined loop.")
