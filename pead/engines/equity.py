"""Equity backtest engine: quarterly-resized, trailing-NAV compounding book of earnings-event positions.

One engine for every equity strategy. A strategy supplies a WEIGHT per event (signed, arbitrary
scale -- the engine only uses weight x base_unit, so the scale is absorbed by `base_unit_fraction`)
and a `Sizing` spec; the engine owns everything mechanical:

  * Sizing is resized QUARTERLY off the trailing (previous quarter-end, already-realized) NAV, never
    same-day NAV, so profits can only affect sizing from the next quarter -- real compounding with no
    same-day feedback loop. A position's dollar notional is fixed for its whole life once entered.
  * Liquidity cap: no position larger than `liquidity_cap_frac` x its 21-day average dollar volume.
  * Leverage cap: gross notional entered in a quarter is limited to `max_gross_leverage` x trailing
    NAV, trimmed by PRIORITY (largest-conviction positions kept, smallest dropped first) instead of
    shaving every position equally.
  * Costs: per-position round-trip bps by liquidity quintile, charged at entry and again at exit.
  * P&L accrues daily on the position's own return path (market-adjusted for market-neutral books,
    raw for books that deliberately carry beta).

Besides the NAV series the engine returns PER-POSITION P&L (gross, costs, net) and the daily
long-leg / short-leg P&L split, which is what makes "where does the return come from" answerable
without re-running anything. Numerically identical to the original per-strategy scripts (17-26):
pead/tests/test_reproduce.py compares NAVs to the committed legacy files.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pead.core.data import EquityUniverse
from pead.core.metrics import summarize

COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15


@dataclass(frozen=True)
class Sizing:
    base_unit_fraction: float = 0.003
    max_gross_leverage: float = 2.5
    liquidity_cap_frac: float = 0.05
    return_basis: str = "mktadj"          # 'mktadj' (market-neutral books) | 'raw' (carries beta)
    cap_mode: str = "priority"            # leverage-cap trimming: 'priority' (drop lowest conviction
                                          # first) | 'proportional' (shave every new position equally;
                                          # how Strategies 1-3 were originally built)


@dataclass
class EquityRun:
    daily: pd.DataFrame
    quarterlog: pd.DataFrame
    positions: pd.DataFrame               # one row per funded position, with notional and pnl columns
    summary: dict
    extras: dict = field(default_factory=dict)


def cap_notionals(target, liq_cap, max_gross, mode="priority"):
    """Apply the two capacity limits to one batch of target dollar positions (signed).

      1. liquidity cap: |position| <= liq_cap (a fraction of the name's average dollar volume)
      2. gross-leverage cap: sum |position| <= max_gross; if exceeded, 'priority' keeps the largest-
         conviction positions whole and drops the rest (the marginal one is trimmed to fit),
         'proportional' shaves every position by the same factor.

    Shared by the backtest loop and the live engine so both size identically.
    Returns (notional, status, n_dropped)."""
    target = np.sign(target) * np.minimum(np.abs(target), liq_cap)
    gross_req = float(np.abs(target).sum())
    if not (gross_req > max_gross and gross_req > 0):
        return target.copy(), "ok", 0
    if mode == "proportional":
        return target * (max_gross / gross_req), "priority_capped", 0
    pri = np.argsort(-np.abs(target))
    cum = np.cumsum(np.abs(target[pri]))
    kept = cum <= max_gross
    out = np.zeros_like(target)
    out[pri[kept]] = target[pri[kept]]
    n_kept = int(kept.sum())
    n_drop = 0
    if n_kept < len(target):
        remaining = max_gross - (cum[n_kept - 1] if n_kept > 0 else 0.0)
        b = pri[n_kept]
        if remaining > 0:
            out[b] = np.sign(target[b]) * min(abs(target[b]), remaining)
        n_drop = len(target) - n_kept - (1 if remaining > 0 else 0)
    return out, "priority_capped", n_drop


def run_equity_backtest(uni: EquityUniverse, weight: np.ndarray, sizing: Sizing) -> EquityRun:
    full = uni.events
    assert len(weight) == len(full), "weight must align with universe.events"
    keep = np.flatnonzero(np.asarray(weight) != 0)       # unfunded events can't affect anything
    pos = full.iloc[keep].reset_index(drop=True)
    weight = np.asarray(weight, dtype=float)[keep]
    eq_ret = uni.ret_raw if sizing.return_basis == "raw" else uni.ret_mktadj
    cal = uni.calendar
    cap0 = uni.initial_capital

    n_pos, n_days = len(pos), len(cal)
    date_to_idx = {d: i for i, d in enumerate(cal)}
    quarter_of = cal.to_period("Q")
    uq = quarter_of.unique().sort_values()
    n_q = len(uq)
    qmap = {q: i for i, q in enumerate(uq)}
    qcode_of_day = np.array([qmap[q] for q in quarter_of])

    entry_q = pos["day0_date"].dt.to_period("Q").map(qmap).to_numpy()
    exit_q = pos["exit_date"].dt.to_period("Q").map(qmap).to_numpy()
    liq = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
    cost_bps = liq.map(COST_BPS).fillna(DEFAULT_COST_BPS).to_numpy()
    adv21 = pos["avg_dollar_volume_21d"].to_numpy()
    entry_cal = pos["day0_date"].map(date_to_idx).to_numpy()
    exit_cal = pos["exit_date"].map(date_to_idx).to_numpy()

    entry_idx, exit_idx = pos["entry_row_idx"].to_numpy(), pos["exit_row_idx"].to_numpy()
    lengths = exit_idx - entry_idx
    total_rows = int(lengths.sum())
    rep = np.repeat(np.arange(n_pos), lengths)
    offsets = np.arange(total_rows) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    flat_row = np.repeat(entry_idx + 1, lengths) + offsets
    flat_ret = eq_ret[flat_row]
    flat_cal = cal.searchsorted(uni.panel_date[flat_row])
    flat_q = qcode_of_day[flat_cal]
    order = np.argsort(flat_q, kind="stable")
    fq_s, fr_s, rep_s = flat_q[order], flat_ret[order], rep[order]
    bounds = np.searchsorted(fq_s, np.arange(n_q + 1))

    liq_cap = np.where(np.isfinite(adv21), sizing.liquidity_cap_frac * adv21, np.inf)
    trailing_nav = cap0
    notional = np.zeros(n_pos)
    base_at_entry = np.zeros(n_pos)
    qlog = []
    for qi in range(n_q):
        q_mask = entry_q == qi
        base_unit = trailing_nav * sizing.base_unit_fraction
        note, n_drop = "ok", 0
        if q_mask.any():
            idx = np.flatnonzero(q_mask)
            q_not, note, n_drop = cap_notionals(weight[idx] * base_unit, liq_cap[idx],
                                                sizing.max_gross_leverage * trailing_nav, sizing.cap_mode)
            notional[idx] = q_not
            base_at_entry[idx] = base_unit
        s, e = bounds[qi], bounds[qi + 1]
        trade_pnl = float(np.sum(notional[rep_s[s:e]] * fr_s[s:e])) if e > s else 0.0
        entry_cost = float(np.sum(np.abs(notional[q_mask]) * cost_bps[q_mask] / 1e4)) if q_mask.any() else 0.0
        xm = exit_q == qi
        exit_cost = float(np.sum(np.abs(notional[xm]) * cost_bps[xm] / 1e4)) if xm.any() else 0.0
        q_pnl = trade_pnl - entry_cost - exit_cost
        qlog.append(dict(quarter=str(uq[qi]), trailing_nav_used=trailing_nav, base_unit=base_unit,
                         n_new_positions=int(q_mask.sum()), cap_status=note, n_dropped_low_conviction=n_drop,
                         quarter_realized_pnl=q_pnl))
        trailing_nav += q_pnl

    entry_costs = np.abs(notional) * cost_bps / 1e4
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal, -entry_costs)
    np.add.at(daily_pnl, exit_cal, -entry_costs)
    flat_pnl = notional[rep] * flat_ret
    np.add.at(daily_pnl, flat_cal, flat_pnl)
    long_flat = np.where(notional[rep] > 0, flat_pnl, 0.0)
    pnl_long = np.bincount(flat_cal, weights=long_flat, minlength=n_days)
    pnl_short = np.bincount(flat_cal, weights=flat_pnl - long_flat, minlength=n_days)

    turnover = np.zeros(n_days)
    np.add.at(turnover, entry_cal, np.abs(notional))
    np.add.at(turnover, exit_cal, np.abs(notional))

    def _book(values):
        d = np.zeros(n_days + 1)
        np.add.at(d, entry_cal, values)
        np.add.at(d, exit_cal, -values)
        return np.cumsum(d[:n_days])

    gross_exp, net_exp = _book(np.abs(notional)), _book(notional)
    long_exp, short_exp = _book(np.maximum(notional, 0)), _book(np.minimum(notional, 0))
    n_open = _book((notional != 0).astype(float)).round().astype(int)

    nav = cap0 + np.cumsum(daily_pnl)
    prior = np.concatenate([[cap0], nav[:-1]])
    daily = pd.DataFrame({"date": list(cal), "daily_pnl": daily_pnl, "nav": nav, "daily_return": daily_pnl / prior,
                          "pnl_long": pnl_long, "pnl_short": pnl_short, "gross_exposure": gross_exp,
                          "net_exposure": net_exp, "long_exposure": long_exp, "short_exposure": short_exp,
                          "n_open_positions": n_open, "turnover_dollars": turnover})

    gross_pnl = np.bincount(rep, weights=flat_pnl, minlength=n_pos)
    positions = pos.assign(weight=weight, notional=notional, side=np.sign(notional), gross_pnl=gross_pnl,
                           cost=2 * entry_costs, net_pnl=gross_pnl - 2 * entry_costs,
                           ret_on_notional=np.divide(gross_pnl, np.abs(notional), out=np.zeros(n_pos),
                                                     where=notional != 0))
    positions = positions[positions["notional"] != 0].reset_index(drop=True)

    years = n_days / 252.0
    summary = summarize(daily, cap0)
    liquidity_capped = (np.abs(weight * base_at_entry) > np.abs(notional) + 1e-6) & (base_at_entry > 0)
    summary.update(
        base_unit_fraction=sizing.base_unit_fraction, max_gross_leverage=sizing.max_gross_leverage,
        n_positions=int((notional != 0).sum()), n_long=int((notional > 0).sum()), n_short=int((notional < 0).sum()),
        avg_gross_exposure=float(gross_exp.mean()), avg_leverage_vs_initial_capital=float(gross_exp.mean() / cap0),
        avg_net_exposure_frac_of_gross=float(np.nanmean(net_exp / np.where(gross_exp == 0, np.nan, gross_exp))),
        avg_n_open_positions=float(n_open.mean()), total_transaction_costs=float(2 * entry_costs.sum()),
        annual_turnover_x_capital=float(turnover.sum() / years / cap0),
        pct_liquidity_capped=float(liquidity_capped.mean()),
        n_quarters_leverage_capped=int(sum(q["cap_status"] == "priority_capped" for q in qlog)),
    )
    return EquityRun(daily=daily, quarterlog=pd.DataFrame(qlog), positions=positions, summary=summary)


def apply_beta_overlay(run: EquityRun, vwretd: np.ndarray, initial_capital: float,
                       beta: float = 0.5, cost_bps: float = 2.0) -> EquityRun:
    """Hold a synthetic market-index position of `beta` x trailing NAV alongside the alpha book,
    re-sized each quarter (130/30-style long extension). The alpha book is untouched; the overlay's
    daily P&L is notional x market return, with `cost_bps` one-way on each rebalance's turnover."""
    d = run.daily
    alpha_pnl = np.diff(d["nav"].to_numpy(), prepend=initial_capital)
    quarters = pd.DatetimeIndex(d["date"]).to_period("Q").to_numpy()
    n = len(d)
    nav_arr, pnl_arr, ov_arr, ov_pnl_arr = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    running, overlay, prev_q = initial_capital, 0.0, None
    for i in range(n):
        if quarters[i] != prev_q:
            new = beta * running
            running -= abs(new - overlay) * cost_bps / 1e4
            overlay, prev_q = new, quarters[i]
        ov = overlay * vwretd[i]
        running += alpha_pnl[i] + ov
        nav_arr[i], pnl_arr[i], ov_arr[i], ov_pnl_arr[i] = running, alpha_pnl[i] + ov, overlay, ov
    out = d.copy()
    out["daily_pnl"], out["nav"] = pnl_arr, nav_arr
    out["daily_return"] = pnl_arr / np.concatenate([[initial_capital], nav_arr[:-1]])
    out["overlay_notional"], out["pnl_overlay"] = ov_arr, ov_pnl_arr
    out["pnl_alpha"] = alpha_pnl
    summary = {**run.summary, **summarize(out, initial_capital), "beta_overlay": beta, "overlay_cost_bps": cost_bps}
    return EquityRun(daily=out, quarterlog=run.quarterlog, positions=run.positions, summary=summary)
