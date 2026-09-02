"""Shared position-construction helpers for the size-balanced and size+sector-neutral strategies
(11, 16_positions_v2, 23-24, 34-35)."""
import numpy as np


def size_balance(df, decile_val, sign):
    """Within one decile, spread +-1 unit of weight evenly across size quintiles each quarter,
    so no single size bucket dominates the leg."""
    sub = df[df["decile"] == decile_val].copy()
    counts = sub.groupby(["ann_quarter", "size_quintile"])["event_id"].transform("count")
    quintiles_per_q = sub.groupby("ann_quarter")["size_quintile"].transform("nunique")
    sub["weight"] = sign * (1.0 / quintiles_per_q) / counts
    return sub


def weights_size_sector_neutral(pos, d_centered, trim, tilt_power):
    """Trim/tilt/size+sector-neutralize an arbitrary already-centered cross-sectional signal
    (d_centered in [-0.5, 0.5], e.g. sue_rank_pct - 0.5) into a position weight vector -- the
    exact construction 23/24_finalize_strategyN.py use for sue_rank_pct specifically, generalized
    here so 34_gpu_param_sweep.py and 35_finalize_strategy8.py can reuse it for an arbitrary blend
    of SUE rank and the walk-forward ML score instead of duplicating the neutralization logic."""
    n_pos = len(pos)
    frac = np.clip(np.abs(d_centered) / 0.5, 0, 1)
    tilt = np.sign(d_centered) * (frac ** tilt_power)
    keep = np.abs(d_centered) > trim
    w = np.where(keep, tilt, 0.0)
    df = pos[["ann_quarter", "size_quintile", "ff12_sector"]].copy()
    df["w"] = w
    df["side"] = np.sign(w)
    kept_idx = np.where(keep)[0]
    sub = df.iloc[kept_idx].copy()
    grp_key = list(zip(sub["ann_quarter"], sub["side"], sub["ff12_sector"], sub["size_quintile"]))
    sub["grp_key"] = grp_key
    grp_totals = sub.groupby("grp_key")["w"].transform(lambda x: x.abs().sum()).to_numpy()
    n_groups = sub.groupby([sub["ann_quarter"], sub["side"]])["grp_key"].transform("nunique").to_numpy()
    safe = grp_totals > 0
    w_neutral = np.zeros(len(sub))
    w_neutral[safe] = sub["w"].to_numpy()[safe] / grp_totals[safe] / n_groups[safe]
    out = np.zeros(n_pos)
    out[kept_idx] = w_neutral
    nz = out[out != 0]
    if len(nz):
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5
    return out
