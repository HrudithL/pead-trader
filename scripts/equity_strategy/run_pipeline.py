"""
Single command to run the WHOLE equity-strategy pipeline (scripts 11-35, including Strategy 8's
GPU-tiered pipeline, 32-35) and actually use the hardware a dedicated machine (more RAM/VRAM, more
CPU cores/threads) provides, instead of the strictly-sequential `python script.py` invocations the
README's "Run order" section shows one at a time.

The pipeline's own dependency graph (built from what each script actually reads/writes -- see the
`deps=` list on every `stage()` call below) is much SHALLOWER than the numbered run-order suggests:
once 16_positions_v2.py has built positions_rankweighted_v2.parquet, scripts 17-22, 26-28 (and, for
strategy 8, 32) all only read THAT one file (+ the raw equity panel) and write to entirely distinct
output files -- they don't actually depend on each other, they're just numbered in the order they
were historically developed. This script runs every stage whose dependencies are already satisfied
CONCURRENTLY, as OS-level subprocesses (real parallelism across cores, not threads fighting the
GIL), instead of one at a time:

  - A `ThreadPoolExecutor` (real multithreading: each worker thread just blocks inside
    `subprocess.run`, so the GIL is released for the whole stage and the actual parallel work
    happens as separate OS processes) drives up to `--max-workers` stages at once, default
    `os.cpu_count()`.
  - Each subprocess gets its own BLAS/OpenMP thread-count env vars (OMP_NUM_THREADS etc.) set to
    a fair share of the machine's cores, so N concurrent stages don't each try to grab every core
    for their own numpy/pandas calls and thrash each other -- exactly the "properly utilize cores
    and threads" this script exists for.
  - The GPU-tiered stages (32-34, `gpu=True`) are additionally serialized to at most ONE running
    at a time regardless of `--max-workers`, since there is exactly one physical GPU to share --
    they can still run concurrently alongside unrelated CPU-only stages, just not alongside
    each other.
  - A stage whose every declared output file already exists is skipped (crash/reboot/interrupt
    safe, `--force` to override), same convention as options_strategy/run_pipeline.py.

Usage, real run (needs the raw WRDS pull under data/normalized_equity/, data/events/, etc.):
    python scripts/equity_strategy/run_pipeline.py --device cuda

Usage anywhere, to validate the Strategy 8 GPU pipeline (32-35) end-to-end in seconds, no raw
data or GPU needed (stages 11-31 need real data unconditionally and are skipped in this mode):
    python scripts/equity_strategy/run_pipeline.py --device cpu --mock-data --smoke-test

Progress: printed live, one line per stage start/done/failed, plus a full per-stage stdout/stderr
log at logs/equity_pipeline/<stage>.log (one file per stage, not a single shared log -- concurrent
stages writing to one file would interleave into something unreadable) and a run-level summary at
logs/equity_pipeline_status.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, LOGS_DIR

THIS_DIR = Path(__file__).resolve().parent
STAGE_LOG_DIR = LOGS_DIR / "equity_pipeline"
STATUS_PATH = LOGS_DIR / "equity_pipeline_status.json"


def stage(name, script, outputs, deps=(), extra_args=(), gpu=False):
    if isinstance(outputs, (str, Path)):
        outputs = [outputs]
    return dict(name=name, script=str(THIS_DIR / script), outputs=[DATA_DIR / o for o in outputs],
                deps=list(deps), extra_args=list(extra_args), gpu=gpu)


def build_stages(args):
    gpu_common = ["--device", args.device]
    tail_common = []
    if args.mock_data:
        gpu_common += ["--mock-data"]
        tail_common += ["--mock-data"]
    if args.smoke_test:
        gpu_common += ["--smoke-test"]
        tail_common += ["--smoke-test"]
    # 33_gpu_walkforward_signal.py checkpoints per fold under a config-hashed directory of its
    # own (see that script) -- this top-level --force only bypasses run_pipeline.py's OWN
    # "output already exists" skip unless it's also threaded down to that inner check.
    walkforward_extra = list(gpu_common)
    if args.force:
        walkforward_extra += ["--force"]

    stages = []
    if not args.mock_data:
        # 11-31 have no --mock-data path (they need the real WRDS pull unconditionally), so they're
        # only meaningful -- and only included -- on a real run.
        stages += [
            stage("11_positions", "11_positions.py",
                  ["positions_extreme.parquet", "positions_rankweighted.parquet", "positions_balanced.parquet"]),
            stage("12_backtest_engine", "12_backtest_engine.py", "backtest_comparison.csv",
                  deps=["11_positions"]),
            stage("14_daily_decay", "14_daily_decay.py",
                  ["event_cells.parquet", "cell_daily_curve.csv", "cell_decay_days.csv"]),
            stage("15_decay_days_v2", "15_decay_days_v2.py", "cell_decay_days.csv",
                  deps=["14_daily_decay"]),
            stage("16_positions_v2", "16_positions_v2.py",
                  ["positions_extreme_v2.parquet", "positions_rankweighted_v2.parquet",
                   "positions_balanced_v2.parquet"],
                  deps=["14_daily_decay", "15_decay_days_v2"]),
            stage("17_backtest_v2", "17_backtest_v2.py", "backtest_v2_comparison.csv",
                  deps=["16_positions_v2"]),
            stage("18_param_sweep", "18_param_sweep.py",
                  ["sweep_base_unit_fraction.csv", "sweep_liquidity_cap_frac.csv"],
                  deps=["16_positions_v2"]),
            stage("19_param_sweep_extended", "19_param_sweep_extended.py", "sweep_extended.csv",
                  deps=["16_positions_v2"]),
            stage("20_strategy4_tilted", "20_strategy4_tilted.py",
                  ["positions_strategy4_v2.parquet", "backtest_v2_strategy4.csv",
                   "backtest_v2_strategy4_summary.json"],
                  deps=["16_positions_v2", "17_backtest_v2"]),
            stage("21_leverage_and_improvements", "21_leverage_and_improvements.py",
                  "sweep_strategy4_leverage.csv", deps=["16_positions_v2"]),
            stage("22_sector_neutral_and_cadence", "22_sector_neutral_and_cadence.py", [],
                  deps=["16_positions_v2"]),
            stage("23_finalize_strategy5", "23_finalize_strategy5.py",
                  ["positions_strategy5_v2.parquet", "backtest_v2_strategy5.csv",
                   "backtest_v2_strategy5_summary.json"],
                  deps=["16_positions_v2", "17_backtest_v2", "20_strategy4_tilted"]),
            stage("24_finalize_strategy6", "24_finalize_strategy6.py",
                  ["positions_strategy6_v2.parquet", "backtest_v2_strategy6.csv",
                   "backtest_v2_strategy6_summary.json"],
                  deps=["16_positions_v2", "17_backtest_v2", "20_strategy4_tilted", "23_finalize_strategy5"]),
            stage("25_strategy6_beta_overlay", "25_strategy6_beta_overlay.py",
                  ["backtest_v2_strategy6_beta050.csv", "backtest_v2_strategy6_beta050_summary.json"],
                  deps=["24_finalize_strategy6"]),
            stage("26_strategy7_unconstrained_netexposure", "26_strategy7_unconstrained_netexposure.py",
                  ["positions_strategy7_v2.parquet", "backtest_v2_strategy7.csv",
                   "backtest_v2_strategy7_summary.json"],
                  deps=["16_positions_v2"]),
            stage("27_ear_signal_diagnostic", "27_ear_signal_diagnostic.py", [],
                  deps=["16_positions_v2"]),
            stage("28_sue_reversal_test", "28_sue_reversal_test.py",
                  ["sue_reversal_summary.json", "sue_reversal_by_decile.csv"]),
        ]
        if args.include_reports:
            stages += [
                stage("29_v2_strategy_charts", "29_v2_strategy_charts.py", [],
                      deps=["17_backtest_v2", "20_strategy4_tilted", "23_finalize_strategy5",
                            "24_finalize_strategy6", "25_strategy6_beta_overlay",
                            "26_strategy7_unconstrained_netexposure",
                            "21_leverage_and_improvements", "28_sue_reversal_test"]),
                stage("30_build_strategy_showcase_report", "30_build_strategy_showcase_report.py", [],
                      deps=["17_backtest_v2", "20_strategy4_tilted", "23_finalize_strategy5",
                            "24_finalize_strategy6", "25_strategy6_beta_overlay",
                            "26_strategy7_unconstrained_netexposure", "29_v2_strategy_charts"]),
                stage("31_build_development_report", "31_build_development_report.py", [],
                      deps=["29_v2_strategy_charts"]),
            ]

    gpu_deps = [] if args.mock_data else ["16_positions_v2"]
    stages += [
        stage("32_gpu_feature_panel", "32_gpu_feature_panel.py", "equity_feature_panel.parquet",
              deps=gpu_deps, extra_args=gpu_common, gpu=True),
        stage("33_gpu_walkforward_signal", "33_gpu_walkforward_signal.py", "equity_ml_signal.parquet",
              deps=["32_gpu_feature_panel"], extra_args=walkforward_extra, gpu=True),
        stage("34_gpu_param_sweep", "34_gpu_param_sweep.py",
              ["equity_gpu_sweep_results.csv", "equity_gpu_sweep_best.json"],
              deps=["32_gpu_feature_panel", "33_gpu_walkforward_signal"], extra_args=gpu_common, gpu=True),
        # 35 reads and rewrites backtest_v2_comparison.csv (strategies 1-6's summary files) in its
        # non-mock path, so on a real run it must also wait for 24_finalize_strategy6 (which
        # itself transitively waits on 17/20/23) -- otherwise a concurrent run could have 35 read
        # stale/still-being-written summaries, or have 24 overwrite 35's comparison file afterward
        # and silently drop Strategy 8 from it. Not needed under --mock-data: that path returns
        # before ever touching backtest_v2_comparison.csv, and 11-31 aren't even in `stages` then.
        stage("35_finalize_strategy8", "35_finalize_strategy8.py",
              ["positions_strategy8_v2.parquet", "backtest_v2_strategy8.csv",
               "backtest_v2_strategy8_summary.json"],
              deps=(["34_gpu_param_sweep"] if args.mock_data
                    else ["34_gpu_param_sweep", "24_finalize_strategy6"]),
              extra_args=tail_common),
    ]
    return stages


def stage_done(s):
    return len(s["outputs"]) > 0 and all(p.exists() for p in s["outputs"])


def run_stage(s, threads_per_slot, status, status_lock):
    log_path = STAGE_LOG_DIR / f"{s['name']}.log"
    env = dict(os.environ)
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[var] = str(threads_per_slot)

    with status_lock:
        status[s["name"]] = {"state": "running", "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_status(status)
    print(f"[{s['name']}] started (threads/proc={threads_per_slot}{', GPU' if s['gpu'] else ''})",
          flush=True)

    t0 = time.time()
    cmd = [sys.executable, s["script"]] + s["extra_args"]
    with open(log_path, "w") as logf:
        logf.write(f"cmd: {' '.join(cmd)}\n\n")
        logf.flush()
        result = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, env=env)
    elapsed = time.time() - t0

    with status_lock:
        if result.returncode == 0:
            status[s["name"]] = {"state": "done", "elapsed_sec": round(elapsed, 1)}
        else:
            status[s["name"]] = {"state": "failed", "elapsed_sec": round(elapsed, 1),
                                  "returncode": result.returncode}
        _write_status(status)

    if result.returncode == 0:
        print(f"[{s['name']}] done in {elapsed:.0f}s", flush=True)
    else:
        print(f"[{s['name']}] FAILED after {elapsed:.0f}s (exit {result.returncode}) -- "
              f"see {log_path}", flush=True)
    return s["name"], result.returncode


def _write_status(status):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu",
                         help="array backend for the GPU-tiered stages (32-34). Default: %(default)s")
    parser.add_argument("--mock-data", action="store_true",
                         help="run only the Strategy 8 GPU pipeline (32-35) against synthetic "
                              "data -- 11-31 need the real WRDS pull unconditionally and are "
                              "skipped in this mode.")
    parser.add_argument("--smoke-test", action="store_true",
                         help="shrink every GPU-stage size/iteration knob for a fast correctness check")
    parser.add_argument("--include-reports", action="store_true",
                         help="also run 29-31 (charts + PDF reports) -- needs "
                              "data/results_summary_v2_FINAL.csv, which is manually curated, not "
                              "generated by any script, so this is opt-in rather than default")
    parser.add_argument("--force", action="store_true",
                         help="re-run every stage even if its declared output already exists")
    parser.add_argument("--max-workers", type=int, default=None,
                         help="max concurrent stages (subprocesses); default os.cpu_count()")
    args = parser.parse_args()

    max_workers = args.max_workers or os.cpu_count() or 4
    # +1 accounts for the one GPU stage that can run alongside the CPU-only stages -- a fair,
    # if approximate, per-process thread budget so concurrent stages don't each grab every core.
    threads_per_slot = max(1, (os.cpu_count() or 4) // (max_workers + 1))

    stages = build_stages(args)
    by_name = {s["name"]: s for s in stages}
    STAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"equity pipeline: {len(stages)} stages, device={args.device}, mock_data={args.mock_data}, "
          f"max_workers={max_workers}, threads/proc={threads_per_slot}, "
          f"cpu_count={os.cpu_count()}")

    remaining = {s["name"] for s in stages}
    done = set()
    failed = set()
    status = {}
    status_lock = Lock()
    gpu_running = False
    gpu_lock = Lock()

    # A stage's own declared outputs already existing is NOT enough to skip it: some stages share
    # an output file with an earlier stage that overwrites/corrects it (14_daily_decay and
    # 15_decay_days_v2 both declare cell_decay_days.csv -- 14 writes a preliminary version, 15
    # writes the corrected one). On a fresh checkout, that file being tracked in git while 14's
    # OTHER output (event_cells.parquet) is absent would otherwise mark 15 "already done" before
    # 14 -- which is about to overwrite the shared file with 14's preliminary version -- ever
    # runs. So a stage is only skippable here if EVERY dependency is also skippable this same way
    # (computed to a fixpoint, since a stage can depend on another stage that depends on another).
    progress = True
    while progress:
        progress = False
        for s in stages:
            if s["name"] in done or s["name"] not in remaining:
                continue
            if not args.force and stage_done(s) and set(s["deps"]).issubset(done):
                print(f"[{s['name']}] output already exists -- skipping")
                remaining.discard(s["name"])
                done.add(s["name"])
                status[s["name"]] = {"state": "skipped_already_done"}
                progress = True
    _write_status(status)

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=max_workers + 1) as pool:  # +1 slot reserved for the GPU stage
        in_flight = {}  # future -> stage name

        def try_submit():
            nonlocal gpu_running
            cpu_slots_free = max_workers - sum(1 for n in in_flight.values() if not by_name[n]["gpu"])
            for name in sorted(remaining):
                s = by_name[name]
                if name in in_flight:
                    continue
                if not set(s["deps"]).issubset(done):
                    continue
                if s["gpu"]:
                    with gpu_lock:
                        if gpu_running:
                            continue
                        gpu_running = True
                else:
                    if cpu_slots_free <= 0:
                        continue
                    cpu_slots_free -= 1
                fut = pool.submit(run_stage, s, threads_per_slot, status, status_lock)
                in_flight[fut] = name

        try_submit()
        while in_flight:
            completed, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
            for fut in completed:
                name, rc = fut.result()
                del in_flight[fut]
                remaining.discard(name)
                if by_name[name]["gpu"]:
                    with gpu_lock:
                        gpu_running = False
                if rc == 0:
                    done.add(name)
                else:
                    failed.add(name)
                    # drop everything that (transitively) depended on this failed stage, so the
                    # rest of the independent graph still finishes instead of the whole run
                    # stopping cold on one broken branch
                    changed = True
                    while changed:
                        changed = False
                        for other in list(remaining):
                            if name in by_name[other]["deps"] or any(
                                    d in failed for d in by_name[other]["deps"]):
                                if other not in failed:
                                    failed.add(other)
                                    remaining.discard(other)
                                    status[other] = {"state": "skipped_dependency_failed"}
                                    changed = True
            try_submit()

    _write_status(status)
    elapsed = time.time() - t_start
    print(f"\nequity pipeline finished in {elapsed:.0f}s: {len(done)} done, {len(failed)} failed/skipped")
    if failed:
        print(f"failed or skipped-due-to-failed-dependency: {sorted(failed)}")
        print(f"per-stage logs: {STAGE_LOG_DIR}")
        sys.exit(1)
    print(f"per-stage status: {STATUS_PATH}")


if __name__ == "__main__":
    main()
