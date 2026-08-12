"""Tests for scripts/pull_wrds_crsp.py -- crsp.dsf (chunked by calendar year,
pulled concurrently), crsp.dsi, and crsp.dsedelist, filtered by the resolved
PERMNO list.

Per this project's "tests that pass without actually proving anything" review
history, the concurrency tests below use a REAL `concurrent.futures.
ThreadPoolExecutor` (via pull_wrds_crsp.main() itself, not a mocked-out
executor) with fake/mocked WRDS connections -- each simulated connection is
its own object (mirroring "each thread opens its own connection" in
production), and the shared on-disk manifest CSV is the one piece of real
mutable state multiple threads actually touch, so a race in
wrds_pull_lib.append_manifest_row's locking would have a real chance to
surface as a corrupted/short manifest file.
"""

import datetime as dt
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pull_wrds_crsp as crsp_script  # noqa: E402

lib = crsp_script.lib

TABLE_SPECS = [
    ("crsp", "dsf", lib.CRSP_DSF_REQUIRED_COLUMNS, lib.CRSP_DSF_OPTIONAL_COLUMNS),
    ("crsp", "dsi", lib.CRSP_DSI_REQUIRED_COLUMNS, lib.CRSP_DSI_OPTIONAL_COLUMNS),
    ("crsp", "dsedelist", lib.CRSP_DSEDELIST_REQUIRED_COLUMNS, lib.CRSP_DSEDELIST_OPTIONAL_COLUMNS),
]
FULL_COLUMN_SET = set().union(*(set(req) | set(opt) for _l, _t, req, opt in TABLE_SPECS))

N_DSF_YEARS = dt.date.fromisoformat(lib.RETURN_END_DATE).year - dt.date.fromisoformat(lib.LOOKBACK_START_DATE).year + 1


class FakeCrspDB:
    """One instance per simulated WRDS connection (crsp_script.lib.connect
    is patched to return a FRESH instance of this class every call, matching
    production's "every thread opens its own connection" design -- nothing
    here is shared mutable state across instances).

    `fail_marker`, if set, makes raw_sql raise for any query whose SQL text
    contains it (e.g. a specific year's date range) -- lets a test target
    exactly one concurrent task's failure deterministically, regardless of
    which order the thread pool happens to schedule tasks in.
    """

    def __init__(self, missing_columns_by_table=None, fail_marker=None):
        self.missing_columns_by_table = missing_columns_by_table or {}
        self.fail_marker = fail_marker
        self.calls = []

    def describe_table(self, library, table):
        missing = self.missing_columns_by_table.get(f"{library}.{table}", set())
        cols = sorted(FULL_COLUMN_SET - set(missing))
        return pd.DataFrame({"name": cols, "type": ["VARCHAR"] * len(cols)})

    def raw_sql(self, sql, return_iter=False):
        self.calls.append(sql)
        if self.fail_marker is not None and self.fail_marker in sql:
            raise RuntimeError(f"simulated failure (marker {self.fail_marker!r})")
        select_list = sql.split("FROM")[0].replace("SELECT", "")
        cols = [c.strip() for c in select_list.split(",") if c.strip()]
        row = pd.DataFrame({c: [1] for c in cols})
        return iter([row]) if return_iter else row

    def close(self):
        pass


def _write_resolved_ids(metadata_dir, permnos=(1,)):
    metadata_dir.mkdir(parents=True, exist_ok=True)
    permno_str = ",".join(str(p) for p in permnos)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": [permno_str], "gvkey": ["001000"], "ibes_ticker": ["AAA"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)


def _patch_crsp_env(monkeypatch, tmp_path, write_resolved_ids=True, permnos=(1,)):
    """Finding 7: this script writes to its OWN manifest file
    (wrds_pull_manifest_crsp.csv, via lib.manifest_path_for("crsp") --
    computed fresh from METADATA_DIR), so patching METADATA_DIR alone is
    enough; no separate MANIFEST_PATH patch needed anymore."""
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    monkeypatch.setattr(lib, "RAW_DIR", raw_dir)
    monkeypatch.setattr(lib, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(lib, "RESOLVED_IDS_PATH", metadata_dir / "resolved_universe_ids.parquet")
    monkeypatch.setattr(lib, "load_env_file", lambda path: None)
    if write_resolved_ids:
        _write_resolved_ids(metadata_dir, permnos=permnos)
    manifest_path = lib.manifest_path_for("crsp")
    return raw_dir, metadata_dir, manifest_path


def _connect_factory(**kwargs):
    def _connect():
        return FakeCrspDB(**kwargs)
    return _connect


# ---------------------------------------------------------------------------
# Guard: refuses to run without resolved_universe_ids.parquet.
# ---------------------------------------------------------------------------


def test_missing_resolved_universe_ids_exits_cleanly_not_a_crash(tmp_path, monkeypatch):
    _patch_crsp_env(monkeypatch, tmp_path, write_resolved_ids=False)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    def _unexpected_connect():
        raise AssertionError("connect() must not be called if resolved ids file is missing")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)

    with pytest.raises(SystemExit) as exc_info:
        crsp_script.main()
    assert "pull_wrds_reference.py" in str(exc_info.value)


# ---------------------------------------------------------------------------
# --force / argument parsing.
# ---------------------------------------------------------------------------


def test_unrecognized_flag_errors_out_before_connecting(tmp_path, monkeypatch):
    _patch_crsp_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py", "--Force"])

    def _unexpected_connect():
        raise AssertionError("connect() must not be called for a bad flag")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)

    with pytest.raises(SystemExit):
        crsp_script.main()


def test_force_flag_rewires_a_year_chunk_that_already_exists(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    (raw_dir / "crsp").mkdir(parents=True)
    stale_year = dt.date.fromisoformat(lib.LOOKBACK_START_DATE).year
    (raw_dir / "crsp" / f"dsf_{stale_year}.parquet").write_bytes(b"stale-marker")

    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py", "--force"])

    crsp_script.main()

    assert (raw_dir / "crsp" / f"dsf_{stale_year}.parquet").read_bytes() != b"stale-marker", (
        "with --force, an already-existing year-chunk file must be re-queried"
    )


def test_without_force_flag_skips_existing_year_chunk(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    (raw_dir / "crsp").mkdir(parents=True)
    stale_year = dt.date.fromisoformat(lib.LOOKBACK_START_DATE).year
    (raw_dir / "crsp" / f"dsf_{stale_year}.parquet").write_bytes(b"stale-marker")

    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    crsp_script.main()

    assert (raw_dir / "crsp" / f"dsf_{stale_year}.parquet").read_bytes() == b"stale-marker"


# ---------------------------------------------------------------------------
# Column handling, applied uniformly across every year-chunk connection.
# ---------------------------------------------------------------------------


def test_full_column_set_succeeds_for_dsi_and_dsedelist_and_every_dsf_year(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    crsp_script.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    dsf_rows = manifest[manifest["table"] == "dsf"]
    assert len(dsf_rows) == N_DSF_YEARS
    assert (dsf_rows["status"] == "ok").all()
    for table in ("dsi", "dsedelist"):
        rows = manifest[manifest["table"] == table]
        assert len(rows) == 1
        assert rows.iloc[0]["status"] == "ok"


REQUIRED_CASES = [
    (library, table, column)
    for library, table, required, _optional in TABLE_SPECS
    for column in required
]


@pytest.mark.parametrize("library,table,column", REQUIRED_CASES)
def test_missing_required_column_blocks_that_tables_pull(tmp_path, monkeypatch, library, table, column):
    _raw_dir, _metadata_dir, manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lib, "connect",
        _connect_factory(missing_columns_by_table={f"{library}.{table}": {column}}),
    )
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    # A missing required column is a graceful, logged skip inside
    # pull_table() (status "missing_required_columns") -- pull_table itself
    # never raises for this. But Finding 3: this status is NOT one of
    # SUCCESS_STATUSES, so main() must still detect it via the run_log and
    # exit non-zero rather than silently printing SUCCESS -- a whole
    # table's data failed to get pulled, which is exactly the kind of
    # thing that must not look like a clean success.
    with pytest.raises(SystemExit):
        crsp_script.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[manifest["table"] == table]
    assert len(rows) == (N_DSF_YEARS if table == "dsf" else 1)
    assert (rows["status"] == "missing_required_columns").all()


OPTIONAL_CASES = [
    (library, table, column)
    for library, table, _required, optional in TABLE_SPECS
    for column in optional
]


@pytest.mark.parametrize("library,table,column", OPTIONAL_CASES)
def test_missing_optional_column_does_not_block_that_tables_pull(tmp_path, monkeypatch, library, table, column):
    _raw_dir, _metadata_dir, manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lib, "connect",
        _connect_factory(missing_columns_by_table={f"{library}.{table}": {column}}),
    )
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    crsp_script.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[manifest["table"] == table]
    assert len(rows) == (N_DSF_YEARS if table == "dsf" else 1)
    assert (rows["status"] == "ok").all()
    for _, row in rows.iterrows():
        assert column in row["optional_columns_missing"].split(",")


# ---------------------------------------------------------------------------
# WHERE clause construction: permno filter combined with date range, unique
# per calendar year for dsf, dsi left unfiltered.
# ---------------------------------------------------------------------------


def test_filtered_where_sql_combines_permno_and_date_filters(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path, permnos=(1,))

    dbs = []

    def _connect():
        db = FakeCrspDB()
        dbs.append(db)
        return db

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    crsp_script.main()

    all_sql = [sql for db in dbs for sql in db.calls]
    dsi_sql = [s for s in all_sql if "crsp.dsi" in s][0]
    dsedelist_sql = [s for s in all_sql if "crsp.dsedelist" in s][0]
    dsf_sql = [s for s in all_sql if "crsp.dsf" in s]

    assert "IN (" not in dsi_sql, "crsp.dsi must stay unfiltered by PERMNO"
    assert lib.LOOKBACK_START_DATE in dsi_sql
    assert lib.RETURN_END_DATE in dsi_sql

    assert "permno IN (1)" in dsedelist_sql
    assert lib.LOOKBACK_START_DATE in dsedelist_sql
    assert lib.RETURN_END_DATE in dsedelist_sql

    assert len(dsf_sql) == N_DSF_YEARS
    for sql in dsf_sql:
        assert "permno IN (1)" in sql


def test_dsf_year_chunks_span_lookback_to_return_end_date_with_correct_boundaries():
    chunks = list(crsp_script._dsf_year_chunks())
    lookback_start = dt.date.fromisoformat(lib.LOOKBACK_START_DATE)
    return_end = dt.date.fromisoformat(lib.RETURN_END_DATE)

    assert len(chunks) == N_DSF_YEARS
    years = [c[0] for c in chunks]
    assert years == list(range(lookback_start.year, return_end.year + 1))

    first_year, first_start, first_end = chunks[0]
    assert first_start == lib.LOOKBACK_START_DATE
    assert first_end == f"{first_year}-12-31"

    last_year, last_start, last_end = chunks[-1]
    assert last_start == f"{last_year}-01-01"
    assert last_end == lib.RETURN_END_DATE

    # Every middle year (neither the first nor the last) spans its own full
    # calendar year, not clipped.
    for year, start, end in chunks[1:-1]:
        assert start == f"{year}-01-01"
        assert end == f"{year}-12-31"


# ---------------------------------------------------------------------------
# NEW: real ThreadPoolExecutor concurrency. Each simulated connection is
# independent (mirroring "each thread opens its own connection"); the
# manifest CSV is the one shared, real piece of mutable state every thread
# actually writes to, via wrds_pull_lib.append_manifest_row's lock.
# ---------------------------------------------------------------------------


def test_dsf_year_chunking_produces_one_file_per_year(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    crsp_script.main()

    lookback_start = dt.date.fromisoformat(lib.LOOKBACK_START_DATE)
    return_end = dt.date.fromisoformat(lib.RETURN_END_DATE)
    expected_files = {
        raw_dir / "crsp" / f"dsf_{year}.parquet"
        for year in range(lookback_start.year, return_end.year + 1)
    }
    assert len(expected_files) == N_DSF_YEARS
    for f in expected_files:
        assert f.exists(), f"expected year-chunk file {f} to exist"


def test_one_year_chunk_query_failure_does_not_prevent_others_and_is_reported(tmp_path, monkeypatch, capsys):
    """A query-level failure (e.g. a WRDS query error for one year) is
    already caught INSIDE wrds_pull_lib.pull_table() itself (logged as
    status "query_failed", never re-raised) -- true for both the original
    sequential script and this concurrent one, so this alone doesn't
    exercise the executor's own failure-collection path. This test proves
    that case still lets every other year/table complete normally --
    and (Finding 3) that main() still detects the one "query_failed" row via
    the run_log and exits non-zero without printing SUCCESS, even though
    pull_table() itself never raised for it."""
    raw_dir, _metadata_dir, manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    lookback_start = dt.date.fromisoformat(lib.LOOKBACK_START_DATE)
    failing_year = lookback_start.year + 2  # an interior year, not the first/last

    monkeypatch.setattr(lib, "connect", _connect_factory(fail_marker=f"'{failing_year}-01-01'"))
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    with pytest.raises(SystemExit):
        crsp_script.main()
    assert "SUCCESS" not in capsys.readouterr().out

    # Every OTHER year's chunk (and dsi/dsedelist) must still have completed.
    return_end = dt.date.fromisoformat(lib.RETURN_END_DATE)
    for year in range(lookback_start.year, return_end.year + 1):
        f = raw_dir / "crsp" / f"dsf_{year}.parquet"
        if year == failing_year:
            assert not f.exists(), "the failing year must not produce a file"
        else:
            assert f.exists(), f"year {year} should have completed despite {failing_year}'s failure"
    assert (raw_dir / "crsp" / "dsi.parquet").exists()
    assert (raw_dir / "crsp" / "dsedelist.parquet").exists()

    # The manifest itself must have survived concurrent writes from every
    # thread intact (not corrupted/interleaved by a missing lock) -- one row
    # per year (N-1 "ok" + 1 "query_failed"), readable back as a clean CSV.
    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    dsf_rows = manifest[manifest["table"] == "dsf"]
    assert len(dsf_rows) == N_DSF_YEARS
    assert (dsf_rows["status"] == "query_failed").sum() == 1
    assert (dsf_rows["status"] == "ok").sum() == N_DSF_YEARS - 1


def test_dsf_year_chunk_output_content_is_not_cross_contaminated_across_threads(tmp_path, monkeypatch):
    """Goes one step further than test_dsf_year_chunking_produces_one_file_
    per_year and test_filtered_where_sql_combines_permno_and_date_filters
    above: those prove the right FILENAMES exist and the right SQL TEXT was
    issued, but not that each file's actual DATA came from its own year's
    connection rather than another concurrent thread's. Each simulated
    connection here tags its returned rows with the year found in its own
    query's WHERE clause (in the `prc` column, reusing an existing required
    column rather than inventing a new one) -- if a closure-captured-loop-
    variable bug (or any other cross-thread mixup: a shared mutable buffer,
    a wrong `out_name`, etc.) let one thread's data or destination leak into
    another's, this test would catch it directly by reading each
    dsf_<year>.parquet file back and checking its tag against its own
    filename's year, rather than inferring correctness indirectly from
    filenames/SQL text alone."""
    raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)

    class TaggedFakeCrspDB(FakeCrspDB):
        def raw_sql(self, sql, return_iter=False):
            self.calls.append(sql)
            select_list = sql.split("FROM")[0].replace("SELECT", "")
            cols = [c.strip() for c in select_list.split(",") if c.strip()]
            m = re.search(r"date BETWEEN '(\d{4})-", sql)
            tag = m.group(1) if m else "0"
            row = pd.DataFrame({c: ([tag] if c == "prc" else [1]) for c in cols})
            return iter([row]) if return_iter else row

    monkeypatch.setattr(lib, "connect", lambda: TaggedFakeCrspDB())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    crsp_script.main()

    lookback_start = dt.date.fromisoformat(lib.LOOKBACK_START_DATE)
    return_end = dt.date.fromisoformat(lib.RETURN_END_DATE)
    for year in range(lookback_start.year, return_end.year + 1):
        f = raw_dir / "crsp" / f"dsf_{year}.parquet"
        df = pd.read_parquet(f)
        assert (df["prc"].astype(str) == str(year)).all(), (
            f"{f} contains data tagged for a different year than its own "
            f"filename -- cross-contamination between concurrent year-chunk "
            f"workers"
        )


def test_one_task_connection_failure_does_not_prevent_others_and_is_reported(tmp_path, monkeypatch, capsys):
    """A failure OUTSIDE pull_table()'s own protection -- e.g. lib.connect()
    itself raising for one of the 22 concurrent tasks (a real WRDS
    connection error, not a query error) -- is the case that genuinely
    exercises the executor's own failure collection: the worker function
    raises before it ever reaches pull_table(), so this Future's
    .result() must raise, and pull_wrds_crsp.main() must collect that
    failure via try/except around each future (not let one bad future
    crash the whole `as_completed` loop or silently vanish), still let
    every OTHER task complete, and exit with SystemExit(1) reporting it.

    Real ThreadPoolExecutor, real (if fake) per-thread connections -- the
    failure is tied to a specific CALL INDEX (thread-safe counter) rather
    than a specific year, since which of the 22 tasks the thread pool
    happens to schedule Nth is not something a test should assume.

    Finding 9: with per-WORKER connection pooling, lib.connect() is called
    at most MAX_CONCURRENT_CONNECTIONS times total (once per worker thread
    that ever picks up a task), never once per task -- so the failure is
    pinned to the very FIRST connect() call (index 0), the only index
    guaranteed to be reached regardless of the worker count. Since a
    worker's conn_pool.get() retries lib.connect() again on its NEXT task
    after a failed attempt (nothing gets cached on failure), exactly one
    task -- whichever task the pool happens to schedule first overall --
    fails and every other task (including that same worker's later tasks)
    still completes normally, the same as the pre-Finding-9 behavior this
    test originally proved for a per-task-connection design."""
    raw_dir, _metadata_dir, manifest_path = _patch_crsp_env(monkeypatch, tmp_path)

    import itertools
    import threading

    counter = itertools.count()
    counter_lock = threading.Lock()
    FAIL_ON_CALL = 0

    def _connect():
        with counter_lock:
            i = next(counter)
        if i == FAIL_ON_CALL:
            raise RuntimeError("simulated WRDS connection failure")
        return FakeCrspDB()

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    with pytest.raises(SystemExit):
        crsp_script.main()

    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "simulated WRDS connection failure" in out

    # 22 tasks total (20 dsf years + dsi + dsedelist); exactly one failed to
    # even connect, so exactly 21 output files must exist.
    lookback_start = dt.date.fromisoformat(lib.LOOKBACK_START_DATE)
    return_end = dt.date.fromisoformat(lib.RETURN_END_DATE)
    dsf_files = [
        raw_dir / "crsp" / f"dsf_{year}.parquet"
        for year in range(lookback_start.year, return_end.year + 1)
    ]
    existing = [f for f in dsf_files if f.exists()]
    existing_extra = [
        p for p in (raw_dir / "crsp" / "dsi.parquet", raw_dir / "crsp" / "dsedelist.parquet") if p.exists()
    ]
    assert len(existing) + len(existing_extra) == N_DSF_YEARS + 2 - 1, (
        "exactly one task's connection failure must not prevent the other "
        "21 tasks from completing"
    )

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    # The one task whose connect() failed never reached pull_table() at all,
    # so it has NO manifest row (nothing to log against) -- confirmed
    # instead via the file-count assertion above and the printed failure.
    assert len(manifest) == N_DSF_YEARS + 2 - 1


# ---------------------------------------------------------------------------
# Finding 3: every query failing must exit non-zero and must NOT print
# SUCCESS -- not just "one of 22 fails".
# ---------------------------------------------------------------------------


def test_every_query_failing_exits_nonzero_and_does_not_print_success(tmp_path, monkeypatch, capsys):
    raw_dir, _metadata_dir, manifest_path = _patch_crsp_env(monkeypatch, tmp_path)

    # An empty string is a substring of every SQL string built, so every
    # single raw_sql() call (all 22 tasks) fails.
    monkeypatch.setattr(lib, "connect", _connect_factory(fail_marker=""))
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    with pytest.raises(SystemExit):
        crsp_script.main()

    out = capsys.readouterr().out
    assert "SUCCESS" not in out

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert len(manifest) == N_DSF_YEARS + 2
    assert (manifest["status"] == "query_failed").all()
    for f in raw_dir.glob("crsp/dsf_*.parquet"):
        raise AssertionError(f"{f} must not exist -- every query failed")
    assert not (raw_dir / "crsp" / "dsi.parquet").exists()
    assert not (raw_dir / "crsp" / "dsedelist.parquet").exists()


# ---------------------------------------------------------------------------
# Finding 5: empty/invalid PERMNO list must be caught and reported clearly,
# never silently produce a match-nothing "permno IN (NULL)" pull that then
# reports success.
# ---------------------------------------------------------------------------


def test_empty_permno_list_is_caught_and_reported_clearly(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path, write_resolved_ids=False)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": [None], "gvkey": ["001000"], "ibes_ticker": ["AAA"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)

    def _unexpected_connect():
        raise AssertionError("connect() must not be called with an empty PERMNO list")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    with pytest.raises(SystemExit) as exc_info:
        crsp_script.main()
    assert "PERMNO" in str(exc_info.value)
    assert "empty" in str(exc_info.value).lower()


def test_malformed_permno_value_is_caught_and_reported_clearly(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path, write_resolved_ids=False)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": ["not-a-number"], "gvkey": ["001000"], "ibes_ticker": ["AAA"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)

    with pytest.raises(lib.UniverseResolutionError):
        # This is caught by pull_wrds_crsp.py's own wrapping and converted
        # to a clean SystemExit -- exercised end to end below via main().
        lib.load_resolved_filter_lists(metadata_dir / "resolved_universe_ids.parquet")

    def _unexpected_connect():
        raise AssertionError("connect() must not be called with a malformed PERMNO list")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    with pytest.raises(SystemExit) as exc_info:
        crsp_script.main()
    assert "malformed" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Finding 2: --force must delete every existing dsf_<year>.parquet chunk
# up front, so a partial re-run under a changed universe filter can never
# leave a mixed-vintage set (some years old-filter, some new-filter) that a
# later non-force run would silently treat as complete.
# ---------------------------------------------------------------------------


def test_force_flag_deletes_all_existing_year_chunks_upfront_preventing_mixed_vintage_splice(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    (raw_dir / "crsp").mkdir(parents=True)
    lookback_start = dt.date.fromisoformat(lib.LOOKBACK_START_DATE)
    return_end = dt.date.fromisoformat(lib.RETURN_END_DATE)
    failing_year = lookback_start.year + 2  # an interior year, not the first/last

    # Simulate stale chunks left by a PRIOR run under a DIFFERENT universe
    # filter -- every year gets a distinct marker so we can tell a
    # genuinely re-pulled file apart from one that was merely left alone.
    for year in range(lookback_start.year, return_end.year + 1):
        (raw_dir / "crsp" / f"dsf_{year}.parquet").write_bytes(f"stale-{year}".encode())

    monkeypatch.setattr(lib, "connect", _connect_factory(fail_marker=f"'{failing_year}-01-01'"))
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py", "--force"])

    with pytest.raises(SystemExit):
        # Finding 3's fix means the one simulated query failure (for
        # failing_year) now makes this exit non-zero -- expected here, and
        # orthogonal to what THIS test is proving (the upfront deletion).
        crsp_script.main()

    for year in range(lookback_start.year, return_end.year + 1):
        f = raw_dir / "crsp" / f"dsf_{year}.parquet"
        if year == failing_year:
            assert not f.exists(), (
                "the failing year's STALE chunk must be gone too -- a "
                "subsequent non-force run must re-pull it rather than "
                "silently treating the old, different-vintage file as done"
            )
        else:
            assert f.read_bytes() != f"stale-{year}".encode(), (
                f"year {year} must have been genuinely re-pulled under "
                f"--force, not left as the stale marker from a prior run "
                f"under a different universe filter"
            )


# ---------------------------------------------------------------------------
# Finding 11: a legacy dsf.parquet (no year suffix, from a prior monolithic-
# script run) must be rejected loudly, not silently coexist with the new
# dsf_<year>.parquet files.
# ---------------------------------------------------------------------------


def test_legacy_unsuffixed_dsf_parquet_is_rejected(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)
    (raw_dir / "crsp").mkdir(parents=True)
    (raw_dir / "crsp" / "dsf.parquet").write_bytes(b"legacy-monolithic-script-output")

    def _unexpected_connect():
        raise AssertionError("connect() must not be called when a legacy dsf.parquet exists")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    with pytest.raises(SystemExit) as exc_info:
        crsp_script.main()
    assert "dsf.parquet" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Finding 9: one wrds.Connection() per WORKER THREAD, reused across every
# task that thread processes -- never one per task.
# ---------------------------------------------------------------------------


def test_connect_called_at_most_once_per_worker_not_once_per_task(tmp_path, monkeypatch):
    import itertools
    import threading

    _raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)

    counter = itertools.count()
    counter_lock = threading.Lock()

    def _connect():
        with counter_lock:
            next(counter)
        return FakeCrspDB()

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    crsp_script.main()

    n_connect_calls = next(counter)
    assert 1 <= n_connect_calls <= lib.MAX_CONCURRENT_CONNECTIONS, (
        f"expected at most {lib.MAX_CONCURRENT_CONNECTIONS} connect() call(s) "
        f"(one per worker thread, reused across every task that thread "
        f"processes), but got {n_connect_calls} -- a per-task-connection "
        f"design would have produced {N_DSF_YEARS + 2} instead"
    )


# ---------------------------------------------------------------------------
# Finding 10: Ctrl-C must cancel remaining queued tasks promptly, not let
# every one of them drain to completion first.
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_cancels_remaining_queued_tasks_promptly(tmp_path, monkeypatch):
    import itertools
    import threading
    import time

    raw_dir, _metadata_dir, _manifest_path = _patch_crsp_env(monkeypatch, tmp_path)

    call_counter = itertools.count()
    counter_lock = threading.Lock()

    class InterruptingFakeCrspDB(FakeCrspDB):
        def raw_sql(self, sql, return_iter=False):
            with counter_lock:
                i = next(call_counter)
            if i == 0:
                raise KeyboardInterrupt("simulated Ctrl-C")
            # Other workers' first tasks sleep briefly so the interrupt is
            # detected (via as_completed) well before they finish, leaving
            # the remaining ~18 not-yet-started tasks genuinely still
            # queued (not started) for cancel_futures=True to cancel.
            time.sleep(0.05)
            return super().raw_sql(sql, return_iter=return_iter)

    monkeypatch.setattr(lib, "connect", lambda: InterruptingFakeCrspDB())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_crsp.py"])

    with pytest.raises(KeyboardInterrupt):
        crsp_script.main()

    lookback_start = dt.date.fromisoformat(lib.LOOKBACK_START_DATE)
    return_end = dt.date.fromisoformat(lib.RETURN_END_DATE)
    completed = sum(
        1 for year in range(lookback_start.year, return_end.year + 1)
        if (raw_dir / "crsp" / f"dsf_{year}.parquet").exists()
    )
    assert completed < N_DSF_YEARS, (
        "expected most of the queued year-chunks to be CANCELLED rather "
        f"than run to completion after the simulated Ctrl-C -- got "
        f"{completed} of {N_DSF_YEARS} completed, which looks like the "
        f"executor drained its whole queue instead of shutting down "
        f"promptly"
    )
