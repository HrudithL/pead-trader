"""THE single command to build every strategy in this project -- equity (1-8) and options (Tiers
1-4) -- on a dedicated machine (the 5090 Linux GPU box), and walk away.

WHY THIS SCRIPT EXISTS AND HOW IT DIFFERS FROM equity_strategy/run_pipeline.py and
options_strategy/run_pipeline.py: those two schedulers maximize hardware use by running several
DIFFERENT stages/strategies CONCURRENTLY (as long as their dependencies allow it). That's a good
default when you don't care which strategy finishes first. This script instead implements the
model actually wanted for the big unattended GPU-box run: run ONE strategy at a time, but give it
the ENTIRE machine -- every CPU core, all the RAM, the one GPU -- so that single strategy finishes
as fast as this hardware can make it finish, then move to the next one. Nothing runs concurrently
with anything else here on purpose: two stages sharing a 32-core/512GB-RAM box each get roughly
half of it under the other schedulers' concurrent model; here, one stage gets all of it, then the
next stage gets all of it. Because each individual strategy is faster this way, running them all
back-to-back is still fast overall -- exactly the "sequential across strategies, parallel within
each one" model that was actually asked for.

This script is an ORCHESTRATOR, not a reimplementation: it shells out to the exact same numbered
scripts every other run order in this repo already uses (equity_pead 01-10, equity_strategy 11-35,
options_pead 33-34, options_strategy 35-52). Nothing about any strategy's logic lives here.

WHAT "give it the entire machine" ACTUALLY MEANS, concretely:
  - Every GPU-capable stage gets --device cuda (if a GPU is present) with no other stage
    contending for it.
  - Every stage that scans data year-by-year (--jobs) gets a worker count sized off BOTH this
    machine's core count AND its RAM (see full_resource_jobs() below) -- the whole point of a
    machine with a lot of RAM is that more per-year workers can each hold a year's data in memory
    at once without swapping, which is the actual lever "use the RAM to go faster" pulls.
  - BLAS/OpenMP thread-count env vars are left at their default (unset = "use every core"), since
    nothing else is running at the same time to divide the machine's cores with.

CHECKPOINTING AND FAILURE HANDLING, for a box that WILL crash, freeze, or get power-cycled during
a multi-day run at some point:
  - A stage whose declared output file(s) already exist is skipped on restart (same convention as
    every other pipeline in this repo) -- a crash loses at most the ONE stage that was running,
    never a stage that already finished. Several stages (the per-year OM-scan scripts) checkpoint
    even more finely, one output file per calendar year, so a crash mid-stage loses at most the
    year in progress.
  - Status is written to logs/run_all_status.json IMMEDIATELY after every single stage (not
    batched at the end), so a `kill -9` or power loss still leaves a readable trail of exactly
    what finished, what was running, and what never got reached.
  - A heartbeat file (logs/run_all_heartbeat.json) is updated every --heartbeat-interval seconds
    while a stage is running. If the box hangs (not crashes -- hangs), the process list will still
    show `python` running; the heartbeat's timestamp is what actually tells you whether it's
    stuck versus just slow.
  - On a stage's failure, this script captures WHY, not just THAT: the last 60 lines of that
    stage's own output, its exit code, a hardware snapshot at the moment of failure (RAM
    available, disk free space on the data volume, GPU memory/temperature via `nvidia-smi` if
    present), written to logs/run_all/<stage>.failure.json. That covers the actual common real
    causes on a long unattended run: OOM, a full disk, a GPU driver crash, thermal throttling.
  - By default, a failed stage blocks only the stages that depend on it -- everything else in the
    independent part of the graph still runs, so one broken branch doesn't waste an overnight run.
    Pass --stop-on-failure to abort the whole run on the first failure instead.

Usage on the 5090 (real run, needs the raw WRDS pull under data/ and, for the options half,
OPTIONMETRICS_DIR set with the drive attached):
    tmux new -s pead
    python scripts/run_all_strategies.py --device cuda

Usage anywhere, to validate the orchestrator end-to-end in seconds with no GPU/data needed:
    python scripts/run_all_strategies.py --device cpu --mock-data --smoke-test

Usage to preview the schedule (what would run, skip, or block, in what order) without running
anything:
    python scripts/run_all_strategies.py --dry-run

Useful flags:
    --only <name,name,...>   run just these stages (plus whatever they transitively depend on)
    --force                  re-run every stage even if its output already exists
    --include-reports        also build the chart/PDF stages (cheap, off by default)
    --skip-daily-paths       skip options Tier 2's slow real-data-only daily-path extraction
    --stop-on-failure        abort the whole run on the first failed stage
    --jobs N                 override the auto-sized per-year worker count
    --ram-per-job-gb N       how much RAM to budget per --jobs worker when auto-sizing (default 4)
"""
import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(THIS_DIR / "options_strategy"))  # lib/hw.py has no options-specific deps

from common.paths import DATA_DIR, EVENTS_DIR, METADATA_DIR, REPORTS_DIR, FIGURES_DIR, LOGS_DIR
from lib.hw import cpu_count, total_ram_gb, gpu_summary, hardware_report  # noqa: E402

EVENT_DIR = DATA_DIR / "event_options"
STATUS_PATH = LOGS_DIR / "run_all_status.json"
HEARTBEAT_PATH = LOGS_DIR / "run_all_heartbeat.json"
FAILURE_DIR = LOGS_DIR / "run_all"
LOG_PATH = LOGS_DIR / "run_all.log"
_LOG_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Stage catalog: name, script, deps, declared outputs, capability flags, domain.
# Order here IS a valid dependency order (each stage's deps appear earlier in the list) -- the
# runner still checks deps explicitly rather than trusting that, so a future edit that breaks
# ordering fails loudly (RuntimeError) instead of silently running out of order.
# ---------------------------------------------------------------------------

def stage(name, script, deps=(), outputs=(), domain="equity", supports_device=False,
          supports_jobs=False, supports_mock=False, supports_smoke=None, supports_force=False):
    if supports_smoke is None:
        supports_smoke = supports_mock
    return dict(name=name, script=str((THIS_DIR / script).resolve()), deps=list(deps),
                outputs=[Path(p) for p in outputs], domain=domain,
                supports_device=supports_device, supports_jobs=supports_jobs,
                supports_mock=supports_mock, supports_smoke=supports_smoke,
                supports_force=supports_force)


def build_catalog(include_reports: bool):
    D = DATA_DIR
    cat = [
        # ---------------- equity_pead prerequisites (evidence pipeline's data substrate) --------
        stage("eq_pead_01_build_deciles", "equity_pead/01_build_deciles.py",
              outputs=[D / "decile_events.parquet"]),
        stage("eq_pead_06_ff_adjustment", "equity_pead/06_ff_adjustment.py",
              deps=["eq_pead_01_build_deciles"], outputs=[D / "decile_events_ff_adjusted.parquet"]),
        stage("eq_pead_08_extended_horizons", "equity_pead/08_extended_horizons.py",
              deps=["eq_pead_01_build_deciles"], outputs=[D / "decile_events_extended.parquet"]),
        stage("eq_pead_10_extended_ff_decay", "equity_pead/10_extended_ff_decay.py",
              deps=["eq_pead_06_ff_adjustment", "eq_pead_08_extended_horizons"],
              outputs=[D / "decile_events_ff_adjusted_extended.parquet"]),

        # ---------------- equity_strategy: v1 (legacy), v2, Strategies 1-7 ----------------------
        stage("eq_11_positions", "equity_strategy/11_positions.py",
              deps=["eq_pead_10_extended_ff_decay"],
              outputs=[D / "positions_extreme.parquet", D / "positions_rankweighted.parquet",
                        D / "positions_balanced.parquet"]),
        stage("eq_12_backtest_engine", "equity_strategy/12_backtest_engine.py",
              deps=["eq_11_positions"], outputs=[D / "backtest_comparison.csv"]),
        stage("eq_14_daily_decay", "equity_strategy/14_daily_decay.py",
              deps=["eq_pead_06_ff_adjustment"],
              outputs=[D / "event_cells.parquet", D / "cell_daily_curve.csv", D / "cell_decay_days.csv"]),
        stage("eq_15_decay_days_v2", "equity_strategy/15_decay_days_v2.py",
              deps=["eq_14_daily_decay"], outputs=[D / "cell_decay_days.csv"]),
        stage("eq_16_positions_v2", "equity_strategy/16_positions_v2.py",
              deps=["eq_14_daily_decay", "eq_15_decay_days_v2", "eq_pead_10_extended_ff_decay"],
              outputs=[D / "positions_extreme_v2.parquet", D / "positions_rankweighted_v2.parquet",
                        D / "positions_balanced_v2.parquet"]),
        stage("eq_17_backtest_v2", "equity_strategy/17_backtest_v2.py",
              deps=["eq_16_positions_v2"], outputs=[D / "backtest_v2_comparison.csv"]),
        stage("eq_18_param_sweep", "equity_strategy/18_param_sweep.py",
              deps=["eq_16_positions_v2"],
              outputs=[D / "sweep_base_unit_fraction.csv", D / "sweep_liquidity_cap_frac.csv"]),
        stage("eq_19_param_sweep_extended", "equity_strategy/19_param_sweep_extended.py",
              deps=["eq_16_positions_v2"], outputs=[D / "sweep_extended.csv"]),
        stage("eq_20_strategy4_tilted", "equity_strategy/20_strategy4_tilted.py",
              deps=["eq_16_positions_v2", "eq_17_backtest_v2"],
              outputs=[D / "positions_strategy4_v2.parquet", D / "backtest_v2_strategy4.csv",
                        D / "backtest_v2_strategy4_summary.json"]),
        stage("eq_21_leverage_and_improvements", "equity_strategy/21_leverage_and_improvements.py",
              deps=["eq_16_positions_v2"], outputs=[D / "sweep_strategy4_leverage.csv"]),
        stage("eq_22_sector_neutral_and_cadence", "equity_strategy/22_sector_neutral_and_cadence.py",
              deps=["eq_16_positions_v2"], outputs=[]),
        stage("eq_23_finalize_strategy5", "equity_strategy/23_finalize_strategy5.py",
              deps=["eq_16_positions_v2", "eq_17_backtest_v2", "eq_20_strategy4_tilted"],
              outputs=[D / "positions_strategy5_v2.parquet", D / "backtest_v2_strategy5.csv",
                        D / "backtest_v2_strategy5_summary.json"]),
        stage("eq_24_finalize_strategy6", "equity_strategy/24_finalize_strategy6.py",
              deps=["eq_16_positions_v2", "eq_17_backtest_v2", "eq_20_strategy4_tilted",
                    "eq_23_finalize_strategy5"],
              outputs=[D / "positions_strategy6_v2.parquet", D / "backtest_v2_strategy6.csv",
                        D / "backtest_v2_strategy6_summary.json"]),
        stage("eq_25_strategy6_beta_overlay", "equity_strategy/25_strategy6_beta_overlay.py",
              deps=["eq_24_finalize_strategy6"],
              outputs=[D / "backtest_v2_strategy6_beta050.csv",
                        D / "backtest_v2_strategy6_beta050_summary.json"]),
        stage("eq_26_strategy7_unconstrained", "equity_strategy/26_strategy7_unconstrained_netexposure.py",
              deps=["eq_16_positions_v2"],
              outputs=[D / "positions_strategy7_v2.parquet", D / "backtest_v2_strategy7.csv",
                        D / "backtest_v2_strategy7_summary.json"]),
        stage("eq_27_ear_signal_diagnostic", "equity_strategy/27_ear_signal_diagnostic.py",
              deps=["eq_16_positions_v2", "eq_pead_06_ff_adjustment"], outputs=[]),
        stage("eq_28_sue_reversal_test", "equity_strategy/28_sue_reversal_test.py",
              outputs=[D / "sue_reversal_summary.json", D / "sue_reversal_by_decile.csv"]),

        # ---------------- equity_strategy: Strategy 8 (GPU-tiered) ------------------------------
        stage("eq_32_gpu_feature_panel", "equity_strategy/32_gpu_feature_panel.py",
              deps=["eq_16_positions_v2", "eq_pead_10_extended_ff_decay"],
              outputs=[D / "equity_feature_panel.parquet"],
              supports_device=True, supports_mock=True),
        stage("eq_33_gpu_walkforward_signal", "equity_strategy/33_gpu_walkforward_signal.py",
              deps=["eq_32_gpu_feature_panel"], outputs=[D / "equity_walkforward_summary.json"],
              supports_device=True, supports_mock=True, supports_force=True),
        stage("eq_34_gpu_param_sweep", "equity_strategy/34_gpu_param_sweep.py",
              deps=["eq_33_gpu_walkforward_signal"], outputs=[D / "equity_gpu_sweep_best.json"],
              supports_device=True, supports_mock=True),
        stage("eq_35_finalize_strategy8", "equity_strategy/35_finalize_strategy8.py",
              deps=["eq_34_gpu_param_sweep"],
              outputs=[D / "backtest_v2_strategy8.csv", D / "backtest_v2_strategy8_summary.json"],
              supports_mock=True),

        # ---------------- options_pead prerequisites --------------------------------------------
        stage("opt_pead_33_trading_calendar", "options_pead/33_build_trading_calendar.py",
              domain="options", outputs=[METADATA_DIR / "om_trading_calendar.parquet"]),
        stage("opt_pead_34_link_events_secid", "options_pead/34_link_events_secid.py",
              domain="options", deps=["opt_pead_33_trading_calendar"],
              outputs=[EVENT_DIR / "decile_events_secid.parquet"]),

        # ---------------- options_strategy: contract selection + Tier 1 -------------------------
        stage("opt_35_select_entry_contracts", "options_strategy/35_select_entry_contracts.py",
              domain="options", deps=["opt_pead_34_link_events_secid"],
              outputs=[EVENT_DIR / "entry_contracts_all.parquet"], supports_jobs=True, supports_force=True),
        stage("opt_36_forward_option_prices", "options_strategy/36_forward_option_prices.py",
              domain="options", deps=["opt_35_select_entry_contracts", "opt_pead_33_trading_calendar"],
              outputs=[EVENT_DIR / "option_event_panel.parquet"], supports_jobs=True, supports_force=True),
        stage("opt_40_options_backtest_tier1", "options_strategy/40_options_backtest.py",
              domain="options", deps=["opt_36_forward_option_prices"],
              outputs=[D / "backtest_options_v1_comparison.csv"], supports_mock=True, supports_smoke=False),

        # ---------------- options_strategy: Tier 1.5 (Kelly-optimal selector) -------------------
        stage("opt_46_return_distributions", "options_strategy/46_build_return_distributions.py",
              domain="options", outputs=[METADATA_DIR / "decile_return_distributions.parquet"]),
        stage("opt_47_scan_full_chain", "options_strategy/47_scan_full_chain_entries.py",
              domain="options", deps=["opt_pead_34_link_events_secid"],
              outputs=[EVENT_DIR / "full_chain_all.parquet"], supports_jobs=True, supports_force=True),
        stage("opt_47b_attach_underlying", "options_strategy/47b_attach_underlying_price.py",
              domain="options", deps=["opt_47_scan_full_chain"],
              outputs=[EVENT_DIR / "full_chain_with_underlying.parquet"], supports_jobs=True, supports_force=True),
        stage("opt_48_optimal_contract_selector", "options_strategy/48_optimal_contract_selector.py",
              domain="options", deps=["opt_47b_attach_underlying", "opt_46_return_distributions"],
              outputs=[EVENT_DIR / "optimal_contracts.parquet"], supports_device=True, supports_mock=True),
        stage("opt_49_forward_prices_optimal", "options_strategy/49_forward_prices_optimal.py",
              domain="options", deps=["opt_48_optimal_contract_selector"],
              outputs=[EVENT_DIR / "optimal_forward_prices.parquet"], supports_jobs=True, supports_force=True),
        stage("opt_50_options_backtest_optimal", "options_strategy/50_options_backtest_optimal.py",
              domain="options", deps=["opt_49_forward_prices_optimal"],
              outputs=[D / "backtest_options_optimal_comparison.csv"]),

        # ---------------- options_strategy: Tier 2 (dynamic exit optimizer) ---------------------
        stage("opt_41_daily_option_paths", "options_strategy/41_build_daily_option_paths.py",
              domain="options", deps=["opt_35_select_entry_contracts"],
              outputs=[EVENT_DIR / "daily_price_paths.parquet"], supports_jobs=True, supports_force=True),
        stage("opt_42_gpu_exit_optimizer", "options_strategy/42_gpu_exit_optimizer.py",
              domain="options", deps=["opt_41_daily_option_paths"],
              outputs=[D / "gpu_exit_optimizer_results.csv"], supports_device=True, supports_mock=True),

        # ---------------- options_strategy: Tier 3 (sizing sweep + ML selector) -----------------
        stage("opt_43_gpu_param_sweep", "options_strategy/43_gpu_param_sweep.py",
              domain="options", deps=["opt_36_forward_option_prices"],
              outputs=[D / "gpu_param_sweep_results.csv"], supports_device=True, supports_mock=True),
        stage("opt_44_ml_contract_selector", "options_strategy/44_ml_contract_selector.py",
              domain="options", deps=["opt_36_forward_option_prices"],
              outputs=[D / "ml_contract_selector_summary.json"], supports_device=True, supports_mock=True),

        # ---------------- options_strategy: Tier 4 (joint allocation) ---------------------------
        stage("opt_45_joint_portfolio_optimizer", "options_strategy/45_joint_portfolio_optimizer.py",
              domain="options", deps=["opt_40_options_backtest_tier1", "eq_24_finalize_strategy6",
                                       "eq_25_strategy6_beta_overlay"],
              outputs=[D / "joint_portfolio_summary.json"], supports_device=True, supports_mock=True),
    ]

    if include_reports:
        cat += [
            stage("eq_pead_02_decile_summary", "equity_pead/02_decile_summary.py",
                  deps=["eq_pead_01_build_deciles"], outputs=[]),
            stage("eq_pead_03_subsample", "equity_pead/03_subsample.py",
                  deps=["eq_pead_01_build_deciles"], outputs=[]),
            stage("eq_pead_04_charts", "equity_pead/04_charts.py",
                  deps=["eq_pead_01_build_deciles"], outputs=[]),
            stage("eq_pead_07_ff_charts", "equity_pead/07_ff_charts.py",
                  deps=["eq_pead_06_ff_adjustment"], outputs=[]),
            stage("eq_pead_09_decay_analysis", "equity_pead/09_decay_analysis.py",
                  deps=["eq_pead_08_extended_horizons"], outputs=[]),
            stage("eq_pead_32_build_evidence_report", "equity_pead/32_build_evidence_report.py",
                  deps=["eq_pead_02_decile_summary", "eq_pead_03_subsample", "eq_pead_04_charts",
                        "eq_pead_07_ff_charts", "eq_pead_09_decay_analysis"],
                  outputs=[REPORTS_DIR / "PEAD_Report.pdf"]),
            stage("eq_13_strategy_charts", "equity_strategy/13_strategy_charts.py",
                  deps=["eq_12_backtest_engine"], outputs=[]),
            stage("eq_29_v2_strategy_charts", "equity_strategy/29_v2_strategy_charts.py",
                  deps=["eq_17_backtest_v2", "eq_20_strategy4_tilted", "eq_23_finalize_strategy5",
                        "eq_24_finalize_strategy6", "eq_25_strategy6_beta_overlay",
                        "eq_26_strategy7_unconstrained", "eq_21_leverage_and_improvements",
                        "eq_28_sue_reversal_test"], outputs=[]),
            stage("eq_30_showcase_report", "equity_strategy/30_build_strategy_showcase_report.py",
                  deps=["eq_29_v2_strategy_charts"], outputs=[REPORTS_DIR / "PEAD_Strategy_Showcase.pdf"]),
            stage("eq_31_development_report", "equity_strategy/31_build_development_report.py",
                  deps=["eq_29_v2_strategy_charts"], outputs=[REPORTS_DIR / "PEAD_Strategy_Development.pdf"]),
            stage("opt_pead_37_decile_summary", "options_pead/37_decile_summary_options.py",
                  domain="options", deps=["opt_36_forward_option_prices"], outputs=[]),
            stage("opt_pead_38_charts", "options_pead/38_charts_options.py",
                  domain="options", deps=["opt_pead_37_decile_summary"], outputs=[]),
            stage("opt_pead_39_build_report", "options_pead/39_build_options_report.py",
                  domain="options", deps=["opt_pead_38_charts"],
                  outputs=[REPORTS_DIR / "PEAD_Options_Report.pdf"]),
            stage("opt_51_strategy_charts", "options_strategy/51_options_strategy_charts.py",
                  domain="options", deps=["opt_40_options_backtest_tier1", "opt_50_options_backtest_optimal",
                                           "opt_48_optimal_contract_selector"],
                  outputs=[FIGURES_DIR / "26_optimal_selector_put_share_by_decile.png"]),
            stage("opt_52_build_report", "options_strategy/52_build_options_strategy_report.py",
                  domain="options", deps=["opt_51_strategy_charts", "opt_45_joint_portfolio_optimizer",
                                           "opt_43_gpu_param_sweep"],
                  outputs=[REPORTS_DIR / "PEAD_Options_Strategy_Report.pdf"]),
        ]
    return cat


# ---------------------------------------------------------------------------
# Hardware: size --jobs off BOTH cores and RAM, since running solo means nothing else on the box
# is competing for either -- "use the huge RAM to go faster" means more per-year workers can each
# hold a full year's data in memory at once.
# ---------------------------------------------------------------------------

def full_resource_jobs(ram_per_job_gb: float) -> int:
    ram = total_ram_gb()
    by_cores = cpu_count()
    by_ram = max(1, int(ram // ram_per_job_gb)) if ram else by_cores
    return max(1, min(by_cores, by_ram))


def available_ram_gb():
    """Best-effort AVAILABLE (not total) RAM, for failure diagnostics -- cross-platform, stdlib
    only, same style as lib.hw.total_ram_gb()."""
    try:
        import os
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullAvailPhys / (1024 ** 3)
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024 ** 2)  # kB -> GB
    except Exception:
        return None
    return None


def gpu_snapshot():
    """Best-effort `nvidia-smi` read for a failure snapshot -- not required (falls back to the
    cupy-based gpu_summary() if nvidia-smi isn't on PATH), just more informative when it is."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    g = gpu_summary()
    return f"(nvidia-smi unavailable) cupy sees: {g}"


def hardware_snapshot():
    return {
        "ram_available_gb": round(available_ram_gb() or -1, 1),
        "ram_total_gb": round(total_ram_gb() or -1, 1),
        "disk_free_gb": round(shutil.disk_usage(DATA_DIR).free / (1024 ** 3), 1),
        "gpu": gpu_snapshot(),
    }


# ---------------------------------------------------------------------------
# Status / heartbeat / failure logging
# ---------------------------------------------------------------------------

def _load_status():
    return json.loads(STATUS_PATH.read_text()) if STATUS_PATH.exists() else {}


def _save_status(status):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2, default=str))


def _record(name, **fields):
    status = _load_status()
    status[name] = {**status.get(name, {}), **fields}
    _save_status(status)


def _log(line: str):
    with _LOG_LOCK:
        print(line, flush=True)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class Heartbeat:
    """Background thread that timestamps 'what's running right now' every `interval` seconds --
    the difference between a hung process (heartbeat goes stale) and a slow-but-alive one."""

    def __init__(self, interval: float):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._name = None
        self._t0 = None

    def start(self, name):
        self._name = name
        self._t0 = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT_PATH.write_text(json.dumps({
                "stage": self._name, "elapsed_sec": round(time.time() - self._t0, 1),
                "last_heartbeat": time.strftime("%Y-%m-%d %H:%M:%S"),
            }))
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


def _closure(names, by_name):
    keep, frontier = set(), list(names)
    while frontier:
        n = frontier.pop()
        if n in keep:
            continue
        keep.add(n)
        frontier.extend(by_name[n]["deps"])
    return keep


def build_command(s, args, resolved_device, jobs):
    cmd = [sys.executable, s["script"]]
    if s["supports_device"]:
        cmd += ["--device", resolved_device]
    if args.mock_data and s["supports_mock"]:
        cmd += ["--mock-data"]
    if args.smoke_test and s["supports_smoke"]:
        cmd += ["--smoke-test"]
    if s["supports_jobs"]:
        cmd += ["--jobs", str(jobs)]
    if s["supports_force"] and args.force:
        cmd += ["--force"]
    if s["name"] == "opt_41_daily_option_paths" and args.smoke_test:
        cmd += ["--max-hold-days", "10", "--limit-events", "500"]
    return cmd


def run_stage(s, args, resolved_device, jobs, heartbeat: Heartbeat):
    cmd = build_command(s, args, resolved_device, jobs)
    _log(f"[{s['name']}] STARTING: {' '.join(cmd)}")
    _record(s["name"], state="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    heartbeat.start(s["name"])
    t0 = time.time()
    lines = []
    stage_log = FAILURE_DIR / f"{s['name']}.log"
    stage_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(stage_log, "w", encoding="utf-8") as slog:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1)
            for line in proc.stdout:
                _log(f"[{s['name']}] {line.rstrip()}")
                slog.write(line)
                lines.append(line.rstrip())
                if len(lines) > 200:
                    lines.pop(0)
            returncode = proc.wait()
    finally:
        heartbeat.stop()
    elapsed = time.time() - t0

    if returncode != 0:
        snapshot = hardware_snapshot()
        failure = {
            "stage": s["name"], "exit_code": returncode, "elapsed_sec": round(elapsed, 1),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "command": cmd,
            "last_output_lines": lines[-60:], "hardware_at_failure": snapshot,
            "likely_cause": (
                "process killed by signal (often OOM-killer on Linux) -- check hardware_at_failure.ram_available_gb"
                if returncode < 0 else
                "non-zero exit -- see last_output_lines / logs/run_all/<stage>.log for the traceback"),
        }
        (FAILURE_DIR / f"{s['name']}.failure.json").write_text(json.dumps(failure, indent=2, default=str))
        _log(f"[{s['name']}] FAILED after {elapsed:.0f}s (exit {returncode}) -- "
             f"see logs/run_all/{s['name']}.failure.json for why")
        _record(s["name"], state="failed", elapsed_sec=round(elapsed, 1), returncode=returncode)
        return False

    _log(f"[{s['name']}] done in {elapsed:.0f}s")
    _record(s["name"], state="done", elapsed_sec=round(elapsed, 1))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", choices=["cpu", "cuda"], default=None,
                         help="device for every GPU-capable stage. Default: cuda if this machine has one, else cpu.")
    parser.add_argument("--mock-data", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--jobs", type=int, default=None,
                         help="override the auto-sized (cores AND ram) per-year worker count.")
    parser.add_argument("--ram-per-job-gb", type=float, default=4.0,
                         help="RAM budget per --jobs worker when auto-sizing (default: %(default)s).")
    parser.add_argument("--force", action="store_true", help="re-run every stage even if its output already exists")
    parser.add_argument("--include-reports", action="store_true", help="also build the chart/PDF stages")
    parser.add_argument("--skip-daily-paths", action="store_true",
                         help="skip options Tier 2's slow real-data-only daily-path extraction (and, "
                              "therefore, the exit optimizer that depends on it)")
    parser.add_argument("--only", type=str, default=None,
                         help="comma-separated stage names to run (plus transitive deps)")
    parser.add_argument("--stop-on-failure", action="store_true",
                         help="abort the whole run on the first failed stage, instead of only blocking its dependents")
    parser.add_argument("--heartbeat-interval", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    gpu = gpu_summary()
    resolved_device = args.device or ("cuda" if gpu["available"] else "cpu")
    jobs = args.jobs or full_resource_jobs(args.ram_per_job_gb)

    catalog = build_catalog(args.include_reports)
    by_name = {s["name"]: s for s in catalog}
    active = set(by_name)
    if args.only:
        requested = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in requested if n not in by_name]
        if unknown:
            parser.error(f"--only: unknown stage(s) {unknown}. Known: {sorted(by_name)}")
        active = _closure(requested, by_name)

    mock_unsafe_paths = set()
    if args.mock_data:
        for s in catalog:
            if s["supports_mock"]:
                mock_unsafe_paths.update(s["outputs"])

    print(f"run_all_strategies: {len(active)}/{len(catalog)} stages active, hardware: {hardware_report()}")
    print(f"device={resolved_device}, jobs={jobs} (ram_per_job_gb={args.ram_per_job_gb}), "
          f"mock_data={args.mock_data}, smoke_test={args.smoke_test}")

    heartbeat = Heartbeat(args.heartbeat_interval)
    state = {}
    for name in [n["name"] for n in catalog]:
        if name not in active:
            continue
        s = by_name[name]

        def eff_deps(s=s):
            if args.mock_data and s["supports_mock"]:
                return []
            return s["deps"]

        blocked = any(d in active and state.get(d) != "done" for d in eff_deps())
        if blocked:
            state[name] = "skipped_blocked"
            print(f"[{name}] skipped: a dependency did not complete successfully")
            _record(name, state="skipped_blocked")
            continue

        if args.skip_daily_paths and name in ("opt_41_daily_option_paths", "opt_42_gpu_exit_optimizer"):
            state[name] = "skipped_manual"
            print(f"[{name}] --skip-daily-paths -- skipping")
            _record(name, state="skipped_manual")
            continue

        if not Path(s["script"]).exists():
            state[name] = "skipped_no_script"
            print(f"[{name}] script not found ({s['script']}) -- skipping")
            continue

        if not args.force and s["outputs"] and all(p.exists() for p in s["outputs"]):
            state[name] = "done"
            print(f"[{name}] output already exists -- skipping")
            _record(name, state="done_previously")
            continue

        if args.mock_data and not s["supports_mock"] and not all(
                p.exists() and p not in mock_unsafe_paths for p in s["outputs"] + [Path(d) for d in []]):
            # a plain (non-mock-capable) stage under --mock-data only runs if its real inputs are
            # already on disk; since this catalog checks OUTPUT existence for skip logic already,
            # a plain stage with no output on disk simply can't produce one without real data.
            if not all(p.exists() for p in s["outputs"]):
                state[name] = "skipped_mock_unsupported"
                print(f"[{name}] skipped: --mock-data requested and this stage has no mock-data path")
                _record(name, state="skipped_mock_unsupported")
                continue

        cmd = build_command(s, args, resolved_device, jobs)
        if args.dry_run:
            state[name] = "done"
            print(f"[{name}] WOULD RUN: {' '.join(cmd)}")
            continue

        ok = run_stage(s, args, resolved_device, jobs, heartbeat)
        state[name] = "done" if ok else "failed"
        if not ok and args.stop_on_failure:
            print(f"\nstopping: {name} failed and --stop-on-failure was set.")
            sys.exit(1)

    print("\n=== run_all_strategies summary ===")
    for name in [n["name"] for n in catalog]:
        if name in active:
            print(f"  {name}: {state.get(name, 'not reached')}")
    if any(state.get(n) == "failed" for n in active):
        print(f"\nfinished with at least one FAILED stage -- see logs/run_all/<stage>.failure.json. "
              f"Re-run this same command to resume (completed stages are skipped).")
        sys.exit(1)
    print(f"\nfinished. per-stage status: {STATUS_PATH}")


if __name__ == "__main__":
    main()
