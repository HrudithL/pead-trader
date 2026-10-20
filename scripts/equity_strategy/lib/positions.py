"""Shared position-construction helpers for the size-balanced and size+sector-neutral strategies
(11, 16_positions_v2, 23-24, 34-35)."""
import numpy as np


def open_gross_notional(notional, entry_qcode, exit_qcode, qi):
    """Abs notional of positions entered before quarter `qi` that haven't exited by it yet --
    the carryover gross exposure already on the book that a quarter's NEW positions have to share
    the leverage cap with. Bug this fixes: every finalize_strategyN.py script used to cap only
    `gross_requested` (this quarter's own new-position targets) against MAX_GROSS_LEVERAGE *
    trailing_nav, never adding in still-open positions from prior quarters (holding periods span
    multiple quarters via the decay-day cells), so the cap only ever bound entry-cohort size, not
    actual portfolio gross exposure -- which is why realized gross_exposure (computed post-hoc
    from the exit/entry deltas) could run to several times the nominal cap."""
    still_open = (entry_qcode < qi) & (exit_qcode >= qi)
    return float(np.abs(notional[still_open]).sum())


def cap_new_positions_by_leverage(target, max_gross, open_gross):
    """Priority-trim this quarter's new position notional (`target`, signed dollars, lower
    |target| dropped first) so that, combined with `open_gross` (abs notional already open from
    prior quarters -- see open_gross_notional), total gross exposure never exceeds `max_gross`.
    Returns (capped_notional, cap_status, n_dropped)."""
    target = np.asarray(target, dtype=float)
    available = max_gross - open_gross
    gross_requested = float(np.abs(target).sum())
    if available <= 0:
        return np.zeros_like(target), "priority_capped", len(target)
    if gross_requested <= available or gross_requested == 0:
        return target.copy(), "ok", 0
    order_priority = np.argsort(-np.abs(target))
    cum = np.cumsum(np.abs(target[order_priority]))
    keep_mask = cum <= available
    q_notional = np.zeros_like(target)
    q_notional[order_priority[keep_mask]] = target[order_priority[keep_mask]]
    n_kept = int(keep_mask.sum())
    n_dropped = len(target) - n_kept
    if n_kept < len(target):
        remaining = available - (cum[n_kept - 1] if n_kept > 0 else 0.0)
        boundary = order_priority[n_kept]
        if remaining > 0:
            q_notional[boundary] = np.sign(target[boundary]) * min(abs(target[boundary]), remaining)
            n_dropped -= 1
    return q_notional, "priority_capped", n_dropped


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
