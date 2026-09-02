"""
Attaches the underlying's own day0 closing price (secprd{year}.parquet's `close` column) to
data/event_options/full_chain_all.parquet, keyed by (secid, day0_date). script 47's chain scan
only reads opprcd (option quotes: strike, mid, delta, etc.) -- it has no underlying spot price at
all, which scripts/48_optimal_contract_selector.py needs to compute each candidate strike's payoff
under the decile return distribution (payoff = max(0, spot*(1+r) - strike) for a call).

secprd is much smaller than opprcd (one row per underlying per day, not one row per contract), so
this is a fast standalone pass -- no need for options_lib.scan_year_for_keys's batched streaming;
a straight read + filter per year is enough. Years are independent, so --jobs > 1 reads several
years at once in separate worker processes (see script 35's docstring / lib.hw for the default).

Output: data/event_options/full_chain_with_underlying.parquet
"""
import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR
from lib.options import OM_DIR
from lib.hw import add_jobs_arg

OUT_DIR = DATA_DIR / "event_options"
YEARS = [y for y in range(1996, 2014) if y != 2011]


def _process_year(year, need_y):
    t0 = time.time()
    path = f"{OM_DIR}/secprd{year}.parquet"
    tbl = pq.read_table(path, columns=["secid", "date", "close"])
    df = tbl.to_pandas()
    df["secid"] = df["secid"].astype("int64")
    df["date"] = pd.to_datetime(df["date"])
    keys = need_y.rename(columns={"day0_date": "date"})[["secid", "date"]]
    matched = df.merge(keys, on=["secid", "date"], how="inner")
    matched = matched.rename(columns={"date": "day0_date", "close": "underlying_price"})
    msg = f"{year}: {len(need_y):,} needed -> {len(matched):,} priced  [{time.time()-t0:.1f}s]"
    return year, matched[["secid", "day0_date", "underlying_price"]], msg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_jobs_arg(parser)
    args = parser.parse_args()

    chain = pd.read_parquet(OUT_DIR / "full_chain_all.parquet")
    chain["day0_date"] = pd.to_datetime(chain["day0_date"])
    chain["year"] = chain["day0_date"].dt.year
    needed = chain[["secid", "day0_date", "year"]].drop_duplicates()
    print(f"need underlying price for {len(needed):,} distinct (secid, day0_date) pairs")

    year_groups = {y: needed[needed["year"] == y] for y in YEARS}
    year_groups = {y: g for y, g in year_groups.items() if not g.empty}
    print(f"{len(year_groups)} years to process, --jobs {args.jobs}", flush=True)

    results = {}
    if args.jobs <= 1 or len(year_groups) <= 1:
        for year, need_y in year_groups.items():
            year, matched, msg = _process_year(year, need_y)
            print(msg, flush=True)
            results[year] = matched
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futures = {ex.submit(_process_year, year, need_y): year
                       for year, need_y in year_groups.items()}
            for fut in as_completed(futures):
                year, matched, msg = fut.result()
                print(msg, flush=True)
                results[year] = matched

    all_px = [results[y] for y in sorted(results) if results[y] is not None]
    px = pd.concat(all_px, ignore_index=True)
    chain = chain.drop(columns=["year"]).merge(px, on=["secid", "day0_date"], how="left")
    hit_rate = chain["underlying_price"].notna().mean()
    print(f"\nunderlying price coverage: {hit_rate:.1%} of {len(chain):,} chain rows")

    out_path = OUT_DIR / "full_chain_with_underlying.parquet"
    chain.to_parquet(out_path, index=False)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
