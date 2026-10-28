"""Synthetic fixtures shared by the guard-rail tests (REF-pit-test-kit). No data dependency.

`make_universe` builds an `EquityUniverse` in which every event is its own stock occupying a contiguous block of
panel rows (one row per calendar day), so a test controls the exact return path each position earns.
"""
import numpy as np
import pandas as pd

from pead.core.data import EquityUniverse


def make_universe(events, rets, n_days=120, start="2000-01-03", cap0=1e6):
    """events: list of dicts with `day0` (calendar index of entry, close) and `exit` (calendar index of exit).
    rets: list (same length) of arrays of length n_days: that stock's daily return on each calendar day."""
    cal = pd.bdate_range(start, periods=n_days)
    ev, panel_date, panel_ret, permno = [], [], [], []
    for i, (e, r) in enumerate(zip(events, rets)):
        base = i * n_days
        ev.append(dict(permno=i, entry_row_idx=base + e["day0"], exit_row_idx=base + e["exit"],
                       day0_date=cal[e["day0"]], exit_date=cal[e["exit"]],
                       avg_dollar_volume_21d=1e15, liquidity_quintile=3))
        panel_date.extend(cal)
        panel_ret.extend(np.asarray(r, dtype=float))
        permno.extend([i] * n_days)
    ret = np.array(panel_ret)
    return EquityUniverse(events=pd.DataFrame(ev), ret_mktadj=ret, ret_raw=ret,
                          panel_date=np.array(panel_date, dtype="datetime64[ns]"), calendar=cal,
                          vwretd=np.zeros(n_days), initial_capital=cap0, panel_permno=np.array(permno))


def jump(n_days, day, size):
    r = np.zeros(n_days)
    r[day] = size
    return r


def random_daily_frame(n=600, seed=0, mu=0.0004, sd=0.01, cap0=1e6):
    """A daily frame (date, daily_pnl, nav, daily_return) from random returns, for metric tests."""
    rng = np.random.default_rng(seed)
    r = rng.normal(mu, sd, n)
    nav = cap0 * np.cumprod(1 + r)
    pnl = np.diff(nav, prepend=cap0)
    return pd.DataFrame({"date": pd.bdate_range("1996-01-02", periods=n), "daily_pnl": pnl, "nav": nav, "daily_return": r})
