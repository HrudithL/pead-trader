"""
Build a decile-sorted PEAD event panel from the analyst-sourced (sue_source=='analyst')
IBES surprise measure, following the exact conventions already established and documented
in data/events/FINDINGS.md and data/results/FINDINGS.md:

  - restrict to day0_status == 'ok' (a real day-0 trading session was resolved)
  - restrict to sue_source == 'analyst' (per user decision: cleanest, most literal
    "IBES surprise" story, ~183k events; sue_seasonal kept only as an appendix robustness cut)
  - rank sue_analyst into percentiles against the EXPANDING pool of strictly earlier announcement
    quarters (pead.common.pit_rank; the old within-own-quarter rank let early reporters be ranked
    against firms that had not reported yet). The first quarter has no pool and is dropped.
    Rounding to 8 decimals before ranking so exact ties don't get ordered by floating-point noise.
    PEAD_DECILE_RANKING=within_quarter rebuilds the old (look-ahead) deciles.
  - assign deciles 1 (most negative surprise) .. 10 (most positive surprise)

Output: data/interim/decile_events.parquet
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pead.common.paths import EVENTS_DIR, RESULTS_DIR, DATA_DIR
from pead.common.pit_rank import rank_pct, ranking_mode

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

ev["sue_rank_pct"] = rank_pct(ev["sue_analyst_r"], ev["ann_quarter"])
n_pre = len(ev)
ev = ev.dropna(subset=["sue_rank_pct"]).reset_index(drop=True)      # warmup quarter: nothing earlier to rank against
print(f"ranking mode {ranking_mode()}: dropped {n_pre - len(ev):,} warmup events")

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
