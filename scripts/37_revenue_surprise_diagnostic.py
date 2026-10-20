"""
Diagnostic: does a REVENUE surprise -- built from Compustat quarterly `saleq`
(raw_wrds/comp/fundq.parquet), completely unused elsewhere in this project (only `ceqq` from this
file has been touched, for book-to-market) -- carry information beyond sue_analyst (the EPS
surprise every strategy here actually trades)? This is the literature's best-precedented candidate
for this exact question (Jegadeesh & Livnat 2006 find revenue surprise adds incremental
post-announcement drift beyond earnings surprise), and it measures something structurally
different from EPS: revenue is much harder to manage/manipulate via accounting choices than
bottom-line earnings, so a revenue beat is a cleaner signal of real operating momentum.

No IBES analyst revenue estimates exist in this data pull, so the surprise is built the same way
sue_seasonal's name implies: a seasonal-random-walk expectation. For each firm-quarter,
  rev_surprise_raw = saleq_t - saleq_{t-4}          (year-over-year, controls for seasonality)
scaled by the trailing standard deviation of that same raw diff over the firm's own past 8
available quarters (shifted by 1 quarter so the current observation never enters its own
denominator) -- the standard "standardized unexpected X" construction. Firms with fewer than 6
trailing observations get no revenue-surprise value rather than a noisy one, the same
drop-rather-than-force convention already used for thin BM/sector cells elsewhere in this project.

Point-in-time note: the revenue value being "surprised about" becomes public at the SAME
disclosure as the EPS number itself (same earnings release), so no extra lag is needed beyond
matching to the event's own fpedats; the scaling denominator only uses strictly-prior quarters, so
it's automatically safe.

Same two-part structure as scripts/27, /36, /38: Part 1 (characterization) then Part 2 (an actual
blended trading-weight test).

No file output -- a diagnostic that informs a design decision, not a strategy build.
"""
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data/normalized_equity")
MAIN = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data")

# ---------- build the point-in-time-safe revenue surprise signal ----------
print("loading Compustat quarterly fundamentals (fundq)...")
fundq = pd.read_parquet(MAIN / "raw_wrds" / "comp" / "fundq.parquet",
                         columns=["gvkey", "datadate", "fyearq", "fqtr", "saleq"])
fundq = fundq.dropna(subset=["fyearq", "fqtr", "saleq"]).copy()
fundq["fyearq"] = fundq["fyearq"].astype(int)
fundq["fqtr"] = fundq["fqtr"].astype(int)
fundq["datadate"] = pd.to_datetime(fundq["datadate"])
fundq = fundq.drop_duplicates(subset=["gvkey", "fyearq", "fqtr"])
print(f"fundq usable rows: {len(fundq):,}")

# year-over-year match: same gvkey, same fiscal quarter, prior fiscal year
prior = fundq[["gvkey", "fyearq", "fqtr", "saleq"]].rename(
    columns={"fyearq": "fyearq_prior_key", "saleq": "saleq_prior"})
fundq["fyearq_lookup"] = fundq["fyearq"] - 1
fundq = fundq.merge(prior, left_on=["gvkey", "fyearq_lookup", "fqtr"],
                     right_on=["gvkey", "fyearq_prior_key", "fqtr"], how="left")
fundq["rev_surprise_raw"] = fundq["saleq"] - fundq["saleq_prior"]
print(f"rows with a YoY match: {fundq['rev_surprise_raw'].notna().sum():,} / {len(fundq):,}")

# trailing 8-quarter std of the same YoY diff, per firm, using only STRICTLY PRIOR quarters
fundq = fundq.sort_values(["gvkey", "datadate"]).reset_index(drop=True)
fundq["trailing_std"] = fundq.groupby("gvkey")["rev_surprise_raw"].transform(
    lambda s: s.shift(1).rolling(8, min_periods=6).std())
fundq["rev_sue_raw"] = fundq["rev_surprise_raw"] / fundq["trailing_std"]
fundq.loc[fundq["trailing_std"] <= 0, "rev_sue_raw"] = np.nan
print(f"rows with a usable trailing-scaled revenue surprise: {fundq['rev_sue_raw'].notna().sum():,} / {len(fundq):,}")

fundq["gvkey_primary"] = fundq["gvkey"]  # fundq's own gvkey is already single-valued

ev = pd.read_parquet(DATA / "decile_events.parquet",
                      columns=["permno", "day0_date", "gvkey", "fpedats", "ann_quarter", "sue_rank_pct"])
ev["gvkey_primary"] = ev["gvkey"].astype(str).str.split(",").str[0]
ev["fpedats"] = pd.to_datetime(ev["fpedats"])
n_events = len(ev)

sig = ev.merge(fundq[["gvkey_primary", "datadate", "rev_sue_raw"]],
                left_on=["gvkey_primary", "fpedats"], right_on=["gvkey_primary", "datadate"], how="left")
sig = sig.drop_duplicates(subset=["permno", "day0_date"])
match_rate = sig["rev_sue_raw"].notna().mean()
print(f"events matched to a usable revenue-surprise value (exact fpedats==datadate): "
      f"{sig['rev_sue_raw'].notna().sum():,} / {n_events:,} ({match_rate:.1%})")

sig["rev_sue_rank_pct"] = sig.groupby("ann_quarter")["rev_sue_raw"].rank(pct=True)

# ---------- Part 1: characterization ----------
ff = pd.read_parquet(DATA / "decile_events_ff_adjusted.parquet",
                      columns=["permno", "day0_date", "ret_fwd_60d_mktadj_adj"])
ff["day0_date"] = pd.to_datetime(ff["day0_date"])
sig["day0_date"] = pd.to_datetime(sig["day0_date"])
char = sig.merge(ff, on=["permno", "day0_date"], how="inner")
char = char.dropna(subset=["rev_sue_rank_pct", "ret_fwd_60d_mktadj_adj"])
print(f"\nevents usable for characterization: {len(char):,}")
print("correlation sue_rank_pct vs rev_sue_rank_pct:", char[["sue_rank_pct", "rev_sue_rank_pct"]].corr().iloc[0, 1])


def fama_macbeth(df, ret_col, q_col="ann_quarter"):
    sub = df[[q_col, ret_col]].dropna(subset=[ret_col])
    qmeans = sub.groupby(q_col)[ret_col].mean()
    n_q = qmeans.shape[0]
    mean = qmeans.mean()
    se = qmeans.std(ddof=1) / np.sqrt(n_q)
    t = mean / se if se > 0 else np.nan
    return mean, se, t, len(sub), n_q


char["q"] = char.groupby("ann_quarter")["rev_sue_rank_pct"].transform(
    lambda x: pd.qcut(x, 5, labels=False, duplicates="drop"))
print("Fama-MacBeth: 60d forward return by revenue-surprise quintile:")
for q in sorted(char["q"].dropna().unique()):
    mean, se, t, n, nq = fama_macbeth(char[char["q"] == q], "ret_fwd_60d_mktadj_adj")
    print(f"  quintile {int(q)}: mean={mean:.4f} se={se:.4f} t={t:.2f} n={n:,} n_quarters={nq}")

top = char[char["q"] == char["q"].max()].groupby("ann_quarter")["ret_fwd_60d_mktadj_adj"].mean()
bot = char[char["q"] == 0].groupby("ann_quarter")["ret_fwd_60d_mktadj_adj"].mean()
common = top.index.intersection(bot.index)
spread = top.loc[common] - bot.loc[common]
t_spread = spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))
print(f"top-bottom spread: mean={spread.mean():.4f} t={t_spread:.2f} n_quarters={len(spread)}")

# double sort: does revenue surprise add anything WITHIN sue_rank_pct deciles?
char["sue_decile"] = pd.cut(char["sue_rank_pct"], 10, labels=False)
print("\nDouble sort: 60d forward return, SUE decile x revenue-surprise quintile (top - bottom):")
for d in sorted(char["sue_decile"].dropna().unique()):
    sub = char[char["sue_decile"] == d]
    sub_top = sub[sub["q"] == sub["q"].max()]["ret_fwd_60d_mktadj_adj"].mean()
    sub_bot = sub[sub["q"] == 0]["ret_fwd_60d_mktadj_adj"].mean()
    print(f"  SUE decile {int(d)}: rev-surprise top={sub_top:.4f} bottom={sub_bot:.4f} spread={sub_top - sub_bot:.4f}")

# ---------- Part 2: actual blended trading-weight test ----------
print("\n" + "=" * 70)
print("Part 2: blending revenue surprise into Strategy 6's weight construction")
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

pos2 = pos.merge(sig[["permno", "day0_date", "rev_sue_rank_pct"]], on=["permno", "day0_date"], how="left")
print(f"positions with a usable revenue-surprise value: {pos2['rev_sue_rank_pct'].notna().sum():,} / {len(pos2):,}")


def compute_weights_blend(trim, tilt_power, blend_weight):
    sue_rank = pos2["sue_rank_pct"].to_numpy()
    sig_rank = pos2["rev_sue_rank_pct"].to_numpy()
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


print("\nrevenue surprise blend test:")
for w in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]:
    weight = compute_weights_blend(TRIM, TILT_POWER, w)
    ann_ret, ann_vol, sharpe, max_dd = simulate(weight, BUF, LIQUIDITY_CAP_FRAC, MAX_GROSS_LEVERAGE)
    print(f"  blend_weight={w}: ann_ret={ann_ret:.4f} sharpe={sharpe:.4f} max_dd={max_dd:.4f}")
