"""
Rebuild the three strategies' position lists using PER-CELL (not per-decile) holding periods:
each event's holding period now comes from its own (decile x size x book-to-market) cell's
empirically measured decay day (cell_decay_days.csv), falling back to the coarser medium/coarse
cell exactly as already resolved in event_cells.parquet when a fine cell didn't have enough
quarters/events to trust.

Output: data/positions_extreme_v2.parquet, data/positions_rankweighted_v2.parquet,
        data/positions_balanced_v2.parquet
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path("/root/pead_report/data")
RAW = Path("/mnt/user-data/uploads/PEAD_Trading/data/normalized_equity")

cells = pd.read_parquet(DATA / "event_cells.parquet")
decay = pd.read_csv(DATA / "cell_decay_days.csv")[["cell", "decay_day"]]
cells = cells.merge(decay, left_on="holding_cell", right_on="cell", how="left")
cells["holding_days"] = cells["decay_day"].fillna(60).astype(int)  # 60d fallback, matches
                                                                     # this project's own
                                                                     # original core-horizon
                                                                     # convention, for the
                                                                     # rare cell with no match
print(f"holding_days distribution:\n{cells['holding_days'].describe()}")

ev_full = pd.read_parquet(DATA / "decile_events_ff_adjusted_extended.parquet")
ev_full["day0_date"] = pd.to_datetime(ev_full["day0_date"])
cells["day0_date"] = pd.to_datetime(cells["day0_date"])
ev = ev_full.merge(cells[["permno", "day0_date", "holding_cell", "holding_days"]],
                    on=["permno", "day0_date"], how="inner")
print(f"events with an assigned per-cell holding period: {len(ev):,}")

print("loading daily panel for exact trading-day exit dates...")
frames = []
for y in range(1995, 2015):
    frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date"]))
eq = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["permno", "date"])
eq = eq.sort_values(["permno", "date"]).reset_index(drop=True)
eq["row_idx"] = np.arange(len(eq))
eq_permno = eq["permno"].to_numpy()
eq_date = eq["date"].to_numpy()
n_rows = len(eq)

day0_lookup = eq[["permno", "date", "row_idx"]].rename(columns={"date": "day0_date"})
ev = ev.merge(day0_lookup, on=["permno", "day0_date"], how="left")
ev["row_idx"] = ev["row_idx"].astype("Int64")

target_idx = ev["row_idx"] + ev["holding_days"]
in_bounds = ev["row_idx"].notna() & (target_idx < n_rows)
target_idx_i = target_idx.where(in_bounds).astype("Int64")

same_permno = pd.Series(False, index=ev.index)
valid_pos = in_bounds.to_numpy()
ti = target_idx_i[valid_pos].astype(int).to_numpy()
same_permno_vals = eq_permno[ti] == ev.loc[valid_pos, "permno"].to_numpy()
same_permno.loc[valid_pos] = same_permno_vals

ev["exit_row_idx"] = pd.NA
ev.loc[same_permno, "exit_row_idx"] = target_idx_i[same_permno]
ev["exit_row_idx"] = ev["exit_row_idx"].astype("Int64")
ev["exit_date"] = pd.NaT
ok = ev["exit_row_idx"].notna()
ev.loc[ok, "exit_date"] = eq_date[ev.loc[ok, "exit_row_idx"].astype(int).to_numpy()]

print(f"events with a resolvable entry+exit trading day: {ev['exit_date'].notna().sum():,} / {len(ev):,}")
tradeable = ev[ev["exit_date"].notna()].copy()

KEEP_COLS = ["permno", "day0_date", "exit_date", "row_idx", "exit_row_idx", "decile",
             "sue_rank_pct", "holding_days", "holding_cell", "size_quintile", "ff12_sector",
             "avg_dollar_volume_21d", "liquidity_quintile", "ann_quarter"]
KEEP_COLS = [c for c in KEEP_COLS if c in tradeable.columns]
tradeable = tradeable[KEEP_COLS].rename(columns={"row_idx": "entry_row_idx"})
tradeable["event_id"] = tradeable["permno"].astype(str) + "_" + tradeable["day0_date"].dt.strftime("%Y%m%d")

# ---------- Strategy 1: extreme decile long/short ----------
s1 = tradeable[tradeable["decile"].isin([1, 10])].copy()
s1["weight"] = np.where(s1["decile"] == 10, 1.0, -1.0)
s1.to_parquet(DATA / "positions_extreme_v2.parquet", index=False)
print(f"Strategy 1 (extreme L/S, per-cell holding): {len(s1):,} positions")

# ---------- Strategy 2: rank-weighted ----------
s2 = tradeable.copy()
s2["weight"] = 2 * (s2["sue_rank_pct"] - 0.5)
s2.to_parquet(DATA / "positions_rankweighted_v2.parquet", index=False)
print(f"Strategy 2 (rank-weighted, per-cell holding): {len(s2):,} positions")

# ---------- Strategy 3: extreme decile, size-balanced ----------
s3 = tradeable[tradeable["decile"].isin([1, 10])].copy()
s3 = s3.dropna(subset=["size_quintile"])


def size_balance(df, decile_val, sign):
    sub = df[df["decile"] == decile_val].copy()
    counts = sub.groupby(["ann_quarter", "size_quintile"])["event_id"].transform("count")
    quintiles_per_q = sub.groupby("ann_quarter")["size_quintile"].transform("nunique")
    sub["weight"] = sign * (1.0 / quintiles_per_q) / counts
    return sub


s3_long = size_balance(s3, 10, 1.0)
s3_short = size_balance(s3, 1, -1.0)
s3 = pd.concat([s3_long, s3_short], ignore_index=True)
s3["weight"] = s3["weight"] / s3["weight"].abs().mean() * s1["weight"].abs().mean()
s3.to_parquet(DATA / "positions_balanced_v2.parquet", index=False)
print(f"Strategy 3 (size-balanced extreme, per-cell holding): {len(s3):,} positions")
