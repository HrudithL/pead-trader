"""
Parameter sensitivity sweep: how do BASE_UNIT_FRACTION (position sizing) and
LIQUIDITY_CAP_FRAC (per-position cap as a fraction of a stock's own daily dollar volume)
trade off return against risk (vol, drawdown) and against practical constraints (how much of
the book ends up liquidity-capped, how often the 1.5x gross leverage cap binds)?

This does NOT hardcode one "right" answer. It re-runs the same sequential, trailing-NAV
simulation from 17_backtest_v2.py across a grid of parameter values and reports, for every
combination, everything needed to reason about the tradeoff: annualized return, annualized vol,
Sharpe, max drawdown, average realized leverage, how often that leverage bumps into the 1.5x
safety cap, what fraction of positions get truncated by the liquidity rule, and total transaction
costs. MAX_GROSS_LEVERAGE (the hard safety cap approved earlier) is held fixed at 1.5x throughout
-- this sweep is about how hard the strategy pushes *within* that ceiling and how large a single
position is allowed to get relative to a stock's own trading volume, not about raising the
ceiling itself.

The expensive part (loading the full daily return panel, expanding each position into its
day-by-day return path) does not depend on either parameter, so it is computed ONCE per strategy
and reused across every grid point -- only the O(quarters) sequential sizing loop and the final
aggregation are re-run per combination, which is what actually keeps a ~30-point grid across 3
strategies tractable.

Output: data/sweep_base_unit_fraction.csv, data/sweep_liquidity_cap_frac.csv
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path("/root/pead_report/data")
RAW = Path("/mnt/user-data/uploads/PEAD_Trading/data/normalized_equity")

INITIAL_CAPITAL = 10_000_000.0
MAX_GROSS_LEVERAGE = 1.5
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15

print("loading daily market-adjusted return panel...")
frames = []
for y in range(1995, 2015):
    frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date", "ret"]))
eq = pd.concat(frames, ignore_index=True)
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
eq = eq.merge(mkt, on="date", how="left")
eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
eq_ret = eq["ret_mktadj"].to_numpy()
eq_date_arr = eq["date"].to_numpy()

calendar_dt = pd.DatetimeIndex(sorted(mkt["date"].unique()))
date_to_idx = {d: i for i, d in enumerate(calendar_dt)}
n_days = len(calendar_dt)

quarter_of = calendar_dt.to_period("Q")
unique_quarters = quarter_of.unique().sort_values()
n_quarters = len(unique_quarters)
quarter_code_map = {q: i for i, q in enumerate(unique_quarters)}
quarter_code_of_day = np.array([quarter_code_map[q] for q in quarter_of])


def prepare_strategy(positions_path):
    """One-time, param-independent setup: load positions, expand into day-by-day return rows."""
    pos = pd.read_parquet(DATA / positions_path).reset_index(drop=True)
    pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
    pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
    pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    pos["exit_date"] = pd.to_datetime(pos["exit_date"])
    n_pos = len(pos)

    entry_quarter = pos["day0_date"].dt.to_period("Q")
    exit_quarter = pos["exit_date"].dt.to_period("Q")
    entry_qcode = entry_quarter.map(quarter_code_map).to_numpy()
    exit_qcode = exit_quarter.map(quarter_code_map).to_numpy()

    weight = pos["weight"].to_numpy()
    liq_q = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
    cost_bps = liq_q.map(COST_BPS).fillna(DEFAULT_COST_BPS).to_numpy()
    adv21 = pos["avg_dollar_volume_21d"].to_numpy() if "avg_dollar_volume_21d" in pos.columns else np.full(n_pos, np.inf)

    entry_cal_idx = pos["day0_date"].map(date_to_idx).to_numpy()
    exit_cal_idx = pos["exit_date"].map(date_to_idx).to_numpy()

    entry_idx = pos["entry_row_idx"].to_numpy()
    exit_idx = pos["exit_row_idx"].to_numpy()
    lengths = exit_idx - entry_idx
    total_rows = int(lengths.sum())
    starts = entry_idx + 1
    rep_pos_idx = np.repeat(np.arange(n_pos), lengths)
    offsets = np.arange(total_rows) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    flat_eq_row = np.repeat(starts, lengths) + offsets
    flat_ret = eq_ret[flat_eq_row]
    flat_date = eq_date_arr[flat_eq_row]
    flat_cal_idx = calendar_dt.searchsorted(flat_date)
    flat_qcode = quarter_code_of_day[flat_cal_idx]

    order = np.argsort(flat_qcode, kind="stable")
    flat_qcode_sorted = flat_qcode[order]
    flat_ret_sorted = flat_ret[order]
    rep_pos_idx_sorted = rep_pos_idx[order]
    boundaries = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))

    return dict(n_pos=n_pos, weight=weight, cost_bps=cost_bps, adv21=adv21,
                entry_qcode=entry_qcode, exit_qcode=exit_qcode,
                entry_cal_idx=entry_cal_idx, exit_cal_idx=exit_cal_idx,
                flat_ret_sorted=flat_ret_sorted, rep_pos_idx_sorted=rep_pos_idx_sorted,
                boundaries=boundaries, flat_cal_idx=flat_cal_idx, flat_ret=flat_ret,
                rep_pos_idx=rep_pos_idx)


def simulate(prep, base_unit_fraction, liquidity_cap_frac, max_gross_leverage=MAX_GROSS_LEVERAGE):
    n_pos = prep["n_pos"]
    weight = prep["weight"]
    cost_bps = prep["cost_bps"]
    liq_cap_dollars = liquidity_cap_frac * prep["adv21"]
    liq_cap_dollars = np.where(np.isfinite(liq_cap_dollars), liq_cap_dollars, np.inf)
    entry_qcode, exit_qcode = prep["entry_qcode"], prep["exit_qcode"]
    boundaries = prep["boundaries"]
    flat_ret_sorted, rep_pos_idx_sorted = prep["flat_ret_sorted"], prep["rep_pos_idx_sorted"]

    trailing_nav = INITIAL_CAPITAL
    notional = np.zeros(n_pos)
    base_unit_at_entry = np.zeros(n_pos)
    n_capped_quarters = 0
    quarter_pnls = []

    for qi in range(n_quarters):
        q_mask = entry_qcode == qi
        base_unit = trailing_nav * base_unit_fraction
        scale = 1.0
        if q_mask.any():
            target = weight[q_mask] * base_unit
            cap = liq_cap_dollars[q_mask]
            target = np.sign(target) * np.minimum(np.abs(target), cap)
            gross_requested = float(np.abs(target).sum())
            max_gross = max_gross_leverage * trailing_nav
            if gross_requested > max_gross and gross_requested > 0:
                scale = max_gross / gross_requested
                n_capped_quarters += 1
            notional[q_mask] = target * scale
            base_unit_at_entry[q_mask] = base_unit

        s, e = boundaries[qi], boundaries[qi + 1]
        trade_pnl_q = float(np.sum(notional[rep_pos_idx_sorted[s:e]] * flat_ret_sorted[s:e])) if e > s else 0.0
        entry_cost_q = float(np.sum(np.abs(notional[q_mask]) * cost_bps[q_mask] / 10_000.0)) if q_mask.any() else 0.0
        exit_mask = exit_qcode == qi
        exit_cost_q = float(np.sum(np.abs(notional[exit_mask]) * cost_bps[exit_mask] / 10_000.0)) if exit_mask.any() else 0.0
        quarter_pnl = trade_pnl_q - entry_cost_q - exit_cost_q
        quarter_pnls.append(quarter_pnl)
        trailing_nav += quarter_pnl

    entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
    exit_costs = entry_costs.copy()
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, prep["entry_cal_idx"], -entry_costs)
    np.add.at(daily_pnl, prep["exit_cal_idx"], -exit_costs)
    flat_notional = notional[prep["rep_pos_idx"]]
    np.add.at(daily_pnl, prep["flat_cal_idx"], flat_notional * prep["flat_ret"])

    exposure_delta = np.zeros(n_days + 1)
    np.add.at(exposure_delta, prep["entry_cal_idx"], np.abs(notional))
    np.add.at(exposure_delta, prep["exit_cal_idx"], -np.abs(notional))
    gross_exposure = np.cumsum(exposure_delta[:n_days])

    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / INITIAL_CAPITAL
    years = n_days / 252.0
    total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()
    liquidity_capped = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)

    return dict(
        base_unit_fraction=base_unit_fraction, liquidity_cap_frac=liquidity_cap_frac,
        annualized_return=ann_ret, annualized_vol=ann_vol, sharpe=sharpe, max_drawdown=max_dd,
        avg_leverage=float(gross_exposure.mean() / INITIAL_CAPITAL),
        pct_liquidity_capped=float(liquidity_capped.mean()),
        n_quarters_leverage_capped=n_capped_quarters,
        total_transaction_costs=float(entry_costs.sum() + exit_costs.sum()),
        final_nav=float(nav[-1]),
    )


STRATEGIES = [("positions_extreme_v2.parquet", "extreme"),
              ("positions_rankweighted_v2.parquet", "rankweighted"),
              ("positions_balanced_v2.parquet", "balanced")]

print("preparing strategies (one-time, parameter-independent expansion)...")
preps = {name: prepare_strategy(path) for path, name in STRATEGIES}

# ---------- sweep 1: BASE_UNIT_FRACTION, liquidity cap held at current 1% ----------
buf_grid = [0.0010, 0.0015, 0.0020, 0.0025, 0.0030, 0.0040, 0.0050]
rows1 = []
for path, name in STRATEGIES:
    for buf in buf_grid:
        r = simulate(preps[name], buf, liquidity_cap_frac=0.01)
        r["strategy"] = name
        rows1.append(r)
sweep1 = pd.DataFrame(rows1)
sweep1.to_csv(DATA / "sweep_base_unit_fraction.csv", index=False)
print("\n=== BASE_UNIT_FRACTION sweep (liquidity_cap_frac fixed at 1%) ===")
print(sweep1[["strategy", "base_unit_fraction", "annualized_return", "annualized_vol", "sharpe",
              "max_drawdown", "avg_leverage", "pct_liquidity_capped", "n_quarters_leverage_capped"]]
      .to_string(index=False))

# ---------- sweep 2: LIQUIDITY_CAP_FRAC, base unit fraction held at 0.002 (a candidate mid point) ----------
liq_grid = [0.005, 0.0075, 0.010, 0.015, 0.020, 0.030, 0.050]
rows2 = []
for path, name in STRATEGIES:
    for lf in liq_grid:
        r = simulate(preps[name], base_unit_fraction=0.002, liquidity_cap_frac=lf)
        r["strategy"] = name
        rows2.append(r)
sweep2 = pd.DataFrame(rows2)
sweep2.to_csv(DATA / "sweep_liquidity_cap_frac.csv", index=False)
print("\n=== LIQUIDITY_CAP_FRAC sweep (base_unit_fraction fixed at 0.002) ===")
print(sweep2[["strategy", "liquidity_cap_frac", "annualized_return", "annualized_vol", "sharpe",
              "max_drawdown", "avg_leverage", "pct_liquidity_capped", "n_quarters_leverage_capped"]]
      .to_string(index=False))
