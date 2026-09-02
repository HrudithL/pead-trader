"""
THE single command to kick off the whole options-strategy pipeline (scripts 35-52: data
extraction, all four GPU tiers, and the final charts/report) and walk away. Built for the actual
constraint on this project: physical/SSH access to the 5090 box is limited to short windows, and
the real runs (Tier 3/4 especially) are meant to run unattended for days.

This is a SCHEDULER, not just a sequential runner: it reads the real dependency graph between the
16 stages below and runs everything that CAN run concurrently at once, instead of one stage at a
time, so a beefy machine's cores/RAM/VRAM don't sit idle waiting on a stage that has nothing to do
with them:

  - "io" stages (35/36/41/47/47b/49) stream the OptionMetrics opprcd/secprd files one calendar
    year at a time. Each of those scripts now parallelizes ITS OWN year loop internally across
    --jobs worker processes (see lib/hw.py). Across stages, io-kind stages are capped at
    --io-parallel concurrent stages (default 1) because on this dev machine they share one
    physical external drive -- raise --io-parallel on a machine with faster/striped storage.
  - "cpu" stages (40/46/50) are fast vectorized pandas/numpy passes with no GPU and no shared-disk
    bottleneck -- up to --cpu-parallel of them run at once.
  - "gpu" stages (42/43/44/45/48) all contend for the one GPU's compute + VRAM, so at most ONE
    ever runs at a time regardless of anything else -- but it runs CONCURRENTLY with whatever
    io/cpu stage is simultaneously ready, instead of blocking the whole pipeline on the GPU queue.
  - "light" stages (51/52) are the final chart/report passes -- cheap, always safe to run whenever
    their inputs are ready, sharing the cpu pool.

A stage only starts once every stage it depends on has reached a terminal state (done, or a
skip that still leaves that dependency's declared output file in place). A stage is skipped
outright if its script doesn't exist yet, if it was blocked by a skipped/failed dependency, if
--mock-data was requested but that stage has no mock-data path AND its real input isn't already
on disk (a mock-capable upstream stage's own output never counts as a "real input" here, so a
non-mock stage downstream of one doesn't get launched for real against synthetic data), or (41
only) if --skip-daily-paths was passed. A stage already carrying its declared output file is
treated as done without re-running it (crash/reboot/lost-tmux-session safe), unless --force --
which is also threaded down to every per-year-checkpointed stage (35/36/41/47/47b/49) so a forced
run actually rescans instead of silently reusing their stale per-year parquet checkpoints.

Progress is in `logs/pipeline_status.json` (also updated per-stage by supporting scripts' own
`lib.gpu.StageTimer`, now cross-process-lock-safe since several stages can finish at once) and in
`logs/pipeline_run.log` (every stage's stdout/stderr, each line prefixed with that stage's name so
concurrent output stays legible -- interleaved by wall-clock arrival, same as any `make -j` log).

Usage on the 5090 (real run, needs OPTIONMETRICS_DIR set and the drive attached):
    tmux new -s pead
    python scripts/options_strategy/run_pipeline.py --device cuda

Usage anywhere, to validate the orchestrator itself end-to-end in seconds with no GPU/data needed:
    python scripts/options_strategy/run_pipeline.py --device cpu --mock-data --smoke-test

Usage to preview the schedule (what would run, in what order/concurrency) without running anything:
    python scripts/options_strategy/run_pipeline.py --dry-run
"""
import argparse
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, EVENTS_DIR, METADATA_DIR, REPORTS_DIR, FIGURES_DIR, LOGS_DIR
from lib.hw import add_jobs_arg, cpu_count, gpu_summary, hardware_report

THIS_DIR = Path(__file__).resolve().parent
EVENT_DIR = DATA_DIR / "event_options"
STATUS_PATH = LOGS_DIR / "pipeline_status.json"
LOG_PATH = LOGS_DIR / "pipeline_run.log"
_LOG_LOCK = threading.Lock()


def stage(name, script, deps=(), inputs=(), outputs=(), kind="cpu", supports_mock=False,
          supports_smoke=None, supports_device=False, supports_jobs=False, supports_force=False,
          skip_if_exists=True, extra=()):
    # every mock-capable stage in this pipeline also takes --smoke-test EXCEPT
    # 40_options_backtest.py, which only ever added --mock-data -- so default supports_smoke to
    # supports_mock and let a stage override it explicitly rather than repeating =True everywhere.
    if supports_smoke is None:
        supports_smoke = supports_mock
    return dict(name=name, script=str(THIS_DIR / script), deps=list(deps),
                inputs=[Path(p) for p in inputs], outputs=[Path(p) for p in outputs], kind=kind,
                supports_mock=supports_mock, supports_smoke=supports_smoke,
                supports_device=supports_device, supports_jobs=supports_jobs,
                supports_force=supports_force, skip_if_exists=skip_if_exists, extra=list(extra))


def build_stages():
    """The real dependency graph for scripts 35-52. `inputs`/`outputs` are declared paths (not
    exhaustive -- just enough to (a) know when a stage is already done and (b) know, under
    --mock-data, whether a stage with no mock-data path can still run for real because its input
    already happens to exist on disk)."""
    return [
        stage("35_select_entry_contracts", "35_select_entry_contracts.py",
              inputs=[EVENT_DIR / "decile_events_secid.parquet"],
              outputs=[EVENT_DIR / "entry_contracts_all.parquet"],
              kind="io", supports_jobs=True, supports_force=True),

        stage("36_forward_option_prices", "36_forward_option_prices.py",
              deps=["35_select_entry_contracts"],
              inputs=[EVENT_DIR / "entry_contracts_all.parquet",
                      METADATA_DIR / "om_trading_calendar.parquet"],
              outputs=[EVENT_DIR / "option_event_panel.parquet"],
              kind="io", supports_jobs=True, supports_force=True),

        stage("40_options_backtest", "40_options_backtest.py",
              deps=["36_forward_option_prices"],
              inputs=[EVENT_DIR / "option_event_panel.parquet"],
              outputs=[DATA_DIR / "backtest_options_v1_comparison.csv"],
              kind="cpu", supports_mock=True, supports_smoke=False),

        stage("41_build_daily_option_paths", "41_build_daily_option_paths.py",
              deps=["35_select_entry_contracts"],
              inputs=[EVENT_DIR / "entry_contracts_all.parquet"],
              outputs=[EVENT_DIR / "daily_price_paths.parquet"],
              kind="io", supports_jobs=True, supports_force=True),

        stage("42_gpu_exit_optimizer", "42_gpu_exit_optimizer.py",
              deps=["41_build_daily_option_paths"],
              inputs=[EVENT_DIR / "daily_price_paths.parquet"],
              outputs=[DATA_DIR / "gpu_exit_optimizer_results.csv"],
              kind="gpu", supports_mock=True, supports_device=True),

        stage("43_gpu_param_sweep", "43_gpu_param_sweep.py",
              deps=["36_forward_option_prices"],
              inputs=[EVENT_DIR / "option_event_panel.parquet"],
              outputs=[DATA_DIR / "gpu_param_sweep_results.csv"],
              kind="gpu", supports_mock=True, supports_device=True),

        stage("44_ml_contract_selector", "44_ml_contract_selector.py",
              deps=["36_forward_option_prices"],
              inputs=[EVENT_DIR / "option_event_panel.parquet"],
              outputs=[DATA_DIR / "ml_contract_selector_summary.json"],
              kind="gpu", supports_mock=True, supports_device=True),

        stage("45_joint_portfolio_optimizer", "45_joint_portfolio_optimizer.py",
              deps=["40_options_backtest"],
              inputs=[DATA_DIR / "backtest_options_v1_comparison.csv"],
              outputs=[DATA_DIR / "joint_portfolio_summary.json"],
              kind="gpu", supports_mock=True, supports_device=True),

        stage("46_build_return_distributions", "46_build_return_distributions.py",
              inputs=[EVENTS_DIR / "equity_events_1996_2013.parquet"],
              outputs=[METADATA_DIR / "decile_return_distributions.parquet"],
              kind="cpu"),

        stage("47_scan_full_chain_entries", "47_scan_full_chain_entries.py",
              inputs=[EVENT_DIR / "decile_events_secid.parquet"],
              outputs=[EVENT_DIR / "full_chain_all.parquet"],
              kind="io", supports_jobs=True, supports_force=True),

        stage("47b_attach_underlying_price", "47b_attach_underlying_price.py",
              deps=["47_scan_full_chain_entries"],
              inputs=[EVENT_DIR / "full_chain_all.parquet"],
              outputs=[EVENT_DIR / "full_chain_with_underlying.parquet"],
              kind="io", supports_jobs=True, supports_force=True),

        stage("48_optimal_contract_selector", "48_optimal_contract_selector.py",
              deps=["47b_attach_underlying_price", "46_build_return_distributions"],
              inputs=[EVENT_DIR / "full_chain_with_underlying.parquet",
                      METADATA_DIR / "decile_return_distributions.parquet"],
              outputs=[EVENT_DIR / "optimal_contracts.parquet"],
              kind="gpu", supports_mock=True, supports_device=True),

        stage("49_forward_prices_optimal", "49_forward_prices_optimal.py",
              deps=["48_optimal_contract_selector"],
              inputs=[EVENT_DIR / "optimal_contracts.parquet"],
              outputs=[EVENT_DIR / "optimal_forward_prices.parquet"],
              kind="io", supports_jobs=True, supports_force=True),

        stage("50_options_backtest_optimal", "50_options_backtest_optimal.py",
              deps=["49_forward_prices_optimal"],
              inputs=[EVENT_DIR / "optimal_forward_prices.parquet"],
              outputs=[DATA_DIR / "backtest_options_optimal_comparison.csv"],
              kind="cpu"),

        stage("51_options_strategy_charts", "51_options_strategy_charts.py",
              deps=["40_options_backtest", "50_options_backtest_optimal",
                    "48_optimal_contract_selector"],
              outputs=[FIGURES_DIR / "26_optimal_selector_put_share_by_decile.png"],
              kind="light", skip_if_exists=False),

        stage("52_build_options_strategy_report", "52_build_options_strategy_report.py",
              deps=["40_options_backtest", "50_options_backtest_optimal",
                    "48_optimal_contract_selector", "45_joint_portfolio_optimizer",
                    "43_gpu_param_sweep", "51_options_strategy_charts"],
              outputs=[REPORTS_DIR / "PEAD_Options_Strategy_Report.pdf"],
              kind="light", skip_if_exists=False),
    ]


def _log(line: str):
    with _LOG_LOCK:
        print(line, flush=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _closure(stage_names, stages_by_name):
    """Every stage in `stage_names` plus everything they transitively depend on."""
    keep, frontier = set(), list(stage_names)
    while frontier:
        name = frontier.pop()
        if name in keep:
            continue
        keep.add(name)
        frontier.extend(stages_by_name[name]["deps"])
    return keep


def build_command(s, args, resolved_device):
    cmd = [sys.executable, s["script"]]
    if s["supports_device"]:
        cmd += ["--device", resolved_device]
    if args.mock_data and s["supports_mock"]:
        cmd += ["--mock-data"]
    if args.smoke_test and s["supports_smoke"]:
        cmd += ["--smoke-test"]
    if s["supports_jobs"]:
        cmd += ["--jobs", str(args.jobs)]
    if s["supports_force"] and args.force:
        cmd += ["--force"]
    if s["name"] == "41_build_daily_option_paths" and args.smoke_test:
        cmd += ["--max-hold-days", "10", "--limit-events", "500"]
    cmd += s["extra"]
    return cmd


def run_stage(s, args, resolved_device, sema):
    cmd = build_command(s, args, resolved_device)
    queued_at = time.time()
    if not sema.acquire(blocking=False):
        _log(f"[{s['name']}] QUEUED (waiting for a free {s['kind']} slot)")
        sema.acquire()
    try:
        t0 = time.time()
        queued_for = t0 - queued_at
        suffix = f" (queued {queued_for:.0f}s)" if queued_for > 1.0 else ""
        _log(f"[{s['name']}] STARTING{suffix}: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
        for line in proc.stdout:
            _log(f"[{s['name']}] {line.rstrip()}")
        returncode = proc.wait()
        elapsed = time.time() - t0
    finally:
        sema.release()
    if returncode != 0:
        _log(f"[{s['name']}] FAILED after {elapsed:.0f}s (exit {returncode})")
        return s["name"], False, elapsed
    _log(f"[{s['name']}] done in {elapsed:.0f}s")
    return s["name"], True, elapsed


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", choices=["cpu", "cuda"], default=None,
                         help="device for every GPU-capable stage. Default: cuda if this machine "
                              "has one (via cupy), else cpu.")
    parser.add_argument("--mock-data", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    add_jobs_arg(parser, help_suffix="Passed to every stage that scans OptionMetrics data "
                                      "year-by-year (35/36/41/47/47b/49).")
    parser.add_argument("--io-parallel", type=int, default=1,
                         help="max concurrent OM-scan STAGES (separate from --jobs, which is the "
                              "worker count WITHIN one such stage). Default 1: this dev machine's "
                              "OM drive is a single physical external volume, so running two scan "
                              "stages at once just contends for the same disk. Raise this on a "
                              "machine with faster/striped storage (e.g. the 5090's local NVMe).")
    parser.add_argument("--cpu-parallel", type=int, default=max(1, min(4, cpu_count())),
                         help="max concurrent CPU-only stages (40/46/50) with no shared-disk "
                              f"bottleneck. Default: %(default)s (this machine has {cpu_count()} "
                              "cores).")
    parser.add_argument("--force", action="store_true",
                         help="re-run every stage even if its output already exists")
    parser.add_argument("--skip-daily-paths", action="store_true",
                         help="skip the (slow, real-data-only) Tier 2 daily-path extraction stage "
                              "(41) -- 42 is then skipped too, since it depends on 41's output")
    parser.add_argument("--only", type=str, default=None,
                         help="comma-separated stage names to run (plus whatever they transitively "
                              "depend on), instead of the full pipeline")
    parser.add_argument("--dry-run", action="store_true",
                         help="print the planned schedule (what would run, skip, or block, and "
                              "with what command) without actually running anything")
    args = parser.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    gpu = gpu_summary()
    resolved_device = args.device or ("cuda" if gpu["available"] else "cpu")

    stages = build_stages()
    stages_by_name = {s["name"]: s for s in stages}
    active_names = set(stages_by_name)
    # under --mock-data, any file that's a declared OUTPUT of a mock-capable stage must never be
    # treated by a non-mock-capable downstream stage as "real input already on disk" -- a
    # mock-capable stage running under --mock-data writes synthetic data to that exact real path
    # (e.g. 48_optimal_contract_selector writes a mock optimal_contracts.parquet), and without this
    # exclusion 49_forward_prices_optimal (no mock-data path of its own) would see that file exist
    # and launch for real against synthetic rows lacking secid/day0_date, on a machine that also
    # has no real OM/calendar data to fall back on.
    mock_unsafe_paths = set()
    if args.mock_data:
        for s in stages:
            if s["supports_mock"]:
                mock_unsafe_paths.update(s["outputs"])
    if args.only:
        requested = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in requested if n not in stages_by_name]
        if unknown:
            parser.error(f"--only: unknown stage(s) {unknown}. Known stages: "
                         f"{sorted(stages_by_name)}")
        active_names = _closure(requested, stages_by_name)

    print(f"pipeline: {len(active_names)}/{len(stages)} stages active, hardware: {hardware_report()}")
    print(f"device={resolved_device}, mock_data={args.mock_data}, smoke_test={args.smoke_test}, "
          f"jobs={args.jobs}, io_parallel={args.io_parallel}, cpu_parallel={args.cpu_parallel}")

    sema_by_kind = {
        "io": threading.Semaphore(max(1, args.io_parallel)),
        "cpu": threading.Semaphore(max(1, args.cpu_parallel)),
        "light": threading.Semaphore(max(1, args.cpu_parallel)),
        "gpu": threading.Semaphore(1),  # exactly one GPU on the box this is built for
    }

    state = {}          # name -> "done" | "failed" | "skipped_*" | "running"
    futures = {}         # Future -> name
    pool = ThreadPoolExecutor(max_workers=len(stages))

    def effective_deps(s):
        """Under --mock-data, a stage that supports mock data doesn't actually read its real
        upstream dependency's output at all (it generates synthetic data instead) -- so its real
        dependency chain should not gate or block it. Without this, e.g. 42_gpu_exit_optimizer
        (mock-capable) would sit blocked behind 41_build_daily_option_paths (not mock-capable,
        needs the real OM drive) even though --mock-data means 42 never touches 41's output."""
        if args.mock_data and s["supports_mock"]:
            return []
        return s["deps"]

    def deps_resolved(s):
        return all(d not in active_names or state.get(d) in
                   ("done", "skipped_no_script", "skipped_blocked", "skipped_manual",
                    "skipped_mock_unsupported", "failed")
                   for d in effective_deps(s))

    def deps_ok(s):
        """True only if every ACTIVE dependency actually finished with usable output."""
        for d in effective_deps(s):
            if d not in active_names:
                continue
            if state.get(d) != "done":
                return False
        return True

    try:
        # loop while there's a stage not yet even evaluated OR outstanding work in flight --
        # `futures` (not `state`) is the source of truth for "still running": a stage's entry in
        # `state` is set to "running" at submit time (so a second pass doesn't resubmit it), so
        # checking key-presence in `state` alone would let this loop exit while a submitted stage
        # is still actually executing.
        while futures or any(n not in state for n in active_names):
            progressed = False
            for name in list(active_names):
                if name in state:
                    continue
                s = stages_by_name[name]
                if not deps_resolved(s):
                    continue
                progressed = True

                if not Path(s["script"]).exists():
                    state[name] = "skipped_no_script"
                    print(f"[{name}] script not written yet ({s['script']}) -- skipping")
                    continue
                if not args.force and s["skip_if_exists"] and s["outputs"] and \
                        all(p.exists() for p in s["outputs"]):
                    state[name] = "done"
                    print(f"[{name}] output already exists -- skipping")
                    continue
                if args.skip_daily_paths and name == "41_build_daily_option_paths":
                    state[name] = "skipped_manual"
                    print(f"[{name}] --skip-daily-paths -- skipping")
                    continue
                if not deps_ok(s):
                    state[name] = "skipped_blocked"
                    print(f"[{name}] skipped: a dependency did not complete successfully")
                    continue
                if args.mock_data and not s["supports_mock"] and \
                        not all(p.exists() and p not in mock_unsafe_paths for p in s["inputs"]):
                    state[name] = "skipped_mock_unsupported"
                    print(f"[{name}] skipped: --mock-data was requested, this stage has no "
                          f"mock-data path, and its real input isn't on disk yet (or is itself a "
                          f"mock-capable stage's output under --mock-data)")
                    continue

                cmd = build_command(s, args, resolved_device)
                if args.dry_run:
                    state[name] = "done"  # pretend-succeed so downstream stages preview too
                    print(f"[{name}] WOULD RUN ({s['kind']}): {' '.join(cmd)}")
                    continue

                state[name] = "running"
                fut = pool.submit(run_stage, s, args, resolved_device, sema_by_kind[s["kind"]])
                futures[fut] = name

            if args.dry_run:
                continue
            if not progressed and not futures:
                # nothing left ready and nothing in flight, but not everything resolved -> a
                # dependency cycle or a bug in the stage table; fail loudly rather than spin.
                unresolved = [n for n in active_names if n not in state]
                raise RuntimeError(f"scheduler stuck: no progress possible for {unresolved}")
            if futures:
                done_futs, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                for fut in done_futs:
                    name = futures.pop(fut)
                    _, ok, _ = fut.result()
                    state[name] = "done" if ok else "failed"
    finally:
        pool.shutdown(wait=True)

    print("\n=== pipeline summary ===")
    for name in stages_by_name:
        if name in active_names:
            print(f"  {name}: {state.get(name, 'not reached')}")
    if any(state.get(n) == "failed" for n in active_names):
        print("\npipeline finished with at least one FAILED stage -- see the messages above and "
              f"{LOG_PATH} for full output. Fix the failure and re-run this same command to "
              "resume (completed stages' outputs are left in place and will be skipped).")
        sys.exit(1)
    print(f"\npipeline finished. per-stage timing/status: {STATUS_PATH}")


if __name__ == "__main__":
    main()
