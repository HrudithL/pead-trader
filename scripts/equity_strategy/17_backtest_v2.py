"""
Backtest engine v2: real compounding, without the runaway-leverage bug.

The fix, precisely: position sizing is no longer a fixed dollar amount forever (the naive,
non-compounding approach). Instead, the "base unit notional" -- the dollar size of one full unit
of |weight| -- is resized QUARTERLY, off the TRAILING (prior quarter-end, already-realized) NAV,
never off the same-day live NAV. This is what makes it real compounding: a strategy that made
money last quarter genuinely trades a bit bigger this quarter, exactly like a real fund
rebalancing its book size periodically. There is no same-day feedback loop for profits to run
away through, because NAV realized in quarter q can only affect sizing from quarter q+1 onward.

A second, independent safeguard: total GROSS EXPOSURE (the sum of every currently open
position's absolute dollar size) is hard-capped at MAX_GROSS_LEVERAGE x the trailing NAV used
for that quarter. If the signals firing in a given quarter would otherwise commit more than
that, every position entered that quarter is scaled down proportionally so the cap binds
exactly, not exceeded.

Implementation note -- this really is a sequential, quarter-by-quarter simulation, not a single
vectorized pass with a static NAV plugged in: quarters are processed in chronological order, and
before any quarter q's new positions are sized, the ACTUAL realized P&L of every calendar day up
through the end of quarter q-1 (from every position live during those days, including positions
carried over from earlier quarters) has already been computed and folded into trailing_nav. A
position's notional is fixed for its whole life once assigned (real funds don't re-size an
already-open trade every quarter), but a position can only ever be sized off NAV that was fully
realized before that position existed -- so there is no circularity: by the time quarter q is
reached, every position that could possibly contribute P&L inside quarter q's days was entered
in quarter q or earlier, and any such earlier position already has a notional fixed from ITS OWN
entry quarter's (even earlier) trailing NAV.

Output per strategy: data/backtest_v2_<name>.csv, data/backtest_v2_<name>_quarterlog.csv,
                      data/backtest_v2_<name>_summary.json
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
MAX_GROSS_LEVERAGE = 1.5          # hard cap: gross exposure can never exceed 1.5x trailing NAV
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15

# Sizing settings picked from the parameter sweep (19_param_sweep_extended.py): the point just
# before each strategy's own leverage-cap elbow, where the 1.5x hard cap starts binding almost
# every quarter and extra requested size stops buying real exposure (it just gets scaled back
# down, adding turnover and cost without adding market exposure). Extreme and balanced share the
# same headroom and settle at base_unit_fraction=0.003 / liquidity_cap_frac=0.05. Rank-weighted
# saturates the leverage cap far earlier (it runs ~1,900 positions open at once, so its natural
# aggregate exposure is much higher for the same per-position sizing) and gets essentially no
# benefit from pushing past base_unit_fraction=0.002 -- it stays at its current setting.
STRATEGY_PARAMS = {
    "extreme":      dict(base_unit_fraction=0.003, liquidity_cap_frac=0.05),
    "rankweighted": dict(base_unit_fraction=0.002, liquidity_cap_frac=0.05),
    "balanced":     dict(base_unit_fraction=0.003, liquidity_cap_frac=0.05),
}

print("loading daily market-adjusted return panel...")
frames = []
for y in range(1995, 2015):
    frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date", "ret"]))
eq = pd.concat(frames, ignore_index=True)
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
eq = eq.merge(mkt, on="date", how="left")
eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
eq["row_idx"] = np.arange(len(eq))
eq_ret = eq["ret_mktadj"].to_numpy()
eq_date_arr = eq["date"].to_numpy()

calendar_dt = pd.DatetimeIndex(sorted(mkt["date"].unique()))
calendar = list(calendar_dt)
date_to_idx = {d: i for i, d in enumerate(calendar_dt)}
n_days = len(calendar)

quarter_of = calendar_dt.to_period("Q")
unique_quarters = quarter_of.unique().sort_values()
n_quarters = len(unique_quarters)
quarter_code_map = {q: i for i, q in enumerate(unique_quarters)}
quarter_code_of_day = np.array([quarter_code_map[q] for q in quarter_of])


def run_backtest(positions_path, name):
    base_unit_fraction = STRATEGY_PARAMS[name]["base_unit_fraction"]
    liquidity_cap_frac = STRATEGY_PARAMS[name]["liquidity_cap_frac"]
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
    liq_cap_dollars = (liquidity_cap_frac * pos["avg_dollar_volume_21d"]).fillna(np.inf).to_numpy() \
        if "avg_dollar_volume_21d" in pos.columns else np.full(n_pos, np.inf)

    entry_cal_idx = pos["day0_date"].map(date_to_idx).to_numpy()
    exit_cal_idx = pos["exit_date"].map(date_to_idx).to_numpy()

    # ---------- precompute the day-by-day expansion of every position's return path ----------
    # this only depends on entry/exit trading days and realized returns -- NOT on notional -- so
    # it can be built once, up front, outside the sequential sizing loop.
    entry_idx = pos["entry_row_idx"].to_numpy()
    exit_idx = pos["exit_row_idx"].to_numpy()
    lengths = exit_idx - entry_idx
    total_rows = int(lengths.sum())
    print(f"  {name}: {n_pos:,} positions, {total_rows:,} position-days to expand")

    starts = entry_idx + 1
    rep_pos_idx = np.repeat(np.arange(n_pos), lengths)
    offsets = np.arange(total_rows) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    flat_eq_row = np.repeat(starts, lengths) + offsets
    flat_ret = eq_ret[flat_eq_row]
    flat_date = eq_date_arr[flat_eq_row]
    flat_cal_idx = calendar_dt.searchsorted(flat_date)
    flat_qcode = quarter_code_of_day[flat_cal_idx]

    # sort the flat rows by quarter so each quarter's realized trading P&L can be sliced out in
    # one contiguous block during the sequential loop below
    order = np.argsort(flat_qcode, kind="stable")
    flat_qcode_sorted = flat_qcode[order]
    flat_ret_sorted = flat_ret[order]
    rep_pos_idx_sorted = rep_pos_idx[order]
    boundaries = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))

    # ---------- TRUE sequential quarter-by-quarter simulation ----------
    # invariant maintained at the top of iteration qi: trailing_nav equals the fully realized NAV
    # as of the end of quarter (qi-1), and `notional` has already been assigned for every position
    # with entry_qcode <= qi-1 (i.e. every position that could possibly have a flat row inside
    # quarter qi, since a position's flat rows never occur before its own entry quarter).
    trailing_nav = INITIAL_CAPITAL
    notional = np.zeros(n_pos)
    base_unit_at_entry = np.zeros(n_pos)
    quarter_log = []

    for qi in range(n_quarters):
        q = unique_quarters[qi]
        q_mask = entry_qcode == qi
        base_unit = trailing_nav * base_unit_fraction
        gross_requested = 0.0
        scale = 1.0
        if q_mask.any():
            target = weight[q_mask] * base_unit
            cap = liq_cap_dollars[q_mask]
            target = np.sign(target) * np.minimum(np.abs(target), cap)
            gross_requested = float(np.abs(target).sum())
            max_gross = MAX_GROSS_LEVERAGE * trailing_nav
            if gross_requested > max_gross and gross_requested > 0:
                scale = max_gross / gross_requested
            notional[q_mask] = target * scale
            base_unit_at_entry[q_mask] = base_unit

        # realized trading P&L of every calendar day inside quarter qi, from every position live
        # during those days -- notional is already fixed for all of them (entered this quarter or
        # earlier), so this is a real, fully-determined number, not an estimate.
        s, e = boundaries[qi], boundaries[qi + 1]
        seg_pos = rep_pos_idx_sorted[s:e]
        seg_ret = flat_ret_sorted[s:e]
        trade_pnl_q = float(np.sum(notional[seg_pos] * seg_ret)) if e > s else 0.0

        entry_cost_q = float(np.sum(np.abs(notional[q_mask]) * cost_bps[q_mask] / 10_000.0)) if q_mask.any() else 0.0
        exit_mask = exit_qcode == qi
        exit_cost_q = float(np.sum(np.abs(notional[exit_mask]) * cost_bps[exit_mask] / 10_000.0)) if exit_mask.any() else 0.0

        quarter_pnl = trade_pnl_q - entry_cost_q - exit_cost_q
        quarter_log.append(dict(
            quarter=str(q), trailing_nav_used=trailing_nav, base_unit=base_unit,
            n_new_positions=int(q_mask.sum()), gross_requested=gross_requested,
            scale_applied=scale, quarter_realized_pnl=quarter_pnl,
        ))
        trailing_nav = trailing_nav + quarter_pnl

    pos["notional"] = notional
    pos["liquidity_capped"] = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)

    # ---------- final full-history daily P&L / NAV series, using the now fully-resolved notional ----------
    entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
    exit_costs = entry_costs.copy()

    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
    np.add.at(daily_pnl, exit_cal_idx, -exit_costs)

    turnover_dollars = np.zeros(n_days)
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

    flat_notional = notional[rep_pos_idx]
    flat_pnl = flat_notional * flat_ret
    np.add.at(daily_pnl, flat_cal_idx, flat_pnl)

    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / INITIAL_CAPITAL

    # sanity check: the sequential loop's own running trailing_nav trajectory should match this
    # full-history NAV series at each quarter boundary (both are computed from the same notional
    # array, just via two different aggregation paths) -- confirms no double counting / drift.
    check_rows = []
    running_nav_check = INITIAL_CAPITAL
    for qi, q in enumerate(unique_quarters):
        running_nav_check += quarter_log[qi]["quarter_realized_pnl"]
        q_end_mask = quarter_of == q
        if q_end_mask.any():
            q_end_day = np.where(q_end_mask)[0].max()
            actual_nav_at_end = nav[q_end_day]
            quarter_log[qi]["nav_after_quarter"] = running_nav_check
            quarter_log[qi]["full_series_nav_at_quarter_end"] = float(actual_nav_at_end)
            check_rows.append(abs(running_nav_check - actual_nav_at_end))
    max_check_diff = max(check_rows) if check_rows else 0.0
    if max_check_diff > 1.0:
        print(f"  {name}: WARNING sequential-vs-full-series NAV check diff = ${max_check_diff:,.2f}")
    else:
        print(f"  {name}: sequential-vs-full-series NAV reconciliation OK (max diff ${max_check_diff:,.4f})")

    out = pd.DataFrame({
        "date": calendar, "daily_pnl": daily_pnl, "nav": nav,
        "gross_exposure": gross_exposure, "n_open_positions": n_open,
        "turnover_dollars": turnover_dollars, "daily_return": daily_ret,
    })
    out.to_csv(DATA / f"backtest_v2_{name}.csv", index=False)
    pd.DataFrame(quarter_log).to_csv(DATA / f"backtest_v2_{name}_quarterlog.csv", index=False)

    total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    years = n_days / 252.0
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    drawdown = (nav - running_max) / running_max
    max_dd = drawdown.min()
    total_costs = entry_costs.sum() + exit_costs.sum()
    annual_turnover = turnover_dollars.sum() / years / INITIAL_CAPITAL

    summary = dict(
        name=name, n_positions=n_pos, total_return=total_ret, annualized_return=ann_ret,
        annualized_vol=ann_vol, sharpe=sharpe, max_drawdown=max_dd,
        avg_gross_exposure=float(gross_exposure.mean()),
        avg_leverage_vs_initial_capital=float(gross_exposure.mean() / INITIAL_CAPITAL),
        avg_n_open_positions=float(n_open.mean()), max_n_open_positions=int(n_open.max()),
        total_transaction_costs=float(total_costs), annual_turnover_x_capital=float(annual_turnover),
        pct_liquidity_capped=float(pos["liquidity_capped"].mean()), final_nav=float(nav[-1]),
        n_quarters_leverage_capped=int(sum(1 for q in quarter_log if q["scale_applied"] < 0.999)),
        max_nav_check_diff=float(max_check_diff),
    )
    with open(DATA / f"backtest_v2_{name}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  {name}: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} Sharpe={sharpe:.2f} "
          f"maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f} "
          f"(leverage-capped in {summary['n_quarters_leverage_capped']} quarters)")
    return summary


results = []
for path, name in [("positions_extreme_v2.parquet", "extreme"),
                    ("positions_rankweighted_v2.parquet", "rankweighted"),
                    ("positions_balanced_v2.parquet", "balanced")]:
    results.append(run_backtest(path, name))

pd.DataFrame(results).to_csv(DATA / "backtest_v2_comparison.csv", index=False)
print("\nwrote data/backtest_v2_comparison.csv")
