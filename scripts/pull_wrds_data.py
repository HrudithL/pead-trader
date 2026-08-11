"""
One-time WRDS bulk pull for pead-trading.

Pulls I/B/E/S (analyst estimates + actuals), CRSP (returns, market benchmark,
delistings, identifiers), and Compustat (fundamentals, lower priority) for
1996-01-01 through 2013-12-31 -- the OptionMetrics archive's native window on
the external drive, so the earnings/estimates side and the options side line
up on the same period for the first time.

Deliberately does NOT check for or pull OptionMetrics/options data. This WRDS
account does not have that access, and options data comes from the external
drive archive instead. Do not add an optionm check here without confirming
that's actually changed.

This is meant to run ONCE. Every table lands under data/raw_wrds/<library>/ as
Parquet, gitignored, so this should not need to run again except for a
deliberate, explicit refresh -- per the standing "minimize WRDS queries" rule.
Re-running is idempotent by default: pull_table() skips any table whose output
Parquet file already exists (logged as "skipped_exists"), unless --force is
passed on the command line. That way a re-run after a partial failure doesn't
re-query tables that already succeeded.

Column lists are checked against the account's actual schema via
db.describe_table() before each query, rather than assumed blind, since exact
column/table names can vary slightly by WRDS account vintage. Any table or
column that doesn't match is logged and skipped rather than crashing the run,
so one wrong name doesn't take down an otherwise-successful pull. A small
subset of columns per table is marked "required": if any of those are
missing, the pull is skipped entirely (logged as "missing_required_columns")
rather than silently proceeding with a crippled column set, since some
columns (join keys, point-in-time validity bounds) would silently corrupt a
downstream join or calculation if quietly dropped.

Every table's outcome -- success, skip, or failure -- is appended to
data/metadata/wrds_pull_manifest.csv immediately after that table's attempt,
not buffered in memory until the end, so an interrupted run still leaves an
accurate manifest for everything it got through.

Usage:
    python scripts/pull_wrds_data.py [--force]
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import builtins
import csv
import datetime as dt
import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import sqlalchemy.exc as sa_exc

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw_wrds"
METADATA_DIR = REPO_ROOT / "data" / "metadata"
MANIFEST_PATH = METADATA_DIR / "wrds_pull_manifest.csv"

# Matches the OptionMetrics archive's native coverage (verified in
# memory/pead-trading-spec.md section 6b: 1996-2013, not 1997-2013).
START_DATE = "1996-01-01"
END_DATE = "2013-12-31"
# One year of lookback so rolling-window features (volatility, momentum) have
# history even for events near the start of the sample.
LOOKBACK_START_DATE = "1995-01-01"
# CRSP return data needs a buffer PAST END_DATE too: an I/B/E/S event dated
# right at END_DATE otherwise gets zero post-event return days, truncating
# the drift window for the last several months of events. ~6 months covers a
# 60-trading-day forward window with room to spare. Only used for the return
# series themselves (crsp.dsf/dsi/dsedelist) -- event filters (I/B/E/S
# statpers/anndats/revdats) and Compustat still use END_DATE.
RETURN_END_DATE = "2014-06-30"

# Standard Compustat filter for ordinary industrial/domestic/consolidated
# statements. Compustat stores multiple rows per gvkey/datadate across
# indfmt/datafmt/popsrc/consol combinations; without this filter fundq/funda
# pulls contain duplicate, unusable rows.
COMPUSTAT_STD_FILTER = "indfmt='INDL' AND datafmt='STD' AND popsrc='D' AND consol='C'"

MANIFEST_FIELDNAMES = [
    "library", "table", "status", "rows", "path", "missing_columns", "error",
    "pulled_at", "start_date", "end_date",
]


def load_env_file(path: Path) -> None:
    """Minimal .env loader. Avoids adding python-dotenv as a dependency for this.

    Only full lines starting with "#" (after stripping leading whitespace)
    are treated as comments. We deliberately do NOT strip inline "#..."
    comments from the middle of a KEY=value line: .env files conventionally
    don't use inline comments, and any attempt to detect "outside of quotes"
    is more complex than it's worth here -- a value containing a literal '#'
    (e.g. a password) would otherwise get silently truncated.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    import wrds

    # Avoid hanging on the interactive "create a .pgpass file?" prompt -- we
    # keep credentials in .env only and never write them to disk in plaintext.
    # Scoped to just the wrds.Connection(...) call (and restored via
    # try/finally even if it raises) rather than patched globally for the
    # rest of the process.
    original_input = builtins.input
    builtins.input = lambda *a, **k: "n"
    try:
        username = os.environ["WRDS_USERNAME"]
        password = os.environ["WRDS_PASSWORD"]
        print(f"Connecting to WRDS as {username} ...", flush=True)
        # 60s is a safety net, not an accommodation for a live Duo push. If this
        # account's 30-day MFA grace period (per WRDS docs) has lapsed for this
        # network, re-authenticate via the WRDS website or SSH first.
        db = wrds.Connection(
            wrds_username=username,
            wrds_password=password,
            wrds_connect_args={"connect_timeout": 60},
        )
    finally:
        builtins.input = original_input
    print("Connected.", flush=True)
    return db


def _rel(path: Path) -> str:
    """Render `path` relative to REPO_ROOT for display/logging when possible,
    falling back to the absolute path (e.g. when a test points out_dir
    somewhere outside the repo)."""
    try:
        return str(Path(path).relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def append_manifest_row(row: dict, manifest_path: Path = None) -> None:
    """Append one row to the manifest CSV immediately, creating the header if
    the file doesn't exist yet (or is empty). Writing per-row -- rather than
    buffering the whole run's log in memory and writing once at the end,
    after db.close(), inside a finally block that can itself be interrupted
    -- means a killed/interrupted run still leaves a manifest that's accurate
    for everything it got through before it died.
    """
    manifest_path = Path(manifest_path) if manifest_path is not None else MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault(
        "pulled_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    )
    file_is_new = not manifest_path.exists() or manifest_path.stat().st_size == 0
    with manifest_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDNAMES)
        if file_is_new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDNAMES})


def record(log: list, row: dict, manifest_path: Path = None) -> None:
    """Add one entry to both the in-memory run log (used only for the final
    printed summary) and the on-disk manifest (the durable record)."""
    row = dict(row)
    row.setdefault(
        "pulled_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    )
    log.append(row)
    append_manifest_row(row, manifest_path=manifest_path)


def _stream_query_to_parquet(db, sql: str, out_path: Path):
    """Run `sql` against `db` using chunked streaming (`return_iter=True`)
    and write each chunk straight to `out_path` with a pyarrow ParquetWriter,
    instead of materializing the whole result set in memory first.

    wrds.Connection.raw_sql's default (non-streaming) path internally
    accumulates chunks via repeated pd.concat, which is O(n^2) in the number
    of chunks -- risky for a table the size of crsp.dsf. This holds at most
    one chunk in memory at a time.

    Returns (total_rows, wrote_any). wrote_any is False only if the query
    returned zero chunks (no rows at all), in which case no file is created.
    """
    writer = None
    total_rows = 0
    try:
        for chunk in db.raw_sql(sql, return_iter=True):
            arrow_chunk = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(str(out_path), arrow_chunk.schema)
            writer.write_table(arrow_chunk)
            total_rows += len(chunk)
    finally:
        if writer is not None:
            writer.close()
    return total_rows, writer is not None


def pull_table(
    db,
    log,
    library,
    table,
    desired_columns,
    where_sql="",
    out_name=None,
    required_columns=None,
    stream=False,
    start_date=None,
    end_date=None,
    raw_dir=None,
    manifest_path=None,
    force=False,
):
    """Check real columns first, select only what exists, save to Parquet, log the outcome.

    `db` only needs to satisfy the small interface actually used here --
    `describe_table(library, table)` and `raw_sql(sql, return_iter=...)` --
    so tests can pass a lightweight stub instead of a real wrds.Connection.

    `required_columns` (a subset of `desired_columns`) must ALL be present in
    the real schema or the pull is skipped entirely (status
    "missing_required_columns") rather than silently proceeding with
    whatever happened to match.

    `stream=True` uses `_stream_query_to_parquet` (chunked, low-memory)
    instead of loading the full result into a DataFrame first; use it for
    any table that could plausibly be large.
    """
    out_name = out_name or table
    raw_dir = raw_dir if raw_dir is not None else RAW_DIR
    out_dir = raw_dir / library
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{out_name}.parquet"
    required_columns = required_columns or []

    def _log(status, rows=0, path="", missing_columns="", error=""):
        record(
            log,
            {
                "library": library,
                "table": table,
                "status": status,
                "rows": rows,
                "path": path,
                "missing_columns": missing_columns,
                "error": error,
                "start_date": start_date or "",
                "end_date": end_date or "",
            },
            manifest_path=manifest_path,
        )

    if out_path.exists() and not force:
        print(
            f"  {library}.{table}: {_rel(out_path)} already exists, skipping "
            f"(pass --force to re-pull).",
            flush=True,
        )
        _log("skipped_exists", path=_rel(out_path))
        return

    try:
        schema = db.describe_table(library, table)
        actual_columns = {c.lower() for c in schema["name"]}
    except sa_exc.NoSuchTableError as e:
        # Genuinely "this table doesn't exist on this account" -- safe to log
        # as such.
        print(f"  {library}.{table}: table not found ({e})", flush=True)
        _log("no_such_table", error=str(e))
        return
    except Exception as e:
        # Anything else (connection lost, timeout, permission error, ...) is
        # NOT "table doesn't exist" and must not be silently classified as
        # such -- that would produce a wrong, misleading manifest.
        print(
            f"  WARNING: {library}.{table}: describe_table failed for a reason "
            f"other than a missing table -- NOT assuming it doesn't exist: {e}",
            flush=True,
        )
        _log("describe_table_error", error=str(e))
        return

    cols_to_select = [c for c in desired_columns if c.lower() in actual_columns]
    missing = [c for c in desired_columns if c.lower() not in actual_columns]
    if missing:
        print(f"  {library}.{table}: columns not present, skipping: {missing}", flush=True)

    missing_required = [c for c in required_columns if c.lower() not in actual_columns]
    if missing_required:
        print(
            f"  {library}.{table}: REQUIRED columns missing, NOT querying: "
            f"{missing_required}",
            flush=True,
        )
        _log(
            "missing_required_columns",
            missing_columns=",".join(missing_required),
            error=f"required columns missing: {missing_required}",
        )
        return

    if not cols_to_select:
        _log(
            "no_matching_columns",
            missing_columns=",".join(missing),
            error=f"none of {desired_columns} found",
        )
        return

    sql = f"SELECT {', '.join(cols_to_select)} FROM {library}.{table}"
    if where_sql:
        sql += f" WHERE {where_sql}"

    try:
        print(f"Pulling {library}.{table} -> {_rel(out_path)} ...", flush=True)
        if stream:
            rows, wrote_any = _stream_query_to_parquet(db, sql, out_path)
            if not wrote_any:
                print("  0 rows returned; no file written.", flush=True)
                _log("ok", rows=0, missing_columns=",".join(missing))
                return
        else:
            df = db.raw_sql(sql)
            df.to_parquet(out_path, index=False)
            rows = len(df)
        print(f"  {rows:,} rows.", flush=True)
        _log(
            "ok", rows=rows, path=_rel(out_path), missing_columns=",".join(missing)
        )
    except Exception as e:
        print(f"  FAILED: {e}", flush=True)
        if stream and out_path.exists():
            # Don't leave a partially-written parquet file behind that a
            # later --force-less re-run would mistake for a complete pull.
            out_path.unlink(missing_ok=True)
        _log("query_failed", missing_columns=",".join(missing), error=str(e))


def main():
    load_env_file(REPO_ROOT / ".env")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    force = "--force" in sys.argv

    db = connect()
    log = []

    try:
        # --- One-time library audit ---
        libraries = sorted(db.list_libraries())
        (METADATA_DIR / "wrds_available_libraries.txt").write_text(
            "\n".join(libraries) + "\n"
        )
        print(
            f"{len(libraries)} libraries visible. "
            f"Saved to data/metadata/wrds_available_libraries.txt",
            flush=True,
        )

        have_ibes = "ibes" in libraries
        have_crsp = "crsp" in libraries
        comp_candidates = [lib for lib in ("comp", "compa", "compustat") if lib in libraries]
        have_comp = bool(comp_candidates)
        comp_lib = comp_candidates[0] if have_comp else None
        # Deliberately not checking for optionm/OptionMetrics here -- see module
        # docstring. Options data comes from the external drive archive.

        # --- I/B/E/S ---
        if have_ibes:
            # NOTE: these use LOOKBACK_START_DATE (not START_DATE) for the same
            # reason CRSP/Compustat do -- rolling-window/history features need
            # a runway before START_DATE, so early-1996 events have a chance at
            # a prior consensus snapshot.
            pull_table(
                db, log, "ibes", "statsumu_epsus",
                ["ticker", "cusip", "oftic", "cname", "statpers", "measure",
                 "fiscalp", "fpi", "curcode", "usfirm", "fpedats",
                 "numest", "numup", "numdown", "medest", "meanest",
                 "stdev", "highest", "lowest"],
                where_sql=f"statpers BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
                required_columns=["ticker", "statpers"],
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
            )
            pull_table(
                db, log, "ibes", "actu_epsus",
                ["ticker", "cusip", "oftic", "cname", "pends", "anndats",
                 "anntims", "value", "curr_act", "measure"],
                where_sql=f"anndats BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
                required_columns=["ticker", "anndats"],
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
            )
            pull_table(
                db, log, "ibes", "idsum",
                ["ticker", "cusip", "cname", "oftic", "sdates"],
                required_columns=["ticker"],
                start_date=None, end_date=None,
            )
            # Analyst-level detail: STDEV/NUMEST from the summary file already cover
            # dispersion and analyst count for most purposes, but this is a one-shot
            # pull, so grab it now rather than come back for it later if a future
            # slice wants revision-level detail. fpi needed to separate detail
            # estimates by forecast horizon.
            pull_table(
                db, log, "ibes", "det_epsus",
                ["ticker", "cusip", "estimator", "analys", "fpedats", "fpi",
                 "revdats", "revtims", "value", "measure"],
                where_sql=f"revdats BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
                required_columns=["ticker", "revdats"],
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
            )
        else:
            print("ibes library not visible on this account -- skipping.", flush=True)
            record(
                log,
                {"library": "ibes", "table": "*", "status": "library_unavailable",
                 "rows": 0, "path": "", "missing_columns": "", "error": "",
                 "start_date": "", "end_date": ""},
            )

        # --- CRSP ---
        if have_crsp:
            # Return series get a forward buffer (RETURN_END_DATE, not
            # END_DATE) so events near the end of the sample still have a
            # full post-event drift window. bid/ask/bidlo/askhi/openprc/numtrd
            # added for any future transaction-cost/spread modeling -- this
            # table won't be re-pulled later per the "minimize WRDS queries"
            # rule, so grab them now.
            pull_table(
                db, log, "crsp", "dsf",
                ["permno", "date", "ret", "retx", "prc", "vol", "shrout",
                 "cfacpr", "cfacshr", "bid", "ask", "bidlo", "askhi",
                 "openprc", "numtrd"],
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                required_columns=["permno", "date"],
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
            )
            pull_table(
                db, log, "crsp", "dsi",
                ["date", "vwretd", "vwretx", "ewretd", "ewretx", "sprtrn"],
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                required_columns=["date"],
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
            )
            pull_table(
                db, log, "crsp", "dsedelist",
                ["permno", "dlstdt", "dlret", "dlstcd"],
                where_sql=f"dlstdt BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                required_columns=["permno", "dlstdt"],
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
            )
            # siccd is the point-in-time SIC code (unlike comp.company.sic
            # below, which is the firm's current/most-recent classification
            # only) -- needed for historically-accurate sector attribution.
            # nameenddt/namedt are required: point-in-time identifier
            # validity depends on both bounds being present.
            pull_table(
                db, log, "crsp", "stocknames",
                ["permno", "permco", "namedt", "nameenddt", "ticker",
                 "comnam", "cusip", "shrcd", "exchcd", "siccd"],
                required_columns=["namedt", "nameenddt"],
                start_date=None, end_date=None,
            )
        else:
            print("crsp library not visible on this account -- skipping.", flush=True)
            record(
                log,
                {"library": "crsp", "table": "*", "status": "library_unavailable",
                 "rows": 0, "path": "", "missing_columns": "", "error": "",
                 "start_date": "", "end_date": ""},
            )

        # --- Compustat (lower priority) ---
        if have_comp:
            fundq_where = (
                f"datadate BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}' "
                f"AND {COMPUSTAT_STD_FILTER}"
            )
            funda_where = (
                f"datadate BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}' "
                f"AND {COMPUSTAT_STD_FILTER}"
            )
            # indfmt/datafmt/popsrc/consol select the standard
            # industrial/domestic/consolidated statement -- Compustat stores
            # multiple rows per gvkey/datadate across these dimensions, so
            # without both the columns AND the where-clause filter this
            # produces duplicate, unusable rows downstream. rdq (report date
            # of quarterly earnings) is Compustat's standard PEAD event-date
            # field.
            pull_table(
                db, log, comp_lib, "fundq",
                ["gvkey", "datadate", "fyearq", "fqtr", "atq", "ceqq",
                 "saleq", "niq", "epspxq", "rdq",
                 "indfmt", "datafmt", "popsrc", "consol"],
                where_sql=fundq_where,
                required_columns=["gvkey", "datadate"],
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
            )
            # sic/gsector here are CURRENT-SNAPSHOT only (the firm's most
            # recent classification), NOT point-in-time -- do not use this
            # for historical sector attribution without that caveat. Use
            # crsp.stocknames.siccd (above) for point-in-time SIC instead.
            pull_table(
                db, log, comp_lib, "company",
                ["gvkey", "conm", "sic", "gsector"],
                required_columns=["gvkey"],
                start_date=None, end_date=None,
            )
            # Annual fundamentals alongside quarterly -- book-to-market and
            # other annual-basis controls aren't in fundq, and this is a
            # one-shot pull, so grab it now.
            pull_table(
                db, log, comp_lib, "funda",
                ["gvkey", "datadate", "fyear", "at", "ceq", "sale", "ni", "epspx",
                 "indfmt", "datafmt", "popsrc", "consol"],
                where_sql=funda_where,
                required_columns=["gvkey", "datadate"],
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
            )
        else:
            print("Compustat library not visible on this account -- skipping.", flush=True)
            record(
                log,
                {"library": "comp", "table": "*", "status": "library_unavailable",
                 "rows": 0, "path": "", "missing_columns": "", "error": "",
                 "start_date": "", "end_date": ""},
            )

        # --- Linking tables (best-effort; exact names vary by account) ---
        # wrdsapps.iclink was removed: confirmed via a prior run's manifest
        # that it doesn't exist on this account (no_such_table) -- dead code.
        if have_ibes and have_crsp:
            # ncusip is required: we rely on it for CUSIP-based identifier
            # matching between I/B/E/S and CRSP, so a silently-missing ncusip
            # would produce a link table that looks fine but can't actually
            # be joined on.
            pull_table(
                db, log, "wrdsapps", "ibcrsphist",
                ["ticker", "permno", "ncusip", "score", "sdate", "edate"],
                required_columns=["ncusip"],
                start_date=None, end_date=None,
            )
        if have_crsp and have_comp:
            pull_table(
                db, log, "crsp", "ccmxpf_lnkhist",
                ["gvkey", "lpermno", "linktype", "linkprim", "linkdt", "linkenddt"],
                required_columns=["gvkey", "lpermno"],
                start_date=None, end_date=None,
            )
    finally:
        db.close()
        print("Connection closed.", flush=True)

    # --- Summary (the manifest itself was already written incrementally,
    # row by row, above -- this just prints what this run did) ---
    print("\nManifest is at data/metadata/wrds_pull_manifest.csv")
    if log:
        print(pd.DataFrame(log).to_string(index=False))


if __name__ == "__main__":
    main()
