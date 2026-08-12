"""Pulls ibes.statsumu_epsus, ibes.actu_epsus, and ibes.detu_epsus, filtered
by the I/B/E/S ticker list resolved by pull_wrds_reference.py. The three
tables are independent of each other and are pulled concurrently.

============================================================================
Run `python scripts/pull_wrds_reference.py` FIRST AND ALONE before this
script. It produces data/metadata/resolved_universe_ids.parquet, which this
script reads to build its ticker filter -- there is nothing to filter by
until that script has finished successfully. If that file doesn't exist
yet, this script exits immediately with a clear error instead of pulling an
unfiltered (or accidentally empty) table.
============================================================================

Once pull_wrds_reference.py has finished, this script is safe to run
concurrently, in its own terminal, alongside pull_wrds_crsp.py and
pull_wrds_compustat.py -- each opens its own WRDS connection(s) and touches
only its own tables under data/raw_wrds/.

If I/B/E/S ticker resolution was skipped upstream (wrdsapps.ibcrsphist
wasn't available when pull_wrds_reference.py ran) -- or the resolved ticker
list fails _validate_ibes_tickers's format allowlist -- this script skips
all three ibes.* pulls cleanly (logged to the manifest), the same way the
original monolithic script degraded just the I/B/E/S family without
aborting CRSP/Compustat. Neither of those pulls need I/B/E/S ticker linkage
at all, so this script never held them hostage even before the split.

WHY ibes.detu_epsus (analyst-level revision detail, the largest of these
three tables) is NOT year-chunked the way crsp.dsf is: crsp.dsf is one row
per PERMNO per TRADING DAY -- daily granularity over ~8,358 names and ~19
years is what makes it this project's single largest table by far.
ibes.detu_epsus, by contrast, is one row per analyst per estimate REVISION
per company per fiscal period -- a company typically gets a handful of
analysts each revising their estimate a handful of times per quarter, not
one row every trading day. Filtering by the resolved I/B/E/S ticker list
(this project's ~8,358-security universe, versus I/B/E/S's much broader
full coverage of tens of thousands of public companies) is also expected to
be a much larger relative cut here than CRSP's date-only chunking problem
(see memory/phase1-equity-pead-plan.md's 2026-08-12 update). Given both of
those, a single filtered query is expected to be tractable; the existing
streaming path (stream=True, pinned schema, atomic writes) already bounds
memory to a small constant regardless of row count, so this is safe even
if that estimate turns out to be optimistic. If a real run shows
ibes.detu_epsus is unexpectedly huge, revisit year-chunking it the same way
as crsp.dsf -- the pull_table()-based per-year pattern in pull_wrds_crsp.py
would carry over directly.

Usage:
    python scripts/pull_wrds_ibes.py [--force]
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402


def _pull_statsumu_epsus(ticker_filter, force, manifest_path):
    db = lib.connect()
    chunk_log = []
    try:
        # NOTE: uses LOOKBACK_START_DATE (not START_DATE) for the same
        # reason CRSP/Compustat do -- rolling-window/history features need
        # a runway before START_DATE, so early-1996 events have a chance at
        # a prior consensus snapshot. statpers is the consensus SNAPSHOT
        # date, not an event date -- intentionally left at END_DATE (not
        # RETURN_END_DATE): extending it would pull in consensus snapshots
        # past the sample cutoff rather than giving an in-sample event more
        # drift-window room, which is what RETURN_END_DATE is for.
        where_sql = (
            f"statpers BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.END_DATE}' "
            f"AND {ticker_filter}"
        )
        lib.pull_table(
            db, chunk_log, "ibes", "statsumu_epsus",
            lib.IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS,
            optional_columns=lib.IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS,
            where_sql=where_sql,
            stream=True,
            start_date=lib.LOOKBACK_START_DATE, end_date=lib.END_DATE,
            manifest_path=manifest_path,
            force=force,
        )
    finally:
        db.close()
    return chunk_log


def _pull_actu_epsus(ticker_filter, force, manifest_path):
    db = lib.connect()
    chunk_log = []
    try:
        # anndats is the earnings ANNOUNCEMENT date -- an actual PEAD
        # trigger event, so (per RETURN_END_DATE's module-level reasoning
        # in wrds_pull_lib.py) it gets the forward buffer like the return
        # series, not just END_DATE.
        where_sql = (
            f"anndats BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.RETURN_END_DATE}' "
            f"AND {ticker_filter}"
        )
        lib.pull_table(
            db, chunk_log, "ibes", "actu_epsus",
            lib.IBES_ACTU_EPSUS_REQUIRED_COLUMNS,
            optional_columns=lib.IBES_ACTU_EPSUS_OPTIONAL_COLUMNS,
            where_sql=where_sql,
            stream=True,
            start_date=lib.LOOKBACK_START_DATE, end_date=lib.RETURN_END_DATE,
            manifest_path=manifest_path,
            force=force,
        )
    finally:
        db.close()
    return chunk_log


def _pull_detu_epsus(ticker_filter, force, manifest_path):
    db = lib.connect()
    chunk_log = []
    try:
        # Analyst-level detail: STDEV/NUMEST from the summary file already
        # cover dispersion and analyst count for most purposes, but this is
        # a one-shot pull, so grab it now rather than come back for it
        # later if a future slice wants revision-level detail.
        #
        # detu_epsus is expected to share det_epsus's column names (IBES
        # detail tables conventionally do between adjusted/unadjusted
        # variants) -- this should still be spot-checked against this
        # account's actual schema on the next real run; most of this
        # table's columns are optional precisely because that hasn't been
        # verified yet (see IBES_DETU_EPSUS_OPTIONAL_COLUMNS).
        # revdats (the estimate REVISION date) is an actual event date for
        # this table's purposes, so it gets the RETURN_END_DATE buffer too,
        # same reasoning as actu_epsus.anndats above.
        where_sql = (
            f"revdats BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.RETURN_END_DATE}' "
            f"AND {ticker_filter}"
        )
        lib.pull_table(
            db, chunk_log, "ibes", "detu_epsus",
            lib.IBES_DETU_EPSUS_REQUIRED_COLUMNS,
            optional_columns=lib.IBES_DETU_EPSUS_OPTIONAL_COLUMNS,
            where_sql=where_sql,
            stream=True,
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

    # Finding 7: this script writes ONLY to its own manifest CSV (a separate
    # OS process from the other 3 scripts, each with its own manifest
    # file) -- see lib.manifest_path_for's docstring for why.
    manifest_path = lib.manifest_path_for("ibes")

    args = lib.parse_common_args(sys.argv[1:], description=__doc__)
    force = args.force

    resolved_ids_path = lib.require_resolved_universe_file()
    try:
        _permnos, _gvkeys, ibes_tickers = lib.load_resolved_filter_lists(resolved_ids_path)
    except lib.UniverseResolutionError as e:
        raise SystemExit(
            f"FATAL: {resolved_ids_path} could not be read -- resolved "
            f"universe file is malformed, re-run "
            f"scripts/pull_wrds_reference.py. ({e})"
        ) from e
    print(f"Loaded {len(ibes_tickers):,} resolved I/B/E/S ticker(s) from {resolved_ids_path}.", flush=True)

    run_log = []

    # Same reasoning as pull_wrds_compustat.py's GVKEY gate: pull_wrds_
    # reference.py already aborts the WHOLE upstream run if I/B/E/S ticker
    # resolution was attempted but came back suspiciously empty, so an
    # empty `ibes_tickers` list reaching this script means resolution was
    # genuinely skipped upstream (wrdsapps.ibcrsphist wasn't available),
    # not a broken join silently sailing through.
    if not ibes_tickers:
        print(
            "I/B/E/S ticker list is empty (ticker resolution was skipped or "
            "unavailable when pull_wrds_reference.py ran) -- none of the "
            "ibes.* Phase C tables will be pulled.",
            flush=True,
        )
        lib.record(
            run_log,
            {"library": "ibes", "table": "statsumu_epsus,actu_epsus,detu_epsus",
             "status": "ibes_ticker_resolution_skipped", "rows": 0, "path": "",
             "missing_columns": "",
             "error": "resolved_universe_ids.parquet has no I/B/E/S tickers "
                      "-- resolution was skipped or unavailable upstream",
             "start_date": "", "end_date": ""},
            manifest_path=manifest_path,
        )
        print(f"\nManifest is at {manifest_path}")
        return

    try:
        lib._validate_ibes_tickers(ibes_tickers)
    except ValueError as e:
        print(
            f"WARNING: I/B/E/S ticker validation failed -- none of the "
            f"ibes.* Phase C tables will be pulled: {e}",
            flush=True,
        )
        lib.record(
            run_log,
            {"library": "ibes", "table": "statsumu_epsus,actu_epsus,detu_epsus",
             "status": "ibes_ticker_validation_failed", "rows": 0, "path": "",
             "missing_columns": "", "error": str(e),
             "start_date": "", "end_date": ""},
            manifest_path=manifest_path,
        )
        print(f"\nManifest is at {manifest_path}")
        return

    ticker_filter = lib.build_string_in_clause("ticker", ibes_tickers)

    tasks = [
        ("ibes.statsumu_epsus", _pull_statsumu_epsus, (ticker_filter, force, manifest_path)),
        ("ibes.actu_epsus", _pull_actu_epsus, (ticker_filter, force, manifest_path)),
        ("ibes.detu_epsus", _pull_detu_epsus, (ticker_filter, force, manifest_path)),
    ]
    n_workers = min(lib.MAX_CONCURRENT_CONNECTIONS, len(tasks))
    print(
        f"Running {len(tasks)} task(s) with up to {n_workers} concurrent "
        f"WRDS connections ...",
        flush=True,
    )

    failures = []
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
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

    print(f"\nManifest is at {manifest_path}")
    if run_log:
        import pandas as pd
        print(pd.DataFrame(run_log).to_string(index=False))

    # Finding 3: also check the collected run_log for any non-success status
    # (e.g. "query_failed", swallowed inside pull_table() and never raised)
    # -- not just executor-level exceptions -- so a run where every query
    # failed can't still print SUCCESS and exit 0.
    run_log_failures = lib.find_run_log_failures(run_log)

    if failures or run_log_failures:
        print(
            f"\n{len(failures) + len(run_log_failures)} of {len(tasks)} "
            f"task(s) FAILED (see above/the manifest for detail; every "
            f"other task still completed):",
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

    print("\nSUCCESS: ibes.statsumu_epsus, ibes.actu_epsus, ibes.detu_epsus pulled.", flush=True)


if __name__ == "__main__":
    main()
