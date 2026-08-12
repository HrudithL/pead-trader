"""Unit tests for scripts/wrds_pull_lib.py -- the shared library extracted
from the original monolithic pull_wrds_data.py (now split into
pull_wrds_reference.py/pull_wrds_crsp.py/pull_wrds_compustat.py/
pull_wrds_ibes.py -- see memory/phase1-equity-pead-plan.md's "Split Into
Parallel Scripts" section). Covers only the parts that don't need a real
WRDS connection: load_env_file, pull_table's required/optional column
handling and idempotency logic against a stub `db` object, the schema-
pinned streaming writer, manifest error sanitization, resolve_universe's
full resolution logic, the IN (...) clause builders, and the NEW helpers
this split added (parse_common_args, load_resolved_filter_lists,
require_resolved_universe_file).

Every test in this file was moved here UNCHANGED in behavior from the
original tests/test_pull_wrds_data.py (mechanical `pwd` -> `lib` rename
only, since the functions under test moved verbatim into wrds_pull_lib.py)
-- see that PR's description for the diff proving no behavioral drift in
the extracted logic. main()-level tests (which exercised the old single
pull_wrds_data.main() end to end) moved to tests/test_pull_wrds_reference.py,
tests/test_pull_wrds_crsp.py, tests/test_pull_wrds_compustat.py, and
tests/test_pull_wrds_ibes.py instead, split by which new script's main()
now owns that behavior.
"""

import re
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import sqlalchemy.exc as sa_exc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import wrds_pull_lib as lib  # noqa: E402




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

    lib.load_env_file(env_path)

    assert __import__("os").environ["WRDS_USERNAME"] == "plainvalue"
    assert __import__("os").environ["WRDS_PASSWORD"] == "quoted value"
    assert __import__("os").environ["SINGLE_QUOTED"] == "single quoted"
    # A literal '#' inside a value must NOT be treated as a mid-line comment
    # and truncate the value.
    assert __import__("os").environ["WITH_HASH"] == "sekret#123"


def test_load_env_file_missing_file_is_a_noop(tmp_path):
    # Should not raise even if the .env file doesn't exist.
    lib.load_env_file(tmp_path / "does_not_exist.env")


def test_load_env_file_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("SOME_KEY=from_file\n")
    monkeypatch.setenv("SOME_KEY", "from_process_env")

    lib.load_env_file(env_path)

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
        self.raw_sql_sqls = []

    def describe_table(self, library, table):
        if self._describe_error is not None:
            raise self._describe_error
        # Real db.describe_table() always returns a "type" column too (the
        # database's declared SQL type per column) -- pull_table now reads
        # it (see _column_sql_types) to build the streaming path's pinned
        # pyarrow schema. Defaulting every column here to "VARCHAR" is fine
        # for tests that don't care about the pinned schema's exact dtypes
        # (status/rows/SQL-text assertions only); tests that DO care about
        # schema pinning call _stream_query_to_parquet directly with an
        # explicit pinned_schema instead of going through this stub.
        return pd.DataFrame({
            "name": self._columns,
            "type": ["VARCHAR"] * len(self._columns),
        })

    def raw_sql(self, sql, return_iter=False):
        self.raw_sql_calls += 1
        self.raw_sql_sqls.append(sql)
        if return_iter:
            return iter(self._chunks or [])
        return self._rows


# ---------------------------------------------------------------------------
# pull_table: required-column matching / idempotency logic
# ---------------------------------------------------------------------------


def test_pull_table_all_columns_present(tmp_path):
    db = StubDB(
        columns=["permno", "date", "ret"],
        rows=pd.DataFrame({"permno": [1, 2], "date": ["2000-01-01", "2000-01-02"], "ret": [0.1, -0.2]}),
    )
    log = []
    manifest_path = tmp_path / "manifest.csv"

    lib.pull_table(
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
    assert "optional_columns_missing" in manifest.columns
    assert db.raw_sql_calls == 1


def test_pull_table_missing_required_column_blocks_pull(tmp_path):
    db = StubDB(
        columns=["permno", "date"],  # "ret" missing
        rows=pd.DataFrame({"permno": [1], "date": ["2000-01-01"]}),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date", "ret"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "missing_required_columns"
    assert log[0]["missing_columns"] == "ret"
    assert db.raw_sql_calls == 0
    assert not (tmp_path / "raw" / "crsp" / "dsf.parquet").exists()


def test_pull_table_missing_column_does_not_query(tmp_path):
    db = StubDB(
        columns=["permno"],  # "namedt"/"nameenddt" missing
        rows=pd.DataFrame({"permno": [1]}),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "stocknames",
        ["permno", "namedt", "nameenddt"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "missing_required_columns"
    assert "namedt" in log[0]["missing_columns"]
    assert "nameenddt" in log[0]["missing_columns"]
    # The whole point: a missing-column gap must not silently run the query.
    assert db.raw_sql_calls == 0
    assert not (tmp_path / "raw" / "crsp" / "stocknames.parquet").exists()


def test_pull_table_unknown_table_logs_no_such_table(tmp_path):
    db = StubDB(describe_error=sa_exc.NoSuchTableError("crsp.not_a_real_table"))
    log = []

    lib.pull_table(
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

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "describe_table_error"
    assert log[0]["status"] != "no_such_table"


def test_pull_table_all_required_columns_missing_blocks_pull(tmp_path):
    db = StubDB(columns=["unrelated_col"], rows=pd.DataFrame())
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "missing_required_columns"
    assert db.raw_sql_calls == 0


def test_pull_table_skips_when_out_path_already_exists(tmp_path):
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    (out_dir / "dsf.parquet").write_bytes(b"not really parquet, just a marker")

    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    lib.pull_table(
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

    lib.pull_table(
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

    lib.pull_table(
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

    lib.pull_table(
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
    lib.pull_table(
        db1, log, "crsp", "dsf", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=manifest_path,
    )
    db2 = StubDB(describe_error=sa_exc.NoSuchTableError("x"))
    lib.pull_table(
        db2, log, "wrdsapps", "iclink", ["ticker"],
        raw_dir=tmp_path / "raw", manifest_path=manifest_path,
    )

    manifest = pd.read_csv(manifest_path)
    assert len(manifest) == 2
    assert list(manifest["status"]) == ["ok", "no_such_table"]


# ---------------------------------------------------------------------------
# Fix 2 (this round): required_columns / optional_columns split.
#
# required_columns missing -> abort (status "missing_required_columns"), same
# as before. optional_columns missing -> dropped from the query and recorded
# in "optional_columns_missing", but the pull still succeeds.
# ---------------------------------------------------------------------------


def test_pull_table_missing_optional_column_does_not_block_pull(tmp_path):
    """The core Fix 2 behavior: a table with a required subset and an
    optional subset must still succeed when an optional column is absent
    from the real schema -- the previous round's design (every column in
    `columns` mandatory) would have aborted this with
    "missing_required_columns"; this must now succeed with status "ok" and
    the missing optional column noted, not silently omitted from the
    manifest.
    """
    db = StubDB(
        columns=["permno", "date", "ret"],  # "numtrd" (optional) absent
        rows=pd.DataFrame({"permno": [1, 2], "date": ["2000-01-01", "2000-01-02"], "ret": [0.1, -0.2]}),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf",
        required_columns=["permno", "date", "ret"],
        optional_columns=["numtrd"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "ok"
    assert log[0]["optional_columns_missing"] == "numtrd"
    assert db.raw_sql_calls == 1
    assert (tmp_path / "raw" / "crsp" / "dsf.parquet").exists()
    # The dropped optional column must not appear in the actual query.
    assert "numtrd" not in db.raw_sql_sqls[0]


def test_pull_table_present_optional_column_is_included_in_query(tmp_path):
    db = StubDB(
        columns=["permno", "date", "ret", "numtrd"],
        rows=pd.DataFrame({
            "permno": [1], "date": ["2000-01-01"], "ret": [0.1], "numtrd": [42],
        }),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf",
        required_columns=["permno", "date", "ret"],
        optional_columns=["numtrd"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "ok"
    assert log[0]["optional_columns_missing"] == ""
    assert "numtrd" in db.raw_sql_sqls[0]


def test_pull_table_missing_multiple_optional_columns_all_noted(tmp_path):
    db = StubDB(
        columns=["ticker", "cusip"],  # cname/oftic/sdates (all optional) absent
        rows=pd.DataFrame({"ticker": ["ABC"], "cusip": ["12345678"]}),
    )
    log = []

    lib.pull_table(
        db, log, "ibes", "idsum",
        required_columns=["ticker", "cusip"],
        optional_columns=["cname", "oftic", "sdates"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "ok"
    missing = set(log[0]["optional_columns_missing"].split(","))
    assert missing == {"cname", "oftic", "sdates"}


def test_pull_table_required_column_missing_still_aborts_even_with_optional_present(tmp_path):
    db = StubDB(
        columns=["permno", "numtrd"],  # date/ret (required) missing; numtrd (optional) present
        rows=pd.DataFrame({"permno": [1], "numtrd": [1]}),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf",
        required_columns=["permno", "date", "ret"],
        optional_columns=["numtrd"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "missing_required_columns"
    assert db.raw_sql_calls == 0


# ---------------------------------------------------------------------------
# Fix 1 (this round): the zero-row / kept-existing guard must gate on the
# ACTUAL ROW COUNT, in both the streaming and non-streaming paths -- not on
# "were there any chunks" / "did the query return an iterator".
#
# Verified against the real contract (pandas.io.sql._query_iterator, read
# directly from the installed pandas==2.2.3 source): a zero-row query
# yields exactly ONE empty chunk, never zero chunks. A stub using
# `chunks=[]` (the previous round's test) does NOT match this real contract
# and would pass even against badly-broken production code that checked
# "were there any chunks" -- these tests use `chunks=[<one empty df>]`,
# matching the verified real behavior, so they actually exercise the bug
# this fix closes.
# ---------------------------------------------------------------------------


def test_pull_table_streaming_zero_rows_with_existing_file_logs_kept_existing_and_path(tmp_path):
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    out_path = out_dir / "dsf.parquet"
    original_bytes = b"STALE-BUT-STILL-GOOD-PARQUET-BYTES"
    out_path.write_bytes(original_bytes)

    # One empty chunk -- the REAL contract for a zero-row streamed query,
    # not an empty iterator.
    empty_chunk = pd.DataFrame({"permno": pd.Series([], dtype="int64"),
                                 "date": pd.Series([], dtype="object")})
    db = StubDB(columns=["permno", "date"], chunks=[empty_chunk])
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        stream=True, force=True,
    )

    assert log[0]["status"] == "ok_empty_query_kept_existing"
    assert log[0]["rows"] == 0
    # Fix 1 also requires the path be logged (previously logged empty even
    # though a real file exists at that location).
    assert log[0]["path"] == "data/raw_wrds/crsp/dsf.parquet" or log[0]["path"] != ""
    # The file must be byte-identical before and after -- left completely
    # untouched, not overwritten/emptied/rewritten.
    assert out_path.read_bytes() == original_bytes


def test_pull_table_streaming_zero_rows_without_existing_file_logs_plain_ok(tmp_path):
    empty_chunk = pd.DataFrame({"permno": pd.Series([], dtype="int64"),
                                 "date": pd.Series([], dtype="object")})
    db = StubDB(columns=["permno", "date"], chunks=[empty_chunk])
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        stream=True,
    )

    assert log[0]["status"] == "ok"
    assert log[0]["rows"] == 0
    assert not (tmp_path / "raw" / "crsp" / "dsf.parquet").exists()


def test_pull_table_non_streaming_zero_rows_with_existing_file_logs_kept_existing_and_path(tmp_path):
    """The non-streaming path (7 of main()'s 13 tables) had NO zero-row
    protection at all before this round -- a --force re-pull whose query
    legitimately returned zero rows would silently overwrite a
    previously-good out_path with an empty Parquet file. This test would
    have failed against the pre-round code (which unconditionally called
    df.to_parquet(...) regardless of len(df)).
    """
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    out_path = out_dir / "stocknames.parquet"
    original_bytes = b"STALE-BUT-STILL-GOOD-NON-STREAMING-PARQUET-BYTES"
    out_path.write_bytes(original_bytes)

    db = StubDB(
        columns=["permno"],
        rows=pd.DataFrame({"permno": pd.Series([], dtype="int64")}),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "stocknames", ["permno"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        force=True,
    )

    assert log[0]["status"] == "ok_empty_query_kept_existing"
    assert log[0]["rows"] == 0
    assert log[0]["path"] != ""
    assert out_path.read_bytes() == original_bytes


def test_pull_table_non_streaming_zero_rows_without_existing_file_logs_plain_ok(tmp_path):
    db = StubDB(
        columns=["permno"],
        rows=pd.DataFrame({"permno": pd.Series([], dtype="int64")}),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "stocknames", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
    )

    assert log[0]["status"] == "ok"
    assert log[0]["rows"] == 0
    assert not (tmp_path / "raw" / "crsp" / "stocknames.parquet").exists()


# ---------------------------------------------------------------------------
# Fix 3, round 2 (this round): schema pinning for the streaming writer, built
# from db.describe_table()'s DECLARED SQL types rather than inferred from
# chunk data. Replaces round 5's lookahead-buffer approach, which was found
# not to work against the real wrds/pandas driver contract (see below).
# ---------------------------------------------------------------------------


def test_sql_type_to_pyarrow_type_maps_common_postgres_types():
    """Base mapping sanity check, covering the vocabulary WRDS's Postgres-
    backed schema actually uses (verified directly against sqlalchemy's own
    type objects -- e.g. str(sqlalchemy.dialects.postgresql.INTEGER()) ==
    "INTEGER", str(sqlalchemy.dialects.postgresql.DOUBLE_PRECISION()) ==
    "DOUBLE PRECISION" -- not guessed blind)."""
    # Integers.
    assert lib._sql_type_to_pyarrow_type("INTEGER") == pa.int64()
    assert lib._sql_type_to_pyarrow_type("BIGINT") == pa.int64()
    assert lib._sql_type_to_pyarrow_type("SMALLINT") == pa.int64()
    assert lib._sql_type_to_pyarrow_type("integer") == pa.int64()  # case-insensitive

    # Floating/numeric.
    assert lib._sql_type_to_pyarrow_type("DOUBLE PRECISION") == pa.float64()
    assert lib._sql_type_to_pyarrow_type("REAL") == pa.float64()
    assert lib._sql_type_to_pyarrow_type("NUMERIC") == pa.float64()
    assert lib._sql_type_to_pyarrow_type("NUMERIC(10, 2)") == pa.float64()  # precision stripped
    assert lib._sql_type_to_pyarrow_type("DECIMAL") == pa.float64()

    # Date/time -- deliberately still pa.string(), matching this script's
    # existing (out-of-scope-to-change) behavior of writing dates as ISO
    # strings.
    assert lib._sql_type_to_pyarrow_type("DATE") == pa.string()
    assert lib._sql_type_to_pyarrow_type("TIMESTAMP") == pa.string()
    assert lib._sql_type_to_pyarrow_type("TIMESTAMP WITHOUT TIME ZONE") == pa.string()

    # Text.
    assert lib._sql_type_to_pyarrow_type("VARCHAR") == pa.string()
    assert lib._sql_type_to_pyarrow_type("VARCHAR(32)") == pa.string()
    assert lib._sql_type_to_pyarrow_type("CHARACTER VARYING") == pa.string()
    assert lib._sql_type_to_pyarrow_type("TEXT") == pa.string()
    assert lib._sql_type_to_pyarrow_type("CHAR(3)") == pa.string()

    # Boolean.
    assert lib._sql_type_to_pyarrow_type("BOOLEAN") == pa.bool_()

    # Unmapped -> sane fallback, logged (not silently mishandled).
    assert lib._sql_type_to_pyarrow_type("SOME_EXOTIC_TYPE") == pa.string()
    assert lib._sql_type_to_pyarrow_type(None) == pa.string()


def test_sql_type_to_pyarrow_type_warns_on_unmapped_type(capsys):
    lib._sql_type_to_pyarrow_type("SOME_EXOTIC_TYPE", column="widget")
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "SOME_EXOTIC_TYPE" in captured.out
    assert "widget" in captured.out


def test_build_pinned_schema_uses_declared_types_for_exactly_the_given_columns():
    column_sql_types = {
        "permno": "INTEGER", "numtrd": "INTEGER", "date": "DATE", "ret": "DOUBLE PRECISION",
    }
    schema = lib._build_pinned_schema(["permno", "date", "ret"], column_sql_types)
    assert schema.names == ["permno", "date", "ret"]
    assert schema.field("permno").type == pa.int64()
    assert schema.field("date").type == pa.string()
    assert schema.field("ret").type == pa.float64()
    # numtrd wasn't requested -- must not appear even though it's a known type.
    assert "numtrd" not in schema.names


def test_build_pinned_schema_is_independent_of_any_data():
    """The whole point of this round's fix: the pinned schema is a pure
    function of (columns, declared SQL types) -- it does not take chunks,
    data, or row values as input at all, unlike round 5's
    _merge_schema_resolving_nulls, which took a running schema AND a new
    chunk's inferred schema. Calling it twice with the same declared types
    must produce identical schemas regardless of anything about data."""
    column_sql_types = {"permno": "INTEGER", "ret": "DOUBLE PRECISION"}
    schema_1 = lib._build_pinned_schema(["permno", "ret"], column_sql_types)
    schema_2 = lib._build_pinned_schema(["permno", "ret"], column_sql_types)
    assert schema_1.equals(schema_2)


def _real_driver_chunk(rows, columns):
    """Build a chunk exactly the way wrds.Connection.raw_sql's real
    dtype_backend="numpy_nullable" path does -- by calling pandas' OWN
    internal conversion function directly
    (pandas.io.sql._convert_arrays_to_dataframe, the function
    wrds.raw_sql's pd.read_sql_query(...) call uses under the hood for every
    chunk) -- rather than a plain pd.DataFrame(...) construction (numpy-
    backend), which behaves differently and is what let round 5's bug slip
    through untested.

    `rows` is a list of row tuples (DB-API style), `columns` the column
    names, matching _convert_arrays_to_dataframe's own signature.
    """
    from pandas.io import sql as pdsql

    return pdsql._convert_arrays_to_dataframe(
        rows, columns, coerce_float=True, dtype_backend="numpy_nullable"
    )


def test_real_driver_all_null_numeric_chunk_resolves_to_string_not_null():
    """Reproduces, directly against the real driver conversion path (not a
    stub), the exact fact that defeated round 5's schema-resolution logic:
    a chunk that's entirely NULL for a numeric column does NOT come back as
    pa.null() -- it comes back as pandas StringDtype, and pyarrow reports a
    concrete `string` field for it, not `null`. Round 5's "still unresolved"
    check (`any(pa.types.is_null(f.type) for f in resolved_schema)`) would
    never fire here, so its buffer would accept `numtrd: string` as final
    after this one chunk -- exactly the bug this round's fix eliminates by
    not inferring the schema from data at all.
    """
    chunk = _real_driver_chunk([(1, None), (2, None)], ["permno", "numtrd"])
    assert str(chunk["numtrd"].dtype) == "string"  # NOT pa.null()-shaped
    arrow_schema = pa.Table.from_pandas(chunk, preserve_index=False).schema
    assert arrow_schema.field("numtrd").type == pa.string()
    assert not pa.types.is_null(arrow_schema.field("numtrd").type)


def test_round5_inference_approach_would_have_coerced_real_ints_to_strings():
    """Literal reproduction of round 5's bug: round 5's merge/buffer logic,
    run against REAL driver chunks (via _real_driver_chunk, not a numpy-
    backend stub), pins `numtrd` as `string` from an all-NULL first chunk
    and then silently downcasts a later chunk's real integer values (100,
    200) into the strings "100"/"200". Round 5's own functions were removed
    from the module this round, so its buffering/merge behavior is
    reproduced inline here (a faithful copy of the logic that shipped in
    round 5) purely to prove -- against real driver output, not assumption
    -- that this is what it actually did.
    """

    def widen_integers_to_float64(schema):
        fields = []
        for f in schema:
            if pa.types.is_integer(f.type):
                fields.append(pa.field(f.name, pa.float64(), nullable=True))
            else:
                fields.append(f)
        return pa.schema(fields)

    def merge_schema_resolving_nulls(resolved, new_schema):
        if resolved is None:
            return widen_integers_to_float64(new_schema)
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

    chunk1 = _real_driver_chunk([(1, None), (2, None)], ["permno", "numtrd"])
    chunk2 = _real_driver_chunk([(3, 100), (4, 200)], ["permno", "numtrd"])
    table1 = pa.Table.from_pandas(chunk1, preserve_index=False)
    table2 = pa.Table.from_pandas(chunk2, preserve_index=False)

    resolved = merge_schema_resolving_nulls(None, table1.schema)
    still_unresolved = any(pa.types.is_null(f.type) for f in resolved)
    # Round 5's buffer-continue condition never fires -- it thinks the
    # schema is already fully resolved after just the all-NULL chunk.
    assert not still_unresolved
    assert resolved.field("numtrd").type == pa.string()

    # Round 5 would now cast chunk2's REAL integer values down into that
    # pinned string field -- silently, with no error.
    casted = table2.cast(resolved, safe=False)
    assert casted.column("numtrd").to_pylist() == ["100", "200"]  # the bug


def test_stream_query_to_parquet_pinned_schema_produces_numeric_output_despite_leading_all_null_chunk(tmp_path):
    """Fix, proven end to end: with the schema pinned from DECLARED types
    (not inferred from data), the exact scenario that broke round 5 --an
    all-NULL numeric chunk followed by a chunk with real integer values --
    now produces a genuinely numeric output column, using REAL driver chunks
    (via _real_driver_chunk), not a numpy-backend stub.
    """
    chunk1 = _real_driver_chunk([(1, None), (2, None)], ["permno", "numtrd"])
    chunk2 = _real_driver_chunk([(3, 100), (4, 200)], ["permno", "numtrd"])

    class FakeDB:
        def raw_sql(self, sql, return_iter=False):
            assert return_iter
            return iter([chunk1, chunk2])

    out_path = tmp_path / "dsf.parquet"
    pinned_schema = lib._build_pinned_schema(
        ["permno", "numtrd"], {"permno": "INTEGER", "numtrd": "INTEGER"}
    )

    total_rows, wrote_any = lib._stream_query_to_parquet(
        FakeDB(), "crsp", "dsf", ["permno", "numtrd"], "", out_path, pinned_schema
    )

    assert wrote_any
    assert total_rows == 4

    result = pd.read_parquet(out_path)
    assert len(result) == 4
    assert pa.types.is_integer(pq.read_schema(out_path).field("numtrd").type)
    assert result["numtrd"].isna().sum() == 2
    typed = result.loc[~result["numtrd"].isna(), "numtrd"].astype(int).tolist()
    assert typed == [100, 200]  # real ints, NOT "100"/"200" strings


def test_stream_query_to_parquet_pinned_schema_identical_regardless_of_chunk_order_or_composition(tmp_path):
    """The pinned schema no longer depends on data AT ALL -- unlike round
    5's approach (schema built by merging each chunk's own inferred
    schema), it comes entirely from declared SQL types. Prove two runs with
    completely different chunk order/composition (one all-NULL-then-real,
    one real-then-all-NULL) produce byte-identical pinned schemas, using
    real driver chunks.
    """
    chunk_null = _real_driver_chunk([(1, None), (2, None)], ["permno", "numtrd"])
    chunk_real = _real_driver_chunk([(3, 100), (4, 200)], ["permno", "numtrd"])

    class FakeDBOrderA:
        def raw_sql(self, sql, return_iter=False):
            return iter([chunk_null, chunk_real])

    class FakeDBOrderB:
        def raw_sql(self, sql, return_iter=False):
            return iter([chunk_real, chunk_null])

    column_sql_types = {"permno": "INTEGER", "numtrd": "INTEGER"}
    pinned_schema_a = lib._build_pinned_schema(["permno", "numtrd"], column_sql_types)
    pinned_schema_b = lib._build_pinned_schema(["permno", "numtrd"], column_sql_types)
    assert pinned_schema_a.equals(pinned_schema_b), (
        "pinned schema must be identical regardless of data -- it's built "
        "purely from declared types before any chunk is seen"
    )

    out_a = tmp_path / "order_a.parquet"
    out_b = tmp_path / "order_b.parquet"
    rows_a, wrote_a = lib._stream_query_to_parquet(
        FakeDBOrderA(), "crsp", "dsf", ["permno", "numtrd"], "", out_a, pinned_schema_a
    )
    rows_b, wrote_b = lib._stream_query_to_parquet(
        FakeDBOrderB(), "crsp", "dsf", ["permno", "numtrd"], "", out_b, pinned_schema_b
    )

    assert wrote_a and wrote_b
    assert rows_a == rows_b == 4
    schema_a = pq.read_schema(out_a)
    schema_b = pq.read_schema(out_b)
    assert schema_a.equals(schema_b)
    assert pa.types.is_integer(schema_a.field("numtrd").type)


def test_stream_query_to_parquet_signature_takes_query_pieces_and_pinned_schema(tmp_path):
    """_stream_query_to_parquet builds its own SQL from
    (library, table, columns, where_sql), and takes the pinned schema as an
    explicit parameter built by the CALLER before any data is fetched --
    not something it infers or buffers internally."""
    chunk = pd.DataFrame({"permno": [1], "date": ["2000-01-01"]})

    class FakeDB:
        def raw_sql(self, sql, return_iter=False):
            assert return_iter
            assert "permno" in sql and "date" in sql
            assert "crsp.dsf" in sql
            return iter([chunk])

    out_path = tmp_path / "dsf.parquet"
    pinned_schema = lib._build_pinned_schema(
        ["permno", "date"], {"permno": "INTEGER", "date": "DATE"}
    )
    total_rows, wrote_any = lib._stream_query_to_parquet(
        FakeDB(), "crsp", "dsf", ["permno", "date"], "", out_path, pinned_schema
    )
    assert wrote_any
    assert total_rows == 1
    assert pq.read_schema(out_path).equals(pinned_schema)


def test_stream_query_to_parquet_writer_is_used_incrementally_not_accumulated(tmp_path, monkeypatch):
    """Peak memory during the write must be bounded by one chunk at a time,
    not the whole table. Asserted structurally: confirm
    ParquetWriter.write_table is called incrementally, once per chunk,
    rather than collecting every chunk into a Python list and writing once
    at the very end via a single pq.write_table(combined) call.
    """
    write_table_call_row_counts = []
    original_write_table = pq.ParquetWriter.write_table

    def _tracking_write_table(self, table, **kwargs):
        write_table_call_row_counts.append(len(table))
        return original_write_table(self, table, **kwargs)

    monkeypatch.setattr(pq.ParquetWriter, "write_table", _tracking_write_table)

    chunks = [
        pd.DataFrame({"permno": [1, 2], "date": ["2000-01-01", "2000-01-02"]}),
        pd.DataFrame({"permno": [3, 4], "date": ["2000-01-03", "2000-01-04"]}),
        pd.DataFrame({"permno": [5, 6], "date": ["2000-01-05", "2000-01-06"]}),
        pd.DataFrame({"permno": [7, 8], "date": ["2000-01-07", "2000-01-08"]}),
    ]

    class FakeDB:
        def raw_sql(self, sql, return_iter=False):
            return iter(chunks)

    out_path = tmp_path / "dsf.parquet"
    pinned_schema = lib._build_pinned_schema(
        ["permno", "date"], {"permno": "INTEGER", "date": "DATE"}
    )
    total_rows, wrote_any = lib._stream_query_to_parquet(
        FakeDB(), "crsp", "dsf", ["permno", "date"], "", out_path, pinned_schema
    )

    assert wrote_any
    assert total_rows == 8
    # One write_table call per chunk proves chunks were written
    # incrementally rather than concatenated into one giant table and
    # written in a single call at the end.
    assert len(write_table_call_row_counts) == len(chunks)
    # No single write_table call ever saw the whole table's worth of rows.
    assert all(c < total_rows for c in write_table_call_row_counts)

    # Confirm none of round 5's whole-table-accumulation/lookahead-buffer
    # primitives are used anywhere in this function's source anymore.
    import inspect
    source = inspect.getsource(lib._stream_query_to_parquet)
    _docstring_parts = source.split('"""')
    code_only = _docstring_parts[0] + "".join(_docstring_parts[2:])
    assert "concat_tables(" not in code_only
    assert "replace_schema_metadata(" not in code_only
    assert "pending" not in code_only
    assert not hasattr(lib, "_widen_integers_to_float64")
    assert not hasattr(lib, "_merge_schema_resolving_nulls")
    assert not hasattr(lib, "_SCHEMA_RESOLUTION_CHUNK_CAP")


def test_stream_query_to_parquet_cleans_up_tmp_file_on_keyboard_interrupt(tmp_path, monkeypatch):
    chunk = pd.DataFrame({"permno": [1], "date": ["2000-01-01"]})

    class FakeDB:
        def raw_sql(self, sql, return_iter=False):
            return iter([chunk])

    out_path = tmp_path / "dsf.parquet"
    tmp_marker_path = out_path.with_suffix(out_path.suffix + ".tmp")

    def _raise_on_init(where, schema, **kwargs):
        Path(where).write_bytes(b"PARTIAL")
        raise KeyboardInterrupt()

    monkeypatch.setattr(pq, "ParquetWriter", _raise_on_init)

    pinned_schema = lib._build_pinned_schema(
        ["permno", "date"], {"permno": "INTEGER", "date": "DATE"}
    )
    with pytest.raises(KeyboardInterrupt):
        lib._stream_query_to_parquet(
            FakeDB(), "crsp", "dsf", ["permno", "date"], "", out_path, pinned_schema
        )

    assert not tmp_marker_path.exists()
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# Fix 4 (this round): manifest error-text sanitization.
# ---------------------------------------------------------------------------


def test_sanitize_error_text_truncates_long_text():
    long_text = "x" * 1000
    result = lib._sanitize_error_text(long_text)
    assert len(result) == lib._MAX_ERROR_LEN


def test_sanitize_error_text_redacts_wrds_username(monkeypatch):
    monkeypatch.setenv("WRDS_USERNAME", "jdoe123")
    text = 'FATAL: password authentication failed for user "jdoe123" at host db.wrds.upenn.edu'
    result = lib._sanitize_error_text(text)
    assert "jdoe123" not in result
    assert "[REDACTED_WRDS_USERNAME]" in result


def test_sanitize_error_text_noop_without_username_set(monkeypatch):
    monkeypatch.delenv("WRDS_USERNAME", raising=False)
    text = "some ordinary error message"
    assert lib._sanitize_error_text(text) == text


def test_sanitize_error_text_handles_empty_string():
    assert lib._sanitize_error_text("") == ""
    assert lib._sanitize_error_text(None) is None


def test_append_manifest_row_sanitizes_error_field_before_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("WRDS_USERNAME", "secretuser99")
    manifest_path = tmp_path / "manifest.csv"

    lib.append_manifest_row(
        {
            "library": "crsp", "table": "dsf", "status": "query_failed",
            "rows": 0, "path": "", "missing_columns": "",
            "optional_columns_missing": "",
            "error": 'connection failed for user "secretuser99": ' + ("z" * 500),
        },
        manifest_path=manifest_path,
    )

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    error_text = manifest.iloc[0]["error"]
    assert "secretuser99" not in error_text
    assert "[REDACTED_WRDS_USERNAME]" in error_text
    assert len(error_text) <= lib._MAX_ERROR_LEN


def test_pull_table_query_failed_error_is_sanitized_in_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("WRDS_USERNAME", "myuser42")

    class FakeDB:
        def describe_table(self, library, table):
            return pd.DataFrame({"name": ["permno"], "type": ["INTEGER"]})

        def raw_sql(self, sql, return_iter=False):
            raise RuntimeError('auth failed for user "myuser42" host=db.example.com')

    log = []
    manifest_path = tmp_path / "manifest.csv"

    lib.pull_table(
        FakeDB(), log, "crsp", "dsf", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=manifest_path,
    )

    assert log[0]["status"] == "query_failed"
    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    error_text = manifest.iloc[0]["error"]
    assert "myuser42" not in error_text
    assert "[REDACTED_WRDS_USERNAME]" in error_text
    # The in-memory log (console-only, never committed) is deliberately NOT
    # sanitized -- only what actually reaches the git-tracked file is.
    assert "myuser42" in log[0]["error"]




def test_parse_args_accepts_force_flag():
    assert lib.parse_common_args([]).force is False
    assert lib.parse_common_args(["--force"]).force is True


def test_parse_args_rejects_unrecognized_flag():
    # A typo like --Force or -force must error out loudly (argparse exits
    # with SystemExit) rather than being silently ignored -- the old
    # `"--force" in sys.argv` check would have just proceeded as if --force
    # hadn't been passed, skipping every already-pulled table and exiting
    # looking successful.
    with pytest.raises(SystemExit):
        lib.parse_common_args(["--Force"])
    with pytest.raises(SystemExit):
        lib.parse_common_args(["-force"])




# ---------------------------------------------------------------------------
# Atomic writes -- a failed (re-)write must never destroy or truncate a
# previously-good out_path, whether the failure is a normal Exception or a
# KeyboardInterrupt.
# ---------------------------------------------------------------------------


def test_pull_table_force_failure_preserves_original_file_non_streaming(
    tmp_path, monkeypatch
):
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    out_path = out_dir / "dsf.parquet"
    original_content = b"GOOD-ORIGINAL-PARQUET-BYTES"
    out_path.write_bytes(original_content)

    def _raise_to_parquet(self, path, *a, **k):
        # Simulate a REAL partial write (some bytes actually land on disk
        # before the failure), not just an exception with no I/O at all --
        # otherwise this test can't tell an atomic (write-to-temp-then-
        # replace) implementation apart from one that writes directly to
        # out_path, since either would "pass" if the mock never touches the
        # filesystem.
        Path(path).write_bytes(b"CORRUPTED-PARTIAL-WRITE")
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _raise_to_parquet)

    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        force=True,
    )

    assert log[0]["status"] == "query_failed"
    assert out_path.read_bytes() == original_content, (
        "a failed forced re-write must leave the previously-good file intact"
    )
    assert not (out_dir / "dsf.parquet.tmp").exists()


def test_pull_table_force_failure_preserves_original_file_streaming(tmp_path):
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    out_path = out_dir / "dsf.parquet"
    original_content = b"GOOD-ORIGINAL-STREAMED-PARQUET-BYTES"
    out_path.write_bytes(original_content)

    class RaisingChunks:
        def __iter__(self):
            yield pd.DataFrame({"permno": [1], "date": ["2000-01-01"]})
            raise RuntimeError("simulated mid-stream failure")

    db = StubDB(columns=["permno", "date"])
    db.raw_sql = lambda sql, return_iter=False: (
        RaisingChunks() if return_iter else pd.DataFrame()
    )

    log = []
    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        force=True, stream=True,
    )

    assert log[0]["status"] == "query_failed"
    assert out_path.read_bytes() == original_content, (
        "a failed forced streaming re-write must leave the previously-good "
        "file intact, not truncate it"
    )
    assert not (out_dir / "dsf.parquet.tmp").exists()


def test_pull_table_streaming_exception_on_third_chunk_leaves_no_output_file(tmp_path):
    class RaisingChunks:
        def __iter__(self):
            yield pd.DataFrame({"permno": [1], "date": ["2000-01-01"]})
            yield pd.DataFrame({"permno": [2], "date": ["2000-01-02"]})
            raise RuntimeError("simulated failure on 3rd chunk")

    db = StubDB(columns=["permno", "date"])
    db.raw_sql = lambda sql, return_iter=False: (
        RaisingChunks() if return_iter else pd.DataFrame()
    )

    log = []
    out_path = tmp_path / "raw" / "crsp" / "dsf.parquet"

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        stream=True,
    )

    assert log[0]["status"] == "query_failed"
    assert not out_path.exists()
    assert not out_path.with_suffix(out_path.suffix + ".tmp").exists()


def test_pull_table_cleans_up_tmp_file_on_keyboard_interrupt_non_streaming(
    tmp_path, monkeypatch
):
    def _raise_keyboard_interrupt(self, path, *a, **k):
        Path(path).write_bytes(b"PARTIAL-BEFORE-CTRL-C")
        raise KeyboardInterrupt()

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _raise_keyboard_interrupt)

    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []
    raw_dir = tmp_path / "raw"

    with pytest.raises(KeyboardInterrupt):
        lib.pull_table(
            db, log, "crsp", "dsf", ["permno"],
            raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        )

    assert not (raw_dir / "crsp" / "dsf.parquet.tmp").exists()
    assert not (raw_dir / "crsp" / "dsf.parquet").exists()
    # KeyboardInterrupt is a BaseException, not an Exception -- pull_table's
    # outer `except Exception` must NOT swallow it and log "query_failed";
    # it must propagate all the way out.
    assert log == []


# ---------------------------------------------------------------------------
# Defensive identifier validation before SQL string construction, and before
# `library`/`table` are used for anything at all (including
# describe_table()).
# ---------------------------------------------------------------------------


def test_pull_table_rejects_unsafe_library_name(tmp_path):
    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    with pytest.raises(ValueError):
        lib.pull_table(
            db, log, "crsp; DROP TABLE x; --", "dsf", ["permno"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_pull_table_rejects_unsafe_table_name(tmp_path):
    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    with pytest.raises(ValueError):
        lib.pull_table(
            db, log, "crsp", "dsf; DROP TABLE x; --", ["permno"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_pull_table_rejects_unsafe_column_name(tmp_path):
    db = StubDB(
        columns=["permno", "date; DROP TABLE x; --"],
        rows=pd.DataFrame({"permno": [1]}),
    )
    log = []

    with pytest.raises(ValueError):
        lib.pull_table(
            db, log, "crsp", "dsf", ["permno", "date; DROP TABLE x; --"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_pull_table_rejects_unsafe_optional_column_name(tmp_path):
    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    with pytest.raises(ValueError):
        lib.pull_table(
            db, log, "crsp", "dsf", ["permno"],
            optional_columns=["date; DROP TABLE x; --"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_pull_table_rejects_unsafe_name_before_describe_table_is_called(tmp_path):
    """Identifier validation must happen before `library`/`table` are used
    for anything, including db.describe_table(...) -- not after. A db stub
    that would raise if describe_table were ever called with the bad name
    proves validation happens first.
    """

    class ExplodingDB:
        def describe_table(self, library, table):
            raise AssertionError(
                "describe_table must not be called with an unvalidated name"
            )

    log = []
    with pytest.raises(ValueError):
        lib.pull_table(
            ExplodingDB(), log, "crsp; DROP TABLE x; --", "dsf", ["permno"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_sql_identifier_regex_rejects_trailing_newline():
    # `$` in Python regex also matches immediately before a trailing
    # newline, so "dsf\n" would incorrectly pass a "$"-anchored pattern.
    # `\Z` is a true end-of-string anchor and must reject it.
    assert lib._SQL_IDENTIFIER_RE.match("dsf") is not None
    assert lib._SQL_IDENTIFIER_RE.match("dsf\n") is None
    with pytest.raises(ValueError):
        lib._validate_sql_identifier("table", "dsf\n")


# ---------------------------------------------------------------------------
# Phase B: resolve_universe -- local resolution of the OptionMetrics
# universe (current + historical CUSIPs) to CRSP PERMNO / Compustat GVKEY /
# I/B/E/S ticker, using small synthetic fixtures rather than the real
# 8,358-row data/metadata files (which this suite must not depend on or
# regenerate).
# ---------------------------------------------------------------------------


def _write_parquet(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def _resolve_universe_fixture(tmp_path, om=None, om_hist=None, stocknames=None, ccmxpf=None, ibcrsphist=None):
    """Write the five input Parquet fixtures resolve_universe() expects and
    call it, returning (ResolvedUniverse, unresolved_df, ambiguous_df,
    resolved_df) so a test can assert on any of the four outputs."""
    om = om if om is not None else pd.DataFrame(
        {"secid": [], "cusip": [], "ticker": [], "issuer": []}
    )
    om_hist = om_hist if om_hist is not None else pd.DataFrame({"secid": [], "cusip": []})
    stocknames = stocknames if stocknames is not None else pd.DataFrame(
        {"permno": [], "cusip": [], "ncusip": []}
    )
    ccmxpf = ccmxpf if ccmxpf is not None else pd.DataFrame({"lpermno": [], "gvkey": []})
    ibcrsphist = ibcrsphist if ibcrsphist is not None else pd.DataFrame({"permno": [], "ticker": []})

    om_path = _write_parquet(tmp_path / "metadata" / "optionmetrics_universe.parquet", om)
    om_hist_path = _write_parquet(
        tmp_path / "metadata" / "optionmetrics_universe_history.parquet", om_hist
    )
    stocknames_path = _write_parquet(tmp_path / "raw" / "crsp" / "stocknames.parquet", stocknames)
    ccmxpf_path = _write_parquet(tmp_path / "raw" / "crsp" / "ccmxpf_lnkhist.parquet", ccmxpf)
    ibcrsphist_path = _write_parquet(
        tmp_path / "raw" / "wrdsapps" / "ibcrsphist.parquet", ibcrsphist
    )

    unresolved_out = tmp_path / "metadata" / "unresolved_secids.csv"
    ambiguous_out = tmp_path / "metadata" / "ambiguous_links.csv"
    resolved_out = tmp_path / "metadata" / "resolved_universe_ids.parquet"
    without_gvkey_out = tmp_path / "metadata" / "permnos_without_gvkey.csv"
    without_ibes_ticker_out = tmp_path / "metadata" / "permnos_without_ibes_ticker.csv"

    resolved = lib.resolve_universe(
        om_universe_path=om_path,
        om_universe_history_path=om_hist_path,
        stocknames_path=stocknames_path,
        ccmxpf_lnkhist_path=ccmxpf_path,
        ibcrsphist_path=ibcrsphist_path,
        unresolved_secids_out=unresolved_out,
        ambiguous_links_out=ambiguous_out,
        resolved_ids_out=resolved_out,
        permnos_without_gvkey_out=without_gvkey_out,
        permnos_without_ibes_ticker_out=without_ibes_ticker_out,
    )
    # Read the CUSIP-shaped columns back as strings explicitly: plain
    # pd.read_csv() would otherwise re-infer a numeric-looking value like
    # "00000003" as the int 3, silently stripping the leading zeros --
    # the exact CUSIP pitfall this project's own task briefing calls out
    # for the Parquet inputs applies equally to reading these CSV outputs
    # back.
    ambiguous_dtype = {
        "secid": "Int64", "permno": "Int64", "gvkey": str, "ibes_ticker": str,
        "om_cusip": str, "om_ticker": str, "issuer": str,
    }
    gap_dtype = {
        "secid": "Int64", "permno": "Int64", "om_cusip": str, "om_ticker": str,
        "issuer": str,
    }
    return (
        resolved,
        pd.read_csv(unresolved_out, dtype={"secid": "int64", "cusip": str, "ticker": str, "issuer": str}),
        pd.read_csv(ambiguous_out, dtype=ambiguous_dtype),
        pd.read_parquet(resolved_out),
        pd.read_csv(without_gvkey_out, dtype=gap_dtype),
        pd.read_csv(without_ibes_ticker_out, dtype=gap_dtype),
    )


def test_resolve_universe_clean_one_to_one_resolution(tmp_path):
    """secid 1 resolves via its CURRENT cusip straight through to a single
    permno/gvkey/ibes ticker -- the baseline happy path."""
    om = pd.DataFrame({
        "secid": [1], "cusip": ["00000001"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [100], "cusip": ["00000001"], "ncusip": ["00000001"],
    })
    ccmxpf = pd.DataFrame({"lpermno": [100], "gvkey": ["001000"]})
    ibcrsphist = pd.DataFrame({"permno": [100], "ticker": ["AAA"]})

    resolved, unresolved_df, ambiguous_df, resolved_df, without_gvkey_df, without_ibes_ticker_df = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.permnos == [100]
    assert resolved.gvkeys == ["001000"]
    assert resolved.ibes_tickers == ["AAA"]
    assert resolved.n_secids_total == 1
    assert resolved.n_secids_resolved == 1
    assert resolved.n_ambiguous_links == 0
    assert len(unresolved_df) == 0
    assert len(ambiguous_df) == 0

    row = resolved_df.iloc[0]
    assert row["secid"] == 1
    assert row["permno"] == "100"
    assert row["gvkey"] == "001000"
    assert row["ibes_ticker"] == "AAA"
    assert row["permno_ambiguous"] == False  # noqa: E712
    assert row["gvkey_ambiguous"] == False  # noqa: E712
    assert row["ibes_ticker_ambiguous"] == False  # noqa: E712


def test_resolve_universe_resolves_via_historical_cusip_only(tmp_path):
    """secid 2's CURRENT cusip ("00000002") has no CRSP match at all -- the
    only real match is through a HISTORICAL cusip ("00000099") the security
    had before a rename/spinoff. If the historical-cusip join were silently
    skipped (only the current file consulted), this secid would show up as
    unresolved instead of linking to permno 200 -- that's exactly the
    scenario this test is designed to catch.
    """
    om = pd.DataFrame({
        "secid": [2], "cusip": ["00000002"], "ticker": ["BBB"], "issuer": ["BBB CORP"],
    })
    om_hist = pd.DataFrame({"secid": [2], "cusip": ["00000099"]})
    stocknames = pd.DataFrame({
        # Neither cusip NOR ncusip matches the CURRENT OM cusip
        # ("00000002") -- only the historical one ("00000099") matches.
        "permno": [200], "cusip": ["00000099"], "ncusip": ["00000099"],
    })
    ccmxpf = pd.DataFrame({"lpermno": [200], "gvkey": ["002000"]})
    ibcrsphist = pd.DataFrame({"permno": [200], "ticker": ["BBB"]})

    resolved, unresolved_df, ambiguous_df, resolved_df, without_gvkey_df, without_ibes_ticker_df = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.permnos == [200], (
        "secid 2 should resolve via its HISTORICAL cusip (00000099) even "
        "though its CURRENT cusip (00000002) has no CRSP match at all -- "
        "if the historical-cusip union were silently skipped, this would "
        "come back empty (secid 2 unresolved) instead"
    )
    assert resolved.n_secids_resolved == 1
    assert len(unresolved_df) == 0


def test_resolve_universe_no_crsp_match_lands_in_unresolved_not_dropped(tmp_path):
    """secid 3 has no matching cusip or ncusip anywhere in crsp.stocknames
    (current or historical) -- it must show up in unresolved_secids.csv
    with its identifying detail, never just silently vanish."""
    om = pd.DataFrame({
        "secid": [3], "cusip": ["00000003"], "ticker": ["CCC"], "issuer": ["CCC CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [300], "cusip": ["99999999"], "ncusip": ["99999999"],
    })
    ccmxpf = pd.DataFrame({"lpermno": [300], "gvkey": ["003000"]})
    ibcrsphist = pd.DataFrame({"permno": [300], "ticker": ["CCC"]})

    resolved, unresolved_df, ambiguous_df, resolved_df, without_gvkey_df, without_ibes_ticker_df = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.permnos == []
    assert resolved.n_secids_resolved == 0
    assert len(unresolved_df) == 1
    row = unresolved_df.iloc[0]
    assert row["secid"] == 3
    assert row["cusip"] == "00000003"
    assert row["ticker"] == "CCC"
    assert row["issuer"] == "CCC CORP"

    resolved_row = resolved_df.iloc[0]
    assert pd.isna(resolved_row["permno"])


def test_resolve_universe_ambiguous_secid_to_permno_is_flagged_not_dropped(tmp_path):
    """Two different OM secids (4 and 5) both resolve to the SAME permno
    (400) -- e.g. because CRSP recycled a CUSIP, or two OM entries actually
    share history. This must be flagged in ambiguous_links.csv, and BOTH
    secids' permno must still make it into the final filter list (over-
    inclusion is the safe direction for a universe filter) -- neither one
    may be silently dropped or silently picked over the other.
    """
    om = pd.DataFrame({
        "secid": [4, 5],
        "cusip": ["00000004", "00000005"],
        "ticker": ["DDD", "EEE"],
        "issuer": ["DDD CORP", "EEE CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [400, 400],
        "cusip": ["00000004", "00000005"],
        "ncusip": ["00000004", "00000005"],
    })
    ccmxpf = pd.DataFrame({"lpermno": [400], "gvkey": ["004000"]})
    ibcrsphist = pd.DataFrame({"permno": [400], "ticker": ["DDD"]})

    resolved, unresolved_df, ambiguous_df, resolved_df, without_gvkey_df, without_ibes_ticker_df = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.n_ambiguous_links > 0
    assert set(ambiguous_df["stage"]) == {"secid_to_permno"}
    assert set(ambiguous_df["secid"].dropna().astype(int)) == {4, 5}

    # The over-inclusion policy: permno 400 must still be in the filter
    # list, and BOTH secid 4 and secid 5's rows in resolved_universe_ids.
    # parquet must show permno 400 with permno_ambiguous flagged True, not
    # one of them silently dropped.
    assert resolved.permnos == [400]
    for secid in (4, 5):
        row = resolved_df[resolved_df["secid"] == secid].iloc[0]
        assert row["permno"] == "400"
        assert row["permno_ambiguous"] == True  # noqa: E712


def test_resolve_universe_missing_input_file_raises_before_any_output_written(tmp_path):
    """A missing Phase A input file (e.g. crsp.stocknames never pulled) must
    raise UniverseResolutionError -- the caller (main()) is responsible for
    turning that into a hard abort before Phase C, per the design's
    "never silently produce an empty filter" requirement."""
    om_path = _write_parquet(
        tmp_path / "metadata" / "optionmetrics_universe.parquet",
        pd.DataFrame({"secid": [1], "cusip": ["1"], "ticker": ["A"], "issuer": ["A"]}),
    )
    om_hist_path = _write_parquet(
        tmp_path / "metadata" / "optionmetrics_universe_history.parquet",
        pd.DataFrame({"secid": [], "cusip": []}),
    )

    with pytest.raises(lib.UniverseResolutionError):
        lib.resolve_universe(
            om_universe_path=om_path,
            om_universe_history_path=om_hist_path,
            stocknames_path=tmp_path / "raw" / "crsp" / "stocknames.parquet",  # never written
            ccmxpf_lnkhist_path=tmp_path / "raw" / "crsp" / "ccmxpf_lnkhist.parquet",
            ibcrsphist_path=tmp_path / "raw" / "wrdsapps" / "ibcrsphist.parquet",
            unresolved_secids_out=tmp_path / "metadata" / "unresolved_secids.csv",
            ambiguous_links_out=tmp_path / "metadata" / "ambiguous_links.csv",
            resolved_ids_out=tmp_path / "metadata" / "resolved_universe_ids.parquet",
        )


def test_resolve_universe_missing_required_column_raises(tmp_path):
    """A Phase A table that WAS pulled but is missing a column resolve_
    universe structurally depends on (e.g. crsp.stocknames without ncusip)
    must also raise, not silently join on whatever happens to be there."""
    om_path = _write_parquet(
        tmp_path / "metadata" / "optionmetrics_universe.parquet",
        pd.DataFrame({"secid": [1], "cusip": ["1"], "ticker": ["A"], "issuer": ["A"]}),
    )
    om_hist_path = _write_parquet(
        tmp_path / "metadata" / "optionmetrics_universe_history.parquet",
        pd.DataFrame({"secid": [], "cusip": []}),
    )
    stocknames_path = _write_parquet(
        tmp_path / "raw" / "crsp" / "stocknames.parquet",
        pd.DataFrame({"permno": [1], "cusip": ["1"]}),  # ncusip missing
    )
    ccmxpf_path = _write_parquet(
        tmp_path / "raw" / "crsp" / "ccmxpf_lnkhist.parquet",
        pd.DataFrame({"lpermno": [1], "gvkey": ["1"]}),
    )
    ibcrsphist_path = _write_parquet(
        tmp_path / "raw" / "wrdsapps" / "ibcrsphist.parquet",
        pd.DataFrame({"permno": [1], "ticker": ["A"]}),
    )

    with pytest.raises(lib.UniverseResolutionError):
        lib.resolve_universe(
            om_universe_path=om_path,
            om_universe_history_path=om_hist_path,
            stocknames_path=stocknames_path,
            ccmxpf_lnkhist_path=ccmxpf_path,
            ibcrsphist_path=ibcrsphist_path,
            unresolved_secids_out=tmp_path / "metadata" / "unresolved_secids.csv",
            ambiguous_links_out=tmp_path / "metadata" / "ambiguous_links.csv",
            resolved_ids_out=tmp_path / "metadata" / "resolved_universe_ids.parquet",
        )


# ---------------------------------------------------------------------------
# Fix 4: mutation-proof tests for the CUSIP/NCUSIP join. Every fixture above
# this point sets stocknames.cusip == stocknames.ncusip for every row, which
# means matching via only one of the two columns is indistinguishable from
# matching via both -- confirmed by mutation testing that all pre-existing
# tests still passed after dropping either half of the join, dropping
# normalization, or swapping the GVKEY clause builder to numeric. These
# fixtures deliberately set cusip != ncusip so each half of the join (and
# normalization) has an independent, catchable effect.
# ---------------------------------------------------------------------------


def test_resolve_universe_resolves_via_ncusip_only_not_cusip(tmp_path):
    """secid 10's OM cusip matches crsp.stocknames' NCUSIP column but NOT
    its CUSIP column (which holds an unrelated value) -- this must still
    resolve. If the ncusip half of the stocknames join were removed, this
    secid would come back unresolved instead."""
    om = pd.DataFrame({
        "secid": [10], "cusip": ["00000010"], "ticker": ["JJJ"], "issuer": ["JJJ CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [1010],
        "cusip": ["99999999"],     # does NOT match the OM cusip
        "ncusip": ["00000010"],    # DOES match the OM cusip
    })
    ccmxpf = pd.DataFrame({"lpermno": [1010], "gvkey": ["010000"]})
    ibcrsphist = pd.DataFrame({"permno": [1010], "ticker": ["JJJ"]})

    resolved, *_ = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.permnos == [1010], (
        "secid 10 should resolve via NCUSIP alone (its CUSIP column value "
        "doesn't match at all) -- if the ncusip half of the join were "
        "dropped, this would come back unresolved instead"
    )


def test_resolve_universe_resolves_via_cusip_only_not_ncusip(tmp_path):
    """secid 20's OM cusip matches crsp.stocknames' CUSIP column but NOT its
    NCUSIP column (which holds an unrelated value) -- this must still
    resolve. If the cusip half of the stocknames join were removed, this
    secid would come back unresolved instead."""
    om = pd.DataFrame({
        "secid": [20], "cusip": ["00000020"], "ticker": ["KKK"], "issuer": ["KKK CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [2020],
        "cusip": ["00000020"],    # DOES match the OM cusip
        "ncusip": ["88888888"],   # does NOT match the OM cusip
    })
    ccmxpf = pd.DataFrame({"lpermno": [2020], "gvkey": ["020000"]})
    ibcrsphist = pd.DataFrame({"permno": [2020], "ticker": ["KKK"]})

    resolved, *_ = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.permnos == [2020], (
        "secid 20 should resolve via CUSIP alone (its NCUSIP column value "
        "doesn't match at all) -- if the cusip half of the join were "
        "dropped, this would come back unresolved instead"
    )


def test_resolve_universe_cusip_normalization_strip_and_uppercase_matters(tmp_path):
    """secid 30's crsp.stocknames CUSIP is lowercase and whitespace-padded
    (" ab000030 ") -- it only matches the OM CUSIP ("AB000030") after
    normalization (strip + uppercase) is applied. NCUSIP is deliberately a
    non-matching value so this test isolates normalization's effect from
    the ncusip-vs-cusip question covered by the two tests above."""
    om = pd.DataFrame({
        "secid": [30], "cusip": ["AB000030"], "ticker": ["LLL"], "issuer": ["LLL CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [3030],
        "cusip": [" ab000030 "],   # only matches AFTER strip+uppercase
        "ncusip": ["zzzzzzzz"],    # does not match either way
    })
    ccmxpf = pd.DataFrame({"lpermno": [3030], "gvkey": ["030000"]})
    ibcrsphist = pd.DataFrame({"permno": [3030], "ticker": ["LLL"]})

    resolved, *_ = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.permnos == [3030], (
        "secid 30 should resolve once CUSIP normalization (strip + "
        "uppercase) is applied to crsp.stocknames' cusip column -- if "
        "normalization were removed, ' ab000030 ' would never match "
        "'AB000030' and this secid would come back unresolved instead"
    )


def test_resolve_universe_gvkey_survives_as_zero_padded_string_despite_numeric_source(tmp_path):
    """crsp.ccmxpf_lnkhist's gvkey column supplied as a genuine int64 (1000,
    not "001000") -- simulating the case where a Parquet round-trip ever
    inferred GVKEY numerically. resolve_universe must recover the correct
    zero-padded 6-digit GVKEY string ("001000"), and the clause main()
    renders from it must be the quoted string form, never the bare numeric
    form build_numeric_in_clause (Fix 1's actual bug) would produce."""
    om = pd.DataFrame({
        "secid": [40], "cusip": ["00000040"], "ticker": ["MMM"], "issuer": ["MMM CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [4040], "cusip": ["00000040"], "ncusip": ["00000040"],
    })
    ccmxpf = pd.DataFrame({"lpermno": [4040], "gvkey": [1000]})  # int, not str
    ibcrsphist = pd.DataFrame({"permno": [4040], "ticker": ["MMM"]})

    resolved, *_ = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.gvkeys == ["001000"], (
        "GVKEY must come back as its correct zero-padded 6-digit string "
        "form even when the source column was a numeric dtype"
    )
    clause = lib.build_string_in_clause("gvkey", resolved.gvkeys)
    assert clause == "gvkey IN ('001000')", (
        f"expected the quoted, zero-padded clause; got {clause!r} -- "
        f"build_numeric_in_clause (Fix 1's bug) would instead produce an "
        f"unquoted 'gvkey IN (1000)' (zeros stripped) or, at best, an "
        f"unquoted 'gvkey IN (001000)' that a real varchar column has no "
        f"operator for"
    )


def test_resolve_universe_gvkey_survives_as_zero_padded_string_despite_float64_source(tmp_path):
    """Fix 1, round 2: crsp.ccmxpf_lnkhist's gvkey column supplied as a
    genuine float64 (1000.0, NOT the int64 1000 the test above covers) --
    this is exactly what pull_table's non-streaming path (used for this
    small Phase A linking table) can actually produce: pandas' dtype
    inference lands on float64 for a gvkey column whenever it has any
    null, or under certain other numeric-inference conditions. The pre-fix
    bug: `.astype(str)` on a float64 column gives "1000.0" (already 6+
    characters), so `.str.zfill(6)` is a NO-OP -- silently producing a
    GVKEY that matches nothing in Compustat, without the resolution-floor/
    empty checks ever catching it (the gvkey list is non-empty, just
    wrong). resolve_universe must still recover the correct zero-padded
    "001000"."""
    om = pd.DataFrame({
        "secid": [41], "cusip": ["00000041"], "ticker": ["NNN"], "issuer": ["NNN CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [4141], "cusip": ["00000041"], "ncusip": ["00000041"],
    })
    ccmxpf = pd.DataFrame({
        "lpermno": [4141],
        "gvkey": pd.array([1000.0], dtype="float64"),
    })
    assert ccmxpf["gvkey"].dtype == "float64", (
        "sanity check: this test is only meaningful if the source column "
        "really is float64, not int64/object"
    )
    ibcrsphist = pd.DataFrame({"permno": [4141], "ticker": ["NNN"]})

    resolved, *_ = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    assert resolved.gvkeys == ["001000"], (
        f"GVKEY must come back as the correct zero-padded 6-digit string "
        f"even when the source column is float64 -- got {resolved.gvkeys!r}. "
        f"The pre-fix bug would have silently produced ['1000.0'] instead, "
        f"which matches nothing in Compustat."
    )
    clause = lib.build_string_in_clause("gvkey", resolved.gvkeys)
    assert clause == "gvkey IN ('001000')"


# ---------------------------------------------------------------------------
# Fix 1, round 2: _normalize_gvkeys (root-cause dtype fix) and
# _validate_gvkeys (safety net) in isolation, not just end-to-end via
# resolve_universe above.
# ---------------------------------------------------------------------------


def test_normalize_gvkeys_handles_float64_dtype():
    result = lib._normalize_gvkeys(pd.Series([1000.0, 2.0], dtype="float64"))
    assert list(result) == ["001000", "000002"]


def test_normalize_gvkeys_handles_int64_dtype():
    result = lib._normalize_gvkeys(pd.Series([1000, 2], dtype="int64"))
    assert list(result) == ["001000", "000002"]


def test_normalize_gvkeys_handles_object_dtype_already_correct():
    result = lib._normalize_gvkeys(pd.Series(["001000", "000002"], dtype="object"))
    assert list(result) == ["001000", "000002"]


def test_validate_gvkeys_accepts_well_formed():
    lib._validate_gvkeys(["001000", "999999"])  # must not raise


def test_validate_gvkeys_rejects_float_like_string():
    # This is exactly the pre-fix bug's output shape -- the safety net must
    # catch it even if _normalize_gvkeys's root-cause fix were ever
    # bypassed or regressed.
    with pytest.raises(ValueError):
        lib._validate_gvkeys(["1000.0"])


def test_validate_gvkeys_rejects_wrong_length():
    with pytest.raises(ValueError):
        lib._validate_gvkeys(["12345"])
    with pytest.raises(ValueError):
        lib._validate_gvkeys(["1234567"])


# ---------------------------------------------------------------------------
# Fix 3: crsp.ccmxpf_lnkhist / wrdsapps.ibcrsphist missing/unpullable at the
# resolve_universe() level (see the main()-level dedicated tests earlier in
# this file for the end-to-end Phase C gating behavior) -- PERMNO resolution
# must still succeed and the OTHER identifier family must be unaffected.
# ---------------------------------------------------------------------------


def test_resolve_universe_missing_ccmxpf_lnkhist_skips_gvkey_only(tmp_path):
    om = pd.DataFrame({
        "secid": [50], "cusip": ["00000050"], "ticker": ["NNN"], "issuer": ["NNN CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [5050], "cusip": ["00000050"], "ncusip": ["00000050"],
    })
    ibcrsphist = pd.DataFrame({"permno": [5050], "ticker": ["NNN"]})

    om_path = _write_parquet(tmp_path / "metadata" / "optionmetrics_universe.parquet", om)
    om_hist_path = _write_parquet(
        tmp_path / "metadata" / "optionmetrics_universe_history.parquet", om_hist
    )
    stocknames_path = _write_parquet(tmp_path / "raw" / "crsp" / "stocknames.parquet", stocknames)
    ibcrsphist_path = _write_parquet(
        tmp_path / "raw" / "wrdsapps" / "ibcrsphist.parquet", ibcrsphist
    )
    # crsp.ccmxpf_lnkhist.parquet is deliberately never written -- simulates
    # the table being missing/unpullable on this account.

    resolved = lib.resolve_universe(
        om_universe_path=om_path,
        om_universe_history_path=om_hist_path,
        stocknames_path=stocknames_path,
        ccmxpf_lnkhist_path=tmp_path / "raw" / "crsp" / "ccmxpf_lnkhist.parquet",
        ibcrsphist_path=ibcrsphist_path,
        unresolved_secids_out=tmp_path / "metadata" / "unresolved_secids.csv",
        ambiguous_links_out=tmp_path / "metadata" / "ambiguous_links.csv",
        resolved_ids_out=tmp_path / "metadata" / "resolved_universe_ids.parquet",
    )

    assert resolved.permnos == [5050], "PERMNO resolution must succeed regardless"
    assert resolved.ibes_tickers == ["NNN"], "I/B/E/S ticker resolution must be unaffected"
    assert resolved.gvkeys == []
    assert resolved.gvkey_resolution_skipped is True
    assert "ccmxpf_lnkhist" in resolved.gvkey_skip_reason
    assert resolved.ibes_ticker_resolution_skipped is False
    assert resolved.ibes_ticker_skip_reason == ""

    # Finding 6 (round 2 review): GVKEY resolution was SKIPPED (never
    # evaluated), so the coverage-gap audit CSV must be empty -- NOT list
    # every resolved PERMNO as if each one had been individually checked
    # and found to have no GVKEY link. That would conflate "we never
    # checked" with "we checked and it doesn't link".
    assert resolved.n_permnos_without_gvkey == 0
    without_gvkey_df = pd.read_csv(tmp_path / "metadata" / "permnos_without_gvkey.csv")
    assert without_gvkey_df.empty, (
        "GVKEY resolution was skipped -- permnos_without_gvkey.csv must be "
        "empty, not populated with every resolved PERMNO"
    )


def test_resolve_universe_missing_ibcrsphist_skips_ibes_ticker_only(tmp_path):
    om = pd.DataFrame({
        "secid": [60], "cusip": ["00000060"], "ticker": ["OOO"], "issuer": ["OOO CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [6060], "cusip": ["00000060"], "ncusip": ["00000060"],
    })
    ccmxpf = pd.DataFrame({"lpermno": [6060], "gvkey": ["060000"]})

    om_path = _write_parquet(tmp_path / "metadata" / "optionmetrics_universe.parquet", om)
    om_hist_path = _write_parquet(
        tmp_path / "metadata" / "optionmetrics_universe_history.parquet", om_hist
    )
    stocknames_path = _write_parquet(tmp_path / "raw" / "crsp" / "stocknames.parquet", stocknames)
    ccmxpf_path = _write_parquet(tmp_path / "raw" / "crsp" / "ccmxpf_lnkhist.parquet", ccmxpf)
    # wrdsapps.ibcrsphist.parquet is deliberately never written.

    resolved = lib.resolve_universe(
        om_universe_path=om_path,
        om_universe_history_path=om_hist_path,
        stocknames_path=stocknames_path,
        ccmxpf_lnkhist_path=ccmxpf_path,
        ibcrsphist_path=tmp_path / "raw" / "wrdsapps" / "ibcrsphist.parquet",
        unresolved_secids_out=tmp_path / "metadata" / "unresolved_secids.csv",
        ambiguous_links_out=tmp_path / "metadata" / "ambiguous_links.csv",
        resolved_ids_out=tmp_path / "metadata" / "resolved_universe_ids.parquet",
    )

    assert resolved.permnos == [6060]
    assert resolved.gvkeys == ["060000"], "GVKEY resolution must be unaffected"
    assert resolved.ibes_tickers == []
    assert resolved.ibes_ticker_resolution_skipped is True
    assert "ibcrsphist" in resolved.ibes_ticker_skip_reason
    assert resolved.gvkey_resolution_skipped is False
    assert resolved.gvkey_skip_reason == ""

    # Finding 6 (round 2 review): same reasoning as the ccmxpf_lnkhist test
    # above, for the I/B/E/S ticker side.
    assert resolved.n_permnos_without_ibes_ticker == 0
    without_ibes_ticker_df = pd.read_csv(
        tmp_path / "metadata" / "permnos_without_ibes_ticker.csv"
    )
    assert without_ibes_ticker_df.empty, (
        "I/B/E/S ticker resolution was skipped -- "
        "permnos_without_ibes_ticker.csv must be empty, not populated with "
        "every resolved PERMNO"
    )


# ---------------------------------------------------------------------------
# Fix 5: a PERMNO that resolves fine but has no GVKEY/ibes ticker link at
# all must be counted and written to its own audit CSV -- distinct from
# unresolved_secids.csv (which covers "never got a PERMNO").
# ---------------------------------------------------------------------------


def test_resolve_universe_tracks_permnos_without_gvkey_and_ibes_ticker_link(tmp_path):
    om = pd.DataFrame({
        "secid": [70], "cusip": ["00000070"], "ticker": ["PPP"], "issuer": ["PPP CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [7070], "cusip": ["00000070"], "ncusip": ["00000070"],
    })
    # PERMNO 7070 resolves fine but has NO row in either linking table.
    ccmxpf = pd.DataFrame({"lpermno": [9999], "gvkey": ["099999"]})
    ibcrsphist = pd.DataFrame({"permno": [9999], "ticker": ["ZZZ"]})

    resolved, _unresolved_df, _ambiguous_df, _resolved_df, without_gvkey_df, without_ibes_ticker_df = (
        _resolve_universe_fixture(
            tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
        )
    )

    assert resolved.permnos == [7070]
    assert resolved.n_permnos_without_gvkey == 1
    assert resolved.n_permnos_without_ibes_ticker == 1
    assert list(without_gvkey_df["permno"]) == [7070]
    assert list(without_gvkey_df["secid"]) == [70]
    assert list(without_gvkey_df["om_cusip"]) == ["00000070"]
    assert list(without_gvkey_df["issuer"]) == ["PPP CORP"]
    assert list(without_ibes_ticker_df["permno"]) == [7070]
    assert list(without_ibes_ticker_df["secid"]) == [70]


def test_resolve_universe_permno_with_gvkey_and_ticker_link_not_in_gap_audit(tmp_path):
    """The flip side: a PERMNO that DOES have both links must not appear in
    either coverage-gap CSV."""
    resolved, _unresolved_df, _ambiguous_df, _resolved_df, without_gvkey_df, without_ibes_ticker_df = (
        _resolve_universe_fixture(
            tmp_path,
            om=pd.DataFrame({
                "secid": [1], "cusip": ["00000001"], "ticker": ["AAA"], "issuer": ["AAA CORP"],
            }),
            om_hist=pd.DataFrame({"secid": [], "cusip": []}),
            stocknames=pd.DataFrame({
                "permno": [100], "cusip": ["00000001"], "ncusip": ["00000001"],
            }),
            ccmxpf=pd.DataFrame({"lpermno": [100], "gvkey": ["001000"]}),
            ibcrsphist=pd.DataFrame({"permno": [100], "ticker": ["AAA"]}),
        )
    )
    assert resolved.n_permnos_without_gvkey == 0
    assert resolved.n_permnos_without_ibes_ticker == 0
    assert len(without_gvkey_df) == 0
    assert len(without_ibes_ticker_df) == 0


# ---------------------------------------------------------------------------
# Fix 7: the permno_to_gvkey / permno_to_ibes_ticker ambiguous-link stages
# must carry secid/cusip/issuer detail (merged in via the PERMNO
# resolution), matching the secid_to_permno stage -- not just a bare permno.
# ---------------------------------------------------------------------------


def test_resolve_universe_ambiguous_gvkey_and_ticker_stages_carry_om_detail(tmp_path):
    om = pd.DataFrame({
        "secid": [80], "cusip": ["00000080"], "ticker": ["QQQ"], "issuer": ["QQQ CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [8080], "cusip": ["00000080"], "ncusip": ["00000080"],
    })
    # permno 8080 links to TWO distinct gvkeys and TWO distinct tickers.
    ccmxpf = pd.DataFrame({"lpermno": [8080, 8080], "gvkey": ["080001", "080002"]})
    ibcrsphist = pd.DataFrame({"permno": [8080, 8080], "ticker": ["QQQ", "QQQOLD"]})

    _resolved, _unresolved_df, ambiguous_df, _resolved_df, _wg, _wt = _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    gvkey_stage = ambiguous_df[ambiguous_df["stage"] == "permno_to_gvkey"]
    ticker_stage = ambiguous_df[ambiguous_df["stage"] == "permno_to_ibes_ticker"]
    assert len(gvkey_stage) >= 1
    assert len(ticker_stage) >= 1
    assert set(gvkey_stage["secid"].dropna().astype(int)) == {80}, (
        "permno_to_gvkey rows must carry the secid this permno came from, "
        "not just a bare permno"
    )
    assert set(gvkey_stage["om_cusip"].dropna()) == {"00000080"}
    assert set(gvkey_stage["issuer"].dropna()) == {"QQQ CORP"}
    assert set(ticker_stage["secid"].dropna().astype(int)) == {80}
    assert set(ticker_stage["om_cusip"].dropna()) == {"00000080"}
    assert set(ticker_stage["issuer"].dropna()) == {"QQQ CORP"}


# ---------------------------------------------------------------------------
# Fix 6: I/B/E/S ticker format allowlist -- applied before building the
# ticker IN (...) clause, since build_string_in_clause's quote-doubling
# escape is correct under Postgres defaults but not guaranteed safe if
# standard_conforming_strings is off.
# ---------------------------------------------------------------------------


def test_validate_ibes_tickers_accepts_typical_tickers():
    lib._validate_ibes_tickers(["AAPL", "BRK.A", "BF-B", "A", "1234567890"])


def test_validate_ibes_tickers_rejects_disallowed_characters():
    with pytest.raises(ValueError):
        lib._validate_ibes_tickers(["AAPL; DROP TABLE x; --"])


def test_validate_ibes_tickers_rejects_too_long_ticker():
    with pytest.raises(ValueError):
        lib._validate_ibes_tickers(["A" * 11])


def test_validate_ibes_tickers_rejects_lowercase():
    with pytest.raises(ValueError):
        lib._validate_ibes_tickers(["aapl"])


# ---------------------------------------------------------------------------
# Fix 2, round 2: a single malformed/blank I/B/E/S ticker must never abort
# the ENTIRE run -- only ever degrade the I/B/E/S family, the same way a
# missing wrdsapps.ibcrsphist table already does (Fix 3, round 1).
# ---------------------------------------------------------------------------


def test_resolve_universe_blank_ibes_ticker_treated_as_missing_link(tmp_path):
    """A blank/whitespace-only ticker in wrdsapps.ibcrsphist (e.g. ""
    surviving dropna() because it's an empty string, not NaN) must be
    dropped the same way a missing link is -- reducing this permno's
    I/B/E/S coverage and landing it in permnos_without_ibes_ticker.csv --
    rather than reaching main()'s ticker-format allowlist, where it would
    previously have raised and (per the bug this round fixes) aborted the
    entire run including CRSP/Compustat pulls that don't need I/B/E/S at
    all."""
    om = pd.DataFrame({
        "secid": [90], "cusip": ["00000090"], "ticker": ["RRR"], "issuer": ["RRR CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        "permno": [9090], "cusip": ["00000090"], "ncusip": ["00000090"],
    })
    ccmxpf = pd.DataFrame({"lpermno": [9090], "gvkey": ["090000"]})
    ibcrsphist = pd.DataFrame({"permno": [9090], "ticker": ["   "]})  # blank after strip

    resolved, _unresolved_df, _ambiguous_df, _resolved_df, _without_gvkey_df, without_ibes_ticker_df = (
        _resolve_universe_fixture(
            tmp_path, om=om, om_hist=om_hist, stocknames=stocknames, ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
        )
    )

    assert resolved.ibes_tickers == [], (
        "a blank ticker must not appear in the resolved ticker list"
    )
    assert resolved.ibes_ticker_resolution_skipped is False, (
        "the linking table itself WAS available -- this is a per-row data "
        "quality issue, not a missing/unpullable table"
    )
    assert list(without_ibes_ticker_df["permno"]) == [9090], (
        "the permno with only a blank ticker must show up in the "
        "coverage-gap audit, same as if it had no ibcrsphist row at all"
    )




# ---------------------------------------------------------------------------
# Fix 8: CUSIP length sanity check -- logs a warning (never hard-fails) when
# normalized CUSIPs on either side of the join aren't all length 8.
# ---------------------------------------------------------------------------


def test_check_cusip_lengths_warns_on_mismatched_length(capsys):
    lib._check_cusip_lengths(pd.Series(["12345678", "1234", "ABCDEFGH"]), "test context")
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "test context" in captured.out


def test_check_cusip_lengths_silent_when_all_length_8(capsys):
    lib._check_cusip_lengths(pd.Series(["12345678", "ABCDEFGH"]), "test context")
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out




# ---------------------------------------------------------------------------
# Fix 4: an ABSOLUTE floor on the universe file's secid count
# (MIN_UNIVERSE_SECID_COUNT), independent of the RATE-based
# MIN_PERMNO_RESOLUTION_RATE floor above -- a truncated/corrupted universe
# file that happens to resolve at 100% would sail straight through a
# rate-only floor.
# ---------------------------------------------------------------------------


def test_check_universe_secid_floor_rejects_below_threshold(tmp_path):
    path = tmp_path / "tiny_universe.parquet"
    pd.DataFrame({"secid": [1, 2, 3]}).to_parquet(path, index=False)
    with pytest.raises(lib.UniverseResolutionError):
        lib._check_universe_secid_floor(path)


def test_check_universe_secid_floor_accepts_at_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "MIN_UNIVERSE_SECID_COUNT", 3)
    path = tmp_path / "universe.parquet"
    pd.DataFrame({"secid": [1, 2, 3]}).to_parquet(path, index=False)
    assert lib._check_universe_secid_floor(path) == 3


def test_check_universe_secid_floor_missing_file_raises(tmp_path):
    with pytest.raises(lib.UniverseResolutionError):
        lib._check_universe_secid_floor(tmp_path / "does_not_exist.parquet")




# ---------------------------------------------------------------------------
# IN (...) clause builders -- numeric (permno/gvkey) and string (ibes
# ticker), and that they combine correctly with each table's existing date/
# format filters in main() (nothing lost or overwritten).
# ---------------------------------------------------------------------------


def test_build_numeric_in_clause_basic():
    assert lib.build_numeric_in_clause("permno", [3, 1, 2]) == "permno IN (1, 2, 3)"


def test_build_numeric_in_clause_dedupes():
    assert lib.build_numeric_in_clause("permno", [1, 1, 2]) == "permno IN (1, 2)"


def test_build_numeric_in_clause_empty_list_matches_nothing_not_everything():
    # An empty resolved-ID list must produce a filter that matches NOTHING
    # (IN (NULL)), not an omitted/empty filter that would silently pull an
    # unfiltered table.
    assert lib.build_numeric_in_clause("permno", []) == "permno IN (NULL)"


def test_build_numeric_in_clause_rejects_non_numeric_value():
    with pytest.raises(ValueError):
        lib.build_numeric_in_clause("permno", ["1; DROP TABLE x; --"])


def test_build_numeric_in_clause_rejects_unsafe_column_name():
    with pytest.raises(ValueError):
        lib.build_numeric_in_clause("permno; DROP TABLE x; --", [1])


def test_build_string_in_clause_basic():
    assert lib.build_string_in_clause("ticker", ["MSFT", "AAPL"]) == "ticker IN ('AAPL', 'MSFT')"


def test_build_string_in_clause_escapes_embedded_quote():
    assert lib.build_string_in_clause("ticker", ["O'BRIEN"]) == "ticker IN ('O''BRIEN')"


def test_build_string_in_clause_empty_list_matches_nothing_not_everything():
    assert lib.build_string_in_clause("ticker", []) == "ticker IN (NULL)"


def test_build_string_in_clause_drops_blank_values():
    assert lib.build_string_in_clause("ticker", ["AAPL", "", "   ", None]) == "ticker IN ('AAPL')"


# ---------------------------------------------------------------------------
# NEW this round: helpers specifically needed by the split-scripts design --
# require_resolved_universe_file() (the "run pull_wrds_reference.py first"
# guard shared by the three consumer scripts) and load_resolved_filter_lists()
# (reconstructing resolve_universe()'s in-memory PERMNO/GVKEY/ticker filter
# lists from resolved_universe_ids.parquet on disk, since each consumer
# script is now its own process and never calls resolve_universe() itself).
# ---------------------------------------------------------------------------


def test_require_resolved_universe_file_missing_raises_systemexit_with_clear_message(tmp_path):
    missing_path = tmp_path / "metadata" / "resolved_universe_ids.parquet"
    with pytest.raises(SystemExit) as exc_info:
        lib.require_resolved_universe_file(missing_path)
    message = str(exc_info.value)
    assert "pull_wrds_reference.py" in message
    assert str(missing_path) in message


def test_require_resolved_universe_file_existing_returns_path(tmp_path):
    path = tmp_path / "metadata" / "resolved_universe_ids.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({
        "secid": [1], "permno": ["100"], "gvkey": ["001000"], "ibes_ticker": ["AAA"],
    }).to_parquet(path, index=False)
    assert lib.require_resolved_universe_file(path) == path


def test_require_resolved_universe_file_missing_columns_raises_systemexit_with_clear_message(tmp_path):
    """Finding 12: a file that EXISTS but is missing a column
    load_resolved_filter_lists() structurally depends on must be rejected by
    THIS guard, with the same clear, actionable message as the missing-file
    case -- not sail through here only to fail one line later inside
    load_resolved_filter_lists with a less-actionable error."""
    path = tmp_path / "metadata" / "resolved_universe_ids.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"secid": [1]}).to_parquet(path, index=False)

    with pytest.raises(SystemExit) as exc_info:
        lib.require_resolved_universe_file(path)
    message = str(exc_info.value)
    assert "permno" in message
    assert "gvkey" in message
    assert "ibes_ticker" in message
    assert "pull_wrds_reference.py" in message


def test_require_resolved_universe_file_malformed_parquet_raises_systemexit(tmp_path):
    path = tmp_path / "metadata" / "resolved_universe_ids.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not actually parquet")

    with pytest.raises(SystemExit) as exc_info:
        lib.require_resolved_universe_file(path)
    assert "pull_wrds_reference.py" in str(exc_info.value)


def test_require_resolved_universe_file_guard_rejects_leftover_partial_state(tmp_path):
    """Finding 1: proves the DOWNSTREAM guard itself (not just
    pull_wrds_reference.py exiting 1) correctly refuses a resolved-ids file
    left in a partial/leftover state -- e.g. a stale file from a run whose
    floor check failed AFTER something had already been written to that
    exact path in a pre-fix world. Simulates that leftover state directly
    (a file missing the columns a real resolve_universe() output always
    has) and confirms require_resolved_universe_file -- the exact function
    every one of the 3 consumer scripts calls first -- rejects it, rather
    than merely checking that path.exists() is True."""
    path = tmp_path / "metadata" / "resolved_universe_ids.parquet"
    path.parent.mkdir(parents=True)
    # A bare/partial file, as could only ever be observed here if the
    # atomic-gating fix (Finding 1) were removed or broken: exists, but
    # missing the columns a genuine resolve_universe() output always has.
    pd.DataFrame({"secid": [1]}).to_parquet(path, index=False)
    assert path.exists(), "sanity check: the leftover file really is present"

    with pytest.raises(SystemExit):
        lib.require_resolved_universe_file(path)


def test_load_resolved_filter_lists_reconstructs_exact_union_of_per_secid_lists(tmp_path):
    """Two secids with overlapping and distinct permno/gvkey/ticker values,
    including a genuinely unresolved secid (all-null row) -- the returned
    lists must be the deduplicated, sorted UNION across every row, exactly
    matching what resolve_universe() itself would have returned as
    all_permnos/all_gvkeys/all_tickers for the same underlying data."""
    path = tmp_path / "resolved_universe_ids.parquet"
    pd.DataFrame({
        "secid": [1, 2, 3],
        "cusip": ["A", "B", "C"],
        "ticker": ["AAA", "BBB", "CCC"],
        "issuer": ["A CORP", "B CORP", "C CORP"],
        "permno": ["100,200", "200", None],
        "gvkey": ["001000", "001000,002000", None],
        "ibes_ticker": ["AAA", None, None],
    }).to_parquet(path, index=False)

    permnos, gvkeys, ibes_tickers = lib.load_resolved_filter_lists(path)

    assert permnos == [100, 200]
    assert gvkeys == ["001000", "002000"]
    assert ibes_tickers == ["AAA"]


def test_load_resolved_filter_lists_all_empty_when_family_skipped(tmp_path):
    """If an identifier family was skipped upstream (e.g. GVKEY resolution
    because crsp.ccmxpf_lnkhist was unavailable), every row's gvkey column
    is null -- load_resolved_filter_lists must come back with an empty list
    for that family, not raise or fabricate a value."""
    path = tmp_path / "resolved_universe_ids.parquet"
    pd.DataFrame({
        "secid": [1],
        "cusip": ["A"],
        "ticker": ["AAA"],
        "issuer": ["A CORP"],
        "permno": ["100"],
        "gvkey": [None],
        "ibes_ticker": [None],
    }).to_parquet(path, index=False)

    permnos, gvkeys, ibes_tickers = lib.load_resolved_filter_lists(path)

    assert permnos == [100]
    assert gvkeys == []
    assert ibes_tickers == []


def test_load_resolved_filter_lists_missing_expected_column_raises(tmp_path):
    path = tmp_path / "resolved_universe_ids.parquet"
    pd.DataFrame({"secid": [1], "permno": ["1"]}).to_parquet(path, index=False)
    with pytest.raises(lib.UniverseResolutionError):
        lib.load_resolved_filter_lists(path)


def test_max_concurrent_connections_is_a_small_positive_int():
    # A named, easy-to-adjust constant, not a magic number -- but it must
    # still be a sane positive value (real concurrency, not accidentally 0
    # or negative, and not so large it risks tripping a WRDS connection
    # limit).
    assert isinstance(lib.MAX_CONCURRENT_CONNECTIONS, int)
    assert 1 <= lib.MAX_CONCURRENT_CONNECTIONS <= 10


def test_parse_common_args_rejects_unrecognized_flag():
    with pytest.raises(SystemExit):
        lib.parse_common_args(["--Force"])


# ---------------------------------------------------------------------------
# Finding 6: load_resolved_filter_lists round-tripped through REAL
# resolve_universe() output (not a hand-built clean fixture), including a
# fixture where an input column is genuinely float64-typed -- the exact
# dtype-round-trip bug class that already bit GVKEY once.
# ---------------------------------------------------------------------------


def test_load_resolved_filter_lists_round_trip_through_real_resolve_universe_with_float_typed_source_columns(tmp_path):
    """Every other load_resolved_filter_lists test hand-constructs a clean
    Parquet file directly -- never runs it through the REAL resolve_universe()
    first. Here: crsp.stocknames' permno column, crsp.ccmxpf_lnkhist's
    lpermno column, and wrdsapps.ibcrsphist's permno column are all
    genuinely float64-typed (pandas infers this from a null elsewhere in
    each column -- the same real-world shape that produced the float64
    GVKEY column that bit this codebase before resolve_universe's
    _normalize_gvkeys fix). resolve_universe() itself must still recover
    clean ints from these, and load_resolved_filter_lists() must then read
    that real Parquet output back correctly -- not a hand-built fixture."""
    om = pd.DataFrame({
        "secid": [1, 2], "cusip": ["00000001", "00000002"],
        "ticker": ["AAA", "BBB"], "issuer": ["AAA CORP", "BBB CORP"],
    })
    om_hist = pd.DataFrame({"secid": [], "cusip": []})
    stocknames = pd.DataFrame({
        # float64-typed: pandas infers this from the None in the third,
        # non-matching row.
        "permno": [100, 200, None],
        "cusip": ["00000001", "00000002", "99999999"],
        "ncusip": ["00000001", "00000002", "99999999"],
    })
    assert stocknames["permno"].dtype == "float64", (
        "sanity check: this test is only meaningful if the source column "
        "really is float64"
    )
    ccmxpf = pd.DataFrame({
        "lpermno": pd.array([100.0, 200.0, None], dtype="float64"),
        "gvkey": ["001000", "002000", None],
    })
    ibcrsphist = pd.DataFrame({
        "permno": pd.array([100.0, 200.0, None], dtype="float64"),
        "ticker": ["AAA", "BBB", None],
    })

    resolved_out = tmp_path / "metadata" / "resolved_universe_ids.parquet"
    _resolve_universe_fixture(
        tmp_path, om=om, om_hist=om_hist, stocknames=stocknames,
        ccmxpf=ccmxpf, ibcrsphist=ibcrsphist,
    )

    permnos, gvkeys, ibes_tickers = lib.load_resolved_filter_lists(resolved_out)

    assert permnos == [100, 200], (
        f"PERMNOs must survive the real resolve_universe() -> Parquet -> "
        f"load_resolved_filter_lists() round trip as clean ints even when "
        f"a SOURCE column was float64-typed -- got {permnos!r}"
    )
    assert gvkeys == ["001000", "002000"]
    assert ibes_tickers == ["AAA", "BBB"]


def test_load_resolved_filter_lists_malformed_cell_raises_clear_actionable_error_not_raw_traceback(tmp_path):
    """Finding 6: a malformed cell (here, a non-numeric token in the permno
    column -- something a real resolve_universe() run should never
    produce, but a hand-edited or corrupted file could) must raise a clear,
    actionable UniverseResolutionError naming this exact remediation step,
    not a bare ValueError/traceback from deep inside int()."""
    path = tmp_path / "resolved_universe_ids.parquet"
    pd.DataFrame({
        "secid": [1], "permno": ["not-a-number"], "gvkey": ["001000"],
        "ibes_ticker": ["AAA"],
    }).to_parquet(path, index=False)

    with pytest.raises(lib.UniverseResolutionError) as exc_info:
        lib.load_resolved_filter_lists(path)
    message = str(exc_info.value)
    assert "malformed" in message.lower()
    assert "pull_wrds_reference.py" in message


# ---------------------------------------------------------------------------
# Finding 7: per-script manifest files (chosen fix over a cross-process
# fcntl lock -- see manifest_path_for's docstring for the reasoning). Since
# there is no shared file at all, the test burden is simple: confirm each
# component gets its own distinct path, and that writes to two different
# components' paths never land in the same file.
# ---------------------------------------------------------------------------


def test_manifest_path_for_returns_distinct_paths_for_each_component(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "METADATA_DIR", tmp_path)
    components = ["reference", "crsp", "compustat", "ibes"]
    paths = {c: lib.manifest_path_for(c) for c in components}
    assert len(set(paths.values())) == 4, "each component must get its own manifest file"
    for component, path in paths.items():
        assert path.name == f"wrds_pull_manifest_{component}.csv"
        assert path.parent == tmp_path


def test_separate_manifest_files_do_not_cross_contaminate(tmp_path, monkeypatch):
    """Would fail if Finding 7's fix were removed/reverted to one shared
    MANIFEST_PATH: two different "scripts" (simulated here by calling
    record() against two different components' manifest paths, the same
    way two real separate OS processes each running their own script would)
    must never have their rows land in the same file."""
    monkeypatch.setattr(lib, "METADATA_DIR", tmp_path)
    crsp_path = lib.manifest_path_for("crsp")
    compustat_path = lib.manifest_path_for("compustat")

    base_row = {
        "rows": 1, "path": "", "missing_columns": "", "error": "",
        "start_date": "", "end_date": "",
    }
    lib.record([], {**base_row, "library": "crsp", "table": "dsf", "status": "ok"}, manifest_path=crsp_path)
    lib.record([], {**base_row, "library": "comp", "table": "fundq", "status": "ok"}, manifest_path=compustat_path)

    crsp_df = pd.read_csv(crsp_path)
    compustat_df = pd.read_csv(compustat_path)
    assert len(crsp_df) == 1
    assert crsp_df.iloc[0]["library"] == "crsp"
    assert len(compustat_df) == 1
    assert compustat_df.iloc[0]["library"] == "comp"


# ---------------------------------------------------------------------------
# Finding 4: library-alias-resolution persistence (write_library_availability
# / load_library_availability), so pull_wrds_compustat.py can read the
# ACTUAL resolved Compustat library alias instead of assuming "comp".
# ---------------------------------------------------------------------------


def test_write_then_load_library_availability_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "METADATA_DIR", tmp_path)
    lib.write_library_availability({
        "have_ibes": True, "have_crsp": True, "have_comp": True, "comp_lib": "compa",
    })
    info = lib.load_library_availability()
    assert info == {
        "have_ibes": True, "have_crsp": True, "have_comp": True, "comp_lib": "compa",
    }


def test_load_library_availability_missing_file_raises_systemexit_with_clear_message(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "METADATA_DIR", tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        lib.load_library_availability()
    assert "pull_wrds_reference.py" in str(exc_info.value)


def test_load_library_availability_malformed_json_raises_systemexit(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "METADATA_DIR", tmp_path)
    lib.library_availability_path().parent.mkdir(parents=True, exist_ok=True)
    lib.library_availability_path().write_text("not valid json {")
    with pytest.raises(SystemExit):
        lib.load_library_availability()


# ---------------------------------------------------------------------------
# Finding 8: documented, not silently discovered, real total peak concurrent
# connection count across all 3 consumer scripts running simultaneously.
# ---------------------------------------------------------------------------


def test_documented_peak_total_connections_matches_the_three_scripts_real_peak_usage():
    # crsp: min(MAX_CONCURRENT_CONNECTIONS, 22 tasks); compustat: min(.., 2);
    # ibes: min(.., 3) -- guards against this documented number silently
    # drifting out of sync with the per-script worker-count logic.
    crsp_peak = min(lib.MAX_CONCURRENT_CONNECTIONS, 22)
    compustat_peak = min(lib.MAX_CONCURRENT_CONNECTIONS, 2)
    ibes_peak = min(lib.MAX_CONCURRENT_CONNECTIONS, 3)
    assert lib.DOCUMENTED_PEAK_TOTAL_CONNECTIONS == crsp_peak + compustat_peak + ibes_peak


# ---------------------------------------------------------------------------
# Fix 1 (round 3 review): provenance-aware re-pull, replacing the prior
# round's destructive "delete every existing dsf_<year>.parquet chunk up
# front on --force" sweep (a data-destruction regression -- see
# pull_wrds_crsp.py's module docstring). compute_universe_fingerprint,
# last_manifest_fingerprint_for_path, and determine_chunk_force are the
# three new pieces this mechanism is built from.
# ---------------------------------------------------------------------------


def test_compute_universe_fingerprint_stable_for_identical_bytes(tmp_path):
    path = tmp_path / "resolved_universe_ids.parquet"
    path.write_bytes(b"identical-content")
    assert (
        lib.compute_universe_fingerprint(path)
        == lib.compute_universe_fingerprint(path)
    )


def test_compute_universe_fingerprint_differs_for_different_bytes(tmp_path):
    path_a = tmp_path / "a.parquet"
    path_b = tmp_path / "b.parquet"
    path_a.write_bytes(b"universe-version-one")
    path_b.write_bytes(b"universe-version-two")
    assert (
        lib.compute_universe_fingerprint(path_a)
        != lib.compute_universe_fingerprint(path_b)
    )


def test_compute_universe_fingerprint_defaults_to_resolved_ids_path(tmp_path, monkeypatch):
    resolved_path = tmp_path / "resolved_universe_ids.parquet"
    resolved_path.write_bytes(b"default-path-content")
    monkeypatch.setattr(lib, "RESOLVED_IDS_PATH", resolved_path)
    assert lib.compute_universe_fingerprint() == lib.compute_universe_fingerprint(resolved_path)


def test_last_manifest_fingerprint_for_path_returns_none_when_manifest_missing(tmp_path):
    assert lib.last_manifest_fingerprint_for_path(tmp_path / "no_such_manifest.csv", "some/path") is None


def test_last_manifest_fingerprint_for_path_returns_none_when_no_matching_row(tmp_path):
    manifest_path = tmp_path / "manifest.csv"
    lib.append_manifest_row(
        {"library": "crsp", "table": "dsf", "status": "ok", "rows": 1,
         "path": "some/other/path.parquet", "missing_columns": "",
         "optional_columns_missing": "", "error": "", "start_date": "", "end_date": "",
         "universe_fingerprint": "fp-1"},
        manifest_path=manifest_path,
    )
    assert lib.last_manifest_fingerprint_for_path(manifest_path, "target/path.parquet") is None


def test_last_manifest_fingerprint_for_path_returns_none_when_field_blank(tmp_path):
    """A row with a BLANK universe_fingerprint (e.g. one written before this
    field existed, or via a call site that never passed one -- see
    pull_table's `universe_fingerprint or ""` default) must read back as
    None (no provenance), not as the literal empty string."""
    manifest_path = tmp_path / "manifest.csv"
    target = "target/dsf_2000.parquet"
    lib.append_manifest_row(
        {"library": "crsp", "table": "dsf", "status": "ok", "rows": 1,
         "path": target, "missing_columns": "", "optional_columns_missing": "",
         "error": "", "start_date": "", "end_date": "",
         "universe_fingerprint": ""},
        manifest_path=manifest_path,
    )
    assert lib.last_manifest_fingerprint_for_path(manifest_path, target) is None


def test_last_manifest_fingerprint_for_path_returns_most_recent_of_multiple_rows(tmp_path):
    manifest_path = tmp_path / "manifest.csv"
    target = "target/dsf_2000.parquet"
    for fingerprint in ("fp-old", "fp-newer", "fp-newest"):
        lib.append_manifest_row(
            {"library": "crsp", "table": "dsf", "status": "ok", "rows": 1,
             "path": target, "missing_columns": "", "optional_columns_missing": "",
             "error": "", "start_date": "", "end_date": "",
             "universe_fingerprint": fingerprint},
            manifest_path=manifest_path,
        )
    assert lib.last_manifest_fingerprint_for_path(manifest_path, target) == "fp-newest"


def test_determine_chunk_force_true_when_requested_force_regardless_of_everything_else(tmp_path):
    out_path = tmp_path / "raw" / "crsp" / "dsf_2000.parquet"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"x")
    manifest_path = tmp_path / "manifest.csv"
    lib.append_manifest_row(
        {"library": "crsp", "table": "dsf", "status": "ok", "rows": 1,
         "path": str(out_path), "missing_columns": "", "optional_columns_missing": "",
         "error": "", "start_date": "", "end_date": "",
         "universe_fingerprint": "same-fingerprint"},
        manifest_path=manifest_path,
    )
    assert lib.determine_chunk_force(
        out_path, manifest_path, universe_fingerprint="same-fingerprint", requested_force=True,
    ) is True


def test_determine_chunk_force_false_when_file_does_not_exist(tmp_path):
    out_path = tmp_path / "raw" / "crsp" / "dsf_2000.parquet"
    manifest_path = tmp_path / "manifest.csv"
    assert lib.determine_chunk_force(
        out_path, manifest_path, universe_fingerprint="fp", requested_force=False,
    ) is False


def test_determine_chunk_force_true_when_no_manifest_history(tmp_path):
    out_path = tmp_path / "raw" / "crsp" / "dsf_2000.parquet"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"x")
    manifest_path = tmp_path / "manifest.csv"  # never created
    assert lib.determine_chunk_force(
        out_path, manifest_path, universe_fingerprint="fp", requested_force=False,
    ) is True


def test_determine_chunk_force_true_when_fingerprint_mismatches(tmp_path):
    out_path = tmp_path / "raw" / "crsp" / "dsf_2000.parquet"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"x")
    manifest_path = tmp_path / "manifest.csv"
    lib.append_manifest_row(
        {"library": "crsp", "table": "dsf", "status": "ok", "rows": 1,
         "path": str(out_path), "missing_columns": "", "optional_columns_missing": "",
         "error": "", "start_date": "", "end_date": "",
         "universe_fingerprint": "old-fingerprint"},
        manifest_path=manifest_path,
    )
    assert lib.determine_chunk_force(
        out_path, manifest_path, universe_fingerprint="new-fingerprint", requested_force=False,
    ) is True


def test_determine_chunk_force_false_when_fingerprint_matches(tmp_path):
    out_path = tmp_path / "raw" / "crsp" / "dsf_2000.parquet"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"x")
    manifest_path = tmp_path / "manifest.csv"
    lib.append_manifest_row(
        {"library": "crsp", "table": "dsf", "status": "ok", "rows": 1,
         "path": str(out_path), "missing_columns": "", "optional_columns_missing": "",
         "error": "", "start_date": "", "end_date": "",
         "universe_fingerprint": "same-fingerprint"},
        manifest_path=manifest_path,
    )
    assert lib.determine_chunk_force(
        out_path, manifest_path, universe_fingerprint="same-fingerprint", requested_force=False,
    ) is False


def test_pull_table_kept_existing_carries_forward_prior_fingerprint_not_current(tmp_path):
    """Regression test for the "stale fingerprint stamped on kept-existing
    file" bug: a chunk is pulled under universe A (fingerprint A recorded
    for out_path). The universe then changes to B; a fingerprint-mismatch-
    triggered re-pull attempt under B legitimately returns zero rows, so
    pull_table correctly leaves the existing (universe-A) file untouched
    and logs "ok_empty_query_kept_existing". That manifest row must NOT be
    stamped with B's fingerprint -- if it were, a THIRD run still under
    universe B would see a (false) fingerprint match against B and skip
    re-pulling forever, permanently freezing universe-A data under a
    fingerprint that no longer describes it.
    """
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    out_path = out_dir / "dsf_2005.parquet"
    original_bytes = b"UNIVERSE-A-DATA"
    out_path.write_bytes(original_bytes)
    manifest_path = tmp_path / "manifest.csv"

    fingerprint_a = "fingerprint-universe-a"
    fingerprint_b = "fingerprint-universe-b"

    # Run 1 (already happened): universe A's pull recorded fingerprint A for
    # this exact out_path.
    lib.append_manifest_row(
        {"library": "crsp", "table": "dsf", "status": "ok", "rows": 100,
         "path": lib._rel(out_path), "missing_columns": "",
         "optional_columns_missing": "", "error": "", "start_date": "",
         "end_date": "", "universe_fingerprint": fingerprint_a},
        manifest_path=manifest_path,
    )

    # Sanity check: before run 2, universe B's fingerprint correctly
    # mismatches fingerprint A -- a re-pull is (correctly) requested.
    assert lib.determine_chunk_force(
        out_path, manifest_path, universe_fingerprint=fingerprint_b, requested_force=False,
    ) is True

    # Run 2: the fingerprint-mismatch-triggered re-pull attempt under
    # universe B legitimately returns zero rows.
    empty_chunk = pd.DataFrame({"permno": pd.Series([], dtype="int64"),
                                 "date": pd.Series([], dtype="object")})
    db = StubDB(columns=["permno", "date"], chunks=[empty_chunk])
    log = []
    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"], out_name="dsf_2005",
        raw_dir=raw_dir, manifest_path=manifest_path,
        stream=True, force=True, universe_fingerprint=fingerprint_b,
    )

    assert log[0]["status"] == "ok_empty_query_kept_existing"
    assert out_path.read_bytes() == original_bytes

    # The critical assertion: the manifest row for this untouched file must
    # NOT carry universe B's fingerprint (that would misrepresent universe-A
    # data as confirmed-current-under-B).
    assert log[0]["universe_fingerprint"] != fingerprint_b

    # Run 3: a subsequent run still under universe B must NOT see a
    # fingerprint match -- it must attempt to re-pull this chunk again,
    # not silently skip it forever.
    assert lib.determine_chunk_force(
        out_path, manifest_path, universe_fingerprint=fingerprint_b, requested_force=False,
    ) is True


# ---------------------------------------------------------------------------
# Fix 2 (round 3 review): a substantive, universe-filtered table returning
# zero rows with NO exception and NO pre-existing file is essentially
# always a bug (a disconnected filter, a wrong join, a shifted date column)
# -- callers opt in via zero_rows_is_suspicious=True. Distinct from the
# "kept existing" tests above (which fire only when out_path already
# exists, regardless of this flag).
# ---------------------------------------------------------------------------


def test_pull_table_streaming_zero_rows_without_existing_file_is_suspicious_when_flagged(tmp_path):
    empty_chunk = pd.DataFrame({"permno": pd.Series([], dtype="int64"),
                                 "date": pd.Series([], dtype="object")})
    db = StubDB(columns=["permno", "date"], chunks=[empty_chunk])
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        stream=True, zero_rows_is_suspicious=True,
    )

    assert log[0]["status"] == "ok_suspicious_zero_rows"
    assert log[0]["rows"] == 0
    assert not (tmp_path / "raw" / "crsp" / "dsf.parquet").exists()


def test_pull_table_non_streaming_zero_rows_without_existing_file_is_suspicious_when_flagged(tmp_path):
    db = StubDB(
        columns=["permno"],
        rows=pd.DataFrame({"permno": pd.Series([], dtype="int64")}),
    )
    log = []

    lib.pull_table(
        db, log, "crsp", "dsedelist", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        zero_rows_is_suspicious=True,
    )

    assert log[0]["status"] == "ok_suspicious_zero_rows"
    assert log[0]["rows"] == 0


def test_pull_table_zero_rows_without_flag_defaults_to_plain_ok_not_suspicious(tmp_path):
    """zero_rows_is_suspicious defaults to False -- existing callers that
    don't pass it (e.g. crsp.dsi, the small Phase A reference tables) must
    be unaffected."""
    empty_chunk = pd.DataFrame({"permno": pd.Series([], dtype="int64")})
    db = StubDB(columns=["permno"], chunks=[empty_chunk])
    log = []

    lib.pull_table(
        db, log, "crsp", "dsi", ["permno"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        stream=True,
    )

    assert log[0]["status"] == "ok"


def test_pull_table_zero_rows_with_existing_file_is_not_suspicious_even_when_flagged(tmp_path):
    """ok_suspicious_zero_rows is a DIFFERENT scenario from
    ok_empty_query_kept_existing -- when out_path already exists, the
    existing "kept existing" status takes precedence regardless of
    zero_rows_is_suspicious."""
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    out_path = out_dir / "dsf.parquet"
    out_path.write_bytes(b"STALE-BUT-STILL-GOOD")

    empty_chunk = pd.DataFrame({"permno": pd.Series([], dtype="int64"),
                                 "date": pd.Series([], dtype="object")})
    db = StubDB(columns=["permno", "date"], chunks=[empty_chunk])
    log = []

    lib.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        stream=True, force=True, zero_rows_is_suspicious=True,
    )

    assert log[0]["status"] == "ok_empty_query_kept_existing"


def test_ok_suspicious_zero_rows_is_not_a_success_status():
    assert "ok_suspicious_zero_rows" not in lib.SUCCESS_STATUSES
    failures = lib.find_run_log_failures([
        {"status": "ok_suspicious_zero_rows"},
        {"status": "ok"},
    ])
    assert len(failures) == 1
    assert failures[0]["status"] == "ok_suspicious_zero_rows"


def test_library_unavailable_is_a_success_status():
    """Fix 3 (round 3 review): "library_unavailable" must be excluded from
    find_run_log_failures -- a library simply not being visible on this
    account is not a failure of any pull that was actually attempted."""
    assert "library_unavailable" in lib.SUCCESS_STATUSES
    failures = lib.find_run_log_failures([
        {"status": "library_unavailable"},
        {"status": "ok"},
    ])
    assert failures == []


# ---------------------------------------------------------------------------
# Lower-priority fix (round 3 review): append_manifest_row/record's
# manifest_path is now a REQUIRED parameter -- omitting it must raise
# immediately instead of silently falling back to the vestigial shared
# lib.MANIFEST_PATH constant.
# ---------------------------------------------------------------------------


def test_append_manifest_row_requires_manifest_path_argument():
    with pytest.raises(TypeError):
        lib.append_manifest_row({"library": "crsp", "table": "dsf", "status": "ok"})


def test_append_manifest_row_rejects_explicit_none_manifest_path():
    with pytest.raises(ValueError):
        lib.append_manifest_row({"library": "crsp", "table": "dsf", "status": "ok"}, manifest_path=None)


def test_record_requires_manifest_path_argument():
    with pytest.raises(TypeError):
        lib.record([], {"library": "crsp", "table": "dsf", "status": "ok"})
