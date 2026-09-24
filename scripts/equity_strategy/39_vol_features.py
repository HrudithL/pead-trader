"""
Strategy 9, step 1: per-event volatility features, the substrate for subsampling the SUE deciles
by how volatile each stock is going into its announcement.

Two measures, both computed ONLY from trading days strictly before day0 (the gather window ends at
entry_row_idx - 1, the same leak-free convention 32_gpu_feature_panel.py uses):

  - tvol_60d     total volatility: std of the stock's raw daily returns over the 60 trading days
                 before day0 (needs >= 30 valid days).
  - ivol_252d    idiosyncratic volatility: std of the residuals from a market-model regression
                 (ret_i = a + b * vwretd + e) fit on the 252 trading days before day0 (needs
                 >= 120 valid days). This is the "arbitrage risk" measure from the PEAD
                 limits-to-arbitrage literature (e.g. Mendenhall 2004): idiosyncratic risk can't
                 be hedged away by an arbitrageur holding the market, so it's the risk that should
                 slow the drift's correction -- and it's also the risk a market-adjusted position
                 in this strategy actually carries.

Both are then ranked against the PREVIOUS announcement quarter's distribution within the event's
own size quintile:

  - Within size quintile, because volatility is strongly (inversely) tied to size. Ranking raw vol
    across the whole cross-section would mostly re-sort by size and quietly undo the size
    neutralization every strategy since Balanced relies on.
  - Against the previous quarter, not the event's own quarter, because an event early in a quarter
    can't know the vol of firms announcing later in that same quarter. The first quarter of the
    sample has no prior quarter and gets NaN ranks.

  -> <m>_pct       percentile (0-1) of the event's vol within that prior-quarter, same-size-quintile
                   distribution
  -> <m>_tercile   0 (low) / 1 / 2 (high), cut at that distribution's 33rd/67th percentiles

Also carries two outcome columns for the diagnostic (40) -- NOT signals:
  - hold_ret       sum of the stock's daily market-adjusted returns over its own holding window
                   (entry_row_idx+1 .. exit_row_idx), i.e. exactly what one dollar of notional in
                   the backtest engine earns (the engine accrues fixed-notional, non-compounded P&L)
  - ret_fwd_60d_mktadj  the fixed-60-day market-adjusted forward return from the event file

Output: data/equity_vol_panel.parquet (one row per row of positions_rankweighted_v2.parquet after
the same cleaning 23/24 apply, same order, superset of its columns).
"""
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, RAW_EQUITY_DIR
from lib.backtest_v2 import load_mktadj_panel, load_positions

TVOL_WINDOW, TVOL_MIN = 60, 30
IVOL_WINDOW, IVOL_MIN = 252, 120
CHUNK = 20_000  # events per gather chunk: (20k x 252) float64 matrices, ~40MB each


def trailing_window(values, eq_permno, entry_row_idx, event_permno, window):
    """(n, window) matrix of values[entry-window .. entry-1], NaN where the window runs off the
    panel or into a different permno's history."""
    offsets = np.arange(-window, 0)
    idx = entry_row_idx[:, None] + offsets[None, :]
    in_bounds = idx >= 0
    idx_safe = np.clip(idx, 0, len(values) - 1)
    valid = in_bounds & (eq_permno[idx_safe] == event_permno[:, None])
    return np.where(valid, values[idx_safe], np.nan)


def total_vol(ret_win):
    n = np.sum(np.isfinite(ret_win), axis=1)
    with warnings.catch_warnings():  # rows with < 2 valid days; masked to NaN just below anyway
        warnings.simplefilter("ignore", RuntimeWarning)
        v = np.nanstd(ret_win, axis=1, ddof=1)
    return np.where(n >= TVOL_MIN, v, np.nan)


def idio_vol(ret_win, mkt_win):
    """Per-row OLS of ret on mkt over the jointly-valid days; residual std (ddof=2)."""
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


def prior_quarter_ranks(df, col):
    """Percentile and tercile of df[col] against the previous ann_quarter's distribution within
    the same size_quintile. Returns (pct, tercile) arrays aligned to df."""
    pct = np.full(len(df), np.nan)
    terc = np.full(len(df), np.nan)
    quarters = sorted(df["ann_quarter"].dropna().unique())
    for q_prev, q in zip(quarters[:-1], quarters[1:]):
        for sq in range(1, 6):
            ref = df.loc[(df["ann_quarter"] == q_prev) & (df["size_quintile"] == sq), col].dropna().to_numpy()
            if len(ref) < 30:
                continue
            ref = np.sort(ref)
            tgt = (df["ann_quarter"] == q) & (df["size_quintile"] == sq) & df[col].notna()
            vals = df.loc[tgt, col].to_numpy()
            p = np.searchsorted(ref, vals, side="right") / len(ref)
            lo, hi = np.quantile(ref, [1 / 3, 2 / 3])
            idx = np.where(tgt.to_numpy())[0]
            pct[idx] = p
            terc[idx] = np.where(vals <= lo, 0, np.where(vals <= hi, 1, 2))
    return pct, terc


def main():
    t0 = time.time()
    print("loading daily panel and positions...")
    eq, _ = load_mktadj_panel(RAW_EQUITY_DIR)
    pos = load_positions(DATA_DIR)
    print(f"  {len(eq):,} daily rows, {len(pos):,} positions  [{time.time()-t0:.1f}s]")

    eq_ret_raw = eq["ret"].to_numpy(dtype=float)
    eq_mkt = eq["vwretd"].to_numpy(dtype=float)
    eq_permno = eq["permno"].to_numpy()
    entry = pos["entry_row_idx"].to_numpy()
    ev_permno = pos["permno"].to_numpy(dtype=eq_permno.dtype)

    tvol = np.empty(len(pos))
    ivol = np.empty(len(pos))
    beta = np.empty(len(pos))
    for s in range(0, len(pos), CHUNK):
        e = min(s + CHUNK, len(pos))
        r60 = trailing_window(eq_ret_raw, eq_permno, entry[s:e], ev_permno[s:e], TVOL_WINDOW)
        tvol[s:e] = total_vol(r60)
        r252 = trailing_window(eq_ret_raw, eq_permno, entry[s:e], ev_permno[s:e], IVOL_WINDOW)
        m252 = trailing_window(eq_mkt, eq_permno, entry[s:e], ev_permno[s:e], IVOL_WINDOW)
        ivol[s:e], beta[s:e] = idio_vol(r252, m252)
    pos["tvol_60d"] = tvol
    pos["ivol_252d"] = ivol
    pos["beta_252d"] = beta
    print(f"  vol features: tvol coverage {np.isfinite(tvol).mean():.1%}, "
          f"ivol coverage {np.isfinite(ivol).mean():.1%}  [{time.time()-t0:.1f}s]")

    for col in ["tvol_60d", "ivol_252d"]:
        pct, terc = prior_quarter_ranks(pos, col)
        pos[f"{col}_pct"] = pct
        pos[f"{col}_tercile"] = pd.array(np.where(np.isfinite(terc), terc, np.nan)).astype("Int64")
    print(f"  prior-quarter ranks: tercile coverage tvol {pos['tvol_60d_tercile'].notna().mean():.1%}, "
          f"ivol {pos['ivol_252d_tercile'].notna().mean():.1%}  [{time.time()-t0:.1f}s]")

    csum = np.concatenate([[0.0], np.cumsum(eq["ret_mktadj"].to_numpy())])
    pos["hold_ret"] = csum[pos["exit_row_idx"].to_numpy() + 1] - csum[entry + 1]

    ev = pd.read_parquet(DATA_DIR / "decile_events_ff_adjusted_extended.parquet",
                          columns=["permno", "day0_date", "ret_fwd_60d_mktadj"])
    ev["day0_date"] = pd.to_datetime(ev["day0_date"])
    # (permno, day0_date) isn't unique upstream (same-day multi-fiscal-quarter IBES events -- see
    # 32_gpu_feature_panel.py); one value per key is all this outcome column needs.
    ev = ev.drop_duplicates(subset=["permno", "day0_date"], keep="first")
    n_before = len(pos)
    pos = pos.merge(ev, on=["permno", "day0_date"], how="left")
    assert len(pos) == n_before, "ret_fwd_60d_mktadj merge fanned out"

    out = DATA_DIR / "equity_vol_panel.parquet"
    pos.to_parquet(out, index=False)
    print(f"wrote {out} ({len(pos):,} rows)  [{time.time()-t0:.1f}s]")
    print(pos[["tvol_60d", "ivol_252d", "beta_252d"]].describe(percentiles=[.1, .5, .9]).round(4).to_string())


if __name__ == "__main__":
    main()
