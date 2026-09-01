"""
Strategy 7: same trimmed/tilted signal and the same size+sector diversification as Strategy 5/6,
but WITHOUT forcing the long leg and short leg to be dollar-equal every quarter.

Why this matters, and what changes mechanically: Strategy 5/6's neutralization groups events by
(quarter, SIDE, sector, size_quintile) -- crossing the group key with side is what forces the
long leg's total budget and the short leg's total budget to each land at the same fixed
constant every quarter, regardless of how lopsided the actual signal is that quarter. If a
quarter's earnings season produces a lot more strong positive surprises than strong negative
ones (which happens, surprise counts are not symmetric quarter to quarter, e.g. broad economic
expansions skew the SUE distribution), the old construction throws that information away by
re-forcing 50/50 dollar balance anyway.

Strategy 7 drops SIDE from the group key -- neutralization is by (quarter, sector, size_quintile)
only. Each such cell still gets a fixed capital budget (for diversification: no single
sector/size bucket can dominate), but WITHIN a cell, the actual mix of long and short signals
keeps its natural relative weight. A cell with only positive-surprise names that quarter comes
out net long; a mixed cell nets out more balanced. Aggregated across the whole book, this lets
the portfolio's net exposure drift with the natural asymmetry of the earnings-surprise signal
itself, instead of being reset to flat every quarter by construction. This is the "unrestricted"
alternative flagged in conversation to raising beta via a separate market overlay -- same
directional idea (let some net exposure through), different implementation (emerges from the
signal itself rather than a bolted-on index position), with the explicit tradeoff that it now
mixes a market-timing-like effect into the same weights used for the earnings-surprise bet,
rather than keeping the two sources of return cleanly separable.

Output: data/positions_strategy7_v2.parquet, data/backtest_v2_strategy7.csv,
        data/backtest_v2_strategy7_quarterlog.csv, data/backtest_v2_strategy7_summary.json
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
LIQUIDITY_CAP_FRAC = 0.05
MAX_GROSS_LEVERAGE = 2.5  # same cap Strategy 6 uses -- this is a like-for-like comparison to it
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
eq_raw_ret = eq["ret"].fillna(0.0).to_numpy()  # RAW (not market-adjusted) return -- needed because a book
                                    # that's no longer dollar-neutral actually carries real market
                                    # exposure now, so its P&L must be computed on raw returns,
                                    # not market-adjusted ones (adjusting would silently strip
                                    # the very net-exposure effect this strategy is designed to let
                                    # through).

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
flat_ret_raw = eq_raw_ret[flat_eq_row]
flat_date = eq_date_arr[flat_eq_row]
flat_cal_idx = calendar_dt.searchsorted(flat_date)
flat_qcode = quarter_code_of_day[flat_cal_idx]
order = np.argsort(flat_qcode, kind="stable")
flat_qcode_sorted, flat_ret_sorted, rep_pos_idx_sorted = flat_qcode[order], flat_ret_raw[order], rep_pos_idx[order]
boundaries = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))


def compute_weights_unconstrained(trim, tilt_power):
    d = pos["sue_rank_pct"].to_numpy() - 0.5
    frac = np.clip(np.abs(d) / 0.5, 0, 1)
    tilt = np.sign(d) * (frac ** tilt_power)
    keep = np.abs(d) > trim
    w = np.where(keep, tilt, 0.0)
    df = pos[["ann_quarter", "size_quintile", "ff12_sector"]].copy()
    df["w"] = w
    kept_idx = np.where(keep)[0]
    sub = df.iloc[kept_idx].copy()
    # group key WITHOUT side: (quarter, sector, size_quintile) only -- this is the one change
    # from Strategy 5/6's neutralization that lets net long/short exposure emerge naturally.
    grp_key = list(zip(sub["ann_quarter"], sub["ff12_sector"], sub["size_quintile"]))
    sub["grp_key"] = grp_key
    # normalize by the group's total ABSOLUTE weight (for diversification: no cell dominates)
    # but keep each position's actual SIGN and relative magnitude within the cell -- so a cell
    # that's mostly positive surprises comes out net long, not forced back to zero.
    grp_totals = sub.groupby("grp_key")["w"].transform(lambda x: x.abs().sum()).to_numpy()
    n_groups = sub.groupby("ann_quarter")["grp_key"].transform("nunique").to_numpy()
    safe = grp_totals > 0
    w_neutral = np.zeros(len(sub))
    w_neutral[safe] = sub["w"].to_numpy()[safe] / grp_totals[safe] / n_groups[safe]
    out = np.zeros(n_pos)
    out[kept_idx] = w_neutral
    nz = out[out != 0]
    if len(nz):
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5
    return out


weight = compute_weights_unconstrained(TRIM, TILT_POWER)

# quick diagnostic: how net long/short does this actually run, quarter to quarter?
active = pos[weight != 0].copy()
active["w"] = weight[weight != 0]
net_by_q = active.groupby("ann_quarter")["w"].sum()
gross_by_q = active.groupby("ann_quarter")["w"].apply(lambda x: x.abs().sum())
net_frac = (net_by_q / gross_by_q)
print(f"net exposure as a fraction of gross, by quarter: mean={net_frac.mean():.3f}, "
      f"std={net_frac.std():.3f}, min={net_frac.min():.3f}, max={net_frac.max():.3f}")

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
                             net_exposure=float(np.sum(target)) if q_mask.any() else 0.0,
                             quarter_realized_pnl=quarter_pnl))
    trailing_nav += quarter_pnl

pos_out = pos.copy()
pos_out["weight"] = weight
pos_out = pos_out[pos_out["weight"] != 0].reset_index(drop=True)
pos_out.to_parquet(DATA / "positions_strategy7_v2.parquet", index=False)

entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
exit_costs = entry_costs.copy()
daily_pnl = np.zeros(n_days)
np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
np.add.at(daily_pnl, exit_cal_idx, -exit_costs)
flat_notional = notional[rep_pos_idx]
np.add.at(daily_pnl, flat_cal_idx, flat_notional * flat_ret_raw)

turnover_dollars = np.zeros(n_days)
np.add.at(turnover_dollars, entry_cal_idx, np.abs(notional))
np.add.at(turnover_dollars, exit_cal_idx, np.abs(notional))
exposure_delta = np.zeros(n_days + 1)
open_delta = np.zeros(n_days + 1)
net_exposure_delta = np.zeros(n_days + 1)
np.add.at(exposure_delta, entry_cal_idx, np.abs(notional))
np.add.at(exposure_delta, exit_cal_idx, -np.abs(notional))
np.add.at(net_exposure_delta, entry_cal_idx, notional)
np.add.at(net_exposure_delta, exit_cal_idx, -notional)
np.add.at(open_delta, entry_cal_idx, (weight != 0).astype(float))
np.add.at(open_delta, exit_cal_idx, -(weight != 0).astype(float))
gross_exposure = np.cumsum(exposure_delta[:n_days])
net_exposure = np.cumsum(net_exposure_delta[:n_days])
n_open = np.cumsum(open_delta[:n_days]).astype(int)

nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
true_daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], nav[:-1]])

out = pd.DataFrame({"date": list(calendar_dt), "daily_pnl": daily_pnl, "nav": nav,
                     "gross_exposure": gross_exposure, "net_exposure": net_exposure,
                     "n_open_positions": n_open, "turnover_dollars": turnover_dollars,
                     "daily_return": true_daily_ret})
out.to_csv(DATA / "backtest_v2_strategy7.csv", index=False)
pd.DataFrame(quarter_log).to_csv(DATA / "backtest_v2_strategy7_quarterlog.csv", index=False)

years = n_days / 252.0
total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
ann_ret = (1 + total_ret) ** (1 / years) - 1
ann_vol = pd.Series(true_daily_ret).std(ddof=1) * np.sqrt(252)
sharpe = (pd.Series(true_daily_ret).mean() * 252) / ann_vol
running_max = np.maximum.accumulate(nav)
max_dd = ((nav - running_max) / running_max).min()
liquidity_capped = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)
total_costs = entry_costs.sum() + exit_costs.sum()
annual_turnover = turnover_dollars.sum() / years / INITIAL_CAPITAL
# correlation to the market -- the whole point of this diagnostic: how much beta crept in
corr_to_mkt = np.corrcoef(true_daily_ret, mkt.set_index("date").reindex(calendar_dt)["vwretd"].fillna(0.0).to_numpy())[0, 1]

summary = dict(
    name="strategy7_unconstrained_net_exposure", trim=TRIM, tilt_power=TILT_POWER,
    base_unit_fraction=BUF, liquidity_cap_frac=LIQUIDITY_CAP_FRAC, max_gross_leverage=MAX_GROSS_LEVERAGE,
    n_positions=int((weight != 0).sum()), total_return=total_ret, annualized_return=ann_ret,
    annualized_vol=ann_vol, sharpe=sharpe, max_drawdown=max_dd,
    avg_gross_exposure=float(gross_exposure.mean()), avg_net_exposure=float(net_exposure.mean()),
    avg_net_exposure_pct_of_gross=float((net_exposure / np.where(gross_exposure == 0, np.nan, gross_exposure)).mean()),
    correlation_to_market=float(corr_to_mkt),
    avg_n_open_positions=float(n_open.mean()), max_n_open_positions=int(n_open.max()),
    total_transaction_costs=float(total_costs), annual_turnover_x_capital=float(annual_turnover),
    pct_liquidity_capped=float(liquidity_capped.mean()), final_nav=float(nav[-1]),
    n_quarters_leverage_capped=int(sum(1 for q in quarter_log if q["cap_status"] == "priority_capped")),
)
with open(DATA / "backtest_v2_strategy7_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"strategy7: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} Sharpe={sharpe:.3f} "
      f"maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f} corr_to_mkt={corr_to_mkt:.3f}")
