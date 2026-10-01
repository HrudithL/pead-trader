"""Adapter: live earnings cohort -> the event schema every registered strategy consumes.

The backtest's events come from WRDS (IBES SUE, CRSP size/liquidity, Fama-French sectors). Live, the
same columns are rebuilt from free data, each an explicit, documented substitution:

    sue_rank_pct         rank of live SUE-z within the rolling cohort of recent reporters
    decile               ceil(10 x rank)
    size_quintile        market-cap rank within the cohort
    liquidity_quintile   21d average dollar volume rank within the cohort
    ff12_sector          GICS sector mapped to the nearest Fama-French 12 group
    ann_quarter          one label for the whole cohort (neutralization happens within it)
    ivol_252d, tvol_60d  trailing volatility from price history vs SPY
    ivol_252d_pct        idiosyncratic vol percentile vs the PREVIOUS window's reporters of the same size
                         quintile (the backtest ranks against the prior quarter's cross-section)
    holding_days         median backtest holding window for the event's (decile, size quintile)
"""
import numpy as np
import pandas as pd

from . import config

_HOLD_CACHE = {}


def holding_table():
    """(decile, size_quintile) -> median backtest holding days. Built once from the backtest's events and
    cached in data/live/holding_days.csv (50 derived numbers, safe to commit); falls back to 60 days."""
    if "t" in _HOLD_CACHE:
        return _HOLD_CACHE["t"]
    if config.HOLDING_DAYS_CSV.exists():
        t = pd.read_csv(config.HOLDING_DAYS_CSV)
    else:
        try:
            from pead.core.data import _load_events
            ev = _load_events()
            t = (ev.dropna(subset=["size_quintile"]).groupby(["decile", "size_quintile"])["holding_days"]
                 .median().round().reset_index())
            t.to_csv(config.HOLDING_DAYS_CSV, index=False)
        except Exception:
            t = pd.DataFrame(columns=["decile", "size_quintile", "holding_days"])
    _HOLD_CACHE["t"] = {(int(r.decile), int(r.size_quintile)): int(r.holding_days) for r in t.itertuples()}
    return _HOLD_CACHE["t"]


def _bucket(values, k):
    r = pd.Series(values).rank(pct=True)
    return np.ceil(r * k).clip(1, k).astype(int).to_numpy()


def build_events(cohort: pd.DataFrame, history: pd.DataFrame = None) -> pd.DataFrame:
    """cohort: rows with ticker, report_date, sue_z, sector, market_cap, adv21_dollars, ivol_252d, tvol_60d.
    history: earlier reporters (same columns) used to rank volatility against the previous window."""
    df = cohort.copy().reset_index(drop=True)
    for c in ("sue_z", "market_cap", "adv21_dollars", "ivol_252d", "tvol_60d"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["sue_rank_pct"] = df["sue_z"].rank(pct=True)
    df["decile"] = np.ceil(df["sue_rank_pct"] * 10).clip(1, 10).astype(int)
    df["size_quintile"] = _bucket(df["market_cap"].fillna(df["market_cap"].median()), 5)
    df["liquidity_quintile"] = _bucket(df["adv21_dollars"].fillna(df["adv21_dollars"].median()), 5)
    df["ff12_sector"] = df["sector"].map(config.GICS_TO_FF12).fillna("Other")
    df["avg_dollar_volume_21d"] = df["adv21_dollars"]
    df["ann_quarter"] = "LIVE"
    df["event_id"] = df["ticker"].astype(str) + "_" + df["report_date"].astype(str)
    df["ivol_252d_pct"] = 0.5
    if history is not None and len(history) >= 20 and df["ivol_252d"].notna().any():
        h = history.copy()
        for c in ("ivol_252d", "market_cap"):
            h[c] = pd.to_numeric(h[c], errors="coerce")
        h["size_quintile"] = _bucket(h["market_cap"].fillna(h["market_cap"].median()), 5)
        for q, grp in df.groupby("size_quintile"):
            ref = h.loc[h["size_quintile"] == q, "ivol_252d"].dropna().to_numpy()
            if len(ref) >= 10:
                pct = np.searchsorted(np.sort(ref), grp["ivol_252d"].fillna(np.nanmedian(ref)).to_numpy()) / len(ref)
                df.loc[grp.index, "ivol_252d_pct"] = pct
    ht = holding_table()
    df["holding_days"] = [ht.get((int(d), int(s)), config.DEFAULT_HOLDING_TRADING_DAYS)
                          for d, s in zip(df["decile"], df["size_quintile"])]
    return df
