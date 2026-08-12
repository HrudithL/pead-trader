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


def _widen_integers_to_float64(schema: "pa.Schema") -> "pa.Schema":
    """Return `schema` with every integer-typed field replaced by
    pa.float64(). Non-integer fields (including pa.null()) pass through
    unchanged.

    This is the core defense against the Int64-vs-Float64 chunk-drift bug:
    wrds's chunked reader runs pandas dtype inference independently per
    chunk, so the SAME numeric column can come back as pandas `Int64` in a
    chunk where every value happens to be whole and `Float64` in another
    chunk with a fractional value. Under a true single-pass incremental
    writer (ParquetWriter's schema is fixed the moment it's constructed), a
    LATER chunk revealing a fraction can never retroactively widen an
    already-pinned int64 column -- so the only way to guarantee we never
    need an unsafe (truncating) downcast later is to never pin an integer
    type in the first place. The accepted cost: a genuinely integer-only
    column (e.g. permno, gvkey -- true database integers that will never
    have a fractional value in any chunk) also ends up as float64 in the
    output. That's a real, deliberate trade-off: those id values are well
    within float64's exact-integer range, so no precision is actually lost,
    and a uniform rule (rather than a per-column allowlist of which columns
    are "safe" to leave as int) is far less bug-prone than trying to guess
    upfront which columns could ever drift.
    """
    fields = []
    for field in schema:
        if pa.types.is_integer(field.type):
            fields.append(pa.field(field.name, pa.float64(), nullable=True))
        else:
            fields.append(field)
    return pa.schema(fields)


def _merge_schema_resolving_nulls(resolved: "pa.Schema", new_schema: "pa.Schema") -> "pa.Schema":
    """Merge `new_schema` into `resolved` (or just widen+return `new_schema`
    if `resolved` is None): for each field, keep `resolved`'s type unless
    it's still pa.null() and `new_schema` has a concrete type for that
    field, in which case adopt `new_schema`'s type (widened per
    _widen_integers_to_float64). Used to resolve a column that happened to
    be entirely NULL in earlier buffered chunks (e.g. crsp.dsf's numtrd,
    NASDAQ-only and NULL for every NYSE/AMEX row) once a chunk with a real
    value for it is seen.
    """
    if resolved is None:
        return _widen_integers_to_float64(new_schema)
    fields = []
    for rf in resolved:
        if pa.types.is_null(rf.type):
            nf_type = new_schema.field(rf.name).type
            if pa.types.is_integer(nf_type):
                nf_type = pa.float64()
            fields.append(pa.field(rf.name, nf_type, nullable=True))
        else:
            fields.append(rf)
    return pa.schema(fields)


# Bounded lookahead buffer for schema resolution (see
# _stream_query_to_parquet's docstring for the full reasoning): rather than
# holding the WHOLE table's chunks in memory (the previous round's approach)
# or blindly trusting only the very first chunk (which could show a
# pa.null() type for a column that's coincidentally all-NULL in that one
# chunk), we buffer at most this many chunks while any column's resolved
# type is still ambiguous, then pin the schema and flush. A small fixed
# constant, NOT proportional to table size -- still a bounded-memory
# improvement over accumulating every chunk, while giving a realistic chance
# of resolving a null-in-first-chunk column from real data before pinning.
_SCHEMA_RESOLUTION_CHUNK_CAP = 3


def _stream_query_to_parquet(db, library, table, columns, where_sql, out_path):
    """Run the real `columns` query against `library`.`table` (filtered by
    `where_sql`, if given) using chunked streaming (`return_iter=True`), and
    write the result to `out_path` incrementally via a real
    `pyarrow.parquet.ParquetWriter` -- bounded memory (see below), never the
    whole table held at once.

    SCHEMA PINNING (Fix 3, replacing the previous round's accumulate-then-
    pa.concat_tables-then-strip-metadata approach):

    The problem that approach was patching: wrds's chunked reader runs
    pandas' dtype inference independently per chunk, so the SAME numeric
    column can come back as pandas `Int64` in a chunk where every value
    happens to be whole and `Float64` in another chunk with a fractional
    value -- for the same query, same column, just depending on which rows
    landed in which 500k-row chunk. Reconciling that after the fact (concat
    with permissive promotion, then strip stale per-chunk pandas metadata so
    a later read doesn't try to cast back to the wrong original dtype) works,
    but it requires holding every chunk in memory at once (roughly doubling
    peak memory: the accumulated chunks plus the final concatenated table),
    and the schema is only decided after ALL data is already in hand.

    DEVIATION FROM THE ORIGINALLY SUGGESTED "zero-row probe query" APPROACH
    (documented here because round 5's verification discipline explicitly
    asks for this): the natural design is "issue `SELECT <cols> FROM
    <table> WHERE 1=0`, convert the resulting empty DataFrame to a pyarrow
    Schema, and trust that as the pinned schema, since a zero-row result
    should reflect the DB's real per-column type rather than a
    value-dependent guess." I implemented this and then verified it against
    the ACTUAL pandas/wrds code path (pandas.io.sql._convert_arrays_to_dataframe
    and _query_iterator, read directly from the installed pandas==2.2.3
    source, not assumed): for a genuinely zero-row result, pandas takes the
    `DataFrame(columns=columns)` fallback branch -- EVERY column comes back
    plain `object` dtype, with no per-column type information from the DB
    driver at all. Converting that to a pyarrow Schema gives pa.null() for
    literally every field, regardless of the column's real type. I confirmed
    this empirically (see the round's test suite / PR notes) -- the probe
    query is not a shortcut, it's a wasted round trip that provides no type
    information. Given that finding, and given this project's standing
    "minimize WRDS query volume" constraint, I dropped the probe query
    entirely rather than ship a mechanism that looks like protection but
    isn't (exactly the failure mode this round is meant to eliminate).

    What this function does instead: build the schema from REAL chunks,
    widening every integer field to float64 up front
    (_widen_integers_to_float64) so a later chunk's fractional value can
    never require an unsafe downcast. Because a column can still be
    genuinely all-NULL (pa.null()) in an early chunk (e.g. numtrd, NASDAQ-
    only), we buffer up to _SCHEMA_RESOLUTION_CHUNK_CAP chunks -- a small,
    FIXED constant, not proportional to the table's size -- merging each
    into a running "resolved schema" (_merge_schema_resolving_nulls) until
    every column has a concrete type or the cap is reached, THEN create the
    ParquetWriter and flush the buffered chunks, and stream+cast+write every
    subsequent chunk one at a time with no further buffering. This is a
    materially bounded memory profile (a small constant multiple of one
    chunk, not the whole table), though not literally "exactly one chunk"
    in every case -- an honest, documented trade-off, not the literal
    single-chunk design originally sketched, because a true single-chunk
    design cannot correctly resolve a column that's all-NULL in chunk one
    without either guessing its type or accepting a hard failure later.

    KNOWN LIMITATION: if an optional/nullable column is NULL in every one
    of the first _SCHEMA_RESOLUTION_CHUNK_CAP chunks and only gets a real
    value in a LATER chunk, the pinned schema still has pa.null() for that
    field, and casting that later chunk raises (pyarrow does not support
    casting real data onto a pa.null() target -- verified directly:
    `pa.table(...).cast(schema_with_null_field, safe=False)` raises
    ArrowNotImplementedError, it does not silently drop or corrupt data).
    That exception is caught by pull_table's outer handler and logged as
    "query_failed" -- a real, visible failure, not silent data loss -- so
    this limitation degrades safely if it's ever hit; it is not expected to
    be hit in practice for this project's tables (whichever optional
    columns can be sparse, e.g. crsp.dsf.numtrd, are NULL for a whole
    EXCHANGE, not the whole table, so any chunk spanning more than a
    handful of distinct permnos should see a real value well within the
    3-chunk cap).

    We no longer call `.replace_schema_metadata(None)` anywhere: that call
    existed to strip stale pandas-dtype metadata left over from the
    concat-then-promote approach, which doesn't apply here -- the schema is
    pinned before most/all chunks are written and every chunk is explicitly
    cast to it, so there is no stale metadata to strip.

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
    resolved_schema = None
    pending = []  # bounded by _SCHEMA_RESOLUTION_CHUNK_CAP -- see docstring
    try:
        for chunk in db.raw_sql(sql, return_iter=True):
            total_rows += len(chunk)
            chunk_table = pa.Table.from_pandas(chunk, preserve_index=False)

            if writer is not None:
                writer.write_table(chunk_table.cast(resolved_schema, safe=False))
                continue

            resolved_schema = _merge_schema_resolving_nulls(
                resolved_schema, chunk_table.schema
            )
            pending.append(chunk_table)

            still_unresolved = any(pa.types.is_null(f.type) for f in resolved_schema)
            if not still_unresolved or len(pending) >= _SCHEMA_RESOLUTION_CHUNK_CAP:
                writer = pq.ParquetWriter(str(tmp_path), resolved_schema)
                for pending_table in pending:
                    writer.write_table(pending_table.cast(resolved_schema, safe=False))
                pending = []

        if writer is None:
            # Fewer chunks existed in total than the resolution cap (this
            # includes the zero-total-rows case: exactly one empty chunk).
            # Flush whatever's pending now with the best schema resolved so
            # far -- there's nothing more to look at.
            if resolved_schema is None:
                resolved_schema = pa.schema([])
            writer = pq.ParquetWriter(str(tmp_path), resolved_schema)
            for pending_table in pending:
                writer.write_table(pending_table.cast(resolved_schema, safe=False))
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

    `stream=True` uses `_stream_query_to_parquet` (schema pinned from a
    zero-row probe query, then written incrementally via a real
    ParquetWriter -- see that function's docstring) instead of loading the
    full result into a DataFrame first; use it for any table that could
    plausibly be large.
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
            rows, _wrote_file = _stream_query_to_parquet(
                db, library, table, query_columns, where_sql, out_path
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
            # which is what RETURN_END_DATE is for.
            pull_table(
                db, log, "ibes", "statsumu_epsus",
                IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS,
                optional_columns=IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS,
                where_sql=f"statpers BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
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
                where_sql=f"anndats BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            pull_table(
                db, log, "ibes", "idsum",
                IBES_IDSUM_REQUIRED_COLUMNS,
                optional_columns=IBES_IDSUM_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
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
            # are optional here -- they exist purely for a future
            # transaction-cost/capacity phase this project isn't in yet, so a
            # renamed/missing one shouldn't take down the whole table.
            pull_table(
                db, log, "crsp", "dsf",
                CRSP_DSF_REQUIRED_COLUMNS,
                optional_columns=CRSP_DSF_OPTIONAL_COLUMNS,
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                stream=True,
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
            # This is the market-benchmark counterpart to dsf above.
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
                where_sql=f"dlstdt BETWEEN '{LOOKBACK_START_DATE}' AND '{RETURN_END_DATE}'",
                start_date=LOOKBACK_START_DATE, end_date=RETURN_END_DATE,
                force=force,
            )
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
            pull_table(
                db, log, comp_lib, "fundq",
                COMP_FUNDQ_REQUIRED_COLUMNS,
                optional_columns=COMP_FUNDQ_OPTIONAL_COLUMNS,
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
                COMP_COMPANY_REQUIRED_COLUMNS,
                optional_columns=COMP_COMPANY_OPTIONAL_COLUMNS,
                start_date=None, end_date=None,
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
