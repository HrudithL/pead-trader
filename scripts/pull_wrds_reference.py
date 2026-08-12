"""Phase A + B of the WRDS bulk pull for pead-trading: pulls the small
reference/linking tables, then resolves the OptionMetrics universe to CRSP
PERMNO / Compustat GVKEY / I/B/E/S ticker, purely locally.

============================================================================
RUN THIS SCRIPT FIRST, AND ALONE. Do not start pull_wrds_crsp.py,
pull_wrds_compustat.py, or pull_wrds_ibes.py until this one has finished
successfully. Those three scripts filter every big-table pull by the
PERMNO/GVKEY/I-B-E-S-ticker lists this script resolves and writes to
data/metadata/resolved_universe_ids.parquet -- there is nothing to filter
by until this script has produced that file. This is the one real
dependency in the whole split-scripts design (see
memory/phase1-equity-pead-plan.md's "Split Into Parallel Scripts" section):
it cannot be parallelized away.
============================================================================

Concretely, this script:
  1. (Phase A) Pulls crsp.stocknames, comp.company, wrdsapps.ibcrsphist,
     crsp.ccmxpf_lnkhist, and ibes.idsum -- unfiltered, exactly as the
     original monolithic pull_wrds_data.py did. These ARE the resolution
     mechanism Phase B uses, or are already small regardless (tens of
     thousands of rows) -- no universe filter needed or possible yet.
  2. (Phase B) Resolves data/metadata/optionmetrics_universe*.parquet
     (built elsewhere, not touched here) against those Phase A tables via
     wrds_pull_lib.resolve_universe(), writing
     data/metadata/resolved_universe_ids.parquet plus the audit CSVs
     (unresolved_secids.csv, ambiguous_links.csv,
     permnos_without_gvkey.csv, permnos_without_ibes_ticker.csv).

None of the pull/resolution LOGIC changed as part of this split -- every
fix from 7 rounds of review (schema pinning, atomic writes, identifier
validation, per-identifier-family degrade, the absolute universe-size
floor, the resolution-rate floor) lives unchanged in
scripts/wrds_pull_lib.py and is only called from here, not reimplemented.

This is meant to run ONCE. Re-running is idempotent by default:
wrds_pull_lib.pull_table() skips any table whose output Parquet file
already exists (logged as "skipped_exists"), unless --force is passed.

Usage:
    python scripts/pull_wrds_reference.py [--force]
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrds_pull_lib as lib  # noqa: E402


def main():
    print(
        "=" * 78 + "\n"
        "pull_wrds_reference.py -- Phase A (reference tables) + Phase B "
        "(local universe resolution)\n"
        "This MUST be run first and alone. Do not start pull_wrds_crsp.py / "
        "pull_wrds_compustat.py / pull_wrds_ibes.py until this script "
        "prints a success message below.\n" + "=" * 78,
        flush=True,
    )

    lib.load_env_file(lib.REPO_ROOT / ".env")
    lib.RAW_DIR.mkdir(parents=True, exist_ok=True)
    lib.METADATA_DIR.mkdir(parents=True, exist_ok=True)

    # Finding 7: this script writes ONLY to its own manifest CSV (a separate
    # OS process from the 3 consumer scripts, each of which also gets its
    # own manifest file) -- see lib.manifest_path_for's docstring for why.
    manifest_path = lib.manifest_path_for("reference")

    args = lib.parse_common_args(sys.argv[1:], description=__doc__)
    force = args.force

    # Finding 1: resolve_universe() below writes the resolved-IDs Parquet to
    # a TEMP path, not the final data/metadata/resolved_universe_ids.parquet
    # path the 3 consumer scripts check for -- that final path is only ever
    # populated (via os.replace, atomically) AFTER every one of this
    # script's own floor/canary checks below has passed. Before this fix,
    # resolve_universe() wrote directly to the final path, so an aborted run
    # (a FATAL floor-check failure AFTER resolution already ran) still left
    # a fully-formed file at the path the consumer scripts' guard
    # (require_resolved_universe_file) checks with a bare path.exists() --
    # i.e. a run this script itself judged unsafe could still let the 3
    # consumer scripts proceed. Any SystemExit raised below (from this
    # point on) leaves tmp_resolved_ids_path behind (harmless -- nothing
    # checks that path) and the FINAL path completely untouched (still
    # whatever a prior successful run left there, if anything).
    final_resolved_ids_path = lib.METADATA_DIR / "resolved_universe_ids.parquet"
    tmp_resolved_ids_path = final_resolved_ids_path.with_suffix(
        final_resolved_ids_path.suffix + ".tmp"
    )

    # Lower-priority fix (round 3 review): the audit CSVs used to be written
    # straight to their final paths inside resolve_universe(), NOT gated
    # behind the same atomic-promotion-after-checks-pass logic as the main
    # resolved-ids Parquet above -- so a failed run could leave a mismatched
    # set of audit files sitting next to a STALE (from a prior successful
    # run) resolved-ids file. Same tmp-path-then-os.replace-together pattern
    # as tmp_resolved_ids_path: every one of these is written to a `.tmp`
    # path by resolve_universe() below, and only promoted to its final path
    # (all four together, right alongside the resolved-ids file) after every
    # floor/canary/failure check has passed.
    audit_csv_final_paths = {
        "unresolved_secids": lib.METADATA_DIR / "unresolved_secids.csv",
        "ambiguous_links": lib.METADATA_DIR / "ambiguous_links.csv",
        "permnos_without_gvkey": lib.METADATA_DIR / "permnos_without_gvkey.csv",
        "permnos_without_ibes_ticker": lib.METADATA_DIR / "permnos_without_ibes_ticker.csv",
    }
    audit_csv_tmp_paths = {
        name: path.with_suffix(path.suffix + ".tmp")
        for name, path in audit_csv_final_paths.items()
    }

    # Fix 4: absolute-count floor on the universe file, checked BEFORE
    # connecting to WRDS at all -- see _check_universe_secid_floor's
    # docstring for why the existing RATE-based floor
    # (MIN_PERMNO_RESOLUTION_RATE, checked later, after resolution) isn't
    # enough on its own: a truncated/corrupted universe file that happens to
    # resolve at 100% would sail straight past a rate-only check.
    try:
        lib._check_universe_secid_floor(lib.METADATA_DIR / "optionmetrics_universe.parquet")
    except lib.UniverseResolutionError as e:
        print(
            f"FATAL: universe file failed the absolute-count floor check -- "
            f"aborting before connecting to WRDS or attempting any "
            f"resolution: {e}",
            flush=True,
        )
        raise SystemExit(1) from e

    db = lib.connect()
    log = []

    try:
        # --- One-time library audit ---
        libraries = sorted(db.list_libraries())
        (lib.METADATA_DIR / "wrds_available_libraries.txt").write_text(
            "\n".join(libraries) + "\n"
        )
        print(
            f"{len(libraries)} libraries visible. "
            f"Saved to data/metadata/wrds_available_libraries.txt",
            flush=True,
        )

        have_ibes = "ibes" in libraries
        have_crsp = "crsp" in libraries
        comp_candidates = [lib_name for lib_name in ("comp", "compa", "compustat") if lib_name in libraries]
        have_comp = bool(comp_candidates)
        comp_lib = comp_candidates[0] if have_comp else None
        # Deliberately not checking for optionm/OptionMetrics here -- options
        # data comes from the external drive archive, per this project's
        # established data-source split.

        # Finding 4: persist this account's ACTUAL resolved library
        # alias(es) -- most importantly comp_lib, since Compustat can be
        # exposed as comp/compa/compustat depending on the account/
        # subscription -- so pull_wrds_compustat.py (a separate process,
        # with no access to the comp_lib variable computed above) reads the
        # real resolved name instead of assuming a hardcoded "comp" literal.
        # Written unconditionally (even if a library is unavailable, in
        # which case comp_lib is None and the compustat script degrades
        # cleanly -- see its own gate) -- this file doesn't gate anything
        # sensitive by itself, since every consumer script still requires
        # resolved_universe_ids.parquet (gated, see Finding 1 above) first.
        lib.write_library_availability({
            "have_ibes": have_ibes, "have_crsp": have_crsp, "have_comp": have_comp,
            "comp_lib": comp_lib,
        })

        for lib_name, available in (("ibes", have_ibes), ("crsp", have_crsp), ("comp", have_comp)):
            if not available:
                print(f"{lib_name} library not visible on this account -- skipping.", flush=True)
                lib.record(
                    log,
                    {"library": lib_name, "table": "*", "status": "library_unavailable",
                     "rows": 0, "path": "", "missing_columns": "", "error": "",
                     "start_date": "", "end_date": ""},
                    manifest_path=manifest_path,
                )

        # =====================================================================
        # Phase A -- small reference/linking tables, pulled unfiltered (as
        # before). These ARE the resolution mechanism Phase B uses below, or
        # are already small regardless (tens of thousands of rows) -- no
        # universe filter needed or possible yet at this point.
        # =====================================================================
        if have_crsp:
            # siccd is the point-in-time SIC code (unlike comp.company.sic
            # below, which is the firm's current/most-recent classification
            # only) -- needed for historically-accurate sector attribution,
            # but downgraded to optional here (it's still useful even if
            # missing, unlike the identity/validity-window columns).
            lib.pull_table(
                db, log, "crsp", "stocknames",
                lib.CRSP_STOCKNAMES_REQUIRED_COLUMNS,
                optional_columns=lib.CRSP_STOCKNAMES_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                manifest_path=manifest_path,
                force=force,
            )
        if have_comp:
            lib.pull_table(
                db, log, comp_lib, "company",
                lib.COMP_COMPANY_REQUIRED_COLUMNS,
                optional_columns=lib.COMP_COMPANY_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                manifest_path=manifest_path,
                force=force,
            )
        # wrdsapps.iclink was removed: confirmed via a prior run's manifest
        # that it doesn't exist on this account (no_such_table) -- dead code.
        if have_ibes and have_crsp:
            lib.pull_table(
                db, log, "wrdsapps", "ibcrsphist",
                lib.WRDSAPPS_IBCRSPHIST_REQUIRED_COLUMNS,
                optional_columns=lib.WRDSAPPS_IBCRSPHIST_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                manifest_path=manifest_path,
                force=force,
            )
        if have_crsp and have_comp:
            lib.pull_table(
                db, log, "crsp", "ccmxpf_lnkhist",
                lib.CRSP_CCMXPF_LNKHIST_REQUIRED_COLUMNS,
                optional_columns=lib.CRSP_CCMXPF_LNKHIST_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                manifest_path=manifest_path,
                force=force,
            )
        if have_ibes:
            lib.pull_table(
                db, log, "ibes", "idsum",
                lib.IBES_IDSUM_REQUIRED_COLUMNS,
                optional_columns=lib.IBES_IDSUM_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                manifest_path=manifest_path,
                force=force,
            )

        # =====================================================================
        # Phase B -- local universe resolution (no WRDS query). Resolves the
        # ~8,358-secid OptionMetrics universe (data/metadata/
        # optionmetrics_universe*.parquet, built elsewhere, not touched here)
        # to CRSP PERMNO / Compustat GVKEY / I/B/E/S ticker using the Phase A
        # tables just pulled above. See resolve_universe's docstring for the
        # full join logic and the mandatory auditability outputs.
        #
        # A failure in the PERMNO resolution stage (missing input file,
        # unexpected schema) MUST abort the whole run before writing
        # anything the three consumer scripts (which run after this one)
        # would trust -- a broken resolution step must never silently fall
        # through to an empty filter that then pulls either the entire
        # market or nothing. GVKEY/I/B/E/S ticker resolution are each
        # allowed to degrade independently instead (Fix 3).
        # =====================================================================
        try:
            # Finding 1: writes to the TEMP path, not final_resolved_ids_path
            # -- see this function's top-of-main comment for why.
            resolved = lib.resolve_universe(
                om_universe_path=lib.METADATA_DIR / "optionmetrics_universe.parquet",
                om_universe_history_path=lib.METADATA_DIR / "optionmetrics_universe_history.parquet",
                stocknames_path=lib.RAW_DIR / "crsp" / "stocknames.parquet",
                ccmxpf_lnkhist_path=lib.RAW_DIR / "crsp" / "ccmxpf_lnkhist.parquet",
                ibcrsphist_path=lib.RAW_DIR / "wrdsapps" / "ibcrsphist.parquet",
                # Lower-priority fix: these four audit CSVs now also go to
                # TEMP paths, promoted together with tmp_resolved_ids_path
                # only after every check below passes -- see this function's
                # top-of-main comment for audit_csv_tmp_paths.
                unresolved_secids_out=audit_csv_tmp_paths["unresolved_secids"],
                ambiguous_links_out=audit_csv_tmp_paths["ambiguous_links"],
                resolved_ids_out=tmp_resolved_ids_path,
                permnos_without_gvkey_out=audit_csv_tmp_paths["permnos_without_gvkey"],
                permnos_without_ibes_ticker_out=audit_csv_tmp_paths["permnos_without_ibes_ticker"],
            )
        except lib.UniverseResolutionError as e:
            print(
                f"FATAL: universe resolution failed -- aborting. The three "
                f"consumer scripts (pull_wrds_crsp.py/pull_wrds_compustat.py/"
                f"pull_wrds_ibes.py) must NOT be run: {e}",
                flush=True,
            )
            raise SystemExit(1) from e

        # This project's documented expectation is ~99.99% secid-to-PERMNO
        # resolution once real CRSP data is pulled -- MIN_PERMNO_RESOLUTION_
        # RATE (90%) is a floor far below that, so it only fires on a
        # systemic break (not ordinary real-world linking gaps).
        resolution_rate = resolved.n_secids_resolved / resolved.n_secids_total if resolved.n_secids_total else 0.0
        if resolution_rate < lib.MIN_PERMNO_RESOLUTION_RATE:
            print(
                f"FATAL: secid-to-PERMNO resolution rate {resolution_rate:.1%} "
                f"is below the {lib.MIN_PERMNO_RESOLUTION_RATE:.0%} floor -- "
                f"aborting. The three consumer scripts must NOT be run. This "
                f"is far below this project's ~99.99% documented expectation "
                f"and points at a systemic linking failure, not ordinary "
                f"real-world gaps.",
                flush=True,
            )
            raise SystemExit(1)

        # GVKEY/ticker resolution are each allowed to degrade independently
        # (Fix 3) -- but if the linking table WAS available (not skipped)
        # and resolution still came back with zero GVKEYs/tickers, that's a
        # broken join, not a genuine empty result, and must still abort the
        # whole run (not just the Compustat/I-B-E-S consumer scripts): this
        # mirrors the original monolithic script's behavior exactly, which
        # treated this as a whole-run canary of a broken resolution, not a
        # per-table degrade.
        if not resolved.gvkey_resolution_skipped and not resolved.gvkeys:
            print(
                "FATAL: GVKEY resolution was attempted (crsp.ccmxpf_lnkhist "
                "was available) but produced ZERO GVKEYs -- this points at a "
                "broken join, not a genuinely empty universe. Aborting. The "
                "three consumer scripts must NOT be run.",
                flush=True,
            )
            raise SystemExit(1)
        if not resolved.ibes_ticker_resolution_skipped and not resolved.ibes_tickers:
            print(
                "FATAL: I/B/E/S ticker resolution was attempted "
                "(wrdsapps.ibcrsphist was available) but produced ZERO "
                "tickers -- this points at a broken join, not a genuinely "
                "empty universe. Aborting. The three consumer scripts must "
                "NOT be run.",
                flush=True,
            )
            raise SystemExit(1)

        # Fix 3 (round 3 review): pull_wrds_reference.py was previously
        # exempt from the "did any pull actually fail" check the three
        # consumer scripts already apply to themselves -- resolve_universe()
        # can complete and every floor/canary check above can pass using
        # ONLY the Phase A tables that pulled correctly, silently masking a
        # real Phase A failure (query_failed, missing_required_columns,
        # describe_table_error, or the new ok_suspicious_zero_rows from Fix
        # 2). Checked here, inside this same try block, so a failure gets
        # identical treatment to the floor/canary checks above: cleanup via
        # the except clause below, and neither the resolved-ids file nor the
        # audit CSVs are ever promoted. "library_unavailable" (a library
        # simply not being on this account) is correctly NOT a failure here
        # -- see SUCCESS_STATUSES's docstring in wrds_pull_lib.py.
        run_log_failures = lib.find_run_log_failures(log)
        if run_log_failures:
            print(
                f"FATAL: {len(run_log_failures)} Phase A table pull(s) did "
                f"not succeed -- refusing to promote "
                f"{final_resolved_ids_path} or declare success, even though "
                f"universe resolution itself may have completed using only "
                f"the tables that DID pull correctly. The three consumer "
                f"scripts must NOT be run:",
                flush=True,
            )
            for row in run_log_failures:
                print(
                    f"  - {row.get('library')}.{row.get('table')}: "
                    f"status={row.get('status')} error={row.get('error')}",
                    flush=True,
                )
            raise SystemExit(1)
    except BaseException:
        # Finding 1: best-effort cleanup of the temp resolved-ids file (and,
        # lower-priority fix, the temp audit CSVs) on ANY abort path
        # (including the SystemExit(1) calls above, which are BaseException,
        # not Exception) -- not required for correctness (nothing else ever
        # checks these tmp paths' existence), just tidiness so a failed run
        # doesn't leave debris a human might mistake for something
        # meaningful.
        if tmp_resolved_ids_path.exists():
            tmp_resolved_ids_path.unlink(missing_ok=True)
        for tmp_path in audit_csv_tmp_paths.values():
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        raise
    finally:
        db.close()
        print("Connection closed.", flush=True)

    # Finding 1: every floor/canary check above has now passed -- ONLY now
    # is the resolved-IDs file promoted (atomically, via os.replace) to the
    # final path the 3 consumer scripts' require_resolved_universe_file()
    # guard checks (including, this round, Fix 3's run_log_failures check).
    # If anything above raised, this line is never reached, so
    # final_resolved_ids_path is left exactly as it was before this run
    # (still absent, or still whatever a prior successful run produced).
    # The four audit CSVs are promoted together with it, for the same
    # reason (lower-priority fix): a failed run must not leave a mismatched
    # set of audit files sitting next to a stale prior-run resolved-ids
    # file.
    os.replace(tmp_resolved_ids_path, final_resolved_ids_path)
    for name, tmp_path in audit_csv_tmp_paths.items():
        os.replace(tmp_path, audit_csv_final_paths[name])

    # --- Summary (the manifest itself was already written incrementally,
    # row by row, above -- this just prints what this run did) ---
    print(f"\nManifest is at {manifest_path}")
    if log:
        print(pd.DataFrame(log).to_string(index=False))

    print(
        "\n" + "=" * 78 + "\n"
        "SUCCESS: reference tables pulled and universe resolved. "
        "data/metadata/resolved_universe_ids.parquet is ready.\n"
        "It is now safe to run these three scripts IN PARALLEL (separate "
        "terminals):\n"
        "  python scripts/pull_wrds_crsp.py\n"
        "  python scripts/pull_wrds_compustat.py\n"
        "  python scripts/pull_wrds_ibes.py\n" + "=" * 78,
        flush=True,
    )


if __name__ == "__main__":
    main()
