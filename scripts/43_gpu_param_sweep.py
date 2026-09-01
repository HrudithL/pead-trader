"""
Tier 3, part 1: a GPU-batched coarse sweep over (trim, tilt_power, hold_horizon) -- the same kind
of "wide grid, find the elbow" pass scripts 18/19 already ran for the equity strategies, but
covering the options book's signal-construction knobs (script 40 currently hand-picks
TRIM=0.15, TILT_POWER=1.0) instead of one CPU run per combo.

Deliberately two-stage, matching how 18/19 fed into 20-24 in the equity pipeline: this script
computes a fast, no-compounding SCREENING metric (weighted-mean-return and a Sharpe-like ratio,
NOT a full trailing-NAV backtest with priority-based capping -- that sequential, path-dependent
logic doesn't batch cleanly across a parameter tensor) to find promising (trim, tilt_power,
horizon) regions cheaply across a whole grid at once. The actual, precise Sharpe/return/drawdown
for any specific combo this sweep flags as promising should then be obtained by literally setting
those constants in script 40 and running the real engine -- this script is a filter, not a
replacement for script 40.

What's actually GPU-shaped here: `side` (call vs. put) only depends on sign(decile - 5.5), so it's
fixed across the whole (trim, tilt_power) grid -- only each position's WEIGHT changes per combo,
not which return column it uses. That makes the whole grid a single (N_events, N_trim, N_tilt)
broadcast-and-reduce, evaluated once per horizon (the small, 6-value outer loop) -- the same
"batch the parameter dimension as an extra tensor axis" idea as script 42's exit-rule grid.

Output: data/gpu_param_sweep_results.csv -- one row per (trim, tilt_power, horizon) combo.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from gpu_lib import get_backend, to_host, add_device_arg, add_mock_data_arg, add_smoke_test_arg, \
    make_mock_option_event_panel, StageTimer

DATA = Path("data")
HORIZONS = [1, 5, 10, 20, 40, 60]


def load_panel(args):
    if args.mock_data:
        panel = make_mock_option_event_panel(n_events=args.mock_n_events)
    else:
        panel_path = DATA / "event_options" / "option_event_panel.parquet"
        if not panel_path.exists():
            raise FileNotFoundError(f"{panel_path} not found; run scripts 33-36 or pass --mock-data.")
        panel = pd.read_parquet(panel_path)
        panel = panel[panel["decile"].notna()].reset_index(drop=True)
        panel["decile"] = panel["decile"].astype(int)
    return panel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_device_arg(parser)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    parser.add_argument("--trim-grid", type=str, default="0.0,0.05,0.10,0.15,0.20,0.30,0.40")
    parser.add_argument("--tilt-power-grid", type=str, default="0.5,0.75,1.0,1.5,2.0")
    args = parser.parse_args()

    trim_grid = [float(x) for x in args.trim_grid.split(",")]
    tilt_grid = [float(x) for x in args.tilt_power_grid.split(",")]
    horizons = HORIZONS
    if args.smoke_test:
        trim_grid, tilt_grid, horizons = trim_grid[:3], tilt_grid[:2], horizons[:2]
        args.mock_n_events = min(args.mock_n_events, 2_000)
        print(f"--smoke-test: shrunk grid to trim={trim_grid}, tilt={tilt_grid}, horizons={horizons}")

    with StageTimer("43_gpu_param_sweep", extra={"device": args.device, "mock_data": args.mock_data}):
        xp, resolved_device = get_backend(args.device)
        print(f"backend: {xp.__name__} (device={resolved_device})")

        panel = load_panel(args)
        n = len(panel)
        print(f"panel: {n:,} events")

        d = (panel["decile"].to_numpy() - 5.5) / 4.5
        side = np.sign(d)
        is_call, is_put = side > 0, side < 0
        d_xp = xp.asarray(d)
        trim_xp = xp.asarray(trim_grid)
        tilt_xp = xp.asarray(tilt_grid)

        abs_d = xp.abs(d_xp)[:, None]                          # (N, 1)
        frac = xp.clip(abs_d, 0, 1)
        sign_d = xp.sign(d_xp)[:, None]

        rows = []
        for h in horizons:
            call_ret = panel.get(f"call_ret_fwd_{h}d")
            put_ret = panel.get(f"put_ret_fwd_{h}d")
            if call_ret is None or put_ret is None:
                continue
            ret_for_side = np.where(is_call, call_ret.to_numpy(), np.where(is_put, put_ret.to_numpy(), np.nan))
            valid = np.isfinite(ret_for_side)
            ret_xp = xp.asarray(np.nan_to_num(ret_for_side))[:, None]        # (N, 1)
            valid_xp = xp.asarray(valid)[:, None]

            for tilt_power in tilt_grid:
                tilt = sign_d * (frac ** tilt_power)                          # (N, 1)
                # broadcast against the trim grid -> (N, n_trim)
                keep = (abs_d > trim_xp[None, :]) & valid_xp
                weight = xp.where(keep, tilt, 0.0)                            # (N, n_trim)
                pnl = weight * ret_xp                                        # (N, n_trim)

                gross = xp.abs(weight).sum(axis=0)
                mean_w_ret = xp.where(gross > 0, pnl.sum(axis=0) / xp.maximum(gross, 1e-12), xp.nan)
                pnl_std = xp.where(keep.sum(axis=0) > 1,
                                    xp.nanstd(xp.where(keep, pnl, xp.nan), axis=0), xp.nan)
                sharpe_proxy = mean_w_ret / xp.where(pnl_std > 0, pnl_std, xp.nan)
                n_active = keep.sum(axis=0)

                mean_w_ret_h = to_host(xp, mean_w_ret)
                sharpe_h = to_host(xp, sharpe_proxy)
                n_active_h = to_host(xp, n_active)
                for i, trim in enumerate(trim_grid):
                    rows.append(dict(horizon=h, trim=trim, tilt_power=tilt_power,
                                      n_active=int(n_active_h[i]),
                                      mean_weighted_return=float(mean_w_ret_h[i]),
                                      sharpe_proxy=float(sharpe_h[i])))

        results = pd.DataFrame(rows)
        results.to_csv(DATA / "gpu_param_sweep_results.csv", index=False)
        best = results.sort_values("sharpe_proxy", ascending=False).head(5)
        print(f"\nswept {len(trim_grid)} x {len(tilt_grid)} x {len(horizons)} = "
              f"{len(trim_grid)*len(tilt_grid)*len(horizons)} combos")
        print("\ntop 5 by sharpe_proxy (a coarse screen -- confirm the winner with the real "
              "engine in script 40, not this number):")
        print(best.to_string(index=False))
        print(f"\nwrote {DATA / 'gpu_param_sweep_results.csv'} ({len(results)} rows)")


if __name__ == "__main__":
    main()
