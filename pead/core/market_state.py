"""Market-state gates (OV-03): CRSP value-weighted trend and earnings breadth, all known at the close of day t."""
import numpy as np
import pandas as pd


def trend_gate(vwretd: np.ndarray, sma: int = 200, mom: int = 126) -> np.ndarray:
    """True when the index is above its `sma`-day mean AND up over `mom` sessions; True (ON) during warm-up."""
    idx = pd.Series(np.cumprod(1.0 + np.nan_to_num(vwretd)))
    above = idx > idx.rolling(sma, min_periods=sma).mean()
    up = idx / idx.shift(mom) - 1.0 > 0
    warm = idx.rolling(sma, min_periods=sma).mean().isna() | idx.shift(mom).isna()
    return (warm | (above & up)).to_numpy()


def earnings_breadth(uni, window: int = 20, b_thr: float = 0.75, n_min: int = 150) -> np.ndarray:
    """B2_t = share of events with day0 in the last `window` sessions that had sue_rank_pct >= b_thr AND a positive day-0 raw
    return. NaN when fewer than n_min events. Uses only events with day0 <= t."""
    cal = uni.calendar
    ev = uni.events
    d = cal.get_indexer(pd.DatetimeIndex(ev["day0_date"]))
    ok = d >= 0
    good = (ev["sue_rank_pct"].to_numpy(dtype=float) >= b_thr) & (uni.ret_raw[ev["entry_row_idx"].to_numpy()] > 0)
    n = len(cal)
    cnt = np.bincount(d[ok], minlength=n).astype(float)
    gd = np.bincount(d[ok & good], minlength=n).astype(float)
    kern = np.ones(window)
    cnt_w, gd_w = np.convolve(cnt, kern)[:n], np.convolve(gd, kern)[:n]
    with np.errstate(invalid="ignore", divide="ignore"):
        b = gd_w / cnt_w
    b[cnt_w < n_min] = np.nan
    return b


def gate_scale(gate: np.ndarray, s_off: float = 0.0) -> np.ndarray:
    """Exposure multiplier for day t from the gate at the close of t-1."""
    prev = np.concatenate([[True], gate[:-1]])
    return np.where(prev, 1.0, s_off)
