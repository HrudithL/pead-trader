"""Position-weight constructions shared by the equity strategies.

Every function takes an events DataFrame (columns used are documented per function) and returns a
signed weight per event, aligned to the frame. They are pure functions of event features -- no
backtest state, no data loading -- which is what lets the live paper-trading engine call the exact
same code on today's cohort of earnings reporters that the backtest calls on 17 years of history.
"""
import numpy as np
import pandas as pd


def centered_rank(events: pd.DataFrame, col: str = "sue_rank_pct") -> np.ndarray:
    """SUE rank within the announcement quarter, centered: -0.5 (worst surprise) .. +0.5 (best)."""
    return events[col].to_numpy() - 0.5


def extreme_decile(events: pd.DataFrame) -> np.ndarray:
    """+1 for decile 10 (best surprise), -1 for decile 1, nothing for deciles 2-9."""
    return np.where(events["decile"] == 10, 1.0, np.where(events["decile"] == 1, -1.0, 0.0))


def rank_weighted(events: pd.DataFrame) -> np.ndarray:
    """Every event, weight proportional to its SUE-rank distance from the median."""
    return 2 * (events["sue_rank_pct"].to_numpy() - 0.5)


def size_balanced_extreme(events: pd.DataFrame) -> np.ndarray:
    """Extreme deciles only, with +-1 unit of weight spread evenly across the size quintiles
    present in each announcement quarter so no size bucket dominates a leg."""
    out = np.zeros(len(events))
    ev = events.reset_index(drop=True)
    ok = ev["decile"].isin([1, 10]) & ev["size_quintile"].notna()
    parts = []
    for dec, sign in ((10, 1.0), (1, -1.0)):
        sub = ev[ok & (ev["decile"] == dec)]
        counts = sub.groupby(["ann_quarter", "size_quintile"])["event_id"].transform("count")
        nq = sub.groupby("ann_quarter")["size_quintile"].transform("nunique")
        parts.append(pd.Series(sign * (1.0 / nq) / counts, index=sub.index))
    w = pd.concat(parts)
    # balanced + extreme share a comparable mean |weight| of 1.0
    out[w.index.to_numpy()] = (w / w.abs().mean()).to_numpy()
    return out


def neutralized_tilt(events: pd.DataFrame, d_centered: np.ndarray, trim: float, tilt_power: float,
                     groups=("size_quintile", "ff12_sector"), by_side: bool = True, scale=None) -> np.ndarray:
    """Trim -> tilt -> neutralize a centered cross-sectional signal into position weights.

      trim        drop events whose |signal| <= trim (the low-conviction middle of the rank distribution)
      tilt_power  weight ~ (|signal|/0.5)**tilt_power, so 1.0 is linear in rank, >1 leans to the extremes
      groups      within each announcement quarter, every (side, *groups) cell gets an EQUAL share of
                  that side's budget (so no size bucket / sector can dominate), split among its members
                  by tilt. ("size_quintile",) = Conviction Tilt, + "ff12_sector" = Sector Balanced / Balanced Leverage / the Calm strategies.
      by_side     True: long and short legs each sum to the same total (dollar-neutral by construction).
                  False: cells are neutralized across both sides together, so net long/short exposure
                  falls out of the signal (Market Exposed) -- which makes the book directional.
      scale       optional per-event multiplier applied BEFORE neutralization; it reallocates weight
                  only WITHIN a cell, so cell totals (and neutrality) are unchanged. 0 drops the event
                  from its cell entirely. Used for inverse-volatility sizing and cell filters.

    Result is rescaled to a mean |weight| of 0.5 (the engine's `base_unit_fraction` calibration)."""
    n = len(events)
    frac = np.clip(np.abs(d_centered) / 0.5, 0, 1)
    tilt = np.sign(d_centered) * (frac ** tilt_power)
    keep = np.abs(d_centered) > trim
    if scale is not None:
        scale = np.nan_to_num(np.asarray(scale, dtype=float), nan=0.0)
        tilt = tilt * scale
        keep = keep & (scale > 0)
    w = np.where(keep, tilt, 0.0)
    kept = np.flatnonzero(keep)
    out = np.zeros(n)
    if len(kept) == 0:
        return out
    sub = events.iloc[kept][["ann_quarter", *groups]].copy()
    sub["w"] = w[kept]
    sub["side"] = np.sign(sub["w"])
    cell_cols = ["ann_quarter", *( ["side"] if by_side else [] ), *groups]
    sub["cell"] = list(zip(*(sub[c] for c in cell_cols)))
    totals = sub.groupby("cell")["w"].transform(lambda x: x.abs().sum()).to_numpy()
    budget_keys = ["ann_quarter", "side"] if by_side else ["ann_quarter"]
    n_cells = sub.groupby([sub[k] for k in budget_keys])["cell"].transform("nunique").to_numpy()
    safe = totals > 0
    neutral = np.zeros(len(sub))
    neutral[safe] = sub["w"].to_numpy()[safe] / totals[safe] / n_cells[safe]
    out[kept] = neutral
    nz = out[out != 0]
    if len(nz):
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5
    return out
