"""Tests for scripts/pull_wrds_compustat.py -- comp.fundq and comp.funda,
filtered by the resolved GVKEY list, pulled concurrently.
"""

import itertools
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pull_wrds_compustat as compustat_script  # noqa: E402

lib = compustat_script.lib

TABLE_SPECS = [
    ("comp", "fundq", lib.COMP_FUNDQ_REQUIRED_COLUMNS, lib.COMP_FUNDQ_OPTIONAL_COLUMNS),
    ("comp", "funda", lib.COMP_FUNDA_REQUIRED_COLUMNS, lib.COMP_FUNDA_OPTIONAL_COLUMNS),
]
FULL_COLUMN_SET = set().union(*(set(req) | set(opt) for _l, _t, req, opt in TABLE_SPECS))


class FakeCompustatDB:
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
        return FakeCompustatDB(**kwargs)
    return _connect


def _write_resolved_ids(metadata_dir, gvkeys=("001000",)):
    metadata_dir.mkdir(parents=True, exist_ok=True)
    gvkey_str = ",".join(gvkeys)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": ["100"], "gvkey": [gvkey_str if gvkeys else None], "ibes_ticker": ["AAA"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)


def _write_library_availability(metadata_dir, comp_lib="comp", have_comp=True):
    """Finding 4: pull_wrds_compustat.py now reads the resolved Compustat
    library alias from this file (written by pull_wrds_reference.py in
    production) instead of assuming a hardcoded "comp" literal."""
    metadata_dir.mkdir(parents=True, exist_ok=True)
    lib.write_library_availability(
        {"have_ibes": True, "have_crsp": True, "have_comp": have_comp, "comp_lib": comp_lib},
        path=metadata_dir / "wrds_library_availability.json",
    )


def _patch_compustat_env(
    monkeypatch, tmp_path, write_resolved_ids=True, gvkeys=("001000",),
    write_library_availability=True, comp_lib="comp", have_comp=True,
):
    """Finding 7: this script writes to its OWN manifest file
    (wrds_pull_manifest_compustat.csv, via lib.manifest_path_for("compustat")
    -- computed fresh from METADATA_DIR), so patching METADATA_DIR alone is
    enough; no separate MANIFEST_PATH patch needed anymore."""
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    monkeypatch.setattr(lib, "RAW_DIR", raw_dir)
    monkeypatch.setattr(lib, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(lib, "RESOLVED_IDS_PATH", metadata_dir / "resolved_universe_ids.parquet")
    monkeypatch.setattr(lib, "load_env_file", lambda path: None)
    if write_resolved_ids:
        _write_resolved_ids(metadata_dir, gvkeys=gvkeys)
    if write_library_availability:
        _write_library_availability(metadata_dir, comp_lib=comp_lib, have_comp=have_comp)
    manifest_path = lib.manifest_path_for("compustat")
    return raw_dir, metadata_dir, manifest_path


# ---------------------------------------------------------------------------
# Guard + argument parsing.
# ---------------------------------------------------------------------------


def test_missing_resolved_universe_ids_exits_cleanly_not_a_crash(tmp_path, monkeypatch):
    _patch_compustat_env(monkeypatch, tmp_path, write_resolved_ids=False)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    def _unexpected_connect():
        raise AssertionError("connect() must not be called if resolved ids file is missing")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)

    with pytest.raises(SystemExit) as exc_info:
        compustat_script.main()
    assert "pull_wrds_reference.py" in str(exc_info.value)


def test_unrecognized_flag_errors_out_before_connecting(tmp_path, monkeypatch):
    _patch_compustat_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py", "--Force"])

    def _unexpected_connect():
        raise AssertionError("connect() must not be called for a bad flag")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)

    with pytest.raises(SystemExit):
        compustat_script.main()


def test_force_flag_rewires_fundq_pull(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_compustat_env(monkeypatch, tmp_path)
    (raw_dir / "comp").mkdir(parents=True)
    (raw_dir / "comp" / "fundq.parquet").write_bytes(b"stale-marker")

    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py", "--force"])

    compustat_script.main()

    assert (raw_dir / "comp" / "fundq.parquet").read_bytes() != b"stale-marker"


def test_without_force_flag_skips_existing_fundq(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_compustat_env(monkeypatch, tmp_path)
    (raw_dir / "comp").mkdir(parents=True)
    (raw_dir / "comp" / "fundq.parquet").write_bytes(b"stale-marker")

    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()

    assert (raw_dir / "comp" / "fundq.parquet").read_bytes() == b"stale-marker"


# ---------------------------------------------------------------------------
# Column handling.
# ---------------------------------------------------------------------------


def test_full_column_set_succeeds_for_both_tables(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)
    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()

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
    _raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lib, "connect",
        _connect_factory(missing_columns_by_table={f"{library}.{table}": {column}}),
    )
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    # Finding 3: "missing_required_columns" is a graceful, logged skip
    # inside pull_table() (never an exception there) -- but it's NOT one of
    # SUCCESS_STATUSES, so main() must still detect it via the run_log and
    # exit non-zero rather than silently printing SUCCESS.
    with pytest.raises(SystemExit):
        compustat_script.main()

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
    _raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lib, "connect",
        _connect_factory(missing_columns_by_table={f"{library}.{table}": {column}}),
    )
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "ok"
    assert column in rows.iloc[0]["optional_columns_missing"].split(",")


# ---------------------------------------------------------------------------
# GVKEY resolution-skipped / validation-failed degrade -- neither ever
# connects to WRDS at all, since both are detected before any task is built.
# ---------------------------------------------------------------------------


def test_gvkey_resolution_skipped_upstream_skips_both_tables_cleanly(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path, gvkeys=())

    def _unexpected_connect():
        raise AssertionError("connect() must not be called when GVKEY resolution was skipped")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()  # must NOT raise

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[manifest["table"] == "fundq,funda"]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "gvkey_resolution_skipped"


def test_gvkey_validation_failure_skips_both_tables_cleanly(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)
    pd.DataFrame({
        "secid": [1], "cusip": ["1"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
        "permno": ["100"], "gvkey": ["BAD-GVKEY!"], "ibes_ticker": ["AAA"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)

    def _unexpected_connect():
        raise AssertionError("connect() must not be called when GVKEY validation fails")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()  # must NOT raise

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[manifest["table"] == "fundq,funda"]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "gvkey_validation_failed"


# ---------------------------------------------------------------------------
# WHERE clause construction.
# ---------------------------------------------------------------------------


def test_filtered_where_sql_combines_date_std_filter_and_gvkey(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, _manifest_path = _patch_compustat_env(monkeypatch, tmp_path, gvkeys=("001000",))
    dbs = []

    def _connect():
        db = FakeCompustatDB()
        dbs.append(db)
        return db

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()

    all_sql = [sql for db in dbs for sql in db.calls]
    fundq_sql = [s for s in all_sql if "comp.fundq" in s][0]
    funda_sql = [s for s in all_sql if "comp.funda" in s][0]

    assert lib.COMPUSTAT_STD_FILTER in fundq_sql
    assert "gvkey IN ('001000')" in fundq_sql
    assert lib.LOOKBACK_START_DATE in fundq_sql
    assert lib.RETURN_END_DATE in fundq_sql, "fundq's rdq is a PEAD event date -- needs the forward buffer"

    assert lib.COMPUSTAT_STD_FILTER in funda_sql
    assert "gvkey IN ('001000')" in funda_sql
    assert lib.LOOKBACK_START_DATE in funda_sql
    assert lib.END_DATE in funda_sql
    assert lib.RETURN_END_DATE not in funda_sql, "funda is annual control data, not a PEAD event date"


# ---------------------------------------------------------------------------
# NEW: real ThreadPoolExecutor concurrency across fundq/funda.
# ---------------------------------------------------------------------------


def test_fundq_and_funda_pulled_concurrently_both_succeed(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)
    monkeypatch.setattr(lib, "connect", _connect_factory())
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()

    assert (raw_dir / "comp" / "fundq.parquet").exists()
    assert (raw_dir / "comp" / "funda.parquet").exists()
    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert len(manifest) == 2
    assert (manifest["status"] == "ok").all()


def test_one_task_connection_failure_does_not_prevent_the_other_and_is_reported(tmp_path, monkeypatch, capsys):
    """Same reasoning as pull_wrds_crsp.py's equivalent test: a query-level
    failure is already swallowed inside pull_table(); the case that
    genuinely exercises the ThreadPoolExecutor's own failure collection is
    lib.connect() itself raising for one of the two tasks."""
    raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)

    counter = itertools.count()
    counter_lock = threading.Lock()

    def _connect():
        with counter_lock:
            i = next(counter)
        if i == 0:
            raise RuntimeError("simulated WRDS connection failure")
        return FakeCompustatDB()

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    with pytest.raises(SystemExit):
        compustat_script.main()

    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "simulated WRDS connection failure" in out

    fundq_exists = (raw_dir / "comp" / "fundq.parquet").exists()
    funda_exists = (raw_dir / "comp" / "funda.parquet").exists()
    assert fundq_exists != funda_exists, (
        "exactly one of the two tables should have completed despite the "
        "other task's connection failure"
    )
    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert len(manifest) == 1
    assert manifest.iloc[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Finding 3: every query failing must exit non-zero and must NOT print
# SUCCESS.
# ---------------------------------------------------------------------------


def test_every_query_failing_exits_nonzero_and_does_not_print_success(tmp_path, monkeypatch, capsys):
    raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)

    # An empty string is a substring of every SQL string built, so both
    # fundq's and funda's raw_sql() calls fail.
    monkeypatch.setattr(lib, "connect", _connect_factory(fail_marker=""))
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    with pytest.raises(SystemExit):
        compustat_script.main()

    out = capsys.readouterr().out
    assert "SUCCESS" not in out

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert len(manifest) == 2
    assert (manifest["status"] == "query_failed").all()
    assert not (raw_dir / "comp" / "fundq.parquet").exists()
    assert not (raw_dir / "comp" / "funda.parquet").exists()


# ---------------------------------------------------------------------------
# Fix 2 (round 3 review): comp.fundq/comp.funda are GVKEY-filtered,
# substantive tables -- a genuine zero-row result (no exception, no
# pre-existing file) is essentially always a bug, not a legitimate empty
# result, so it must be classified as a FAILURE status
# ("ok_suspicious_zero_rows"), not plain "ok".
# ---------------------------------------------------------------------------


class _ZeroRowFakeCompustatDB(FakeCompustatDB):
    def raw_sql(self, sql, return_iter=False):
        self.calls.append(sql)
        select_list = sql.split("FROM")[0].replace("SELECT", "")
        cols = [c.strip() for c in select_list.split(",") if c.strip()]
        if self.fail_marker is not None and self.fail_marker in sql:
            empty = pd.DataFrame({c: [] for c in cols})
            return iter([empty]) if return_iter else empty
        row = pd.DataFrame({c: [1] for c in cols})
        return iter([row]) if return_iter else row


def test_fundq_zero_rows_is_suspicious_and_fails_the_script(tmp_path, monkeypatch, capsys):
    raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(monkeypatch, tmp_path)

    def _connect():
        return _ZeroRowFakeCompustatDB(fail_marker="comp.fundq")

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    with pytest.raises(SystemExit):
        compustat_script.main()

    out = capsys.readouterr().out
    assert "SUCCESS" not in out

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    fundq_rows = manifest[manifest["table"] == "fundq"]
    assert (fundq_rows["status"] == "ok_suspicious_zero_rows").any()
    assert not (raw_dir / "comp" / "fundq.parquet").exists()
    # funda, unaffected by the marker, must have completed normally.
    funda_rows = manifest[manifest["table"] == "funda"]
    assert (funda_rows["status"] == "ok").any()


# ---------------------------------------------------------------------------
# Finding 4: Compustat is queried under whichever library alias
# pull_wrds_reference.py actually resolved (comp/compa/compustat), never a
# hardcoded "comp" literal.
# ---------------------------------------------------------------------------


def test_queries_compa_not_comp_when_account_exposes_compustat_as_compa(tmp_path, monkeypatch):
    """Finding 4: when the account exposes Compustat as "compa" instead of
    "comp" -- persisted by pull_wrds_reference.py to
    wrds_library_availability.json -- this script must query compa.fundq/
    compa.funda, never a hardcoded comp.fundq/comp.funda."""
    raw_dir, _metadata_dir, _manifest_path = _patch_compustat_env(
        monkeypatch, tmp_path, comp_lib="compa",
    )
    dbs = []

    def _connect():
        db = FakeCompustatDB()
        dbs.append(db)
        return db

    monkeypatch.setattr(lib, "connect", _connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()

    all_sql = [sql for db in dbs for sql in db.calls]
    assert any("FROM compa.fundq" in s for s in all_sql), (
        "expected a query against compa.fundq (the resolved alias), got: "
        f"{all_sql}"
    )
    assert any("FROM compa.funda" in s for s in all_sql)
    assert not any("FROM comp.fundq" in s for s in all_sql), (
        "must NOT have queried a hardcoded comp.fundq when the resolved "
        "alias is compa"
    )
    assert not any("FROM comp.funda" in s for s in all_sql)
    assert (raw_dir / "compa" / "fundq.parquet").exists()
    assert (raw_dir / "compa" / "funda.parquet").exists()


def test_compustat_unavailable_on_account_skips_both_tables_cleanly(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_compustat_env(
        monkeypatch, tmp_path, comp_lib=None, have_comp=False,
    )

    def _unexpected_connect():
        raise AssertionError("connect() must not be called when Compustat is unavailable")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_compustat.py"])

    compustat_script.main()  # must NOT raise

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    rows = manifest[manifest["table"] == "fundq,funda"]
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "comp_library_unavailable"
