"""
Extend forward-return measurement past +60 trading days, to find where each decile's
drift actually flattens out (the "decay point") rather than assuming a holding period.

Same construction as the project's existing ret_fwd_*d_mktadj columns: for each event,
the return over h trading days STRICTLY AFTER day 0, in the resolved permno's own trading
history (not a market-wide calendar), market-adjusted against CRSP's value-weighted index
total return (vwretd). Extends to h = 80, 100, 120, 150, 180 trading days (existing data
already covers 1, 5, 10, 20, 40, 60).

Output: data/decile_events_extended.parquet (decile_events.parquet + new ret columns)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, RAW_EQUITY_DIR

RAW = RAW_EQUITY_DIR
DATA = DATA_DIR

HORIZONS = [80, 100, 120, 150, 180]

print("loading daily equity panel (1995-2014)...")
frames = []
for y in range(1995, 2015):
    frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date", "ret"]))
eq = pd.concat(frames, ignore_index=True)
print(f"raw daily rows: {len(eq):,}")

mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
eq = eq.merge(mkt, on="date", how="left")
eq["ret_mktadj"] = eq["ret"] - eq["vwretd"]

# drop rows with no usable return (can't include in a cumulative product)
eq = eq.dropna(subset=["ret_mktadj"]).copy()
eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
print(f"usable daily rows after cleaning: {len(eq):,}")

# global cumulative log return (safe to diff across two rows of the SAME permno, since
# a permno's rows are contiguous after sorting by permno then date -- see script docstring)
eq["cumlog"] = np.log1p(eq["ret_mktadj"]).cumsum()
eq["row_idx"] = np.arange(len(eq))
permno_arr = eq["permno"].to_numpy()
cumlog_arr = eq["cumlog"].to_numpy()
n_rows = len(eq)

# ---------- locate each event's day-0 row ----------
ev = pd.read_parquet(DATA / "decile_events.parquet")
ev["day0_date"] = pd.to_datetime(ev["day0_date"])

day0_lookup = eq[["permno", "date", "row_idx"]].rename(columns={"date": "day0_date"})
ev = ev.merge(day0_lookup, on=["permno", "day0_date"], how="left")
n_found = ev["row_idx"].notna().sum()
print(f"day-0 row located in daily panel: {n_found:,} / {len(ev):,}")

ev["row_idx"] = ev["row_idx"].astype("Int64")

for h in HORIZONS:
    target_idx = ev["row_idx"] + h
    valid = ev["row_idx"].notna() & (target_idx < n_rows)
    target_idx_arr = target_idx.where(valid).astype("Int64")

    ret_col = np.full(len(ev), np.nan)
    valid_mask = valid.to_numpy()
    ti = target_idx_arr[valid_mask].astype(int).to_numpy()
    si = ev["row_idx"][valid_mask].astype(int).to_numpy()

    same_permno = permno_arr[ti] == ev.loc[valid_mask, "permno"].to_numpy()
    cumret = np.full(len(ti), np.nan)
    cumret[same_permno] = np.exp(cumlog_arr[ti[same_permno]] - cumlog_arr[si[same_permno]]) - 1.0

    out = np.full(len(ev), np.nan)
    out[valid_mask] = cumret
    ev[f"ret_fwd_{h}d_mktadj"] = out
    n_ok = np.isfinite(out).sum()
    print(f"  +{h}d: {n_ok:,} events ({n_ok/len(ev):.1%})")

ev = ev.drop(columns=["row_idx"])
out_path = DATA / "decile_events_extended.parquet"
ev.to_parquet(out_path, index=False)
print(f"wrote {out_path}")
