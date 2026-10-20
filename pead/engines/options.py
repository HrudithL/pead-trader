"""Options backtest engine: premium-budgeted book of single-name options bought at earnings day 0.

Positions are marked to market at fixed checkpoints (+1, 5, 10, 20, 40, 60 trading days after the
announcement reaction day) from real OptionMetrics mid quotes, so P&L is realized option-price
change, not a model. Sizing mirrors the equity engine's discipline:

  * Each quarter's candidates share the book's premium budget IN PROPORTION to their own
    (half-)Kelly fraction -- the relative edge/conviction -- instead of each being sized as if it
    were the only bet in the book (which over-commits capital by ~100x with 1,000+ concurrent
    candidates and collapses the book to ~46 funded trades).
  * The budget is `max_premium_frac` x trailing NAV MINUS premium still outstanding from positions
    carried over from earlier quarters, so the cap holds across overlapping holding periods.
  * Contracts are whole lots of 100; a flat round-trip cost (% of premium) is charged at entry.

Returns the NAV series, the per-position P&L (which side, which decile, which strike), and a daily
call-leg / put-leg P&L split for attribution.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pead.core.metrics import summarize

CHECKPOINTS = [1, 5, 10, 20, 40, 60]
CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class OptionsSizing:
    hold_horizon: int = 60
    half_kelly: float = 0.5
    max_premium_frac: float = 0.20
    roundtrip_cost_pct: float = 0.03
    min_roundtrip_cost_per_share: float = 0.0   # absolute floor (e.g. one $0.05 tick) under the % cost
    min_premium: float = 0.0                    # skip contracts whose mid is below this (penny options)
    dead_quote_mark: str = "zero_bid_half_ask"  # how to value a checkpoint where the bid is ZERO (nobody will buy):
                                                # 'zero_bid_half_ask' -> half the ask (a few cents, ~0); 'zero_bid_zero' -> 0 (liquidating means hitting the zero bid); 'carry_last' ->
                                                # the previous live quote (the ORIGINAL backtest convention, which
                                                # overstates results because it never marks a dying option down)


def run_options_backtest(panel: pd.DataFrame, calendar: np.ndarray, sizing: OptionsSizing,
                         initial_capital: float, eligible: np.ndarray = None):
    """panel: one row per event with kelly_* pick columns and kelly_fwd_mid_{h}d forward prices.
    eligible: optional boolean mask (aligned to panel) of events allowed to trade."""
    n_days = len(calendar)
    cal_d = calendar.astype("datetime64[D]")
    active = panel["kelly_growth"].to_numpy() > 0
    if eligible is not None:
        active = active & eligible
    entry_mid = panel["kelly_premium"].to_numpy()
    kelly_frac = panel["kelly_fraction"].to_numpy() * sizing.half_kelly
    day0 = pd.to_datetime(panel["day0_date"]).to_numpy().astype("datetime64[D]")
    entry_cal = np.searchsorted(cal_d, day0)

    checkpoints = [h for h in CHECKPOINTS if h <= sizing.hold_horizon]
    fits = (entry_cal + checkpoints[-1]) < n_days
    valid = active & np.isfinite(entry_mid) & (entry_mid > max(0.0, sizing.min_premium)) & fits
    idx_all = np.flatnonzero(valid)
    n_pos = len(idx_all)

    cols = [entry_mid[idx_all]]
    for h in checkpoints:
        c = f"kelly_fwd_mid_{h}d"
        mid = panel[c].to_numpy()[idx_all] if c in panel.columns else np.full(n_pos, np.nan)
        bc, ac = f"kelly_fwd_bid_{h}d", f"kelly_fwd_ask_{h}d"
        if sizing.dead_quote_mark in ("zero_bid_half_ask", "zero_bid_zero") and bc in panel.columns and ac in panel.columns:
            bid, ask = panel[bc].to_numpy()[idx_all], panel[ac].to_numpy()[idx_all]
            dead = np.isnan(mid) & (bid == 0) & (ask > 0)
            mid = np.where(dead, ask / 2.0 if sizing.dead_quote_mark == "zero_bid_half_ask" else 0.0, mid)
        cols.append(mid)
    price = pd.DataFrame(np.column_stack(cols)).ffill(axis=1).to_numpy()
    deltas = np.diff(price, axis=1)

    cal_idx_matrix = entry_cal[idx_all][:, None] + np.array(checkpoints)[None, :]
    rep_global = np.repeat(idx_all, len(checkpoints))
    rep_local = np.repeat(np.arange(n_pos), len(checkpoints))
    flat_cal = cal_idx_matrix.ravel()
    flat_delta = deltas.ravel()

    quarter_of_day = pd.PeriodIndex(cal_d, freq="Q")
    uq = quarter_of_day.unique().sort_values()
    n_q = len(uq)
    qmap = {q: i for i, q in enumerate(uq)}
    qcode_of_day = np.array([qmap[q] for q in quarter_of_day])
    entry_q = qcode_of_day[entry_cal[idx_all]]
    exit_cal = entry_cal[idx_all] + sizing.hold_horizon
    flat_q = qcode_of_day[flat_cal]
    order = np.argsort(flat_q, kind="stable")
    fq_s, fd_s, rep_s = flat_q[order], flat_delta[order], rep_global[order]
    bounds = np.searchsorted(fq_s, np.arange(n_q + 1))

    contracts_all = np.zeros(len(panel))
    entry_cost = np.zeros(len(panel))
    trailing_nav = initial_capital
    qlog = []
    max_outstanding_frac = 0.0
    for qi in range(n_q):
        q_local = np.flatnonzero(entry_q == qi)
        q_glob = idx_all[q_local]
        qstart = np.flatnonzero(qcode_of_day == qi)[0]
        still_open = (entry_q < qi) & (exit_cal >= qstart)
        outstanding = float(np.sum(contracts_all[idx_all[still_open]] * CONTRACT_MULTIPLIER
                                   * entry_mid[idx_all[still_open]]))
        if len(q_glob):
            qk = kelly_frac[q_glob]
            budget = max(0.0, sizing.max_premium_frac * trailing_nav - outstanding)
            target = (qk / qk.sum()) * budget if qk.sum() > 0 else np.zeros_like(qk)
            lots = np.floor(target / (CONTRACT_MULTIPLIER * entry_mid[q_glob]))
            contracts_all[q_glob] = lots
            per_share = np.maximum(entry_mid[q_glob] * sizing.roundtrip_cost_pct, sizing.min_roundtrip_cost_per_share)
            entry_cost[q_glob] = lots * CONTRACT_MULTIPLIER * per_share
        added = float(np.sum(contracts_all[q_glob] * CONTRACT_MULTIPLIER * entry_mid[q_glob])) if len(q_glob) else 0.0
        outstanding_after = outstanding + added
        max_outstanding_frac = max(max_outstanding_frac, outstanding_after / trailing_nav if trailing_nav > 0 else 0.0)
        s, e = bounds[qi], bounds[qi + 1]
        trade_pnl = float(np.sum(contracts_all[rep_s[s:e]] * CONTRACT_MULTIPLIER * fd_s[s:e])) if e > s else 0.0
        entry_cost_q = float(entry_cost[q_glob].sum()) if len(q_glob) else 0.0
        qlog.append(dict(quarter=str(uq[qi]), trailing_nav_used=trailing_nav,
                         n_new_positions=int((contracts_all[q_glob] > 0).sum()) if len(q_glob) else 0,
                         outstanding_premium_before=outstanding, premium_budget=sizing.max_premium_frac * trailing_nav,
                         outstanding_premium_after=outstanding_after, quarter_realized_pnl=trade_pnl - entry_cost_q))
        trailing_nav += trade_pnl - entry_cost_q

    flat_contracts = contracts_all[rep_global]
    flat_pnl = flat_contracts * CONTRACT_MULTIPLIER * flat_delta
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal[idx_all], -entry_cost[idx_all])
    np.add.at(daily_pnl, flat_cal, flat_pnl)
    is_call = np.repeat((panel["kelly_side"].to_numpy()[idx_all] == "C"), len(checkpoints))
    pnl_call = np.bincount(flat_cal, weights=np.where(is_call, flat_pnl, 0.0), minlength=n_days)
    pnl_put = np.bincount(flat_cal, weights=np.where(is_call, 0.0, flat_pnl), minlength=n_days)

    nav = initial_capital + np.cumsum(daily_pnl)
    daily = pd.DataFrame({"date": cal_d, "daily_pnl": daily_pnl, "nav": nav,
                          "daily_return": daily_pnl / np.concatenate([[initial_capital], nav[:-1]]),
                          "pnl_call": pnl_call, "pnl_put": pnl_put})

    funded = contracts_all[idx_all] > 0
    prem = contracts_all[idx_all] * CONTRACT_MULTIPLIER * entry_mid[idx_all]
    gross = np.bincount(rep_local, weights=flat_pnl, minlength=n_pos)
    positions = panel.iloc[idx_all].assign(
        contracts=contracts_all[idx_all], premium_paid=prem, cost=entry_cost[idx_all], gross_pnl=gross,
        net_pnl=gross - entry_cost[idx_all], ret_on_premium=np.divide(gross, prem, out=np.zeros(n_pos), where=prem > 0),
        side=np.where(panel["kelly_side"].to_numpy()[idx_all] == "C", 1, -1),
    )[funded].reset_index(drop=True)

    summary = summarize(daily, initial_capital)
    have_quotes = f"kelly_fwd_bid_{checkpoints[-1]}d" in panel.columns
    summary.update(dead_quote_mark=sizing.dead_quote_mark if have_quotes else "carry_last (no bid/ask columns in input)",
                   hold_horizon_days=sizing.hold_horizon, n_positions_eligible=int(n_pos),
                   n_positions_sized=int(funded.sum()), half_kelly_multiplier=sizing.half_kelly,
                   assumed_roundtrip_cost_pct=sizing.roundtrip_cost_pct, max_premium_frac=sizing.max_premium_frac,
                   max_outstanding_premium_frac=float(max_outstanding_frac),
                   total_transaction_costs=float(entry_cost.sum()), total_premium_paid=float(prem.sum()),
                   pct_calls=float((positions["kelly_side"] == "C").mean()) if len(positions) else np.nan)
    return daily, pd.DataFrame(qlog), positions, summary
