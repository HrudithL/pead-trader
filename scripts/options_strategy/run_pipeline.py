"""
Single command to kick off the whole options-strategy GPU pipeline (Tiers 1-4) and walk away.
Built for the actual constraint on this project: physical/SSH access to the 5090 box is limited
to short windows, and the real runs (Tier 3/4 especially) are meant to run unattended for days.

Each stage is a subprocess (a plain script from scripts/40+), run in dependency order. A stage is
skipped if its declared output file already exists -- so re-running this after an interruption
(crash, reboot, `tmux` session lost) picks up where it left off rather than restarting from
scratch. Progress is in `logs/pipeline_status.json` (also updated per-stage by each script's own
`gpu_lib.StageTimer`) and in `logs/pipeline_run.log` (raw stdout/stderr from every stage,
appended, so the whole history survives even if a stage's own log wasn't watched live).

Usage on the 5090 (real run, needs OPTIONMETRICS_DIR set and the drive attached):
    tmux new -s pead
    python scripts/options_strategy/run_pipeline.py --device cuda

Usage anywhere, to validate the orchestrator itself end-to-end in seconds with no GPU/data needed:
    python scripts/options_strategy/run_pipeline.py --device cpu --mock-data --smoke-test
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, LOGS_DIR

THIS_DIR = Path(__file__).resolve().parent
STATUS_PATH = LOGS_DIR / "pipeline_status.json"
LOG_PATH = LOGS_DIR / "pipeline_run.log"


def stage(name, script, output, extra_args=()):
    return dict(name=name, script=str(THIS_DIR / script), output=DATA_DIR / output,
                extra_args=list(extra_args))


def build_stages(args):
    common_gpu = ["--device", args.device]
    if args.mock_data:
        common_gpu += ["--mock-data"]
    if args.smoke_test:
        common_gpu += ["--smoke-test"]
    mock_flag = ["--mock-data"] if args.mock_data else []

    stages = [
        stage("40_options_backtest", "40_options_backtest.py",
              "backtest_options_v1_comparison.csv", mock_flag),
    ]
    if not args.skip_daily_paths:
        stages.append(stage("41_build_daily_option_paths", "41_build_daily_option_paths.py",
                             "event_options/daily_price_paths.parquet"))
    stages.append(stage("42_gpu_exit_optimizer", "42_gpu_exit_optimizer.py",
                         "gpu_exit_optimizer_results.csv", common_gpu))
    # 43/44/45 are the Tier 3/4 stages -- wired in as they're built out; run_pipeline.py already
    # skips any stage whose script doesn't exist yet rather than failing the whole run, so this
    # driver is safe to use incrementally as later tiers land.
    for name, script, output in [
        ("43_gpu_param_sweep", "43_gpu_param_sweep.py", "gpu_param_sweep_results.csv"),
        ("44_ml_contract_selector", "44_ml_contract_selector.py", "ml_contract_selector_summary.json"),
        ("45_joint_portfolio_optimizer", "45_joint_portfolio_optimizer.py", "joint_portfolio_summary.json"),
    ]:
        stages.append(stage(name, script, output, common_gpu))
    return stages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--mock-data", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--skip-daily-paths", action="store_true",
                         help="skip the (slow, real-data-only) Tier 2 daily-path extraction stage")
    parser.add_argument("--force", action="store_true",
                         help="re-run every stage even if its output already exists")
    args = parser.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stages = build_stages(args)

    print(f"pipeline: {len(stages)} stages, device={args.device}, mock_data={args.mock_data}")
    for s in stages:
        if not Path(s["script"]).exists():
            print(f"[{s['name']}] script not written yet ({s['script']}) -- skipping for now")
            continue
        if not args.force and s["output"].exists():
            print(f"[{s['name']}] output already exists ({s['output']}) -- skipping")
            continue

        cmd = [sys.executable, s["script"]] + s["extra_args"]
        print(f"[{s['name']}] running: {' '.join(cmd)}", flush=True)
        t0 = time.time()
        with open(LOG_PATH, "a") as logf:
            logf.write(f"\n=== {s['name']} started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            logf.flush()
            result = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT)
        elapsed = time.time() - t0

        if result.returncode != 0:
            print(f"[{s['name']}] FAILED after {elapsed:.0f}s (exit {result.returncode}) -- "
                  f"see {LOG_PATH} for the full error. Stopping pipeline; fix and re-run "
                  f"this same command to resume from here.")
            sys.exit(result.returncode)
        print(f"[{s['name']}] done in {elapsed:.0f}s")

    print("\npipeline finished (or every remaining stage was already done / not yet written).")
    if STATUS_PATH.exists():
        print(f"per-stage timing/status: {STATUS_PATH}")


if __name__ == "__main__":
    main()
