"""
Strategy 8: Strategy 6's exact construction (trim=0.15, tilt_power=1.0, size+sector-neutral,
priority-based leverage cap at 2.5x, base_unit_fraction=0.003), with one change -- the conviction
rank used for trim/tilt/sizing is a BLEND of sue_rank_pct (weight 0.80) and analyst estimate
DISPERSION rank (weight 0.20, from raw_wrds/ibes/statsumu_epsus.parquet's `stdev`/`meanest`,
point-in-time-safe as of the latest consensus snapshot strictly before anndats -- see
scripts/36_revision_dispersion_diagnostic.py for the full diagnostic this is built from).

IMPORTANT framing, carried over honestly from the diagnostic rather than oversold here: dispersion
has NO standalone directional predictive power in this data (a quintile sort of forward returns by
dispersion alone is flat, t=-0.43) -- this is NOT a new alpha signal in the sense revenue surprise
or a genuine second SUE measure would be. What blending it in at a modest weight actually does is
change WHICH low-conviction positions get trimmed: composition checks (avg size_quintile,
liquidity_quintile, dollar volume) are essentially unchanged between the pure-SUE and
dispersion-blended books, so this isn't a disguised size/liquidity quality filter either. The
effect is best understood as a genuine RISK/variance filter -- de-emphasizing positions where
analysts disagree most about the estimate the surprise is being measured against, i.e. where the
"surprise" itself is measured against a noisier consensus and is more likely to be noise rather
than real information -- not a return-predictive tilt. Framed this way (following scripts/27's own
convention: EAR "adds nothing," Strategy 5's sector-neutrality "removes an unpriced risk" --
neither claimed as new alpha), this is the Strategy-5-style result: better risk-adjusted profile,
not more expected return.

blend_weight=0.20 chosen from scripts/36's finer sweep: Sharpe stays in a ~1.28-1.32 plateau across
blend_weight 0.05-0.25 while max drawdown steadily improves (-17.3% at blend_weight=0 to -9.4% at
0.25); 0.20 lands in that plateau with a strong drawdown improvement (-10.8%, essentially matching
Strategy 5's -10.8%) while keeping meaningfully more return than Strategy 5 (5.96% vs. 4.06%).

Produces the same output shape as Strategy 6 (scripts/24) so it drops into the same
report/charting pipeline: data/positions_strategy8_v2.parquet, data/backtest_v2_strategy8.csv,
data/backtest_v2_strategy8_quarterlog.csv, data/backtest_v2_strategy8_summary.json.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
# Untracked WRDS-derived data lives only in the main checkout on this machine, not in this
# worktree (see .gitignore).
RAW = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data/normalized_equity")
MAIN = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data")

INITIAL_CAPITAL = 10_000_000.0
LIQUIDITY_CAP_FRAC = 0.05
MAX_GROSS_LEVERAGE = 2.5
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15
TRIM, TILT_POWER, BUF = 0.15, 1.0, 0.003
DISPERSION_BLEND_WEIGHT = 0.20

# ---------- build the point-in-time-safe dispersion signal (same as scripts/36) ----------
print("building point-in-time dispersion signal from IBES consensus summary...")
ss = pd.read_parquet(MAIN / "raw_wrds" / "ibes" / "statsumu_epsus.parquet",
                      columns=["ticker", "statpers", "fpedats", "fpi", "measure", "meanest", "stdev"])
ss = ss[(ss["fpi"] == "6") & (ss["measure"] == "EPS")].copy()
ss["statpers"] = pd.to_datetime(ss["statpers"])
ss["fpedats"] = pd.to_datetime(ss["fpedats"])
ss = ss.dropna(subset=["fpedats", "meanest"])

ev_ids = pd.read_parquet(DATA / "decile_events.parquet",
                          columns=["permno", "day0_date", "ibes_ticker", "fpedats", "anndats", "ann_quarter"])
ev_ids["fpedats"] = pd.to_datetime(ev_ids["fpedats"])
ev_ids["anndats"] = pd.to_datetime(ev_ids["anndats"])

m = ev_ids.merge(ss, left_on=["ibes_ticker", "fpedats"], right_on=["ticker", "fpedats"], how="inner")
m = m[m["statpers"] < m["anndats"]].sort_values("statpers")
latest = m.groupby(["permno", "day0_date"], as_index=False).last()
latest["dispersion_raw"] = latest["stdev"] / latest["meanest"].abs()
latest.loc[latest["meanest"].abs() < 1e-6, "dispersion_raw"] = np.nan
latest["dispersion_rank_pct"] = latest.groupby("ann_quarter")["dispersion_raw"].rank(pct=True)
dispersion_lookup = latest[["permno", "day0_date", "dispersion_rank_pct"]]
print(f"events with a usable dispersion value: {dispersion_lookup['dispersion_rank_pct'].notna().sum():,} "
      f"/ {len(ev_ids):,}")

# ---------- everything below mirrors scripts/24_finalize_strategy6.py exactly ----------
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
pos = pos.merge(dispersion_lookup, on=["permno", "day0_date"], how="left")
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


def compute_weights_size_sector_dispersion(trim, tilt_power, dispersion_blend_weight):
    sue_rank = pos["sue_rank_pct"].to_numpy()
    disp_rank = pos["dispersion_rank_pct"].to_numpy()
    disp_rank = np.where(np.isnan(disp_rank), 0.5, disp_rank)
    blended = (1 - dispersion_blend_weight) * sue_rank + dispersion_blend_weight * disp_rank
    d = blended - 0.5
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


weight = compute_weights_size_sector_dispersion(TRIM, TILT_POWER, DISPERSION_BLEND_WEIGHT)

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
pos_out.to_parquet(DATA / "positions_strategy8_v2.parquet", index=False)

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
daily_ret_old = np.diff(nav, prepend=INITIAL_CAPITAL) / INITIAL_CAPITAL  # legacy convention, kept
                                                                          # for parity with scripts/24's
                                                                          # own summary.json field
true_daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], nav[:-1]])

out = pd.DataFrame({"date": list(calendar_dt), "daily_pnl": daily_pnl, "nav": nav,
                     "gross_exposure": gross_exposure, "n_open_positions": n_open,
                     "turnover_dollars": turnover_dollars, "daily_return": daily_ret_old})
out.to_csv(DATA / "backtest_v2_strategy8.csv", index=False)
pd.DataFrame(quarter_log).to_csv(DATA / "backtest_v2_strategy8_quarterlog.csv", index=False)

years = n_days / 252.0
total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
ann_ret = (1 + total_ret) ** (1 / years) - 1
# corrected (true, nav.pct_change()-based) Sharpe/vol, matching results_summary_v2_FINAL.csv's
# convention rather than scripts/24's own legacy summary.json field (see README's documented bug)
ann_vol = pd.Series(true_daily_ret).std(ddof=1) * np.sqrt(252)
sharpe = (pd.Series(true_daily_ret).mean() * 252) / ann_vol if ann_vol > 0 else np.nan
running_max = np.maximum.accumulate(nav)
max_dd = ((nav - running_max) / running_max).min()
liquidity_capped = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)
total_costs = entry_costs.sum() + exit_costs.sum()
annual_turnover = turnover_dollars.sum() / years / INITIAL_CAPITAL

summary = dict(
    name="strategy8_dispersion_filter", trim=TRIM, tilt_power=TILT_POWER,
    base_unit_fraction=BUF, liquidity_cap_frac=LIQUIDITY_CAP_FRAC,
    dispersion_blend_weight=DISPERSION_BLEND_WEIGHT,
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
with open(DATA / "backtest_v2_strategy8_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"\nstrategy8 (dispersion filter, blend={DISPERSION_BLEND_WEIGHT}): ann.ret={ann_ret:.2%} "
      f"ann.vol={ann_vol:.2%} Sharpe={sharpe:.3f} maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f}")
print(f"vs. Strategy 6 (tracked): ann.ret=6.49% Sharpe=1.30 maxDD=-16.6%")
