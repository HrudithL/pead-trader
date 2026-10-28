"""Path-dependent and calendar-day exits (EQ-04 / EQ-05, REF-exit-rules): pure transforms of an EquityUniverse.

Like `with_fixed_hold`, each function returns a universe whose events carry new `exit_row_idx` / `exit_date` / `holding_days`;
the engine itself is unchanged. All rules look only at the position's own path up to the exit row (no later information), fill at
that day's close (no gap-through credit) and keep a delisting in the P&L by cutting at the stock's last row.
"""
import dataclasses

import numpy as np
import pandas as pd

from pead.engines.equity import with_fixed_hold


def _run_end(uni):
    pp = uni.panel_permno
    last = np.concatenate([np.flatnonzero(pp[1:] != pp[:-1]), [len(pp) - 1]])
    return last


def _set_exit(uni, ev, exit_row):
    ev = ev.copy()
    ev["exit_row_idx"] = exit_row
    ev["exit_date"] = pd.to_datetime(uni.panel_date[exit_row])
    ev["holding_days"] = exit_row - ev["entry_row_idx"].to_numpy()
    return dataclasses.replace(uni, events=ev.reset_index(drop=True))


def with_path_exits(uni, max_hold: int, stop_pct=None, trail_activation: float = None, trail_amount: float = None):
    """Time exit after `max_hold` sessions, plus an optional close-based percent stop and a LATCHED trailing stop.
    `stop_pct` may be a scalar or one value per event (a volatility-scaled stop). Wealth path v_k = prod(1 + raw ret) from the entry close. Stop: first k with v_k <= 1 - stop_pct. Trail: once the running peak
    reaches 1 + trail_activation it stays active; exit at the first k with v_k <= peak_k * (1 - trail_amount). The earliest trigger wins;
    with no stop it reproduces `with_fixed_hold` exactly."""
    held = with_fixed_hold(uni, max_hold)
    if stop_pct is None and trail_activation is None:
        return held
    ev = held.events
    e, x = ev["entry_row_idx"].to_numpy(), ev["exit_row_idx"].to_numpy()
    new_exit = x.copy()
    r = uni.ret_raw
    for i in range(len(ev)):
        seg = r[e[i] + 1:x[i] + 1]
        if len(seg) == 0:
            continue
        v = np.cumprod(1.0 + seg)
        hit = np.zeros(len(v), bool)
        if stop_pct is not None:
            sp = stop_pct[i] if np.ndim(stop_pct) else stop_pct
            hit |= v <= 1.0 - sp
        if trail_activation is not None and trail_amount is not None:
            peak = np.maximum.accumulate(np.maximum(v, 1.0))
            active = peak >= 1.0 + trail_activation
            hit |= active & (v <= peak * (1.0 - trail_amount))
        k = np.flatnonzero(hit)
        if len(k):
            new_exit[i] = e[i] + 1 + k[0]
    return _set_exit(uni, ev, new_exit)


def with_calendar_hold(uni, calendar_days: int):
    """Exit on the last trading row of the stock on or before day0_date + `calendar_days` (the stock's last row if it ends sooner)."""
    ev = uni.events
    e = ev["entry_row_idx"].to_numpy()
    last = _run_end(uni)
    run_end = last[np.searchsorted(last, e, side="left")]
    target = (pd.DatetimeIndex(ev["day0_date"]) + pd.Timedelta(days=calendar_days)).to_numpy()
    pd_dates = uni.panel_date
    exit_row = np.empty(len(ev), dtype=np.int64)
    for i in range(len(ev)):
        j = np.searchsorted(pd_dates[e[i]:run_end[i] + 1], target[i], side="right") - 1
        exit_row[i] = e[i] + max(int(j), 0)
    ok = exit_row > e
    return _set_exit(uni, ev.loc[ok], exit_row[ok])
