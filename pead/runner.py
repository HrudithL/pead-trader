"""The backtest runner: take strategy names in, run them in parallel, save and (optionally) report.

    python -m pead run balanced_leverage                 # one strategy
    python -m pead run balanced_leverage options_cap30 # several, in parallel
    python -m pead run equity                              # every equity strategy
    python -m pead run all --jobs 8

Layout of the work: strategies are independent simulations over shared read-only data, so they run
as separate worker PROCESSES (one per strategy, up to the worker count planned from this machine's
cores and RAM). Each worker loads the daily panel once -- from a binary cache after the first run --
pins its BLAS pool to one thread so N workers do not oversubscribe the cores, and runs every strategy
routed to it. The equity and options engines are NumPy-bound: a strategy takes 1-3 seconds, and the
GPU is used only by the data-prep stages (Kelly selector, walk-forward ML), not here.
"""
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from pead.common.hardware import hardware_report, plan_workers
from pead.strategies import registry


def _init_worker(threads):
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[var] = str(threads)


def _run_one(name, report):
    from pead.core.store import save_result
    from pead.strategies.base import RunContext
    t0 = time.time()
    result = registry.get(name).run(RunContext())
    out = save_result(result)
    if report:
        from pead.reporting.single import build_strategy_report
        build_strategy_report(result)
    return name, result.summary, time.time() - t0, str(out)


def run_strategies(names, jobs=None, report=True, verbose=True):
    names = registry.resolve(names)
    plan = plan_workers(len(names), max_workers=jobs)
    if verbose:
        print(f"[runner] {len(names)} strategies on {hardware_report()} -> {plan['workers']} worker(s)")
    results = {}
    if plan["workers"] == 1:
        for n in names:
            name, s, dt, _ = _run_one(n, report)
            results[name] = s
            _print(name, s, dt, verbose)
        return results
    with ProcessPoolExecutor(max_workers=plan["workers"], initializer=_init_worker,
                             initargs=(plan["threads_per_worker"],)) as ex:
        futs = {ex.submit(_run_one, n, report): n for n in names}
        for f in as_completed(futs):
            name, s, dt, _ = f.result()
            results[name] = s
            _print(name, s, dt, verbose)
    return results


def _print(name, s, dt, verbose):
    if verbose:
        print(f"[runner] {name:34s} ret={s['annualized_return']:7.2%} vol={s['annualized_vol']:6.2%} "
              f"sharpe={s['sharpe']:5.2f} maxdd={s['max_drawdown']:7.1%}  ({dt:.0f}s)", flush=True)
