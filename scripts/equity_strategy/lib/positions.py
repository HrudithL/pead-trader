"""Shared position-construction helper for the size-balanced strategies (11, 16_positions_v2)."""


def size_balance(df, decile_val, sign):
    """Within one decile, spread +-1 unit of weight evenly across size quintiles each quarter,
    so no single size bucket dominates the leg."""
    sub = df[df["decile"] == decile_val].copy()
    counts = sub.groupby(["ann_quarter", "size_quintile"])["event_id"].transform("count")
    quintiles_per_q = sub.groupby("ann_quarter")["size_quintile"].transform("nunique")
    sub["weight"] = sign * (1.0 / quintiles_per_q) / counts
    return sub
