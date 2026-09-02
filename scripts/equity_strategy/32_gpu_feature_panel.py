"""
Strategy 8, Tier 1: build a richer per-event feature panel than any prior equity strategy used.

Every strategy through 7 sizes and tilts positions off ONE signal: SUE rank, optionally
size/sector-neutralized. This script builds the substrate for going further -- a feature per
event that requires scanning a real trailing window of that firm's OWN daily return history,
which is exactly the kind of workload the 5090's extra RAM/VRAM/cores exists for: for every one
of the ~100k+ tradeable events, gather up to 252 trailing daily returns from the full
multi-decade panel and reduce them to two cross-sectional features, entirely as dense array ops
(no python-level loop over events) so the identical code path scales from this CPU dev machine to
a batched GPU kernel with zero changes -- see `compute_trailing_features` below.

Features added on top of what positions_rankweighted_v2.parquet already carries:
  - ear               announcement-day return (day0's own realized return, mkt-adjusted) --
                       computed as eq_ret[entry_row_idx], the SAME leak-free definition
                       27_ear_signal_diagnostic.py's docstring documents (day0's own return, fully
                       realized before day0+1 trading begins -- NOT ret_fwd_1d_mktadj, which is
                       the day0->day0+1 return and overlaps the position's own first day of held
                       P&L, the exact bug that diagnostic documents fixing).
  - bm_tercile         book-to-market tercile, merged in from the same extended event file
                       (already computed upstream, just never carried into the position files).
  - momentum_12_1      classic 12-month-minus-1-month momentum: cumulative return over the 252
                       trading days ending 21 days before day0, skipping the most recent month
                       (the reversal-prone part of raw momentum).
  - vol_60d            trailing 60-trading-day realized volatility of daily returns ending the
                       day before day0.
  - fwd_realized_ret   the event's own realized market-adjusted return over its holding window
                       (entry_row_idx+1 .. exit_row_idx) -- NOT a tradeable signal, this is the
                       supervised-learning TARGET 33_gpu_walkforward_signal.py trains against.
                       Computed via a prefix-sum of log(1+ret), not a per-position gather loop:
                       O(n_days) once, then an O(n_positions) lookup -- deliberately the cheap
                       path, since (unlike momentum/vol) every position's own window is already
                       known to be single-permno-contiguous (16_positions_v2.py validated that
                       building entry_row_idx/exit_row_idx in the first place).

Momentum and volatility are the two engineered features that actually need the trailing-window
gather (a value 21-252 days before an arbitrary row can belong to a DIFFERENT permno if the
window runs past that permno's own history start -- unlike the forward/holding window, which
16_positions_v2.py already validated stays single-permno). `compute_trailing_features` masks
those out explicitly rather than silently including a different stock's returns.

Output: data/equity_feature_panel.parquet (one row per tradeable event, superset of
positions_rankweighted_v2.parquet's columns).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, RAW_EQUITY_DIR
from lib.gpu import (get_backend, add_device_arg, add_mock_data_arg, add_smoke_test_arg,
                      StageTimer, compute_trailing_features, compute_fwd_realized_return,
                      make_mock_feature_panel, mark_mock_output)

DATA = DATA_DIR
RAW = RAW_EQUITY_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_device_arg(parser)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    args = parser.parse_args()
    xp, device = get_backend(args.device)

    with StageTimer("32_gpu_feature_panel", extra={"device": device, "mock_data": args.mock_data}):
        if args.mock_data:
            n_events = 300 if args.smoke_test else args.mock_n_events
            n_permnos = 40 if args.smoke_test else args.mock_n_permnos
            print(f"building mock feature panel ({n_permnos} permnos, {n_events} events)...")
            pos = make_mock_feature_panel(n_events=n_events, n_permnos=n_permnos, seed=0)
            out_path = DATA / "equity_feature_panel.parquet"
            pos.to_parquet(out_path, index=False)
            mark_mock_output(out_path, is_mock=True, is_smoke=args.smoke_test)
            print(f"wrote {out_path} ({len(pos):,} rows, {len(pos.columns)} columns)")
            return

        print("loading positions_rankweighted_v2.parquet...")
        pos = pd.read_parquet(DATA / "positions_rankweighted_v2.parquet").reset_index(drop=True)
        pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
        pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
        pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
        pos["day0_date"] = pd.to_datetime(pos["day0_date"])
        # event_id (16_positions_v2.py: permno + "_" + day0_date) is NOT guaranteed unique -- a
        # handful of rows share it across multiple same-day IBES fiscal-quarter events (see
        # equity_pead/01_build_deciles.py's own comment on this). 33/34/35 all merge frames back
        # together keyed on the event identifier; a non-unique key there is a real Cartesian-
        # product risk (duplicating/cross-associating predictions between distinct events), not
        # just the narrower bm_tercile-merge fan-out already guarded below. event_uid is a fresh
        # per-ROW integer assigned here, after `pos` is already one row per genuine tradeable
        # position -- guaranteed unique regardless of what event_id collides on upstream -- and is
        # carried through 33/34/35 as the actual merge key instead of event_id.
        pos["event_uid"] = np.arange(len(pos))

        print("merging bm_tercile from decile_events_ff_adjusted_extended.parquet...")
        ev_extra = pd.read_parquet(
            DATA / "decile_events_ff_adjusted_extended.parquet",
            columns=["permno", "day0_date", "bm_tercile"])
        ev_extra["day0_date"] = pd.to_datetime(ev_extra["day0_date"])
        # (permno, day0_date) is not a unique key upstream -- a handful of rows share it across
        # multiple same-day IBES fiscal-quarter events (see equity_pead/01_build_deciles.py's own
        # comment on this), and that non-uniqueness survives into this file since the dedup applied
        # there only covers the `feat` classification table, not decile_events*. A merge on that key
        # would silently fan out `pos` (and, since pos is 1 row per tradeable event already, cause
        # duplicate/cross-associated rows downstream in 34/35's event_id merges). Collapse to one
        # bm_tercile per (permno, day0_date) before merging -- this feature only needs a value per
        # key, not per underlying event -- and verify the merge stays 1:1.
        ev_extra = ev_extra.drop_duplicates(subset=["permno", "day0_date"], keep="first")
        n_before = len(pos)
        pos = pos.merge(ev_extra, on=["permno", "day0_date"], how="left")
        assert len(pos) == n_before, (
            f"bm_tercile merge changed row count ({n_before} -> {len(pos)}) -- "
            "decile_events_ff_adjusted_extended.parquet has an unexpectedly non-unique key")

        print("loading daily market-adjusted return panel...")
        frames = []
        for y in range(1995, 2015):
            frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet",
                                           columns=["permno", "date", "ret"]))
        eq = pd.concat(frames, ignore_index=True)
        mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
        eq = eq.merge(mkt, on="date", how="left")
        eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
        eq = eq.sort_values(["permno", "date"]).drop_duplicates(
            subset=["permno", "date"]).reset_index(drop=True)
        eq_ret = eq["ret_mktadj"].to_numpy()
        eq_permno = eq["permno"].to_numpy()

        # leak-free EAR: the announcement day's OWN return (day0 itself, at entry_row_idx), fully
        # realized before day0+1 trading begins -- same definition 27_ear_signal_diagnostic.py's
        # corrected Part 2 uses (`day0_own_ret = eq_ret[pos["entry_row_idx"].to_numpy()]`).
        pos["ear"] = eq_ret[pos["entry_row_idx"].to_numpy()]

        print(f"computing trailing momentum/volatility features on {device} for {len(pos):,} events...")
        eq_ret_xp = xp.asarray(eq_ret)
        eq_permno_xp = xp.asarray(eq_permno)
        entry_row_idx_xp = xp.asarray(pos["entry_row_idx"].to_numpy())
        event_permno_xp = xp.asarray(pos["permno"].to_numpy())
        momentum, vol = compute_trailing_features(xp, eq_ret_xp, eq_permno_xp,
                                                    entry_row_idx_xp, event_permno_xp)
        pos["momentum_12_1"] = momentum
        pos["vol_60d"] = vol

        print("computing per-position realized forward return (walk-forward training label)...")
        pos["fwd_realized_ret"] = compute_fwd_realized_return(
            eq_ret, pos["entry_row_idx"].to_numpy(), pos["exit_row_idx"].to_numpy())

        n_missing_mom = pos["momentum_12_1"].isna().sum()
        print(f"events with insufficient trailing history for momentum: {n_missing_mom:,} / {len(pos):,} "
              f"(dropped by 34/35, not by this stage -- kept here so coverage is visible)")

        out_path = DATA / "equity_feature_panel.parquet"
        pos.to_parquet(out_path, index=False)
        mark_mock_output(out_path, is_mock=False, is_smoke=args.smoke_test)
        print(f"wrote {out_path} ({len(pos):,} rows, {len(pos.columns)} columns)")


if __name__ == "__main__":
    main()
