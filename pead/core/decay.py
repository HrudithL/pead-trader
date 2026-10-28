"""Decay-aware exposure scale (EQ-28 part B): how well is the top-decile drift working in the recent past?

spread_t = mean K-session post-entry market-adjusted return of top-decile events minus the same mean over all events, using only
events whose K-th post-entry session is complete by the close of t-1 and finished within the last `window_days` calendar days.
ref_t = expanding mean of spread_u (u < t); scale_t = clip(spread_t / ref_t, s_min, 1) once `min_history_days` have elapsed
(else 1; and 1 when ref <= 0 or spread is undefined). Never above 1, so the book is never levered.
"""
import numpy as np
import pandas as pd


def event_outcomes(uni, k: int):
    """(completion calendar index, K-session mktadj return) per event; NaN completion when the K-th session leaves the stock's run."""
    ev = uni.events
    e = ev["entry_row_idx"].to_numpy()
    pp = uni.panel_permno
    ok = (e + k < len(pp))
    end = np.minimum(e + k, len(pp) - 1)
    ok &= pp[end] == pp[e]
    lr = np.log1p(np.clip(uni.ret_mktadj, -0.99, None))
    cum = np.concatenate([[0.0], np.cumsum(lr)])
    out = np.expm1(cum[end + 1] - cum[e + 1])
    comp = uni.calendar.searchsorted(pd.DatetimeIndex(uni.panel_date[end]))
    return np.where(ok, comp, -1), np.where(ok, out, np.nan)


def rolling_spread(uni, k: int = 20, window_days: int = 750, top: float = 0.9):
    cal = uni.calendar
    n = len(cal)
    comp, out = event_outcomes(uni, k)
    sue = uni.events["sue_rank_pct"].to_numpy(dtype=float)
    valid = (comp >= 0) & np.isfinite(out) & np.isfinite(sue)
    is_top = sue >= top

    def cum(mask):
        s = np.bincount(comp[valid & mask], weights=out[valid & mask], minlength=n)
        c = np.bincount(comp[valid & mask], minlength=n).astype(float)
        return np.concatenate([[0.0], np.cumsum(s)]), np.concatenate([[0.0], np.cumsum(c)])

    (ts, tc), (as_, ac) = cum(is_top), cum(np.ones(len(out), bool))
    start = cal.searchsorted(cal - pd.Timedelta(days=window_days))
    t = np.arange(n)
    hi = t                                          # completions at index <= t-1  ->  cumulative index t
    nt, na = tc[hi] - tc[start], ac[hi] - ac[start]
    with np.errstate(invalid="ignore", divide="ignore"):
        spread = (ts[hi] - ts[start]) / nt - (as_[hi] - as_[start]) / na
    spread[(nt < 30) | (na < 100)] = np.nan
    return spread


def decay_scale(uni, k: int = 20, window_days: int = 750, s_min: float = 0.4, min_history_days: int = 1095) -> np.ndarray:
    spread = rolling_spread(uni, k, window_days)
    s = pd.Series(spread)
    ref = s.shift(1).expanding().mean().to_numpy()                 # mean of spread_u, u < t
    cal = uni.calendar
    first = np.flatnonzero(np.isfinite(spread))
    scale = np.ones(len(cal))
    if len(first) == 0:
        return scale
    live = (cal >= cal[first[0]] + pd.Timedelta(days=min_history_days)) & np.isfinite(spread) & np.isfinite(ref) & (ref > 0)
    scale[live] = np.clip(spread[live] / ref[live], s_min, 1.0)
    return scale
