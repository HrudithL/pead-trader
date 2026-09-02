"""
Same job as script 36 (fetch each selected contract's mid price at day0+{1,5,10,20,40,60}
trading days), but for the contracts scripts/48_optimal_contract_selector.py actually picked
(Kelly-optimal AND the naive-EV comparison pick) instead of script 35's near-ATM heuristic pick.
Needed to backtest the optimal-contract strategy for real rather than just trusting the selector's
own expected-growth estimate.

Target years are independent -- --jobs > 1 scans several years at once in separate worker
processes (see script 35's docstring / lib.hw for the default and why it's capped). Writes one
checkpoint parquet per year (skipped on restart if already written, same resume convention as
scripts 35/36/41/47) plus the final combined file.

Output: data/event_options/optimal_forward_prices_<year>.parquet (one per year touched)
        data/event_options/optimal_forward_prices.parquet -- one row per event with entry +
        forward mid prices for both the kelly pick and the naive pick.
"""
import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, METADATA_DIR
from lib.options import scan_year_for_keys
from lib.hw import add_jobs_arg, add_force_arg

HORIZONS = [1, 5, 10, 20, 40, 60]
OUT_DIR = DATA_DIR / "event_options"


def _process_year(year, grp, force=False):
    year_out_path = OUT_DIR / f"optimal_forward_prices_{year}.parquet"
    if year_out_path.exists() and not force:
        return year, pd.read_parquet(year_out_path), \
            f"{year}: already written, skipping ({year_out_path})"

    t0 = time.time()
    keys = grp.rename(columns={"target_date": "date"})[["secid", "date"]].drop_duplicates()
    raw = scan_year_for_keys(int(year), keys)
    if raw.empty:
        return year, None, f"{year}: 0 rows found for {len(grp):,} lookups"
    raw["mid"] = np.where((raw["best_bid"] > 0) & (raw["best_offer"] >= raw["best_bid"]),
                           (raw["best_bid"] + raw["best_offer"]) / 2.0, np.nan)
    raw = raw.rename(columns={"date": "target_date"})[
        ["secid", "target_date", "optionid", "mid"]]
    matched = grp.merge(raw, on=["secid", "target_date", "optionid"], how="left")
    matched.to_parquet(year_out_path, index=False)
    hit_rate = matched["mid"].notna().mean()
    msg = (f"{year}: {len(grp):,} lookups -> {matched['mid'].notna().sum():,} priced "
           f"({hit_rate:.1%})  [{time.time()-t0:.1f}s]")
    return year, matched, msg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_jobs_arg(parser)
    add_force_arg(parser)
    args = parser.parse_args()

    cal = pd.read_parquet(METADATA_DIR / "om_trading_calendar.parquet").sort_values("date").reset_index(drop=True)
    cal_dates = cal["date"].values
    cal_index = {d: i for i, d in enumerate(cal_dates)}
    gap_days = pd.Series(cal_dates).diff().dt.days
    LARGE_GAP = 10
    gap_after = (gap_days > LARGE_GAP).values

    def forward_date(d, k):
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

    picks = pd.read_parquet(OUT_DIR / "optimal_contracts.parquet")
    picks["day0_date"] = pd.to_datetime(picks["day0_date"])
    print(f"picks loaded: {len(picks):,} events")

    need_rows = []
    for which in ["kelly", "naive"]:
        oid_col = f"{which}_optionid"
        sub = picks[picks[oid_col] > 0][["event_id", "secid", "day0_date", oid_col]].copy()
        sub = sub.rename(columns={oid_col: "optionid"})
        for h in HORIZONS:
            sub_h = sub.copy()
            sub_h["horizon"] = h
            sub_h["target_date"] = sub_h["day0_date"].map(lambda d: forward_date(d, h))
            sub_h["which"] = which
            need_rows.append(sub_h)

    need = pd.concat(need_rows, ignore_index=True)
    need = need.dropna(subset=["target_date"])
    need["secid"] = need["secid"].astype("int64")
    need["target_year"] = need["target_date"].dt.year
    print(f"total (event, which, horizon) lookups needed: {len(need):,}")

    year_groups = {year: grp for year, grp in need.groupby("target_year")}
    print(f"{len(year_groups)} target years to process, --jobs {args.jobs}", flush=True)

    results = {}
    if args.jobs <= 1 or len(year_groups) <= 1:
        for year, grp in year_groups.items():
            year, matched, msg = _process_year(year, grp, args.force)
            print(msg, flush=True)
            results[year] = matched
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futures = {ex.submit(_process_year, year, grp, args.force): year
                       for year, grp in year_groups.items()}
            for fut in as_completed(futures):
                year, matched, msg = fut.result()
                print(msg, flush=True)
                results[year] = matched

    all_fwd = [results[y] for y in sorted(results) if results[y] is not None]
    fwd = pd.concat(all_fwd, ignore_index=True)
    fwd_wide = fwd.pivot_table(index=["event_id", "which"], columns="horizon", values="mid", aggfunc="first")
    fwd_wide.columns = [f"fwd_mid_{c}d" for c in fwd_wide.columns]
    fwd_wide = fwd_wide.reset_index()

    kelly_fwd = fwd_wide[fwd_wide["which"] == "kelly"].drop(columns=["which"])
    kelly_fwd = kelly_fwd.rename(columns={c: f"kelly_{c}" for c in kelly_fwd.columns if c != "event_id"})
    naive_fwd = fwd_wide[fwd_wide["which"] == "naive"].drop(columns=["which"])
    naive_fwd = naive_fwd.rename(columns={c: f"naive_{c}" for c in naive_fwd.columns if c != "event_id"})

    panel = picks.merge(kelly_fwd, on="event_id", how="left").merge(naive_fwd, on="event_id", how="left")
    for which in ["kelly", "naive"]:
        entry_col = f"{which}_premium"
        for h in HORIZONS:
            fwd_col = f"{which}_fwd_mid_{h}d"
            if fwd_col in panel.columns:
                panel[f"{which}_ret_fwd_{h}d"] = panel[fwd_col] / panel[entry_col] - 1.0

    out_path = OUT_DIR / "optimal_forward_prices.parquet"
    panel.to_parquet(out_path, index=False)
    print(f"\nwrote {out_path} ({len(panel):,} rows)")


if __name__ == "__main__":
    main()
