"""
Turn each strategy's position list into a calendar-time NAV curve.

Design choices, made explicit to avoid the compounding bug found in this project's earlier
(now-discarded) prototype, whose NAV inflated to $1.05 trillion from a $1M start:

  - Position sizing is FIXED-NOTIONAL: every position's dollar size is
    weight * BASE_UNIT_NOTIONAL, capped by a liquidity limit (see below). It is NEVER a
    percentage of the CURRENT, live-compounding NAV. This is the direct fix for the old
    bug (sizing off a growing NAV creates runaway leverage as soon as the strategy has any
    winning streak). The tradeoff, disclosed here: this reports return on a fixed capital
    base, not a compounding fund NAV -- a real fund would periodically resize its base
    notional off a TRAILING (not same-day) NAV, which is a natural refinement but adds
    complexity this first pass intentionally defers.
  - Daily P&L per open position is computed as notional * that day's market-adjusted return
    (simple, not compounded within the position) -- a standard, minor approximation for
    short holding windows (20-80 trading days here) with ordinary daily volatility.
  - Liquidity cap: a position's notional cannot exceed 1% of the stock's own 21-day average
    dollar volume at entry (data already computed: avg_dollar_volume_21d). Where this binds,
    the position is scaled down, not dropped -- flagged via `liquidity_capped`.
  - Transaction costs: a one-way cost in basis points, tiered by liquidity_quintile
    (1=least liquid -> 25bps, 5=most liquid -> 5bps), charged on entry AND exit (so a full
    round trip pays 2x the one-way rate). This has no empirical calibration beyond being a
    reasonable, disclosed, conservative-leaning assumption -- not fitted to any real broker
    or venue data, since none is available in this project.

Output per strategy: data/backtest_<name>.csv (daily NAV/exposure series),
                      data/backtest_<name>_summary.json (headline metrics)
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json

DATA = Path("/root/pead_report/data")
RAW = Path("/mnt/user-data/uploads/PEAD_Trading/data/normalized_equity")

BASE_UNIT_NOTIONAL = 10_000.0     # $ per unit of |weight| = 1
FIXED_CAPITAL = 10_000_000.0      # reference capital base for return-on-capital calcs
LIQUIDITY_CAP_FRAC = 0.01         # max position notional as a fraction of 21d avg $ volume
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}  # one-way, by liquidity_quintile
DEFAULT_COST_BPS = 15             # used when liquidity_quintile is missing

print("loading daily market-adjusted return panel...")
frames = []
for y in range(1995, 2015):
    frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date", "ret"]))
eq = pd.concat(frames, ignore_index=True)
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
eq = eq.merge(mkt, on="date", how="left")
eq["ret_mktadj"] = eq["ret"] - eq["vwretd"]
# NOTE: row indexing must exactly match 11_positions.py's eq construction (permno+date
# dedup only, no dropna) so entry_row_idx/exit_row_idx computed there stay valid here.
# Missing daily returns (e.g. a halt) contribute 0 P&L for that day rather than being
# dropped, which would shift every later row's index.
eq["ret_mktadj"] = eq["ret_mktadj"].fillna(0.0)
eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
eq["row_idx"] = np.arange(len(eq))
eq_ret = eq["ret_mktadj"].to_numpy()
eq_permno = eq["permno"].to_numpy()

calendar_dt = pd.DatetimeIndex(sorted(mkt["date"].unique()))
calendar = list(calendar_dt)
date_to_idx = {d: i for i, d in enumerate(calendar_dt)}
n_days = len(calendar)


def run_backtest(positions_path, name):
    pos = pd.read_parquet(DATA / positions_path)
    pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy()
    pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
    pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)

    liq_q = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
    cost_bps = liq_q.map(COST_BPS).fillna(DEFAULT_COST_BPS)

    target_notional = pos["weight"] * BASE_UNIT_NOTIONAL
    if "avg_dollar_volume_21d" in pos.columns:
        cap = LIQUIDITY_CAP_FRAC * pos["avg_dollar_volume_21d"].fillna(np.inf)
        capped_notional = np.sign(target_notional) * np.minimum(target_notional.abs(), cap)
        pos["liquidity_capped"] = target_notional.abs() > cap
    else:
        capped_notional = target_notional
        pos["liquidity_capped"] = False
    pos["notional"] = capped_notional

    # ---------- daily P&L: expand each position into its held trading days ----------
    daily_pnl = np.zeros(n_days)
    gross_exposure = np.zeros(n_days)
    n_open = np.zeros(n_days, dtype=int)
    turnover_dollars = np.zeros(n_days)

    entry_idx = pos["entry_row_idx"].to_numpy()
    exit_idx = pos["exit_row_idx"].to_numpy()
    notional = pos["notional"].to_numpy()
    entry_cal_idx = pos["day0_date"].map(date_to_idx).to_numpy()
    exit_cal_idx = pos["exit_date"].map(date_to_idx).to_numpy()
    entry_costs = np.abs(notional) * (cost_bps.to_numpy() / 10_000.0)
    exit_costs = entry_costs.copy()

    # entry/exit costs and turnover, and exposure/n_open via difference-array trick
    np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
    np.add.at(daily_pnl, exit_cal_idx, -exit_costs)
    np.add.at(turnover_dollars, entry_cal_idx, np.abs(notional))
    np.add.at(turnover_dollars, exit_cal_idx, np.abs(notional))

    exposure_delta = np.zeros(n_days + 1)
    open_delta = np.zeros(n_days + 1)
    np.add.at(exposure_delta, entry_cal_idx, np.abs(notional))
    np.add.at(exposure_delta, exit_cal_idx, -np.abs(notional))
    np.add.at(open_delta, entry_cal_idx, 1)
    np.add.at(open_delta, exit_cal_idx, -1)
    gross_exposure = np.cumsum(exposure_delta[:n_days])
    n_open = np.cumsum(open_delta[:n_days]).astype(int)

    # daily return P&L: for each position, each day strictly after entry through exit,
    # pnl += notional * ret_mktadj_that_day. Expand via row ranges (vectorized).
    lengths = exit_idx - entry_idx
    total_rows = int(lengths.sum())
    print(f"  {name}: {len(pos):,} positions, {total_rows:,} position-days to expand")

    starts = entry_idx + 1
    # build flat array of (row_idx_in_eq, position_index) pairs
    rep_pos_idx = np.repeat(np.arange(len(pos)), lengths)
    offsets = np.arange(total_rows) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    flat_eq_row = np.repeat(starts, lengths) + offsets

    flat_ret = eq_ret[flat_eq_row]
    flat_notional = notional[rep_pos_idx]
    flat_pnl = flat_notional * flat_ret

    # map eq row -> calendar day index via the eq date array aligned to `calendar`
    eq_date_arr = eq["date"].to_numpy()
    flat_date = eq_date_arr[flat_eq_row]
    flat_cal_idx = calendar_dt.searchsorted(flat_date)

    np.add.at(daily_pnl, flat_cal_idx, flat_pnl)

    nav = FIXED_CAPITAL + np.cumsum(daily_pnl)
    daily_ret = np.diff(nav, prepend=FIXED_CAPITAL) / FIXED_CAPITAL

    out = pd.DataFrame({
        "date": calendar, "daily_pnl": daily_pnl, "nav": nav,
        "gross_exposure": gross_exposure, "n_open_positions": n_open,
        "turnover_dollars": turnover_dollars, "daily_return": daily_ret,
    })
    out.to_csv(DATA / f"backtest_{name}.csv", index=False)

    total_ret = (nav[-1] - FIXED_CAPITAL) / FIXED_CAPITAL
    years = n_days / 252.0
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    drawdown = (nav - running_max) / running_max
    max_dd = drawdown.min()
    avg_gross = gross_exposure.mean()
    avg_leverage = avg_gross / FIXED_CAPITAL
    total_costs = entry_costs.sum() + exit_costs.sum()
    annual_turnover = turnover_dollars.sum() / years / FIXED_CAPITAL

    summary = dict(
        name=name, n_positions=len(pos), total_return=total_ret, annualized_return=ann_ret,
        annualized_vol=ann_vol, sharpe=sharpe, max_drawdown=max_dd, avg_gross_exposure=avg_gross,
        avg_leverage=avg_leverage, avg_n_open_positions=n_open.mean(),
        max_n_open_positions=int(n_open.max()), total_transaction_costs=total_costs,
        annual_turnover_x_capital=annual_turnover,
        pct_liquidity_capped=float(pos["liquidity_capped"].mean()),
        final_nav=float(nav[-1]),
    )
    with open(DATA / f"backtest_{name}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  {name}: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} Sharpe={sharpe:.2f} "
          f"maxDD={max_dd:.2%} avg_leverage={avg_leverage:.2f}x final_nav=${nav[-1]:,.0f}")
    return summary


results = []
for path, name in [("positions_extreme.parquet", "extreme"),
                    ("positions_rankweighted.parquet", "rankweighted"),
                    ("positions_balanced.parquet", "balanced")]:
    results.append(run_backtest(path, name))

pd.DataFrame(results).to_csv(DATA / "backtest_comparison.csv", index=False)
print("\nwrote data/backtest_comparison.csv")
