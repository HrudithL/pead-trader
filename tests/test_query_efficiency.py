"""Static query-structure efficiency audit for every WRDS query this
codebase's four pull scripts actually build.

This does NOT connect to WRDS. It runs the real
pull_wrds_reference.py / pull_wrds_crsp.py / pull_wrds_compustat.py /
pull_wrds_ibes.py `main()` functions (the real query-building code paths --
`wrds_pull_lib.pull_table()` and `_stream_query_to_parquet()`) against one
shared FAKE connection that simply records every `raw_sql()` call's SQL
text, then inspects that captured text -- not a projection or a hand-copied
guess at what the SQL "should" look like.

For every table pulled, it checks:
  1. The query selects only the specific named required+optional columns
     for that table (never `SELECT *`).
  2. Where this project actually filters by both a date range and a
     universe list (permno/gvkey/ticker), both conditions are present in
     the SAME query, joined by a top-level AND (never OR, never built as
     two disconnected clauses). Phase A reference/resolution tables
     (crsp.stocknames, comp.company, wrdsapps.ibcrsphist,
     crsp.ccmxpf_lnkhist, ibes.idsum) are UNFILTERED by design -- see
     wrds_pull_lib.py's Phase A module comment -- and are reported as N/A
     for this check, not a failure.
  3. crsp.dsf specifically: each of its calendar-year chunks' date range is
     scoped to just that one year (not the full 1995-2014 lookback/return
     range), confirming a query against real WRDS would only need to
     scan/return one year's worth of rows per chunk.

Runnable standalone (prints the full pass/fail table without pytest's
output capturing):
    python tests/test_query_efficiency.py
Also runnable as a normal pytest test, which additionally asserts every
check passed:
    pytest tests/test_query_efficiency.py -s
"""

import contextlib
import datetime as dt
import re
import sys
import tempfile
from pathlib import Path

import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import wrds_pull_lib as lib  # noqa: E402
import pull_wrds_reference as ref_script  # noqa: E402
import pull_wrds_crsp as crsp_script  # noqa: E402
import pull_wrds_compustat as compustat_script  # noqa: E402
import pull_wrds_ibes as ibes_script  # noqa: E402

# ---------------------------------------------------------------------------
# Every table pulled anywhere in this codebase, its required+optional
# columns, and how this audit expects it to be filtered.
# ---------------------------------------------------------------------------

_ALL_TABLE_SPECS = [
    ("crsp", "stocknames", lib.CRSP_STOCKNAMES_REQUIRED_COLUMNS, lib.CRSP_STOCKNAMES_OPTIONAL_COLUMNS),
    ("comp", "company", lib.COMP_COMPANY_REQUIRED_COLUMNS, lib.COMP_COMPANY_OPTIONAL_COLUMNS),
    ("wrdsapps", "ibcrsphist", lib.WRDSAPPS_IBCRSPHIST_REQUIRED_COLUMNS, lib.WRDSAPPS_IBCRSPHIST_OPTIONAL_COLUMNS),
    ("crsp", "ccmxpf_lnkhist", lib.CRSP_CCMXPF_LNKHIST_REQUIRED_COLUMNS, lib.CRSP_CCMXPF_LNKHIST_OPTIONAL_COLUMNS),
    ("ibes", "idsum", lib.IBES_IDSUM_REQUIRED_COLUMNS, lib.IBES_IDSUM_OPTIONAL_COLUMNS),
    ("crsp", "dsf", lib.CRSP_DSF_REQUIRED_COLUMNS, lib.CRSP_DSF_OPTIONAL_COLUMNS),
    ("crsp", "dsi", lib.CRSP_DSI_REQUIRED_COLUMNS, lib.CRSP_DSI_OPTIONAL_COLUMNS),
    ("crsp", "dsedelist", lib.CRSP_DSEDELIST_REQUIRED_COLUMNS, lib.CRSP_DSEDELIST_OPTIONAL_COLUMNS),
    ("comp", "fundq", lib.COMP_FUNDQ_REQUIRED_COLUMNS, lib.COMP_FUNDQ_OPTIONAL_COLUMNS),
    ("comp", "funda", lib.COMP_FUNDA_REQUIRED_COLUMNS, lib.COMP_FUNDA_OPTIONAL_COLUMNS),
    ("ibes", "statsumu_epsus", lib.IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS, lib.IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS),
    ("ibes", "actu_epsus", lib.IBES_ACTU_EPSUS_REQUIRED_COLUMNS, lib.IBES_ACTU_EPSUS_OPTIONAL_COLUMNS),
    ("ibes", "detu_epsus", lib.IBES_DETU_EPSUS_REQUIRED_COLUMNS, lib.IBES_DETU_EPSUS_OPTIONAL_COLUMNS),
]
_FULL_COLUMN_SET = set().union(*(set(req) | set(opt) for _l, _t, req, opt in _ALL_TABLE_SPECS))
_EXPECTED_COLUMNS = {
    f"{library}.{table}": set(req) | set(opt) for library, table, req, opt in _ALL_TABLE_SPECS
}

# (expects_universe_filter, expects_date_filter, year_scoped)
_FILTER_EXPECTATIONS = {
    # Phase A: unfiltered BY DESIGN -- these tables ARE the resolution
    # mechanism (or are already small regardless), pulled before any
    # universe filter can even be built. See wrds_pull_lib.py's Phase A
    # module comment.
    "crsp.stocknames": (False, False, False),
    "comp.company": (False, False, False),
    "wrdsapps.ibcrsphist": (False, False, False),
    "crsp.ccmxpf_lnkhist": (False, False, False),
    "ibes.idsum": (False, False, False),
    # Phase C: filtered by both date range and universe list, except
    # crsp.dsi, which is deliberately left unfiltered by PERMNO (one row
    # per trading day market-wide, no per-security ID to filter by).
    "crsp.dsf": (True, True, True),
    "crsp.dsi": (False, True, False),
    "crsp.dsedelist": (True, True, False),
    "comp.fundq": (True, True, False),
    "comp.funda": (True, True, False),
    "ibes.statsumu_epsus": (True, True, False),
    "ibes.actu_epsus": (True, True, False),
    "ibes.detu_epsus": (True, True, False),
}

# Finding 1: the specific identifier column each universe-filtered table's
# `IN (...)` clause must be built on -- NOT just any `IN (...)` clause
# anywhere in the WHERE clause. A query that dropped its real
# `permno IN (...)` filter but happened to retain some unrelated
# `dlstcd IN (100, 200)` clause must FAIL this check, not pass it just
# because *an* IN clause is present somewhere. Derived from this project's
# actual identifier convention (see wrds_pull_lib.build_numeric_in_clause /
# build_string_in_clause call sites in the pull_wrds_*.py scripts): PERMNO
# for CRSP tables, GVKEY for Compustat tables, ticker for I/B/E/S tables.
_UNIVERSE_COLUMN_BY_KEY = {
    "crsp.dsf": "permno",
    "crsp.dsedelist": "permno",
    "comp.fundq": "gvkey",
    "comp.funda": "gvkey",
    "ibes.statsumu_epsus": "ticker",
    "ibes.actu_epsus": "ticker",
    "ibes.detu_epsus": "ticker",
}

N_DSF_YEARS = (
    dt.date.fromisoformat(lib.RETURN_END_DATE).year
    - dt.date.fromisoformat(lib.LOOKBACK_START_DATE).year
    + 1
)
_EXPECTED_QUERY_COUNTS = {key: 1 for key in _EXPECTED_COLUMNS}
_EXPECTED_QUERY_COUNTS["crsp.dsf"] = N_DSF_YEARS


class RecordingDB:
    """Captures every raw_sql() call, keyed by "library.table" (parsed off
    the query's own "FROM library.table" text), for later structural
    inspection. describe_table() always reports the FULL required+optional
    column set for every table this whole codebase pulls (a superset of
    what any one script needs), so no column is ever "missing" for a reason
    unrelated to what this audit checks -- every optional column is always
    selected.

    One instance is deliberately SHARED across all four scripts' main()
    calls below (including pull_wrds_crsp.py's concurrent thread pool) --
    unlike production (one connection per thread) or
    tests/test_pull_wrds_crsp.py's per-instance FakeCrspDB (which exercises
    per-connection isolation). This audit only cares about the SQL TEXT
    each call builds, not connection isolation semantics -- that's already
    covered by tests/test_pull_wrds_crsp.py's concurrency tests -- so a
    single shared recorder, protected by a lock around the one shared
    mutable list it appends to, is simpler and sufficient here.
    """

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self.calls = []  # list of (key, sql)

    def list_libraries(self):
        return ["crsp", "comp", "ibes", "wrdsapps"]

    def describe_table(self, library, table):
        return pd.DataFrame({
            "name": sorted(_FULL_COLUMN_SET), "type": ["VARCHAR"] * len(_FULL_COLUMN_SET),
        })

    def raw_sql(self, sql, return_iter=False):
        match = re.search(r"FROM\s+(\S+)", sql)
        key = match.group(1) if match else "?"
        with self._lock:
            self.calls.append((key, sql))
        select_list = sql.split("FROM")[0].replace("SELECT", "")
        cols = [c.strip() for c in select_list.split(",") if c.strip()]
        row = pd.DataFrame({c: [1] for c in cols})
        return iter([row]) if return_iter else row

    def close(self):
        pass


@contextlib.contextmanager
def _patched(module, **attrs):
    """Finding 5: patching an attribute that doesn't actually exist on
    `module` must FAIL LOUDLY, never silently create a new attribute and
    quietly no-op the intended substitution. Without this guard, a renamed
    constant (e.g. RAW_DIR -> RAW_DATA_DIR) would silently disable this
    audit's sandboxing -- the real module attribute would be left
    untouched, and a subsequent run could write into the REAL
    data/raw_wrds directory or load REAL WRDS credentials via
    load_env_file instead of the fakes this audit relies on."""
    for name in attrs:
        if not hasattr(module, name):
            raise AttributeError(
                f"refusing to patch {module!r}.{name}: no such attribute "
                f"exists on the real module. Patching it anyway would "
                f"silently CREATE a new attribute rather than substituting "
                f"the real one -- exactly the kind of silent sandboxing "
                f"failure that could let this audit touch real data/WRDS "
                f"credentials. If this attribute was renamed in the "
                f"production module, update this patch's keyword to match."
            )
    originals = {name: getattr(module, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(module, name, value)
        yield
    finally:
        for name, original in originals.items():
            setattr(module, name, original)


def _write_universe_fixture(metadata_dir: Path):
    metadata_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["OMX"], "issuer": ["OM FIXTURE"], "sic": ["9999"],
    }).to_parquet(metadata_dir / "optionmetrics_universe.parquet", index=False)
    pd.DataFrame({
        "secid": pd.Series([], dtype="int64"), "cusip": pd.Series([], dtype="object"),
    }).to_parquet(metadata_dir / "optionmetrics_universe_history.parquet", index=False)


def run_all_scripts_and_capture(tmp_root: Path) -> dict:
    """Runs all four real pull scripts' main() -- pull_wrds_reference.py
    first (as production requires), then the other three -- against one
    shared RecordingDB, with no real WRDS/network access anywhere, and
    returns {"library.table": [sql, ...]} for every query actually built.
    crsp.dsf appears N_DSF_YEARS times (once per calendar year); every
    other table appears exactly once.
    """
    raw_dir = tmp_root / "raw"
    metadata_dir = tmp_root / "metadata"
    manifest_path = metadata_dir / "wrds_pull_manifest.csv"
    resolved_ids_path = metadata_dir / "resolved_universe_ids.parquet"
    _write_universe_fixture(metadata_dir)

    db = RecordingDB()
    common_patches = dict(
        RAW_DIR=raw_dir, METADATA_DIR=metadata_dir, MANIFEST_PATH=manifest_path,
        RESOLVED_IDS_PATH=resolved_ids_path, connect=lambda: db,
        load_env_file=lambda path: None,
        # A 1-secid fixture is far below the real ~8,358-row floor -- this
        # audit only cares about the SQL text built downstream of a clean
        # resolution, not the floor check itself (covered elsewhere in
        # tests/test_wrds_pull_lib.py).
        MIN_UNIVERSE_SECID_COUNT=0,
    )

    with _patched(lib, **common_patches):
        with _patched(sys, argv=["pull_wrds_reference.py"]):
            ref_script.main()
        with _patched(sys, argv=["pull_wrds_crsp.py"]):
            crsp_script.main()
        with _patched(sys, argv=["pull_wrds_compustat.py"]):
            compustat_script.main()
        with _patched(sys, argv=["pull_wrds_ibes.py"]):
            ibes_script.main()

    captured = {}
    for key, sql in db.calls:
        captured.setdefault(key, []).append(sql)
    return captured


# ---------------------------------------------------------------------------
# SQL structural analysis helpers.
# ---------------------------------------------------------------------------

_YEAR_RANGE_RE = re.compile(r"BETWEEN '(\d{4}-\d\d-\d\d)' AND '(\d{4}-\d\d-\d\d)'")


def _extract_select_columns(sql: str) -> set:
    select_list = sql.split("FROM")[0]
    select_list = re.sub(r"^\s*SELECT\s+", "", select_list, flags=re.IGNORECASE)
    return {c.strip() for c in select_list.split(",") if c.strip()}


def _extract_where(sql: str):
    m = re.search(r"\bWHERE\b(.*)$", sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


# Finding 4: a naive split on every "AND" token also splits the AND that is
# part of `BETWEEN x AND y` (not a top-level WHERE-clause separator), which
# means a query with just `date BETWEEN '1996-01-01' AND '2013-12-31'` --
# with NO real universe filter at all -- already yields 2+ "fragments" and
# trivially satisfies a "combined via a top-level AND" check regardless of
# whether a universe filter is actually present or joined correctly. Protect
# the BETWEEN...AND... internal AND with a placeholder before splitting on
# real top-level ANDs, then restore it, so fragment count/content reflects
# only genuine top-level WHERE-clause conditions.
_BETWEEN_AND_RE = re.compile(
    r"\bBETWEEN\b\s+('[^']*'|\S+)\s+\bAND\b\s+('[^']*'|\S+)", re.IGNORECASE
)
_BETWEEN_AND_PLACEHOLDER = "\x00BETWEEN_INTERNAL_AND\x00"


def _split_top_level_and(where_clause: str):
    protected = _BETWEEN_AND_RE.sub(
        lambda m: f"BETWEEN {m.group(1)} {_BETWEEN_AND_PLACEHOLDER} {m.group(2)}",
        where_clause,
    )
    fragments = re.split(r"(?<![A-Za-z0-9_])AND(?![A-Za-z0-9_])", protected, flags=re.IGNORECASE)
    return [f.replace(_BETWEEN_AND_PLACEHOLDER, "AND").strip() for f in fragments]


def _has_top_level_or(where_clause: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_])OR(?![A-Za-z0-9_])", where_clause, re.IGNORECASE))


def audit_table(key: str, sqls: list) -> dict:
    """Runs every applicable structural check for one table's captured
    query/queries. Returns {"check_name": (passed: bool, detail: str)}."""
    expects_universe_filter, expects_date_filter, year_scoped = _FILTER_EXPECTATIONS[key]
    checks = {}

    # --- Check 1: named columns only (never SELECT *, never a mismatched
    # subset), for EVERY captured query of this table. ---
    expected_cols = _EXPECTED_COLUMNS[key]
    mismatches = []
    for sql in sqls:
        if re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
            mismatches.append("SELECT * found")
            continue
        actual = _extract_select_columns(sql)
        if actual != expected_cols:
            mismatches.append(f"selected {sorted(actual)} != expected {sorted(expected_cols)}")
    checks["named_columns_only"] = (not mismatches, "; ".join(sorted(set(mismatches))) or "OK")

    # --- Check 2: date range + universe filter combined via a top-level
    # AND (never OR, never missing). N/A for Phase A tables (unfiltered by
    # design). ---
    if expects_universe_filter or expects_date_filter:
        universe_col = _UNIVERSE_COLUMN_BY_KEY.get(key)
        if expects_universe_filter and universe_col is None:
            raise AssertionError(
                f"{key} expects a universe filter but has no entry in "
                f"_UNIVERSE_COLUMN_BY_KEY -- add one (this is an audit bug, "
                f"not a production-code failure)"
            )
        failures = []
        for sql in sqls:
            where_clause = _extract_where(sql)
            if where_clause is None:
                failures.append("no WHERE clause found")
                continue
            if _has_top_level_or(where_clause):
                failures.append("top-level OR found in WHERE clause")
                continue
            fragments = _split_top_level_and(where_clause)
            has_date = any("BETWEEN" in f.upper() for f in fragments)
            # Finding 1: must match specifically on the expected identifier
            # column's own `<column> IN (` -- NOT just any `IN (...)`
            # clause anywhere in the WHERE, which would also (wrongly) pass
            # for e.g. an unrelated `dlstcd IN (100, 200)` clause even if
            # the real `permno IN (...)` universe filter was missing.
            has_universe = universe_col is not None and any(
                re.search(rf"(?<![A-Za-z0-9_]){re.escape(universe_col)}\s+IN\s*\(", f)
                for f in fragments
            )
            if expects_date_filter and not has_date:
                failures.append("no date-range (BETWEEN) fragment found")
            if expects_universe_filter and not has_universe:
                failures.append(
                    f"no '{universe_col} IN (...)' fragment found (an unrelated "
                    f"IN (...) clause on a different column does not count)"
                )
            if expects_date_filter and expects_universe_filter and len(fragments) < 2:
                failures.append("date range and universe filter not joined via AND")
        detail = "; ".join(sorted(set(failures))) or (
            "OK -- date range and universe filter both present, joined via AND, no OR"
            if (expects_date_filter and expects_universe_filter)
            else "OK"
        )
        checks["date_and_universe_filter_combined_via_and"] = (not failures, detail)
    else:
        checks["date_and_universe_filter_combined_via_and"] = (
            True, "N/A -- unfiltered by design (Phase A reference/resolution table)",
        )

    # --- Check 3 (crsp.dsf only): each year-chunk scoped to just its own
    # year, not the full multi-year lookback/return range, AND the whole set
    # of chunks collectively covers the real full date range with no gaps
    # and no duplicated years (Finding 2). Ground truth for what each
    # chunk's boundaries SHOULD be comes from the REAL production chunking
    # function, pull_wrds_crsp._dsf_year_chunks() -- not a second,
    # hand-rolled copy of that boundary logic that could itself drift out
    # of sync with the real one. ---
    if year_scoped:
        failures = []
        expected_chunks = list(crsp_script._dsf_year_chunks())  # [(year, start_iso, end_iso), ...]
        expected_ranges = sorted((start, end) for _year, start, end in expected_chunks)

        actual_ranges = []
        for sql in sqls:
            m = _YEAR_RANGE_RE.search(sql)
            if not m:
                failures.append("could not find a BETWEEN date range")
                continue
            start_s, end_s = m.group(1), m.group(2)
            actual_ranges.append((start_s, end_s))
            start_d, end_d = dt.date.fromisoformat(start_s), dt.date.fromisoformat(end_s)
            if start_d.year != end_d.year:
                failures.append(f"date range spans multiple years ({start_s} to {end_s})")
                continue
            span_days = (end_d - start_d).days
            if span_days > 366:
                failures.append(f"date range wider than one year ({span_days} days): {start_s} to {end_s}")
            if (start_s, end_s) not in expected_ranges:
                failures.append(
                    f"chunk {start_s}..{end_s} does not match any of the real "
                    f"_dsf_year_chunks() boundaries {expected_ranges}"
                )

        # Collective check: the captured chunks, as a SET, must exactly
        # match the real per-year boundaries -- this is what catches e.g.
        # 20 identical chunks all scoped to the same year (each one
        # individually "single-year scoped" and <=366 days, but never
        # collectively covering the real range, and full of duplicates).
        distinct_years = {dt.date.fromisoformat(s).year for s, _e in actual_ranges}
        if len(actual_ranges) != len(expected_chunks):
            failures.append(
                f"expected {len(expected_chunks)} year-chunk queries but captured "
                f"{len(actual_ranges)}"
            )
        elif len(distinct_years) != len(expected_chunks):
            failures.append(
                f"expected {len(expected_chunks)} DISTINCT years covered but only "
                f"{len(distinct_years)} distinct year(s) found across the captured "
                f"chunks (duplicated year-chunk(s)): {sorted(distinct_years)}"
            )
        elif sorted(actual_ranges) != expected_ranges:
            failures.append(
                f"captured chunk date ranges {sorted(actual_ranges)} do not exactly "
                f"match the real _dsf_year_chunks() boundaries {expected_ranges}"
            )
        else:
            # No gaps/overlaps: the union of chunks, laid end to end in
            # order, must reconstruct the exact original
            # [LOOKBACK_START_DATE, RETURN_END_DATE] span with each chunk's
            # start being exactly one day after the previous chunk's end.
            ordered = sorted(actual_ranges)
            if ordered[0][0] != lib.LOOKBACK_START_DATE:
                failures.append(
                    f"union of chunks starts at {ordered[0][0]}, not "
                    f"LOOKBACK_START_DATE ({lib.LOOKBACK_START_DATE})"
                )
            if ordered[-1][1] != lib.RETURN_END_DATE:
                failures.append(
                    f"union of chunks ends at {ordered[-1][1]}, not "
                    f"RETURN_END_DATE ({lib.RETURN_END_DATE})"
                )
            for (_prev_start, prev_end), (next_start, _next_end) in zip(ordered, ordered[1:]):
                expected_next_start = (
                    dt.date.fromisoformat(prev_end) + dt.timedelta(days=1)
                ).isoformat()
                if next_start != expected_next_start:
                    failures.append(
                        f"gap or overlap between chunk ending {prev_end} and chunk "
                        f"starting {next_start} (expected the next chunk to start "
                        f"{expected_next_start})"
                    )

        detail = "; ".join(sorted(set(failures))) or (
            f"OK -- all {len(sqls)} year-chunks confirmed scoped to their own single "
            f"year, matching the real _dsf_year_chunks() boundaries exactly, "
            f"collectively covering {lib.LOOKBACK_START_DATE}..{lib.RETURN_END_DATE} "
            f"with no gaps and no duplicated years"
        )
        checks["yearly_chunk_scoped_to_one_year"] = (not failures, detail)

    return checks


def build_report(captured: dict) -> list:
    rows = []
    # Finding 3: iterate over the UNION of the hardcoded expected-tables
    # list and whatever was actually captured -- never just the hardcoded
    # list alone. A query against a table this audit doesn't know about
    # (e.g. a newly added crsp.msf pull, or one accidentally left in after
    # a refactor) must show up as an explicit FAIL row, never be silently
    # dropped from the report with no failure raised anywhere.
    unexpected_keys = sorted(set(captured) - set(_EXPECTED_COLUMNS))
    for key in list(_EXPECTED_COLUMNS) + unexpected_keys:
        sqls = captured.get(key, [])
        if key not in _EXPECTED_COLUMNS:
            rows.append({
                "table": key,
                "n_queries": len(sqls),
                "n_queries_expected": "?",
                "overall": "FAIL",
                "detail": (
                    f"query captured against {key!r}, which is NOT in this "
                    f"audit's _ALL_TABLE_SPECS list -- an unexpected/unaudited "
                    f"table was queried; add it to _ALL_TABLE_SPECS (and "
                    f"_FILTER_EXPECTATIONS) so it gets properly audited "
                    f"instead of silently skipped"
                ),
            })
            continue
        row = {
            "table": key,
            "n_queries": len(sqls),
            "n_queries_expected": _EXPECTED_QUERY_COUNTS[key],
        }
        if not sqls:
            row["overall"] = "FAIL"
            row["detail"] = "no query captured at all"
            rows.append(row)
            continue
        checks = audit_table(key, sqls)
        for check_name, (passed, detail) in checks.items():
            row[check_name] = "PASS" if passed else f"FAIL: {detail}"
        row["overall"] = "PASS" if all(passed for passed, _ in checks.values()) and len(sqls) == _EXPECTED_QUERY_COUNTS[key] else "FAIL"
        if len(sqls) != _EXPECTED_QUERY_COUNTS[key]:
            row["detail"] = f"expected {_EXPECTED_QUERY_COUNTS[key]} quer{'y' if _EXPECTED_QUERY_COUNTS[key]==1 else 'ies'}, captured {len(sqls)}"
        rows.append(row)
    return rows


def print_report(rows: list) -> None:
    df = pd.DataFrame(rows)
    ordered_cols = [
        "table", "n_queries", "n_queries_expected", "named_columns_only",
        "date_and_universe_filter_combined_via_and", "yearly_chunk_scoped_to_one_year",
        "overall",
    ]
    ordered_cols = [c for c in ordered_cols if c in df.columns] + [
        c for c in df.columns if c not in ordered_cols
    ]
    print("\n" + "=" * 100)
    print("QUERY-STRUCTURE EFFICIENCY AUDIT")
    print("=" * 100)
    print(df[ordered_cols].to_string(index=False))
    n_pass = (df["overall"] == "PASS").sum()
    print(f"\n{n_pass}/{len(df)} tables PASSED every applicable check.")
    print("=" * 100)


# ---------------------------------------------------------------------------
# pytest entry point.
# ---------------------------------------------------------------------------


def test_query_efficiency_audit(tmp_path):
    captured = run_all_scripts_and_capture(tmp_path)
    rows = build_report(captured)
    print_report(rows)

    failures = [r for r in rows if r["overall"] != "PASS"]
    assert not failures, (
        f"{len(failures)} table(s) failed the query-structure audit: "
        f"{[r['table'] for r in failures]} -- see the printed report above "
        f"(run with `pytest tests/test_query_efficiency.py -s` to see it)"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="pead_query_efficiency_audit_") as tmp:
        _captured = run_all_scripts_and_capture(Path(tmp))
    _rows = build_report(_captured)
    print_report(_rows)
    _failures = [r for r in _rows if r["overall"] != "PASS"]
    if _failures:
        sys.exit(1)
