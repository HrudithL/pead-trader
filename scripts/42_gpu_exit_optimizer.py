"""
Tier 2 of the options-strategy roadmap: find the best (stop-loss, profit-target) exit rule per
decile bucket, using a genuine daily price path per position instead of script 40's 6 fixed
checkpoints. This directly generalizes the "fixed horizon" question script 40 already asks (which
of {1,5,10,20,40,60}d is best?) into "what if we exit dynamically, the first day either a stop or
a target is touched?" -- an answer that fixed-horizon checkpoints cannot give at all, since they
only ever look at day0+{1,5,10,20,40,60}, never at what happened in between.

This is the point in the roadmap where the compute need genuinely jumps: instead of 6 checkpoints
per position (script 40), this needs a FULL daily price path per position (script 41's job, ~60
days of quotes each -- 10-60x more OptionMetrics scanning than script 40 needed), and instead of
evaluating 1 exit rule, it evaluates a whole grid of (stop_pct, target_pct) combinations across
every position AT ONCE, batched as a third tensor axis. Both axes (positions and parameter grid)
are the two directions this is meant to scale up on real GPU hardware:

  Given N positions, T price-path days, and K (stop, target) combinations, this kernel builds one
  (N, T+1, K) boolean "hit" tensor and reduces it with argmax/take_along_axis -- pure dense-array
  ops, the same family of operation as Options_Content's `super_return_fast` GPU kernel
  (dense contracts x days scan), just extended with the extra K axis. No cudf/RAPIDS needed:
  gpu_lib.get_backend('cuda') hands back cupy, whose array API is close enough to numpy's that
  this file's logic is IDENTICAL on CPU and GPU -- only the array module differs.

Use --smoke-test (tiny N/T/K, runs in seconds) to validate this finishes cleanly with no shape
errors / OOM on a small Colab GPU before ever pointing it at the real multi-hundred-thousand-
position panel on the 5090. Use --mock-data to run entirely without the real daily-path panel
(which script 41 hasn't necessarily been run to produce yet on this machine).

Output: data/gpu_exit_optimizer_results.csv -- one row per (decile_bucket, stop_pct, target_pct)
        with mean realized return, hit rate for stop vs target vs timeout, and Sharpe.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from gpu_lib import get_backend, to_host, add_device_arg, add_mock_data_arg, add_smoke_test_arg, \
    make_mock_daily_paths, StageTimer

DATA = Path("data")


def build_hit_grid(xp, stop_pcts, target_pcts):
    """K = len(stop_pcts) * len(target_pcts) combinations, as two length-K arrays."""
    ss, tt = xp.meshgrid(xp.asarray(stop_pcts), xp.asarray(target_pcts), indexing="ij")
    return ss.ravel(), tt.ravel()


def evaluate_exit_rules(xp, price_path, stop_pcts, target_pcts):
    """price_path: (N, T+1) array, column 0 = entry price. Returns realized_return: (N, K) and
    exit_reason: (N, K) int8 in {0: target hit, 1: stop hit, 2: held to horizon}."""
    entry = price_path[:, [0]]
    ret_path = price_path / entry - 1.0                       # (N, T+1)
    stop_k, target_k = build_hit_grid(xp, stop_pcts, target_pcts)   # (K,)
    K = stop_k.shape[0]
    N, Tp1 = ret_path.shape

    # (N, T+1, K) -- this is the tensor whose size is the actual GPU-vs-CPU scaling argument:
    # N * (T+1) * K elements. Kept modest by default (--smoke-test truncates further).
    ret_b = ret_path[:, :, None]
    hit_stop = ret_b <= -stop_k[None, None, :]
    hit_target = ret_b >= target_k[None, None, :]
    hit_either = hit_stop | hit_target

    any_hit = hit_either.any(axis=1)                          # (N, K)
    first_hit_day = xp.argmax(hit_either, axis=1)              # 0 if no True -- corrected below
    exit_day = xp.where(any_hit, first_hit_day, Tp1 - 1)        # (N, K)

    n_idx = xp.broadcast_to(xp.arange(N)[:, None], exit_day.shape)
    k_idx = xp.broadcast_to(xp.arange(K)[None, :], exit_day.shape)
    realized_return = ret_path[n_idx, exit_day]                # (N, K), gather (ret_path has no K axis)

    # exit reason: at exit_day, was it the stop or the target that actually fired first? (both
    # could technically be true same-day for a huge overnight gap -- stop takes priority, matches
    # a real risk desk's convention of "protect against the bigger loss first").
    stop_at_exit = hit_stop[n_idx, exit_day, k_idx]
    exit_reason = xp.where(~any_hit, 2, xp.where(stop_at_exit, 1, 0)).astype(xp.int8)
    return realized_return, exit_reason, stop_k, target_k


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_device_arg(parser)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    parser.add_argument("--n-events", type=int, default=5_000)
    parser.add_argument("--max-days", type=int, default=60)
    parser.add_argument("--stop-grid", type=str, default="0.10,0.20,0.30,0.40,0.50",
                         help="comma-separated stop-loss fractions to sweep")
    parser.add_argument("--target-grid", type=str, default="0.15,0.30,0.50,0.75,1.00",
                         help="comma-separated profit-target fractions to sweep")
    args = parser.parse_args()

    n_events, max_days = args.n_events, args.max_days
    stop_grid = [float(x) for x in args.stop_grid.split(",")]
    target_grid = [float(x) for x in args.target_grid.split(",")]
    if args.smoke_test:
        n_events, max_days = min(n_events, 200), min(max_days, 15)
        stop_grid, target_grid = stop_grid[:2], target_grid[:2]
        print(f"--smoke-test: shrunk to n_events={n_events}, max_days={max_days}, "
              f"stop_grid={stop_grid}, target_grid={target_grid}")

    with StageTimer("42_gpu_exit_optimizer", extra={"device": args.device, "mock_data": args.mock_data}):
        xp, resolved_device = get_backend(args.device)
        print(f"backend: {xp.__name__} (device={resolved_device})")

        if not args.mock_data:
            daily_panel_path = DATA / "event_options" / "daily_price_paths.parquet"
            if not daily_panel_path.exists():
                raise FileNotFoundError(
                    f"{daily_panel_path} not found. Run scripts/41_build_daily_option_paths.py "
                    "first (needs the OptionMetrics drive attached), or pass --mock-data.")
            raise NotImplementedError(
                "real daily-panel loading path -- wire up once script 41 has been run for real; "
                "see script 41's docstring for the expected long-format schema to pivot here.")

        print(f"using synthetic mock daily paths (n_events={n_events:,}, max_days={max_days})")
        price_path_np, decile = make_mock_daily_paths(n_events=n_events, max_days=max_days)
        price_path = xp.asarray(price_path_np)

        t0 = time.time()
        realized_return, exit_reason, stop_k, target_k = evaluate_exit_rules(
            xp, price_path, stop_grid, target_grid)
        xp_sync = getattr(xp, "cuda", None)
        if xp_sync is not None:
            xp.cuda.Stream.null.synchronize()
        elapsed = time.time() - t0
        K = len(stop_k)
        print(f"evaluated {n_events:,} positions x {K} (stop,target) combos x {max_days} days "
              f"in {elapsed:.3f}s ({resolved_device})")

        realized_return_h = to_host(xp, realized_return)
        exit_reason_h = to_host(xp, exit_reason)
        stop_k_h, target_k_h = to_host(xp, stop_k), to_host(xp, target_k)

        rows = []
        for k in range(K):
            for bucket_name, bucket_mask in [
                ("D9_D10_calls_proxy", decile >= 9), ("D1_D2_puts_proxy", decile <= 2),
                ("all", np.ones(n_events, dtype=bool)),
            ]:
                r = realized_return_h[bucket_mask, k]
                reasons = exit_reason_h[bucket_mask, k]
                mean_ret, std_ret = r.mean(), r.std(ddof=1)
                sharpe_proxy = mean_ret / std_ret if std_ret > 0 else np.nan
                rows.append(dict(
                    stop_pct=float(stop_k_h[k]), target_pct=float(target_k_h[k]), bucket=bucket_name,
                    n=int(bucket_mask.sum()), mean_return=float(mean_ret), std_return=float(std_ret),
                    sharpe_proxy=float(sharpe_proxy),
                    pct_target_hit=float((reasons == 0).mean()), pct_stop_hit=float((reasons == 1).mean()),
                    pct_held_to_horizon=float((reasons == 2).mean()),
                ))
        results = pd.DataFrame(rows)
        results.to_csv(DATA / "gpu_exit_optimizer_results.csv", index=False)
        best = results[results.bucket == "D9_D10_calls_proxy"].sort_values("sharpe_proxy", ascending=False).head(3)
        print("\ntop 3 (stop, target) combos by Sharpe proxy, D9/D10 bucket:")
        print(best[["stop_pct", "target_pct", "mean_return", "sharpe_proxy"]].to_string(index=False))
        print(f"\nwrote {DATA / 'gpu_exit_optimizer_results.csv'} ({len(results)} rows)")


if __name__ == "__main__":
    main()
