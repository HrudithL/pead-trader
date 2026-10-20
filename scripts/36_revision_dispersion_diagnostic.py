"""
Diagnostic: do IBES analyst REVISIONS (net up/down revisions in the consensus leading into the
announcement) or DISPERSION (how much analysts disagree) carry information beyond sue_analyst
(the surprise itself)? Both are already computed by IBES in raw_wrds/ibes/statsumu_epsus.parquet
(numup, numdown, stdev, meanest, numest) -- this project has never touched that file; only the
already-resolved sue_analyst/sue_seasonal surprise measures in the events file have been used.

Unlike sue_seasonal (a second measure of the SAME earnings-surprise event), these describe analyst
BEHAVIOR going into the announcement -- a genuinely different kind of information, and the
candidate expected to have the best odds of adding real breadth (see README/conversation history
on the Fundamental Law of Active Management: IR ~ IC x sqrt(breadth)).

Point-in-time construction: statsumu_epsus is IBES's own monthly consensus summary, keyed by
(ticker, statpers, fpedats, fpi). fpi='6' is the current-fiscal-quarter forecast (confirmed against
this project's own quarterly fpedats cadence). For each event, take the LATEST statpers strictly
before anndats -- the most recent pre-announcement snapshot -- so nothing here uses information
that wasn't public before the trade would have been placed.

  dispersion_raw = stdev / |meanest|        (Diether/Malloy/Scherbina convention)
  revision_net   = (numup - numdown) / numest

Both ranked cross-sectionally within ann_quarter, matching sue_rank_pct's own construction.

Same two-part structure as scripts/27_ear_signal_diagnostic.py and scripts/38: Part 1
(characterization: correlation with SUE, standalone Fama-MacBeth quintile sort) then Part 2 (an
actual blended trading-weight test, swept over blend weight, using scripts/27's own
compute_weights_blend/simulate pattern -- Strategy 6's exact trim/tilt/size+sector-neutral,
priority-capped construction).

No file output -- a diagnostic that informs a design decision, not a strategy build.
"""
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data/normalized_equity")
MAIN = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data")

# ---------- build the point-in-time-safe revision/dispersion signal ----------
print("loading IBES consensus summary (statsumu_epsus)...")
ss = pd.read_parquet(MAIN / "raw_wrds" / "ibes" / "statsumu_epsus.parquet",
                      columns=["ticker", "statpers", "fpedats", "fpi", "measure",
                               "meanest", "numest", "stdev", "numup", "numdown"])
ss = ss[(ss["fpi"] == "6") & (ss["measure"] == "EPS")].copy()
ss["statpers"] = pd.to_datetime(ss["statpers"])
ss["fpedats"] = pd.to_datetime(ss["fpedats"])
ss = ss.dropna(subset=["fpedats", "meanest", "numest", "numup", "numdown"])
print(f"statsumu_epsus rows (fpi=6, EPS): {len(ss):,}")

ev = pd.read_parquet(DATA / "decile_events.parquet",
                      columns=["permno", "day0_date", "ibes_ticker", "fpedats", "anndats",
                                "ann_quarter", "sue_rank_pct"])
ev["fpedats"] = pd.to_datetime(ev["fpedats"])
ev["anndats"] = pd.to_datetime(ev["anndats"])
n_events = len(ev)
print(f"events to match: {n_events:,}")

merged = ev.merge(ss, left_on=["ibes_ticker", "fpedats"], right_on=["ticker", "fpedats"], how="inner")
merged = merged[merged["statpers"] < merged["anndats"]]
print(f"candidate (event, pre-announcement snapshot) pairs: {len(merged):,}")

# most recent pre-announcement snapshot per event
merged = merged.sort_values("statpers")
latest = merged.groupby(["permno", "day0_date"], as_index=False).last()
print(f"events with a usable point-in-time revision/dispersion snapshot: {len(latest):,} / {n_events:,} "
      f"({len(latest) / n_events:.1%})")

latest["dispersion_raw"] = latest["stdev"] / latest["meanest"].abs()
latest["revision_net"] = (latest["numup"] - latest["numdown"]) / latest["numest"]
latest.loc[latest["meanest"].abs() < 1e-6, "dispersion_raw"] = np.nan

sig = latest[["permno", "day0_date", "ann_quarter", "sue_rank_pct", "dispersion_raw", "revision_net"]].copy()
sig["dispersion_rank_pct"] = sig.groupby("ann_quarter")["dispersion_raw"].rank(pct=True)
sig["revision_rank_pct"] = sig.groupby("ann_quarter")["revision_net"].rank(pct=True)

# ---------- Part 1: characterization ----------
ff = pd.read_parquet(DATA / "decile_events_ff_adjusted.parquet",
                      columns=["permno", "day0_date", "ret_fwd_60d_mktadj_adj"])
ff["day0_date"] = pd.to_datetime(ff["day0_date"])
sig["day0_date"] = pd.to_datetime(sig["day0_date"])
char = sig.merge(ff, on=["permno", "day0_date"], how="inner")


def fama_macbeth(df, ret_col, q_col="ann_quarter"):
    sub = df[[q_col, ret_col]].dropna(subset=[ret_col])
    qmeans = sub.groupby(q_col)[ret_col].mean()
    n_q = qmeans.shape[0]
    mean = qmeans.mean()
    se = qmeans.std(ddof=1) / np.sqrt(n_q)
    t = mean / se if se > 0 else np.nan
    return mean, se, t, len(sub), n_q


for name, rank_col in [("dispersion", "dispersion_rank_pct"), ("revision", "revision_rank_pct")]:
    sub = char.dropna(subset=[rank_col, "ret_fwd_60d_mktadj_adj"]).copy()
    print(f"\n=== {name} ===")
    print(f"correlation sue_rank_pct vs {rank_col}:", sub[["sue_rank_pct", rank_col]].corr().iloc[0, 1])
    sub["q"] = sub.groupby("ann_quarter")[rank_col].transform(lambda x: pd.qcut(x, 5, labels=False, duplicates="drop"))
    print(f"Fama-MacBeth: 60d forward return by {name} quintile:")
    for q in sorted(sub["q"].dropna().unique()):
        mean, se, t, n, nq = fama_macbeth(sub[sub["q"] == q], "ret_fwd_60d_mktadj_adj")
        print(f"  quintile {int(q)}: mean={mean:.4f} se={se:.4f} t={t:.2f} n={n:,}")
    top = sub[sub["q"] == sub["q"].max()].groupby("ann_quarter")["ret_fwd_60d_mktadj_adj"].mean()
    bot = sub[sub["q"] == 0].groupby("ann_quarter")["ret_fwd_60d_mktadj_adj"].mean()
    common = top.index.intersection(bot.index)
    spread = top.loc[common] - bot.loc[common]
    t_spread = spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))
    print(f"  top-bottom spread: mean={spread.mean():.4f} t={t_spread:.2f} n_quarters={len(spread)}")

# ---------- Part 2: actual blended trading-weight test ----------
print("\n" + "=" * 70)
print("Part 2: blending dispersion / revision into Strategy 6's weight construction")
print("=" * 70)

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

pos2 = pos.merge(sig[["permno", "day0_date", "dispersion_rank_pct", "revision_rank_pct"]],
                  on=["permno", "day0_date"], how="left")
print(f"positions with a usable revision/dispersion value: {pos2['revision_rank_pct'].notna().sum():,} / {len(pos2):,}")


def compute_weights_blend(trim, tilt_power, blend_weight, signal_col):
    sue_rank = pos2["sue_rank_pct"].to_numpy()
    sig_rank = pos2[signal_col].to_numpy()
    sig_rank = np.where(np.isnan(sig_rank), 0.5, sig_rank)
    blended_rank = (1 - blend_weight) * sue_rank + blend_weight * sig_rank
    d = blended_rank - 0.5
    frac = np.clip(np.abs(d) / 0.5, 0, 1)
    tilt = np.sign(d) * (frac ** tilt_power)
    keep = np.abs(d) > trim
    w = np.where(keep, tilt, 0.0)
    df = pos2[["ann_quarter", "size_quintile", "ff12_sector"]].copy()
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


def simulate(weight, base_unit_fraction, liquidity_cap_frac, max_gross_leverage):
    liq_cap_dollars = np.where(np.isfinite(adv21), liquidity_cap_frac * adv21, np.inf)
    trailing_nav = INITIAL_CAPITAL
    notional = np.zeros(n_pos)
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
            notional[idx] = q_notional
        s, e = boundaries[qi], boundaries[qi + 1]
        trade_pnl_q = float(np.sum(notional[rep_pos_idx_sorted[s:e]] * flat_ret_sorted[s:e])) if e > s else 0.0
        entry_cost_q = float(np.sum(np.abs(notional[q_mask]) * cost_bps[q_mask] / 10_000.0)) if q_mask.any() else 0.0
        exit_mask = (exit_qcode == qi) & (weight != 0)
        exit_cost_q = float(np.sum(np.abs(notional[exit_mask]) * cost_bps[exit_mask] / 10_000.0)) if exit_mask.any() else 0.0
        trailing_nav += trade_pnl_q - entry_cost_q - exit_cost_q
    entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
    np.add.at(daily_pnl, exit_cal_idx, -entry_costs)
    flat_notional = notional[rep_pos_idx]
    np.add.at(daily_pnl, flat_cal_idx, flat_notional * flat_ret)
    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    ret = np.diff(nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], nav[:-1]])
    years = n_days / 252.0
    ann_ret = (nav[-1] / INITIAL_CAPITAL) ** (1 / years) - 1
    ann_vol = pd.Series(ret).std(ddof=1) * np.sqrt(252)
    sharpe = (pd.Series(ret).mean() * 252) / ann_vol
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()
    return ann_ret, ann_vol, sharpe, max_dd


for name, col in [("dispersion", "dispersion_rank_pct"), ("revision", "revision_rank_pct")]:
    print(f"\n{name} blend test:")
    for w in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]:
        weight = compute_weights_blend(TRIM, TILT_POWER, w, col)
        ann_ret, ann_vol, sharpe, max_dd = simulate(weight, BUF, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE)
        print(f"  blend_weight={w}: ann_ret={ann_ret:.4f} sharpe={sharpe:.4f} max_dd={max_dd:.4f}")

print("\ndispersion blend test, finer grid around the promising region:")
for w in [0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20, 0.225, 0.25]:
    weight = compute_weights_blend(TRIM, TILT_POWER, w, "dispersion_rank_pct")
    ann_ret, ann_vol, sharpe, max_dd = simulate(weight, BUF, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE)
    print(f"  blend_weight={w}: ann_ret={ann_ret:.4f} sharpe={sharpe:.4f} max_dd={max_dd:.4f}")
