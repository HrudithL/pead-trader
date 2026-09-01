"""
Two things, both evidence-based rather than argued in the abstract:

1. What does raising MAX_GROSS_LEVERAGE actually do to strategy 4 (trim=0.15, tilt=1.0,
   base_unit_fraction=0.002), now that it uses priority-based capping instead of proportional
   shaving? Track not just return/Sharpe/drawdown but CONCENTRATION -- how much of the book ends
   up in just a handful of names in a capped quarter -- since priority-based trimming, unlike
   proportional shaving, doesn't spread the pain: it keeps full size on the top few convictions
   and zeroes out the rest, which is healthy at a mild cap-bind rate but becomes a single-name
   blowup risk if pushed too far.

2. A quick empirical read on the two candidate structural improvements that don't just retune an
   existing knob: (a) more frequent (monthly vs quarterly) trailing-NAV resizing, to see if it
   measurably smooths drawdowns without giving up return, and (b) sector concentration -- whether
   the earnings-surprise signal already embeds unintentional sector bets that a neutralization
   step (like the size-quintile one already applied) could remove.

Output: data/sweep_strategy4_leverage.csv, printed sector-concentration and rebalance-cadence
        diagnostics.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, RAW_EQUITY_DIR, INITIAL_CAPITAL

DATA = DATA_DIR
RAW = RAW_EQUITY_DIR
LIQUIDITY_CAP_FRAC = 0.05
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


def compute_weights(trim, tilt_power):
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
    n_quintiles = sub.groupby([sub["ann_quarter"], sub["side"]])["size_quintile"].transform("nunique").to_numpy()
    safe = grp_totals > 0
    w_neutral = np.zeros(len(sub))
    w_neutral[safe] = sub["w"].to_numpy()[safe] / grp_totals[safe] / n_quintiles[safe]
    out = np.zeros(n_pos)
    out[kept_idx] = w_neutral
    nz = out[out != 0]
    if len(nz):
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5
    return out


weight = compute_weights(TRIM, TILT_POWER)


def simulate_priority(weight, base_unit_fraction, liquidity_cap_frac, max_gross_leverage):
    liq_cap_dollars = np.where(np.isfinite(adv21), liquidity_cap_frac * adv21, np.inf)
    trailing_nav = INITIAL_CAPITAL
    notional = np.zeros(n_pos)
    base_unit_at_entry = np.zeros(n_pos)
    n_capped_quarters = 0
    max_single_name_frac = []       # per capped quarter: largest |notional| / trailing_nav
    n_active_in_capped_q = []       # how many names actually get non-zero notional that quarter
    n_requested_in_capped_q = []

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
                n_active = int((q_notional != 0).sum())
                n_active_in_capped_q.append(n_active)
                n_requested_in_capped_q.append(len(target))
                max_single_name_frac.append(float(np.abs(q_notional).max() / trailing_nav) if trailing_nav > 0 else 0.0)
            notional[idx] = q_notional
            base_unit_at_entry[idx] = base_unit

        s, e = boundaries[qi], boundaries[qi + 1]
        trade_pnl_q = float(np.sum(notional[rep_pos_idx_sorted[s:e]] * flat_ret_sorted[s:e])) if e > s else 0.0
        entry_cost_q = float(np.sum(np.abs(notional[q_mask]) * cost_bps[q_mask] / 10_000.0)) if q_mask.any() else 0.0
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
    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / INITIAL_CAPITAL
    years = n_days / 252.0
    total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()

    return dict(
        max_gross_leverage=max_gross_leverage, ann_ret=ann_ret, ann_vol=ann_vol, sharpe=sharpe,
        max_dd=max_dd, pct_leverage_capped_quarters=n_capped_quarters / n_quarters,
        avg_max_single_name_frac_when_capped=float(np.mean(max_single_name_frac)) if max_single_name_frac else 0.0,
        worst_max_single_name_frac=float(np.max(max_single_name_frac)) if max_single_name_frac else 0.0,
        avg_names_kept_when_capped=float(np.mean(n_active_in_capped_q)) if n_active_in_capped_q else np.nan,
        avg_names_requested_when_capped=float(np.mean(n_requested_in_capped_q)) if n_requested_in_capped_q else np.nan,
        final_nav=float(nav[-1]),
    )


print("\n=== MAX_GROSS_LEVERAGE sweep for strategy 4 (priority-based capping) ===")
rows = []
for lev in [1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 10.0]:
    r = simulate_priority(weight, BUF, LIQUIDITY_CAP_FRAC, lev)
    rows.append(r)
lev_sweep = pd.DataFrame(rows)
lev_sweep.to_csv(DATA / "sweep_strategy4_leverage.csv", index=False)
pd.set_option("display.width", 160)
print(lev_sweep.to_string(index=False))

# ---------- sector concentration diagnostic ----------
print("\n=== sector concentration check (strategy 4 active positions, trim=0.15) ===")
active = pos[weight != 0].copy()
active["w"] = weight[weight != 0]
if "ff12_sector" in active.columns:
    sector_gross = active.groupby(["ann_quarter", "ff12_sector"])["w"].apply(lambda x: x.abs().sum())
    sector_share = sector_gross / sector_gross.groupby("ann_quarter").transform("sum")
    max_share_per_q = sector_share.groupby("ann_quarter").max()
    print(f"avg quarterly max single-sector share of gross weight: {max_share_per_q.mean():.1%}")
    print(f"worst quarterly max single-sector share: {max_share_per_q.max():.1%}")
    print(f"(12 sectors => equal share would be {1/12:.1%} each)")
else:
    print("ff12_sector column not present")
