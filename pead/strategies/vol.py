"""Volatility-based event scaling used by the Strategy 9 family.

Finding behind these (research diagnostic, prep stage `equity_vol_diagnostic`): the PEAD spread is
strongest in LOW idiosyncratic-volatility stocks (D10-D1 +4.3% in the low tercile vs +2.1% in the
high one). Volatility does not change average returns -- it changes how much a given SUE signal is
worth -- so the right use is to SIZE by it, not to hard-filter on it.
"""
import numpy as np
import pandas as pd

IVOL_CLIP = (0.008, 0.08)          # daily; ~13%..127% annualized, so one bad data point can't dominate
MIN_HISTORY_QUARTERS = 12


def inverse_ivol_scale(events: pd.DataFrame, col: str = "ivol_252d", fallback: str = "tvol_60d") -> np.ndarray:
    """1 / clipped idiosyncratic vol (falling back to total 60d vol when the 252d window is short).
    Neutralization normalizes within each (quarter, side, sector, size) cell, so only RELATIVE vol
    inside a cell matters -- the book stays size/sector/dollar neutral. No fitted parameters."""
    vol = events[col].fillna(events[fallback]).to_numpy(dtype=float)
    return np.where(np.isfinite(vol), 1.0 / np.clip(vol, *IVOL_CLIP), 1.0 / np.nanmedian(vol))


def walk_forward_cell_filter(events: pd.DataFrame, trim: float, vol_col: str = "ivol_252d"):
    """Scale of 1.0 / 0.0 per event: drop a (side, vol-tercile) cell for NEW entries in quarter Q when
    its historical EXCESS contribution -- side x (hold_ret minus that announcement quarter's mean
    hold_ret) -- averages <= 0, using only announcement quarters whose positions had ALL exited before
    Q began, and only once >= MIN_HISTORY_QUARTERS such quarters exist. No full-sample information."""
    d = events["sue_rank_pct"].to_numpy() - 0.5
    side = pd.Series(np.where(d > trim, 1, np.where(d < -trim, -1, 0)), index=events.index)
    terc = events[f"{vol_col}_tercile"].astype("float")
    excess = events["hold_ret"] - events.groupby("ann_quarter")["hold_ret"].transform("mean")
    signed = side * excess
    q_done = events.groupby("ann_quarter")["exit_date"].max()
    entry_q = events["day0_date"].dt.to_period("Q")
    cell_q = (pd.DataFrame({"ann_quarter": events["ann_quarter"], "side": side, "terc": terc, "x": signed})
              [(side != 0) & terc.notna()].groupby(["side", "terc", "ann_quarter"])["x"].mean())
    scale = np.ones(len(events))
    for q in sorted(entry_q.unique()):
        usable = set(q_done[q_done < q.start_time].index)
        for (s, t), series in cell_q.groupby(level=["side", "terc"]):
            hist = series[series.index.get_level_values("ann_quarter").isin(usable)]
            if len(hist) >= MIN_HISTORY_QUARTERS and hist.mean() <= 0:
                scale[((entry_q == q) & (side == s) & (terc == t)).to_numpy()] = 0.0
    return scale


def low_vol_rank_scale(events: pd.DataFrame, power: float = 2.0, col: str = "ivol_252d_pct") -> np.ndarray:
    """(1.02 - vol percentile) ** power: weight falls smoothly from the calmest stocks (~1.0) to the wildest
    (~0.02). The percentile is the event's idiosyncratic vol against the PREVIOUS announcement quarter's
    distribution within its own size quintile (prep stage equity_vol_features) -- so it is leak-free and
    computable live from the last quarter's cross-section. Unlike 1/vol it does not depend on the vol
    level or on clip bounds, only on rank, and it all but switches off the highest-vol names."""
    pct = events[col].fillna(0.5).to_numpy(dtype=float)
    return (1.02 - pct) ** power
