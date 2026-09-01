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

Output: data/event_options/daily_paths_<year>.parquet (long format: event_id, day_offset, mid)
        data/event_options/daily_price_paths.parquet   (combined, long format)
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from options_lib import scan_year_for_keys
from gpu_lib import StageTimer

OUT_DIR = Path("data/event_options")
DEFAULT_MAX_HOLD_DAYS = 60


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-hold-days", type=int, default=DEFAULT_MAX_HOLD_DAYS)
    parser.add_argument("--limit-events", type=int, default=None,
                         help="process only the first N events -- for a quick real-data smoke "
                              "test on a small slice before committing to the full ~165k-event "
                              "run (which should happen on the 5090, not here).")
    args = parser.parse_args()

    with StageTimer("41_build_daily_option_paths", extra={"max_hold_days": args.max_hold_days}):
        cal = pd.read_parquet("data/metadata/om_trading_calendar.parquet").sort_values("date")
        cal_dates = cal["date"].to_numpy()

        entries = pd.read_parquet(OUT_DIR / "entry_contracts_all.parquet").reset_index(drop=True)
        entries["event_id"] = entries.index
        if args.limit_events:
            entries = entries.head(args.limit_events)
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

        years = sorted(need["target_year"].unique())
        all_paths = []
        for year in years:
            year_out = OUT_DIR / f"daily_paths_{year}.parquet"
            if year_out.exists():
                print(f"{year}: already written, skipping ({year_out})", flush=True)
                all_paths.append(pd.read_parquet(year_out))
                continue
            grp = need[need["target_year"] == year]
            t0 = time.time()
            keys = grp.rename(columns={"target_date": "date"})[["secid", "date"]].drop_duplicates()
            raw = scan_year_for_keys(int(year), keys)
            if raw.empty:
                print(f"{year}: 0 rows found for {len(grp):,} lookups")
                continue
            raw["mid"] = np.where((raw["best_bid"] > 0) & (raw["best_offer"] >= raw["best_bid"]),
                                   (raw["best_bid"] + raw["best_offer"]) / 2.0, np.nan)
            raw = raw.rename(columns={"date": "target_date"})[
                ["secid", "target_date", "optionid", "mid"]]
            matched = grp.merge(raw, on=["secid", "target_date", "optionid"], how="left")
            matched = matched[["event_id", "cp_type", "day_offset", "mid"]]
            matched.to_parquet(year_out, index=False)
            all_paths.append(matched)
            hit_rate = matched["mid"].notna().mean()
            print(f"{year}: {len(grp):,} lookups -> {matched['mid'].notna().sum():,} priced "
                  f"({hit_rate:.1%})  [{time.time()-t0:.1f}s]", flush=True)

        combined = pd.concat(all_paths, ignore_index=True) if all_paths else pd.DataFrame()
        out_path = OUT_DIR / "daily_price_paths.parquet"
        combined.to_parquet(out_path, index=False)
        print(f"\nwrote {out_path} ({len(combined):,} rows)")


if __name__ == "__main__":
    main()
