"""
Build the trade list (positions) for all three strategies. A "position" here is one
row per (event, entry date, exit date, weight): the unit of what gets held, always a
single ticker's single earnings event, never a whole decile.

Holding period rule (shared across all 3 strategies, derived empirically in
09_decay_analysis.py / 10_extended_ff_decay.py from the size+BM-adjusted decay curve,
not assumed): deciles are bucketed into "extreme" (1,2,9,10 -> 80 trading days),
"shoulder" (3,4,7,8 -> 40 trading days), and "near-median, weak signal" (5,6 -> 20
trading days, short holds since there's little measured edge to wait for).

Strategy 1 -- extreme decile long/short: long D10, short D1, weight = +-1, nothing else traded.
Strategy 2 -- rank-weighted: every event gets weight = 2*(sue_rank_pct - 0.5), continuous
  from -1 (worst surprise that quarter) to +1 (best), ~0 near the median. Holding period
  per event uses its own decile's bucket above.
Strategy 3 -- extreme decile, size-balanced: same D10/D1 selection as Strategy 1, but
  each leg's weight is redistributed across size quintiles within each announcement
  quarter so every size quintile contributes an equal total weight to that leg -- this
  keeps the long and short legs matched on firm size, so the strategy isn't secretly a
  small-cap-short/large-cap-long bet riding on top of the surprise signal (see Section 5b
  of the report: size alone drives a 4x difference in drift magnitude).

Output: data/positions_extreme.parquet, data/positions_rankweighted.parquet,
        data/positions_balanced.parquet
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, RAW_EQUITY_DIR
from lib.positions import size_balance

DATA = DATA_DIR
RAW = RAW_EQUITY_DIR

HOLD_BUCKET = {1: 80, 2: 80, 3: 40, 4: 40, 5: 20, 6: 20, 7: 40, 8: 40, 9: 80, 10: 80}

ev = pd.read_parquet(DATA / "decile_events_ff_adjusted_extended.parquet")
ev = ev[ev["day0_status"] == "ok"].copy() if "day0_status" in ev.columns else ev
ev["holding_days"] = ev["decile"].map(HOLD_BUCKET)

# ---------- locate each event's day-0 row index in the daily panel (for exact exit dates) ----------
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

n_tradeable = ev["exit_date"].notna().sum()
print(f"events with a resolvable entry+exit trading day: {n_tradeable:,} / {len(ev):,}")

tradeable = ev[ev["exit_date"].notna()].copy()

KEEP_COLS = ["event_id" if "event_id" in tradeable.columns else "permno", "permno", "ibes_ticker",
             "day0_date", "exit_date", "row_idx", "exit_row_idx", "decile", "sue_rank_pct",
             "holding_days", "size_quintile", "ff12_sector", "avg_dollar_volume_21d",
             "liquidity_quintile", "ann_quarter"]
KEEP_COLS = [c for c in dict.fromkeys(KEEP_COLS) if c in tradeable.columns]
tradeable = tradeable[KEEP_COLS].rename(columns={"row_idx": "entry_row_idx"})
tradeable["event_id"] = tradeable["permno"].astype(str) + "_" + tradeable["day0_date"].dt.strftime("%Y%m%d")

# ---------- Strategy 1: extreme decile long/short ----------
s1 = tradeable[tradeable["decile"].isin([1, 10])].copy()
s1["weight"] = np.where(s1["decile"] == 10, 1.0, -1.0)
s1.to_parquet(DATA / "positions_extreme.parquet", index=False)
print(f"Strategy 1 (extreme L/S): {len(s1):,} positions "
      f"({(s1['decile']==10).sum():,} long, {(s1['decile']==1).sum():,} short)")

# ---------- Strategy 2: rank-weighted ----------
s2 = tradeable.copy()
s2["weight"] = 2 * (s2["sue_rank_pct"] - 0.5)
s2.to_parquet(DATA / "positions_rankweighted.parquet", index=False)
print(f"Strategy 2 (rank-weighted): {len(s2):,} positions, "
      f"mean |weight| = {s2['weight'].abs().mean():.3f}")

# ---------- Strategy 3: extreme decile, size-balanced ----------
s3 = tradeable[tradeable["decile"].isin([1, 10])].copy()
s3 = s3.dropna(subset=["size_quintile"])

s3_long = size_balance(s3, 10, 1.0)
s3_short = size_balance(s3, 1, -1.0)
s3 = pd.concat([s3_long, s3_short], ignore_index=True)
# rescale so average |weight| matches Strategy 1's (both are +-1 nominal per leg per quarter,
# but here it's spread across quintile sub-buckets) -- normalize to comparable notional scale
s3["weight"] = s3["weight"] / s3["weight"].abs().mean() * s1["weight"].abs().mean()
s3.to_parquet(DATA / "positions_balanced.parquet", index=False)
print(f"Strategy 3 (size-balanced extreme): {len(s3):,} positions")
