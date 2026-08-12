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
(wrds_pull_lib.MAX_CONCURRENT_CONNECTIONS workers) -- each worker thread
opens its OWN wrds.Connection() (psycopg2 connections are not safe to share
across threads, so this is never attempted). crsp.dsi and crsp.dsedelist
are small/non-chunked and join the same bounded pool as two more
independent tasks, so all of this script's WRDS work shares one concurrency
cap rather than a separate ad hoc pool per table.

Every year-chunk (and crsp.dsi/crsp.dsedelist) goes through the EXACT SAME
wrds_pull_lib.pull_table() logic as before -- required/optional columns,
schema pinning, atomic writes -- via a per-year `where_sql` date range
combined with the existing PERMNO filter, not a separate/simpler code path.
If one year's chunk (or crsp.dsi/crsp.dsedelist) fails, the others still
run to completion; every task's outcome (success or failure) is collected
via `concurrent.futures.Future.result()` and reported at the end, so a
single failure is visible but never silently swallows or crashes the rest
of the run.

Usage:
    python scripts/pull_wrds_crsp.py [--force]
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import datetime as dt
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402


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


def _pull_dsf_year_chunk(year, chunk_start, chunk_end, permno_filter, force, manifest_path):
    """Worker function for one calendar year of crsp.dsf, run inside the
    bounded thread pool below. Opens its OWN WRDS connection (never shared
    across threads) and closes it before returning, even on failure.
    Returns this chunk's local log list (merged into the run-wide log by
    the caller after all futures complete) so multiple threads never
    append to one shared Python list concurrently.
    """
    db = lib.connect()
    chunk_log = []
    try:
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
        )
    finally:
        db.close()
    return chunk_log


def _pull_dsi(permno_filter, force, manifest_path):
    """crsp.dsi: the market-benchmark counterpart to dsf. Left unfiltered
    by design -- one row per trading day market-wide, already tiny, and
    there is no per-security ID to filter it by. `permno_filter` is
    unused here (kept in the signature so every task submitted to the
    shared thread pool below has the same call shape)."""
    db = lib.connect()
    chunk_log = []
    try:
        lib.pull_table(
            db, chunk_log, "crsp", "dsi",
            lib.CRSP_DSI_REQUIRED_COLUMNS,
            optional_columns=lib.CRSP_DSI_OPTIONAL_COLUMNS,
            where_sql=f"date BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.RETURN_END_DATE}'",
            start_date=lib.LOOKBACK_START_DATE, end_date=lib.RETURN_END_DATE,
            manifest_path=manifest_path,
            force=force,
        )
    finally:
        db.close()
    return chunk_log


def _pull_dsedelist(permno_filter, force, manifest_path):
    db = lib.connect()
    chunk_log = []
    try:
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
        )
    finally:
        db.close()
    return chunk_log


def main():
    lib.load_env_file(lib.REPO_ROOT / ".env")
    lib.RAW_DIR.mkdir(parents=True, exist_ok=True)
    lib.METADATA_DIR.mkdir(parents=True, exist_ok=True)

    args = lib.parse_common_args(sys.argv[1:], description=__doc__)
    force = args.force

    resolved_ids_path = lib.require_resolved_universe_file()
    permnos, _gvkeys, _ibes_tickers = lib.load_resolved_filter_lists(resolved_ids_path)
    permno_filter = lib.build_numeric_in_clause("permno", permnos)
    print(f"Loaded {len(permnos):,} resolved PERMNO(s) from {resolved_ids_path}.", flush=True)

    tasks = []
    for year, chunk_start, chunk_end in _dsf_year_chunks():
        tasks.append((
            f"crsp.dsf ({year})",
            _pull_dsf_year_chunk,
            (year, chunk_start, chunk_end, permno_filter, force, lib.MANIFEST_PATH),
        ))
    tasks.append(("crsp.dsi", _pull_dsi, (permno_filter, force, lib.MANIFEST_PATH)))
    tasks.append(("crsp.dsedelist", _pull_dsedelist, (permno_filter, force, lib.MANIFEST_PATH)))

    print(
        f"Running {len(tasks)} task(s) ({len(tasks) - 2} crsp.dsf year-chunks "
        f"+ crsp.dsi + crsp.dsedelist) with up to "
        f"{lib.MAX_CONCURRENT_CONNECTIONS} concurrent WRDS connections ...",
        flush=True,
    )

    run_log = []
    failures = []
    with ThreadPoolExecutor(max_workers=lib.MAX_CONCURRENT_CONNECTIONS) as executor:
        future_to_label = {
            executor.submit(fn, *fn_args): label for label, fn, fn_args in tasks
        }
        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                chunk_log = future.result()
                run_log.extend(chunk_log)
                print(f"  DONE: {label}", flush=True)
            except Exception as e:
                failures.append((label, e))
                print(f"  FAILED: {label}: {e}", flush=True)

    print("\nManifest is at data/metadata/wrds_pull_manifest.csv")
    if run_log:
        import pandas as pd
        print(pd.DataFrame(run_log).to_string(index=False))

    if failures:
        print(
            f"\n{len(failures)} of {len(tasks)} task(s) FAILED (see above/the "
            f"manifest for detail; every other task still completed):",
            flush=True,
        )
        for label, e in failures:
            print(f"  - {label}: {e}", flush=True)
        raise SystemExit(1)

    print("\nSUCCESS: crsp.dsf (all years), crsp.dsi, crsp.dsedelist pulled.", flush=True)


if __name__ == "__main__":
    main()
