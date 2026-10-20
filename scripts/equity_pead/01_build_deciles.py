"""
Build a decile-sorted PEAD event panel from the analyst-sourced (sue_source=='analyst')
IBES surprise measure, following the exact conventions already established and documented
in data/events/FINDINGS.md and data/results/FINDINGS.md:

  - restrict to day0_status == 'ok' (a real day-0 trading session was resolved)
  - restrict to sue_source == 'analyst' (per user decision: cleanest, most literal
    "IBES surprise" story, ~183k events; sue_seasonal kept only as an appendix robustness cut)
  - rank sue_analyst into percentiles against the EXPANDING pool of every prior, fully-completed
    announcement quarter (not the event's own quarter -- see "Point-in-time ranking" below),
    rounding to 8 decimals before ranking so exact ties don't get ordered by floating-point noise
  - assign deciles 1 (most negative surprise) .. 10 (most positive surprise)

Point-in-time ranking (bug fixed): this used to rank each event against
`groupby("ann_quarter")`, i.e. against every announcement in its OWN calendar quarter --
including firms that hadn't reported yet as of that event's own anndats (earnings within a
quarter are staggered over ~6+ weeks, so an early reporter's decile was informed by data that
didn't exist yet on its own trade date). Fixed by ranking each event only against the
cumulative pool of sue_analyst_r values from strictly EARLIER ann_quarters -- fully realized,
zero look-ahead. The first ann_quarter in the sample has no prior pool and is dropped (a
one-quarter warmup, the minimum possible given the fix, and negligible next to the ~18-year
sample).

Output: data/interim/decile_events.parquet
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import EVENTS_DIR, RESULTS_DIR, DATA_DIR

IN_EVENTS = EVENTS_DIR / "equity_events_1996_2013.parquet"
IN_FEATURES = RESULTS_DIR / "event_classification_features.parquet"
OUT_DIR = DATA_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

ev = pd.read_parquet(IN_EVENTS)
print(f"raw events: {len(ev):,}")

ev = ev[ev["day0_status"] == "ok"].copy()
print(f"day0_status == ok: {len(ev):,}")

ev = ev[ev["sue_source"] == "analyst"].copy()
print(f"sue_source == analyst: {len(ev):,}")

# announcement calendar quarter, point-in-time-safe ranking bucket
ev["anndats"] = pd.to_datetime(ev["anndats"])
ev["ann_quarter"] = ev["anndats"].dt.to_period("Q")

# round to 8 decimals before ranking (matches existing pipeline's tie-handling)
ev["sue_analyst_r"] = ev["sue_analyst"].round(8)

# Point-in-time percentile rank: each event is ranked against the EXPANDING pool of every
# strictly earlier ann_quarter's values only, never its own quarter's (see module docstring for
# why ranking within the event's own quarter is a look-ahead). Average-rank tie handling
# (rank of a tied value = midpoint between its first and last position in the sorted pool),
# matching pandas' rank(method="average", pct=True) convention used everywhere else in this repo.
ev = ev.sort_values("ann_quarter").reset_index(drop=True)
unique_quarters_sorted = ev["ann_quarter"].drop_duplicates().sort_values().to_numpy()
sue_by_quarter = {q: ev.loc[ev["ann_quarter"] == q, "sue_analyst_r"].to_numpy()
                   for q in unique_quarters_sorted}

sue_rank_pct = pd.Series(np.nan, index=ev.index)
cum_pool = np.array([], dtype=float)
for q in unique_quarters_sorted:
    mask = (ev["ann_quarter"] == q).to_numpy()
    if len(cum_pool) > 0:
        vals = ev.loc[mask, "sue_analyst_r"].to_numpy()
        sorted_pool = np.sort(cum_pool)
        left = np.searchsorted(sorted_pool, vals, side="left")
        right = np.searchsorted(sorted_pool, vals, side="right")
        sue_rank_pct.loc[mask] = (left + right) / 2.0 / len(sorted_pool)
    cum_pool = np.concatenate([cum_pool, sue_by_quarter[q]])
ev["sue_rank_pct"] = sue_rank_pct

n_before_warmup = len(ev)
ev = ev.dropna(subset=["sue_rank_pct"]).reset_index(drop=True)
print(f"dropped {n_before_warmup - len(ev):,} events in the first ann_quarter "
      f"({unique_quarters_sorted[0]}) -- no prior-quarter pool to rank against (warmup)")

# decile 1..10, most negative -> 1, most positive -> 10
ev["decile"] = np.minimum(np.ceil(ev["sue_rank_pct"] * 10), 10).astype(int)
ev.loc[ev["sue_rank_pct"] <= 0, "decile"] = 1  # guard against pct==0 edge case

# cell sizes sanity check
cell_sizes = ev.groupby("ann_quarter").size()
print(f"quarters used for ranking: {ev['ann_quarter'].nunique()}, "
      f"median events/quarter: {cell_sizes.median():.0f}, min: {cell_sizes.min()}")

print(ev["decile"].value_counts().sort_index())

# merge subsample classification features
feat = pd.read_parquet(IN_FEATURES)
feat["day0_date"] = pd.to_datetime(feat["day0_date"])
ev["day0_date"] = pd.to_datetime(ev["day0_date"])

# feature table is keyed at (permno, day0_date) grain; a handful of rows share that key
# with multiple IBES fiscal-quarter events announced same-day (see n_events_same_permno_day0
# in data/events/FINDINGS.md) -- the classification features themselves are identical for
# those duplicates (same firm, same day), so dedupe on the key before the m:1 merge.
dup_before = len(feat)
feat = feat.drop_duplicates(subset=["permno", "day0_date"])
print(f"feature rows deduped on (permno, day0_date): {dup_before:,} -> {len(feat):,}")

before = len(ev)
ev = ev.merge(feat, on=["permno", "day0_date"], how="left", validate="m:1")
assert len(ev) == before, "merge changed row count -- duplicate (permno, day0_date) in features"
print(f"matched classification features: {ev['ff12_sector'].notna().sum():,} / {len(ev):,}")

out_path = OUT_DIR / "decile_events.parquet"
ev.to_parquet(out_path, index=False)
print(f"wrote {out_path} ({len(ev):,} rows)")
