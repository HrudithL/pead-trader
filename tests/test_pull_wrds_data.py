"""Unit tests for scripts/pull_wrds_data.py -- only the parts that don't need
a real WRDS connection: load_env_file, and pull_table's column-matching /
required-columns / idempotency logic against a stub `db` object.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy.exc as sa_exc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pull_wrds_data as pwd  # noqa: E402


# ---------------------------------------------------------------------------
# load_env_file
# ---------------------------------------------------------------------------


def test_load_env_file_handles_comments_blank_lines_and_quoted_values(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# a full-line comment",
                "",
                "   ",
                "WRDS_USERNAME=plainvalue",
                'WRDS_PASSWORD="quoted value"',
                "SINGLE_QUOTED='single quoted'",
                "# another comment after blank lines",
                "WITH_HASH=sekret#123",
            ]
        )
        + "\n"
    )

    for key in ("WRDS_USERNAME", "WRDS_PASSWORD", "SINGLE_QUOTED", "WITH_HASH"):
        monkeypatch.delenv(key, raising=False)

    pwd.load_env_file(env_path)

    assert __import__("os").environ["WRDS_USERNAME"] == "plainvalue"
    assert __import__("os").environ["WRDS_PASSWORD"] == "quoted value"
    assert __import__("os").environ["SINGLE_QUOTED"] == "single quoted"
    # A literal '#' inside a value must NOT be treated as a mid-line comment
    # and truncate the value.
    assert __import__("os").environ["WITH_HASH"] == "sekret#123"


def test_load_env_file_missing_file_is_a_noop(tmp_path):
    # Should not raise even if the .env file doesn't exist.
    pwd.load_env_file(tmp_path / "does_not_exist.env")


def test_load_env_file_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("SOME_KEY=from_file\n")
    monkeypatch.setenv("SOME_KEY", "from_process_env")

    pwd.load_env_file(env_path)

    # os.environ.setdefault semantics: process env wins over the .env file.
    assert __import__("os").environ["SOME_KEY"] == "from_process_env"


# ---------------------------------------------------------------------------
# pull_table stub
# ---------------------------------------------------------------------------


class StubDB:
    """Minimal stand-in for wrds.Connection exposing only what pull_table
    actually calls: describe_table(library, table) and
    raw_sql(sql, return_iter=...)."""

    def __init__(self, columns=None, rows=None, chunks=None, describe_error=None):
        self._columns = columns or []
        self._rows = rows if rows is not None else pd.DataFrame()
        self._chunks = chunks
        self._describe_error = describe_error
        self.raw_sql_calls = 0

    def describe_table(self, library, table):
        if self._describe_error is not None:
            raise self._describe_error
        return pd.DataFrame({"name": self._columns})

    def raw_sql(self, sql, return_iter=False):
        self.raw_sql_calls += 1
        if return_iter:
            return iter(self._chunks or [])
        return self._rows


# ---------------------------------------------------------------------------
# pull_table: column-matching / required-columns / status logic
# ---------------------------------------------------------------------------


def test_pull_table_all_desired_columns_present(tmp_path):
    db = StubDB(
        columns=["permno", "date", "ret"],
        rows=pd.DataFrame({"permno": [1, 2], "date": ["2000-01-01", "2000-01-02"], "ret": [0.1, -0.2]}),
    )
    log = []
    manifest_path = tmp_path / "manifest.csv"

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno", "date", "ret"],
        raw_dir=tmp_path / "raw", manifest_path=manifest_path,
    )

    assert log[0]["status"] == "ok"
    assert log[0]["rows"] == 2
    assert log[0]["missing_columns"] == ""
    assert (tmp_path / "raw" / "crsp" / "dsf.parquet").exists()
    assert manifest_path.exists()
    manifest = pd.read_csv(manifest_path)
    assert list(manifest["status"]) == ["ok"]
    assert "pulled_at" in manifest.columns
    assert db.raw_sql_calls == 1


def test_pull_table_some_optional_columns_missing(tmp_path):
    db = StubDB(
        columns=["permno", "date"],  # "ret" missing
        rows=pd.DataFrame({"permno": [1], "date": ["2000-01-01"]}),
    )
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno", "date", "ret"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "ok"
    assert log[0]["missing_columns"] == "ret"
    assert db.raw_sql_calls == 1


def test_pull_table_missing_required_column_does_not_query(tmp_path):
    db = StubDB(
        columns=["permno"],  # "namedt"/"nameenddt" missing
        rows=pd.DataFrame({"permno": [1]}),
    )
    log = []

    pwd.pull_table(
        db, log, "crsp", "stocknames",
        ["permno", "namedt", "nameenddt"],
        required_columns=["namedt", "nameenddt"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "missing_required_columns"
    assert "namedt" in log[0]["missing_columns"]
    assert "nameenddt" in log[0]["missing_columns"]
    # The whole point: a required-column gap must not silently run the query.
    assert db.raw_sql_calls == 0
    assert not (tmp_path / "raw" / "crsp" / "stocknames.parquet").exists()


def test_pull_table_unknown_table_logs_no_such_table(tmp_path):
    db = StubDB(describe_error=sa_exc.NoSuchTableError("crsp.not_a_real_table"))
    log = []

    pwd.pull_table(
        db, log, "crsp", "not_a_real_table", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "no_such_table"
    assert db.raw_sql_calls == 0


def test_pull_table_describe_table_error_is_not_misclassified_as_no_such_table(tmp_path):
    # A connection/timeout/permission error is NOT "table doesn't exist" and
    # must be logged distinctly, per the finding this guards against.
    db = StubDB(describe_error=RuntimeError("connection reset by peer"))
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "describe_table_error"
    assert log[0]["status"] != "no_such_table"


def test_pull_table_no_matching_columns(tmp_path):
    db = StubDB(columns=["unrelated_col"], rows=pd.DataFrame())
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "no_matching_columns"
    assert db.raw_sql_calls == 0


def test_pull_table_skips_when_out_path_already_exists(tmp_path):
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    (out_dir / "dsf.parquet").write_bytes(b"not really parquet, just a marker")

    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "skipped_exists"
    assert db.raw_sql_calls == 0


def test_pull_table_force_reruns_even_if_out_path_exists(tmp_path):
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    (out_dir / "dsf.parquet").write_bytes(b"stale")

    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        force=True,
    )

    assert log[0]["status"] == "ok"
    assert db.raw_sql_calls == 1


def test_pull_table_streaming_writes_chunks_and_counts_rows(tmp_path):
    chunks = [
        pd.DataFrame({"permno": [1, 2], "date": ["2000-01-01", "2000-01-02"]}),
        pd.DataFrame({"permno": [3], "date": ["2000-01-03"]}),
    ]
    db = StubDB(columns=["permno", "date"], chunks=chunks)
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        stream=True,
    )

    assert log[0]["status"] == "ok"
    assert log[0]["rows"] == 3
    out_path = tmp_path / "raw" / "crsp" / "dsf.parquet"
    assert out_path.exists()
    result = pd.read_parquet(out_path)
    assert len(result) == 3


def test_pull_table_manifest_rows_include_provenance_columns(tmp_path):
    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []
    manifest_path = tmp_path / "manifest.csv"

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=manifest_path,
        start_date="1995-01-01", end_date="2014-06-30",
    )

    manifest = pd.read_csv(manifest_path)
    row = manifest.iloc[0]
    assert row["start_date"] == "1995-01-01"
    assert row["end_date"] == "2014-06-30"
    assert isinstance(row["pulled_at"], str) and len(row["pulled_at"]) > 0


def test_pull_table_manifest_appends_across_multiple_calls(tmp_path):
    manifest_path = tmp_path / "manifest.csv"
    log = []

    db1 = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    pwd.pull_table(
        db1, log, "crsp", "dsf", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=manifest_path,
    )
    db2 = StubDB(describe_error=sa_exc.NoSuchTableError("x"))
    pwd.pull_table(
        db2, log, "wrdsapps", "iclink", ["ticker"],
        raw_dir=tmp_path / "raw", manifest_path=manifest_path,
    )

    manifest = pd.read_csv(manifest_path)
    assert len(manifest) == 2
    assert list(manifest["status"]) == ["ok", "no_such_table"]
