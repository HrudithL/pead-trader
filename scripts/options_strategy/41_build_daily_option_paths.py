"""
Tier 2 data extraction: a genuine DAILY price path per selected entry contract (script 35's
entry_contracts_all.parquet), from day0 out to day0+MAX_HOLD_DAYS trading days, instead of
script 36's 6 fixed checkpoints. Feeds scripts/42_gpu_exit_optimizer.py's dynamic-exit search.

This is meaningfully heavier than script 36: that script needed, per event, 6 (secid, date)
lookups; this needs up to MAX_HOLD_DAYS+1 (default 61) of them -- roughly 10x the row volume, and
because the target dates for one event now span a contiguous run of calendar days rather than 6
sparse points, many more distinct calendar years get touched per event near a year boundary.
Expect a full run (the whole ~165k-event panel) to take on the rough order of 1-3 hours on this
machine's disk/network path to the OptionMetrics drive (script 36's 6-checkpoint version took
under 20 minutes) -- run it on the 5090 box, not interactively here; it's resumable (see below) so
an interrupted run picks back up rather than restarting.

Unlike scripts 35/36 (one parquet write per YEAR), this checkpoints per (year, chunk-of-events)
so a run can be safely killed and resumed mid-year without losing hours of progress on a slow
external-drive scan -- necessary given options_lib.scan_year_for_keys already had to add retry
logic for transient USB read errors seen while building this same panel (see options_lib.py).

Target years are independent of each other -- --jobs > 1 scans several years at once in separate
worker processes (see script 35's docstring / lib.hw for why the default is capped rather than
defaulting to every CPU core: the OM drive is a single physical volume on this dev machine).

Output: data/event_options/daily_paths_<year>.parquet (long format: event_id, day_offset, mid)
        data/event_options/daily_price_paths.parquet   (combined, long format)
        (--limit-events writes daily_paths_smoke_<year>.parquet /
        daily_price_paths_smoke.parquet instead -- a truncated "quick real-data smoke test" slice
        must never be mistakable for, or silently satisfy the resume check of, the real full run)
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
from lib.gpu import StageTimer
from lib.hw import add_jobs_arg, add_force_arg

OUT_DIR = DATA_DIR / "event_options"
DEFAULT_MAX_HOLD_DAYS = 60


def _process_year(year, grp, force=False, out_tag=""):
    year_out = OUT_DIR / f"daily_paths{out_tag}_{year}.parquet"
    if year_out.exists() and not force:
        return year, pd.read_parquet(year_out), f"{year}: already written, skipping ({year_out})"

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
    matched = matched[["event_id", "cp_type", "day_offset", "mid"]]
    matched.to_parquet(year_out, index=False)
    hit_rate = matched["mid"].notna().mean()
    msg = (f"{year}: {len(grp):,} lookups -> {matched['mid'].notna().sum():,} priced "
           f"({hit_rate:.1%})  [{time.time()-t0:.1f}s]")
    return year, matched, msg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-hold-days", type=int, default=DEFAULT_MAX_HOLD_DAYS)
    parser.add_argument("--limit-events", type=int, default=None,
                         help="process only the first N events -- for a quick real-data smoke "
                              "test on a small slice before committing to the full ~165k-event "
                              "run (which should happen on the 5090, not here).")
    add_jobs_arg(parser)
    add_force_arg(parser)
    args = parser.parse_args()

    with StageTimer("41_build_daily_option_paths", extra={"max_hold_days": args.max_hold_days}):
        cal = pd.read_parquet(METADATA_DIR / "om_trading_calendar.parquet").sort_values("date")
        cal_dates = cal["date"].to_numpy()

        entries = pd.read_parquet(OUT_DIR / "entry_contracts_all.parquet").reset_index(drop=True)
        entries["event_id"] = entries.index
        # --limit-events makes this a SAMPLE, not the real per-year/combined output -- write it
        # under a distinct name so it can never satisfy this script's own or run_pipeline.py's
        # resume/skip check for a real full run (a truncated "quick real-data smoke test" run must
        # not be mistakable for -- or silently stand in for -- the genuine ~165k-event dataset).
        out_tag = "_smoke" if args.limit_events else ""
        if args.limit_events:
            entries = entries.head(args.limit_events)
            print(f"--limit-events {args.limit_events}: writing to daily_price_paths{out_tag}.parquet, "
                  f"NOT the canonical daily_price_paths.parquet")
        print(f"entries: {len(entries):,} rows, max_hold_days={args.max_hold_days}")

        entry_idx_in_cal = np.searchsorted(cal_dates, entries["day0_date"].to_numpy())
        n_cal = len(cal_dates)

        # long table of every (event, cp_type, day_offset, target_date) lookup needed
        need_rows = []
        for cp in ["call", "put"]:
            oid_col = f"{cp}_optionid"
            if oid_col not in entries.columns:
                continue
            sub = entries[entries[oid_col].notna()][["event_id", "secid", oid_col]].copy()
            sub = sub.rename(columns={oid_col: "optionid"})
            base_idx = entry_idx_in_cal[entries[oid_col].notna().to_numpy()]
            for offset in range(0, args.max_hold_days + 1):
                cal_idx = base_idx + offset
                in_range = cal_idx < n_cal
                if not in_range.any():
                    continue
                sub_o = sub[in_range].copy()
                sub_o["day_offset"] = offset
                sub_o["target_date"] = cal_dates[cal_idx[in_range]]
                sub_o["cp_type"] = cp
                need_rows.append(sub_o)
        need = pd.concat(need_rows, ignore_index=True)
        need["secid"] = need["secid"].astype("int64")
        need["target_year"] = pd.to_datetime(need["target_date"]).dt.year
        print(f"total (event, cp_type, day_offset) lookups needed: {len(need):,}, "
              f"spanning years {sorted(need['target_year'].unique())}")

        year_groups = {year: grp for year, grp in need.groupby("target_year")}
        print(f"{len(year_groups)} target years to process, --jobs {args.jobs}", flush=True)

        results = {}
        if args.jobs <= 1 or len(year_groups) <= 1:
            for year, grp in year_groups.items():
                year, matched, msg = _process_year(year, grp, args.force, out_tag)
                print(msg, flush=True)
                results[year] = matched
        else:
            with ProcessPoolExecutor(max_workers=args.jobs) as ex:
                futures = {ex.submit(_process_year, year, grp, args.force, out_tag): year
                           for year, grp in year_groups.items()}
                for fut in as_completed(futures):
                    year, matched, msg = fut.result()
                    print(msg, flush=True)
                    results[year] = matched

        all_paths = [results[y] for y in sorted(results) if results[y] is not None]
        combined = pd.concat(all_paths, ignore_index=True) if all_paths else pd.DataFrame()
        out_path = OUT_DIR / f"daily_price_paths{out_tag}.parquet"
        combined.to_parquet(out_path, index=False)
        print(f"\nwrote {out_path} ({len(combined):,} rows)")


if __name__ == "__main__":
    main()
