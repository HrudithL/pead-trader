"""Concurrency speedup benchmark for pull_wrds_crsp.py's bounded thread pool.

Answers one question honestly: does running the crsp.dsf/dsi/dsedelist pull
through wrds_pull_lib.MAX_CONCURRENT_CONNECTIONS-wide concurrency actually
buy real wall-clock time over running the same work sequentially -- and does
that hold up once the per-chunk workload is uneven (some years plausibly
have more data than others), not just under an idealized uniform workload?

NO REAL WRDS CONNECTION IS EVER MADE HERE. Every "query" is a FAKE
`db.raw_sql()` call that sleeps for a simulated duration and returns a
trivial one-row DataFrame -- this benchmarks the CONCURRENCY MECHANISM
(the real `ThreadPoolExecutor` + `as_completed` orchestration in
pull_wrds_crsp.main(), completely unmodified), not real WRDS query
performance. The simulated per-task delay (see DEFAULT_DELAY_SECONDS below)
is an illustrative, documented guess approximating typical WRDS query
round-trip latency for a small-to-medium result set -- it is NOT measured
from a live connection, and this script must never be pointed at one.

DEPENDENCY-INJECTION SEAM USED (no source changes needed elsewhere):
pull_wrds_crsp.main() calls `lib.connect()` and reads
`lib.MAX_CONCURRENT_CONNECTIONS` fresh, at call time, on every run -- both
are plain attributes on the `wrds_pull_lib` module object, read dynamically
rather than captured once at import time. That means swapping in a fake
connection factory (this module's `FakeWrdsConnection`) and a different
worker count is just a matter of reassigning `wrds_pull_lib.connect` and
`wrds_pull_lib.MAX_CONCURRENT_CONNECTIONS` before calling
`pull_wrds_crsp.main()` -- exactly the technique tests/test_pull_wrds_crsp.py
already relies on (see its `_connect_factory` helper). No new parameter or
seam had to be added to pull_wrds_crsp.py or wrds_pull_lib.py for this
benchmark: the real orchestration code (the actual ThreadPoolExecutor +
as_completed loop in pull_wrds_crsp.main()) runs completely unmodified,
against a connection object it believes is real.

Usage:
    python scripts/benchmark_concurrency.py

Also exercised (with a much smaller simulated delay, purely to keep the
automated suite fast) by tests/test_concurrency_benchmark.py, which imports
the helpers below directly rather than re-implementing any orchestration.
"""

import contextlib
import datetime as dt
import random
import re
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402
import pull_wrds_crsp as crsp_script  # noqa: E402

def production_max_workers() -> int:
    """The real, configured concurrency cap this project actually ships
    with, read FRESH from `wrds_pull_lib.MAX_CONCURRENT_CONNECTIONS` at
    call time -- never cached once at import time. Caching it at import
    (as a plain module-level constant) would give a false sense of
    security: this benchmark/its tests would keep reporting against
    whatever the setting happened to be when the module was first
    imported, even if the real production setting changed afterward (or
    was temporarily monkeypatched elsewhere in the same test session)."""
    return lib.MAX_CONCURRENT_CONNECTIONS

# Approximating typical WRDS query round-trip latency for a small-to-medium
# result set, based on this project's own prior interactive-WRDS-query
# experience (see memory/pead-trading-project.md's WRDS connection notes) --
# an illustrative, documented guess, NOT a measurement of real WRDS
# behavior, since this benchmark never opens a live connection.
DEFAULT_DELAY_SECONDS = 1.0

_YEAR_RE = re.compile(r"date BETWEEN '(\d{4})-")


def _extract_year(sql: str):
    m = _YEAR_RE.search(sql)
    return int(m.group(1)) if m else None


def make_uniform_delay_fn(delay_seconds: float = DEFAULT_DELAY_SECONDS):
    """The idealized case: every task (each crsp.dsf year-chunk, plus
    crsp.dsi/crsp.dsedelist) takes exactly `delay_seconds`."""

    def _delay_fn(sql: str) -> float:
        return delay_seconds

    return _delay_fn


def make_nonuniform_delay_fn(
    base_delay_seconds: float = DEFAULT_DELAY_SECONDS,
    start_year: int = None,
    end_year: int = None,
):
    """A more realistic per-chunk delay: crsp.dsf's yearly chunks almost
    certainly do NOT all take the same time against a real WRDS connection.
    Within this project's 1996-2013 OptionMetrics-linked universe, later
    years plausibly have more actively-traded/optioned names and heavier
    daily volume than 1995-1996, so a later year's chunk should return (and
    therefore simulate) more rows -- and take more time -- than an earlier
    one.

    This is a rough, DELIBERATELY ILLUSTRATIVE proxy, not a measurement of
    actual per-year crsp.dsf row counts for this project's universe:
    start_year's chunk gets ~0.4x base_delay_seconds, end_year's chunk gets
    ~2.0x, linearly interpolated in between, plus a small +/-15% per-year
    jitter (seeded by the year itself, so re-running this function is
    deterministic) so the ramp isn't perfectly smooth. crsp.dsi/
    crsp.dsedelist (no year in their query, not chunked) get a fixed, light
    half-base delay instead of being forced into the per-year ramp.
    """
    start_year = start_year or dt.date.fromisoformat(lib.LOOKBACK_START_DATE).year
    end_year = end_year or dt.date.fromisoformat(lib.RETURN_END_DATE).year

    def _scale(year: int) -> float:
        frac = 0.0 if end_year == start_year else (year - start_year) / (end_year - start_year)
        ramp = 0.4 + 1.6 * frac
        jitter = random.Random(year).uniform(0.85, 1.15)
        return ramp * jitter

    def _delay_fn(sql: str) -> float:
        year = _extract_year(sql)
        if year is None:
            return base_delay_seconds * 0.5
        return base_delay_seconds * _scale(year)

    return _delay_fn


class FakeWrdsConnection:
    """One instance simulates one WRDS connection. `lib.connect` is
    reassigned (see module docstring) to return a fresh instance of this on
    every call, matching production's "every thread opens its own
    connection" design -- nothing here is shared mutable state across
    instances.

    describe_table() answers instantly: a real WRDS schema-metadata call is
    cheap relative to a big-table data query, so this benchmark doesn't
    simulate latency for it -- only raw_sql() sleeps, standing in for the
    actual data round trip. time.sleep() releases the GIL for its duration,
    which is what makes it a faithful stand-in for real network-bound I/O:
    a real WRDS query would also spend most of its wall-clock time blocked
    on the network/database rather than holding the GIL, so multiple
    threads sleeping concurrently genuinely run in parallel wall-clock-wise
    -- unlike CPU-bound work, which the GIL would serialize regardless of
    thread count.
    """

    def __init__(self, columns, delay_fn):
        self._columns = columns
        self._delay_fn = delay_fn

    def describe_table(self, library, table):
        return pd.DataFrame({"name": self._columns, "type": ["VARCHAR"] * len(self._columns)})

    def raw_sql(self, sql, return_iter=False):
        time.sleep(self._delay_fn(sql))
        select_list = sql.split("FROM")[0].replace("SELECT", "")
        cols = [c.strip() for c in select_list.split(",") if c.strip()]
        row = pd.DataFrame({c: [1] for c in cols})
        return iter([row]) if return_iter else row

    def close(self):
        pass


_ALL_CRSP_COLUMNS = sorted(
    set(lib.CRSP_DSF_REQUIRED_COLUMNS) | set(lib.CRSP_DSF_OPTIONAL_COLUMNS)
    | set(lib.CRSP_DSI_REQUIRED_COLUMNS) | set(lib.CRSP_DSI_OPTIONAL_COLUMNS)
    | set(lib.CRSP_DSEDELIST_REQUIRED_COLUMNS) | set(lib.CRSP_DSEDELIST_OPTIONAL_COLUMNS)
)

N_DSF_YEARS = (
    dt.date.fromisoformat(lib.RETURN_END_DATE).year
    - dt.date.fromisoformat(lib.LOOKBACK_START_DATE).year
    + 1
)
N_TASKS = N_DSF_YEARS + 2  # + crsp.dsi + crsp.dsedelist -- this project's real task count


@contextlib.contextmanager
def _patched_module_attrs(module, **attrs):
    """Minimal save/restore context manager. This benchmark runs as a plain
    script, not under pytest, so it can't use the `monkeypatch` fixture the
    test suite uses for the exact same kind of substitution (see e.g.
    tests/test_pull_wrds_crsp.py's `_patch_crsp_env`).

    Finding 5: refuses (raises) if the target attribute doesn't already
    exist on `module`, rather than silently creating a new one. Without
    this guard, a renamed constant (e.g. RAW_DIR -> RAW_DATA_DIR) would
    silently no-op this benchmark's sandboxing -- the real
    wrds_pull_lib.RAW_DIR would be left untouched while this function
    happily creates and later deletes an unrelated attribute, and this
    benchmark could then write into the REAL data/raw_wrds directory or
    load REAL WRDS credentials via load_env_file instead of the fakes it
    relies on."""
    for name in attrs:
        if not hasattr(module, name):
            raise AttributeError(
                f"refusing to patch {module!r}.{name}: no such attribute "
                f"exists on the real module. Patching it anyway would "
                f"silently CREATE a new attribute rather than substituting "
                f"the real one -- exactly the kind of silent sandboxing "
                f"failure that could let this benchmark touch real "
                f"data/WRDS credentials. If this attribute was renamed in "
                f"the production module, update this call's keyword to match."
            )
    originals = {name: getattr(module, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(module, name, value)
        yield
    finally:
        for name, original in originals.items():
            setattr(module, name, original)


def _write_resolved_ids(metadata_dir: Path, permnos=(1, 2, 3)):
    metadata_dir.mkdir(parents=True, exist_ok=True)
    permno_str = ",".join(str(p) for p in permnos)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": [permno_str], "gvkey": ["001000"], "ibes_ticker": ["AAA"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)


def run_crsp_pull(tmp_root: Path, delay_fn, max_workers: int) -> float:
    """Runs the REAL pull_wrds_crsp.main() orchestration -- its actual
    ThreadPoolExecutor + as_completed mechanism, completely unmodified --
    against fake connections, and returns the measured wall-clock seconds.

    Always passes --force, so every one of this run's tasks is genuinely
    (re-)queried through the fake connection regardless of what's already on
    disk in `tmp_root` -- this is what makes the measured time an honest
    reflection of doing all the (simulated) work, not an idempotency
    short-circuit.
    """
    raw_dir = tmp_root / "raw"
    metadata_dir = tmp_root / "metadata"
    manifest_path = metadata_dir / "wrds_pull_manifest.csv"
    resolved_ids_path = metadata_dir / "resolved_universe_ids.parquet"
    _write_resolved_ids(metadata_dir)

    def _connect():
        return FakeWrdsConnection(_ALL_CRSP_COLUMNS, delay_fn)

    with _patched_module_attrs(
        lib,
        RAW_DIR=raw_dir,
        METADATA_DIR=metadata_dir,
        MANIFEST_PATH=manifest_path,
        RESOLVED_IDS_PATH=resolved_ids_path,
        connect=_connect,
        MAX_CONCURRENT_CONNECTIONS=max_workers,
        load_env_file=lambda path: None,
    ), _patched_module_attrs(sys, argv=["pull_wrds_crsp.py", "--force"]):
        start = time.perf_counter()
        crsp_script.main()
        elapsed = time.perf_counter() - start
    return elapsed


def benchmark(label: str, delay_fn, workdir: Path, concurrent_workers: int = None) -> dict:
    # Read the real production concurrency cap FRESH here (a default
    # argument value is evaluated once, at function-definition time, which
    # would reintroduce the same stale-caching problem this was fixed to
    # avoid) rather than at import time.
    if concurrent_workers is None:
        concurrent_workers = production_max_workers()
    print(f"\n--- {label} ---")
    print(f"Workload: {N_TASKS} tasks ({N_DSF_YEARS} crsp.dsf year-chunks + crsp.dsi + crsp.dsedelist)")

    seq_dir = workdir / "sequential"
    seq_dir.mkdir(parents=True, exist_ok=True)
    seq_time = run_crsp_pull(seq_dir, delay_fn, max_workers=1)
    print(f"Sequential (1 worker, one fake query at a time):  {seq_time:6.2f}s")

    conc_dir = workdir / "concurrent"
    conc_dir.mkdir(parents=True, exist_ok=True)
    conc_time = run_crsp_pull(conc_dir, delay_fn, max_workers=concurrent_workers)
    print(f"Concurrent ({concurrent_workers} workers, real ThreadPoolExecutor): {conc_time:6.2f}s")

    speedup = seq_time / conc_time if conc_time > 0 else float("inf")
    print(f"Speedup: {speedup:.2f}x")
    return {
        "label": label, "sequential_s": seq_time, "concurrent_s": conc_time,
        "workers": concurrent_workers, "speedup": speedup,
    }


def main():
    print(
        "Concurrency benchmark for pull_wrds_crsp.py's real ThreadPoolExecutor\n"
        "orchestration, run against FAKE connections with simulated latency.\n"
        "NO real WRDS connection is made anywhere in this script.\n"
        f"Simulated per-task delay approximates typical WRDS query round-trip\n"
        f"latency for a small-to-medium result set (base: {DEFAULT_DELAY_SECONDS}s/task) --\n"
        f"illustrative, NOT measured from a live connection.\n"
        f"Real, configured production concurrency cap "
        f"(wrds_pull_lib.MAX_CONCURRENT_CONNECTIONS): {production_max_workers()}",
        flush=True,
    )

    results = []
    with tempfile.TemporaryDirectory(prefix="pead_concurrency_benchmark_") as tmp:
        tmp_root = Path(tmp)
        results.append(benchmark(
            f"1. Uniform latency ({DEFAULT_DELAY_SECONDS}s/task for every task)",
            make_uniform_delay_fn(DEFAULT_DELAY_SECONDS),
            tmp_root / "uniform",
        ))
        results.append(benchmark(
            f"2. Non-uniform latency (~{0.4 * DEFAULT_DELAY_SECONDS:.2f}s-"
            f"{2.0 * DEFAULT_DELAY_SECONDS:.2f}s/task, scaled by year -- a more "
            f"realistic proxy for uneven per-year data volume)",
            make_nonuniform_delay_fn(DEFAULT_DELAY_SECONDS),
            tmp_root / "nonuniform",
        ))

    print("\n=== Summary (real measured numbers, simulated I/O latency, no live WRDS connection) ===")
    for r in results:
        print(
            f"{r['label']}\n"
            f"  Sequential: {r['sequential_s']:.2f}s.  "
            f"Concurrent ({r['workers']} workers): {r['concurrent_s']:.2f}s.  "
            f"Speedup: {r['speedup']:.2f}x"
        )
    max_workers = production_max_workers()
    idealized = float(max_workers)
    print(
        f"\nFor reference, the idealized (uniform-workload, zero-overhead) speedup "
        f"bound for {max_workers} workers is {idealized:.1f}x -- a bounded "
        f"thread pool's REAL speedup under an UNEVEN workload (run 2 above) can fall "
        f"meaningfully short of that idealized ratio, since one long straggler task "
        f"can leave other workers idle waiting for it; the numbers above are the "
        f"actual measured results, not an assumption that the idealized ratio holds."
    )


if __name__ == "__main__":
    main()
