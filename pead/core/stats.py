"""Canonical portfolio-level statistics (MT-03 / REF-metrics-v2), additive: nothing imports this yet.

One implementation of the numbers the repo prints, with explicit definitions. `metrics.summarize` keeps its
legacy meaning; this module is where the active-window / PSR / DSR / bootstrap versions live until the
summary is switched over (see docs/plans/research_library_2026-10-27/methodology/MT-03_*.md).

Conventions: returns are daily NAV-percentage returns (P&L / prior NAV); 252 sessions per year; Sharpe
ratios passed to psr/dsr are PER-PERIOD (daily) unless a function says otherwise; `kurt` is NON-excess.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as sps

from pead.core.alpha import _ols_hac

TRADING_DAYS = 252
EULER_GAMMA = 0.5772156649
ERAS = {"1996_2001": ("1996", "2001"), "2002_2007": ("2002", "2007"), "2008_2013": ("2008", "2013")}


@dataclass(frozen=True)
class Window:
    start: pd.Timestamp
    end: pd.Timestamp
    label: str


def returns_from_daily(daily: pd.DataFrame, cap0: float) -> pd.Series:
    nav = daily["nav"].to_numpy()
    prior = np.concatenate([[cap0], nav[:-1]])
    return pd.Series(daily["daily_pnl"].to_numpy() / prior, index=pd.DatetimeIndex(daily["date"]))


def active_window(daily: pd.DataFrame) -> Window:
    """First and last day with a non-zero return (drops the flat warm-up and any flat tail)."""
    r = daily["daily_return"].to_numpy() if "daily_return" in daily else daily["daily_pnl"].to_numpy()
    nz = np.flatnonzero(r != 0)
    dates = pd.DatetimeIndex(daily["date"])
    if len(nz) == 0:
        return Window(dates[0], dates[-1], "active")
    return Window(dates[nz[0]], dates[nz[-1]], "active")


def cagr(nav_start: float, nav_end: float, n_days: int, basis: str = "trading252") -> float:
    if nav_end <= 0 or n_days <= 0:
        return -1.0
    per_year = TRADING_DAYS if basis == "trading252" else 365.25
    return float((nav_end / nav_start) ** (per_year / n_days) - 1.0)


def arithmetic_annualized(r) -> float:
    """252 x mean daily return. Never call this 'annualized_return'."""
    return float(np.mean(r) * TRADING_DAYS)


def sharpe(r, rf: float = 0.0, periods: int = TRADING_DAYS, ddof: int = 1) -> float:
    r = np.asarray(r, dtype=float) - rf / periods
    sd = r.std(ddof=ddof)
    return float(r.mean() / sd * np.sqrt(periods)) if sd > 0 else float("nan")


def sortino(r, mar: float = 0.0, periods: int = TRADING_DAYS) -> float:
    """Semi-deviation over ALL observations: sqrt(mean(min(r - mar, 0)^2))."""
    r = np.asarray(r, dtype=float)
    dd = np.sqrt(np.mean(np.minimum(r - mar, 0.0) ** 2))
    return float((r.mean() - mar) * periods / (dd * np.sqrt(periods))) if dd > 0 else float("nan")


def max_drawdown(nav, initial: float) -> float:
    """Worst peak-to-trough, with the starting capital counted as the first peak."""
    path = np.concatenate([[initial], np.asarray(nav, dtype=float)])
    return float((path / np.maximum.accumulate(path) - 1.0).min())


def psr(sr_hat: float, T: int, skew: float, kurt: float, sr_bench: float = 0.0) -> float:
    """Probabilistic Sharpe ratio (Bailey & Lopez de Prado). `sr_hat`, `sr_bench` per period; kurt NON-excess."""
    denom = 1.0 - skew * sr_hat + (kurt - 1.0) / 4.0 * sr_hat ** 2
    if denom <= 0 or T < 2:
        return float("nan")
    return float(sps.norm.cdf((sr_hat - sr_bench) * np.sqrt(T - 1) / np.sqrt(denom)))


def expected_max_sr(n_trials: int, var_sr_trials: float) -> float:
    """Expected maximum per-period Sharpe among `n_trials` independent null trials."""
    if n_trials < 2:
        return 0.0
    return float(np.sqrt(var_sr_trials) * ((1 - EULER_GAMMA) * sps.norm.ppf(1 - 1.0 / n_trials)
                                           + EULER_GAMMA * sps.norm.ppf(1 - 1.0 / (n_trials * np.e))))


def dsr(sr_hat: float, T: int, skew: float, kurt: float, n_trials: int, var_sr_trials: float) -> float:
    return psr(sr_hat, T, skew, kurt, sr_bench=expected_max_sr(n_trials, var_sr_trials))


def minimum_track_record_length(sr_hat: float, skew: float, kurt: float, sr_bench: float = 0.0, alpha: float = 0.05) -> float:
    """Periods needed for PSR(sr_bench) >= 1 - alpha."""
    if sr_hat <= sr_bench:
        return float("inf")
    z = sps.norm.ppf(1 - alpha)
    return float(1 + (1 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2) * (z / (sr_hat - sr_bench)) ** 2)


def _block_ids(index: pd.DatetimeIndex, block):
    if isinstance(block, int):
        return np.arange(len(index)) // block
    per = pd.DatetimeIndex(index).to_period("M" if block == "month" else "Q")
    return pd.factorize(per, sort=True)[0]


def block_bootstrap(r, index=None, block="month", n: int = 2000, seed: int = 0, stat=None) -> np.ndarray:
    """Circular-ish block bootstrap: resample whole blocks (calendar months/quarters, or fixed length) with
    replacement and concatenate until the original length is reached. `stat` maps a return array to a float
    (default: annualised Sharpe). Returns the `n` bootstrap draws of the statistic."""
    r = np.asarray(r, dtype=float)
    stat = stat or sharpe
    ids = _block_ids(index if index is not None else pd.date_range("2000-01-03", periods=len(r), freq="B"), block)
    starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
    ends = np.r_[starts[1:], len(r)]
    blocks = [r[s:e] for s, e in zip(starts, ends)]
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    for i in range(n):
        picked, total = [], 0
        while total < len(r):
            b = blocks[rng.integers(len(blocks))]
            picked.append(b)
            total += len(b)
        out[i] = stat(np.concatenate(picked)[:len(r)])
    return out


def paired_block_bootstrap(r_a, r_b, index=None, block="month", n: int = 2000, seed: int = 0, stat=None) -> np.ndarray:
    """Draws of stat(a) - stat(b) using the SAME resampled dates for both series (for ablations)."""
    r_a, r_b = np.asarray(r_a, dtype=float), np.asarray(r_b, dtype=float)
    assert len(r_a) == len(r_b)
    stat = stat or sharpe
    ids = _block_ids(index if index is not None else pd.date_range("2000-01-03", periods=len(r_a), freq="B"), block)
    starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
    ends = np.r_[starts[1:], len(r_a)]
    spans = list(zip(starts, ends))
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    for i in range(n):
        idx, total = [], 0
        while total < len(r_a):
            s, e = spans[rng.integers(len(spans))]
            idx.append(np.arange(s, e))
            total += e - s
        sel = np.concatenate(idx)[:len(r_a)]
        out[i] = stat(r_a[sel]) - stat(r_b[sel])
    return out


def alpha_hac(r, factors: np.ndarray, rf=0.0, lags="auto") -> dict:
    """OLS alpha on `factors` (T x k) with Newey-West/Bartlett errors. lags='auto' -> floor(4 (T/100)^(2/9)).
    Reuses alpha._ols_hac (the single NW routine in the repo)."""
    y = np.asarray(r, dtype=float) - rf
    F = np.asarray(factors, dtype=float)
    if F.ndim == 1:
        F = F[:, None]
    T = len(y)
    L = int(np.floor(4 * (T / 100) ** (2 / 9))) if lags == "auto" else int(lags)
    X = np.column_stack([np.ones(T), F])
    b, se, e = _ols_hac(y, X, lags=L)
    r2 = 1.0 - e.var() / y.var() if y.var() > 0 else float("nan")
    return dict(alpha_ann=float(b[0] * TRADING_DAYS), t=float(b[0] / se[0]), loadings=b[1:].tolist(), r2=float(r2), lags=L)


def era_table(daily: pd.DataFrame, cap0: float, eras: dict = None) -> pd.DataFrame:
    r = returns_from_daily(daily, cap0)
    rows = []
    for name, (y0, y1) in (eras or ERAS).items():
        x = r.loc[y0:y1]
        rows.append(dict(era=name, n_days=len(x), sharpe=sharpe(x) if len(x) > 2 else np.nan,
                         ret_sum=float(x.sum())))
    return pd.DataFrame(rows)


def active_summary(daily: pd.DataFrame, cap0: float) -> dict:
    """Active-window restatement of the headline numbers (does not touch metrics.summarize)."""
    w = active_window(daily)
    d = daily[(pd.DatetimeIndex(daily["date"]) >= w.start) & (pd.DatetimeIndex(daily["date"]) <= w.end)]
    r = returns_from_daily(daily, cap0).loc[w.start:w.end].to_numpy()
    nav_start = float(daily["nav"].iloc[max(daily.index.get_loc(d.index[0]) - 1, 0)]) if d.index[0] > daily.index[0] else cap0
    return dict(window_start=str(w.start.date()), window_end=str(w.end.date()), n_days_active=int(len(r)),
                cagr=cagr(nav_start, float(d["nav"].iloc[-1]), len(r)), arith_ann_252=arithmetic_annualized(r),
                sharpe_active=sharpe(r), sortino=sortino(r), max_drawdown_incl_initial=max_drawdown(daily["nav"], cap0),
                skew=float(sps.skew(r)), kurtosis=float(sps.kurtosis(r, fisher=False)),
                psr_0=psr(float(r.mean() / r.std(ddof=1)), len(r), float(sps.skew(r)), float(sps.kurtosis(r, fisher=False))))
