"""
For every event with a selected entry call and/or put (data/event_options/entry_contracts_all.parquet),
look up that SAME contract's (optionid) mid-price at day0+{1,5,10,20,40,60} trading days, using the
OptionMetrics trading calendar built in script 33. This is a buy-and-hold, single-contract lookup
(no rolling/re-selection), directly analogous to the equity panel's ret_fwd_{1,5,10,20,40,60}d.

A contract may not have a valid two-sided quote at a given forward date (thin trading, or the
event's day0 was chosen close enough to expiry -- guarded against via MIN_DTE in script 35, but
early-sample years have sparser quoting) -- those horizons come back as NaN and are simply
excluded from that horizon's decile mean later, same convention as missing equity forward returns.

Output: data/event_options/forward_prices_<year>.parquet (one per year touched)
        data/event_options/option_event_panel.parquet -- final wide panel: one row per event,
        entry + forward mid prices/returns for call and put, decile, day0_date, ff12_sector etc.
"""
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, METADATA_DIR
from lib.options import scan_year_for_keys

HORIZONS = [1, 5, 10, 20, 40, 60]
OUT_DIR = DATA_DIR / "event_options"

cal = pd.read_parquet(METADATA_DIR / "om_trading_calendar.parquet")
cal = cal.sort_values("date").reset_index(drop=True)
cal_dates = cal["date"].values
cal_index = {d: i for i, d in enumerate(cal_dates)}

# the calendar has one real hole: all of 2011 is missing (no opprcd2011.parquet). A naive
# "k trading days later" lookup would silently step OVER that hole for events near the end of
# 2010, landing in 2012 and mislabeling a >1-year gap as a 60-trading-day horizon. Detect any
# gap wider than 10 real calendar days between consecutive calendar entries within [i, j] and
# refuse to compute a forward date across it.
gap_days = pd.Series(cal_dates).diff().dt.days
LARGE_GAP = 10
gap_after = (gap_days > LARGE_GAP).values  # gap_after[i] True means a hole between cal[i-1], cal[i]


def forward_date(d, k):
    """k trading days after d, using the OM calendar; nearest calendar date >= d if d itself
    isn't an exact match (day0_date should usually already be a real trading day). Returns NaT
    if the calendar has no more data, or if [d, d+k] would cross the 2011 coverage hole."""
    i = cal_index.get(d)
    if i is None:
        pos = np.searchsorted(cal_dates, d)
        if pos >= len(cal_dates):
            return pd.NaT
        i = pos
    j = i + k
    if j >= len(cal_dates):
        return pd.NaT
    if gap_after[i + 1:j + 1].any():
        return pd.NaT
    return cal_dates[j]


entries = pd.read_parquet(OUT_DIR / "entry_contracts_all.parquet")
entries = entries.reset_index(drop=True)
entries["event_id"] = entries.index
print(f"entries loaded: {len(entries):,} rows")

# long-format table of every (event, cp_type, horizon) lookup needed
need_rows = []
for cp in ["call", "put"]:
    oid_col = f"{cp}_optionid"
    if oid_col not in entries.columns:
        continue
    sub = entries[entries[oid_col].notna()][["event_id", "secid", "day0_date", oid_col]].copy()
    sub = sub.rename(columns={oid_col: "optionid"})
    for h in HORIZONS:
        sub_h = sub.copy()
        sub_h["horizon"] = h
        sub_h["target_date"] = sub_h["day0_date"].map(lambda d: forward_date(d, h))
        sub_h["cp_type"] = cp
        need_rows.append(sub_h)

need = pd.concat(need_rows, ignore_index=True)
need = need.dropna(subset=["target_date"])
need["secid"] = need["secid"].astype("int64")
need["target_year"] = need["target_date"].dt.year
print(f"total (event, cp_type, horizon) lookups needed: {len(need):,}, "
      f"spanning years {sorted(need['target_year'].unique())}")

all_fwd = []
for year, grp in need.groupby("target_year"):
    t0 = time.time()
    keys = grp.rename(columns={"target_date": "date"})[["secid", "date"]].drop_duplicates()
    raw = scan_year_for_keys(int(year), keys)
    if raw.empty:
        print(f"{year}: 0 rows found for {len(grp):,} lookups")
        continue
    raw["mid"] = np.where((raw["best_bid"] > 0) & (raw["best_offer"] >= raw["best_bid"]),
                           (raw["best_bid"] + raw["best_offer"]) / 2.0, np.nan)
    raw = raw.rename(columns={"date": "target_date"})[
        ["secid", "target_date", "optionid", "mid", "best_bid", "best_offer",
         "volume", "open_interest"]]
    matched = grp.merge(raw, on=["secid", "target_date", "optionid"], how="left")
    matched.to_parquet(OUT_DIR / f"forward_prices_{year}.parquet", index=False)
    all_fwd.append(matched)
    hit_rate = matched["mid"].notna().mean()
    print(f"{year}: {len(grp):,} lookups -> {matched['mid'].notna().sum():,} priced "
          f"({hit_rate:.1%})  [{time.time()-t0:.1f}s]")

fwd = pd.concat(all_fwd, ignore_index=True)
fwd_wide = fwd.pivot_table(index=["event_id", "cp_type"], columns="horizon",
                            values="mid", aggfunc="first")
fwd_wide.columns = [f"fwd_mid_{c}d" for c in fwd_wide.columns]
fwd_wide = fwd_wide.reset_index()

fwd_wide_c = fwd_wide[fwd_wide["cp_type"] == "call"].drop(columns=["cp_type"])
fwd_wide_c = fwd_wide_c.rename(columns={c: f"call_{c}" for c in fwd_wide_c.columns if c != "event_id"})
fwd_wide_p = fwd_wide[fwd_wide["cp_type"] == "put"].drop(columns=["cp_type"])
fwd_wide_p = fwd_wide_p.rename(columns={c: f"put_{c}" for c in fwd_wide_p.columns if c != "event_id"})

panel = entries.merge(fwd_wide_c, on="event_id", how="left").merge(fwd_wide_p, on="event_id", how="left")

events_meta = pd.read_parquet(OUT_DIR / "decile_events_secid.parquet",
                               columns=["secid", "day0_date", "decile", "permno", "anndats",
                                        "ann_quarter", "ff12_sector", "size_quintile",
                                        "sue_analyst"])
events_meta["secid"] = events_meta["secid"].astype("int64")
panel = panel.merge(events_meta, on=["secid", "day0_date"], how="left")

for cp in ["call", "put"]:
    entry_col = f"{cp}_entry_mid"
    if entry_col not in panel.columns:
        continue
    for h in HORIZONS:
        fwd_col = f"{cp}_fwd_mid_{h}d"
        if fwd_col in panel.columns:
            panel[f"{cp}_ret_fwd_{h}d"] = panel[fwd_col] / panel[entry_col] - 1.0

# straddle (call+put) return: a directionless, pure-volatility/theta exposure -- computed for
# free from the same call+put mid prices already fetched above, for events that matched both a
# call and a put. A decile pattern that's flat here (unlike the call/put patterns) would confirm
# the directional PEAD signal isn't actually a volatility-crush artifact correlated with |SUE|.
if "call_entry_mid" in panel.columns and "put_entry_mid" in panel.columns:
    both = panel["call_entry_mid"].notna() & panel["put_entry_mid"].notna()
    panel["straddle_entry_mid"] = panel["call_entry_mid"] + panel["put_entry_mid"]
    for h in HORIZONS:
        cfwd, pfwd = f"call_fwd_mid_{h}d", f"put_fwd_mid_{h}d"
        if cfwd in panel.columns and pfwd in panel.columns:
            fwd_sum = panel[cfwd] + panel[pfwd]
            panel[f"straddle_ret_fwd_{h}d"] = np.where(
                both & panel[cfwd].notna() & panel[pfwd].notna(),
                fwd_sum / panel["straddle_entry_mid"] - 1.0, np.nan)

out_path = OUT_DIR / "option_event_panel.parquet"
panel.to_parquet(out_path, index=False)
print(f"\nwrote {out_path} ({len(panel):,} rows, {panel['decile'].notna().sum():,} with decile)")
