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
doesn't exist, or whose real schema is missing a REQUIRED column, is logged
and skipped rather than crashing the run.

Columns come in two tiers per table (see each *_REQUIRED_COLUMNS /
*_OPTIONAL_COLUMNS pair below):
  - required: load-bearing payload/join-key/point-in-time-validity columns.
    Missing ANY of these aborts that table's pull entirely (logged as
    "missing_required_columns"), because a result missing one of these would
    look complete in the manifest while silently lacking something a
    downstream join or calculation depends on.
  - optional: genuinely speculative "grab it now since this is a one-shot
    pull" extras (transaction-cost fields, GICS sector, analyst-count detail
    beyond what this project's Phase I signal needs, etc.) whose exact name
    can vary by WRDS account vintage/subscription tier. A missing optional
    column is dropped from that table's query and noted in the manifest's
    "optional_columns_missing" field -- visible, but never fatal.
Every column named in either tier for a given table IS requested from the
real query when present; only a missing REQUIRED column can abort the pull.

Every table's outcome -- success, skip, or failure -- is appended to
data/metadata/wrds_pull_manifest.csv immediately after that table's attempt,
not buffered in memory until the end, so an interrupted run still leaves an
accurate manifest for everything it got through. Error text written to that
manifest is sanitized first (see _sanitize_error_text) since the manifest is
git-tracked.

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
from dataclasses import dataclass, field
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

# Per-table required/optional column lists for every pull_table() call in
# main(), pulled out to module level (rather than inline list literals in
# main()) for one reason: tests/test_pull_wrds_data.py needs these same lists
# to build fake schemas that never spuriously fail a table's pull, and a
# previous round's hand-copied duplicate of that set drifted out of sync
# with main()'s real calls. Defining the lists once here and having BOTH
# main() and the test suite import these same constants makes that kind of
# drift structurally impossible instead of merely policed by hand -- there is
# only one place a column ever gets added or removed for a given table.
#
# required = aborts the whole table's pull if missing from the real schema.
# optional = dropped from the query (and noted in the manifest) if missing;
#            never aborts the pull.
#
# The required/optional split below reflects what this project's Phase I
# signal-and-returns equity study actually needs as load-bearing vs. what was
# added purely as "grab it now, it's a one-shot pull" speculation (see the
# per-table reasoning comments at each constant).

# ibes.statsumu_epsus: meanest/fpi/fpedats/statpers/ticker are the consensus
# estimate itself, its forecast-horizon identification, and its snapshot
# date -- without any of them this table has no usable payload at all.
# numest/stdev are also promoted to required (not merely nice-to-have):
# this project's earnings-surprise construction uses analyst dispersion
# (stdev) and coverage count (numest) directly, so losing either one would
# silently degrade the surprise measure rather than just being missing
# color. Everything else here is genuinely optional detail.
IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS = [
    "ticker", "statpers", "fpedats", "fpi", "meanest", "numest", "stdev",
]
IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS = [
    "cusip", "oftic", "cname", "measure", "fiscalp", "curcode", "usfirm",
    "numup", "numdown", "medest", "highest", "lowest",
]
IBES_STATSUMU_EPSUS_COLUMNS = (
    IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS + IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS
)

# ibes.actu_epsus: ticker/anndats/value are the join key, the event date
# itself, and the actual reported EPS -- the whole reason this table is
# pulled. Everything else is identifying/descriptive color that doesn't gate
# whether this table is usable.
IBES_ACTU_EPSUS_REQUIRED_COLUMNS = ["ticker", "anndats", "value"]
IBES_ACTU_EPSUS_OPTIONAL_COLUMNS = [
    "cusip", "oftic", "cname", "pends", "anntims", "curr_act",
]
IBES_ACTU_EPSUS_COLUMNS = IBES_ACTU_EPSUS_REQUIRED_COLUMNS + IBES_ACTU_EPSUS_OPTIONAL_COLUMNS

IBES_IDSUM_REQUIRED_COLUMNS = ["ticker", "cusip"]
IBES_IDSUM_OPTIONAL_COLUMNS = ["cname", "oftic", "sdates"]
IBES_IDSUM_COLUMNS = IBES_IDSUM_REQUIRED_COLUMNS + IBES_IDSUM_OPTIONAL_COLUMNS

# detu_epsus (unadjusted), NOT det_epsus (split-adjusted): the other two
# I/B/E/S tables above (statsumu_epsus, actu_epsus) are both split-UNADJUSTED
# (that's what the "u" in their names means), so pulling the split-ADJUSTED
# detail file would mix incompatible adjustment bases and couldn't be
# combined with them. The adjusted file also carries look-ahead: its
# adjustment factor isn't knowable until the future split actually happens.
#
# ticker/fpedats/value are required: the join key, forecast-period
# identification, and the actual estimate value -- without any of these the
# table is unusable. Everything else (including revdats/revtims, the
# revision-date fields the WHERE clause below filters on) is downgraded to
# optional: this table's exact column names are an unverified guess for this
# account (never spot-checked against a real describe_table() call), so a
# renamed column here shouldn't take down the whole table. NOTE the one
# sharp edge this creates: if revdats itself turns out to be genuinely
# absent from this account's schema, the WHERE clause below (which filters
# on revdats) will still reference it and the query will fail at run time --
# that failure is caught and logged as "query_failed" (a real, visible
# failure), not silently swallowed, so this is a safe (if slightly
# unpolished) degradation rather than a correctness bug.
IBES_DETU_EPSUS_REQUIRED_COLUMNS = ["ticker", "fpedats", "value"]
IBES_DETU_EPSUS_OPTIONAL_COLUMNS = [
    "cusip", "estimator", "analys", "revdats", "revtims", "measure",
]
IBES_DETU_EPSUS_COLUMNS = IBES_DETU_EPSUS_REQUIRED_COLUMNS + IBES_DETU_EPSUS_OPTIONAL_COLUMNS

# crsp.dsf: permno/date/ret are the join key, the trading date, and the
# return itself. prc/shrout/cfacpr/cfacshr are required too: market-cap
# weighting and split/dividend adjustment (via the cfac* factors) are
# core to this project's signal-and-returns construction, not extras.
# retx/vol/bid/ask/bidlo/askhi/openprc/numtrd are downgraded to optional:
# retx is redundant with ret for this phase's purposes (no dividend-only
# analysis planned yet), and the rest exist purely for a future
# transaction-cost/capacity phase this project isn't in yet.
CRSP_DSF_REQUIRED_COLUMNS = ["permno", "date", "ret", "prc", "shrout", "cfacpr", "cfacshr"]
CRSP_DSF_OPTIONAL_COLUMNS = [
    "retx", "vol", "bid", "ask", "bidlo", "askhi", "openprc", "numtrd",
]
CRSP_DSF_COLUMNS = CRSP_DSF_REQUIRED_COLUMNS + CRSP_DSF_OPTIONAL_COLUMNS

# crsp.dsi: date plus at least one of the value-weighted return / S&P
# benchmark is the whole payload this table exists for -- vwretd and
# sprtrn are both kept required (either one alone would still let a
# market-adjustment be computed, but both are cheap to require and this is
# a one-shot pull). vwretx/ewretd/ewretx are equal-weighted or ex-dividend
# variants this project doesn't need for Phase I.
CRSP_DSI_REQUIRED_COLUMNS = ["date", "vwretd", "sprtrn"]
CRSP_DSI_OPTIONAL_COLUMNS = ["vwretx", "ewretd", "ewretx"]
CRSP_DSI_COLUMNS = CRSP_DSI_REQUIRED_COLUMNS + CRSP_DSI_OPTIONAL_COLUMNS

# crsp.dsedelist: permno/dlstdt/dlret are the join key, the delisting date,
# and the delisting return itself -- without dlret this table has no
# payload, just the fact that a delisting happened with no return impact to
# attribute to it. dlstcd (the delisting reason code) is nice for
# diagnostics but not load-bearing for the return series itself.
CRSP_DSEDELIST_REQUIRED_COLUMNS = ["permno", "dlstdt", "dlret"]
CRSP_DSEDELIST_OPTIONAL_COLUMNS = ["dlstcd"]
CRSP_DSEDELIST_COLUMNS = CRSP_DSEDELIST_REQUIRED_COLUMNS + CRSP_DSEDELIST_OPTIONAL_COLUMNS

# crsp.stocknames: this table's entire reason for being pulled is
# point-in-time identity -- permno (the join key), namedt/nameenddt (the
# validity window), ticker, and the CUSIP pair (cusip = header/most-recent,
# ncusip = the one actually in effect during [namedt, nameenddt]) are all
# required for that reason. permco/comnam/shrcd/exchcd/siccd are
# descriptive/classification extras that are useful but not what this
# table's pull is justified on.
CRSP_STOCKNAMES_REQUIRED_COLUMNS = [
    "permno", "namedt", "nameenddt", "ticker", "cusip", "ncusip",
]
CRSP_STOCKNAMES_OPTIONAL_COLUMNS = ["permco", "comnam", "shrcd", "exchcd", "siccd"]
CRSP_STOCKNAMES_COLUMNS = (
    CRSP_STOCKNAMES_REQUIRED_COLUMNS + CRSP_STOCKNAMES_OPTIONAL_COLUMNS
)

# comp.fundq: gvkey/datadate are the join key and report date; rdq is
# Compustat's standard PEAD event-date field. indfmt/datafmt/popsrc/consol
# are required because COMPUSTAT_STD_FILTER (below) filters on exactly
# these four columns -- if any were silently missing from this account's
# schema, the WHERE clause referencing them would be a raw SQL error at
# query time rather than a graceful skip; requiring them means the
# missing-column check aborts BEFORE the query is ever built. The actual
# financial-statement fields (fyearq/fqtr/atq/ceqq/saleq/niq/epspxq) are
# useful but not required to abort the whole table if one is renamed.
COMP_FUNDQ_REQUIRED_COLUMNS = [
    "gvkey", "datadate", "indfmt", "datafmt", "popsrc", "consol", "rdq",
]
COMP_FUNDQ_OPTIONAL_COLUMNS = ["fyearq", "fqtr", "atq", "ceqq", "saleq", "niq", "epspxq"]
COMP_FUNDQ_COLUMNS = COMP_FUNDQ_REQUIRED_COLUMNS + COMP_FUNDQ_OPTIONAL_COLUMNS

# comp.company: gvkey/conm/sic are the join key, company name, and current
# SIC classification. gsector (GICS sector) is downgraded to optional: GICS
# coverage varies by subscription tier, and it's already documented
# elsewhere as current-snapshot-only (not point-in-time), so it's a
# nice-to-have, not load-bearing.
COMP_COMPANY_REQUIRED_COLUMNS = ["gvkey", "conm", "sic"]
COMP_COMPANY_OPTIONAL_COLUMNS = ["gsector"]
COMP_COMPANY_COLUMNS = COMP_COMPANY_REQUIRED_COLUMNS + COMP_COMPANY_OPTIONAL_COLUMNS

# comp.funda: same indfmt/datafmt/popsrc/consol reasoning as fundq above
# (COMPUSTAT_STD_FILTER filters on these). fyear/at/ceq/sale/ni/epspx are
# useful annual controls (book-to-market, etc.) but not required to abort
# the whole table if one is renamed.
COMP_FUNDA_REQUIRED_COLUMNS = ["gvkey", "datadate", "indfmt", "datafmt", "popsrc", "consol"]
COMP_FUNDA_OPTIONAL_COLUMNS = ["fyear", "at", "ceq", "sale", "ni", "epspx"]
COMP_FUNDA_COLUMNS = COMP_FUNDA_REQUIRED_COLUMNS + COMP_FUNDA_OPTIONAL_COLUMNS

# wrdsapps.ibcrsphist: ticker/permno/sdate/edate are the link itself plus
# its point-in-time validity window -- without any of them this table
# can't do the one thing it exists for. ncusip/score are nice-to-have
# corroborating fields for the link, not load-bearing.
WRDSAPPS_IBCRSPHIST_REQUIRED_COLUMNS = ["ticker", "permno", "sdate", "edate"]
WRDSAPPS_IBCRSPHIST_OPTIONAL_COLUMNS = ["ncusip", "score"]
WRDSAPPS_IBCRSPHIST_COLUMNS = (
    WRDSAPPS_IBCRSPHIST_REQUIRED_COLUMNS + WRDSAPPS_IBCRSPHIST_OPTIONAL_COLUMNS
)

# crsp.ccmxpf_lnkhist: gvkey/lpermno are the join keys on each side;
# linkdt/linkenddt are the point-in-time link validity bounds, without
# which this table can silently misattribute a gvkey/permno link outside
# its actually-valid window -- all four required for that reason.
# linktype/linkprim (link quality/primacy flags) are useful for filtering
# to the "best" link but not required for the link to exist at all.
CRSP_CCMXPF_LNKHIST_REQUIRED_COLUMNS = ["gvkey", "lpermno", "linkdt", "linkenddt"]
CRSP_CCMXPF_LNKHIST_OPTIONAL_COLUMNS = ["linktype", "linkprim"]
CRSP_CCMXPF_LNKHIST_COLUMNS = (
    CRSP_CCMXPF_LNKHIST_REQUIRED_COLUMNS + CRSP_CCMXPF_LNKHIST_OPTIONAL_COLUMNS
)

MANIFEST_FIELDNAMES = [
    "library", "table", "status", "rows", "path", "missing_columns",
    "optional_columns_missing", "error", "pulled_at", "start_date", "end_date",
]

# Manifest error-text sanitization (Fix 4): data/metadata/wrds_pull_manifest.csv
# is git-tracked (a deliberate choice from a prior round), and exception text
# from psycopg2/sqlalchemy connection failures can embed hostnames, usernames,
# or other connection details. _MAX_ERROR_LEN caps how much raw exception text
# ever reaches that file.
_MAX_ERROR_LEN = 300


def _sanitize_error_text(text: str) -> str:
    """Best-effort scrub of an exception message before it's written to the
    git-tracked manifest CSV.

    Two simple steps, deliberately not a general-purpose credential
    scrubber:
      1. If the literal WRDS_USERNAME from the environment appears verbatim
         in the text, redact it. This is the most common way a
         connection-error message leaks account identity (e.g.
         "FATAL: password authentication failed for user \"jdoe123\"").
      2. Truncate to _MAX_ERROR_LEN characters. The manifest only needs
         enough text to identify what failed, not a full stack trace -- and
         the longer an error message is, the more likely it accidentally
         embeds a hostname, DSN, or other connection detail.

    Limits (documented, not hidden): this does NOT parse connection-string
    syntax, does NOT redact hostnames/ports/database names, and won't catch
    a username that differs from the current process's WRDS_USERNAME (e.g.
    an error surfaced from a stale or alternate credential, or one where the
    username is embedded in a different case). psycopg2/sqlalchemy error
    text does not normally echo the password itself, only host/user, so
    password leakage specifically is not the primary risk this addresses.
    This is "don't casually commit the account name to git history", not a
    substitute for reviewing manifest diffs before a push.
    """
    if not text:
        return text
    username = os.environ.get("WRDS_USERNAME", "")
    if username:
        text = text.replace(username, "[REDACTED_WRDS_USERNAME]")
    return text[:_MAX_ERROR_LEN]


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

    Any "error" field is sanitized (see _sanitize_error_text) right here,
    at the single choke point where a row actually reaches the git-tracked
    CSV -- rather than at each call site that sets "error" -- so a future
    call site can't forget to sanitize its own error text.
    """
    manifest_path = Path(manifest_path) if manifest_path is not None else MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    if row.get("error"):
        row["error"] = _sanitize_error_text(str(row["error"]))
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
    printed summary -- kept un-sanitized since it's console-only, never
    committed) and the on-disk manifest (the durable, git-tracked record,
    sanitized by append_manifest_row)."""
    row = dict(row)
    row.setdefault(
        "pulled_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    )
    log.append(row)
    append_manifest_row(row, manifest_path=manifest_path)


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



# Round 5's approach (removed) tried to *infer* the pinned schema from real
# chunk data (widening integers to float64, buffering a few chunks to
# resolve any column that showed pa.null() in an early chunk). That was
# verified NOT to work against the real wrds/pandas driver contract
# (dtype_backend="numpy_nullable", read directly from the installed
# pandas==2.2.3 source, pandas.io.sql._convert_arrays_to_dataframe): a
# column that's entirely NULL in a chunk does NOT come back as pa.null() --
# it comes back as pandas `string[python]` (StringDtype), which pyarrow then
# reports as a concrete `string` field, not `null`. Confirmed directly:
#
#   >>> df = pandas.io.sql._convert_arrays_to_dataframe(
#   ...     [(1, None), (2, None)], ["permno", "numtrd"],
#   ...     dtype_backend="numpy_nullable")
#   >>> df.dtypes
#   permno             Int64
#   numtrd    string[python]
#   >>> pyarrow.Table.from_pandas(df, preserve_index=False).schema
#   permno: int64
#   numtrd: string
#
# Round 5's "still unresolved" check (`any(pa.types.is_null(f.type) ...)`)
# never fires in this case -- the buffer stops on the very first chunk,
# pins `numtrd` as `string`, and every subsequent chunk's real integer
# values (e.g. 100, 200) get cast DOWN into strings ("100", "200") for the
# whole table, silently and with no error. This was verified directly by
# running round 5's buffering/merge logic against the real-driver chunk
# above (see the round's PR notes / verification records), not merely
# assumed from reading the diff.
#
# The fix: don't infer the schema from data at all. db.describe_table()
# already returns the database's own declared SQL type for every column
# (the `type` column, e.g. INTEGER, DOUBLE PRECISION, DATE, VARCHAR) --
# deterministic and known before a single row is fetched. Build the pinned
# pyarrow schema from THAT, once, before opening the ParquetWriter. See
# _sql_type_to_pyarrow_type / _build_pinned_schema below.

# Base SQL type name (after stripping any parenthesized length/precision,
# e.g. "VARCHAR(32)" -> "VARCHAR", "NUMERIC(10, 2)" -> "NUMERIC") -> pyarrow
# type. Vocabulary is standard PostgreSQL/SQLAlchemy type-name conventions
# (WRDS is Postgres-backed) -- verified directly against sqlalchemy's own
# type objects (str(sqlalchemy.dialects.postgresql.INTEGER()) == "INTEGER",
# etc.), not guessed blind.
_SQL_INTEGER_TYPES = {"INTEGER", "BIGINT", "SMALLINT", "INT", "INT2", "INT4", "INT8"}
_SQL_FLOAT_TYPES = {
    "DOUBLE PRECISION", "REAL", "NUMERIC", "DECIMAL", "FLOAT",
    "FLOAT4", "FLOAT8",
}
# Date/time types are deliberately left as pa.string() -- matching this
# script's existing behavior of writing dates as ISO strings. Changing that
# is explicitly out of scope for this round (see PR description); this set
# exists so a date/time column is routed to the SAME pa.string() outcome as
# everything else, deliberately, rather than falling through to the
# unmapped-type warning path.
_SQL_DATE_TIME_TYPES = {
    "DATE", "TIME", "TIMESTAMP",
    "TIME WITHOUT TIME ZONE", "TIME WITH TIME ZONE",
    "TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP WITH TIME ZONE",
}
_SQL_TEXT_TYPES = {
    "VARCHAR", "CHARACTER VARYING", "TEXT", "CHARACTER", "CHAR", "BPCHAR",
    "NAME",
}
_SQL_BOOLEAN_TYPES = {"BOOLEAN", "BOOL"}


def _sql_type_to_pyarrow_type(sql_type, column=None):
    """Map one declared SQL type (as found in db.describe_table()'s `type`
    column, stringified) to the pyarrow type used to pin the streaming
    Parquet schema.

    `sql_type` may be None (e.g. a test double that doesn't know a given
    column's real type) or any string -- normalized by uppercasing and
    stripping a parenthesized length/precision suffix before matching.
    Anything not recognized falls back to pa.string() and is logged as a
    warning (not silently mishandled) so an unmapped type is visible.
    """
    if sql_type is None:
        print(
            f"  WARNING: no declared SQL type known for column "
            f"{column!r}; defaulting to pa.string().",
            flush=True,
        )
        return pa.string()

    base = str(sql_type).strip().upper().split("(", 1)[0].strip()

    if base in _SQL_INTEGER_TYPES:
        return pa.int64()
    if base in _SQL_FLOAT_TYPES:
        return pa.float64()
    if base in _SQL_DATE_TIME_TYPES:
        return pa.string()
    if base in _SQL_TEXT_TYPES:
        return pa.string()
    if base in _SQL_BOOLEAN_TYPES:
        return pa.bool_()

    print(
        f"  WARNING: unmapped SQL type {sql_type!r} for column {column!r} "
        f"-- defaulting to pa.string() rather than guessing.",
        flush=True,
    )
    return pa.string()


def _column_sql_types(schema_df) -> dict:
    """Extract a {lowercased column name: declared SQL type string} mapping
    from the DataFrame returned by db.describe_table(library, table) --
    specifically its `name` and `type` columns. This is the authoritative,
    data-independent source _build_pinned_schema uses; it reflects the
    database's OWN declared type for every column, known before any row is
    ever fetched.
    """
    return {
        str(name).lower(): sql_type
        for name, sql_type in zip(schema_df["name"], schema_df["type"])
    }


def _build_pinned_schema(columns, column_sql_types) -> "pa.Schema":
    """Build the pyarrow.Schema the streaming Parquet writer is pinned to,
    for exactly `columns` (the required + present-optional columns actually
    being selected), from `column_sql_types` (see _column_sql_types) --
    BEFORE any data is fetched. Deterministic: depends only on the
    database's declared types, never on which values happen to land in
    which chunk, so it is identical run to run regardless of chunk order or
    composition.
    """
    fields = [
        pa.field(
            col,
            _sql_type_to_pyarrow_type(column_sql_types.get(col.lower()), column=col),
            nullable=True,
        )
        for col in columns
    ]
    return pa.schema(fields)


def _stream_query_to_parquet(db, library, table, columns, where_sql, out_path, pinned_schema):
    """Run the real `columns` query against `library`.`table` (filtered by
    `where_sql`, if given) using chunked streaming (`return_iter=True`), and
    write the result to `out_path` incrementally via a real
    `pyarrow.parquet.ParquetWriter` -- bounded memory (a small constant
    multiple of one chunk, never the whole table held at once).

    SCHEMA PINNING (Fix 3 round 2, replacing round 5's data-inference
    approach entirely):

    `pinned_schema` is built by the caller (pull_table, via
    _build_pinned_schema) from the database's OWN declared column types
    (db.describe_table()'s `type` column) -- BEFORE this function is ever
    called, and therefore before a single row has been fetched. Every chunk
    is cast to `pinned_schema` (`.cast(pinned_schema, safe=False)`) and
    written via ParquetWriter, which is opened against `pinned_schema`
    immediately, before the first chunk is even requested.

    WHY NOT INFER THE SCHEMA FROM DATA (round 5's approach, removed): round
    5 widened integer fields to float64 and buffered a few chunks to
    "resolve" any column that showed pa.null() in an early chunk. That
    logic never actually fires against the real driver: verified directly
    against the installed pandas==2.2.3 source
    (pandas.io.sql._convert_arrays_to_dataframe, used internally by
    wrds.Connection.raw_sql's dtype_backend="numpy_nullable" path) that a
    column which is entirely NULL in a chunk comes back as pandas
    `string[python]` (StringDtype), NOT pa.null() --

        >>> df = pandas.io.sql._convert_arrays_to_dataframe(
        ...     [(1, None), (2, None)], ["permno", "numtrd"],
        ...     dtype_backend="numpy_nullable")
        >>> pyarrow.Table.from_pandas(df, preserve_index=False).schema
        permno: int64
        numtrd: string

    so round 5's "still unresolved" check (looking for pa.null()) never
    triggers, the buffer accepts `numtrd: string` from the very first
    all-NULL chunk as final, and every later chunk's real integer values
    silently get cast DOWN to strings for the whole table -- no error, no
    warning. This was confirmed by running round 5's actual buffering/merge
    functions against this real-driver chunk output, not just inferred from
    the diff.

    Building the pinned schema from declared SQL types up front sidesteps
    this class of bug entirely: the schema no longer depends on which
    values happen to land in which chunk, so there is nothing to buffer,
    infer, or get wrong-by-coincidence. It is also identical run to run
    regardless of chunk order/composition.

    DEVIATION FROM THE ORIGINALLY SUGGESTED "zero-row probe query" APPROACH
    (documented here because this project's verification discipline asks
    for this, and it's still relevant context even though it no longer
    matters for schema pinning specifically): a zero-row `SELECT ... WHERE
    1=0` query does NOT give per-column type info either -- pandas takes the
    `DataFrame(columns=columns)` fallback branch for a genuinely zero-row
    result, and every column comes back plain `object` dtype (pa.null() for
    every field), regardless of the column's real type. db.describe_table()
    (already called by pull_table for the required/optional column check)
    is the actual source of real per-column type info, with no extra query
    needed -- consistent with this project's "minimize WRDS query volume"
    constraint.

    The final write goes to a temporary path and is moved into place with
    os.replace() only after it fully succeeds, so a failure partway through
    (e.g. on a --force re-pull) can never truncate or destroy a
    previously-good `out_path` -- it is left completely untouched until the
    new file is known-good.

    Returns (total_rows, wrote_file). Per the real wrds/pandas contract
    (verified directly against pandas.io.sql._query_iterator and
    wrds.Connection.raw_sql's use of pd.read_sql_query(..., chunksize=...)):
    a zero-row query still yields exactly ONE (empty) chunk, never zero
    chunks -- so `wrote_file` is False whenever total_rows == 0 (in which
    case no file is written and out_path, if it already existed, is left
    completely untouched), not merely whenever "no chunks were produced".
    """
    col_list_sql = ", ".join(columns)
    sql = f"SELECT {col_list_sql} FROM {library}.{table}"
    if where_sql:
        sql += f" WHERE {where_sql}"

    total_rows = 0
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    writer = None
    try:
        # The schema is fully known before any data is fetched, so the
        # writer is opened immediately -- no buffering, no "resolve as we
        # go" state at all.
        writer = pq.ParquetWriter(str(tmp_path), pinned_schema)
        for chunk in db.raw_sql(sql, return_iter=True):
            total_rows += len(chunk)
            chunk_table = pa.Table.from_pandas(chunk, preserve_index=False)
            writer.write_table(chunk_table.cast(pinned_schema, safe=False))
    except (Exception, KeyboardInterrupt):
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    writer.close()

    if total_rows == 0:
        # Per the verified contract above, this is the single-empty-chunk
        # case, not a "zero chunks" case -- the writer above did open and
        # write one empty table to tmp_path. Discard it: a genuinely empty
        # result must never overwrite (or create alongside) out_path.
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return 0, False

    os.replace(tmp_path, out_path)
    return total_rows, True


def pull_table(
    db,
    log,
    library,
    table,
    required_columns,
    optional_columns=None,
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

    `required_columns` is the must-have subset: every one of these must be
    present in the real schema (per db.describe_table()) or the pull does
    not run at all (status "missing_required_columns", logged with the
    actual missing names) -- never a partial column set for a REQUIRED
    column.

    `optional_columns` (default: none) is the nice-to-have subset: any of
    these that's present in the real schema is included in the query
    opportunistically; any that's absent is simply dropped (not aborted),
    and every dropped optional column across the whole call is recorded in
    the manifest's "optional_columns_missing" field so the gap is visible
    without being fatal.

    `stream=True` uses `_stream_query_to_parquet` (schema pinned from
    db.describe_table()'s declared column types, via _build_pinned_schema,
    then written incrementally via a real ParquetWriter -- see that
    function's docstring) instead of loading the full result into a
    DataFrame first; use it for any table that could plausibly be large.
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
    optional_columns = list(optional_columns or [])
    for col in required_columns:
        _validate_sql_identifier("column", col)
    for col in optional_columns:
        _validate_sql_identifier("column", col)

    out_name = out_name or table
    raw_dir = raw_dir if raw_dir is not None else RAW_DIR
    out_dir = raw_dir / library
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{out_name}.parquet"

    def _log(status, rows=0, path="", missing_columns="", optional_columns_missing="", error=""):
        record(
            log,
            {
                "library": library,
                "table": table,
                "status": status,
                "rows": rows,
                "path": path,
                "missing_columns": missing_columns,
                "optional_columns_missing": optional_columns_missing,
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
        # Keep the declared SQL type per column too (not just the name) --
        # this is what the streaming path's pinned pyarrow schema is built
        # from (see _build_pinned_schema), instead of anything inferred
        # from data.
        column_sql_types = _column_sql_types(schema)
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

    missing_required = [c for c in required_columns if c.lower() not in actual_columns]
    if missing_required:
        print(
            f"  {library}.{table}: required columns missing, NOT querying: "
            f"{missing_required}",
            flush=True,
        )
        _log(
            "missing_required_columns",
            missing_columns=",".join(missing_required),
            error=f"required columns missing: {missing_required}",
        )
        return

    present_optional = [c for c in optional_columns if c.lower() in actual_columns]
    missing_optional = [c for c in optional_columns if c.lower() not in actual_columns]
    if missing_optional:
        print(
            f"  {library}.{table}: optional columns not in schema, dropping "
            f"(NOT aborting the pull): {missing_optional}",
            flush=True,
        )
    optional_missing_str = ",".join(missing_optional)

    query_columns = list(required_columns) + present_optional

    sql = f"SELECT {', '.join(query_columns)} FROM {library}.{table}"
    if where_sql:
        sql += f" WHERE {where_sql}"

    try:
        print(f"Pulling {library}.{table} -> {_rel(out_path)} ...", flush=True)
        if stream:
            pinned_schema = _build_pinned_schema(query_columns, column_sql_types)
            rows, _wrote_file = _stream_query_to_parquet(
                db, library, table, query_columns, where_sql, out_path, pinned_schema
            )
        else:
            df = db.raw_sql(sql)
            rows = len(df)

        if rows == 0:
            if out_path.exists():
                # A --force re-pull (or, non-streaming, any run) whose query
                # legitimately returned zero rows: gate strictly on the
                # actual row count, not on "did the query return an
                # iterator" / "were there any chunks" -- a zero-row query
                # still produces exactly one (empty) chunk in the streaming
                # case, never zero chunks (verified against pandas'
                # chunked-read implementation), so that was never a safe
                # signal. The previous file at out_path was never touched
                # (streaming: no real chunk means no replace(); non-
                # streaming: we return before ever writing), so "rows: 0"
                # alone would misleadingly read as "this table is empty"
                # when actually the old data is still sitting on disk --
                # log the distinct status WITH the path of the file that
                # was left in place.
                print(
                    "  0 rows returned; existing file at out_path left "
                    "untouched (not overwritten with an empty result).",
                    flush=True,
                )
                _log(
                    "ok_empty_query_kept_existing",
                    rows=0,
                    path=_rel(out_path),
                    optional_columns_missing=optional_missing_str,
                )
            else:
                print("  0 rows returned; no file written.", flush=True)
                _log("ok", rows=0, optional_columns_missing=optional_missing_str)
            return

        if not stream:
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

        print(f"  {rows:,} rows.", flush=True)
        _log(
            "ok", rows=rows, path=_rel(out_path),
            optional_columns_missing=optional_missing_str,
        )
    except Exception as e:
        print(f"  FAILED: {e}", flush=True)
        # Both write paths above are now atomic (write-to-temp, then
        # os.replace only on full success) and clean up their own temp file
        # on failure -- so out_path itself is never touched by a failed
        # write and must be left alone here, whether it pre-existed (a
        # previously-good file from an earlier run) or never existed.
        _log(
            "query_failed", error=str(e),
            optional_columns_missing=optional_missing_str,
        )


# ---------------------------------------------------------------------------
# Phase B: local universe resolution (no WRDS query).
#
# This project only ever needs the ~8,358 securities that actually have real
# options data in the local archive -- not the entire US equity market that
# an unfiltered date-only pull previously produced. `data/metadata/
# optionmetrics_universe.parquet` (current secid/cusip/ticker/issuer/sic) and
# `..._history.parquet` (one row per historical CUSIP/ticker change for the
# same secids) already exist locally (built elsewhere, not touched here) --
# this module resolves those OptionMetrics secids to CRSP PERMNO, Compustat
# GVKEY, and I/B/E/S ticker using the small reference tables Phase A already
# pulled, so Phase C's big-table pulls (crsp.dsf, comp.fundq/funda, the
# ibes.*_epsus family) can be filtered down to just what this project needs.
#
# A security can have had multiple CUSIPs/tickers over 1996-2013 (renames,
# spinoffs, etc.), so the CUSIP side of the join checks BOTH the current
# file and the history file, not just today's CUSIP -- and CRSP itself
# carries two CUSIP-shaped columns per row (`cusip`, the header/most-recent
# value, and `ncusip`, the point-in-time value) -- either one matching an
# OM CUSIP counts as a link.
# ---------------------------------------------------------------------------


class UniverseResolutionError(RuntimeError):
    """Raised whenever Phase B's local resolution can't proceed safely: a
    required input file is missing, or a loaded DataFrame is missing a
    column this resolution structurally depends on.

    main() catches this and aborts the ENTIRE run before attempting any of
    Phase C's expensive WRDS queries -- a broken resolution step must never
    be allowed to silently fall through to an empty/missing filter that then
    pulls either the whole market (no filter) or nothing (an accidentally
    always-false filter) from WRDS.
    """


@dataclass
class ResolvedUniverse:
    """The output of resolve_universe(): the deduplicated ID lists Phase C's
    pull_table() calls filter on, plus the counts needed for the mandatory
    auditability summary (see resolve_universe's docstring).

    `permnos`/`gvkeys` are ints; `ibes_tickers` are strings. Every list is
    the UNION of every ID reachable from any resolved OM secid -- including
    ones flagged as ambiguous in ambiguous_links.csv -- because for a
    universe FILTER (as opposed to a clean one-to-one attribution join)
    over-inclusion is the safe direction: pulling a handful of extra
    PERMNOs/GVKEYs/tickers costs a little extra data, silently dropping one
    because its link happened to be ambiguous costs a security's worth of
    data this project actually needs.
    """

    permnos: list = field(default_factory=list)
    gvkeys: list = field(default_factory=list)
    ibes_tickers: list = field(default_factory=list)
    n_secids_total: int = 0
    n_secids_resolved: int = 0
    n_ambiguous_links: int = 0


def _require_columns(df: pd.DataFrame, columns, context: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise UniverseResolutionError(
            f"{context}: missing expected column(s) {missing} -- actual "
            f"columns are {list(df.columns)}"
        )


def _load_parquet_or_raise(path: Path, context: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise UniverseResolutionError(
            f"{context}: expected input file not found at {path} -- Phase A "
            f"must pull this table successfully before Phase B can resolve "
            f"the universe."
        )
    try:
        return pd.read_parquet(path)
    except Exception as e:
        raise UniverseResolutionError(
            f"{context}: failed to read {path} as Parquet: {e}"
        ) from e


def _norm_cusip(series: pd.Series) -> pd.Series:
    """Normalize a CUSIP-shaped column to a comparable string form: cast to
    str, strip whitespace, uppercase. CUSIPs may have leading zeros (e.g.
    "04341310"), so they must be compared/joined as strings, never as
    numbers."""
    return series.astype(str).str.strip().str.upper()


def _agg_unique_join(df: pd.DataFrame, key_col: str, val_col: str) -> dict:
    """Return {key: comma-joined sorted-unique str(val)} for a `key_col` ->
    `val_col` mapping that may legitimately be one-to-many (e.g. one PERMNO
    linked to more than one GVKEY across CCM's link history). A key with
    exactly one distinct value still comes back as that one value with no
    comma; a key with more than one is visibly ambiguous in the output
    (comma-separated) without needing a second lookup."""
    out = {}
    for key, group in df.groupby(key_col)[val_col]:
        vals = sorted({str(v) for v in group.dropna()})
        out[key] = ",".join(vals) if vals else None
    return out


def resolve_universe(
    om_universe_path,
    om_universe_history_path,
    stocknames_path,
    ccmxpf_lnkhist_path,
    ibcrsphist_path,
    unresolved_secids_out,
    ambiguous_links_out,
    resolved_ids_out,
) -> ResolvedUniverse:
    """Resolve the OptionMetrics universe (current + historical CUSIPs) to
    CRSP PERMNO, Compustat GVKEY, and I/B/E/S ticker, purely locally (no WRDS
    query) -- see the module comment above this class for why.

    Steps (mirrors memory/phase1-equity-pead-plan.md's "Update 2026-08-12"
    section):
      1. Load the two OM universe files and the three just-pulled Phase A
         reference tables (crsp.stocknames, crsp.ccmxpf_lnkhist,
         wrdsapps.ibcrsphist). Any missing file or missing required column
         raises UniverseResolutionError immediately -- see
         _load_parquet_or_raise/_require_columns.
      2. Union the current + historical CUSIPs per secid.
      3. Join against crsp.stocknames on EITHER cusip OR ncusip to get the
         resolved PERMNO(s) per secid.
      4. Join the resolved PERMNOs against crsp.ccmxpf_lnkhist (on lpermno)
         to get GVKEY(s), and against wrdsapps.ibcrsphist (on permno) to get
         I/B/E/S ticker(s).
      5. Write the three auditability artifacts (unresolved_secids.csv,
         ambiguous_links.csv, resolved_ids_out) and print a one-line summary.

    Returns a ResolvedUniverse with the deduplicated PERMNO/GVKEY/ticker
    filter lists Phase C's pull_table() calls use, favoring over-inclusion
    for any ambiguous link (see ResolvedUniverse's docstring).
    """
    om = _load_parquet_or_raise(om_universe_path, "optionmetrics_universe.parquet")
    om_hist = _load_parquet_or_raise(
        om_universe_history_path, "optionmetrics_universe_history.parquet"
    )
    stocknames = _load_parquet_or_raise(stocknames_path, "crsp.stocknames pull")
    ccmxpf = _load_parquet_or_raise(ccmxpf_lnkhist_path, "crsp.ccmxpf_lnkhist pull")
    ibcrsphist = _load_parquet_or_raise(ibcrsphist_path, "wrdsapps.ibcrsphist pull")

    _require_columns(om, ["secid", "cusip", "ticker", "issuer"], "optionmetrics_universe.parquet")
    _require_columns(om_hist, ["secid", "cusip"], "optionmetrics_universe_history.parquet")
    _require_columns(stocknames, ["permno", "cusip", "ncusip"], "crsp.stocknames pull")
    _require_columns(ccmxpf, ["lpermno", "gvkey"], "crsp.ccmxpf_lnkhist pull")
    _require_columns(ibcrsphist, ["permno", "ticker"], "wrdsapps.ibcrsphist pull")

    om = om.copy()
    om["secid"] = om["secid"].astype("int64")
    om_lookup = om[["secid", "cusip", "ticker", "issuer"]].drop_duplicates(subset=["secid"])
    all_secids = om["secid"].drop_duplicates()

    # --- Union current + historical CUSIPs per secid ---
    om_cusips = pd.concat(
        [
            om.loc[om["cusip"].notna(), ["secid", "cusip"]],
            om_hist.loc[om_hist["cusip"].notna(), ["secid", "cusip"]],
        ],
        ignore_index=True,
    )
    om_cusips["cusip"] = _norm_cusip(om_cusips["cusip"])
    om_cusips = om_cusips.drop_duplicates()

    # --- crsp.stocknames: stack cusip and ncusip into one long (permno,
    # cusip-shaped-value) table -- either column matching an OM cusip counts
    # as a link. ---
    sn = stocknames.dropna(subset=["permno"]).copy()
    sn["permno"] = sn["permno"].astype("int64")
    cusip_side = sn.loc[sn["cusip"].notna(), ["permno", "cusip"]].rename(
        columns={"cusip": "crsp_cusip"}
    )
    ncusip_side = sn.loc[sn["ncusip"].notna(), ["permno", "ncusip"]].rename(
        columns={"ncusip": "crsp_cusip"}
    )
    sn_long = pd.concat([cusip_side, ncusip_side], ignore_index=True)
    sn_long["crsp_cusip"] = _norm_cusip(sn_long["crsp_cusip"])
    sn_long = sn_long.drop_duplicates()

    secid_permno = (
        om_cusips.merge(sn_long, left_on="cusip", right_on="crsp_cusip", how="inner")[
            ["secid", "permno"]
        ]
        .drop_duplicates()
    )

    resolved_secid_set = set(secid_permno["secid"].unique())
    n_total = int(all_secids.shape[0])
    n_resolved = len(resolved_secid_set)

    # --- Unresolved secids: written for a human to spot-check, never
    # silently dropped. NOTE for anyone reading this CSV back
    # programmatically: the `cusip` column is text that can have leading
    # zeros (same caveat as the OM universe Parquet files) -- pd.read_csv()
    # will silently re-infer a value like "00000003" as the int 3 unless you
    # pass dtype={"cusip": str} (or similar) explicitly. ---
    unresolved = om_lookup[~om_lookup["secid"].isin(resolved_secid_set)]
    unresolved.to_csv(unresolved_secids_out, index=False)

    # --- permno -> gvkey (crsp.ccmxpf_lnkhist), restricted to resolved
    # permnos only -- no linkdt/linkenddt window filtering here: this is a
    # broad "which securities need data pulled at all" universe filter, not
    # a point-in-time attribution join, so over-inclusion (every gvkey ever
    # linked to a resolved permno) is the safe direction. ---
    cc = ccmxpf.dropna(subset=["lpermno", "gvkey"]).copy()
    cc["lpermno"] = cc["lpermno"].astype("int64")
    cc["gvkey"] = cc["gvkey"].astype(str).str.strip()
    permno_gvkey = (
        cc.loc[cc["lpermno"].isin(secid_permno["permno"]), ["lpermno", "gvkey"]]
        .rename(columns={"lpermno": "permno"})
        .drop_duplicates()
    )

    # --- permno -> I/B/E/S ticker (wrdsapps.ibcrsphist), same reasoning. ---
    ic = ibcrsphist.dropna(subset=["permno", "ticker"]).copy()
    ic["permno"] = ic["permno"].astype("int64")
    ic["ticker"] = ic["ticker"].astype(str).str.strip()
    permno_ticker = (
        ic.loc[ic["permno"].isin(secid_permno["permno"]), ["permno", "ticker"]]
        .drop_duplicates()
    )

    # --- Ambiguous links: flagged for human review, never silently
    # resolved one way. Three independent stages, each a genuine one-to-many
    # (or many-to-one) case: ---
    ambiguous_frames = []

    secid_permno_counts = secid_permno.groupby("secid")["permno"].nunique()
    multi_permno_secids = set(secid_permno_counts[secid_permno_counts > 1].index)
    permno_secid_counts = secid_permno.groupby("permno")["secid"].nunique()
    multi_secid_permnos = set(permno_secid_counts[permno_secid_counts > 1].index)
    if multi_permno_secids or multi_secid_permnos:
        stage1 = secid_permno[
            secid_permno["secid"].isin(multi_permno_secids)
            | secid_permno["permno"].isin(multi_secid_permnos)
        ].merge(om_lookup, on="secid", how="left")
        stage1 = stage1.rename(columns={"cusip": "om_cusip", "ticker": "om_ticker"})
        stage1["stage"] = "secid_to_permno"
        ambiguous_frames.append(stage1)

    gvkey_counts = permno_gvkey.groupby("permno")["gvkey"].nunique()
    multi_gvkey_permnos = set(gvkey_counts[gvkey_counts > 1].index)
    if multi_gvkey_permnos:
        stage2 = permno_gvkey[permno_gvkey["permno"].isin(multi_gvkey_permnos)].copy()
        stage2["stage"] = "permno_to_gvkey"
        ambiguous_frames.append(stage2)

    ticker_counts = permno_ticker.groupby("permno")["ticker"].nunique()
    multi_ticker_permnos = set(ticker_counts[ticker_counts > 1].index)
    if multi_ticker_permnos:
        stage3 = permno_ticker[permno_ticker["permno"].isin(multi_ticker_permnos)].rename(
            columns={"ticker": "ibes_ticker"}
        )
        stage3["stage"] = "permno_to_ibes_ticker"
        ambiguous_frames.append(stage3)

    ambiguous_columns = [
        "stage", "secid", "permno", "gvkey", "ibes_ticker", "om_cusip", "om_ticker", "issuer",
    ]
    if ambiguous_frames:
        ambiguous_df = pd.concat(ambiguous_frames, ignore_index=True, sort=False)
        for col in ambiguous_columns:
            if col not in ambiguous_df.columns:
                ambiguous_df[col] = None
        ambiguous_df = ambiguous_df[ambiguous_columns]
    else:
        ambiguous_df = pd.DataFrame(columns=ambiguous_columns)
    ambiguous_df.to_csv(ambiguous_links_out, index=False)
    n_ambiguous_links = len(ambiguous_df)

    # --- resolved_universe_ids.parquet: one row per OM secid, nullable
    # where resolution failed, comma-joined where a link was ambiguous (see
    # ResolvedUniverse's docstring for why ambiguous links still count
    # toward the filter lists below even though they're flagged here). ---
    gvkey_by_permno = _agg_unique_join(permno_gvkey, "permno", "gvkey")
    ticker_by_permno = _agg_unique_join(permno_ticker, "permno", "ticker")
    secid_to_permnos = secid_permno.groupby("secid")["permno"].apply(lambda s: sorted(set(s)))

    rows = []
    for secid, om_row in om_lookup.set_index("secid").iterrows():
        permnos_for_secid = secid_to_permnos.get(secid, [])
        if not permnos_for_secid:
            rows.append({
                "secid": secid,
                "cusip": om_row["cusip"],
                "ticker": om_row["ticker"],
                "issuer": om_row["issuer"],
                "permno": None,
                "gvkey": None,
                "ibes_ticker": None,
                "permno_ambiguous": False,
                "gvkey_ambiguous": False,
                "ibes_ticker_ambiguous": False,
            })
            continue
        gvkeys_for_secid = sorted({
            g for p in permnos_for_secid for g in (gvkey_by_permno.get(p) or "").split(",") if g
        })
        tickers_for_secid = sorted({
            t for p in permnos_for_secid for t in (ticker_by_permno.get(p) or "").split(",") if t
        })
        rows.append({
            "secid": secid,
            "cusip": om_row["cusip"],
            "ticker": om_row["ticker"],
            "issuer": om_row["issuer"],
            "permno": ",".join(str(p) for p in permnos_for_secid),
            "gvkey": ",".join(gvkeys_for_secid) if gvkeys_for_secid else None,
            "ibes_ticker": ",".join(tickers_for_secid) if tickers_for_secid else None,
            # Ambiguous in EITHER direction: this secid maps to more than
            # one permno, OR at least one of its permnos is itself shared
            # by more than one secid (many-to-one) -- both are flagged in
            # ambiguous_links.csv's "secid_to_permno" stage, so both must
            # be reflected here too, not just the one-to-many case.
            "permno_ambiguous": (
                len(permnos_for_secid) > 1
                or any(p in multi_secid_permnos for p in permnos_for_secid)
            ),
            "gvkey_ambiguous": len(gvkeys_for_secid) > 1,
            "ibes_ticker_ambiguous": len(tickers_for_secid) > 1,
        })

    resolved_df = pd.DataFrame(rows)
    resolved_df.to_parquet(resolved_ids_out, index=False)

    all_permnos = sorted(int(p) for p in secid_permno["permno"].unique())
    all_gvkeys = sorted(permno_gvkey["gvkey"].unique().tolist())
    all_tickers = sorted(permno_ticker["ticker"].unique().tolist())

    pct = (100.0 * n_resolved / n_total) if n_total else 0.0
    print(
        f"Universe resolution: {n_resolved:,} of {n_total:,} OptionMetrics "
        f"secids resolved to a PERMNO ({pct:.1f}%); {n_ambiguous_links:,} "
        f"ambiguous link(s) flagged; resolved {len(all_permnos):,} distinct "
        f"PERMNOs, {len(all_gvkeys):,} distinct GVKEYs, {len(all_tickers):,} "
        f"distinct I/B/E/S tickers. Details in data/metadata/ "
        f"(unresolved_secids.csv, ambiguous_links.csv, "
        f"resolved_universe_ids.parquet).",
        flush=True,
    )

    return ResolvedUniverse(
        permnos=all_permnos,
        gvkeys=all_gvkeys,
        ibes_tickers=all_tickers,
        n_secids_total=n_total,
        n_secids_resolved=n_resolved,
        n_ambiguous_links=n_ambiguous_links,
    )


# ---------------------------------------------------------------------------
# Phase C's IN (...) clause builders. PERMNO/GVKEY are numeric identifiers,
# safe to interpolate as bare numeric literals once validated as digits-only
# (defensive, consistent with _validate_sql_identifier's existing posture on
# this script's own hardcoded/derived inputs -- these values are WRDS-
# sourced, not external/untrusted input, but the validation costs nothing
# and catches a corrupted resolution result loudly instead of building a
# malformed query). I/B/E/S tickers are strings and get single-quoted with
# embedded quotes escaped -- string interpolation is less trivially safe
# than the numeric case, but adding SQLAlchemy text()-bound-parameter
# support to pull_table's raw_sql call sites would touch the very internals
# this round is explicitly not supposed to change; proper quoting/escaping
# here is the smaller, contained fix for this specific list.
# ---------------------------------------------------------------------------

_NUMERIC_ID_RE = re.compile(r"^-?\d+\Z")


def _validate_numeric_id(value) -> str:
    s = str(value).strip()
    if not _NUMERIC_ID_RE.match(s):
        raise ValueError(
            f"Non-numeric id {value!r} cannot be safely interpolated into a "
            f"numeric IN (...) clause"
        )
    return s


def build_numeric_in_clause(column: str, values) -> str:
    """Build `"<column> IN (1, 2, 3)"` from an iterable of PERMNO/GVKEY-like
    values, after validating every value is digits-only. An empty `values`
    produces `"<column> IN (NULL)"` (matches nothing, since NULL never
    equals anything) rather than the invalid-SQL `"IN ()"` or, worse, an
    omitted filter that would silently pull an unfiltered table."""
    _validate_sql_identifier("column", column)
    ids = sorted({_validate_numeric_id(v) for v in values}, key=lambda s: (len(s), s))
    if not ids:
        return f"{column} IN (NULL)"
    return f"{column} IN ({', '.join(ids)})"


def _escape_sql_string_literal(value: str) -> str:
    return value.replace("'", "''")


def build_string_in_clause(column: str, values) -> str:
    """Build `"<column> IN ('AAPL', 'MSFT')"` from an iterable of I/B/E/S
    ticker strings, single-quoting each and escaping any embedded single
    quote by doubling it (the standard SQL escape). An empty `values`
    produces `"<column> IN (NULL)"`, matching build_numeric_in_clause's
    empty-list behavior."""
    _validate_sql_identifier("column", column)
    cleaned = sorted({
        str(v).strip() for v in values if v is not None and str(v).strip() != ""
    })
    if not cleaned:
        return f"{column} IN (NULL)"
    quoted = ", ".join(f"'{_escape_sql_string_literal(v)}'" for v in cleaned)
    return f"{column} IN ({quoted})"


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

        for lib_name, available in (("ibes", have_ibes), ("crsp", have_crsp), ("comp", have_comp)):
            if not available:
                print(f"{lib_name} library not visible on this account -- skipping.", flush=True)
                record(
                    log,
                    {"library": lib_name, "table": "*", "status": "library_unavailable",
                     "rows": 0, "path": "", "missing_columns": "", "error": "",
                     "start_date": "", "end_date": ""},
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
            pull_table(
                db, log, "crsp", "stocknames",
                CRSP_STOCKNAMES_REQUIRED_COLUMNS,
                optional_columns=CRSP_STOCKNAMES_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                force=force,
            )
        if have_comp:
            pull_table(
                db, log, comp_lib, "company",
                COMP_COMPANY_REQUIRED_COLUMNS,
                optional_columns=COMP_COMPANY_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                force=force,
            )
        # wrdsapps.iclink was removed: confirmed via a prior run's manifest
        # that it doesn't exist on this account (no_such_table) -- dead code.
        if have_ibes and have_crsp:
            pull_table(
                db, log, "wrdsapps", "ibcrsphist",
                WRDSAPPS_IBCRSPHIST_REQUIRED_COLUMNS,
                optional_columns=WRDSAPPS_IBCRSPHIST_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                force=force,
            )
        if have_crsp and have_comp:
            pull_table(
                db, log, "crsp", "ccmxpf_lnkhist",
                CRSP_CCMXPF_LNKHIST_REQUIRED_COLUMNS,
                optional_columns=CRSP_CCMXPF_LNKHIST_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
                force=force,
            )
        if have_ibes:
            pull_table(
                db, log, "ibes", "idsum",
                IBES_IDSUM_REQUIRED_COLUMNS,
                optional_columns=IBES_IDSUM_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
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
        # A failure here (missing input file, unexpected schema) MUST abort
        # the whole run before Phase C's expensive WRDS queries -- a broken
        # resolution step must never silently fall through to an empty
        # filter that then pulls either the entire market or nothing.
        # =====================================================================
        try:
            resolved = resolve_universe(
                om_universe_path=METADATA_DIR / "optionmetrics_universe.parquet",
                om_universe_history_path=METADATA_DIR / "optionmetrics_universe_history.parquet",
                stocknames_path=RAW_DIR / "crsp" / "stocknames.parquet",
                ccmxpf_lnkhist_path=RAW_DIR / "crsp" / "ccmxpf_lnkhist.parquet",
                ibcrsphist_path=RAW_DIR / "wrdsapps" / "ibcrsphist.parquet",
                unresolved_secids_out=METADATA_DIR / "unresolved_secids.csv",
                ambiguous_links_out=METADATA_DIR / "ambiguous_links.csv",
                resolved_ids_out=METADATA_DIR / "resolved_universe_ids.parquet",
            )
        except UniverseResolutionError as e:
            print(
                f"FATAL: universe resolution failed -- aborting before any "
                f"Phase C (big-table) query is attempted: {e}",
                flush=True,
            )
            raise SystemExit(1) from e

        permno_filter = build_numeric_in_clause("permno", resolved.permnos)
        gvkey_filter = build_numeric_in_clause("gvkey", resolved.gvkeys)
        ticker_filter = build_string_in_clause("ticker", resolved.ibes_tickers)

        # =====================================================================
        # Phase C -- big tables, now filtered by the resolved IDs above (in
        # addition to the existing date/format filters -- combined with AND,
        # nothing lost or overwritten). crsp.dsi is deliberately left
        # unfiltered: one row per trading day market-wide, already tiny.
        # =====================================================================
        if have_ibes:
            # NOTE: these use LOOKBACK_START_DATE (not START_DATE) for the same
            # reason CRSP/Compustat do -- rolling-window/history features need
            # a runway before START_DATE, so early-1996 events have a chance at
            # a prior consensus snapshot.
            # statpers is the consensus SNAPSHOT date, not an event date --
            # intentionally left at END_DATE (not RETURN_END_DATE): extending
            # it would pull in consensus snapshots past the sample cutoff
            # rather than giving an in-sample event more drift-window room,
            # which is what RETURN_END_DATE is for.
            pull_table(
                db, log, "ibes", "statsumu_epsus",
                IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS,
                optional_columns=IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS,
                where_sql=(
                    f"statpers BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}' "
                    f"AND {ticker_filter}"
                ),
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
                force=force,
            )
            # anndats is the earnings ANNOUNCEMENT date -- an actual PEAD
            # trigger event, so (per the module-level RETURN_END_DATE
            # comment) it gets the forward buffer like the return series,
            # not just END_DATE.
            pull_table(
                db, log, "ibes", "actu_epsus",
                IBES_ACTU_EPSUS_REQUIRED_COLUMNS,
                optional_columns=IBES_ACTU_EPSUS_OPTIONAL_COLUMNS,
                where_sql=(
                    f"anndats BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}' "
                    f"AND {ticker_filter}"
                ),
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
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
            # verified yet (see IBES_DETU_EPSUS_OPTIONAL_COLUMNS above).
            # revdats (the estimate REVISION date) is an actual event date for
            # this table's purposes, so it gets the RETURN_END_DATE buffer
            # too, same reasoning as actu_epsus.anndats above.
            pull_table(
                db, log, "ibes", "detu_epsus",
                IBES_DETU_EPSUS_REQUIRED_COLUMNS,
                optional_columns=IBES_DETU_EPSUS_OPTIONAL_COLUMNS,
                where_sql=(
                    f"revdats BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}' "
                    f"AND {ticker_filter}"
                ),
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )

        if have_crsp:
            # Return series get a forward buffer (RETURN_END_DATE, not
            # END_DATE) so events near the end of the sample still have a
            # full post-event drift window. bid/ask/bidlo/askhi/openprc/numtrd
            # are optional here -- they exist purely for a future
            # transaction-cost/capacity phase this project isn't in yet, so a
            # renamed/missing one shouldn't take down the whole table.
            pull_table(
                db, log, "crsp", "dsf",
                CRSP_DSF_REQUIRED_COLUMNS,
                optional_columns=CRSP_DSF_OPTIONAL_COLUMNS,
                where_sql=(
                    f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}' "
                    f"AND {permno_filter}"
                ),
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            # This is the market-benchmark counterpart to dsf above. Left
            # unfiltered by design -- one row per trading day market-wide,
            # already tiny, and there is no per-security ID to filter it by.
            pull_table(
                db, log, "crsp", "dsi",
                CRSP_DSI_REQUIRED_COLUMNS,
                optional_columns=CRSP_DSI_OPTIONAL_COLUMNS,
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            pull_table(
                db, log, "crsp", "dsedelist",
                CRSP_DSEDELIST_REQUIRED_COLUMNS,
                optional_columns=CRSP_DSEDELIST_OPTIONAL_COLUMNS,
                where_sql=(
                    f"dlstdt BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}' "
                    f"AND {permno_filter}"
                ),
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )

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
                f"AND {COMPUSTAT_STD_FILTER} AND {gvkey_filter}"
            )
            # funda (annual) is intentionally left at END_DATE, not
            # RETURN_END_DATE: it's lower-priority annual control data
            # (book-to-market, etc.), not itself a quarterly PEAD trigger
            # date the way fundq's rdq is, so it doesn't need the
            # next-announcement forward buffer.
            funda_where = (
                f"datadate BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}' "
                f"AND {COMPUSTAT_STD_FILTER} AND {gvkey_filter}"
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
            pull_table(
                db, log, comp_lib, "fundq",
                COMP_FUNDQ_REQUIRED_COLUMNS,
                optional_columns=COMP_FUNDQ_OPTIONAL_COLUMNS,
                where_sql=fundq_where,
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            # Annual fundamentals alongside quarterly -- book-to-market and
            # other annual-basis controls aren't in fundq, and this is a
            # one-shot pull, so grab it now. See the restated-data caveat
            # above fundq's pull_table call -- it applies here too.
            pull_table(
                db, log, comp_lib, "funda",
                COMP_FUNDA_REQUIRED_COLUMNS,
                optional_columns=COMP_FUNDA_OPTIONAL_COLUMNS,
                where_sql=funda_where,
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=END_DATE,
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
