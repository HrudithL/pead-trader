"""Leak-free trailing volatility measures, shared by the equity (39) and options (46-48) sides.

Every window ends the trading day BEFORE day0 (the announcement reaction day itself is excluded),
and never crosses into a different permno's history -- see trailing_window.
"""
import warnings

import numpy as np
import pandas as pd

TVOL_WINDOW, TVOL_MIN = 60, 30
IVOL_WINDOW, IVOL_MIN = 252, 120


def trailing_window(values, eq_permno, entry_row_idx, event_permno, window):
    """(n, window) matrix of values[entry-window .. entry-1] from a (permno, date)-sorted daily
    panel, NaN where the window runs off the panel or into a different permno's history."""
    offsets = np.arange(-window, 0)
    idx = entry_row_idx[:, None] + offsets[None, :]
    in_bounds = idx >= 0
    idx_safe = np.clip(idx, 0, len(values) - 1)
    valid = in_bounds & (eq_permno[idx_safe] == event_permno[:, None])
    return np.where(valid, values[idx_safe], np.nan)


def total_vol(ret_win):
    """Std of daily returns per row; NaN with fewer than TVOL_MIN valid days."""
    n = np.sum(np.isfinite(ret_win), axis=1)
    with warnings.catch_warnings():  # rows with < 2 valid days; masked to NaN just below anyway
        warnings.simplefilter("ignore", RuntimeWarning)
        v = np.nanstd(ret_win, axis=1, ddof=1)
    return np.where(n >= TVOL_MIN, v, np.nan)


def idio_vol(ret_win, mkt_win):
    """Per-row OLS of ret on mkt over the jointly-valid days: (residual std with ddof=2, beta).
    NaN with fewer than IVOL_MIN valid days."""
    ok = np.isfinite(ret_win) & np.isfinite(mkt_win)
    n = ok.sum(axis=1)
    r = np.where(ok, ret_win, 0.0)
    m = np.where(ok, mkt_win, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        r_mean = r.sum(axis=1) / n
        m_mean = m.sum(axis=1) / n
        rc = np.where(ok, r - r_mean[:, None], 0.0)
        mc = np.where(ok, m - m_mean[:, None], 0.0)
        beta = (rc * mc).sum(axis=1) / (mc * mc).sum(axis=1)
        resid = np.where(ok, rc - beta[:, None] * mc, 0.0)
        v = np.sqrt((resid * resid).sum(axis=1) / (n - 2))
    return np.where(n >= IVOL_MIN, v, np.nan), np.where(n >= IVOL_MIN, beta, np.nan)


def event_total_vol(permno, day0_date, raw_equity_dir, chunk=50_000):
    """tvol_60d (daily std of raw returns over the 60 trading days before day0) for arbitrary
    (permno, day0_date) events, looked up in the normalized CRSP panel. Events whose day0 isn't a
    trading day for that permno in the panel get NaN rather than a nearby day's value."""
    frames = [pd.read_parquet(raw_equity_dir / f"equity_{y}.parquet", columns=["permno", "date", "ret"])
              for y in range(1995, 2015)]
    eq = (pd.concat(frames, ignore_index=True)
          .sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True))
    eq["row_idx"] = np.arange(len(eq))
    ev = pd.DataFrame({"permno": pd.to_numeric(permno).astype("int64"),
                       "date": pd.to_datetime(day0_date)})
    eq["permno"] = eq["permno"].astype("int64")
    ev = ev.merge(eq[["permno", "date", "row_idx"]], on=["permno", "date"], how="left")
    out = np.full(len(ev), np.nan)
    ok = ev["row_idx"].notna().to_numpy()
    rows = ev.loc[ok, "row_idx"].astype("int64").to_numpy()
    perm = ev.loc[ok, "permno"].to_numpy()
    ret = eq["ret"].to_numpy(dtype=float)
    eq_permno = eq["permno"].to_numpy()
    vals = np.empty(len(rows))
    for s in range(0, len(rows), chunk):
        e = min(s + chunk, len(rows))
        vals[s:e] = total_vol(trailing_window(ret, eq_permno, rows[s:e], perm[s:e], TVOL_WINDOW))
    out[ok] = vals
    return out
