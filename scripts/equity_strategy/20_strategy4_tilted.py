"""
Strategy 4: a "trimmed, tilted, size-neutral" strategy designed from what the first three
strategies revealed about why extreme decile wins on return and rank-weighted wins on Sharpe.

What the first three showed, in numbers (see backtest_v2_*_summary.json):
  - extreme:      458 avg open positions, leverage cap binds 15% of quarters,  35% of positions
                   liquidity-capped, ann.ret 4.91%, Sharpe 0.76 -- concentrating capital only in
                   the highest-conviction bets (D1/D10) means each dollar goes to the trades with
                   the largest measured average edge, and there's enough headroom under both the
                   liquidity and leverage caps to size those bets up meaningfully.
  - rankweighted: 1,934 avg open positions (4.2x more), leverage cap binds 90% of quarters, 99.3%
                   of positions liquidity-capped, ann.ret 3.88%, Sharpe 0.88 -- trading every
                   decile (including the near-zero-conviction middle ones) buys real diversification
                   (lower vol, better Sharpe) but the sheer position count means the book is almost
                   always leverage-capped AND almost every single position is individually
                   liquidity-capped. Both caps historically apply a UNIFORM proportional haircut,
                   which cuts the strongest and weakest convictions by the identical percentage --
                   diluting exactly the positions that were driving the edge.
  - balanced:     same 458 positions as extreme, but size-quintile neutralized -- never once hits
                   the leverage cap even at extreme's sizing, ann.ret 4.74%, Sharpe 0.76. Size
                   neutralization alone buys back most of extreme's leverage headroom.

Strategy 4 combines these lessons:
  1. TRIM the universe to only the events with real conviction (|sue_rank_pct - 0.5| > TRIM),
     dropping the near-zero-signal middle deciles that rank-weighted pays turnover/cost for with
     little edge to show for it.
  2. TILT the weight within the kept universe as sign(d) * (|d|/0.5)^TILT_POWER (d = rank - 0.5)
     instead of the plain linear 2*(rank-0.5) -- concentrates capital further toward the true
     extremes without going fully binary like the two-decile "extreme" strategy.
  3. SIZE-NEUTRALIZE by (quarter, side, size quintile), same idea as strategy 3 ("balanced"),
     so no single quarter's book is dominated by whichever size bucket happened to fire the most
     events -- this is what let "balanced" avoid the leverage cap entirely at extreme's sizing.
  4. PRIORITY-BASED cap trimming: when a quarter's requested gross exceeds the 1.5x leverage cap,
     instead of shrinking every position by the same percentage (which is what strategies 1-3 do,
     and is exactly the distortion that hurts rank-weighted), positions are ranked by their own
     conviction (|target notional|) and capital is allocated highest-conviction-first until the
     budget is exhausted -- the lowest-conviction positions in an over-budget quarter are dropped
     to zero rather than everyone getting shaved.

A grid over TRIM x TILT_POWER x base_unit_fraction is swept (liquidity_cap_frac held at 0.05, the
level established as reasonable earlier) to find a good setting, then that setting is run through
to produce the same outputs as the other three strategies.

Output: data/positions_strategy4_v2.parquet, data/backtest_v2_strategy4.csv,
        data/backtest_v2_strategy4_quarterlog.csv, data/backtest_v2_strategy4_summary.json,
        data/sweep_strategy4.csv (the grid search), data/backtest_v2_comparison.csv (updated,
        now with all four strategies)
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
MAX_GROSS_LEVERAGE = 1.5
LIQUIDITY_CAP_FRAC = 0.05
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

# ---------- one-time setup: full rank-weighted universe (all deciles), param-independent ----------
pos = pd.read_parquet(DATA / "positions_rankweighted_v2.parquet").reset_index(drop=True)
pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
pos["day0_date"] = pd.to_datetime(pos["day0_date"])
pos["exit_date"] = pd.to_datetime(pos["exit_date"])
n_pos = len(pos)

entry_qcode = pos["day0_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
exit_qcode = pos["exit_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
liq_q = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
cost_bps = liq_q.map(COST_BPS).fillna(DEFAULT_COST_BPS).to_numpy()
adv21 = pos["avg_dollar_volume_21d"].to_numpy()

entry_cal_idx = pos["day0_date"].map(date_to_idx).to_numpy()
exit_cal_idx = pos["exit_date"].map(date_to_idx).to_numpy()

entry_idx = pos["entry_row_idx"].to_numpy()
exit_idx = pos["exit_row_idx"].to_numpy()
lengths = exit_idx - entry_idx
total_rows = int(lengths.sum())
print(f"strategy4 universe: {n_pos:,} candidate events, {total_rows:,} position-days to expand")

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


def compute_weights(trim, tilt_power):
    """Trim to conviction events, tilt by rank-distance, size-neutralize per (quarter, side)."""
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
    if len(sub) == 0:
        return w
    grp_key = list(zip(sub["ann_quarter"], sub["side"], sub["size_quintile"]))
    sub["grp_key"] = grp_key
    grp_totals = sub.groupby("grp_key")["w"].transform(lambda x: x.abs().sum())
    n_quintiles = sub.groupby([sub["ann_quarter"], sub["side"]])["size_quintile"].transform("nunique").to_numpy()
    grp_totals = grp_totals.to_numpy()
    safe = grp_totals > 0
    w_neutral = np.zeros(len(sub))
    w_neutral[safe] = sub["w"].to_numpy()[safe] / grp_totals[safe] / n_quintiles[safe]

    out = np.zeros(n_pos)
    out[kept_idx] = w_neutral
    nz = out[out != 0]
    if len(nz) > 0:
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5  # rescale to a comparable mean |weight|
    return out


def simulate_priority(weight, base_unit_fraction, liquidity_cap_frac, max_gross_leverage=MAX_GROSS_LEVERAGE):
    liq_cap_dollars = np.where(np.isfinite(adv21), liquidity_cap_frac * adv21, np.inf)
    trailing_nav = INITIAL_CAPITAL
    notional = np.zeros(n_pos)
    base_unit_at_entry = np.zeros(n_pos)
    n_capped_quarters = 0
    n_dropped_positions = 0

    for qi in range(n_quarters):
        q_mask = (entry_qcode == qi) & (weight != 0)
        base_unit = trailing_nav * base_unit_fraction
        if q_mask.any():
            idx = np.where(q_mask)[0]
            target = weight[idx] * base_unit
            cap = liq_cap_dollars[idx]
            target = np.sign(target) * np.minimum(np.abs(target), cap)
            gross_requested = float(np.abs(target).sum())
            max_gross = max_gross_leverage * trailing_nav
            q_notional = target.copy()
            if gross_requested > max_gross and gross_requested > 0:
                n_capped_quarters += 1
                order_priority = np.argsort(-np.abs(target))  # highest conviction first
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
                    n_dropped_positions += (len(target) - n_kept - (1 if remaining > 0 else 0))
            notional[idx] = q_notional
            base_unit_at_entry[idx] = base_unit

        s, e = boundaries[qi], boundaries[qi + 1]
        trade_pnl_q = float(np.sum(notional[rep_pos_idx_sorted[s:e]] * flat_ret_sorted[s:e])) if e > s else 0.0
        entry_mask = q_mask
        entry_cost_q = float(np.sum(np.abs(notional[entry_mask]) * cost_bps[entry_mask] / 10_000.0)) if entry_mask.any() else 0.0
        exit_mask = (exit_qcode == qi) & (weight != 0)
        exit_cost_q = float(np.sum(np.abs(notional[exit_mask]) * cost_bps[exit_mask] / 10_000.0)) if exit_mask.any() else 0.0
        trailing_nav += trade_pnl_q - entry_cost_q - exit_cost_q

    entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
    exit_costs = entry_costs.copy()
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
    np.add.at(daily_pnl, exit_cal_idx, -exit_costs)
    flat_notional = notional[rep_pos_idx]
    np.add.at(daily_pnl, flat_cal_idx, flat_notional * flat_ret)

    exposure_delta = np.zeros(n_days + 1)
    open_delta = np.zeros(n_days + 1)
    np.add.at(exposure_delta, entry_cal_idx, np.abs(notional))
    np.add.at(exposure_delta, exit_cal_idx, -np.abs(notional))
    np.add.at(open_delta, entry_cal_idx, (weight != 0).astype(float))
    np.add.at(open_delta, exit_cal_idx, -(weight != 0).astype(float))
    gross_exposure = np.cumsum(exposure_delta[:n_days])
    n_open = np.cumsum(open_delta[:n_days])

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
    total_costs = entry_costs.sum() + exit_costs.sum()

    return dict(
        ann_ret=ann_ret, ann_vol=ann_vol, sharpe=sharpe, max_dd=max_dd,
        avg_leverage=float(gross_exposure.mean() / INITIAL_CAPITAL),
        avg_n_open=float(n_open.mean()), n_active_positions=int((weight != 0).sum()),
        pct_leverage_capped_quarters=n_capped_quarters / n_quarters,
        pct_liquidity_capped=float(liquidity_capped.mean()),
        total_costs=float(total_costs), final_nav=float(nav[-1]),
        n_dropped_positions_total=n_dropped_positions,
    ), notional


# ---------- grid search ----------
TRIM_GRID = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
TILT_GRID = [1.0, 1.5, 2.0]
BUF_GRID = [0.002, 0.003, 0.004]

rows = []
for trim in TRIM_GRID:
    for tilt_power in TILT_GRID:
        w = compute_weights(trim, tilt_power)
        for buf in BUF_GRID:
            r, _ = simulate_priority(w, buf, LIQUIDITY_CAP_FRAC)
            r.update(trim=trim, tilt_power=tilt_power, base_unit_fraction=buf)
            rows.append(r)

sweep = pd.DataFrame(rows)
sweep.to_csv(DATA / "sweep_strategy4.csv", index=False)
pd.set_option("display.width", 160)
print(sweep[["trim", "tilt_power", "base_unit_fraction", "ann_ret", "ann_vol", "sharpe", "max_dd",
             "avg_leverage", "avg_n_open", "n_active_positions", "pct_leverage_capped_quarters",
             "pct_liquidity_capped"]].sort_values("sharpe", ascending=False).head(15).to_string(index=False))

# pick the best Sharpe among points with return at or above extreme's 4.91% (so it's a genuine
# improvement, not just a lower-risk/lower-return point)
candidates = sweep[sweep.ann_ret >= 0.0491]
if len(candidates) == 0:
    candidates = sweep
best = candidates.sort_values("sharpe", ascending=False).iloc[0]
print(f"\nSelected: trim={best.trim}, tilt_power={best.tilt_power}, base_unit_fraction={best.base_unit_fraction}")
print(best)

# ---------- finalize the chosen configuration ----------
w_final = compute_weights(best.trim, best.tilt_power)
final_summary, notional_final = simulate_priority(w_final, best.base_unit_fraction, LIQUIDITY_CAP_FRAC)

pos_out = pos.copy()
pos_out["weight"] = w_final
pos_out = pos_out[pos_out["weight"] != 0].reset_index(drop=True)
pos_out.to_parquet(DATA / "positions_strategy4_v2.parquet", index=False)

# rebuild the daily NAV series / quarter log for the chosen config (same structure as script 17)
liq_cap_dollars = np.where(np.isfinite(adv21), LIQUIDITY_CAP_FRAC * adv21, np.inf)
trailing_nav = INITIAL_CAPITAL
notional = np.zeros(n_pos)
base_unit_at_entry = np.zeros(n_pos)
quarter_log = []
weight = w_final
for qi in range(n_quarters):
    q_mask = (entry_qcode == qi) & (weight != 0)
    base_unit = trailing_nav * best.base_unit_fraction
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
out.to_csv(DATA / "backtest_v2_strategy4.csv", index=False)
pd.DataFrame(quarter_log).to_csv(DATA / "backtest_v2_strategy4_quarterlog.csv", index=False)

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
    name="strategy4_tilted", trim=float(best.trim), tilt_power=float(best.tilt_power),
    base_unit_fraction=float(best.base_unit_fraction), liquidity_cap_frac=LIQUIDITY_CAP_FRAC,
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
with open(DATA / "backtest_v2_strategy4_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"\nstrategy4: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} Sharpe={sharpe:.2f} "
      f"maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f}")

# ---------- update the 4-strategy comparison table ----------
comparison_rows = []
for name in ["extreme", "rankweighted", "balanced"]:
    with open(DATA / f"backtest_v2_{name}_summary.json") as f:
        comparison_rows.append(json.load(f))
comparison_rows.append(summary | dict(name="strategy4_tilted"))
pd.DataFrame(comparison_rows).to_csv(DATA / "backtest_v2_comparison.csv", index=False)
print("\nwrote data/backtest_v2_comparison.csv (4 strategies)")
