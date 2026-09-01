"""
For every linked PEAD event (data/event_options/decile_events_secid.parquet), select a near-ATM
call and a near-ATM put quoted on day0_date for that event's secid, from the raw OptionMetrics
opprcd{year}.parquet files (100GB+ across 1996-2013, not tracked in this repo -- read via
D:/OptionMetrics/parquet, streamed in batches and filtered down to only the (secid, date) pairs
we need to keep peak memory low).

Selection rule per event:
  - candidates = all quotes for that secid on day0_date with a valid two-sided market
    (best_bid > 0, best_offer >= best_bid) and a non-null delta
  - DTE (exdate - day0_date) must be >= MIN_DTE (95 calendar days -- enough runway to survive
    the longest forward horizon used later, 60 trading days =~ 84 calendar days, with a buffer
    for holidays/weekends)
  - among those, pick the call with delta closest to +0.50 and the put with delta closest to
    -0.50 (near-the-money by OptionMetrics' own greek, avoiding any need to separately source an
    underlying spot price); ties broken by DTE closest to TARGET_DTE (120 days)

Processes one calendar year of opprcd at a time (streamed, columns projected, filtered
immediately against that year's needed (secid, date) keys) to keep memory bounded on this
machine. Writes one parquet per year plus a final combined file.

Output: data/event_options/entry_contracts_<year>.parquet (one row per event x {call,put} found)
        data/event_options/entry_contracts_all.parquet (combined)
"""
import sys
import time
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from options_lib import scan_year_for_keys, pick_best_contracts_vectorized

MIN_DTE = 95
TARGET_DTE = 120
OUT_DIR = Path("data/event_options")
YEARS = [y for y in range(1996, 2014) if y != 2011]

events = pd.read_parquet(OUT_DIR / "decile_events_secid.parquet",
                          columns=["secid", "day0_date", "decile", "permno", "anndats"])
events["secid"] = events["secid"].astype("int64")
events["year"] = events["day0_date"].dt.year

all_rows = []
for year in YEARS:
    ev_y = events[events["year"] == year]
    if ev_y.empty:
        continue
    year_out_path = OUT_DIR / f"entry_contracts_{year}.parquet"
    if year_out_path.exists():
        print(f"{year}: already written, skipping ({year_out_path})", flush=True)
        all_rows.append(pd.read_parquet(year_out_path))
        continue
    t0 = time.time()
    needed_keys = ev_y.rename(columns={"day0_date": "date"})[["secid", "date"]]
    raw = scan_year_for_keys(year, needed_keys)
    if raw.empty:
        print(f"{year}: no matching rows found ({len(ev_y):,} events) -- skipping")
        continue
    raw["dte"] = (raw["exdate"] - raw["date"]).dt.days
    raw = raw[(raw["best_bid"] > 0) & (raw["best_offer"] >= raw["best_bid"]) &
              raw["delta"].notna()].copy()
    raw["mid"] = (raw["best_bid"] + raw["best_offer"]) / 2.0

    keep_cols = ["secid", "date", "optionid", "strike_price", "exdate", "dte", "delta",
                 "impl_volatility", "mid", "volume", "open_interest"]
    rename_map = {"strike_price": "strike", "impl_volatility": "iv", "mid": "entry_mid",
                  "volume": "entry_volume", "open_interest": "entry_oi"}

    best_call = pick_best_contracts_vectorized(raw, "C", 0.50, MIN_DTE, TARGET_DTE)
    best_put = pick_best_contracts_vectorized(raw, "P", -0.50, MIN_DTE, TARGET_DTE)

    year_df = ev_y.rename(columns={"day0_date": "date"})[["secid", "date"]].drop_duplicates()
    if not best_call.empty:
        bc = best_call[keep_cols].rename(columns=rename_map)
        bc = bc.rename(columns={c: f"call_{c}" for c in bc.columns if c not in ("secid", "date")})
        year_df = year_df.merge(bc, on=["secid", "date"], how="left")
    if not best_put.empty:
        bp = best_put[keep_cols].rename(columns=rename_map)
        bp = bp.rename(columns={c: f"put_{c}" for c in bp.columns if c not in ("secid", "date")})
        year_df = year_df.merge(bp, on=["secid", "date"], how="left")

    has_any = pd.Series(False, index=year_df.index)
    if "call_optionid" in year_df:
        has_any |= year_df["call_optionid"].notna()
    if "put_optionid" in year_df:
        has_any |= year_df["put_optionid"].notna()
    year_df = year_df[has_any].rename(columns={"date": "day0_date"})

    if len(year_df):
        year_df.to_parquet(OUT_DIR / f"entry_contracts_{year}.parquet", index=False)
        all_rows.append(year_df)
        n_call = year_df["call_optionid"].notna().sum() if "call_optionid" in year_df else 0
        n_put = year_df["put_optionid"].notna().sum() if "put_optionid" in year_df else 0
        print(f"{year}: {len(ev_y):,} events -> {len(year_df):,} matched "
              f"(call={n_call:,}, put={n_put:,})  [{time.time()-t0:.1f}s]", flush=True)
    else:
        print(f"{year}: {len(ev_y):,} events -> 0 matched  [{time.time()-t0:.1f}s]", flush=True)

combined = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
combined.to_parquet(OUT_DIR / "entry_contracts_all.parquet", index=False)
print(f"\nTOTAL matched: {len(combined):,} rows -> {OUT_DIR / 'entry_contracts_all.parquet'}")
