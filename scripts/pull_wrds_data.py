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
column/table names can vary slightly by WRDS account vintage. Any table that
doesn't exist, or whose real schema is missing ANY column this script asks
for, is logged and skipped rather than crashing the run, so one wrong name
doesn't take down an otherwise-successful pull -- but it is also never
silently queried with a partial column set. There is no "optional column"
tier: every column named in a pull_table() call is one this script actually
needs downstream (a payload value, a join key, or a point-in-time validity
bound), so a missing column always aborts that table's pull (logged as
"missing_required_columns") rather than risking a silently-crippled result
that looks complete in the manifest but is missing something a downstream
join or calculation depends on.

Every table's outcome -- success, skip, or failure -- is appended to
data/metadata/wrds_pull_manifest.csv immediately after that table's attempt,
not buffered in memory until the end, so an interrupted run still leaves an
accurate manifest for everything it got through.

Usage:
    python scripts/pull_wrds_data.py [--force]
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import argparse
import builtins
import csv
import datetime as dt
import os
import re
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
# 60-trading-day forward window with room to spare. Used for:
#   - the return series themselves (crsp.dsf/dsi/dsedelist), and
#   - "event occurred by X" filters, i.e. dates that gate an actual PEAD
#     trigger event needing a post-event drift window: ibes.actu_epsus.anndats
#     (earnings announcement date), ibes.detu_epsus.revdats (analyst estimate
#     revision date), and comp.fundq.datadate (quarterly fundamentals/rdq).
# Deliberately NOT extended for "estimate/observation dated by X" filters,
# which describe when a snapshot or control variable was drawn rather than
# an event with a drift window: ibes.statsumu_epsus.statpers (a consensus
# snapshot date, not an event) and comp.funda.datadate (annual control
# fundamentals, not itself a quarterly PEAD trigger date -- see the
# reasoning comments at each of those call sites below).
RETURN_END_DATE = "2014-06-30"

# Standard Compustat filter for ordinary industrial/domestic/consolidated
# statements. Compustat stores multiple rows per gvkey/datadate across
# indfmt/datafmt/popsrc/consol combinations; without this filter fundq/funda
# pulls contain duplicate, unusable rows.
COMPUSTAT_STD_FILTER = "indfmt='INDL' AND datafmt='STD' AND popsrc='D' AND consol='C'"

# Per-table column lists for every pull_table() call in main(), pulled out to
# module level (rather than inline list literals in main()) for one reason:
# tests/test_pull_wrds_data.py needs a "full column set" fixture to build a
# fake schema that never spuriously fails any table's pull, and a previous
# round's hand-copied duplicate of that set drifted out of sync with main()'s
# real calls. Defining the lists once here and having BOTH main() and the
# test suite import these same constants makes that kind of drift structurally
# impossible instead of merely policed by hand -- there is only one place a
# column ever gets added or removed for a given table.
#
# Every column below is required: see pull_table()'s docstring for why there
# is no more "optional column" tier.
IBES_STATSUMU_EPSUS_COLUMNS = [
    "ticker", "cusip", "oftic", "cname", "statpers", "measure", "fiscalp",
    "fpi", "curcode", "usfirm", "fpedats", "numest", "numup", "numdown",
    "medest", "meanest", "stdev", "highest", "lowest",
]
IBES_ACTU_EPSUS_COLUMNS = [
    "ticker", "cusip", "oftic", "cname", "pends", "anndats", "anntims",
    "value", "curr_act", "measure",
]
IBES_IDSUM_COLUMNS = ["ticker", "cusip", "cname", "oftic", "sdates"]
# detu_epsus (unadjusted), NOT det_epsus (split-adjusted): the other two
# I/B/E/S tables above (statsumu_epsus, actu_epsus) are both split-UNADJUSTED
# (that's what the "u" in their names means), so pulling the split-ADJUSTED
# detail file would mix incompatible adjustment bases and couldn't be
# combined with them. The adjusted file also carries look-ahead: its
# adjustment factor isn't knowable until the future split actually happens.
IBES_DETU_EPSUS_COLUMNS = [
    "ticker", "cusip", "estimator", "analys", "fpedats", "fpi", "revdats",
    "revtims", "value", "measure",
]
CRSP_DSF_COLUMNS = [
    "permno", "date", "ret", "retx", "prc", "vol", "shrout", "cfacpr",
    "cfacshr", "bid", "ask", "bidlo", "askhi", "openprc", "numtrd",
]
CRSP_DSI_COLUMNS = ["date", "vwretd", "vwretx", "ewretd", "ewretx", "sprtrn"]
CRSP_DSEDELIST_COLUMNS = ["permno", "dlstdt", "dlret", "dlstcd"]
# ncusip (the CUSIP actually in effect during [namedt, nameenddt]) is
# required alongside cusip (the header/most-recent CUSIP, constant across a
# permno's whole history): this table's pull is justified on point-in-time
# grounds (hence namedt/nameenddt below), and cusip alone can't satisfy that
# -- ncusip is the one that's actually valid for a given historical date.
# cusip is kept too since some downstream uses legitimately want the current/
# header identifier rather than the as-of-date one.
CRSP_STOCKNAMES_COLUMNS = [
    "permno", "permco", "namedt", "nameenddt", "ticker", "comnam", "cusip",
    "ncusip", "shrcd", "exchcd", "siccd",
]
COMP_FUNDQ_COLUMNS = [
    "gvkey", "datadate", "fyearq", "fqtr", "atq", "ceqq", "saleq", "niq",
    "epspxq", "rdq", "indfmt", "datafmt", "popsrc", "consol",
]
COMP_COMPANY_COLUMNS = ["gvkey", "conm", "sic", "gsector"]
COMP_FUNDA_COLUMNS = [
    "gvkey", "datadate", "fyear", "at", "ceq", "sale", "ni", "epspx",
    "indfmt", "datafmt", "popsrc", "consol",
]
WRDSAPPS_IBCRSPHIST_COLUMNS = ["ticker", "permno", "ncusip", "score", "sdate", "edate"]
CRSP_CCMXPF_LNKHIST_COLUMNS = [
    "gvkey", "lpermno", "linktype", "linkprim", "linkdt", "linkenddt",
]

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
    """Run `sql` against `db` using chunked streaming (`return_iter=True`),
    accumulating each chunk as a pyarrow Table, then write the combined
    result to `out_path` once, atomically, at the end.

    wrds.Connection.raw_sql's default (non-streaming) path internally
    accumulates chunks via repeated pd.concat, which is O(n^2) in the number
    of chunks -- risky for a table the size of crsp.dsf. Streaming via
    `return_iter=True` avoids that repeated-pd.concat blowup.

    We deliberately do NOT use a fixed-schema pyarrow.parquet.ParquetWriter
    here (an earlier version did, seeded from the first chunk's schema):
    per-chunk schemas can legitimately differ in more than one way. The
    obvious case is a null-type column (e.g. crsp.dsf's `numtrd`, NASDAQ-only
    and NULL for every NYSE/AMEX row) promoted to a real type once a later
    chunk has actual values. The subtler, ACTUALLY-observed case is two
    chunks that both have real (non-null) values in a numeric column but
    infer to different pandas/pyarrow dtypes: pandas' `convert_dtypes()`
    (used internally by wrds's chunked reader) runs independently per chunk,
    so a chunk whose values all happen to be whole numbers infers as `Int64`
    while another chunk with a fractional value infers as `Float64` for the
    very same column. `pa.concat_tables(..., promote_options="default")`
    handles the null-type-promotion case but NOT the Int64-vs-Float64 case
    -- it raises `ArrowTypeError` on that mismatch. `promote_options=
    "permissive"` unifies both cases (e.g. Int64 and Float64 both promote to
    a common `double` column), so that's what we use here. We also strip the
    combined table's schema metadata before writing (see the inline comment
    at that call) -- without that, stale per-chunk pandas-dtype metadata
    makes a later read try to cast the unified column back to the ORIGINAL
    (pre-promotion) dtype and fail all over again on the same data.

    Holding every chunk's pyarrow Table in memory and combining them all at
    once at the end -- rather than writing incrementally -- means this
    function is NOT low-memory and does NOT avoid ever holding the full
    table: it holds one table's entire chunk set (input data plus pyarrow's
    in-memory Arrow representation of it) before the final concat+write.
    That is an accepted, deliberate trade-off, not an oversight: this is a
    one-time bulk pull, not a recurring job, and this project's per-table
    date range tops out at a few GB, so the memory cost of holding one
    table's chunks is acceptable in exchange for not having to build a fully
    streaming, incremental schema-unification system (which would be more
    code, and more surface area for a new subtle bug, than the memory this
    saves is worth). We're still never doing repeated pandas concat, which
    was the original O(n^2) risk this function replaced.

    The final write goes to a temporary path and is moved into place with
    os.replace() only after it fully succeeds, so a failure partway through
    (e.g. on a --force re-pull) can never truncate or destroy a
    previously-good `out_path` -- it is left completely untouched until the
    new file is known-good.

    Returns (total_rows, wrote_any). wrote_any is False only if the query
    returned zero chunks (no rows at all), in which case no file is created
    (and out_path is untouched).
    """
    tables = []
    total_rows = 0
    for chunk in db.raw_sql(sql, return_iter=True):
        tables.append(pa.Table.from_pandas(chunk, preserve_index=False))
        total_rows += len(chunk)

    if not tables:
        return 0, False

    combined = pa.concat_tables(tables, promote_options="permissive")
    # `promote_options="permissive"` unifies the ARROW schema (e.g. Int64 and
    # Float64 chunks both promote to a common `double` column) -- but each
    # chunk's pyarrow Table also carries pandas-specific schema metadata
    # (embedded by pa.Table.from_pandas) recording the ORIGINAL pandas dtype
    # per column. After promotion that metadata is stale for any promoted
    # column: it still says e.g. "this column is pandas Int64", even though
    # the promoted Arrow column is now `double`. If left in place, a later
    # pd.read_parquet() (or .to_pandas()) tries to honor that stale metadata
    # and cast the double column back to Int64 -- which raises ArrowInvalid
    # ("Float value ... was truncated converting to int64") on the very
    # first non-whole-number value, i.e. exactly the case this promotion was
    # supposed to fix. Dropping all schema metadata before writing forces a
    # later read to infer dtypes fresh from the (already-correct) unified
    # Arrow schema instead of trusting stale per-chunk pandas hints.
    combined = combined.replace_schema_metadata(None)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        pq.write_table(combined, str(tmp_path))
        os.replace(tmp_path, out_path)
    except (Exception, KeyboardInterrupt):
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return total_rows, True


# Table/library/column names are f-string-interpolated into the SQL below.
# Every call site in this file passes hardcoded literals, so this isn't
# exploitable today -- but it's a cheap safety net against future misuse.
_SQL_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*\Z")


def _validate_sql_identifier(kind: str, name: str) -> None:
    """Raise ValueError if `name` isn't a safe bare SQL identifier. This is a
    defensive assertion about this script's OWN hardcoded inputs (library,
    table, and column names), not user-facing validation -- failing loudly
    here means a future typo or copy-paste mistake (e.g. an accidental
    semicolon) can't silently become a malformed or dangerous query.
    """
    if not _SQL_IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Unsafe SQL {kind} name {name!r}: must match "
            f"{_SQL_IDENTIFIER_RE.pattern!r}"
        )


def pull_table(
    db,
    log,
    library,
    table,
    columns,
    where_sql="",
    out_name=None,
    stream=False,
    start_date=None,
    end_date=None,
    raw_dir=None,
    manifest_path=None,
    force=False,
):
    """Check real columns first, save to Parquet, log the outcome.

    `db` only needs to satisfy the small interface actually used here --
    `describe_table(library, table)` and `raw_sql(sql, return_iter=...)` --
    so tests can pass a lightweight stub instead of a real wrds.Connection.

    `columns` is the single, complete list of columns this table's pull
    needs. There is no "optional column" tier: every column named here must
    be present in the real schema (per db.describe_table()) or the pull does
    not run at all (status "missing_required_columns", logged with the
    actual missing names) -- never a partial column set. This is
    deliberately strict: a caller that only weakly wants a column shouldn't
    put it in `columns` at all, since its absence always aborts the pull.

    `stream=True` uses `_stream_query_to_parquet` (chunked reads, one
    combined write at the end -- see that function's docstring for the
    memory trade-off) instead of loading the full result into a DataFrame
    first; use it for any table that could plausibly be large.
    """
    # Validate every identifier that's about to be interpolated into the SQL
    # string -- see _validate_sql_identifier's docstring -- before `library`
    # or `table` is used for anything, including describe_table(). where_sql
    # is left as-is structurally: it's still a caller-controlled hardcoded
    # literal here, not runtime/external input, so it isn't validated the
    # same way. If this script ever accepts external/config-driven input for
    # table names or WHERE clauses, that input MUST be validated identically
    # (or parameterized via raw_sql's params support) before reaching here.
    _validate_sql_identifier("library", library)
    _validate_sql_identifier("table", table)
    for col in columns:
        _validate_sql_identifier("column", col)

    out_name = out_name or table
    raw_dir = raw_dir if raw_dir is not None else RAW_DIR
    out_dir = raw_dir / library
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{out_name}.parquet"

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

    missing = [c for c in columns if c.lower() not in actual_columns]
    if missing:
        print(
            f"  {library}.{table}: required columns missing, NOT querying: "
            f"{missing}",
            flush=True,
        )
        _log(
            "missing_required_columns",
            missing_columns=",".join(missing),
            error=f"required columns missing: {missing}",
        )
        return

    sql = f"SELECT {', '.join(columns)} FROM {library}.{table}"
    if where_sql:
        sql += f" WHERE {where_sql}"

    try:
        print(f"Pulling {library}.{table} -> {_rel(out_path)} ...", flush=True)
        if stream:
            rows, wrote_any = _stream_query_to_parquet(db, sql, out_path)
            if not wrote_any:
                if out_path.exists():
                    # --force re-pull whose query legitimately returned zero
                    # rows: the previous file at out_path was never touched
                    # (no chunks means no write at all), so "rows: 0" alone
                    # would misleadingly read as "this table is empty" when
                    # actually the old data is still sitting on disk.
                    print(
                        "  0 rows returned; existing file at out_path left "
                        "untouched (not overwritten with an empty result).",
                        flush=True,
                    )
                    _log("ok_empty_query_kept_existing", rows=0)
                else:
                    print("  0 rows returned; no file written.", flush=True)
                    _log("ok", rows=0)
                return
        else:
            df = db.raw_sql(sql)
            # Write to a temp path and move it into place atomically so a
            # failure partway through to_parquet() can never leave a
            # truncated file at out_path that a later --force-less run's
            # idempotency check (out_path.exists()) would mistake for a
            # complete, valid pull -- and, on a --force re-pull, can never
            # destroy a previously-good out_path.
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            try:
                df.to_parquet(tmp_path, index=False)
                os.replace(tmp_path, out_path)
            except (Exception, KeyboardInterrupt):
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise
            rows = len(df)
        print(f"  {rows:,} rows.", flush=True)
        _log("ok", rows=rows, path=_rel(out_path))
    except Exception as e:
        print(f"  FAILED: {e}", flush=True)
        # Both write paths above are now atomic (write-to-temp, then
        # os.replace only on full success) and clean up their own temp file
        # on failure -- so out_path itself is never touched by a failed
        # write and must be left alone here, whether it pre-existed (a
        # previously-good file from an earlier run) or never existed.
        _log("query_failed", error=str(e))


def _parse_args(argv):
    """Parse this script's own command-line arguments via argparse (rather
    than a hand-rolled `"--force" in sys.argv` check), so a typo like
    `--Force` or `-force` errors out immediately instead of silently being
    ignored -- which would otherwise proceed as if --force hadn't been
    passed (skipping every already-pulled table) and exit looking
    successful.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-pull every table even if its output Parquet file already exists.",
    )
    return parser.parse_args(argv)


def main():
    load_env_file(REPO_ROOT / ".env")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    args = _parse_args(sys.argv[1:])
    force = args.force

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
            # statpers is the consensus SNAPSHOT date, not an event date --
            # intentionally left at END_DATE (not RETURN_END_DATE): extending
            # it would pull in consensus snapshots past the sample cutoff
            # rather than giving an in-sample event more drift-window room,
            # which is what RETURN_END_DATE is for. Every column in
            # IBES_STATSUMU_EPSUS_COLUMNS is required (see pull_table's
            # docstring): meanest/fpi/fpedats in particular, since without
            # them there's no consensus estimate value or forecast-horizon
            # identification at all, making a schema missing any of them
            # useless for this project, not just incomplete.
            pull_table(
                db, log, "ibes", "statsumu_epsus",
                IBES_STATSUMU_EPSUS_COLUMNS,
                where_sql=f"statpers BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
                force=force,
            )
            # anndats is the earnings ANNOUNCEMENT date -- an actual PEAD
            # trigger event, so (per the module-level RETURN_END_DATE
            # comment) it gets the forward buffer like the return series,
            # not just END_DATE. value (the actual reported EPS) is
            # required: without it this table has no payload at all.
            pull_table(
                db, log, "ibes", "actu_epsus",
                IBES_ACTU_EPSUS_COLUMNS,
                where_sql=f"anndats BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            pull_table(
                db, log, "ibes", "idsum",
                IBES_IDSUM_COLUMNS,
                start_date=None, end_date=None,
                force=force,
            )
            # Analyst-level detail: STDEV/NUMEST from the summary file already cover
            # dispersion and analyst count for most purposes, but this is a one-shot
            # pull, so grab it now rather than come back for it later if a future
            # slice wants revision-level detail. fpi needed to separate detail
            # estimates by forecast horizon.
            #
            # detu_epsus (unadjusted), NOT det_epsus (split-adjusted): the
            # other two I/B/E/S tables above (statsumu_epsus, actu_epsus) are
            # both split-UNADJUSTED (that's what the "u" in their names
            # means), so pulling the split-ADJUSTED detail file would mix
            # incompatible adjustment bases and couldn't be combined with
            # them. The adjusted file also carries look-ahead: its adjustment
            # factor isn't knowable until the future split actually happens.
            # detu_epsus is expected to share det_epsus's column names
            # (IBES detail tables conventionally do between adjusted/
            # unadjusted variants) -- this should still be spot-checked
            # against this account's actual schema (e.g. via
            # db.describe_table("ibes", "detu_epsus")) on the next real run;
            # pull_table's own column-matching check will catch a mismatch
            # rather than silently mis-querying.
            # revdats (the estimate REVISION date) is an actual event date
            # for this table's purposes, so it gets the RETURN_END_DATE
            # buffer too, same reasoning as actu_epsus.anndats above.
            pull_table(
                db, log, "ibes", "detu_epsus",
                IBES_DETU_EPSUS_COLUMNS,
                where_sql=f"revdats BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
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
            # ret (the actual daily return) is required: without it this
            # table is useless for this project regardless of what else is
            # present.
            pull_table(
                db, log, "crsp", "dsf",
                CRSP_DSF_COLUMNS,
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            # This is the market-benchmark counterpart to dsf above --
            # vwretd/vwretx/ewretd/ewretx/sprtrn are all required: without
            # the benchmark return series itself, this table has no payload.
            pull_table(
                db, log, "crsp", "dsi",
                CRSP_DSI_COLUMNS,
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            # dlret (the delisting return) is required: without it this
            # table has no payload -- just the fact that a delisting
            # happened, with no return impact to attribute to it.
            pull_table(
                db, log, "crsp", "dsedelist",
                CRSP_DSEDELIST_COLUMNS,
                where_sql=f"dlstdt BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            # siccd is the point-in-time SIC code (unlike comp.company.sic
            # below, which is the firm's current/most-recent classification
            # only) -- needed for historically-accurate sector attribution.
            # nameenddt/namedt are required: point-in-time identifier
            # validity depends on both bounds being present. permno is
            # required too: it's the actual join key this table exists to
            # provide, alongside its validity window. See
            # CRSP_STOCKNAMES_COLUMNS' definition above for why ncusip (not
            # just cusip) is required here.
            pull_table(
                db, log, "crsp", "stocknames",
                CRSP_STOCKNAMES_COLUMNS,
                start_date=None, end_date=None,
                force=force,
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
            # datadate here gates fundq's rdq (report date of quarterly
            # earnings) -- Compustat's standard PEAD event-date field -- so
            # it gets the RETURN_END_DATE forward buffer for the same reason
            # ibes.actu_epsus.anndats does above: standard PEAD practice
            # truncates the holding window at the next earnings announcement,
            # and without this buffer the last couple of in-sample quarters
            # would have no visibility into that next announcement.
            fundq_where = (
                f"datadate BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}' "
                f"AND {COMPUSTAT_STD_FILTER}"
            )
            # funda (annual) is intentionally left at END_DATE, not
            # RETURN_END_DATE: it's lower-priority annual control data
            # (book-to-market, etc.), not itself a quarterly PEAD trigger
            # date the way fundq's rdq is, so it doesn't need the
            # next-announcement forward buffer.
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
            #
            # CAVEAT (fundq/funda both): Compustat fundamentals are
            # as-CURRENTLY-reported, i.e. restated -- a firm's FY2005 numbers
            # here reflect any later restatement, not what the market
            # actually saw on the original rdq. Do not treat this as
            # point-in-time data without accounting for that; downstream
            # feature work should not assume point-in-time correctness this
            # pull doesn't have.
            # indfmt/datafmt/popsrc/consol are required here (part of
            # COMP_FUNDQ_COLUMNS): they're the same four columns
            # COMPUSTAT_STD_FILTER filters on in fundq_where above, so if
            # any of them were silently missing from this account's schema,
            # the WHERE clause referencing them would be a raw SQL error at
            # query time rather than a graceful skip. Requiring them means
            # pull_table's own missing-required-columns check aborts BEFORE
            # the query is ever built, not after a malformed one fails.
            pull_table(
                db, log, comp_lib, "fundq",
                COMP_FUNDQ_COLUMNS,
                where_sql=fundq_where,
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            # sic/gsector here are CURRENT-SNAPSHOT only (the firm's most
            # recent classification), NOT point-in-time -- do not use this
            # for historical sector attribution without that caveat. Use
            # crsp.stocknames.siccd (above) for point-in-time SIC instead.
            pull_table(
                db, log, comp_lib, "company",
                COMP_COMPANY_COLUMNS,
                start_date=None, end_date=None,
                force=force,
            )
            # Annual fundamentals alongside quarterly -- book-to-market and
            # other annual-basis controls aren't in fundq, and this is a
            # one-shot pull, so grab it now. See the restated-data caveat
            # above fundq's pull_table call -- it applies here too. Same
            # indfmt/datafmt/popsrc/consol reasoning as fundq above applies:
            # funda_where filters on those same four columns.
            pull_table(
                db, log, comp_lib, "funda",
                COMP_FUNDA_COLUMNS,
                where_sql=funda_where,
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
                force=force,
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
            # be joined on. permno/sdate/edate are also required: the whole
            # point of this table is the permno link plus its point-in-time
            # validity window, so a schema missing any of those produces a
            # link table that can't actually be used for the thing it exists
            # for.
            pull_table(
                db, log, "wrdsapps", "ibcrsphist",
                WRDSAPPS_IBCRSPHIST_COLUMNS,
                start_date=None, end_date=None,
                force=force,
            )
        if have_crsp and have_comp:
            # linkdt/linkenddt are required for the same reason they're
            # required on crsp.stocknames above: the point-in-time link
            # validity bounds, without which this table can silently
            # misattribute a gvkey/permno link outside its actually-valid
            # window.
            pull_table(
                db, log, "crsp", "ccmxpf_lnkhist",
                CRSP_CCMXPF_LNKHIST_COLUMNS,
                start_date=None, end_date=None,
                force=force,
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
