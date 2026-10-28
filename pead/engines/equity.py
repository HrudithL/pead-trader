"""Equity backtest engine: quarterly-resized, trailing-NAV compounding book of earnings-event positions.

One engine for every equity strategy. A strategy supplies a WEIGHT per event (signed, arbitrary
scale -- the engine only uses weight x base_unit, so the scale is absorbed by `base_unit_fraction`)
and a `Sizing` spec; the engine owns everything mechanical:

  * Sizing is resized QUARTERLY off the trailing (previous quarter-end, already-realized) NAV, never
    same-day NAV, so profits can only affect sizing from the next quarter -- real compounding with no
    same-day feedback loop. A position's dollar notional is fixed for its whole life once entered.
  * Liquidity cap: no position larger than `liquidity_cap_frac` x its 21-day average dollar volume.
  * Leverage cap: gross notional is limited to `max_gross_leverage` x trailing NAV, trimmed by PRIORITY
    (largest-conviction positions kept, smallest dropped first) instead of shaving every position
    equally. What the cap is measured against is `Sizing.exposure_cap`:
      'open_book'        (default) the cap bounds the DAILY gross of the whole book: a quarter's new
                         positions may only use the room left under the cap by positions still open
                         from earlier quarters, on every day they will be held.
      'open_book_qstart' like open_book but carryover is taken at its quarter-start value and the new
                         cohort is treated as entering all at once (simpler, noticeably tighter).
      'entry_cohort'     LEGACY: only the quarter's own new positions are compared with the cap, so
                         overlapping holding periods let realized gross run past it (balanced_leverage_max:
                         6x cap, 7.6x peak). Reproduces every number published before the fix.
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
    cap_mode: str = "priority"            # leverage-cap trimming: 'chronological' (first come first served, causal) |
                                          # 'priority' (drop lowest conviction
                                          # first) | 'proportional' (shave every new position equally;
                                          # how Strategies 1-3 were originally built)
    exposure_cap: str = "open_book"       # what the cap bounds: 'open_book' | 'open_book_qstart' | 'entry_cohort'
                                          # (legacy: new positions only). See the module docstring.
    name_cap_frac: float = None           # optional: no single position larger than this fraction of the sizing NAV


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


def cap_open_book(target, liq_cap, budget, mode, carry, ent, ext):
    """Leverage cap for one quarter's NEW positions that respects the book already open.

    carry   length-L array: gross dollars of earlier-quarter positions open on each day of the quarter
    ent/ext each new position's first and last-held day offset inside the quarter (ext clipped to L)
    budget  max_gross_leverage x trailing NAV

    A new position is admitted only up to the smallest headroom (budget - carry - already admitted new
    gross) over every day it will be held in the quarter, so the book's daily gross never exceeds the
    budget at the trailing NAV the quarter was sized on. (Positions that outlive the quarter are charged
    again as `carry` in later quarters.) 'priority' admits largest-|target| first, 'proportional' scales
    every new position by one common factor. Returns (notional, status, n_dropped)."""
    target, _, _ = cap_notionals(target, liq_cap, np.inf, mode)        # liquidity cap only
    L = len(carry)
    head = budget - carry
    if mode == "proportional":
        prof = np.zeros(L + 1)
        np.add.at(prof, ent, np.abs(target))
        np.add.at(prof, ext, -np.abs(target))
        prof = np.cumsum(prof)[:L]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(prof > 0, head / prof, np.inf)
        s = float(np.clip(ratio.min() if L else 1.0, 0.0, 1.0))
        return target * s, ("ok" if s >= 1.0 else "priority_capped"), 0
    used = np.zeros(L)                                                  # new gross admitted so far, per day
    out = np.zeros_like(target)
    n_drop, trimmed = 0, False
    # 'chronological' admits in announcement order (first come, first served) so no decision depends on events that
    # have not happened yet; 'priority' admits the largest targets first, which is only knowable once the quarter is over.
    order = np.argsort(ent, kind="stable") if mode == "chronological" else np.argsort(-np.abs(target), kind="stable")
    for j in order:
        want = abs(target[j])
        if want == 0:
            continue
        room = (head[ent[j]:ext[j]] - used[ent[j]:ext[j]]).min() if ext[j] > ent[j] else want
        give = min(want, max(room, 0.0))
        if give < want:
            trimmed = True
        if give <= 0:
            n_drop += 1
            continue
        out[j] = np.sign(target[j]) * give
        used[ent[j]:ext[j]] += give
    return out, ("priority_capped" if trimmed else "ok"), n_drop


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
    q_first = np.searchsorted(qcode_of_day, np.arange(n_q + 1))        # first calendar-day index of each quarter
    if sizing.exposure_cap not in ("open_book", "open_book_qstart", "entry_cohort"):
        raise ValueError(f"unknown exposure_cap {sizing.exposure_cap!r}")

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
            budget = sizing.max_gross_leverage * trailing_nav
            tgt = weight[idx] * base_unit
            if sizing.name_cap_frac:
                tgt = np.clip(tgt, -sizing.name_cap_frac * trailing_nav, sizing.name_cap_frac * trailing_nav)
            if sizing.exposure_cap == "entry_cohort":                    # legacy: ignores what is already open
                q_not, note, n_drop = cap_notionals(tgt, liq_cap[idx], budget, sizing.cap_mode)
            elif sizing.exposure_cap == "open_book_qstart":
                carry = float(np.abs(notional[(entry_q < qi) & (exit_q >= qi)]).sum())
                q_not, note, n_drop = cap_notionals(tgt, liq_cap[idx], max(budget - carry, 0.0), sizing.cap_mode)
            else:
                d0, L = q_first[qi], q_first[qi + 1] - q_first[qi]
                held = (notional != 0) & (entry_cal < d0) & (exit_cal > d0)
                diff = np.zeros(L + 1)
                diff[0] = np.abs(notional[held]).sum()
                np.add.at(diff, np.minimum(exit_cal[held] - d0, L), -np.abs(notional[held]))
                q_not, note, n_drop = cap_open_book(
                    tgt, liq_cap[idx], budget, sizing.cap_mode, np.cumsum(diff)[:L],
                    np.clip(entry_cal[idx] - d0, 0, L), np.clip(exit_cal[idx] - d0, 0, L))
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
        exposure_cap=sizing.exposure_cap, name_cap_frac=sizing.name_cap_frac,
        max_gross_over_nav=float(np.nanmax(gross_exp / nav)),             # realized, vs the stated cap
        pct_days_over_cap=float(np.mean(gross_exp / nav > sizing.max_gross_leverage + 1e-9)),
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


def with_fixed_hold(uni: EquityUniverse, hold_sessions: int) -> EquityUniverse:
    """Same universe, but every event is held exactly `hold_sessions` trading sessions after day0 (entry at the day0 close, as
    always), cut at the stock's last available row so a delisting stays in the P&L instead of silently dropping the event.
    No holding window is fitted from data. Events with no row after day0 are dropped."""
    if uni.panel_permno is None:
        raise ValueError("fixed holds need the panel permno array (rebuild the panel cache)")
    ev = uni.events
    pp = uni.panel_permno
    last = np.concatenate([np.flatnonzero(pp[1:] != pp[:-1]), [len(pp) - 1]])      # last row of every permno run
    entry = ev["entry_row_idx"].to_numpy()
    run_end = last[np.searchsorted(last, entry, side="left")]
    exit_row = np.minimum(entry + hold_sessions, run_end)
    ok = exit_row > entry
    out = ev.loc[ok].copy()
    out["exit_row_idx"] = exit_row[ok]
    out["exit_date"] = pd.to_datetime(uni.panel_date[exit_row[ok]])
    out["holding_days"] = exit_row[ok] - entry[ok]
    return EquityUniverse(events=out.reset_index(drop=True), ret_mktadj=uni.ret_mktadj, ret_raw=uni.ret_raw,
                          panel_date=uni.panel_date, calendar=uni.calendar, vwretd=uni.vwretd,
                          initial_capital=uni.initial_capital, extras=uni.extras, panel_permno=uni.panel_permno)


def with_entry_lag(uni: EquityUniverse, k: int) -> EquityUniverse:
    """Same universe, but every position enters `k` sessions AFTER the day0 close (the day0..day0+k return is neither earned nor
    sized on). `day0_date` is overwritten with the lagged entry date because the engine books costs and quarterly sizing from it;
    the original is kept in `day0_date_orig`. Events whose lagged entry would leave the stock's own run of rows are dropped."""
    if k == 0:
        return uni
    if uni.panel_permno is None:
        raise ValueError("entry lag needs the panel permno array")
    ev = uni.events
    pp = uni.panel_permno
    entry = ev["entry_row_idx"].to_numpy()
    new = entry + k
    ok = (new < len(pp)) & (pp[np.minimum(new, len(pp) - 1)] == pp[entry]) & (new < ev["exit_row_idx"].to_numpy())
    out = ev.loc[ok].copy()
    out["day0_date_orig"] = out["day0_date"]
    out["entry_row_idx"] = new[ok]
    out["day0_date"] = pd.to_datetime(uni.panel_date[new[ok]])
    return EquityUniverse(events=out.reset_index(drop=True), ret_mktadj=uni.ret_mktadj, ret_raw=uni.ret_raw,
                          panel_date=uni.panel_date, calendar=uni.calendar, vwretd=uni.vwretd,
                          initial_capital=uni.initial_capital, extras=uni.extras, panel_permno=uni.panel_permno)


def apply_cash_and_risk_target(run: EquityRun, rf: np.ndarray, initial_capital: float, target_vol: float = None,
                               lookback: int = 60, cost_bps: float = 10.0, cash_yield: bool = True) -> EquityRun:
    """Long-only wrapper: idle cash earns `rf`, and total exposure is scaled to hold risk roughly constant.

    * Idle cash = NAV not held in stock (prior-day NAV minus the book's gross exposure), earning the daily cash return `rf`.
    * Risk target: exposure e_t = min(1, target_vol / realised_vol), where realised_vol is the annualised standard deviation of
      the book's own daily returns over the previous `lookback` sessions, using data through t-1 only. The rest sits in cash.
      e never exceeds 1, so the overlay can only reduce risk: it never borrows. `cost_bps` is charged on each day's change in e.
      (Positions were sized off the un-overlaid NAV; the overlay is applied afterwards, as for the beta overlay.)"""
    d = run.daily
    nav0 = d["nav"].to_numpy()
    prior = np.concatenate([[initial_capital], nav0[:-1]])
    r = d["daily_return"].to_numpy().copy()
    gross_prev = np.concatenate([[0.0], d["gross_exposure"].to_numpy()[:-1]])
    idle = np.clip(1.0 - gross_prev / prior, 0.0, 1.0)
    if cash_yield:
        r = r + idle * rf
    s = pd.Series(r)
    if target_vol:
        vol = s.rolling(lookback, min_periods=20).std().shift(1).to_numpy() * np.sqrt(252)
        e = np.where(np.isfinite(vol) & (vol > 0), np.minimum(1.0, target_vol / np.where(vol > 0, vol, 1.0)), 1.0)
    else:
        e = np.ones(len(r))
    de = np.abs(np.diff(e, prepend=1.0))
    r2 = e * r + (1.0 - e) * rf - de * cost_bps / 1e4
    nav = initial_capital * np.cumprod(1.0 + r2)
    out = d.copy()
    out["daily_return"] = r2
    out["nav"] = nav
    out["daily_pnl"] = np.diff(nav, prepend=initial_capital)
    out["risk_exposure"] = e
    out["idle_cash_frac"] = idle
    summary = {**run.summary, **summarize(out, initial_capital), "risk_target_vol": target_vol, "risk_lookback": lookback,
               "avg_risk_exposure": float(e.mean()),
               "max_book_gross_over_nav": float(np.nanmax(d["gross_exposure"].to_numpy() / prior)), "cash_yield": bool(cash_yield)}
    return EquityRun(daily=out, quarterlog=run.quarterlog, positions=run.positions, summary=summary)
