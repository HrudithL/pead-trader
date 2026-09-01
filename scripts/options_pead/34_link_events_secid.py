"""
Rebuild the decile-sorted PEAD event panel (same construction as the original
scripts/equity_pead/01_build_deciles.py, adapted from cloud-sandbox paths to this local repo), then link
each event's CRSP permno to an OptionMetrics secid via data/metadata/resolved_universe_ids.parquet
(already built by an earlier session -- 98.3% match rate against the analyst+ok event set).

Restricts to events whose day0_date falls within the OptionMetrics option-price coverage window
(1996-01-02 .. 2013-08-30, excluding all of 2011 -- see data/metadata/om_trading_calendar.parquet
and the missing opprcd2011.parquet) since those are the only events we can possibly find option
data for.

Output: data/event_options/decile_events_secid.parquet
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import EVENTS_DIR, RESULTS_DIR, METADATA_DIR, DATA_DIR

IN_EVENTS = EVENTS_DIR / "equity_events_1996_2013.parquet"
IN_FEATURES = RESULTS_DIR / "event_classification_features.parquet"
IN_UNIVERSE = METADATA_DIR / "resolved_universe_ids.parquet"
IN_CALENDAR = METADATA_DIR / "om_trading_calendar.parquet"
OUT = DATA_DIR / "event_options" / "decile_events_secid.parquet"

ev = pd.read_parquet(IN_EVENTS)
print(f"raw events: {len(ev):,}")

ev = ev[ev["day0_status"] == "ok"].copy()
print(f"day0_status == ok: {len(ev):,}")

ev = ev[ev["sue_source"] == "analyst"].copy()
print(f"sue_source == analyst: {len(ev):,}")

ev["anndats"] = pd.to_datetime(ev["anndats"])
ev["day0_date"] = pd.to_datetime(ev["day0_date"])
ev["ann_quarter"] = ev["anndats"].dt.to_period("Q")

ev["sue_analyst_r"] = ev["sue_analyst"].round(8)
ev["sue_rank_pct"] = ev.groupby("ann_quarter")["sue_analyst_r"].rank(pct=True, method="average")
ev["decile"] = np.minimum(np.ceil(ev["sue_rank_pct"] * 10), 10).astype(int)
ev.loc[ev["sue_rank_pct"] <= 0, "decile"] = 1

print(ev["decile"].value_counts().sort_index())

feat = pd.read_parquet(IN_FEATURES)
feat["day0_date"] = pd.to_datetime(feat["day0_date"])
dup_before = len(feat)
feat = feat.drop_duplicates(subset=["permno", "day0_date"])
print(f"feature rows deduped: {dup_before:,} -> {len(feat):,}")

before = len(ev)
ev = ev.merge(feat, on=["permno", "day0_date"], how="left", validate="m:1")
assert len(ev) == before, "merge changed row count"
print(f"matched classification features: {ev['ff12_sector'].notna().sum():,} / {len(ev):,}")

# --- link to OptionMetrics secid ---
ru = pd.read_parquet(IN_UNIVERSE)
ru = ru.dropna(subset=["permno"]).copy()
ru["permno"] = ru["permno"].astype(str)
# a small number of secids map ambiguously to more than one permno (or vice versa) -- keep only
# unambiguous links for a clean 1-event-1-underlying panel
ru_clean = ru[~ru["permno_ambiguous"]].drop_duplicates(subset=["permno"], keep=False)
print(f"universe rows: {len(ru):,}, unambiguous+unique permno rows: {len(ru_clean):,}")

ev["permno_str"] = ev["permno"].astype("Int64").astype(str)
ev = ev.merge(ru_clean[["secid", "permno"]], left_on="permno_str", right_on="permno",
              how="left", suffixes=("", "_ru"))
ev["secid"] = ev["secid"].astype("Int64")
print(f"matched to secid: {ev['secid'].notna().sum():,} / {len(ev):,} "
      f"({ev['secid'].notna().mean():.1%})")

# --- restrict to OptionMetrics coverage window ---
cal = pd.read_parquet(IN_CALENDAR)
cal_min, cal_max = cal["date"].min(), cal["date"].max()
gap_start = pd.Timestamp("2010-12-31")  # last calendar date before the 2011 hole (from gap scan)
gap_end = pd.Timestamp("2012-01-01")
print(f"\nOM coverage window: {cal_min.date()} .. {cal_max.date()}, with a gap "
      f"{gap_start.date()}..{gap_end.date()} (missing opprcd2011)")

in_window = ev["day0_date"].between(cal_min, cal_max)
in_gap = ev["day0_date"].between(gap_start, gap_end)
ev["om_coverage_eligible"] = in_window & ~in_gap
print(f"events with day0_date inside OM coverage window (excl. 2011 gap): "
      f"{ev['om_coverage_eligible'].sum():,} / {len(ev):,}")

final = ev[ev["om_coverage_eligible"] & ev["secid"].notna()].copy()
print(f"\nfinal linkable panel (has secid AND inside coverage window): {len(final):,} events, "
      f"{final['secid'].nunique():,} unique secids")
print(final["decile"].value_counts().sort_index())

OUT.parent.mkdir(parents=True, exist_ok=True)
ev.to_parquet(OUT.parent / "decile_events_secid_all.parquet", index=False)
final.to_parquet(OUT, index=False)
print(f"\nwrote {OUT} ({len(final):,} rows)")
print(f"wrote {OUT.parent / 'decile_events_secid_all.parquet'} ({len(ev):,} rows, incl. unlinked)")
