"""
Two follow-up experiments motivated directly by what 21_leverage_and_improvements.py found:

  (1) Raising MAX_GROSS_LEVERAGE past 1.5x bought almost nothing for strategy 4 (+0.05pp return,
      +0.001 Sharpe, and completely stops binding by 2.0x) -- so the cap isn't the real
      constraint anymore. That's not where the next improvement is.
  (2) The sector diagnostic found a real, large, unmanaged risk instead: strategy 4's book
      averages a 20.8% gross-weight share in its single largest FF12 sector each quarter
      (vs. 8.3% if evenly spread across 12 sectors), peaking at 29% in the worst quarter -- the
      earnings-surprise signal is picking up unintended sector clustering (certain sectors
      report, and surprise, in bunches), which is correlated risk unrelated to the actual PEAD
      edge, sized on top of it for free.

Experiment A: extend strategy 4's existing size-quintile neutralization to also neutralize by
FF12 sector (jointly, same (quarter, side, sector, size_quintile) cell approach already used for
size alone), and see whether removing that unpriced sector risk improves Sharpe/drawdown.

Experiment B: monthly (instead of quarterly) trailing-NAV resizing cadence -- still strictly
prior-period-only, no same-day feedback -- to see whether faster de-risking after a bad month and
re-risking after a good one measurably smooths the NAV curve.

Output: data/positions_strategy5_v2.parquet (if experiment A helps),
        data/backtest_v2_strategy5.csv/_summary.json, printed comparison table.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json

DATA = Path("/root/pead_report/data")
RAW = Path("/mnt/user-data/uploads/PEAD_Trading/data/normalized_equity")

INITIAL_CAPITAL = 10_000_000.0
LIQUIDITY_CAP_FRAC = 0.05
MAX_GROSS_LEVERAGE = 1.5
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15
TRIM, TILT_POWER, BUF = 0.15, 1.0, 0.002

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
month_of = calendar_dt.to_period("M")
unique_quarters = quarter_of.unique().sort_values()
unique_months = month_of.unique().sort_values()
n_quarters, n_months = len(unique_quarters), len(unique_months)
quarter_code_map = {q: i for i, q in enumerate(unique_quarters)}
month_code_map = {m: i for i, m in enumerate(unique_months)}
quarter_code_of_day = np.array([quarter_code_map[q] for q in quarter_of])
month_code_of_day = np.array([month_code_map[m] for m in month_of])

pos = pd.read_parquet(DATA / "positions_rankweighted_v2.parquet").reset_index(drop=True)
pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
pos["day0_date"] = pd.to_datetime(pos["day0_date"])
pos["exit_date"] = pd.to_datetime(pos["exit_date"])
n_pos = len(pos)

entry_qcode = pos["day0_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
exit_qcode = pos["exit_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
entry_mcode = pos["day0_date"].dt.to_period("M").map(month_code_map).to_numpy()
exit_mcode = pos["exit_date"].dt.to_period("M").map(month_code_map).to_numpy()
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
flat_mcode = month_code_of_day[flat_cal_idx]
order_q = np.argsort(flat_qcode, kind="stable")
flat_qcode_sorted, flat_ret_sorted_q, rep_pos_idx_sorted_q = flat_qcode[order_q], flat_ret[order_q], rep_pos_idx[order_q]
boundaries_q = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))
order_m = np.argsort(flat_mcode, kind="stable")
flat_mcode_sorted, flat_ret_sorted_m, rep_pos_idx_sorted_m = flat_mcode[order_m], flat_ret[order_m], rep_pos_idx[order_m]
boundaries_m = np.searchsorted(flat_mcode_sorted, np.arange(n_months + 1))


def compute_weights_size_only(trim, tilt_power):
    d = pos["sue_rank_pct"].to_numpy() - 0.5
    frac = np.clip(np.abs(d) / 0.5, 0, 1)
    tilt = np.sign(d) * (frac ** tilt_power)
    keep = np.abs(d) > trim
    w = np.where(keep, tilt, 0.0)
    df = pos[["ann_quarter", "size_quintile"]].copy()
    df["w"] = w
    df["side"] = np.sign(w)
    kept_idx = np.where(keep)[0]
    sub = df.iloc[kept_idx].copy()
    grp_key = list(zip(sub["ann_quarter"], sub["side"], sub["size_quintile"]))
    sub["grp_key"] = grp_key
    grp_totals = sub.groupby("grp_key")["w"].transform(lambda x: x.abs().sum()).to_numpy()
    n_groups = sub.groupby([sub["ann_quarter"], sub["side"]])["size_quintile"].transform("nunique").to_numpy()
    safe = grp_totals > 0
    w_neutral = np.zeros(len(sub))
    w_neutral[safe] = sub["w"].to_numpy()[safe] / grp_totals[safe] / n_groups[safe]
    out = np.zeros(n_pos)
    out[kept_idx] = w_neutral
    nz = out[out != 0]
    if len(nz):
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5
    return out


def compute_weights_size_sector(trim, tilt_power):
    d = pos["sue_rank_pct"].to_numpy() - 0.5
    frac = np.clip(np.abs(d) / 0.5, 0, 1)
    tilt = np.sign(d) * (frac ** tilt_power)
    keep = np.abs(d) > trim
    w = np.where(keep, tilt, 0.0)
    df = pos[["ann_quarter", "size_quintile", "ff12_sector"]].copy()
    df["w"] = w
    df["side"] = np.sign(w)
    kept_idx = np.where(keep)[0]
    sub = df.iloc[kept_idx].copy()
    grp_key = list(zip(sub["ann_quarter"], sub["side"], sub["ff12_sector"], sub["size_quintile"]))
    sub["grp_key"] = grp_key
    grp_totals = sub.groupby("grp_key")["w"].transform(lambda x: x.abs().sum()).to_numpy()
    n_groups = sub.groupby([sub["ann_quarter"], sub["side"]])["grp_key"].transform("nunique").to_numpy()
    safe = grp_totals > 0
    w_neutral = np.zeros(len(sub))
    w_neutral[safe] = sub["w"].to_numpy()[safe] / grp_totals[safe] / n_groups[safe]
    out = np.zeros(n_pos)
    out[kept_idx] = w_neutral
    nz = out[out != 0]
    if len(nz):
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5
    return out


def simulate(weight, base_unit_fraction, liquidity_cap_frac, max_gross_leverage, cadence="quarterly"):
    liq_cap_dollars = np.where(np.isfinite(adv21), liquidity_cap_frac * adv21, np.inf)
    trailing_nav = INITIAL_CAPITAL
    notional = np.zeros(n_pos)

    if cadence == "quarterly":
        periods, entry_code, exit_code = n_quarters, entry_qcode, exit_qcode
        boundaries, ret_sorted, rep_sorted = boundaries_q, flat_ret_sorted_q, rep_pos_idx_sorted_q
    else:
        periods, entry_code, exit_code = n_months, entry_mcode, exit_mcode
        boundaries, ret_sorted, rep_sorted = boundaries_m, flat_ret_sorted_m, rep_pos_idx_sorted_m

    n_capped = 0
    for pi in range(periods):
        p_mask = (entry_code == pi) & (weight != 0)
        base_unit = trailing_nav * base_unit_fraction
        if p_mask.any():
            idx = np.where(p_mask)[0]
            target = weight[idx] * base_unit
            cap = liq_cap_dollars[idx]
            target = np.sign(target) * np.minimum(np.abs(target), cap)
            gross_requested = float(np.abs(target).sum())
            max_gross = max_gross_leverage * trailing_nav
            p_notional = target.copy()
            if gross_requested > max_gross and gross_requested > 0:
                n_capped += 1
                order_priority = np.argsort(-np.abs(target))
                cum = np.cumsum(np.abs(target[order_priority]))
                keep_mask = cum <= max_gross
                p_notional = np.zeros_like(target)
                p_notional[order_priority[keep_mask]] = target[order_priority[keep_mask]]
                n_kept = keep_mask.sum()
                if n_kept < len(target):
                    remaining = max_gross - (cum[n_kept - 1] if n_kept > 0 else 0.0)
                    boundary = order_priority[n_kept]
                    if remaining > 0:
                        p_notional[boundary] = np.sign(target[boundary]) * min(abs(target[boundary]), remaining)
            notional[idx] = p_notional

        s, e = boundaries[pi], boundaries[pi + 1]
        trade_pnl = float(np.sum(notional[rep_sorted[s:e]] * ret_sorted[s:e])) if e > s else 0.0
        entry_cost = float(np.sum(np.abs(notional[p_mask]) * cost_bps[p_mask] / 10_000.0)) if p_mask.any() else 0.0
        exit_mask = (exit_code == pi) & (weight != 0)
        exit_cost = float(np.sum(np.abs(notional[exit_mask]) * cost_bps[exit_mask] / 10_000.0)) if exit_mask.any() else 0.0
        trailing_nav += trade_pnl - entry_cost - exit_cost

    entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
    exit_costs = entry_costs.copy()
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
    np.add.at(daily_pnl, exit_cal_idx, -exit_costs)
    flat_notional = notional[rep_pos_idx]
    np.add.at(daily_pnl, flat_cal_idx, flat_notional * flat_ret)
    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / INITIAL_CAPITAL
    years = n_days / 252.0
    total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()
    total_costs = entry_costs.sum() + exit_costs.sum()

    return dict(ann_ret=ann_ret, ann_vol=ann_vol, sharpe=sharpe, max_dd=max_dd,
                final_nav=float(nav[-1]), total_costs=float(total_costs),
                pct_periods_capped=n_capped / periods), notional, nav


w_size = compute_weights_size_only(TRIM, TILT_POWER)
w_sector = compute_weights_size_sector(TRIM, TILT_POWER)

print("\n=== baseline (size-neutral only, quarterly) vs +sector-neutral vs +monthly cadence ===")
results = {}
results["baseline_size_only_quarterly"] = simulate(w_size, BUF, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE, "quarterly")[0]
results["size_sector_neutral_quarterly"] = simulate(w_sector, BUF, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE, "quarterly")[0]
results["size_only_monthly_cadence"] = simulate(w_size, BUF, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE, "monthly")[0]
results["size_sector_neutral_monthly"] = simulate(w_sector, BUF, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE, "monthly")[0]

df = pd.DataFrame(results).T
print(df.to_string())

# quick base_unit_fraction re-tune for the best combination found, since neutralizing changes the
# natural scale of gross exposure and the old buf may no longer be well-calibrated
best_key = df["sharpe"].astype(float).idxmax()
print(f"\nbest by Sharpe: {best_key}")
w_best = w_sector if "sector" in best_key else w_size
cadence_best = "monthly" if "monthly" in best_key else "quarterly"
print(f"\nretuning base_unit_fraction for {best_key} ({cadence_best} cadence)...")
retune_rows = []
for buf in [0.0015, 0.002, 0.0025, 0.003, 0.0035, 0.004]:
    r, _, _ = simulate(w_best, buf, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE, cadence_best)
    r["base_unit_fraction"] = buf
    retune_rows.append(r)
retune_df = pd.DataFrame(retune_rows)
print(retune_df.to_string(index=False))

best_row = retune_df.loc[retune_df["sharpe"].idxmax()]
print(f"\nfinal chosen: base_unit_fraction={best_row.base_unit_fraction}, cadence={cadence_best}, "
      f"neutralization={'size+sector' if 'sector' in best_key else 'size only'}")
print(f"ann_ret={best_row.ann_ret:.2%} sharpe={best_row.sharpe:.3f} max_dd={best_row.max_dd:.2%}")
