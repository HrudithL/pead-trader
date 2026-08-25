"""
Finalize Strategy 6: same size + FF12-sector neutralized signal as Strategy 5 (trim=0.15,
tilt_power=1.0), but with MAX_GROSS_LEVERAGE raised from 1.5x to 2.5x and base_unit_fraction
retuned to 0.003 -- the point identified in the leverage-cap sweep where this specific,
well-diversified signal's own natural demand stops growing (cap-binding drops to 0% of quarters
by 2.5x). Unlike the earlier (pre-neutralization) strategies, raising the cap here does not
introduce concentration risk, because sector+size neutralization already prevents capital from
piling into a small number of names -- so this is a case where loosening the safety cap is
actually justified by the diversification already built in, not just chasing return.

Result vs. Strategy 5: ann.ret 5.08%->6.49%, Sharpe 1.00->1.02 (improves, not just return),
max_drawdown -10.4%->-16.6% (the honest cost: more gross exposure means a deeper worst case even
though the risk-adjusted ratio holds up).

Produces the same output shape as strategy 5 (23) so it drops into the same report/charting
pipeline: data/positions_strategy6_v2.parquet, data/backtest_v2_strategy6.csv,
data/backtest_v2_strategy6_quarterlog.csv, data/backtest_v2_strategy6_summary.json, and an updated
data/backtest_v2_comparison.csv with all six strategies.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json

DATA = Path("/root/pead_report/data")
RAW = Path("/mnt/user-data/uploads/PEAD_Trading/data/normalized_equity")

INITIAL_CAPITAL = 10_000_000.0
LIQUIDITY_CAP_FRAC = 0.05
MAX_GROSS_LEVERAGE = 2.5
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15
TRIM, TILT_POWER, BUF = 0.15, 1.0, 0.003

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

pos = pd.read_parquet(DATA / "positions_rankweighted_v2.parquet").reset_index(drop=True)
pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
pos["day0_date"] = pd.to_datetime(pos["day0_date"])
pos["exit_date"] = pd.to_datetime(pos["exit_date"])
n_pos = len(pos)

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


weight = compute_weights_size_sector(TRIM, TILT_POWER)

liq_cap_dollars = np.where(np.isfinite(adv21), LIQUIDITY_CAP_FRAC * adv21, np.inf)
trailing_nav = INITIAL_CAPITAL
notional = np.zeros(n_pos)
base_unit_at_entry = np.zeros(n_pos)
quarter_log = []

for qi in range(n_quarters):
    q_mask = (entry_qcode == qi) & (weight != 0)
    base_unit = trailing_nav * BUF
    scale_note = "ok"
    n_dropped_q = 0
    if q_mask.any():
        idx = np.where(q_mask)[0]
        target = weight[idx] * base_unit
        cap = liq_cap_dollars[idx]
        target = np.sign(target) * np.minimum(np.abs(target), cap)
        gross_requested = float(np.abs(target).sum())
        max_gross = MAX_GROSS_LEVERAGE * trailing_nav
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

pos_out = pos.copy()
pos_out["weight"] = weight
pos_out = pos_out[pos_out["weight"] != 0].reset_index(drop=True)
pos_out.to_parquet(DATA / "positions_strategy6_v2.parquet", index=False)

entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
exit_costs = entry_costs.copy()
daily_pnl = np.zeros(n_days)
np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
np.add.at(daily_pnl, exit_cal_idx, -exit_costs)
flat_notional = notional[rep_pos_idx]
np.add.at(daily_pnl, flat_cal_idx, flat_notional * flat_ret)

turnover_dollars = np.zeros(n_days)
np.add.at(turnover_dollars, entry_cal_idx, np.abs(notional))
np.add.at(turnover_dollars, exit_cal_idx, np.abs(notional))

exposure_delta = np.zeros(n_days + 1)
open_delta = np.zeros(n_days + 1)
np.add.at(exposure_delta, entry_cal_idx, np.abs(notional))
np.add.at(exposure_delta, exit_cal_idx, -np.abs(notional))
np.add.at(open_delta, entry_cal_idx, (weight != 0).astype(float))
np.add.at(open_delta, exit_cal_idx, -(weight != 0).astype(float))
gross_exposure = np.cumsum(exposure_delta[:n_days])
n_open = np.cumsum(open_delta[:n_days]).astype(int)

nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / INITIAL_CAPITAL

out = pd.DataFrame({"date": list(calendar_dt), "daily_pnl": daily_pnl, "nav": nav,
                     "gross_exposure": gross_exposure, "n_open_positions": n_open,
                     "turnover_dollars": turnover_dollars, "daily_return": daily_ret})
out.to_csv(DATA / "backtest_v2_strategy6.csv", index=False)
pd.DataFrame(quarter_log).to_csv(DATA / "backtest_v2_strategy6_quarterlog.csv", index=False)

years = n_days / 252.0
total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
ann_ret = (1 + total_ret) ** (1 / years) - 1
ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
running_max = np.maximum.accumulate(nav)
max_dd = ((nav - running_max) / running_max).min()
liquidity_capped = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)
total_costs = entry_costs.sum() + exit_costs.sum()
annual_turnover = turnover_dollars.sum() / years / INITIAL_CAPITAL

summary = dict(
    name="strategy6_leverage25x", trim=TRIM, tilt_power=TILT_POWER,
    base_unit_fraction=BUF, liquidity_cap_frac=LIQUIDITY_CAP_FRAC,
    n_positions=int((weight != 0).sum()), total_return=total_ret, annualized_return=ann_ret,
    annualized_vol=ann_vol, sharpe=sharpe, max_drawdown=max_dd,
    avg_gross_exposure=float(gross_exposure.mean()),
    avg_leverage_vs_initial_capital=float(gross_exposure.mean() / INITIAL_CAPITAL),
    avg_n_open_positions=float(n_open.mean()), max_n_open_positions=int(n_open.max()),
    total_transaction_costs=float(total_costs), annual_turnover_x_capital=float(annual_turnover),
    pct_liquidity_capped=float(liquidity_capped.mean()), final_nav=float(nav[-1]),
    n_quarters_leverage_capped=int(sum(1 for q in quarter_log if q["cap_status"] == "priority_capped")),
    total_positions_dropped_by_priority_cap=int(sum(q["n_dropped_low_conviction"] for q in quarter_log)),
)
with open(DATA / "backtest_v2_strategy6_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"strategy6: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} Sharpe={sharpe:.3f} "
      f"maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f}")

# ---------- update the comparison table to include all 6 strategies ----------
comparison_rows = []
for fname in ["extreme", "rankweighted", "balanced", "strategy4", "strategy5"]:
    with open(DATA / f"backtest_v2_{fname}_summary.json") as f:
        comparison_rows.append(json.load(f))
comparison_rows.append(summary)
pd.DataFrame(comparison_rows).to_csv(DATA / "backtest_v2_comparison.csv", index=False)
print("\nwrote data/backtest_v2_comparison.csv (6 strategies)")
