"""The v2 trailing-NAV, quarterly-resized backtest engine, as a reusable function.

This is the exact loop 23/24_finalize_strategyN.py run inline (liquidity cap at 5% of 21-day ADV,
priority-based leverage-cap trimming, per-liquidity-quintile cost bps, P&L accrued daily on
market-adjusted returns), factored out so the Strategy 9 volatility variants (41) and a same-run
Strategy 6 baseline go through one identical engine rather than a copy per script.
41_strategy9_vol_subsampled.py checks that `run_backtest` with Strategy 6's own weights
reproduces the committed backtest_v2_strategy6.csv NAV to the dollar.
"""
import numpy as np
import pandas as pd

LIQUIDITY_CAP_FRAC = 0.05
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15


def load_mktadj_panel(raw_dir):
    """Daily permno panel with market-adjusted returns, sorted (permno, date) -- the row order
    entry_row_idx/exit_row_idx in every positions_*_v2.parquet index into. Also returns the
    trading calendar (market benchmark dates)."""
    frames = [pd.read_parquet(raw_dir / f"equity_{y}.parquet", columns=["permno", "date", "ret"])
              for y in range(1995, 2015)]
    eq = pd.concat(frames, ignore_index=True)
    mkt = pd.read_parquet(raw_dir / "market_benchmark.parquet", columns=["date", "vwretd"])
    eq = eq.merge(mkt, on="date", how="left")
    eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
    eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
    calendar_dt = pd.DatetimeIndex(sorted(mkt["date"].unique()))
    return eq, calendar_dt


def load_positions(data_dir):
    """positions_rankweighted_v2.parquet (every tradeable event with its per-cell holding window),
    cleaned exactly as 23/24 clean it."""
    pos = pd.read_parquet(data_dir / "positions_rankweighted_v2.parquet").reset_index(drop=True)
    pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
    pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
    pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    pos["exit_date"] = pd.to_datetime(pos["exit_date"])
    return pos


def run_backtest(pos, weight, eq_ret, eq_date_arr, calendar_dt, base_unit_fraction,
                 max_gross_leverage, initial_capital):
    """Run the quarterly trailing-NAV backtest for one weight vector (aligned to `pos`).

    Returns (daily DataFrame, quarter_log DataFrame, summary dict). The summary carries two
    Sharpe ratios: `sharpe` is the true NAV-percentage Sharpe (daily_pnl / prior-day NAV, the
    corrected method the README's results table uses), and `sharpe_fixed_capital` is the older
    daily_pnl / INITIAL_CAPITAL version 17-24 still write into their own summary JSONs -- kept
    only so a result can be compared against those committed files like-for-like."""
    n_pos = len(pos)
    date_to_idx = {d: i for i, d in enumerate(calendar_dt)}
    n_days = len(calendar_dt)
    quarter_of = calendar_dt.to_period("Q")
    unique_quarters = quarter_of.unique().sort_values()
    n_quarters = len(unique_quarters)
    quarter_code_map = {q: i for i, q in enumerate(unique_quarters)}
    quarter_code_of_day = np.array([quarter_code_map[q] for q in quarter_of])

    entry_qcode = pos["day0_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
    exit_qcode = pos["exit_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
    liq_series = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
    cost_bps = liq_series.map(COST_BPS).fillna(DEFAULT_COST_BPS).to_numpy()
    adv21 = pos["avg_dollar_volume_21d"].to_numpy()
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
    flat_qcode_sorted, flat_ret_sorted, rep_pos_idx_sorted = flat_qcode[order], flat_ret[order], rep_pos_idx[order]
    boundaries = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))

    liq_cap_dollars = np.where(np.isfinite(adv21), LIQUIDITY_CAP_FRAC * adv21, np.inf)
    trailing_nav = initial_capital
    notional = np.zeros(n_pos)
    base_unit_at_entry = np.zeros(n_pos)
    quarter_log = []

    for qi in range(n_quarters):
        q_mask = (entry_qcode == qi) & (weight != 0)
        base_unit = trailing_nav * base_unit_fraction
        scale_note = "ok"
        n_dropped_q = 0
        if q_mask.any():
            idx = np.where(q_mask)[0]
            target = weight[idx] * base_unit
            cap = liq_cap_dollars[idx]
            target = np.sign(target) * np.minimum(np.abs(target), cap)
            gross_requested = float(np.abs(target).sum())
            max_gross = max_gross_leverage * trailing_nav
            q_notional = target.copy()
            if gross_requested > max_gross and gross_requested > 0:
                scale_note = "priority_capped"
                order_priority = np.argsort(-np.abs(target))
                cum = np.cumsum(np.abs(target[order_priority]))
                keep_mask = cum <= max_gross
                q_notional = np.zeros_like(target)
                q_notional[order_priority[keep_mask]] = target[order_priority[keep_mask]]
                n_kept = keep_mask.sum()
                if n_kept < len(target):
                    remaining = max_gross - (cum[n_kept - 1] if n_kept > 0 else 0.0)
                    boundary = order_priority[n_kept]
                    if remaining > 0:
                        q_notional[boundary] = np.sign(target[boundary]) * min(abs(target[boundary]), remaining)
                    n_dropped_q = len(target) - n_kept - (1 if remaining > 0 else 0)
            notional[idx] = q_notional
            base_unit_at_entry[idx] = base_unit
        s, e = boundaries[qi], boundaries[qi + 1]
        trade_pnl_q = float(np.sum(notional[rep_pos_idx_sorted[s:e]] * flat_ret_sorted[s:e])) if e > s else 0.0
        entry_cost_q = float(np.sum(np.abs(notional[q_mask]) * cost_bps[q_mask] / 10_000.0)) if q_mask.any() else 0.0
        exit_mask = (exit_qcode == qi) & (weight != 0)
        exit_cost_q = float(np.sum(np.abs(notional[exit_mask]) * cost_bps[exit_mask] / 10_000.0)) if exit_mask.any() else 0.0
        quarter_pnl = trade_pnl_q - entry_cost_q - exit_cost_q
        quarter_log.append(dict(quarter=str(unique_quarters[qi]), trailing_nav_used=trailing_nav,
                                 base_unit=base_unit, n_new_positions=int(q_mask.sum()),
                                 cap_status=scale_note, n_dropped_low_conviction=n_dropped_q,
                                 quarter_realized_pnl=quarter_pnl))
        trailing_nav += quarter_pnl

    entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
    exit_costs = entry_costs.copy()
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
    np.add.at(daily_pnl, exit_cal_idx, -exit_costs)
    np.add.at(daily_pnl, flat_cal_idx, notional[rep_pos_idx] * flat_ret)

    turnover_dollars = np.zeros(n_days)
    np.add.at(turnover_dollars, entry_cal_idx, np.abs(notional))
    np.add.at(turnover_dollars, exit_cal_idx, np.abs(notional))

    exposure_delta = np.zeros(n_days + 1)
    net_delta = np.zeros(n_days + 1)
    open_delta = np.zeros(n_days + 1)
    np.add.at(exposure_delta, entry_cal_idx, np.abs(notional))
    np.add.at(exposure_delta, exit_cal_idx, -np.abs(notional))
    np.add.at(net_delta, entry_cal_idx, notional)
    np.add.at(net_delta, exit_cal_idx, -notional)
    np.add.at(open_delta, entry_cal_idx, (weight != 0).astype(float))
    np.add.at(open_delta, exit_cal_idx, -(weight != 0).astype(float))
    gross_exposure = np.cumsum(exposure_delta[:n_days])
    net_exposure = np.cumsum(net_delta[:n_days])
    n_open = np.cumsum(open_delta[:n_days]).astype(int)

    nav = initial_capital + np.cumsum(daily_pnl)
    prior_nav = np.concatenate([[initial_capital], nav[:-1]])
    daily_ret = daily_pnl / prior_nav
    daily_ret_fixed = np.diff(nav, prepend=initial_capital) / initial_capital

    years = n_days / 252.0
    total_ret = (nav[-1] - initial_capital) / initial_capital
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    vol_fixed = daily_ret_fixed.std(ddof=1) * np.sqrt(252)
    sharpe_fixed = (daily_ret_fixed.mean() * 252) / vol_fixed if vol_fixed > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()
    liquidity_capped = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)

    daily = pd.DataFrame({"date": list(calendar_dt), "daily_pnl": daily_pnl, "nav": nav,
                           "gross_exposure": gross_exposure, "net_exposure": net_exposure,
                           "n_open_positions": n_open, "turnover_dollars": turnover_dollars,
                           "daily_return": daily_ret})
    summary = dict(
        base_unit_fraction=base_unit_fraction, max_gross_leverage=max_gross_leverage,
        n_positions=int((weight != 0).sum()), n_long=int((weight > 0).sum()),
        n_short=int((weight < 0).sum()), total_return=float(total_ret),
        annualized_return=float(ann_ret), annualized_vol=float(ann_vol), sharpe=float(sharpe),
        sharpe_fixed_capital=float(sharpe_fixed), max_drawdown=float(max_dd),
        avg_gross_exposure=float(gross_exposure.mean()),
        avg_leverage_vs_initial_capital=float(gross_exposure.mean() / initial_capital),
        avg_n_open_positions=float(n_open.mean()),
        total_transaction_costs=float(entry_costs.sum() + exit_costs.sum()),
        annual_turnover_x_capital=float(turnover_dollars.sum() / years / initial_capital),
        pct_liquidity_capped=float(liquidity_capped.mean()), final_nav=float(nav[-1]),
        n_quarters_leverage_capped=int(sum(1 for q in quarter_log if q["cap_status"] == "priority_capped")),
    )
    return daily, pd.DataFrame(quarter_log), summary
