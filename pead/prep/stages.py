"""Data-preparation stages: everything that builds the inputs the strategy backtests read.

A backtest never runs these -- `python -m pead run` only needs their outputs to exist. Rebuilding them
needs the licensed WRDS pull (equity stages) and the OptionMetrics extract (options stages), and they are
the only part of the project that is slow (the options scans stream ~31GB). The stages form a dependency
graph; this runner executes it with the machine's parallelism:

    python -m pead prep --list                  stages, their dependencies, and whether outputs exist
    python -m pead prep --all                   everything not yet built (skips stages whose outputs exist)
    python -m pead prep vol_features            one stage (+ --with-deps to build what it needs first)
    python -m pead prep --all --device cuda --jobs 8

Scheduling: independent stages run concurrently as separate processes; `gpu` stages are serialized to one
at a time (there is one GPU); `io` stages (the per-year OptionMetrics scans) are capped by --io-parallel
because they contend for one disk. Each stage logs to logs/prep/<stage>.log and the run's status is
written to logs/prep_status.json. A stage whose declared outputs all exist is skipped (--force to redo),
so a crashed or interrupted run resumes where it stopped.
"""
import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from pead.common.hardware import cpu_count, hardware_report
from pead.common.paths import DATA_DIR, LOGS_DIR, METADATA_DIR

HERE = Path(__file__).resolve().parent


@dataclass
class Stage:
    name: str
    script: str                       # relative to pead/prep
    outputs: list                     # paths relative to DATA_DIR (or absolute); all must exist to skip
    deps: list = field(default_factory=list)
    kind: str = "cpu"                 # cpu | io | gpu
    flags: tuple = ()                 # which shared flags the script accepts: device, jobs, force
    doc: str = ""


def _o(*names):
    return list(names)


STAGES = [s for s in [
    # ---- equity: events -> deciles -> holding-period cells -> positions -> vol features --------------
    Stage("build_deciles", "equity/build_deciles.py", _o("decile_events.parquet"),
          doc="IBES SUE + CRSP/Compustat -> every earnings event with its within-quarter SUE decile and 1-60d forward returns"),
    Stage("ff_adjustment", "equity/ff_adjustment.py", _o("decile_events_ff_adjusted.parquet"), ["build_deciles"],
          doc="size / book-to-market characteristic adjustment of forward returns"),
    Stage("extended_horizons", "equity/extended_horizons.py", _o("decile_events_extended.parquet"), ["build_deciles"],
          doc="forward returns out to 180 days"),
    Stage("extended_ff_decay", "equity/extended_ff_decay.py", _o("decile_events_ff_adjusted_extended.parquet"),
          ["ff_adjustment", "extended_horizons"], doc="characteristic-adjusted decay of the drift"),
    Stage("daily_decay", "equity/daily_decay.py", _o("event_cells.parquet", "cell_daily_curve.csv"), ["extended_ff_decay"],
          doc="200-day daily decay curve per (decile x size x book/market) cell"),
    Stage("decay_days", "equity/decay_days.py", _o("cell_decay_days.csv"), ["daily_decay"],
          doc="windowed-slope detection of the day each cell's alpha has decayed = its holding period"),
    Stage("positions", "equity/positions.py", _o("positions_rankweighted_v2.parquet"), ["decay_days", "daily_decay"],
          doc="every tradeable event with its entry/exit rows (the universe all equity strategies draw from)"),
    Stage("vol_features", "equity/vol_features.py", _o("equity_vol_panel.parquet"), ["positions"],
          doc="leak-free trailing total/idiosyncratic volatility and prior-quarter vol percentiles per event"),
    # ---- options: calendar -> link -> scan chains -> distributions -> Kelly selection -> forward prices --
    Stage("trading_calendar", "options/trading_calendar.py", [METADATA_DIR / "om_trading_calendar.parquet"], kind="io",
          doc="OptionMetrics trading-day calendar (1996-2013 excl. 2011, whose option file is corrupt)"),
    Stage("link_events_secid", "options/link_events_secid.py", _o("event_options/decile_events_secid_all.parquet"),
          ["trading_calendar"], doc="link CRSP permno -> OptionMetrics secid for every event"),
    Stage("select_entry_contracts", "options/select_entry_contracts.py", _o("event_options/entry_contracts_all.parquet"),
          ["link_events_secid"], "io", ("jobs", "force"), "near-ATM call + put per event (the v1 heuristic / evidence panel)"),
    Stage("forward_option_prices", "options/forward_option_prices.py", _o("event_options/option_event_panel.parquet"),
          ["select_entry_contracts"], "io", ("jobs", "force"), "mid prices of those contracts at +1..60 trading days"),
    Stage("return_distributions", "options/return_distributions.py", [METADATA_DIR / "decile_return_distributions.parquet"],
          ["link_events_secid"], doc="walk-forward empirical forward-return distribution per (quarter, SUE decile)"),
    Stage("scan_full_chain", "options/scan_full_chain.py", _o("event_options/full_chain_all.parquet"),
          ["link_events_secid"], "io", ("jobs", "force"), "every strike of every event's day-0 option chain"),
    Stage("attach_underlying_price", "options/attach_underlying_price.py", _o("event_options/full_chain_with_underlying.parquet"),
          ["scan_full_chain"], "io", ("jobs", "force"), "spot price per event (needed for moneyness and payoffs)"),
    Stage("event_vol_features", "options/event_vol_features.py", _o("event_options/event_vol_features.parquet"),
          ["link_events_secid"], doc="trailing realized vol and ATM implied vol per event"),
    Stage("optimal_contract_selector", "options/optimal_contract_selector.py", _o("event_options/optimal_contracts.parquet"),
          ["attach_underlying_price", "return_distributions"], "gpu", ("device",),
          "Kelly-optimal contract per event, scoring every strike on the GPU"),
    Stage("forward_prices_optimal", "options/forward_prices_optimal.py", _o("event_options/optimal_forward_prices.parquet"),
          ["optimal_contract_selector"], "io", ("jobs", "force"), "forward mid prices of the chosen contracts (the options backtest's input)"),
]]
BY_NAME = {s.name: s for s in STAGES}


def _out_paths(stage):
    return [p if Path(p).is_absolute() else DATA_DIR / p for p in stage.outputs]


def is_done(stage):
    return all(Path(p).exists() for p in _out_paths(stage))


def closure(names):
    seen, order = set(), []

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for d in BY_NAME[n].deps:
            visit(d)
        order.append(n)
    for n in names:
        visit(n)
    return order


def _cmd(stage, device, jobs, force):
    cmd = [sys.executable, str(HERE / stage.script)]
    if "device" in stage.flags:
        cmd += ["--device", device]
    if "jobs" in stage.flags:
        cmd += ["--jobs", str(jobs)]
    if "force" in stage.flags and force:
        cmd += ["--force"]
    return cmd


def run(selected, with_deps=False, force=False, device="auto", jobs=4, io_parallel=2, max_workers=None):
    order = closure(selected) if with_deps else list(selected)
    todo = [n for n in order if force or not is_done(BY_NAME[n])]
    skipped = [n for n in order if n not in todo]
    log_dir = LOGS_DIR / "prep"
    log_dir.mkdir(parents=True, exist_ok=True)
    status = {n: "skipped (outputs exist)" for n in skipped}
    lock, gpu_lock = Lock(), Lock()
    done, failed, running = set(skipped), set(), {}
    max_workers = max_workers or min(cpu_count(), 8)
    print(f"[prep] {hardware_report()}; {len(todo)} stage(s) to run, {len(skipped)} up to date")

    def write_status():
        (LOGS_DIR / "prep_status.json").write_text(json.dumps(status, indent=2))

    def work(name):
        st = BY_NAME[name]
        t0 = time.time()
        guard = gpu_lock if st.kind == "gpu" else None
        if guard:
            guard.acquire()
        try:
            with open(log_dir / f"{name}.log", "w") as fh:
                rc = subprocess.run(_cmd(st, device, jobs, force), stdout=fh, stderr=subprocess.STDOUT).returncode
        finally:
            if guard:
                guard.release()
        return name, rc, time.time() - t0

    pending = list(todo)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {}
        while pending or futs:
            for n in list(pending):
                st = BY_NAME[n]
                blocked = [d for d in st.deps if d in todo and d not in done]
                if any(d in failed for d in st.deps if d in todo):
                    pending.remove(n)
                    failed.add(n)
                    status[n] = "blocked (a dependency failed)"
                    continue
                io_now = sum(BY_NAME[r].kind == "io" for r in futs.values())
                if blocked or (st.kind == "io" and io_now >= io_parallel):
                    continue
                pending.remove(n)
                futs[ex.submit(work, n)] = n
                status[n] = "running"
                print(f"[prep] start  {n}")
            write_status()
            if not futs:
                break
            finished, _ = wait(list(futs), return_when=FIRST_COMPLETED)
            for f in finished:
                n = futs.pop(f)
                name, rc, dt = f.result()
                (done if rc == 0 else failed).add(name)
                status[name] = f"{'done' if rc == 0 else 'FAILED'} in {dt:.0f}s"
                print(f"[prep] {'done ' if rc == 0 else 'FAILED'} {name} ({dt:.0f}s)" + ("" if rc == 0 else f" -> see logs/prep/{name}.log"))
    write_status()
    return not failed


def main(argv):
    ap = argparse.ArgumentParser(prog="python -m pead prep", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--with-deps", action="store_true", help="also build the named stages' missing dependencies")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--jobs", type=int, default=4, help="per-year worker processes inside OptionMetrics scan stages")
    ap.add_argument("--io-parallel", type=int, default=2, help="how many OptionMetrics scan stages may run at once")
    ap.add_argument("--max-workers", type=int, default=None)
    a = ap.parse_args(argv)
    if a.list or not (a.stages or a.all):
        print(f"{'stage':28s} {'kind':4s} {'built':6s} deps -> what it builds")
        for s in STAGES:
            print(f"{s.name:28s} {s.kind:4s} {'yes' if is_done(s) else 'no':6s} {','.join(s.deps) or '-'}  -> {s.doc}")
        return
    names = [s.name for s in STAGES] if a.all else a.stages
    for n in names:
        if n not in BY_NAME:
            ap.error(f"unknown stage {n!r}; see --list")
    ok = run(names, with_deps=a.with_deps or a.all, force=a.force, device=a.device, jobs=a.jobs,
             io_parallel=a.io_parallel, max_workers=a.max_workers)
    sys.exit(0 if ok else 1)
