"""
Like scripts/35_select_entry_contracts.py, but instead of immediately collapsing each event's
day0 option chain down to one near-ATM call and one near-ATM put, keeps EVERY strike (both C and
P) quoted for that event's best-matching expiration. Feeds scripts/48_optimal_contract_selector.py,
which needs the actual full chain to find the mathematically best strike rather than assuming
"near-ATM" is automatically the right choice.

Same underlying OM scan cost as script 35 (streaming opprcd{year}.parquet is what dominates
runtime; keeping the whole chain instead of collapsing to 1 winner is a cheap in-memory filter
change, not a heavier scan) -- expect a similar few-minutes-per-year runtime, resumable the same
way (one file per year, skipped on restart if already written).

Output: data/event_options/full_chain_<year>.parquet (one row per event x contract)
        data/event_options/full_chain_all.parquet (combined)
"""
import sys
import time
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from options_lib import scan_year_for_keys, pick_full_chain_vectorized

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
    year_out_path = OUT_DIR / f"full_chain_{year}.parquet"
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
    # OptionMetrics stores strike_price in 1/1000ths of a dollar (a $50 strike is 50000) --
    # script 35's near-ATM picker never needed to compare strike against an absolute underlying
    # price (it only compares deltas/strikes to each other, and to itself across ties), so this
    # never had to be corrected there. script 48 compares strike directly against a computed
    # terminal underlying price, so getting this wrong there produces a silent, dramatic bug: a
    # ~1000x-inflated strike makes every put look like a guaranteed, riskless, deep-ITM payoff
    # (found and fixed after the first real run of script 48 produced exactly that pathological
    # pattern -- puts dominating every decile including the highest, with near-100% Kelly
    # fractions across the board). Applied to `raw` BEFORE the chain is built from it, not after.
    raw["strike_price"] = raw["strike_price"] / 1000.0

    chain = pick_full_chain_vectorized(raw, MIN_DTE, TARGET_DTE)
    if chain.empty:
        print(f"{year}: {len(ev_y):,} events -> 0 chain rows  [{time.time()-t0:.1f}s]", flush=True)
        continue

    keep_cols = ["secid", "date", "optionid", "cp_flag", "strike_price", "exdate", "dte", "delta",
                 "impl_volatility", "mid", "volume", "open_interest"]
    chain = chain[keep_cols].rename(columns={
        "strike_price": "strike", "impl_volatility": "iv", "mid": "entry_mid",
        "volume": "entry_volume", "open_interest": "entry_oi", "date": "day0_date"})

    year_df = ev_y.rename(columns={"day0_date": "day0_date"})[
        ["secid", "day0_date", "decile", "permno"]].drop_duplicates()
    year_df = year_df.merge(chain, on=["secid", "day0_date"], how="inner")

    if len(year_df):
        year_df.to_parquet(year_out_path, index=False)
        all_rows.append(year_df)
        n_events_matched = year_df.groupby(["secid", "day0_date"]).ngroups
        print(f"{year}: {len(ev_y):,} events -> {n_events_matched:,} with a chain, "
              f"{len(year_df):,} total contracts (avg {len(year_df)/max(n_events_matched,1):.1f} "
              f"strikes/event)  [{time.time()-t0:.1f}s]", flush=True)
    else:
        print(f"{year}: {len(ev_y):,} events -> 0 matched  [{time.time()-t0:.1f}s]", flush=True)

combined = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
combined.to_parquet(OUT_DIR / "full_chain_all.parquet", index=False)
print(f"\nTOTAL: {len(combined):,} contract rows -> {OUT_DIR / 'full_chain_all.parquet'}")
