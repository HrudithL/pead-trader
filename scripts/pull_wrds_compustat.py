"""Pulls comp.fundq and comp.funda, filtered by the GVKEY list resolved by
pull_wrds_reference.py. The two tables are independent of each other and
are pulled concurrently.

============================================================================
Run `python scripts/pull_wrds_reference.py` FIRST AND ALONE before this
script. It produces data/metadata/resolved_universe_ids.parquet, which this
script reads to build its GVKEY filter -- there is nothing to filter by
until that script has finished successfully. If that file doesn't exist
yet, this script exits immediately with a clear error instead of pulling an
unfiltered (or accidentally empty) table.
============================================================================

Once pull_wrds_reference.py has finished, this script is safe to run
concurrently, in its own terminal, alongside pull_wrds_crsp.py and
pull_wrds_ibes.py -- each opens its own WRDS connection(s) and touches only
its own tables under data/raw_wrds/.

Compustat is queried under the library alias pull_wrds_reference.py's
one-time db.list_libraries() audit ACTUALLY found on this account (Finding
4) -- comp, compa, or compustat, tried in that order and persisted to
data/metadata/wrds_library_availability.json -- never a hardcoded "comp"
literal, since which alias Compustat is exposed under varies by account/
subscription. If Compustat isn't visible at all, this script skips both
comp.fundq and comp.funda cleanly (logged to the manifest).

If GVKEY resolution was skipped upstream (crsp.ccmxpf_lnkhist wasn't
available when pull_wrds_reference.py ran) -- or the resolved GVKEY list
fails _validate_gvkeys's shape safety net -- this script skips both
comp.fundq and comp.funda cleanly (logged to the manifest), the same way
the original monolithic script degraded just the Compustat family without
aborting CRSP/I-B-E-S. Neither of those pulls need GVKEY linkage at all, so
this script never held them hostage even before the split.

Usage:
    python scripts/pull_wrds_compustat.py [--force]
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402


def _pull_fundq(comp_lib, gvkey_filter, force, manifest_path):
    db = lib.connect()
    chunk_log = []
    try:
        # datadate here gates fundq's rdq (report date of quarterly
        # earnings) -- Compustat's standard PEAD event-date field -- so it
        # gets the RETURN_END_DATE forward buffer for the same reason
        # ibes.actu_epsus.anndats does: standard PEAD practice truncates the
        # holding window at the next earnings announcement, and without
        # this buffer the last couple of in-sample quarters would have no
        # visibility into that next announcement.
        #
        # CAVEAT: Compustat fundamentals are as-CURRENTLY-reported, i.e.
        # restated -- a firm's FY2005 numbers here reflect any later
        # restatement, not what the market actually saw on the original
        # rdq. Do not treat this as point-in-time data without accounting
        # for that; downstream feature work should not assume point-in-time
        # correctness this pull doesn't have.
        fundq_where = (
            f"datadate BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.RETURN_END_DATE}' "
            f"AND {lib.COMPUSTAT_STD_FILTER} AND {gvkey_filter}"
        )
        lib.pull_table(
            db, chunk_log, comp_lib, "fundq",
            lib.COMP_FUNDQ_REQUIRED_COLUMNS,
            optional_columns=lib.COMP_FUNDQ_OPTIONAL_COLUMNS,
            where_sql=fundq_where,
            stream=True,
            start_date=lib.LOOKBACK_START_DATE, end_date=lib.RETURN_END_DATE,
            manifest_path=manifest_path,
            force=force,
        )
    finally:
        db.close()
    return chunk_log


def _pull_funda(comp_lib, gvkey_filter, force, manifest_path):
    db = lib.connect()
    chunk_log = []
    try:
        # funda (annual) is intentionally left at END_DATE, not
        # RETURN_END_DATE: it's lower-priority annual control data
        # (book-to-market, etc.), not itself a quarterly PEAD trigger date
        # the way fundq's rdq is, so it doesn't need the next-announcement
        # forward buffer. Same restated-data caveat as fundq applies here.
        funda_where = (
            f"datadate BETWEEN '{lib.LOOKBACK_START_DATE}' AND '{lib.END_DATE}' "
            f"AND {lib.COMPUSTAT_STD_FILTER} AND {gvkey_filter}"
        )
        lib.pull_table(
            db, chunk_log, comp_lib, "funda",
            lib.COMP_FUNDA_REQUIRED_COLUMNS,
            optional_columns=lib.COMP_FUNDA_OPTIONAL_COLUMNS,
            where_sql=funda_where,
            stream=True,
            start_date=lib.LOOKBACK_START_DATE, end_date=lib.END_DATE,
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
    manifest_path = lib.manifest_path_for("compustat")

    args = lib.parse_common_args(sys.argv[1:], description=__doc__)
    force = args.force

    resolved_ids_path = lib.require_resolved_universe_file()
    try:
        _permnos, gvkeys, _ibes_tickers = lib.load_resolved_filter_lists(resolved_ids_path)
    except lib.UniverseResolutionError as e:
        raise SystemExit(
            f"FATAL: {resolved_ids_path} could not be read -- resolved "
            f"universe file is malformed, re-run "
            f"scripts/pull_wrds_reference.py. ({e})"
        ) from e
    print(f"Loaded {len(gvkeys):,} resolved GVKEY(s) from {resolved_ids_path}.", flush=True)

    # Finding 4: read the ACTUAL resolved Compustat library alias
    # pull_wrds_reference.py's one-time db.list_libraries() audit found
    # (comp/compa/compustat), rather than assuming a hardcoded "comp"
    # literal -- restores the original monolithic script's alias-resolution
    # behavior that the split had silently dropped.
    library_info = lib.load_library_availability()
    comp_lib = library_info.get("comp_lib")
    if not comp_lib:
        print(
            "Compustat library not available on this account (per "
            "data/metadata/wrds_library_availability.json) -- "
            "comp.fundq/comp.funda will NOT be pulled.",
            flush=True,
        )
        run_log = []
        lib.record(
            run_log,
            {"library": "comp", "table": "fundq,funda",
             "status": "comp_library_unavailable", "rows": 0, "path": "",
             "missing_columns": "",
             "error": "wrds_library_availability.json reports no Compustat "
                      "library (comp/compa/compustat) visible on this "
                      "account",
             "start_date": "", "end_date": ""},
            manifest_path=manifest_path,
        )
        print(f"\nManifest is at {manifest_path}")
        return

    run_log = []

    # Fix 3 (original script): comp.fundq/funda need GVKEY resolution to
    # have actually produced GVKEYs. Fix 1 (round 2, original script): a
    # GVKEY that fails _validate_gvkeys's shape safety net degrades this
    # family the same way a missing/empty resolution does -- never crashes
    # this script outright. Neither case is this project's fault to solve
    # here: pull_wrds_reference.py already aborts the WHOLE upstream run if
    # GVKEY resolution was attempted but came back suspiciously empty (see
    # its own FATAL checks), so an empty `gvkeys` list reaching this script
    # means resolution was genuinely skipped upstream (crsp.ccmxpf_lnkhist
    # wasn't available), not a broken join silently sailing through.
    if not gvkeys:
        print(
            "GVKEY list is empty (GVKEY resolution was skipped or unavailable "
            "when pull_wrds_reference.py ran) -- comp.fundq/comp.funda will "
            "NOT be pulled.",
            flush=True,
        )
        lib.record(
            run_log,
            {"library": "comp", "table": "fundq,funda",
             "status": "gvkey_resolution_skipped", "rows": 0, "path": "",
             "missing_columns": "",
             "error": "resolved_universe_ids.parquet has no GVKEYs -- "
                      "resolution was skipped or unavailable upstream",
             "start_date": "", "end_date": ""},
            manifest_path=manifest_path,
        )
        print(f"\nManifest is at {manifest_path}")
        return

    try:
        lib._validate_gvkeys(gvkeys)
    except ValueError as e:
        print(
            f"WARNING: GVKEY validation failed -- comp.fundq/comp.funda will "
            f"NOT be pulled: {e}",
            flush=True,
        )
        lib.record(
            run_log,
            {"library": "comp", "table": "fundq,funda",
             "status": "gvkey_validation_failed", "rows": 0, "path": "",
             "missing_columns": "", "error": str(e),
             "start_date": "", "end_date": ""},
            manifest_path=manifest_path,
        )
        print(f"\nManifest is at {manifest_path}")
        return

    gvkey_filter = lib.build_string_in_clause("gvkey", gvkeys)

    tasks = [
        ("comp.fundq", _pull_fundq, (comp_lib, gvkey_filter, force, manifest_path)),
        ("comp.funda", _pull_funda, (comp_lib, gvkey_filter, force, manifest_path)),
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

    print("\nSUCCESS: comp.fundq, comp.funda pulled.", flush=True)


if __name__ == "__main__":
    main()
