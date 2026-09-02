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

Years are independent (see script 35's docstring for the same note) -- --jobs > 1 scans several
years at once in separate worker processes; lib.hw picks a default that doesn't assume the OM
drive can serve many concurrent readers, override with --jobs on faster/striped storage.

Output: data/event_options/full_chain_<year>.parquet (one row per event x contract)
        data/event_options/full_chain_all.parquet (combined)
"""
import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR
from lib.options import scan_year_for_keys, pick_full_chain_vectorized
from lib.hw import add_jobs_arg, add_force_arg

MIN_DTE = 95
TARGET_DTE = 120
OUT_DIR = DATA_DIR / "event_options"
YEARS = [y for y in range(1996, 2014) if y != 2011]


def _process_year(year, ev_y, force=False):
    year_out_path = OUT_DIR / f"full_chain_{year}.parquet"
    if year_out_path.exists() and not force:
        return year, pd.read_parquet(year_out_path), \
            f"{year}: already written, skipping ({year_out_path})"

    t0 = time.time()
    needed_keys = ev_y.rename(columns={"day0_date": "date"})[["secid", "date"]]
    raw = scan_year_for_keys(year, needed_keys)
    if raw.empty:
        return year, None, f"{year}: no matching rows found ({len(ev_y):,} events) -- skipping"

    raw["dte"] = (raw["exdate"] - raw["date"]).dt.days
    raw = raw[(raw["best_bid"] > 0) & (raw["best_offer"] >= raw["best_bid"]) &
              raw["delta"].notna()].copy()
    raw["mid"] = (raw["best_bid"] + raw["best_offer"]) / 2.0
    # OptionMetrics stores strike_price in 1/1000ths of a dollar -- see the historical note in
    # this script's git history / README for the pathological bug this correction fixes downstream
    # in script 48 if it's ever accidentally dropped. Applied to `raw` BEFORE the chain is built.
    raw["strike_price"] = raw["strike_price"] / 1000.0

    chain = pick_full_chain_vectorized(raw, MIN_DTE, TARGET_DTE)
    if chain.empty:
        return year, None, f"{year}: {len(ev_y):,} events -> 0 chain rows  [{time.time()-t0:.1f}s]"

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
        n_events_matched = year_df.groupby(["secid", "day0_date"]).ngroups
        msg = (f"{year}: {len(ev_y):,} events -> {n_events_matched:,} with a chain, "
               f"{len(year_df):,} total contracts (avg {len(year_df)/max(n_events_matched,1):.1f} "
               f"strikes/event)  [{time.time()-t0:.1f}s]")
        return year, year_df, msg
    return year, None, f"{year}: {len(ev_y):,} events -> 0 matched  [{time.time()-t0:.1f}s]"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_jobs_arg(parser)
    add_force_arg(parser)
    args = parser.parse_args()

    events = pd.read_parquet(OUT_DIR / "decile_events_secid.parquet",
                              columns=["secid", "day0_date", "decile", "permno", "anndats"])
    events["secid"] = events["secid"].astype("int64")
    events["year"] = events["day0_date"].dt.year

    year_groups = {y: events[events["year"] == y] for y in YEARS}
    year_groups = {y: g for y, g in year_groups.items() if not g.empty}
    print(f"{len(year_groups)} years to process, --jobs {args.jobs}", flush=True)

    results = {}
    if args.jobs <= 1 or len(year_groups) <= 1:
        for year, ev_y in year_groups.items():
            year, df, msg = _process_year(year, ev_y, args.force)
            print(msg, flush=True)
            results[year] = df
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futures = {ex.submit(_process_year, year, ev_y, args.force): year
                       for year, ev_y in year_groups.items()}
            for fut in as_completed(futures):
                year, df, msg = fut.result()
                print(msg, flush=True)
                results[year] = df

    all_rows = [results[y] for y in sorted(results) if results[y] is not None]
    combined = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    combined.to_parquet(OUT_DIR / "full_chain_all.parquet", index=False)
    print(f"\nTOTAL: {len(combined):,} contract rows -> {OUT_DIR / 'full_chain_all.parquet'}")


if __name__ == "__main__":
    main()
