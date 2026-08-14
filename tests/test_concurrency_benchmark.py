"""Automated proof that pull_wrds_crsp.py's bounded thread pool produces a
real wall-clock speedup, not an accidentally-serialized illusion of one.

Reuses scripts/benchmark_concurrency.py's helpers directly (the REAL
pull_wrds_crsp.main() orchestration, a fake connection-factory seam, and the
delay-function builders) rather than re-implementing any orchestration or
timing logic here -- see that module's docstring for the full explanation of
the dependency-injection seam used (reassigning `wrds_pull_lib.connect` /
`wrds_pull_lib.MAX_CONCURRENT_CONNECTIONS`, both already read fresh at call
time by pull_wrds_crsp.main(), so no source changes were needed anywhere).

NO real WRDS connection is made here. time.sleep() is used to simulate
query latency specifically because it releases the GIL -- a faithful stand-
in for real network-bound I/O, unlike CPU-bound work (which the GIL would
serialize regardless of thread count) -- so a passing test here is genuine
evidence of real parallel work, not a GIL-masked illusion of it.

DELAY CHOICE: much smaller than scripts/benchmark_concurrency.py's own
default (1.0s/task, chosen there to be illustrative of real WRDS latency) --
here it's scaled down to keep the automated suite fast. What's asserted is
the SPEEDUP RATIO, not the absolute time, and the ratio is not sensitive to
the absolute delay magnitude (only to relative per-task variance, thread-
pool overhead, and GIL/scheduling noise) -- a smaller delay still produces
the same qualitative result, just faster to observe.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import benchmark_concurrency as bc  # noqa: E402

# Scaled down from benchmark_concurrency.py's illustrative 1.0s default --
# see module docstring above for why this doesn't change what's being
# proven, only how long the test takes to run.
TEST_DELAY_SECONDS = 0.15


def test_uniform_workload_concurrent_path_is_at_least_2x_faster(tmp_path):
    """A safely conservative bound given 4-way concurrency (the idealized
    ratio for a uniform workload is ~4x): tolerant of test-environment
    timing noise (thread-pool startup overhead, scheduler jitter, CI
    machine variance) while still being a real, meaningful assertion that
    the concurrent path is doing genuine parallel work rather than
    accidentally serializing (e.g. via a shared lock, or the GIL if this
    were CPU-bound instead of I/O-bound)."""
    delay_fn = bc.make_uniform_delay_fn(TEST_DELAY_SECONDS)

    sequential_s = bc.run_crsp_pull(tmp_path / "sequential", delay_fn, max_workers=1)
    concurrent_s = bc.run_crsp_pull(tmp_path / "concurrent", delay_fn, max_workers=bc.production_max_workers())

    speedup = sequential_s / concurrent_s
    assert speedup >= 2.0, (
        f"expected the real ThreadPoolExecutor-based concurrent path "
        f"({bc.production_max_workers()} workers) to be at least 2x faster than "
        f"running the identical simulated workload with 1 worker, but measured "
        f"only {speedup:.2f}x (sequential {sequential_s:.2f}s, concurrent "
        f"{concurrent_s:.2f}s) -- this would suggest the tasks are not actually "
        f"running in parallel."
    )


def test_nonuniform_workload_concurrent_path_still_meaningfully_faster(tmp_path):
    """Section 2b's ask: the real crsp.dsf workload isn't 20 uniform-latency
    chunks -- some years plausibly carry more data than others. Confirm the
    thread pool still delivers a REAL speedup under an uneven simulated
    workload, using a looser bound than the uniform case (1.5x, not 2x):
    a bounded pool's benefit can fall short of the idealized N/workers ratio
    when task sizes are uneven (one long straggler chunk can leave other
    workers idle waiting for it), so the bar here is deliberately more
    conservative than the uniform test above."""
    delay_fn = bc.make_nonuniform_delay_fn(TEST_DELAY_SECONDS)

    sequential_s = bc.run_crsp_pull(tmp_path / "sequential", delay_fn, max_workers=1)
    concurrent_s = bc.run_crsp_pull(tmp_path / "concurrent", delay_fn, max_workers=bc.production_max_workers())

    speedup = sequential_s / concurrent_s
    assert speedup >= 1.5, (
        f"expected a real (if smaller-than-idealized) speedup under an uneven "
        f"simulated workload, but measured only {speedup:.2f}x (sequential "
        f"{sequential_s:.2f}s, concurrent {concurrent_s:.2f}s)"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="pead_concurrency_benchmark_test_") as tmp:
        tmp_path = Path(tmp)
        test_uniform_workload_concurrent_path_is_at_least_2x_faster(tmp_path / "uniform")
        test_nonuniform_workload_concurrent_path_still_meaningfully_faster(tmp_path / "nonuniform")
    print("Both concurrency-speedup assertions passed.")
