"""Tests for scripts/pull_wrds_ibes.py -- ibes.statsumu_epsus, ibes.actu_epsus,
and ibes.detu_epsus, filtered by the resolved I/B/E/S ticker list, pulled
concurrently.
"""

import itertools
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pull_wrds_ibes as ibes_script  # noqa: E402

lib = ibes_script.lib

TABLE_SPECS = [
    ("ibes", "statsumu_epsus", lib.IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS, lib.IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS),
    ("ibes", "actu_epsus", lib.IBES_ACTU_EPSUS_REQUIRED_COLUMNS, lib.IBES_ACTU_EPSUS_OPTIONAL_COLUMNS),
    ("ibes", "detu_epsus", lib.IBES_DETU_EPSUS_REQUIRED_COLUMNS, lib.IBES_DETU_EPSUS_OPTIONAL_COLUMNS),
]
FULL_COLUMN_SET = set().union(*(set(req) | set(opt) for _l, _t, req, opt in TABLE_SPECS))


class FakeIbesDB:
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


def _connect_factory(**kwargs):
    def _connect():
        return FakeIbesDB(**kwargs)
    return _connect


def _write_resolved_ids(metadata_dir, tickers=("AAA",)):
    metadata_dir.mkdir(parents=True, exist_ok=True)
    ticker_str = ",".join(tickers) if tickers else None
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": ["100"], "gvkey": ["001000"], "ibes_ticker": [ticker_str],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)


def _patch_ibes_env(monkeypatch, tmp_path, write_resolved_ids=True, tickers=("AAA",)):
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    manifest_path = metadata_dir / "wrds_pull_manifest.csv"
    monkeypatch.setattr(lib, "RAW_DIR", raw_dir)
    monkeypatch.setattr(lib, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(lib, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(lib, "RESOLVED_IDS_PATH", metadata_dir / "resolved_universe_ids.parquet")
    monkeypatch.setattr(lib, "load_env_file", lambda path: None)
    if write_resolved_ids:
        _write_resolved_ids(metadata_dir, tickers=tickers)
    return raw_dir, metadata_dir, manifest_path


# ---------------------------------------------------------------------------
# Guard + argument parsing.
# ---------------------------------------------------------------------------


def test_missing_resolved_universe_ids_exits_cleanly_not_a_crash(tmp_path, monkeypatch):
    _patch_ibes_env(monkeypatch, tmp_path, write_resolved_ids=False)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    def _unexpected_connect():
        raise AssertionError("connect() must not be called if resolved ids file is missing")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)

    with pytest.raises(SystemExit) as exc_info:
        ibes_script.main()
    assert "pull_wrds_reference.py" in str(exc_info.value)


def test_unrecognized_flag_errors_out_before_connecting(tmp_path, monkeypatch):
    _patch_ibes_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py", "--Force"])

    def _unexpected_connect():
        raise AssertionError("connect() must not be called for a bad flag")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)

    with pytest.raises(SystemExit):
        ibes_script.main()


def test_force_flag_rewires_statsumu_pull(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    (raw_dir / "ibes").mkdir(parents=True)
    (raw_dir / "ibes" / "statsumu_epsus.parquet").write_bytes(b"stale-marker")

    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py", "--force"])

    ibes_script.main()

    assert (raw_dir / "ibes" / "statsumu_epsus.parquet").read_bytes() != b"stale-marker"


def test_without_force_flag_skips_existing_statsumu(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    (raw_dir / "ibes").mkdir(parents=True)
    (raw_dir / "ibes" / "statsumu_epsus.parquet").write_bytes(b"stale-marker")

    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()

    assert (raw_dir / "ibes" / "statsumu_epsus.parquet").read_bytes() == b"stale-marker"


# ---------------------------------------------------------------------------
# Column handling.
# ---------------------------------------------------------------------------


def test_full_column_set_succeeds_for_all_three_tables(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    for library, table, _req, _opt in TABLE_SPECS:
        rows = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
        assert len(rows) == 1
        assert rows.iloc[0]["status"] == "ok"


REQUIRED_CASES = [
    (library, table, column)
    for library, table, required, _optional in TABLE_SPECS
    for column in required
]


@pytest.mark.parametrize("library,table,column", REQUIRED_CASES)
def test_missing_required_column_blocks_that_tables_pull(tmp_path, monkeypatch, library, table, column):
    _raw_dir, _metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lib, "connect",
        _connect_factory(missing_columns_by_table={f"{library}.{table}": {column}}),
    )
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()  # graceful skip, not an exception

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "missing_required_columns"


OPTIONAL_CASES = [
    (library, table, column)
    for library, table, _required, optional in TABLE_SPECS
    for column in optional
]


@pytest.mark.parametrize("library,table,column", OPTIONAL_CASES)
def test_missing_optional_column_does_not_block_that_tables_pull(tmp_path, monkeypatch, library, table, column):
    _raw_dir, _metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lib, "connect",
        _connect_factory(missing_columns_by_table={f"{library}.{table}": {column}}),
    )
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "ok"
    assert column in rows.iloc[0]["optional_columns_missing"].split(",")


# ---------------------------------------------------------------------------
# ibes.detu_epsus (unadjusted), never det_epsus (adjusted).
# ---------------------------------------------------------------------------


def test_uses_unadjusted_detail_table_not_adjusted(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert (manifest["table"] == "detu_epsus").any()
    assert not (manifest["table"] == "det_epsus").any()
    assert "value" in lib.IBES_DETU_EPSUS_REQUIRED_COLUMNS


# ---------------------------------------------------------------------------
# I/B/E/S ticker resolution-skipped / validation-failed degrade -- neither
# ever connects to WRDS at all, since both are detected before any task is
# built.
# ---------------------------------------------------------------------------


def test_ibes_ticker_resolution_skipped_upstream_skips_all_three_tables_cleanly(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path, tickers=())

    def _unexpected_connect():
        raise AssertionError("connect() must not be called when ticker resolution was skipped")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()  # must NOT raise

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[manifest["table"] == "statsumu_epsus,actu_epsus,detu_epsus"]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "ibes_ticker_resolution_skipped"


def test_ibes_ticker_validation_failure_skips_all_three_tables_cleanly(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": ["100"], "gvkey": ["001000"], "ibes_ticker": ["BAD TICKER!"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)

    def _unexpected_connect():
        raise AssertionError("connect() must not be called when ticker validation fails")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()  # must NOT raise

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[manifest["table"] == "statsumu_epsus,actu_epsus,detu_epsus"]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "ibes_ticker_validation_failed"


def test_blank_ticker_already_excluded_by_load_resolved_filter_lists(tmp_path, monkeypatch):
    """A blank/whitespace-only I/B/E/S ticker for one secid alongside a
    valid one for another must not prevent this script from running --
    wrds_pull_lib.load_resolved_filter_lists (tested directly in
    test_wrds_pull_lib.py) already excludes blank tokens when reconstructing
    the ticker list from resolved_universe_ids.parquet, so this script
    never even sees a blank value reach _validate_ibes_tickers."""
    _raw_dir, metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    pd.DataFrame({
        "secid": [1, 2], "cusip": ["1", "2"], "ticker": ["AAA", "BBB"],
        "issuer": ["AAA CORP", "BBB CORP"],
        "permno": ["100", "200"], "gvkey": ["001000", "002000"],
        "ibes_ticker": ["AAA", "   "],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)

    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()  # must NOT raise, must actually pull

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert (manifest["status"] == "ok").sum() == 3


# ---------------------------------------------------------------------------
# WHERE clause construction: RETURN_END_DATE buffer for event dates
# (anndats/revdats), END_DATE only for the snapshot date (statpers).
# ---------------------------------------------------------------------------


def test_filtered_where_sql_combines_date_filters_and_ticker(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, _manifest_path = _patch_ibes_env(monkeypatch, tmp_path, tickers=("AAA",))
    dbs = []

    def _connect():
        db = FakeIbesDB()
        dbs.append(db)
        return db

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()

    all_sql = [sql for db in dbs for sql in db.calls]
    statsumu_sql = [s for s in all_sql if "ibes.statsumu_epsus" in s][0]
    actu_sql = [s for s in all_sql if "ibes.actu_epsus" in s][0]
    detu_sql = [s for s in all_sql if "ibes.detu_epsus" in s][0]

    assert "ticker IN ('AAA')" in statsumu_sql
    assert lib.END_DATE in statsumu_sql
    assert lib.RETURN_END_DATE not in statsumu_sql, "statpers is a snapshot date, not an event date"

    assert "ticker IN ('AAA')" in actu_sql
    assert lib.RETURN_END_DATE in actu_sql, "anndats is a PEAD event date -- needs the forward buffer"

    assert "ticker IN ('AAA')" in detu_sql
    assert lib.RETURN_END_DATE in detu_sql, "revdats is a PEAD event date -- needs the forward buffer"


# ---------------------------------------------------------------------------
# NEW: real ThreadPoolExecutor concurrency across the three tables.
# ---------------------------------------------------------------------------


def test_all_three_tables_pulled_concurrently_all_succeed(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)
    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    ibes_script.main()

    for _lib_name, table, _req, _opt in TABLE_SPECS:
        assert (raw_dir / "ibes" / f"{table}.parquet").exists()
    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert len(manifest) == 3
    assert (manifest["status"] == "ok").all()


def test_one_task_connection_failure_does_not_prevent_the_others_and_is_reported(tmp_path, monkeypatch, capsys):
    """Same reasoning as the CRSP/Compustat equivalents: lib.connect()
    itself raising for one of the three tasks is what genuinely exercises
    the ThreadPoolExecutor's own failure collection (a query-level failure
    is already swallowed inside pull_table())."""
    raw_dir, _metadata_dir, manifest_path = _patch_ibes_env(monkeypatch, tmp_path)

    counter = itertools.count()
    counter_lock = threading.Lock()

    def _connect():
        with counter_lock:
            i = next(counter)
        if i == 1:
            raise RuntimeError("simulated WRDS connection failure")
        return FakeIbesDB()

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_ibes.py"])

    with pytest.raises(SystemExit):
        ibes_script.main()

    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "simulated WRDS connection failure" in out

    existing = [
        (raw_dir / "ibes" / f"{table}.parquet").exists()
        for _lib_name, table, _req, _opt in TABLE_SPECS
    ]
    assert sum(existing) == 2, (
        "exactly one of the three tables should be missing (its task's "
        "connection failed), the other two must have completed"
    )
    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert len(manifest) == 2
    assert (manifest["status"] == "ok").all()
