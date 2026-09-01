"""
Daily-precision decay curve: compute the size+book-to-market-adjusted cumulative return at
EVERY trading day from 1 to 200 (not the sparse 1/5/10/20/40/60/80/100/120/150/180 checkpoints
used before), so the exact day where an additional day of holding stops paying can be read off
directly instead of interpolated between coarse checkpoints.

Also builds the holding-period CELL each event is assigned to: instead of one holding period
per decile (10 groups), events are cross-cut by decile x firm-size quintile x book-to-market
tercile (up to 10*5*3 = 150 cells). Each event's cell is a composite index built by combining
its three feature values with fixed place-value weights, computed as one vectorized operation
across all events at once (the same idea as a matrix-vector product: each feature is a column,
each column gets a weight, sum the weighted columns to get one composite index, rather than
looping row by row).

Not every one of the 150 possible cells has enough data to trust on its own -- with ~175,000
events spread across 150 cells, many would average close to 1,000 events but the ones in
thin corners (a rare sector-adjacent size/value combination, or a decile that ties heavily at
zero surprise) could have very few, and few is what actually breaks Fama-MacBeth inference
(too few ANNOUNCEMENT QUARTERS represented, not too few raw events). So a hierarchical backoff
is applied: try the full (decile, size, BM) cell; if it doesn't have enough distinct quarters
or enough events, drop the book-to-market split and use (decile, size); if that's still too
thin, drop size too and fall back to decile alone. This keeps every event assigned to the
finest cell the data can actually support, rather than either forcing a uniform grid that's
too noisy in the thin corners, or forcing everything back to only 10 decile-level buckets.

Output: data/event_cells.parquet (event -> assigned cell + cell metadata)
        data/cell_daily_curve.csv (cell x day -> cumulative & marginal adjusted return)
        data/cell_decay_days.csv (cell -> exact decay day)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, RAW_EQUITY_DIR

DATA = DATA_DIR
RAW = RAW_EQUITY_DIR

MAX_DAYS = 200
MIN_QUARTERS = 20      # a holding-period cell needs at least this many announcement quarters...
MIN_EVENTS = 400       # ...and at least this many events, or it backs off to a coarser cell.

# ---------- rebuild the daily market-adjusted panel + cumulative log return ----------
print("loading daily panel...")
frames = []
for y in range(1995, 2015):
    frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date", "ret"]))
eq = pd.concat(frames, ignore_index=True)
mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
eq = eq.merge(mkt, on="date", how="left")
eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
eq["cumlog"] = np.log1p(eq["ret_mktadj"]).cumsum()
eq["row_idx"] = np.arange(len(eq))
eq_permno = eq["permno"].to_numpy()
cumlog_arr = eq["cumlog"].to_numpy()
n_rows = len(eq)

# ---------- event set: reuse the already-built characteristic panel (decile, size, BM, quarter) ----------
ev = pd.read_parquet(DATA / "decile_events_ff_adjusted.parquet")
ev["day0_date"] = pd.to_datetime(ev["day0_date"])
day0_lookup = eq[["permno", "date", "row_idx"]].rename(columns={"date": "day0_date"})
ev = ev.merge(day0_lookup, on=["permno", "day0_date"], how="left")
ev = ev.dropna(subset=["row_idx", "size_quintile", "bm_tercile"]).copy()
ev["row_idx"] = ev["row_idx"].astype(int)
ev["size_quintile"] = ev["size_quintile"].astype(int)
ev["bm_tercile"] = ev["bm_tercile"].astype(int)
ev["decile"] = ev["decile"].astype(int)
print(f"events usable for daily curve: {len(ev):,}")

# ---------- build the full (event x day) cumulative market-adjusted return matrix ----------
entry_idx = ev["row_idx"].to_numpy()
offsets = np.arange(1, MAX_DAYS + 1)
target = entry_idx[:, None] + offsets[None, :]                     # (n_events, MAX_DAYS)
in_bounds = target < n_rows
target_c = np.clip(target, 0, n_rows - 1)
same_permno = eq_permno[target_c] == ev["permno"].to_numpy()[:, None]
valid = in_bounds & same_permno

cum_ret = np.exp(cumlog_arr[target_c] - cumlog_arr[entry_idx][:, None]) - 1.0
cum_ret = np.where(valid, cum_ret, np.nan)
print(f"cumulative return matrix built: {cum_ret.shape}, "
      f"{np.isnan(cum_ret[:, -1]).mean():.1%} events missing at day {MAX_DAYS}")

# ---------- ADJUSTMENT cell (risk benchmark): (quarter, size_quintile, bm_tercile), same as
# before but now applied at every one of the 200 days, not just 5 extra checkpoints ----------
adj_group_cols = ["ann_quarter", "size_quintile", "bm_tercile"]
adj_codes, _ = pd.factorize(ev[adj_group_cols].astype(str).agg("|".join, axis=1))
n_adj_groups = adj_codes.max() + 1
print(f"risk-benchmark cells: {n_adj_groups:,}")

adj_cum_ret = np.empty_like(cum_ret)
valid_mask = ~np.isnan(cum_ret)
filled = np.nan_to_num(cum_ret, nan=0.0)
for day in range(MAX_DAYS):
    col = filled[:, day]
    vmask = valid_mask[:, day]
    sums = np.bincount(adj_codes[vmask], weights=col[vmask], minlength=n_adj_groups)
    counts = np.bincount(adj_codes[vmask], minlength=n_adj_groups)
    group_mean_excl = np.zeros(n_adj_groups)
    with np.errstate(divide="ignore", invalid="ignore"):
        # leave-one-out mean: (group_sum - own_value) / (group_n - 1)
        own_sum_contrib = sums[adj_codes]
        own_count_contrib = counts[adj_codes]
        loo_mean = np.where(own_count_contrib > 1,
                             (own_sum_contrib - col) / (own_count_contrib - 1), np.nan)
    adj_cum_ret[:, day] = cum_ret[:, day] - loo_mean
print("characteristic adjustment applied across all 200 days")

# ---------- HOLDING-PERIOD cell: decile x size x BM, with hierarchical backoff ----------
fine_key = ev["decile"].astype(str) + "_" + ev["size_quintile"].astype(str) + "_" + ev["bm_tercile"].astype(str)
fine_codes, fine_labels = pd.factorize(fine_key)

fine_df = pd.DataFrame({"code": fine_codes, "ann_quarter": ev["ann_quarter"].to_numpy()})
fine_stats = fine_df.groupby("code").agg(n_events=("code", "size"), n_quarters=("ann_quarter", "nunique"))
fine_ok = fine_stats.index[(fine_stats["n_quarters"] >= MIN_QUARTERS) & (fine_stats["n_events"] >= MIN_EVENTS)]

medium_key = ev["decile"].astype(str) + "_" + ev["size_quintile"].astype(str)
medium_codes, medium_labels = pd.factorize(medium_key)
medium_df = pd.DataFrame({"code": medium_codes, "ann_quarter": ev["ann_quarter"].to_numpy()})
medium_stats = medium_df.groupby("code").agg(n_events=("code", "size"), n_quarters=("ann_quarter", "nunique"))
medium_ok = medium_stats.index[(medium_stats["n_quarters"] >= MIN_QUARTERS) & (medium_stats["n_events"] >= MIN_EVENTS)]

coarse_key = ev["decile"].astype(str)
coarse_codes, coarse_labels = pd.factorize(coarse_key)

use_fine = np.isin(fine_codes, fine_ok)
use_medium = (~use_fine) & np.isin(medium_codes, medium_ok)
use_coarse = ~use_fine & ~use_medium

final_cell = np.empty(len(ev), dtype=object)
final_cell[use_fine] = "fine:" + fine_labels[fine_codes[use_fine]]
final_cell[use_medium] = "med:" + medium_labels[medium_codes[use_medium]]
final_cell[use_coarse] = "coarse:" + coarse_labels[coarse_codes[use_coarse]]

ev["holding_cell"] = final_cell
print(f"cell resolution: fine={use_fine.sum():,} ({use_fine.mean():.1%}), "
      f"medium={use_medium.sum():,} ({use_medium.mean():.1%}), "
      f"coarse={use_coarse.sum():,} ({use_coarse.mean():.1%})")
print(f"total distinct holding-period cells in use: {ev['holding_cell'].nunique()}")

ev[["permno", "day0_date", "decile", "size_quintile", "bm_tercile", "ann_quarter",
    "holding_cell"]].to_parquet(DATA / "event_cells.parquet", index=False)

# ---------- Fama-MacBeth cumulative & marginal return, per holding_cell, per day ----------
cell_codes, cell_labels = pd.factorize(ev["holding_cell"])
quarter_codes, quarter_labels = pd.factorize(ev["ann_quarter"].astype(str))
composite = cell_codes.astype(np.int64) * (quarter_codes.max() + 1) + quarter_codes
n_composite = composite.max() + 1

daily_rows = []
prev_cell_mean = None
cum_by_cellday = np.full((len(cell_labels), MAX_DAYS), np.nan)

for day in range(MAX_DAYS):
    col = adj_cum_ret[:, day]
    vmask = ~np.isnan(col)
    sums = np.bincount(composite[vmask], weights=col[vmask], minlength=n_composite)
    counts = np.bincount(composite[vmask], minlength=n_composite)
    with np.errstate(divide="ignore", invalid="ignore"):
        qmeans = np.where(counts > 0, sums / counts, np.nan)
    qmeans = qmeans.reshape(len(cell_labels), quarter_codes.max() + 1)
    cell_mean = np.nanmean(qmeans, axis=1)
    cell_n_quarters = np.sum(~np.isnan(qmeans), axis=1)
    cell_sd = np.nanstd(qmeans, axis=1, ddof=1)
    cum_by_cellday[:, day] = cell_mean

for ci, cell_name in enumerate(cell_labels):
    prev_mean = 0.0
    for day in range(MAX_DAYS):
        cur_mean = cum_by_cellday[ci, day]
        marginal = cur_mean - prev_mean if not np.isnan(cur_mean) and not np.isnan(prev_mean) else np.nan
        daily_rows.append(dict(cell=cell_name, day=day + 1, cumulative_mean=cur_mean, marginal_return=marginal))
        if not np.isnan(cur_mean):
            prev_mean = cur_mean

daily_df = pd.DataFrame(daily_rows)
daily_df.to_csv(DATA / "cell_daily_curve.csv", index=False)
print(f"wrote cell_daily_curve.csv ({len(daily_df):,} rows)")

# ---------- exact decay day per cell ----------
decay_rows = []
for cell_name, sub in daily_df.groupby("cell"):
    sub = sub.sort_values("day")
    is_extreme_high = "_10_" in cell_name or cell_name.endswith("_10") or cell_name.startswith("fine:10") \
        or cell_name.startswith("med:10") or cell_name == "coarse:10" \
        or cell_name.split(":")[1].split("_")[0] in ("6", "7", "8", "9", "10")
    decile_part = cell_name.split(":")[1].split("_")[0]
    positive_side = int(decile_part) >= 6
    # smooth with a trailing 5-day rolling average of the marginal return to avoid a single
    # noisy day triggering a false decay signal, then find the first day (after day 15, to
    # skip the initial reaction window) where that smoothed marginal turns flat/against-side
    smoothed = sub["marginal_return"].rolling(5, min_periods=3).mean()
    decay_day = MAX_DAYS
    for day, m in zip(sub["day"], smoothed):
        if day <= 15:
            continue
        if positive_side and m is not None and not np.isnan(m) and m < 0.00002:
            decay_day = day
            break
        if not positive_side and m is not None and not np.isnan(m) and m > -0.00002:
            decay_day = day
            break
    decay_rows.append(dict(cell=cell_name, decile=int(decile_part), decay_day=decay_day,
                            n_events=(ev["holding_cell"] == cell_name).sum()))

decay_df = pd.DataFrame(decay_rows).sort_values(["decile", "cell"])
decay_df.to_csv(DATA / "cell_decay_days.csv", index=False)
print(decay_df.to_string(index=False))
