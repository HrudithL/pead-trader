"""Pulls crsp.dsf (chunked by year, concurrently), crsp.dsi, and
crsp.dsedelist, filtered by the PERMNO list resolved by
pull_wrds_reference.py.

============================================================================
Run `python scripts/pull_wrds_reference.py` FIRST AND ALONE before this
script. It produces data/metadata/resolved_universe_ids.parquet, which this
script reads to build its PERMNO filter -- there is nothing to filter by
until that script has finished successfully. If that file doesn't exist
yet, this script exits immediately with a clear error instead of pulling an
unfiltered (or accidentally empty) table.
============================================================================

Once pull_wrds_reference.py has finished, this script is safe to run
concurrently, in its own terminal, alongside pull_wrds_compustat.py and
pull_wrds_ibes.py -- each opens its own WRDS connection(s) and touches only
its own tables under data/raw_wrds/.

crsp.dsf is this project's single largest table by far (one row per PERMNO
per trading day, ~8,358 names x ~252 trading days/year x ~19 years). To
speed this up, it's split into one query PER CALENDAR YEAR (from
LOOKBACK_START_DATE's year through RETURN_END_DATE's year), each year
written to its own output file (data/raw_wrds/crsp/dsf_<year>.parquet), and
the year-chunks are pulled CONCURRENTLY via a bounded thread pool
(wrds_pull_lib.MAX_CONCURRENT_CONNECTIONS workers). Each WORKER THREAD opens
ONE wrds.Connection() and reuses it across every task that thread happens to
process (Finding 9, review round 8) -- psycopg2 connections are not safe to
share ACROSS threads, but reusing one connection for every task processed BY
THE SAME thread is fine, and avoids the (non-trivial: auth + schema
introspection) cost of opening a fresh WRDS connection per task, of which
there are 22 (20 dsf year-chunks + dsi + dsedelist) but only
MAX_CONCURRENT_CONNECTIONS workers. crsp.dsi and crsp.dsedelist are small/
non-chunked and join the same bounded pool as two more independent tasks, so
all of this script's WRDS work shares one concurrency cap rather than a
separate ad hoc pool per table.

Every year-chunk (and crsp.dsi/crsp.dsedelist) goes through the EXACT SAME
wrds_pull_lib.pull_table() logic as before -- required/optional columns,
schema pinning, atomic writes -- via a per-year `where_sql` date range
combined with the existing PERMNO filter, not a separate/simpler code path.
If one year's chunk (or crsp.dsi/crsp.dsedelist) fails, the others still
run to completion; every task's outcome (success or failure) is collected
via `concurrent.futures.Future.result()` and reported at the end, so a
single failure is visible but never silently swallows or crashes the rest
of the run. A KeyboardInterrupt (Ctrl-C) during the run cancels every
not-yet-started queued task promptly rather than waiting for all 22 to
drain (Finding 10).

--force / re-pull semantics (round 3 review, Fix 1 -- replacing a
regression from the prior round): passing --force does NOT delete any
existing dsf_<year>.parquet chunk (crsp.dsedelist, similarly permno-
filtered, is treated the same way). A prior round did exactly that -- an
upfront delete-all-existing-chunks sweep meant to prevent a partial
--force re-run from splicing together year-chunks pulled under different
universe filters -- and adversarial review found it destroys hours of good
data if the connection then fails partway through (or if every re-pull
legitimately returns zero rows) before any chunk is replaced.

Instead, this script computes a `universe_fingerprint` ONCE per run (a
content hash of data/metadata/resolved_universe_ids.parquet -- see
wrds_pull_lib.compute_universe_fingerprint) and, for every permno-filtered
output (each dsf_<year>.parquet, dsedelist.parquet), compares it against
that chunk's own last-recorded fingerprint in the manifest (see
wrds_pull_lib.determine_chunk_force) BEFORE deciding whether to ask
pull_table() to attempt a re-pull. --force now means "don't trust the
skip-if-exists shortcut for ANY chunk, ask pull_table() to re-verify/
re-pull every one of them" -- the same as a fingerprint mismatch would
trigger on its own, even without --force. Either way, nothing is ever
deleted up front: pull_table()'s existing atomic write-to-temp-then-
os.replace-only-on-success logic is what actually replaces a chunk file,
and it leaves the existing file completely untouched if the re-pull
attempt fails for any reason (connection error, zero rows, etc.).

Usage:
    python scripts/pull_wrds_crsp.py [--force]
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import datetime as dt
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402


class _ConnectionPool:
    """Finding 9: one wrds.Connection() per WORKER THREAD, reused across
    every task that thread processes -- not one connection per task.

    `threading.local()` gives each worker thread its own private `db`
    attribute; the first task a given thread picks up calls `connect_fn()`
    once and caches the result on that thread's local storage, every
    subsequent task on the SAME thread reuses it. `_all` (guarded by
    `_lock`, since multiple threads append to it) tracks every connection
    actually opened so `close_all()` can close every one of them once the
    whole run is done -- connection lifetime is now tied to the POOL/run,
    not to any single task, so nothing else closes them per-task anymore.

    Scoped to one `main()` call (constructed fresh each run, never a module-
    level singleton) so there's no risk of a later run's threads picking up
    a stale connection cached under a coincidentally-reused thread identity.
    """

    def __init__(self, connect_fn):
        self._connect_fn = connect_fn
        self._local = threading.local()
        self._all = []
        self._lock = threading.Lock()

    def get(self):
        if not hasattr(self._local, "db"):
            db = self._connect_fn()
            self._local.db = db
            with self._lock:
                self._all.append(db)
        return self._local.db

    def close_all(self):
        for db in self._all:
            try:
                db.close()
            except Exception:
                pass


def _parse_iso_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _dsf_year_chunks():
    """Yield (year, chunk_start_date, chunk_end_date) for every calendar
    year from LOOKBACK_START_DATE's year through RETURN_END_DATE's year,
    each clipped to [LOOKBACK_START_DATE, RETURN_END_DATE] so the UNION of
    every chunk's date range is exactly identical to the single unchunked
    query's original `date BETWEEN LOOKBACK_START_DATE AND RETURN_END_DATE`
    filter -- neither widened (which would pull rows the original filter
    excluded) nor narrowed (which would silently drop in-range rows).
    """
    lookback_start = _parse_iso_date(lib.LOOKBACK_START_DATE)
    return_end = _parse_iso_date(lib.RETURN_END_DATE)
    for year in range(lookback_start.year, return_end.year + 1):
        chunk_start = max(dt.date(year, 1, 1), lookback_start)
        chunk_end = min(dt.date(year, 12, 31), return_end)
        yield year, chunk_start.isoformat(), chunk_end.isoformat()


def _pull_dsf_year_chunk(
    year, chunk_start, chunk_end, permno_filter, force, universe_fingerprint,
    manifest_path, conn_pool,
):
    """Worker function for one calendar year of crsp.dsf, run inside the
    bounded thread pool below. Uses `conn_pool` to get THIS thread's shared
    connection (Finding 9) -- never opens or closes a connection itself;
    connection lifecycle is owned by the pool for the whole run.
    """
    db = conn_pool.get()
    chunk_log = []
    where_sql = (
        f"date BETWEEN '{chunk_start}' AND '{chunk_end}' AND {permno_filter}"
    )
    lib.pull_table(
        db, chunk_log, "crsp", "dsf",
        lib.CRSP_DSF_REQUIRED_COLUMNS,
        optional_columns=lib.CRSP_DSF_OPTIONAL_COLUMNS,
        where_sql=where_sql,
        out_name=f"dsf_{year}",
        stream=True,
        start_date=chunk_start, end_date=chunk_end,
        manifest_path=manifest_path,
        force=force,
        universe_fingerprint=universe_fingerprint,
        # Fix 2: crsp.dsf is permno-filtered and this project's date range is
        # entirely inside CRSP's real coverage -- a genuine zero-row year is
        # essentially always a disconnected filter/join bug, not a real gap.
        zero_rows_is_suspicious=True,
    )
    return chunk_log


def _pull_dsi(permno_filter, force, universe_fingerprint, manifest_path, conn_pool):
    """crsp.dsi: the market-benchmark counterpart to dsf. Left unfiltered
    by design -- one row per trading day market-wide, already tiny, and
    there is no per-security ID to filter it by. `permno_filter` is
    unused here (kept in the signature so every task submitted to the
    shared thread pool below has the same call shape)."""
    db = conn_pool.get()
    chunk_log = []
    lib.pull_table(
        db, chunk_log, "crsp", "dsi",
        lib.CRSP_DSI_REQUIRED_COLUMNS,
        optional_columns=lib.CRSP_DSI_OPTIONAL_COLUMNS,
        where_sql=f"date BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.RETURN_END_DATE}'",
        start_date=lib.LOOKBACK_START_DATE, end_date=lib.RETURN_END_DATE,
        manifest_path=manifest_path,
        force=force,
        universe_fingerprint=universe_fingerprint,
        # Fix 2: dsi is unfiltered (no per-security ID) -- left as plain "ok"
        # for a zero-row result, unchanged from before this round.
    )
    return chunk_log


def _pull_dsedelist(permno_filter, force, universe_fingerprint, manifest_path, conn_pool):
    db = conn_pool.get()
    chunk_log = []
    lib.pull_table(
        db, chunk_log, "crsp", "dsedelist",
        lib.CRSP_DSEDELIST_REQUIRED_COLUMNS,
        optional_columns=lib.CRSP_DSEDELIST_OPTIONAL_COLUMNS,
        where_sql=(
            f"dlstdt BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.RETURN_END_DATE}' "
            f"AND {permno_filter}"
        ),
        start_date=lib.LOOKBACK_START_DATE, end_date=lib.RETURN_END_DATE,
        manifest_path=manifest_path,
        force=force,
        universe_fingerprint=universe_fingerprint,
        # Fix 2: permno-filtered, substantive table -- same reasoning as dsf.
        zero_rows_is_suspicious=True,
    )
    return chunk_log


def main():
    lib.load_env_file(lib.REPO_ROOT / ".env")
    lib.RAW_DIR.mkdir(parents=True, exist_ok=True)
    lib.METADATA_DIR.mkdir(parents=True, exist_ok=True)

    # Finding 11: a stale dsf.parquet (no year suffix) from any prior
    # (pre-split) monolithic run could silently coexist with the new
    # dsf_<year>.parquet files this script writes -- a downstream
    # directory-level Parquet read (e.g. a pyarrow dataset or
    # pd.read_parquet("data/raw_wrds/crsp/") over the whole folder) would
    # then double-count every row the legacy file contains. Fail loudly and
    # immediately instead of silently proceeding to write year-chunks
    # alongside it.
    legacy_dsf_path = lib.RAW_DIR / "crsp" / "dsf.parquet"
    if legacy_dsf_path.exists():
        raise SystemExit(
            f"FATAL: legacy {legacy_dsf_path} (no year suffix -- from a "
            f"prior monolithic-script run) exists alongside where this "
            f"script writes dsf_<year>.parquet year-chunk files. A "
            f"directory-level Parquet read over data/raw_wrds/crsp/ would "
            f"silently double-count every row in this file. Remove or "
            f"rename it before running this script."
        )

    # Finding 7: this script writes ONLY to its own manifest CSV (a separate
    # OS process from the other 3 scripts, each with its own manifest
    # file) -- see lib.manifest_path_for's docstring for why.
    manifest_path = lib.manifest_path_for("crsp")

    args = lib.parse_common_args(sys.argv[1:], description=__doc__)
    force = args.force

    resolved_ids_path = lib.require_resolved_universe_file()
    try:
        permnos, _gvkeys, _ibes_tickers = lib.load_resolved_filter_lists(resolved_ids_path)
    except lib.UniverseResolutionError as e:
        raise SystemExit(
            f"FATAL: {resolved_ids_path} could not be read -- resolved "
            f"universe file is malformed, re-run "
            f"scripts/pull_wrds_reference.py. ({e})"
        ) from e
    print(f"Loaded {len(permnos):,} resolved PERMNO(s) from {resolved_ids_path}.", flush=True)

    # Finding 5: the same empty-list guard and identifier-shape validation
    # pull_wrds_compustat.py/pull_wrds_ibes.py already have for GVKEY/I-B-E-S
    # ticker, applied here for PERMNO. An empty PERMNO list reaching this
    # point should be structurally impossible (pull_wrds_reference.py's own
    # MIN_PERMNO_RESOLUTION_RATE floor check aborts before ever writing a
    # resolved-ids file with too few/no PERMNOs) -- so unlike the GVKEY/
    # ticker families (where empty is a LEGITIMATE degrade), an empty PERMNO
    # list here means something is fundamentally broken. Refuse to proceed
    # rather than silently building a "permno IN (NULL)" filter that
    # matches nothing and would otherwise look like a successful empty run.
    if not permnos:
        raise SystemExit(
            "FATAL: resolved PERMNO list is empty. "
            "pull_wrds_reference.py's own resolution-rate floor check "
            "should make this impossible -- refusing to proceed with what "
            "would otherwise be a match-nothing 'permno IN (NULL)' filter "
            "that silently pulls zero rows from crsp.dsf/dsi/dsedelist and "
            "could look like a successful empty run. Re-run "
            "scripts/pull_wrds_reference.py and confirm "
            "data/metadata/resolved_universe_ids.parquet is valid."
        )
    try:
        lib._validate_numeric_ids(permnos)
    except ValueError as e:
        raise SystemExit(
            f"FATAL: resolved PERMNO list failed shape validation -- "
            f"refusing to build a query filter from it: {e}"
        ) from e

    permno_filter = lib.build_numeric_in_clause("permno", permnos)

    # Fix 1 (round 3 review, replacing Finding 2's upfront delete sweep from
    # the prior round -- see this file's module docstring for why that sweep
    # was found to be a data-destruction regression). Computed ONCE for the
    # whole run, then compared per-chunk against each chunk's own manifest
    # history via lib.determine_chunk_force -- never deletes anything;
    # only decides what `force` value pull_table() receives for that one
    # chunk. A mismatch (or no recorded history) behaves like --force for
    # that specific chunk; a match lets the normal skip-if-exists apply.
    universe_fingerprint = lib.compute_universe_fingerprint(resolved_ids_path)

    def _permno_filtered_chunk_force(out_name: str) -> bool:
        out_path = lib.RAW_DIR / "crsp" / f"{out_name}.parquet"
        return lib.determine_chunk_force(out_path, manifest_path, universe_fingerprint, force)

    tasks = []
    for year, chunk_start, chunk_end in _dsf_year_chunks():
        chunk_force = _permno_filtered_chunk_force(f"dsf_{year}")
        tasks.append((
            f"crsp.dsf ({year})",
            _pull_dsf_year_chunk,
            (year, chunk_start, chunk_end, permno_filter, chunk_force, universe_fingerprint, manifest_path),
        ))
    # crsp.dsi is unfiltered (no permno_filter at all), so there is no
    # "universe changed" story for it -- its force value is simply whatever
    # the user passed on the command line, same as every script before this
    # fix.
    tasks.append((
        "crsp.dsi", _pull_dsi,
        (permno_filter, force, universe_fingerprint, manifest_path),
    ))
    dsedelist_force = _permno_filtered_chunk_force("dsedelist")
    tasks.append((
        "crsp.dsedelist", _pull_dsedelist,
        (permno_filter, dsedelist_force, universe_fingerprint, manifest_path),
    ))

    print(
        f"Running {len(tasks)} task(s) ({len(tasks) - 2} crsp.dsf year-chunks "
        f"+ crsp.dsi + crsp.dsedelist) with up to "
        f"{lib.MAX_CONCURRENT_CONNECTIONS} concurrent WRDS connections "
        f"(one per worker thread, reused across every task that thread "
        f"processes) ...",
        flush=True,
    )

    conn_pool = _ConnectionPool(lib.connect)
    run_log = []
    failures = []
    executor = ThreadPoolExecutor(max_workers=lib.MAX_CONCURRENT_CONNECTIONS)
    try:
        future_to_label = {
            executor.submit(fn, *fn_args, conn_pool): label for label, fn, fn_args in tasks
        }
        try:
            for future in as_completed(future_to_label):
                label = future_to_label[future]
                try:
                    chunk_log = future.result()
                    run_log.extend(chunk_log)
                    print(f"  DONE: {label}", flush=True)
                except Exception as e:
                    failures.append((label, e))
                    print(f"  FAILED: {label}: {e}", flush=True)
        except KeyboardInterrupt:
            # Finding 10: without this, the ThreadPoolExecutor context
            # manager's default __exit__ behavior waits for every already-
            # submitted task (all 22, including ones not yet started) to
            # drain before letting the process exit -- a Ctrl-C would
            # appear to hang and then silently let hours of remaining work
            # run to completion anyway. cancel_futures=True cancels every
            # not-yet-started queued task immediately; wait=False means we
            # don't block here waiting for the (small number of) already-
            # running tasks to finish either.
            print(
                "\nKeyboardInterrupt received -- cancelling every "
                "not-yet-started queued task and exiting promptly rather "
                "than waiting for the remaining year-chunks to drain ...",
                flush=True,
            )
            executor.shutdown(wait=False, cancel_futures=True)
            raise
    finally:
        conn_pool.close_all()
    executor.shutdown(wait=True)

    print(f"\nManifest is at {manifest_path}")
    if run_log:
        import pandas as pd
        print(pd.DataFrame(run_log).to_string(index=False))

    # Finding 3: a table/chunk whose pull_table() call logged a non-success
    # status (e.g. "query_failed") never raises -- pull_table swallows
    # query-level failures into the manifest by design -- so it would never
    # show up in `failures` above (which only catches an executor-level
    # exception, e.g. lib.connect() itself raising). Check the collected
    # run_log too, so a run where every query failed can't still print
    # SUCCESS and exit 0.
    run_log_failures = lib.find_run_log_failures(run_log)

    if failures or run_log_failures:
        print(
            f"\n{len(failures) + len(run_log_failures)} of {len(tasks)} "
            f"task/chunk(s) FAILED (see above/the manifest for detail; "
            f"every other task still completed):",
            flush=True,
        )
        for label, e in failures:
            print(f"  - {label}: {e}", flush=True)
        for row in run_log_failures:
            print(
                f"  - {row.get('library')}.{row.get('table')}: "
                f"status={row.get('status')} error={row.get('error')}",
                flush=True,
            )
        raise SystemExit(1)

    print("\nSUCCESS: crsp.dsf (all years), crsp.dsi, crsp.dsedelist pulled.", flush=True)


if __name__ == "__main__":
    main()
