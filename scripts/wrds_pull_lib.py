"""Shared library for pead-trading's WRDS bulk-pull scripts.

This module holds everything that survived 7 rounds of review/fix against
the original monolithic `pull_wrds_data.py` (now split into
`pull_wrds_reference.py`, `pull_wrds_crsp.py`, `pull_wrds_compustat.py`, and
`pull_wrds_ibes.py` -- see `memory/phase1-equity-pead-plan.md`'s "Split Into
Parallel Scripts" section): `connect()`, `pull_table()` and everything it
depends on (schema pinning, atomic writes, identifier validation, the
manifest), and `resolve_universe()` and everything IT depends on (the
CUSIP/PERMNO/GVKEY/ticker resolution logic). None of that logic's behavior
changed as part of this split -- this is a pure extraction, moved verbatim
out of the old single-file script, plus a small number of NEW helpers this
round explicitly added to make the split possible:

  - `MAX_CONCURRENT_CONNECTIONS`: the shared concurrency cap for every
    script's internal thread pool.
  - `load_resolved_filter_lists()`: lets each of the three consumer scripts
    (crsp/compustat/ibes) reconstruct the PERMNO/GVKEY/I-B-E-S-ticker filter
    lists from `resolved_universe_ids.parquet` on disk, since they no longer
    share a single in-process `ResolvedUniverse` object with the reference
    script the way one monolithic `main()` used to.
  - `require_resolved_universe_file()`: the "run pull_wrds_reference.py
    first" guard shared by all three consumer scripts.
  - `parse_common_args()`: the `--force` flag parsing shared by all four
    scripts' `main()`.
  - A `threading.Lock` around the manifest append, needed now that a single
    script (`pull_wrds_crsp.py`) can have multiple threads -- each with its
    own WRDS connection -- calling `pull_table()` (and therefore
    `append_manifest_row()`) concurrently. This wasn't a risk before this
    round: the original script was single-threaded throughout.

Every table/library/column-list constant that used to live only in
`pull_wrds_data.py` also moves here, so every script that needs
`START_DATE`/`END_DATE`/`CRSP_DSF_REQUIRED_COLUMNS`/etc. imports the exact
same values instead of each keeping its own copy that could drift.
"""

import builtins
import csv
import datetime as dt
import hashlib
import json
import os
import re
import threading
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
RESOLVED_IDS_PATH = METADATA_DIR / "resolved_universe_ids.parquet"

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

# Round-of-review discipline (unchanged from the original single-file
# script): per-table required/optional column lists live at module level,
# not as inline list literals at each pull_table() call site, so every
# script AND the test suite import these same constants instead of each
# keeping a hand-copied duplicate that could drift out of sync.
#
# required = aborts the whole table's pull if missing from the real schema.
# optional = dropped from the query (and noted in the manifest) if missing;
#            never aborts the pull.

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
    "universe_fingerprint",
]

# Manifest error-text sanitization (Fix 4): data/metadata/wrds_pull_manifest.csv
# is git-tracked (a deliberate choice from a prior round), and exception text
# from psycopg2/sqlalchemy connection failures can embed hostnames, usernames,
# or other connection details. _MAX_ERROR_LEN caps how much raw exception text
# ever reaches that file.
_MAX_ERROR_LEN = 300

# NEW this round: bounded concurrency cap shared by every script's internal
# thread pool (pull_wrds_crsp.py's per-year crsp.dsf chunks + crsp.dsi/
# crsp.dsedelist, pull_wrds_compustat.py's fundq/funda, pull_wrds_ibes.py's
# statsumu_epsus/actu_epsus/detu_epsus). The user explicitly prioritized
# speed over minimizing WRDS connections for this split, so this is a
# deliberate middle ground: real concurrency (not one sequential connection),
# without opening enough simultaneous WRDS sessions at once to risk tripping
# a per-account connection limit or a rate flag. A single, named, easy-to-
# adjust constant rather than a magic number repeated at each call site.
#
# Finding 8 (review round 8): this caps each SCRIPT individually, but the
# design explicitly intends all 3 consumer scripts to run SIMULTANEOUSLY, as
# separate OS processes, in separate terminals -- so the REAL total peak
# concurrent WRDS connection count when all 3 are running at once is the SUM
# across scripts, not this one number. With MAX_CONCURRENT_CONNECTIONS == 4:
#   pull_wrds_crsp.py:       min(4, 22 tasks)      = 4 connections
#   pull_wrds_compustat.py:  min(4, 2 tasks)        = 2 connections
#   pull_wrds_ibes.py:       min(4, 3 tasks)        = 3 connections
#   -----------------------------------------------------------
#   real simultaneous peak (all 3 running together)  = 9 connections
#
# DECISION (documented, not silently discovered): 9 is accepted as the real,
# intentional ceiling, rather than shrinking MAX_CONCURRENT_CONNECTIONS to
# force a lower total. Reasoning: this account's actual WRDS connection
# limit is unknown (see memory/pead-trading-project.md), but crsp.dsf is by
# far this project's largest and slowest pull (20 year-chunks vs. 2-3 tasks
# for the other two scripts) -- shrinking ITS worker count to buy a lower
# total would cost the most real wall-clock time for the least benefit,
# against a connection-limit risk that has no evidence behind it yet. If a
# real run against live WRDS ever shows connection-count sensitivity
# (throttling, rejected connections), lower MAX_CONCURRENT_CONNECTIONS first
# -- it is the single knob every script's cap already derives from.
DOCUMENTED_PEAK_TOTAL_CONNECTIONS = 9
MAX_CONCURRENT_CONNECTIONS = 4

# Manifest writes can now come from multiple THREADS within one script
# (pull_wrds_crsp.py's per-year connections all append to the same
# MANIFEST_PATH concurrently) -- append_manifest_row's read-then-write
# "is this file new" check plus its append are not atomic as a pair, so two
# threads racing on a brand-new manifest file could both decide "file is new"
# and both write a header, or interleave partial writes. This lock protects
# exactly that: it's process-local (threading, not multiprocessing/fcntl),
# which is all that's needed for a single script's internal thread pool.
#
# KNOWN LIMITATION (documented, not solved here -- out of this round's
# scope): the three consumer scripts are meant to run concurrently as
# SEPARATE PROCESSES (per the split design), each with its own instance of
# this lock -- this does NOT protect the manifest CSV against interleaved
# writes from those separate processes. Cross-process file locking (e.g.
# fcntl) would be needed to close that gap; it isn't attempted here because
# the task this round is scoped to is the thread-safety needed for
# pull_wrds_crsp.py's internal per-year thread pool, not a general-purpose
# multi-process-safe manifest. Worst case today is an occasional interleaved
# or malformed manifest row across processes, not data loss in the actual
# pulled Parquet files.
_MANIFEST_LOCK = threading.Lock()


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


def append_manifest_row(row: dict, manifest_path: Path) -> None:
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

    Guarded by _MANIFEST_LOCK (new this round): pull_wrds_crsp.py's per-year
    thread pool means multiple threads within ONE process can call this
    concurrently against the same manifest_path -- see _MANIFEST_LOCK's
    docstring for exactly what this does and does not protect against.

    `manifest_path` is REQUIRED, with no fallback default (lower-priority
    fix, round 3 review): this used to default to the module-level
    `MANIFEST_PATH` constant, a vestigial single shared path left over from
    before the per-script manifest split (see `manifest_path_for`'s
    docstring) -- if a future call site forgot to pass `manifest_path=`, it
    would silently write to that old shared/racy path instead of erroring.
    Now a call site that omits it gets an immediate TypeError, and an
    explicit `manifest_path=None` raises ValueError here rather than
    silently falling back.
    """
    if manifest_path is None:
        raise ValueError(
            "append_manifest_row: manifest_path must not be None -- pass "
            "the specific per-script manifest path (see manifest_path_for)."
        )
    manifest_path = Path(manifest_path)
    row = dict(row)
    if row.get("error"):
        row["error"] = _sanitize_error_text(str(row["error"]))
    row.setdefault(
        "pulled_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    )
    with _MANIFEST_LOCK:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        file_is_new = not manifest_path.exists() or manifest_path.stat().st_size == 0
        with manifest_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDNAMES)
            if file_is_new:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDNAMES})


def record(log: list, row: dict, manifest_path: Path) -> None:
    """Add one entry to both the in-memory run log (used only for the final
    printed summary -- kept un-sanitized since it's console-only, never
    committed) and the on-disk manifest (the durable, git-tracked record,
    sanitized by append_manifest_row).

    `log` is a plain list. Callers that share a `log` across THREADS (none
    of this library's own code does -- each of pull_wrds_crsp.py's per-year
    worker functions uses its own thread-local log list, merged by the
    caller after all futures complete) are responsible for their own
    thread-safety around it; append_manifest_row's own lock only protects
    the shared on-disk manifest file, not this in-memory list.

    `manifest_path` is REQUIRED -- see append_manifest_row's docstring for
    why this no longer has a silent fallback default.
    """
    row = dict(row)
    row.setdefault(
        "pulled_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    )
    log.append(row)
    append_manifest_row(row, manifest_path=manifest_path)


# Finding 3 (review round 8): the statuses pull_table()/record() can log that
# mean "this table/chunk's data is genuinely present and usable" -- anything
# else (missing_required_columns, no_such_table, describe_table_error,
# query_failed, ok_suspicious_zero_rows, ...) means a table/chunk that was
# ATTEMPTED did not actually produce usable data, and must not be silently
# reported as an overall script SUCCESS. "ok_empty_query_kept_existing"
# counts as success: it means a --force re-pull's query legitimately
# returned zero rows and the previously-good file at out_path was correctly
# left untouched -- not a failure, just a no-op this run. NOTE this is a
# DIFFERENT scenario from "ok_suspicious_zero_rows" (Fix 2, round 3 review):
# that status fires when a substantive, universe-filtered table returns zero
# rows with NO pre-existing file to fall back on -- a real zero-row result
# for one of those tables is essentially always a sign of a bug (a
# disconnected filter, a wrong join, a shifted date column), so it is
# deliberately NOT added here and IS treated as a failure by
# find_run_log_failures below.
#
# "ok_empty_query_stale_fingerprint" (this round, Fix 3 of the fingerprint-
# zero-row-fix PR): a DIFFERENT flavor of the "kept existing" scenario above.
# It fires in exactly the same situation (existing file at out_path, zero-row
# re-pull, file correctly left untouched) EXCEPT the file's carried-forward/
# prior recorded fingerprint is present, non-blank, AND differs from this
# run's current universe_fingerprint -- i.e. this isn't a first attempt (see
# "no prior fingerprint" below) but a KNOWN, confirmed mismatch: the universe
# changed and a re-pull under the new universe has failed to produce any
# rows. That could recur indefinitely (every future run would otherwise log
# plain "ok_empty_query_kept_existing" -- a SUCCESS status -- forever, even
# though the on-disk data is known to be out of step with the active
# universe), so this status is deliberately NOT included below and IS treated
# as a failure by find_run_log_failures, the same reasoning as
# "ok_suspicious_zero_rows". By contrast, when there's no prior recorded
# fingerprint at all (a file pulled before this field existed, or a rotated/
# reset manifest -- a first-attempt situation, not a confirmed-repeated-
# failure one) or the prior fingerprint matches the current run's, the
# ordinary "ok_empty_query_kept_existing" status is used and stays a success.
#
# "library_unavailable" (round 3 review, Fix 3) also counts as success: it
# means pull_wrds_reference.py's one-time db.list_libraries() audit found a
# given library (ibes/crsp/comp) simply isn't visible on this WRDS account --
# that's a legitimate, expected account-tier fact, not a failure of any pull
# that was actually attempted, so it must not gate pull_wrds_reference.py's
# own "safe to proceed" success check (added this round) the way a genuine
# query/schema failure does.
SUCCESS_STATUSES = {
    "ok", "skipped_exists", "ok_empty_query_kept_existing", "library_unavailable",
}


def find_run_log_failures(run_log) -> list:
    """Return the subset of `run_log` rows (as built by pull_table() via
    record()) whose status is NOT one of SUCCESS_STATUSES.

    Used by each of the 3 consumer scripts' main() (pull_wrds_crsp.py,
    pull_wrds_compustat.py, pull_wrds_ibes.py) to decide whether to print a
    SUCCESS message and exit 0, or a FAILURE summary and exit non-zero --
    IN ADDITION TO (not instead of) any executor-level exception already
    collected via concurrent.futures.Future.result(). Before this check
    existed, a table/chunk whose pull_table() call logged e.g.
    "query_failed" (pull_table swallows query-level failures into the
    manifest by design, so this never raises and never reaches the
    Future.result()-based failure collection) was invisible to main() --
    every task's Future.result() returned normally, so main() printed
    SUCCESS and exited 0 even when every single query had actually failed.
    """
    return [row for row in run_log if row.get("status") not in SUCCESS_STATUSES]


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


# Fix 1 (fingerprint-zero-row-fix follow-up): distinct sentinel for _log()'s
# `fingerprint` parameter below. Plain `None` used to serve DOUBLE duty as
# both "caller passed no override -- use this run's current
# universe_fingerprint" (the intended default meaning) AND a legitimate
# real value: last_manifest_fingerprint_for_path(...) also returns None
# whenever out_path has NO prior recorded fingerprint (a file pulled before
# this field existed, or a rotated/reset manifest). Passing THAT None
# straight into `_log(fingerprint=...)` used to be indistinguishable from
# not overriding at all, so it fell through to stamping the CURRENT run's
# fingerprint -- reproducing, in that specific "no prior fingerprint" case,
# the exact "stale/wrong fingerprint stamped on a kept-existing file" bug
# this module's ok_empty_query_kept_existing handling exists to fix. A
# distinct sentinel object (never equal to any real fingerprint string, and
# never equal to None) makes "no override" and "explicitly record
# blank/no-provenance" two different, unambiguous things.
_UNSET_FINGERPRINT = object()


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
    universe_fingerprint=None,
    zero_rows_is_suspicious=False,
):
    """Check real columns first, save to Parquet, log the outcome.

    `db` only needs to satisfy the small interface actually used here --
    `describe_table(library, table)` and `raw_sql(sql, return_iter=...)` --
    so tests can pass a lightweight stub instead of a real wrds.Connection.
    This also means a script can safely call pull_table() concurrently from
    multiple threads, each with its OWN `db` (see pull_wrds_crsp.py) --
    nothing here shares mutable state across `db` instances except the
    manifest file itself, which append_manifest_row's lock protects.

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

    `out_name` (default: `table`) controls the output Parquet's filename
    (`<out_name>.parquet`) independent of the real SQL table name queried --
    this is what lets pull_wrds_crsp.py write one file per calendar year
    (`out_name="dsf_1996"`, etc.) while still querying the real `crsp.dsf`
    table every time.

    `stream=True` uses `_stream_query_to_parquet` (schema pinned from
    db.describe_table()'s declared column types, via _build_pinned_schema,
    then written incrementally via a real ParquetWriter -- see that
    function's docstring) instead of loading the full result into a
    DataFrame first; use it for any table that could plausibly be large.

    `universe_fingerprint` (round 3 review, Fix 1): an opaque string the
    caller computes ONCE per script run (see compute_universe_fingerprint)
    identifying which resolved-universe filter this run is using -- recorded
    verbatim on every manifest row this call produces (via `_log` below),
    purely for provenance/auditability. This function does NOT use it to
    make any decision itself; the caller (see determine_chunk_force) is
    responsible for comparing it against a chunk's prior recorded value
    BEFORE deciding what `force` value to pass in here.

    `zero_rows_is_suspicious` (round 3 review, Fix 2): when True, a
    genuinely zero-row query result (no exception, and no pre-existing
    out_path to fall back on -- see the "ok_empty_query_kept_existing"
    branch below, which is unaffected by this flag) is logged as the
    distinct "ok_suspicious_zero_rows" status instead of plain "ok".
    Callers should only pass True for substantive, universe-filtered tables
    where a real empty result is essentially always a sign of a bug (a
    disconnected filter, a wrong join, a shifted date column) rather than a
    legitimately empty dataset -- e.g. crsp.dsf/crsp.dsedelist,
    comp.fundq/comp.funda, and the ibes.*_epsus family. Leave this False
    (the default) for unfiltered/reference tables (crsp.dsi, the small
    Phase A tables) where a zero-row result has different, legitimate
    emptiness characteristics. "ok_suspicious_zero_rows" is NOT included in
    SUCCESS_STATUSES, so find_run_log_failures() treats it as a failure for
    the purposes of a script's overall exit code -- unlike plain "ok".

    Separately (unaffected by this flag): when out_path DOES already exist
    and a zero-row result leaves it untouched, the logged status is either
    "ok_empty_query_kept_existing" (success -- first attempt, or the file's
    carried-forward fingerprint matches this run's) or
    "ok_empty_query_stale_fingerprint" (NOT a success -- the file's
    carried-forward fingerprint is known to differ from this run's, i.e. a
    re-pull under a changed universe has confirmed-failed to produce rows).
    See SUCCESS_STATUSES's module-level comment and the "rows == 0" branch
    below for the full reasoning.
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

    def _log(status, rows=0, path="", missing_columns="", optional_columns_missing="", error="",
             fingerprint=_UNSET_FINGERPRINT):
        # `fingerprint` lets a call site override what's recorded for THIS
        # row instead of always stamping the current run's
        # `universe_fingerprint` -- needed by the "ok_empty_query_kept_existing"
        # branch below, which must carry forward out_path's PRIOR recorded
        # fingerprint rather than the current (mismatched) one.
        #
        # `_UNSET_FINGERPRINT` (the default -- see its module-level docstring
        # above) means "no override, use this run's current
        # universe_fingerprint". An explicit `fingerprint=None` (or
        # `fingerprint=""`) is a REAL value meaning "record blank/
        # no-provenance" and must NOT fall back to the current fingerprint --
        # that distinction is exactly what fixes the bug described above.
        if fingerprint is _UNSET_FINGERPRINT:
            fingerprint = universe_fingerprint
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
                "universe_fingerprint": fingerprint or "",
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
                # The file at out_path is untouched, so its data still only
                # reflects whatever universe fingerprint was last recorded
                # for it -- NOT this run's (possibly different) universe_
                # fingerprint. Carry that prior value forward rather than
                # stamping the current one, so a later run whose universe
                # still doesn't match sees a fingerprint MISMATCH and
                # attempts to re-pull again instead of treating this stale
                # file as confirmed-current under a universe it was never
                # actually pulled under.
                #
                # If NO prior fingerprint is recorded at all (a file pulled
                # before this field existed, or a rotated/reset manifest --
                # `prior_fingerprint` is None), that None is passed to
                # `_log` explicitly (as a real value, not the "no override"
                # default -- see `_UNSET_FINGERPRINT`), which correctly
                # records it as blank/no-provenance rather than falling
                # back to this run's current fingerprint. determine_chunk_
                # force already treats "no recorded fingerprint" the same
                # as a mismatch (force a re-pull attempt), so this is a
                # first-attempt situation, not a confirmed-repeated-failure
                # one -- see Fix 3 below for that distinction.
                prior_fingerprint = last_manifest_fingerprint_for_path(
                    manifest_path, _rel(out_path)
                )
                # Fix 3: distinguish "genuinely untouched, first attempt or
                # matching universe -- fine" from "this file's data is
                # KNOWN to predate a universe change and a re-pull under the
                # new universe has now failed to produce any rows". The
                # latter would otherwise silently log plain
                # "ok_empty_query_kept_existing" (a SUCCESS status) forever,
                # every single run, with no visible signal that the on-disk
                # data is out of step with the active universe -- a script
                # could run in this state indefinitely and always report
                # exit-0 success. Only fires when there IS a real prior
                # fingerprint (excludes the "no prior fingerprint" case
                # above, which is a first attempt, not a repeated failure)
                # that differs from this run's current one (excludes the
                # ordinary matching-fingerprint / explicit --force-on-a-
                # still-current-file case).
                stale_fingerprint_mismatch = bool(
                    prior_fingerprint
                    and universe_fingerprint
                    and prior_fingerprint != universe_fingerprint
                )
                if stale_fingerprint_mismatch:
                    print(
                        "  WARNING: this file's data predates a universe "
                        "change -- a re-pull attempt under the new universe "
                        "returned zero rows, so the on-disk data is known "
                        "to be out of step with the active universe (not "
                        "just possibly stale, but confirmed: the last "
                        "attempt to refresh it under the new universe "
                        "produced nothing).",
                        flush=True,
                    )
                _log(
                    "ok_empty_query_stale_fingerprint" if stale_fingerprint_mismatch
                    else "ok_empty_query_kept_existing",
                    rows=0,
                    path=_rel(out_path),
                    optional_columns_missing=optional_missing_str,
                    fingerprint=prior_fingerprint,
                )
            elif zero_rows_is_suspicious:
                # Fix 2 (round 3 review): for a substantive, universe-
                # filtered table (the caller opted in via
                # zero_rows_is_suspicious=True), a genuine zero-row result
                # with no pre-existing file to fall back on is essentially
                # always a sign of a bug -- a disconnected filter, a wrong
                # join, a shifted date column -- not a legitimately empty
                # dataset. Log a distinct, NON-success status so this is
                # visible and gates the script's exit code (see
                # SUCCESS_STATUSES/find_run_log_failures) instead of
                # silently blending into ordinary "ok".
                print(
                    "  WARNING: 0 rows returned for a filtered substantive "
                    "table with no pre-existing file -- treating as "
                    "SUSPICIOUS (likely a disconnected filter, a wrong "
                    "join, or a shifted date column), not a clean success.",
                    flush=True,
                )
                _log(
                    "ok_suspicious_zero_rows", rows=0,
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
    """Raised whenever Phase B's local resolution can't proceed safely at
    all -- i.e. the PERMNO resolution stage itself (the two OM universe
    files, or crsp.stocknames) is missing an expected input file, or a
    loaded DataFrame is missing a column that stage structurally depends
    on.

    pull_wrds_reference.py's main() catches this and aborts the ENTIRE run
    before writing anything the three consumer scripts would trust -- a
    broken PERMNO resolution step must never be allowed to silently fall
    through to an empty/missing filter that then pulls either the whole
    market (no filter) or nothing (an accidentally always-false filter)
    from WRDS.

    Fix 3: GVKEY resolution (crsp.ccmxpf_lnkhist) and I/B/E/S ticker
    resolution (wrdsapps.ibcrsphist) are DELIBERATELY NOT covered by this
    exception -- neither is required for anything CRSP-permno-filtered to
    work, so a missing/unpullable linking table for either one degrades
    just that specific stage (see resolve_universe's ResolvedUniverse.
    gvkey_resolution_skipped / ibes_ticker_resolution_skipped) rather than
    aborting the whole run. Only PERMNO resolution is allowed to hard-fail
    everything.
    """


# Fix 2 (round 1): the floor below which the secid-to-PERMNO resolution rate
# is treated as a systemic failure (bad CRSP pull, CUSIP format mismatch,
# etc.) rather than an ordinary real-world linking gap, and the whole run is
# aborted before any Phase C query. This project's documented expectation
# (memory/phase1-equity-pead-plan.md) is ~99.99% resolution once real CRSP
# data is pulled -- 90% is deliberately far below that, so it only fires on
# a genuine systemic break, never on ordinary day-to-day linking noise.
MIN_PERMNO_RESOLUTION_RATE = 0.90

# Fix 4 (round 2): an independent floor on the ABSOLUTE number of secids
# loaded from data/metadata/optionmetrics_universe.parquet, checked BEFORE
# any resolution (or WRDS connection) is attempted. MIN_PERMNO_RESOLUTION_RATE
# above is a RATE, not a count -- a corrupted/truncated universe file (a
# partial regeneration, a bad write, etc.) that happens to resolve at 100%
# would sail straight through a rate-only floor, since 100% of a tiny, wrong
# universe is still 100%. The real file has ~8,358 rows; 8,000 leaves
# meaningful margin below that while still catching a file that's an order
# of magnitude too small. See _check_universe_secid_floor.
MIN_UNIVERSE_SECID_COUNT = 8000


def _check_universe_secid_floor(om_universe_path) -> int:
    """Fix 4: raise UniverseResolutionError if
    data/metadata/optionmetrics_universe.parquet has fewer than
    MIN_UNIVERSE_SECID_COUNT rows -- BEFORE any resolution logic runs and
    (per main()'s call site) before a WRDS connection is even attempted.

    This is deliberately independent of MIN_PERMNO_RESOLUTION_RATE: that
    check only ever sees a RATE (resolved / total), so a universe file
    that's been truncated or corrupted down to a handful of rows -- every
    one of which happens to resolve successfully -- computes 100% and would
    sail straight past a rate-only floor. Gating on the absolute count here
    catches exactly that case before any Phase C (or even Phase A) work is
    attempted.

    Returns the row count on success (informational for the caller); the
    caller doesn't need to keep this value since resolve_universe() re-reads
    the same file itself as part of its own PERMNO-resolution stage.
    """
    om = _load_parquet_or_raise(om_universe_path, "optionmetrics_universe.parquet")
    n = len(om)
    if n < MIN_UNIVERSE_SECID_COUNT:
        raise UniverseResolutionError(
            f"optionmetrics_universe.parquet has only {n:,} row(s), below "
            f"the MIN_UNIVERSE_SECID_COUNT floor of {MIN_UNIVERSE_SECID_COUNT:,} "
            f"-- the real universe file has ~8,358 rows, so this looks like "
            f"a truncated/corrupted regeneration, not a genuinely small "
            f"universe. A rate-based floor alone would not catch this: if "
            f"every one of these few rows happened to resolve, the rate "
            f"would read 100% and this run would otherwise proceed to pull "
            f"a tiny, wrong dataset from WRDS and report success."
        )
    return n


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

    # Fix 3: set when crsp.ccmxpf_lnkhist / wrdsapps.ibcrsphist was missing
    # or unpullable, so GVKEY / I/B/E/S ticker resolution was skipped
    # entirely (never even attempted) rather than attempted-and-empty.
    # main() uses these to distinguish "skip the dependent Phase C pulls,
    # this is expected" from "resolution ran and came back suspiciously
    # empty, abort" (see MIN_PERMNO_RESOLUTION_RATE / the floor check).
    gvkey_resolution_skipped: bool = False
    gvkey_skip_reason: str = ""
    ibes_ticker_resolution_skipped: bool = False
    ibes_ticker_skip_reason: str = ""

    # Fix 5: coverage-gap audit -- a PERMNO that resolved fine but has no
    # ccmxpf_lnkhist row (no GVKEY) or no ibcrsphist row (no I/B/E/S
    # ticker) at all, as opposed to the secid that never got a PERMNO in
    # the first place (see unresolved_secids.csv / n_secids_resolved).
    n_permnos_without_gvkey: int = 0
    n_permnos_without_ibes_ticker: int = 0


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


def _load_optional_linking_table(path, required_columns, context: str):
    """Fix 3: best-effort load of a linking table that this project can
    proceed WITHOUT (crsp.ccmxpf_lnkhist for GVKEY resolution,
    wrdsapps.ibcrsphist for I/B/E/S ticker resolution) -- unlike
    _load_parquet_or_raise, this NEVER raises. Returns (dataframe, None) on
    success, or (None, reason) if the file is missing, fails to read, or is
    missing a column this join structurally depends on.

    The caller (resolve_universe) uses a non-None reason to skip just that
    one identifier family's resolution (and main() skips the Phase C pulls
    that need it), rather than aborting the whole run the way a missing
    crsp.stocknames/om-universe file does.
    """
    path = Path(path)
    if not path.exists():
        return None, f"{context}: file not found at {path}"
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        return None, f"{context}: failed to read {path} as Parquet: {e}"
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        return None, (
            f"{context}: missing expected column(s) {missing} -- actual "
            f"columns are {list(df.columns)}"
        )
    return df, None


def _norm_cusip(series: pd.Series) -> pd.Series:
    """Normalize a CUSIP-shaped column to a comparable string form: cast to
    str, strip whitespace, uppercase. CUSIPs may have leading zeros (e.g.
    "04341310"), so they must be compared/joined as strings, never as
    numbers."""
    return series.astype(str).str.strip().str.upper()


def _check_cusip_lengths(series: pd.Series, context: str) -> None:
    """Fix 8: lightweight sanity check, not a guarantee -- a real CUSIP
    (normalized, as _norm_cusip produces) is always exactly 8 characters.
    This won't catch every possible format mismatch, but a wholesale
    length mismatch (e.g. an un-zero-padded numeric CUSIP, or a 9-character
    CUSIP with a check digit still attached) is exactly the kind of thing
    that would otherwise silently produce a zero-match join and look
    identical to "this security genuinely isn't in CRSP" -- log a warning
    so that's visible early rather than discovered downstream as a
    confusing all-unresolved run. Deliberately does NOT hard-fail: a
    handful of odd-length values mixed in with mostly-valid ones is exactly
    the kind of messy-real-world-data case this project's established
    posture is to flag, not abort on.
    """
    if series.empty:
        return
    lengths = series.str.len()
    bad = lengths[lengths != 8]
    if not bad.empty:
        bad_lengths = sorted(bad.unique().tolist())
        print(
            f"  WARNING: {context}: {len(bad):,} of {len(series):,} "
            f"normalized CUSIP(s) are not length 8 (lengths found: "
            f"{bad_lengths}) -- this can indicate a format mismatch that "
            f"would otherwise silently produce a zero-match join.",
            flush=True,
        )


_GVKEY_RE = re.compile(r"^\d{6}\Z")


def _normalize_gvkeys(series: pd.Series) -> pd.Series:
    """Normalize a GVKEY-shaped column to its correct zero-padded 6-digit
    string form (Fix 1, round 2 -- root-cause fix).

    Compustat's GVKEY is a fixed-width, zero-padded `character varying(6)`
    (e.g. "001000"), never a number. `.astype(str).str.strip().str.zfill(6)`
    recovers the right value when the source column is already
    object/int64-typed (e.g. int 1000 -> "1000" -> zfill -> "001000"), but
    silently produces the WRONG value when the source column is float64:
    this happens whenever `crsp.ccmxpf_lnkhist` came back through
    pull_table's non-streaming path (used for this small Phase A linking
    table), which does no schema pinning -- pandas' dtype inference can
    land on float64 for a `gvkey` column whenever it has any null, or under
    certain other numeric-inference conditions. 1000.0 -> str -> "1000.0"
    -> zfill(6) is then a NO-OP (already 6 characters) -- silently
    producing a GVKEY that matches nothing in Compustat.

    Fix: detect a float dtype explicitly and route it through
    float -> nullable Int64 -> str -> zfill(6), which correctly turns
    1000.0 into "001000". Any other dtype (object/int64/etc.) keeps the
    original str/strip/zfill path, which is already correct for those.
    Callers are expected to have already dropped null gvkey rows (see
    `cc.dropna(subset=["lpermno", "gvkey"])` at this function's one call
    site) -- this does not itself handle NaN.
    """
    if pd.api.types.is_float_dtype(series):
        return series.astype("Int64").astype(str).str.zfill(6)
    return series.astype(str).str.strip().str.zfill(6)


def _validate_gvkeys(gvkeys) -> None:
    """Raise ValueError naming every GVKEY that fails to match the expected
    zero-padded 6-digit shape (`^\\d{6}\\Z`, see _GVKEY_RE) after
    normalization.

    This is a SAFETY NET, not the primary defense: _normalize_gvkeys
    (called inside resolve_universe) already fixes the actual root cause
    (a float64-typed gvkey column silently coercing to the wrong string).
    This catches anything that still doesn't conform -- e.g. a genuinely
    malformed value from some future data source -- so a shape violation
    is surfaced loudly rather than silently building a GVKEY IN (...)
    clause that matches nothing. Call this before build_string_in_clause
    on any GVKEY list sourced from resolve_universe().
    """
    bad = sorted({str(g) for g in gvkeys if not _GVKEY_RE.match(str(g))})
    if bad:
        raise ValueError(
            f"{len(bad)} GVKEY(s) failed shape validation "
            f"{_GVKEY_RE.pattern!r} after normalization: {bad[:20]}"
        )


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
    permnos_without_gvkey_out=None,
    permnos_without_ibes_ticker_out=None,
) -> ResolvedUniverse:
    """Resolve the OptionMetrics universe (current + historical CUSIPs) to
    CRSP PERMNO, Compustat GVKEY, and I/B/E/S ticker, purely locally (no WRDS
    query) -- see the module comment above this class for why.

    Steps (mirrors memory/phase1-equity-pead-plan.md's "Update 2026-08-12"
    section, extended by this round's Fix 3/5/7/8):
      1. Load the two OM universe files and crsp.stocknames -- the PERMNO
         resolution stage. Any missing file or missing required column here
         raises UniverseResolutionError immediately (see
         _load_parquet_or_raise/_require_columns) -- this is the ONE stage
         allowed to hard-fail the whole run (Fix 3).
      1b. Best-effort load crsp.ccmxpf_lnkhist and wrdsapps.ibcrsphist (Fix
         3): if either is missing/unpullable/malformed, that identifier
         family's resolution is skipped entirely (logged loudly, recorded
         on the returned ResolvedUniverse), NOT raised -- neither table is
         needed for anything CRSP-permno-filtered to work.
      2. Union the current + historical CUSIPs per secid. Sanity-check
         normalized CUSIP lengths on both sides of the upcoming join
         (Fix 8) -- logged as a warning, not a hard failure.
      3. Join against crsp.stocknames on EITHER cusip OR ncusip to get the
         resolved PERMNO(s) per secid.
      4. If available, join the resolved PERMNOs against crsp.ccmxpf_lnkhist
         (on lpermno) to get GVKEY(s), and against wrdsapps.ibcrsphist (on
         permno) to get I/B/E/S ticker(s).
      5. Write the auditability artifacts: unresolved_secids.csv,
         ambiguous_links.csv (all three stages now carry secid/cusip/issuer
         detail, Fix 7), resolved_ids_out, and the two coverage-gap CSVs
         (permnos_without_gvkey.csv / permnos_without_ibes_ticker.csv,
         Fix 5) -- then print a one-line summary.

    Returns a ResolvedUniverse with the deduplicated PERMNO/GVKEY/ticker
    filter lists Phase C's pull_table() calls use, favoring over-inclusion
    for any ambiguous link (see ResolvedUniverse's docstring). Callers
    (pull_wrds_reference.py's main()) MUST check gvkey_resolution_skipped/
    ibes_ticker_resolution_skipped before deciding whether to run the
    GVKEY-/ticker-filtered Phase C pulls, and MUST apply the
    MIN_PERMNO_RESOLUTION_RATE floor check themselves (Fix 2) -- this
    function does not raise on a low-but-nonzero resolution rate, only on
    the PERMNO stage being entirely unusable.
    """
    om = _load_parquet_or_raise(om_universe_path, "optionmetrics_universe.parquet")
    om_hist = _load_parquet_or_raise(
        om_universe_history_path, "optionmetrics_universe_history.parquet"
    )
    stocknames = _load_parquet_or_raise(stocknames_path, "crsp.stocknames pull")

    _require_columns(om, ["secid", "cusip", "ticker", "issuer"], "optionmetrics_universe.parquet")
    _require_columns(om_hist, ["secid", "cusip"], "optionmetrics_universe_history.parquet")
    _require_columns(stocknames, ["permno", "cusip", "ncusip"], "crsp.stocknames pull")

    # Fix 3: these two are soft-loaded -- a missing/unpullable/malformed
    # linking table degrades just its own identifier family, never the
    # whole run. Loud, unmissable console warnings (not just the return
    # value) since this changes what Phase C will and won't pull.
    ccmxpf, gvkey_skip_reason = _load_optional_linking_table(
        ccmxpf_lnkhist_path, ["lpermno", "gvkey"], "crsp.ccmxpf_lnkhist pull"
    )
    ibcrsphist, ibes_ticker_skip_reason = _load_optional_linking_table(
        ibcrsphist_path, ["permno", "ticker"], "wrdsapps.ibcrsphist pull"
    )
    if gvkey_skip_reason:
        print(
            f"WARNING: GVKEY resolution SKIPPED (comp.fundq/comp.funda will "
            f"NOT be pulled) -- {gvkey_skip_reason}",
            flush=True,
        )
    if ibes_ticker_skip_reason:
        print(
            f"WARNING: I/B/E/S ticker resolution SKIPPED (the ibes.* Phase C "
            f"pulls will NOT run) -- {ibes_ticker_skip_reason}",
            flush=True,
        )

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
    _check_cusip_lengths(om_cusips["cusip"], "OptionMetrics universe (current + historical) CUSIPs")

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
    _check_cusip_lengths(sn_long["crsp_cusip"], "crsp.stocknames cusip/ncusip")

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

    # Fix 7: permno -> (secid, om_cusip, om_ticker, issuer) lookup, used to
    # enrich BOTH the permno_to_gvkey/permno_to_ibes_ticker ambiguous-link
    # stages below AND the Fix 5 coverage-gap CSVs -- a permno can map back
    # to more than one secid (many-to-one), so this is intentionally not
    # deduplicated down to one row per permno.
    permno_ids = secid_permno.merge(om_lookup, on="secid", how="left").rename(
        columns={"cusip": "om_cusip", "ticker": "om_ticker"}
    )

    # --- permno -> gvkey (crsp.ccmxpf_lnkhist), restricted to resolved
    # permnos only -- no linkdt/linkenddt window filtering here: this is a
    # broad "which securities need data pulled at all" universe filter, not
    # a point-in-time attribution join, so over-inclusion (every gvkey ever
    # linked to a resolved permno) is the safe direction. Fix 3: ccmxpf is
    # None when crsp.ccmxpf_lnkhist was missing/unpullable -- GVKEY
    # resolution is skipped, not attempted-and-empty, so permno_gvkey is
    # just an empty frame in that case. ---
    if ccmxpf is not None:
        cc = ccmxpf.dropna(subset=["lpermno", "gvkey"]).copy()
        cc["lpermno"] = cc["lpermno"].astype("int64")
        # GVKEY is Compustat's fixed-width, zero-padded 6-digit character key
        # (e.g. "001000"), never a number -- comparing/filtering it as an
        # integer would strip the leading zeros and/or fail outright against
        # a real `varchar` column (Fix 1). _normalize_gvkeys handles BOTH the
        # already-string/int case (str/strip/zfill, a no-op for something
        # already correctly shaped) AND the float64 case (round 2's Fix 1:
        # a naive str/zfill on a float64-typed gvkey column turns 1000.0 into
        # the WRONG "1000.0" instead of "001000", since zfill(6) is a no-op
        # once the string is already 6 characters) -- see its docstring for
        # the full failure mode this recovers from.
        cc["gvkey"] = _normalize_gvkeys(cc["gvkey"])
        permno_gvkey = (
            cc.loc[cc["lpermno"].isin(secid_permno["permno"]), ["lpermno", "gvkey"]]
            .rename(columns={"lpermno": "permno"})
            .drop_duplicates()
        )
    else:
        permno_gvkey = pd.DataFrame(columns=["permno", "gvkey"])

    # --- permno -> I/B/E/S ticker (wrdsapps.ibcrsphist), same reasoning
    # (Fix 3: ibcrsphist is None when that table was missing/unpullable). ---
    if ibcrsphist is not None:
        ic = ibcrsphist.dropna(subset=["permno", "ticker"]).copy()
        ic["permno"] = ic["permno"].astype("int64")
        ic["ticker"] = ic["ticker"].astype(str).str.strip()
        # Fix 2 (round 2): a blank/whitespace-only ticker (e.g. "" -- this
        # survives the dropna() above because it's an empty string, not
        # NaN) is not a usable I/B/E/S ticker. Drop it here, treating it
        # the SAME as a missing link (reduces this permno's I/B/E/S
        # coverage and lands it in permnos_without_ibes_ticker.csv below if
        # it has no other, valid ticker) -- rather than letting a blank
        # value reach main()'s ticker-format allowlist validation, where it
        # would previously have raised and (per the bug this round fixes)
        # aborted the ENTIRE run, including CRSP/Compustat pulls that don't
        # need I/B/E/S at all.
        ic = ic[ic["ticker"] != ""]
        permno_ticker = (
            ic.loc[ic["permno"].isin(secid_permno["permno"]), ["permno", "ticker"]]
            .drop_duplicates()
        )
    else:
        permno_ticker = pd.DataFrame(columns=["permno", "ticker"])

    # Fix 5: coverage-gap audit -- a permno that resolved fine but has no
    # GVKEY/ticker link at all (as opposed to the secid that never got a
    # permno in the first place, already covered by unresolved_secids.csv).
    # Distinguishing "never got a PERMNO" (unresolved_secids.csv) from "got a
    # PERMNO but no GVKEY/ticker link" (these two files) is the whole point
    # of this fix -- someone chasing a coverage gap needs to know which
    # stage actually failed.
    permnos_without_gvkey_out = (
        permnos_without_gvkey_out
        if permnos_without_gvkey_out is not None
        else Path(unresolved_secids_out).parent / "permnos_without_gvkey.csv"
    )
    permnos_without_ibes_ticker_out = (
        permnos_without_ibes_ticker_out
        if permnos_without_ibes_ticker_out is not None
        else Path(unresolved_secids_out).parent / "permnos_without_ibes_ticker.csv"
    )
    def _coverage_gap_df(permnos_with_link: set, skip_reason):
        # Fix 6: when a family was never evaluated at all (its linking
        # table missing/unpullable -- skip_reason is not None), writing
        # every resolved PERMNO here would conflate "we never checked" with
        # "we checked and it doesn't link" -- those are different facts a
        # human chasing a coverage gap needs to tell apart. Write an empty
        # (header-only) frame instead in that case; the skip reason is
        # already surfaced loudly elsewhere (the WARNING printed above, the
        # gvkey_skip_reason/ibes_ticker_skip_reason fields, and the summary
        # line below), so this file staying empty reads as "not evaluated,
        # see the skip reason" rather than a misleadingly large "0 links
        # found for anyone" result.
        if skip_reason is not None:
            return pd.DataFrame(columns=list(permno_ids.columns))
        return (
            permno_ids[~permno_ids["permno"].isin(permnos_with_link)]
            .drop_duplicates()
            .sort_values(["permno", "secid"])
        )

    permnos_with_gvkey = set(permno_gvkey["permno"].unique())
    permnos_with_ibes_ticker = set(permno_ticker["permno"].unique())
    permnos_without_gvkey_df = _coverage_gap_df(permnos_with_gvkey, gvkey_skip_reason)
    permnos_without_ibes_ticker_df = _coverage_gap_df(
        permnos_with_ibes_ticker, ibes_ticker_skip_reason
    )
    permnos_without_gvkey_df.to_csv(permnos_without_gvkey_out, index=False)
    permnos_without_ibes_ticker_df.to_csv(permnos_without_ibes_ticker_out, index=False)
    # These are 0 (not "evaluated, 0 found") when the family was skipped --
    # _coverage_gap_df returns an empty frame in that case, so nunique() on
    # it is naturally 0. The summary print below spells out the skipped
    # case explicitly rather than relying on the reader to infer that from
    # a bare "0".
    n_permnos_without_gvkey = permnos_without_gvkey_df["permno"].nunique()
    n_permnos_without_ibes_ticker = permnos_without_ibes_ticker_df["permno"].nunique()

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

    # Fix 7: both permno_to_gvkey and permno_to_ibes_ticker merge in
    # permno_ids (secid/om_cusip/om_ticker/issuer, from the original OM
    # universe lookup joined via the PERMNO resolution) so all three
    # ambiguity outputs are equally actionable -- previously these two
    # stages carried only a bare permno, forcing a second lookup to act on.
    gvkey_counts = permno_gvkey.groupby("permno")["gvkey"].nunique()
    multi_gvkey_permnos = set(gvkey_counts[gvkey_counts > 1].index)
    if multi_gvkey_permnos:
        stage2 = permno_gvkey[permno_gvkey["permno"].isin(multi_gvkey_permnos)].merge(
            permno_ids, on="permno", how="left"
        )
        stage2["stage"] = "permno_to_gvkey"
        ambiguous_frames.append(stage2)

    ticker_counts = permno_ticker.groupby("permno")["ticker"].nunique()
    multi_ticker_permnos = set(ticker_counts[ticker_counts > 1].index)
    if multi_ticker_permnos:
        stage3 = permno_ticker[permno_ticker["permno"].isin(multi_ticker_permnos)].rename(
            columns={"ticker": "ibes_ticker"}
        ).merge(permno_ids, on="permno", how="left")
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
    # KNOWN LIMITATION (Finding 7, round 2 review -- not fixed, judged not
    # worth the added complexity for a one-time bulk-pull script): this is a
    # post-merge ROW count across all three stages, not a count of distinct
    # ambiguous groups/facts. Cardinality multiplies it -- e.g. a genuine
    # 1-to-many-plus-many-to-one situation (one secid with 3 permnos, one of
    # which is shared by 3 secids) can report as more rows than there are
    # underlying ambiguous facts. ambiguous_links.csv itself is still fully
    # accurate (every ambiguous row is really there, nothing is dropped or
    # fabricated) -- only this single summary NUMBER over- counts versus
    # "how many distinct ambiguous situations are there". Treat this count
    # as "at least this many", and use ambiguous_links.csv's stage/secid/
    # permno/gvkey/ibes_ticker columns directly for anything that needs the
    # real distinct-group count.
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

    # Fix 6: distinguish "family skipped, coverage-gap audit not evaluated"
    # from "evaluated, N found with no link" in the printed summary too --
    # not just in the CSVs -- since n_permnos_without_gvkey/
    # n_permnos_without_ibes_ticker read as 0 in both cases otherwise.
    if gvkey_skip_reason is not None:
        gvkey_gap_note = "GVKEY coverage-gap audit NOT evaluated (resolution skipped)"
    else:
        gvkey_gap_note = f"{n_permnos_without_gvkey:,} resolved PERMNO(s) have no GVKEY link"
    if ibes_ticker_skip_reason is not None:
        ibes_gap_note = (
            "I/B/E/S ticker coverage-gap audit NOT evaluated (resolution skipped)"
        )
    else:
        ibes_gap_note = (
            f"{n_permnos_without_ibes_ticker:,} resolved PERMNO(s) have no "
            f"I/B/E/S ticker link"
        )

    pct = (100.0 * n_resolved / n_total) if n_total else 0.0
    print(
        f"Universe resolution: {n_resolved:,} of {n_total:,} OptionMetrics "
        f"secids resolved to a PERMNO ({pct:.1f}%); {n_ambiguous_links:,} "
        f"ambiguous link(s) flagged; resolved {len(all_permnos):,} distinct "
        f"PERMNOs, {len(all_gvkeys):,} distinct GVKEYs, {len(all_tickers):,} "
        f"distinct I/B/E/S tickers. {gvkey_gap_note}; {ibes_gap_note} "
        f"(Fix 5/6). Details in data/metadata/ (unresolved_secids.csv, "
        f"ambiguous_links.csv, permnos_without_gvkey.csv, "
        f"permnos_without_ibes_ticker.csv, resolved_universe_ids.parquet).",
        flush=True,
    )

    return ResolvedUniverse(
        permnos=all_permnos,
        gvkeys=all_gvkeys,
        ibes_tickers=all_tickers,
        n_secids_total=n_total,
        n_secids_resolved=n_resolved,
        n_ambiguous_links=n_ambiguous_links,
        gvkey_resolution_skipped=gvkey_skip_reason is not None,
        gvkey_skip_reason=gvkey_skip_reason or "",
        ibes_ticker_resolution_skipped=ibes_ticker_skip_reason is not None,
        ibes_ticker_skip_reason=ibes_ticker_skip_reason or "",
        n_permnos_without_gvkey=n_permnos_without_gvkey,
        n_permnos_without_ibes_ticker=n_permnos_without_ibes_ticker,
    )


# ---------------------------------------------------------------------------
# Phase C's IN (...) clause builders. PERMNO is a genuine numeric identifier,
# safe to interpolate as a bare numeric literal once validated as
# digits-only (defensive, consistent with _validate_sql_identifier's
# existing posture on this script's own hardcoded/derived inputs -- these
# values are WRDS-sourced, not external/untrusted input, but the validation
# costs nothing and catches a corrupted resolution result loudly instead of
# building a malformed query).
#
# GVKEY and I/B/E/S ticker are both TEXT identifiers, not numbers (Fix 1):
# Compustat's GVKEY is a fixed-width, zero-padded `character varying(6)`
# (e.g. "001000") -- interpolating it via build_numeric_in_clause would
# either strip its leading zeros (silently matching the wrong rows) or
# render a bare unquoted literal that a real `varchar = integer` comparison
# has no operator for (a hard Postgres error). Both GVKEY and ticker go
# through build_string_in_clause, single-quoted with embedded quotes
# escaped -- string interpolation is less trivially safe than the numeric
# case, but adding SQLAlchemy text()-bound-parameter support to
# pull_table's raw_sql call sites would touch the very internals this round
# is explicitly not supposed to change; proper quoting/escaping here is the
# smaller, contained fix for this specific list.
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


def _validate_numeric_ids(values) -> None:
    """Finding 5 (review round 8): raise ValueError naming EVERY value that
    fails _NUMERIC_ID_RE, mirroring _validate_gvkeys/_validate_ibes_tickers's
    pattern -- a safety net callers (pull_wrds_crsp.py, for its resolved
    PERMNO list) run explicitly BEFORE build_numeric_in_clause, so a
    malformed PERMNO list degrades that script the same visible,
    all-bad-values-named way a malformed GVKEY/ticker list already does for
    pull_wrds_compustat.py/pull_wrds_ibes.py, rather than raising on just
    the first bad value with no upstream classification (which is all
    build_numeric_in_clause's own internal validation gives you)."""
    bad = sorted({str(v) for v in values if not _NUMERIC_ID_RE.match(str(v).strip())})
    if bad:
        raise ValueError(
            f"{len(bad)} numeric id(s) failed shape validation "
            f"{_NUMERIC_ID_RE.pattern!r}: {bad[:20]}"
        )


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


# Fix 6: build_string_in_clause's quote-doubling escape is the standard SQL
# escape and is correct under Postgres's default `standard_conforming_strings
# = on`, but isn't guaranteed safe if that GUC is ever off for this account
# (a backslash-escape context would then apply instead). Exploitability here
# is minimal -- these ticker values come from WRDS's own wrdsapps.ibcrsphist
# table, not external/user input -- but this file's established pattern
# elsewhere (_validate_sql_identifier, _validate_numeric_id) is "validate the
# shape and fail loudly" rather than lean on a database setting. This
# allowlist is intentionally generous: letters, digits, and exactly four
# punctuation marks real I/B/E/S tickers use for share classes/warrants --
# ".", "_", "/", "-" -- e.g. "BRK.A", "BF-B" -- rather than tightly matched
# to any one exchange's ticker convention. "/" covers I/B/E/S's own
# share-class suffix convention (e.g. "NWS/1", "VIA/2"): a live pull found
# this missing -- 14 of 7,355 resolved tickers failed the allowlist for
# exactly this reason and blocked the entire ibes.* pull. "/" was added
# alongside the existing "_", not swapped in for it: an accidental
# "/"-for-"_" swap slipped into this character class once already, so keep
# all four characters -- ".", "_", "/", "-" -- present if you touch this
# regex again.
_IBES_TICKER_RE = re.compile(r"^[A-Z0-9._/-]{1,10}\Z")


def _validate_ibes_tickers(tickers) -> None:
    """Raise ValueError naming every ticker that fails the format allowlist
    (see _IBES_TICKER_RE) rather than silently including or excluding it
    from the IN (...) clause. Call this BEFORE build_string_in_clause on any
    ticker list sourced from resolve_universe()."""
    bad = sorted({str(t) for t in tickers if not _IBES_TICKER_RE.match(str(t))})
    if bad:
        raise ValueError(
            f"{len(bad)} I/B/E/S ticker(s) failed the format allowlist "
            f"{_IBES_TICKER_RE.pattern!r} and were not safely interpolated "
            f"into a SQL IN (...) clause: {bad[:20]}"
        )


# ---------------------------------------------------------------------------
# NEW this round: helpers specifically needed by the split (never existed in
# the monolithic script, since one process used to hold the whole
# ResolvedUniverse in memory from resolve_universe() through every Phase C
# pull_table() call in the same main()).
# ---------------------------------------------------------------------------


def manifest_path_for(component: str) -> Path:
    """Finding 7 (review round 8): per-script manifest path.

    The reference/crsp/compustat/ibes scripts run as 4 SEPARATE OS
    processes, by this split's own design -- the three consumer scripts are
    explicitly meant to run SIMULTANEOUSLY, in separate terminals.
    _MANIFEST_LOCK (a threading.Lock) only ever protected concurrent
    THREADS within one process; it does nothing for 3 separate processes
    all appending to (and, on a fresh run, racing to write the header row
    of) one shared `wrds_pull_manifest.csv`.

    CHOSEN FIX: separate manifest files per script/component
    ("wrds_pull_manifest_<component>.csv") rather than a cross-process file
    lock (e.g. fcntl.flock). This avoids the cross-process locking problem
    entirely instead of trying to get it right -- there is no shared file
    for two processes to ever race on, so there is nothing to lock. Each
    script's own INTERNAL thread pool (currently only pull_wrds_crsp.py has
    one, for its per-year crsp.dsf chunks) still shares ONE manifest path
    across ITS OWN threads within that one process, which _MANIFEST_LOCK
    continues to protect exactly as before.

    `component` is one of "reference", "crsp", "compustat", "ibes". Computed
    fresh from the module-level METADATA_DIR on every call (not cached at
    import time) so it automatically follows METADATA_DIR being pointed at
    a test sandbox, the same way RAW_DIR-derived paths already do elsewhere
    in this module.
    """
    return METADATA_DIR / f"wrds_pull_manifest_{component}.csv"


def library_availability_path() -> Path:
    """Companion metadata file path for write_library_availability()/
    load_library_availability() (Finding 4). Computed fresh from METADATA_DIR
    on every call, same reasoning as manifest_path_for()."""
    return METADATA_DIR / "wrds_library_availability.json"


def write_library_availability(info: dict, path=None) -> None:
    """Finding 4 (review round 8): persist the library-alias-resolution
    result pull_wrds_reference.py's one-time `db.list_libraries()` audit
    already computes (which of ibes/crsp/comp are visible on this account,
    and -- since Compustat can be exposed under different names depending on
    the account/subscription -- which alias comp/compa/compustat is the
    real one) so pull_wrds_compustat.py (a separate process, with no access
    to pull_wrds_reference.py's in-memory `comp_lib` variable) can read the
    ACTUAL resolved library name instead of assuming a hardcoded literal.

    Written atomically (temp file + os.replace), matching this codebase's
    established Parquet atomic-write pattern -- see pull_table's/
    _stream_query_to_parquet's docstrings -- so a failure partway through
    the write can never leave a truncated/corrupt JSON file in place of a
    previously-good one.
    """
    path = Path(path) if path is not None else library_availability_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    os.replace(tmp_path, path)


def load_library_availability(path=None) -> dict:
    """Finding 4: read back the library-alias-resolution result written by
    write_library_availability(). Raises SystemExit with a clear, actionable
    message (mirroring require_resolved_universe_file's pattern) if the file
    doesn't exist yet or can't be parsed -- never a raw FileNotFoundError/
    JSONDecodeError deep inside a consumer script.
    """
    path = Path(path) if path is not None else library_availability_path()
    if not path.exists():
        raise SystemExit(
            f"FATAL: {path} does not exist yet. Run `python "
            f"scripts/pull_wrds_reference.py` FIRST and ALONE -- it produces "
            f"this file, which records the actual resolved WRDS library "
            f"alias(es) for this account (e.g. whether Compustat is exposed "
            f"as comp/compa/compustat here). Nothing here was queried."
        )
    try:
        return json.loads(path.read_text())
    except Exception as e:
        raise SystemExit(
            f"FATAL: {path} exists but could not be parsed as valid JSON -- "
            f"{e}. Re-run scripts/pull_wrds_reference.py (with --force if "
            f"needed) to regenerate it."
        ) from e


# ---------------------------------------------------------------------------
# Round 3 review, Fix 1: provenance-aware re-pull, replacing the round-2
# "delete every existing dsf_<year>.parquet chunk up front on --force" sweep
# (Finding 2 from the prior round). That sweep was found to be WORSE than the
# mixed-vintage problem it fixed: if the connection died (or every re-pull
# legitimately returned zero rows) partway through a --force re-run, it had
# already deleted every chunk file before replacing any of them, destroying
# hours of good data and -- if nothing failed loudly enough to raise -- still
# printing SUCCESS.
#
# The replacement never deletes anything up front. Instead, each chunk's
# output file carries (via the manifest) a record of WHICH resolved-universe
# filter produced it. Before deciding whether to trust an existing chunk file
# (the pre-existing skip-if-exists idempotency check pull_table() already
# does), the caller compares the CURRENT run's universe fingerprint against
# that chunk's own last-recorded fingerprint -- a mismatch (or no recorded
# fingerprint at all) means "don't trust the skip shortcut, ask pull_table()
# to attempt a re-pull", exactly like passing --force for that one chunk.
# pull_table()'s existing atomic write-to-temp-then-os.replace-only-on-
# success logic (unchanged by this fix) is what actually guarantees a failed
# re-pull attempt leaves the existing file completely untouched -- this
# mechanism only ever decides whether to ASK for an attempt, never deletes
# anything itself and never decides the attempt's outcome.
# ---------------------------------------------------------------------------


def compute_universe_fingerprint(resolved_ids_path=None) -> str:
    """Stable fingerprint for "which resolved-universe filter produced this
    run's pulls", computed ONCE per script run (never per chunk) and passed
    through to every pull_table() call that run makes.

    Implementation: sha256 of data/metadata/resolved_universe_ids.parquet's
    raw file bytes. A content hash is used rather than mtime or row count --
    the two simpler alternatives this round considered: mtime granularity
    can collide across two genuinely different regenerations of the file
    (or survive a same-content copy that resets it), and row count alone
    can't detect a same-count-but-different-membership universe change
    (e.g. one PERMNO swapped for another). Hashing the whole file's raw
    bytes also means ANY change to the resolved-ids file -- including a
    GVKEY or I/B/E/S ticker change that never touches the PERMNO column at
    all -- is detected, which is the conservative/safe direction for a
    provenance check whose whole job is "did the universe change".
    """
    resolved_ids_path = (
        Path(resolved_ids_path) if resolved_ids_path is not None else RESOLVED_IDS_PATH
    )
    return hashlib.sha256(Path(resolved_ids_path).read_bytes()).hexdigest()


def last_manifest_fingerprint_for_path(manifest_path, rel_out_path: str):
    """Return the most recently recorded `universe_fingerprint` among the
    manifest rows whose `path` field equals `rel_out_path` exactly (the
    same relative-path string pull_table()'s `_rel(out_path)` produces), or
    None if the manifest doesn't exist yet, has no such row, or the
    matching row(s) predate this field being introduced (blank/missing).

    Manifest rows are appended in chronological order (see
    append_manifest_row), so the LAST matching row is the most recent
    provenance fact known about this exact output file. `path` is only
    populated on statuses that actually concern out_path ("ok",
    "skipped_exists", "ok_empty_query_kept_existing") -- rows for a failed
    attempt (e.g. "query_failed") don't set it and are correctly never
    matched here, since they never affected what's actually sitting at
    out_path.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return None
    last_row = None
    with manifest_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("path") == rel_out_path:
                last_row = row
    if last_row is None:
        return None
    fingerprint = last_row.get("universe_fingerprint")
    return fingerprint if fingerprint else None


def determine_chunk_force(out_path, manifest_path, universe_fingerprint, requested_force) -> bool:
    """Decide the `force` value to pass to pull_table() for ONE specific
    chunk/table output (`out_path`), given the CURRENT run's
    `universe_fingerprint` and that chunk's own manifest history --
    replacing the deleted upfront sweep (see this section's module comment).

    Returns True (ask pull_table() to attempt a re-pull) when:
      - `requested_force` is True (the user explicitly passed --force --
        unconditionally re-verify every chunk, the same top-level meaning
        --force has always had), OR
      - `out_path` doesn't exist yet (harmless either way: pull_table()'s
        own `out_path.exists() and not force` skip check only ever
        special-cases an EXISTING file, so the return value here is moot
        when there's nothing to protect -- False is returned for clarity),
        OR
      - the most recent manifest row for this exact `out_path` has NO
        recorded universe_fingerprint (no provenance to trust) or records a
        DIFFERENT fingerprint than the current run's (the universe filter
        has changed since this chunk was last pulled).

    Returns False (trust the existing file; let pull_table()'s normal
    skip-if-exists idempotency apply) only when the file exists AND its
    last recorded fingerprint matches the current run's exactly.

    This function NEVER deletes anything and NEVER decides a re-pull
    attempt's outcome -- it only decides whether pull_table() is ASKED to
    attempt one. pull_table()'s existing write-to-temp-then-os.replace-
    only-on-success logic is what actually guarantees a failed attempt
    leaves `out_path` completely untouched.
    """
    if requested_force:
        return True
    out_path = Path(out_path)
    if not out_path.exists():
        return False
    recorded = last_manifest_fingerprint_for_path(manifest_path, _rel(out_path))
    if recorded is None:
        # No provenance to compare against -- lean toward re-pulling rather
        # than silently trusting a file with no known history (see this
        # section's module comment / the PR description's Fix 1 point 5).
        return True
    return recorded != universe_fingerprint


# Finding 12 (review round 8): the columns load_resolved_filter_lists()
# structurally depends on. Shared by require_resolved_universe_file() (the
# shape check below) and load_resolved_filter_lists() (_require_columns
# call), so both check the exact same set -- one name, not two copies that
# could drift.
RESOLVED_IDS_REQUIRED_COLUMNS = ["permno", "gvkey", "ibes_ticker"]


def require_resolved_universe_file(path=None) -> Path:
    """Guard shared by the three consumer scripts (pull_wrds_crsp.py,
    pull_wrds_compustat.py, pull_wrds_ibes.py): raise SystemExit with a
    clear, actionable message if data/metadata/resolved_universe_ids.parquet
    doesn't exist yet, OR exists but is missing a column
    load_resolved_filter_lists() structurally depends on (Finding 12) --
    instead of proceeding to build an empty/unfiltered query, or crashing on
    a confusing FileNotFoundError/UniverseResolutionError deep inside
    load_resolved_filter_lists() one line later. Both failure modes ("file
    missing" and "file present but malformed") get the SAME clear,
    actionable message and remediation step here, at the single guard every
    consumer script calls first.

    Only the SCHEMA (column names) is checked here, via pyarrow's schema
    metadata -- deliberately not a full pd.read_parquet() of the whole file,
    so this guard stays cheap even for the eventual real, larger resolved-
    ids file. Cell-level malformation (e.g. a value that can't be parsed as
    a PERMNO) is a separate concern, guarded where it's actually cast (see
    load_resolved_filter_lists's _explode_unique).

    Returns the resolved Path on success (so callers can pass it straight to
    load_resolved_filter_lists without repeating the default-path logic).
    """
    path = Path(path) if path is not None else RESOLVED_IDS_PATH
    if not path.exists():
        raise SystemExit(
            f"FATAL: {path} does not exist yet. Run `python "
            f"scripts/pull_wrds_reference.py` FIRST and ALONE -- it produces "
            f"this file, which every other WRDS pull script filters on. "
            f"Nothing here was queried."
        )
    try:
        actual_columns = set(pq.ParquetFile(str(path)).schema_arrow.names)
    except Exception as e:
        raise SystemExit(
            f"FATAL: {path} exists but could not be read as a valid Parquet "
            f"file ({e}) -- this file is malformed. Re-run `python "
            f"scripts/pull_wrds_reference.py` (with --force if needed) to "
            f"regenerate it. Nothing here was queried."
        ) from e
    missing = [c for c in RESOLVED_IDS_REQUIRED_COLUMNS if c not in actual_columns]
    if missing:
        raise SystemExit(
            f"FATAL: {path} exists but is missing expected column(s) "
            f"{missing} (actual columns: {sorted(actual_columns)}) -- this "
            f"file is malformed/incomplete. Re-run `python "
            f"scripts/pull_wrds_reference.py` (with --force if needed) to "
            f"regenerate it. Nothing here was queried."
        )
    return path


def load_resolved_filter_lists(resolved_ids_path=None):
    """Reconstruct the deduplicated PERMNO/GVKEY/I-B-E-S-ticker filter lists
    from data/metadata/resolved_universe_ids.parquet -- the on-disk
    equivalent of a resolve_universe() call's in-memory
    ResolvedUniverse.permnos/.gvkeys/.ibes_tickers, for a consumer script
    that didn't itself call resolve_universe() (each of pull_wrds_crsp.py/
    pull_wrds_compustat.py/pull_wrds_ibes.py runs as its own process and
    only ever reads this file, produced earlier by pull_wrds_reference.py).

    Each row of that Parquet file is one OM secid, with `permno`/`gvkey`/
    `ibes_ticker` as comma-joined strings (or null) per resolve_universe's
    own writer above. Exploding and unioning those comma-joined values
    across every row reconstructs EXACTLY the same sets resolve_universe()
    itself returned: every permno/gvkey/ticker in resolve_universe()'s
    all_permnos/all_gvkeys/all_tickers appears in at least one secid's row
    (by construction -- see resolve_universe()'s `rows.append(...)` loop),
    and nothing else does.

    Deliberately does NOT try to distinguish "GVKEY resolution was skipped
    entirely" from "GVKEY resolution was attempted but returned zero rows
    for every secid" -- pull_wrds_reference.py's own abort checks (mirroring
    the original script's FATAL checks) guarantee that if this file exists
    and its writer exited successfully, the only way an identifier family's
    column can be all-null here is that the family was genuinely skipped
    (an attempted-but-completely-empty family is a FATAL abort in
    pull_wrds_reference.py itself, before this file's caller would ever be
    told it's safe to proceed) -- so "no gvkeys found in this file" and
    "GVKEY resolution was skipped" are the same actionable fact from a
    consumer script's point of view: don't pull comp.fundq/comp.funda.

    Returns (permnos: list[int], gvkeys: list[str], ibes_tickers: list[str]),
    each deduplicated and sorted, matching resolve_universe()'s own
    all_permnos/all_gvkeys/all_tickers construction.
    """
    resolved_ids_path = Path(resolved_ids_path) if resolved_ids_path is not None else RESOLVED_IDS_PATH
    try:
        df = pd.read_parquet(resolved_ids_path)
    except Exception as e:
        raise UniverseResolutionError(
            f"{resolved_ids_path}: failed to read as Parquet -- resolved "
            f"universe file is malformed, re-run "
            f"scripts/pull_wrds_reference.py. ({e})"
        ) from e
    _require_columns(df, RESOLVED_IDS_REQUIRED_COLUMNS, str(resolved_ids_path))

    def _explode_unique(col_name, caster):
        values = set()
        for cell in df[col_name].dropna():
            for token in str(cell).split(","):
                token = token.strip()
                if not token:
                    continue
                # Finding 6 (review round 8): a malformed cell here (e.g. a
                # non-numeric token in the "permno" column) must not surface
                # as a raw pandas/Python traceback -- this is exactly the
                # class of bug that already bit GVKEY once (a float-
                # stringified numeric ID silently surviving a Parquet
                # round-trip). resolve_universe()'s own writer guards against
                # producing this (permno/lpermno are cast to int64 before
                # being joined into a string -- see _normalize_gvkeys's
                # docstring for the historical bug this pattern fixed), so
                # this should not fire against real resolve_universe()
                # output -- but a hand-edited or corrupted file should still
                # fail loudly and actionably, not with a bare ValueError deep
                # inside this loop.
                try:
                    values.add(caster(token))
                except (ValueError, TypeError) as e:
                    raise UniverseResolutionError(
                        f"{resolved_ids_path}: malformed {col_name!r} value "
                        f"{token!r} could not be parsed -- resolved universe "
                        f"file is malformed, re-run "
                        f"scripts/pull_wrds_reference.py. ({e})"
                    ) from e
        return values

    permnos = sorted(_explode_unique("permno", int))
    gvkeys = sorted(_explode_unique("gvkey", str))
    ibes_tickers = sorted(_explode_unique("ibes_ticker", str))
    return permnos, gvkeys, ibes_tickers


def parse_common_args(argv, description=None):
    """Parse the `--force` flag shared by every script's main() via argparse
    (rather than a hand-rolled `"--force" in sys.argv` check), so a typo
    like `--Force` or `-force` errors out immediately instead of silently
    being ignored -- which would otherwise proceed as if --force hadn't
    been passed (skipping every already-pulled table) and exit looking
    successful. One shared implementation so all four scripts parse this
    flag identically instead of each hand-rolling its own argparse setup.
    """
    import argparse

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-pull every table even if its output Parquet file already exists.",
    )
    return parser.parse_args(argv)
