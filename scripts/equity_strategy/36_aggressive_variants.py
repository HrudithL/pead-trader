"""Aggressive variants of Strategies 1-7: same signal, same trim/tilt/neutralization design each
strategy already uses, pushed harder on the two dials that actually control aggressiveness --
position size (base_unit_fraction) and the gross-leverage cap -- to trade Sharpe ratio for more
absolute annual return, per the explicit request: "willing to sacrifice Sharpe down to ~0.6 for
much more return."

Methodology, kept identical in spirit to how Strategy 6's own leverage cap was chosen (sweep, then
pick the point that's actually justified): for each strategy, base_unit_fraction is swept over a
grid of multiples of that strategy's ORIGINAL sizing (1.5x-6x) with the leverage cap held at a
realistic institutional ceiling (6x gross -- already loose vs. each strategy's own 1.5x/2.5x
baseline) so the sweep isolates the sizing effect rather than being confounded by an arbitrary
tighter cap; the 5%-of-ADV liquidity cap is left untouched throughout, since that is a real
capacity constraint (can't trade more of a stock than the market can absorb), not a tunable risk
dial. The candidate that maximizes annualized return is selected subject to TWO floors: Sharpe >=
0.6 (the explicit ask) AND max drawdown >= -40% (a >40% drawdown is a fund-ending event, not a
"more aggressive" variant of a real strategy -- excluded even if its Sharpe alone still clears
0.6). The leverage cap is finalized at the same realistic ceiling (6x gross) the whole sweep
already searched under -- Strategy 6's own 1.5x->2.5x cap increase was justified by "where the cap
stops binding," and 6x is that same idea applied one notch further, not an unlimited backstop; it
is NOT sized off the summary's own "avg_leverage_vs_initial_capital" field, which divides gross
exposure averaged over 17 compounding years by the fixed *starting* capital and so drifts far above
the real trailing-NAV leverage ratio the cap itself is defined against (the same category of error
as the daily_pnl/INITIAL_CAPITAL Sharpe bug this script otherwise avoids).

Every weight-construction formula (trim/tilt/size-neutral for Strategy 4, +sector-neutral for
Strategy 5/6, unconstrained-net-exposure for Strategy 7, the precomputed decile/rank weights for
Strategies 1-3) is copied verbatim from scripts 17/20/23/24/26 -- nothing about WHICH positions are
held or how they're weighted relative to each other changes, only how big the book is sized and how
much gross leverage it's allowed to run, which is exactly "the same principles, applied more
aggressively."

Sharpe/vol use the corrected nav.pct_change()-based daily return throughout (see README's note on
the Sharpe bug) -- NOT the daily_pnl/INITIAL_CAPITAL formula scripts 17/20/23/24 still contain,
which is why results_summary_v2_FINAL.csv's Sharpe values differ from those scripts' own raw
_summary.json files. This script computes the corrected figure directly, so no manual post-hoc
correction is needed for the aggressive variants.

Output per strategy: data/backtest_v2_<name>_aggressive.csv,
data/backtest_v2_<name>_aggressive_quarterlog.csv, data/backtest_v2_<name>_aggressive_summary.json,
data/positions_<name>_aggressive_v2.parquet (strategy4-7 only, where weights are recomputed),
data/sweep_aggressive_<name>.csv (the sizing grid actually run), and a combined
data/results_summary_v2_aggressive_FINAL.csv (corrected-Sharpe table, same shape as
results_summary_v2_FINAL.csv, for the report scripts to read).
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, RAW_EQUITY_DIR, INITIAL_CAPITAL
from lib.positions import weights_size_sector_neutral

DATA = DATA_DIR
RAW = RAW_EQUITY_DIR
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15
LIQUIDITY_CAP_FRAC = 0.05          # untouched real capacity constraint -- see docstring
SEARCH_LEVERAGE_CAP = 6.0          # a realistic institutional ceiling (real 130/30-style long-
                                    # extension books run in this range) -- loose relative to each
                                    # strategy's own 1.5x/2.5x baseline, so the sweep finds where
                                    # SHARPE actually falls off from bigger, more liquidity-capped
                                    # positions, not an arbitrary tighter cap, but NOT unlimited:
                                    # a leverage cap search has to stop somewhere real funds would
                                    # actually stop, or "aggressive" degenerates into "reckless"
MIN_SHARPE = 0.6
MAX_DRAWDOWN_FLOOR = -0.40         # a >40% drawdown is a fund-ending event, not a viable "more
                                    # aggressive" variant -- excluded from candidates even if its
                                    # Sharpe still clears MIN_SHARPE
BUF_MULTIPLIERS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]

# ---------------------------------------------------------------------------
# Load the daily equity panel once (market-adjusted AND raw -- Strategy 7 needs raw)
# ---------------------------------------------------------------------------
print("loading daily equity return panel...")
frames = []
for y in range(1995, 2015):
    frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date", "ret"]))
eq = pd.concat(frames, ignore_index=True)
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
eq = eq.merge(mkt, on="date", how="left")
eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
eq_ret_mktadj = eq["ret_mktadj"].to_numpy()
eq_ret_raw = eq["ret"].fillna(0.0).to_numpy()
eq_date_arr = eq["date"].to_numpy()

calendar_dt = pd.DatetimeIndex(sorted(mkt["date"].unique()))
date_to_idx = {d: i for i, d in enumerate(calendar_dt)}
n_days = len(calendar_dt)
quarter_of = calendar_dt.to_period("Q")
unique_quarters = quarter_of.unique().sort_values()
n_quarters = len(unique_quarters)
quarter_code_map = {q: i for i, q in enumerate(unique_quarters)}
quarter_code_of_day = np.array([quarter_code_map[q] for q in quarter_of])
mkt_ret_by_day = mkt.set_index("date").reindex(calendar_dt)["vwretd"].fillna(0.0).to_numpy()


def load_positions(fname):
    pos = pd.read_parquet(DATA / fname).reset_index(drop=True)
    pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
    pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
    pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    pos["exit_date"] = pd.to_datetime(pos["exit_date"])
    return pos


def expand(pos, use_raw=False):
    """Precompute the day-by-day expansion of every position's return path (param-independent --
    only depends on entry/exit rows and realized returns, not notional)."""
    n_pos = len(pos)
    entry_qcode = pos["day0_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
    exit_qcode = pos["exit_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
    liq_series = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
    cost_bps = liq_series.map(COST_BPS).fillna(DEFAULT_COST_BPS).to_numpy()
    adv21 = pos["avg_dollar_volume_21d"].to_numpy() if "avg_dollar_volume_21d" in pos.columns else np.full(n_pos, np.nan)
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
    eq_ret = eq_ret_raw if use_raw else eq_ret_mktadj
    flat_ret = eq_ret[flat_eq_row]
    flat_date = eq_date_arr[flat_eq_row]
    flat_cal_idx = calendar_dt.searchsorted(flat_date)
    flat_qcode = quarter_code_of_day[flat_cal_idx]
    order = np.argsort(flat_qcode, kind="stable")
    boundaries = np.searchsorted(flat_qcode[order], np.arange(n_quarters + 1))
    return dict(
        n_pos=n_pos, entry_qcode=entry_qcode, exit_qcode=exit_qcode, cost_bps=cost_bps, adv21=adv21,
        entry_cal_idx=entry_cal_idx, exit_cal_idx=exit_cal_idx, rep_pos_idx=rep_pos_idx,
        flat_ret=flat_ret, flat_cal_idx=flat_cal_idx,
        flat_qcode_sorted=flat_qcode[order], flat_ret_sorted=flat_ret[order],
        rep_pos_idx_sorted=rep_pos_idx[order], boundaries=boundaries,
    )


# ---------------------------------------------------------------------------
# Weight construction, copied from 17/20/23/24/26 -- unchanged selection/tilt logic
# ---------------------------------------------------------------------------
def weights_size_neutral(pos, trim, tilt_power):
    """Strategy 4's trim/tilt/size-neutral (no sector) construction, from 20_strategy4_tilted.py."""
    n_pos = len(pos)
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


def weights_unconstrained(pos, trim, tilt_power):
    """Strategy 7's unconstrained-net-exposure construction, from 26_strategy7_....py."""
    n_pos = len(pos)
    d = pos["sue_rank_pct"].to_numpy() - 0.5
    frac = np.clip(np.abs(d) / 0.5, 0, 1)
    tilt = np.sign(d) * (frac ** tilt_power)
    keep = np.abs(d) > trim
    w = np.where(keep, tilt, 0.0)
    df = pos[["ann_quarter", "size_quintile", "ff12_sector"]].copy()
    df["w"] = w
    kept_idx = np.where(keep)[0]
    sub = df.iloc[kept_idx].copy()
    grp_key = list(zip(sub["ann_quarter"], sub["ff12_sector"], sub["size_quintile"]))
    sub["grp_key"] = grp_key
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


# ---------------------------------------------------------------------------
# Shared engine: trailing-NAV, priority-based leverage cap, corrected Sharpe -- 17/20/23/24/26's
# quarter-by-quarter loop, factored into one function so it's written (and audited) once.
# ---------------------------------------------------------------------------
def run_backtest(weight, exp, buf, liq_cap_frac, max_leverage):
    liq_cap_dollars = np.where(np.isfinite(exp["adv21"]), liq_cap_frac * exp["adv21"], np.inf)
    n_pos = exp["n_pos"]
    trailing_nav = INITIAL_CAPITAL
    notional = np.zeros(n_pos)
    base_unit_at_entry = np.zeros(n_pos)
    quarter_log = []

    for qi in range(n_quarters):
        q_mask = (exp["entry_qcode"] == qi) & (weight != 0)
        base_unit = trailing_nav * buf
        scale_note = "ok"
        n_dropped_q = 0
        if q_mask.any():
            idx = np.where(q_mask)[0]
            target = weight[idx] * base_unit
            cap = liq_cap_dollars[idx]
            target = np.sign(target) * np.minimum(np.abs(target), cap)
            gross_requested = float(np.abs(target).sum())
            max_gross = max_leverage * trailing_nav
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
        s, e = exp["boundaries"][qi], exp["boundaries"][qi + 1]
        trade_pnl_q = float(np.sum(notional[exp["rep_pos_idx_sorted"][s:e]] * exp["flat_ret_sorted"][s:e])) if e > s else 0.0
        entry_cost_q = float(np.sum(np.abs(notional[q_mask]) * exp["cost_bps"][q_mask] / 10_000.0)) if q_mask.any() else 0.0
        exit_mask = (exp["exit_qcode"] == qi) & (weight != 0)
        exit_cost_q = float(np.sum(np.abs(notional[exit_mask]) * exp["cost_bps"][exit_mask] / 10_000.0)) if exit_mask.any() else 0.0
        quarter_pnl = trade_pnl_q - entry_cost_q - exit_cost_q
        quarter_log.append(dict(quarter=str(unique_quarters[qi]), trailing_nav_used=trailing_nav,
                                 base_unit=base_unit, n_new_positions=int(q_mask.sum()),
                                 cap_status=scale_note, n_dropped_low_conviction=n_dropped_q,
                                 quarter_realized_pnl=quarter_pnl))
        trailing_nav += quarter_pnl

    entry_costs = np.abs(notional) * (exp["cost_bps"] / 10_000.0)
    exit_costs = entry_costs.copy()
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, exp["entry_cal_idx"], -entry_costs)
    np.add.at(daily_pnl, exp["exit_cal_idx"], -exit_costs)
    flat_notional = notional[exp["rep_pos_idx"]]
    np.add.at(daily_pnl, exp["flat_cal_idx"], flat_notional * exp["flat_ret"])

    turnover_dollars = np.zeros(n_days)
    np.add.at(turnover_dollars, exp["entry_cal_idx"], np.abs(notional))
    np.add.at(turnover_dollars, exp["exit_cal_idx"], np.abs(notional))

    exposure_delta = np.zeros(n_days + 1)
    open_delta = np.zeros(n_days + 1)
    np.add.at(exposure_delta, exp["entry_cal_idx"], np.abs(notional))
    np.add.at(exposure_delta, exp["exit_cal_idx"], -np.abs(notional))
    np.add.at(open_delta, exp["entry_cal_idx"], (weight != 0).astype(float))
    np.add.at(open_delta, exp["exit_cal_idx"], -(weight != 0).astype(float))
    gross_exposure = np.cumsum(exposure_delta[:n_days])
    n_open = np.cumsum(open_delta[:n_days]).astype(int)

    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    # CORRECTED Sharpe: daily return = pnl / prior-day NAV (nav.pct_change()), not / fixed capital
    daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], nav[:-1]])

    years = n_days / 252.0
    total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    if nav.min() <= 0 or total_ret <= -1.0:
        # book was wiped out (or worse) at some point under this sizing/leverage combo -- not a
        # real candidate; report it as such rather than letting (1+total_ret)**(1/years) NaN out
        ann_ret = -1.0
    else:
        ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()
    liquidity_capped = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)
    total_costs = entry_costs.sum() + exit_costs.sum()
    annual_turnover = turnover_dollars.sum() / years / INITIAL_CAPITAL
    corr_to_mkt = float(np.corrcoef(daily_ret, mkt_ret_by_day)[0, 1])

    summary = dict(
        base_unit_fraction=buf, liquidity_cap_frac=liq_cap_frac, max_gross_leverage=max_leverage,
        n_positions=int((weight != 0).sum()), total_return=float(total_ret), annualized_return=float(ann_ret),
        annualized_vol=float(ann_vol), sharpe=float(sharpe), max_drawdown=float(max_dd),
        avg_gross_exposure=float(gross_exposure.mean()),
        avg_leverage_vs_initial_capital=float(gross_exposure.mean() / INITIAL_CAPITAL),
        avg_n_open_positions=float(n_open.mean()), max_n_open_positions=int(n_open.max()),
        total_transaction_costs=float(total_costs), annual_turnover_x_capital=float(annual_turnover),
        pct_liquidity_capped=float(liquidity_capped.mean()), final_nav=float(nav[-1]),
        n_quarters_leverage_capped=int(sum(1 for q in quarter_log if q["cap_status"] == "priority_capped")),
        total_positions_dropped_by_priority_cap=int(sum(q["n_dropped_low_conviction"] for q in quarter_log)),
        correlation_to_market=corr_to_mkt,
    )
    return summary, nav, daily_ret, daily_pnl, gross_exposure, n_open, turnover_dollars, quarter_log, notional


def pick_aggressive(name, weight, exp, base_buf, base_leverage):
    """Grid-search base_unit_fraction multiples at a realistic, generously-loose leverage ceiling
    (SEARCH_LEVERAGE_CAP), pick the candidate that maximizes annualized return subject to
    Sharpe >= MIN_SHARPE AND max_drawdown >= MAX_DRAWDOWN_FLOOR (excluding fund-ending outcomes
    even if Sharpe alone still clears the floor), then re-run once at a finite leverage cap sized
    to where THAT candidate's own gross leverage actually lands (same "find where it stops
    binding" logic Strategy 6's own 2.5x cap was chosen with -- not an unbounded proportional
    scale-up) for the final reported numbers."""
    rows = []
    for mult in BUF_MULTIPLIERS:
        buf = base_buf * mult
        summary, *_ = run_backtest(weight, exp, buf, LIQUIDITY_CAP_FRAC, SEARCH_LEVERAGE_CAP)
        rows.append(dict(buf_multiplier=mult, base_unit_fraction=buf, **{
            k: summary[k] for k in ("annualized_return", "annualized_vol", "sharpe", "max_drawdown",
                                     "avg_leverage_vs_initial_capital", "pct_liquidity_capped")
        }))
    sweep = pd.DataFrame(rows)
    sweep.to_csv(DATA / f"sweep_aggressive_{name}.csv", index=False)

    viable = sweep[(sweep["sharpe"] >= MIN_SHARPE) & (sweep["max_drawdown"] >= MAX_DRAWDOWN_FLOOR)]
    if len(viable):
        qualifying = viable
        fallback = False
    else:
        # nothing clears both bars -- fall back to the best Sharpe among rows that at least didn't
        # blow through the drawdown floor, or the mildest candidate (lowest multiplier) otherwise
        safe = sweep[sweep["max_drawdown"] >= MAX_DRAWDOWN_FLOOR]
        qualifying = safe if len(safe) else sweep.nsmallest(1, "buf_multiplier")
        fallback = True
    winner = qualifying.loc[qualifying["annualized_return"].idxmax()]
    final_buf = float(winner["base_unit_fraction"])
    final_mult = float(winner["buf_multiplier"])
    # finalize the leverage cap AT the same realistic ceiling the whole sweep already searched
    # under (SEARCH_LEVERAGE_CAP) -- note "avg_leverage_vs_initial_capital" in the summary is
    # gross exposure averaged over 17 compounding years divided by the FIXED starting capital, so
    # it drifts far above the real trailing-NAV leverage ratio the cap itself is defined against
    # (exactly like the daily_pnl/INITIAL_CAPITAL Sharpe bug this script otherwise avoids) --
    # it is reported for reference but must not be used to size the cap.
    final_leverage = SEARCH_LEVERAGE_CAP
    tag = " [fallback: no grid point cleared both Sharpe>=0.6 and DD>=-40%]" if fallback else ""
    print(f"  {name}: picked buf_multiplier={final_mult}x (buf={final_buf:.5f}), "
          f"leverage cap {base_leverage}x -> {final_leverage}x "
          f"(search: sharpe={winner['sharpe']:.2f}, ann_ret={winner['annualized_return']:.2%}, "
          f"maxDD={winner['max_drawdown']:.1%}){tag}")
    final_summary, *rest = run_backtest(weight, exp, final_buf, LIQUIDITY_CAP_FRAC, final_leverage)
    return final_summary, rest, sweep


def save_outputs(name, weight, pos, exp, summary, rest):
    nav, daily_ret, daily_pnl, gross_exposure, n_open, turnover_dollars, quarter_log, notional = rest
    out = pd.DataFrame({"date": list(calendar_dt), "daily_pnl": daily_pnl, "nav": nav,
                         "gross_exposure": gross_exposure, "n_open_positions": n_open,
                         "turnover_dollars": turnover_dollars, "daily_return": daily_ret})
    out.to_csv(DATA / f"backtest_v2_{name}_aggressive.csv", index=False)
    pd.DataFrame(quarter_log).to_csv(DATA / f"backtest_v2_{name}_aggressive_quarterlog.csv", index=False)
    summary_full = dict(name=f"{name}_aggressive", **summary)
    with open(DATA / f"backtest_v2_{name}_aggressive_summary.json", "w") as f:
        json.dump(summary_full, f, indent=2)
    if weight is not None and pos is not None:
        pos_out = pos.copy()
        pos_out["weight"] = weight
        pos_out = pos_out[pos_out["weight"] != 0].reset_index(drop=True)
        pos_out.to_parquet(DATA / f"positions_{name}_aggressive_v2.parquet", index=False)
    print(f"  {name}_aggressive: ann.ret={summary['annualized_return']:.2%} "
          f"ann.vol={summary['annualized_vol']:.2%} Sharpe={summary['sharpe']:.3f} "
          f"maxDD={summary['max_drawdown']:.2%} final_nav=${summary['final_nav']:,.0f}")


# ---------------------------------------------------------------------------
# Strategies 1-3: precomputed weight column, only sizing/leverage change (18/19's own sweep axis)
# ---------------------------------------------------------------------------
BASELINE = {
    "extreme":      dict(buf=0.003,  leverage=1.5, positions="positions_extreme_v2.parquet"),
    "rankweighted": dict(buf=0.002,  leverage=1.5, positions="positions_rankweighted_v2.parquet"),
    "balanced":     dict(buf=0.003,  leverage=1.5, positions="positions_balanced_v2.parquet"),
}
results_rows = []

print("\n=== Strategies 1-3: aggressive sizing on the unchanged decile/rank weight construction ===")
for name, cfg in BASELINE.items():
    pos = load_positions(cfg["positions"])
    weight = pos["weight"].to_numpy()
    exp = expand(pos, use_raw=False)
    summary, rest, sweep = pick_aggressive(name, weight, exp, cfg["buf"], cfg["leverage"])
    save_outputs(name, None, None, exp, summary, rest)
    results_rows.append(dict(strategy=f"{name}_aggressive", label=None, ann_return=summary["annualized_return"],
                              ann_vol=summary["annualized_vol"], sharpe=summary["sharpe"],
                              max_drawdown=summary["max_drawdown"], final_nav=summary["final_nav"]))

# ---------------------------------------------------------------------------
# Strategy 4 Aggressive: trim/tilt/size-neutral, same design, bigger book
# ---------------------------------------------------------------------------
print("\n=== Strategy 4 Aggressive ===")
pos4 = load_positions("positions_rankweighted_v2.parquet")
exp4 = expand(pos4, use_raw=False)
TRIM4, TILT4 = 0.15, 1.0
weight4 = weights_size_neutral(pos4, TRIM4, TILT4)
summary4, rest4, _ = pick_aggressive("strategy4", weight4, exp4, base_buf=0.002, base_leverage=1.5)
save_outputs("strategy4", weight4, pos4, exp4, summary4, rest4)
results_rows.append(dict(strategy="strategy4_aggressive", label=None, ann_return=summary4["annualized_return"],
                          ann_vol=summary4["annualized_vol"], sharpe=summary4["sharpe"],
                          max_drawdown=summary4["max_drawdown"], final_nav=summary4["final_nav"]))

# ---------------------------------------------------------------------------
# Strategy 5 Aggressive: + sector-neutral
# ---------------------------------------------------------------------------
print("\n=== Strategy 5 Aggressive ===")
pos5 = load_positions("positions_rankweighted_v2.parquet")
exp5 = expand(pos5, use_raw=False)
TRIM5, TILT5 = 0.15, 1.0
d5 = pos5["sue_rank_pct"].to_numpy() - 0.5
weight5 = weights_size_sector_neutral(pos5, d5, TRIM5, TILT5)
summary5, rest5, _ = pick_aggressive("strategy5", weight5, exp5, base_buf=0.0015, base_leverage=1.5)
save_outputs("strategy5", weight5, pos5, exp5, summary5, rest5)
results_rows.append(dict(strategy="strategy5_aggressive", label=None, ann_return=summary5["annualized_return"],
                          ann_vol=summary5["annualized_vol"], sharpe=summary5["sharpe"],
                          max_drawdown=summary5["max_drawdown"], final_nav=summary5["final_nav"]))

# ---------------------------------------------------------------------------
# Strategy 6 Aggressive: same sector+size-neutral signal, leverage cap pushed well past 2.5x
# ---------------------------------------------------------------------------
print("\n=== Strategy 6 Aggressive ===")
pos6 = load_positions("positions_rankweighted_v2.parquet")
exp6 = expand(pos6, use_raw=False)
TRIM6, TILT6 = 0.15, 1.0
d6 = pos6["sue_rank_pct"].to_numpy() - 0.5
weight6 = weights_size_sector_neutral(pos6, d6, TRIM6, TILT6)
summary6, rest6, _ = pick_aggressive("strategy6", weight6, exp6, base_buf=0.003, base_leverage=2.5)
save_outputs("strategy6", weight6, pos6, exp6, summary6, rest6)
results_rows.append(dict(strategy="strategy6_aggressive", label=None, ann_return=summary6["annualized_return"],
                          ann_vol=summary6["annualized_vol"], sharpe=summary6["sharpe"],
                          max_drawdown=summary6["max_drawdown"], final_nav=summary6["final_nav"]))

# ---------------------------------------------------------------------------
# Strategy 7 Aggressive: unconstrained net exposure, RAW returns (real market beta included)
# ---------------------------------------------------------------------------
print("\n=== Strategy 7 Aggressive ===")
pos7 = load_positions("positions_rankweighted_v2.parquet")
exp7 = expand(pos7, use_raw=True)
TRIM7, TILT7 = 0.15, 1.0
weight7 = weights_unconstrained(pos7, TRIM7, TILT7)
summary7, rest7, _ = pick_aggressive("strategy7", weight7, exp7, base_buf=0.003, base_leverage=2.5)
save_outputs("strategy7", weight7, pos7, exp7, summary7, rest7)
results_rows.append(dict(strategy="strategy7_aggressive", label=None, ann_return=summary7["annualized_return"],
                          ann_vol=summary7["annualized_vol"], sharpe=summary7["sharpe"],
                          max_drawdown=summary7["max_drawdown"], final_nav=summary7["final_nav"]))

# ---------------------------------------------------------------------------
# Combined results table -- same shape as results_summary_v2_FINAL.csv, aggressive variants only
# (Strategy 6+beta-overlay aggressive is added by 37_aggressive_beta_overlay.py, which appends to
# this same file since it depends on Strategy 6 Aggressive's own NAV series computed above)
# ---------------------------------------------------------------------------
labels = {
    "extreme_aggressive": "Extreme decile L/S -- Aggressive",
    "rankweighted_aggressive": "Rank-weighted -- Aggressive",
    "balanced_aggressive": "Balanced -- Aggressive",
    "strategy4_aggressive": "Strategy 4 -- Aggressive",
    "strategy5_aggressive": "Strategy 5 -- Aggressive",
    "strategy6_aggressive": "Strategy 6 -- Aggressive",
    "strategy7_aggressive": "Strategy 7 -- Aggressive",
}
for r in results_rows:
    r["label"] = labels[r["strategy"]]
final = pd.DataFrame(results_rows)[["strategy", "label", "ann_return", "ann_vol", "sharpe", "max_drawdown", "final_nav"]]
final.to_csv(DATA / "results_summary_v2_aggressive_FINAL.csv", index=False)
print(f"\nwrote data/results_summary_v2_aggressive_FINAL.csv ({len(final)} aggressive variants)")
