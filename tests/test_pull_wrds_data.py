"""Unit tests for scripts/pull_wrds_data.py -- only the parts that don't need
a real WRDS connection: load_env_file, pull_table's required/optional column
handling and idempotency logic against a stub `db` object, the schema-pinned
streaming writer, and manifest error sanitization.
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
    assert "optional_columns_missing" in manifest.columns
    assert db.raw_sql_calls == 1


def test_pull_table_missing_required_column_blocks_pull(tmp_path):
    db = StubDB(
        columns=["permno", "date"],  # "ret" missing
        rows=pd.DataFrame({"permno": [1], "date": ["2000-01-01"]}),
    )
    log = []

    pwd.pull_table(
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

    pwd.pull_table(
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


def test_pull_table_all_required_columns_missing_blocks_pull(tmp_path):
    db = StubDB(columns=["unrelated_col"], rows=pd.DataFrame())
    log = []

    pwd.pull_table(
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

    pwd.pull_table(
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

    pwd.pull_table(
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

    pwd.pull_table(
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

    pwd.pull_table(
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

    pwd.pull_table(
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

    pwd.pull_table(
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

    pwd.pull_table(
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

    pwd.pull_table(
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
    assert pwd._sql_type_to_pyarrow_type("INTEGER") == pa.int64()
    assert pwd._sql_type_to_pyarrow_type("BIGINT") == pa.int64()
    assert pwd._sql_type_to_pyarrow_type("SMALLINT") == pa.int64()
    assert pwd._sql_type_to_pyarrow_type("integer") == pa.int64()  # case-insensitive

    # Floating/numeric.
    assert pwd._sql_type_to_pyarrow_type("DOUBLE PRECISION") == pa.float64()
    assert pwd._sql_type_to_pyarrow_type("REAL") == pa.float64()
    assert pwd._sql_type_to_pyarrow_type("NUMERIC") == pa.float64()
    assert pwd._sql_type_to_pyarrow_type("NUMERIC(10, 2)") == pa.float64()  # precision stripped
    assert pwd._sql_type_to_pyarrow_type("DECIMAL") == pa.float64()

    # Date/time -- deliberately still pa.string(), matching this script's
    # existing (out-of-scope-to-change) behavior of writing dates as ISO
    # strings.
    assert pwd._sql_type_to_pyarrow_type("DATE") == pa.string()
    assert pwd._sql_type_to_pyarrow_type("TIMESTAMP") == pa.string()
    assert pwd._sql_type_to_pyarrow_type("TIMESTAMP WITHOUT TIME ZONE") == pa.string()

    # Text.
    assert pwd._sql_type_to_pyarrow_type("VARCHAR") == pa.string()
    assert pwd._sql_type_to_pyarrow_type("VARCHAR(32)") == pa.string()
    assert pwd._sql_type_to_pyarrow_type("CHARACTER VARYING") == pa.string()
    assert pwd._sql_type_to_pyarrow_type("TEXT") == pa.string()
    assert pwd._sql_type_to_pyarrow_type("CHAR(3)") == pa.string()

    # Boolean.
    assert pwd._sql_type_to_pyarrow_type("BOOLEAN") == pa.bool_()

    # Unmapped -> sane fallback, logged (not silently mishandled).
    assert pwd._sql_type_to_pyarrow_type("SOME_EXOTIC_TYPE") == pa.string()
    assert pwd._sql_type_to_pyarrow_type(None) == pa.string()


def test_sql_type_to_pyarrow_type_warns_on_unmapped_type(capsys):
    pwd._sql_type_to_pyarrow_type("SOME_EXOTIC_TYPE", column="widget")
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "SOME_EXOTIC_TYPE" in captured.out
    assert "widget" in captured.out


def test_build_pinned_schema_uses_declared_types_for_exactly_the_given_columns():
    column_sql_types = {
        "permno": "INTEGER", "numtrd": "INTEGER", "date": "DATE", "ret": "DOUBLE PRECISION",
    }
    schema = pwd._build_pinned_schema(["permno", "date", "ret"], column_sql_types)
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
    schema_1 = pwd._build_pinned_schema(["permno", "ret"], column_sql_types)
    schema_2 = pwd._build_pinned_schema(["permno", "ret"], column_sql_types)
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
    pinned_schema = pwd._build_pinned_schema(
        ["permno", "numtrd"], {"permno": "INTEGER", "numtrd": "INTEGER"}
    )

    total_rows, wrote_any = pwd._stream_query_to_parquet(
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
    pinned_schema_a = pwd._build_pinned_schema(["permno", "numtrd"], column_sql_types)
    pinned_schema_b = pwd._build_pinned_schema(["permno", "numtrd"], column_sql_types)
    assert pinned_schema_a.equals(pinned_schema_b), (
        "pinned schema must be identical regardless of data -- it's built "
        "purely from declared types before any chunk is seen"
    )

    out_a = tmp_path / "order_a.parquet"
    out_b = tmp_path / "order_b.parquet"
    rows_a, wrote_a = pwd._stream_query_to_parquet(
        FakeDBOrderA(), "crsp", "dsf", ["permno", "numtrd"], "", out_a, pinned_schema_a
    )
    rows_b, wrote_b = pwd._stream_query_to_parquet(
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
    pinned_schema = pwd._build_pinned_schema(
        ["permno", "date"], {"permno": "INTEGER", "date": "DATE"}
    )
    total_rows, wrote_any = pwd._stream_query_to_parquet(
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
    pinned_schema = pwd._build_pinned_schema(
        ["permno", "date"], {"permno": "INTEGER", "date": "DATE"}
    )
    total_rows, wrote_any = pwd._stream_query_to_parquet(
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
    source = inspect.getsource(pwd._stream_query_to_parquet)
    _docstring_parts = source.split('"""')
    code_only = _docstring_parts[0] + "".join(_docstring_parts[2:])
    assert "concat_tables(" not in code_only
    assert "replace_schema_metadata(" not in code_only
    assert "pending" not in code_only
    assert not hasattr(pwd, "_widen_integers_to_float64")
    assert not hasattr(pwd, "_merge_schema_resolving_nulls")
    assert not hasattr(pwd, "_SCHEMA_RESOLUTION_CHUNK_CAP")


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

    pinned_schema = pwd._build_pinned_schema(
        ["permno", "date"], {"permno": "INTEGER", "date": "DATE"}
    )
    with pytest.raises(KeyboardInterrupt):
        pwd._stream_query_to_parquet(
            FakeDB(), "crsp", "dsf", ["permno", "date"], "", out_path, pinned_schema
        )

    assert not tmp_marker_path.exists()
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# Fix 4 (this round): manifest error-text sanitization.
# ---------------------------------------------------------------------------


def test_sanitize_error_text_truncates_long_text():
    long_text = "x" * 1000
    result = pwd._sanitize_error_text(long_text)
    assert len(result) == pwd._MAX_ERROR_LEN


def test_sanitize_error_text_redacts_wrds_username(monkeypatch):
    monkeypatch.setenv("WRDS_USERNAME", "jdoe123")
    text = 'FATAL: password authentication failed for user "jdoe123" at host db.wrds.upenn.edu'
    result = pwd._sanitize_error_text(text)
    assert "jdoe123" not in result
    assert "[REDACTED_WRDS_USERNAME]" in result


def test_sanitize_error_text_noop_without_username_set(monkeypatch):
    monkeypatch.delenv("WRDS_USERNAME", raising=False)
    text = "some ordinary error message"
    assert pwd._sanitize_error_text(text) == text


def test_sanitize_error_text_handles_empty_string():
    assert pwd._sanitize_error_text("") == ""
    assert pwd._sanitize_error_text(None) is None


def test_append_manifest_row_sanitizes_error_field_before_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("WRDS_USERNAME", "secretuser99")
    manifest_path = tmp_path / "manifest.csv"

    pwd.append_manifest_row(
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
    assert len(error_text) <= pwd._MAX_ERROR_LEN


def test_pull_table_query_failed_error_is_sanitized_in_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("WRDS_USERNAME", "myuser42")

    class FakeDB:
        def describe_table(self, library, table):
            return pd.DataFrame({"name": ["permno"], "type": ["INTEGER"]})

        def raw_sql(self, sql, return_iter=False):
            raise RuntimeError('auth failed for user "myuser42" host=db.example.com')

    log = []
    manifest_path = tmp_path / "manifest.csv"

    pwd.pull_table(
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


# ---------------------------------------------------------------------------
# main()'s per-table required/optional column lists, and the test fixture
# that mirrors them.
#
# Rather than hand-copying a second "full column set" list here (the exact
# duplication that already drifted out of sync once), TABLE_SPECS below
# imports the SAME per-table required/optional column-list constants main()
# itself uses. There is exactly one place any column is ever added to or
# removed from a given table's pull, and both main() and this test suite
# read it from there, so this fixture cannot drift out of sync with main()
# the way an old hand-maintained duplicate list did.
# ---------------------------------------------------------------------------

TABLE_SPECS = [
    ("ibes", "statsumu_epsus", pwd.IBES_STATSUMU_EPSUS_REQUIRED_COLUMNS, pwd.IBES_STATSUMU_EPSUS_OPTIONAL_COLUMNS),
    ("ibes", "actu_epsus", pwd.IBES_ACTU_EPSUS_REQUIRED_COLUMNS, pwd.IBES_ACTU_EPSUS_OPTIONAL_COLUMNS),
    ("ibes", "idsum", pwd.IBES_IDSUM_REQUIRED_COLUMNS, pwd.IBES_IDSUM_OPTIONAL_COLUMNS),
    ("ibes", "detu_epsus", pwd.IBES_DETU_EPSUS_REQUIRED_COLUMNS, pwd.IBES_DETU_EPSUS_OPTIONAL_COLUMNS),
    ("crsp", "dsf", pwd.CRSP_DSF_REQUIRED_COLUMNS, pwd.CRSP_DSF_OPTIONAL_COLUMNS),
    ("crsp", "dsi", pwd.CRSP_DSI_REQUIRED_COLUMNS, pwd.CRSP_DSI_OPTIONAL_COLUMNS),
    ("crsp", "dsedelist", pwd.CRSP_DSEDELIST_REQUIRED_COLUMNS, pwd.CRSP_DSEDELIST_OPTIONAL_COLUMNS),
    ("crsp", "stocknames", pwd.CRSP_STOCKNAMES_REQUIRED_COLUMNS, pwd.CRSP_STOCKNAMES_OPTIONAL_COLUMNS),
    ("comp", "fundq", pwd.COMP_FUNDQ_REQUIRED_COLUMNS, pwd.COMP_FUNDQ_OPTIONAL_COLUMNS),
    ("comp", "company", pwd.COMP_COMPANY_REQUIRED_COLUMNS, pwd.COMP_COMPANY_OPTIONAL_COLUMNS),
    ("comp", "funda", pwd.COMP_FUNDA_REQUIRED_COLUMNS, pwd.COMP_FUNDA_OPTIONAL_COLUMNS),
    ("wrdsapps", "ibcrsphist", pwd.WRDSAPPS_IBCRSPHIST_REQUIRED_COLUMNS, pwd.WRDSAPPS_IBCRSPHIST_OPTIONAL_COLUMNS),
    ("crsp", "ccmxpf_lnkhist", pwd.CRSP_CCMXPF_LNKHIST_REQUIRED_COLUMNS, pwd.CRSP_CCMXPF_LNKHIST_OPTIONAL_COLUMNS),
]

# Every one of main()'s 13 pull_table() call sites must appear here exactly
# once -- this assertion catches a NEW call site being added to main()
# without a matching entry being added here, which is exactly the kind of
# drift this fixture exists to prevent.
assert len(TABLE_SPECS) == 13

# Union of every column name (required OR optional) requested by ANY
# pull_table(...) call in main(), derived (not hand-copied) from
# TABLE_SPECS above, so a FakeMainDB with the full set never spuriously
# triggers "missing_required_columns" for a table under test unless a test
# deliberately removes one column.
FULL_COLUMN_SET = set().union(
    *(set(req) | set(opt) for _lib, _tbl, req, opt in TABLE_SPECS)
)


class FakeMainDB:
    """Stand-in for the whole wrds.Connection used by main(), not just one
    table. Tracks every raw_sql call (which library.table, the full SQL
    text, whether it was a streaming call) so a test can assert which
    tables got queried, with what WHERE clause, how many times.
    """

    def __init__(
        self,
        libraries=("ibes", "crsp", "comp", "wrdsapps"),
        missing_columns_by_table=None,
        chunks_by_table=None,
        raise_after_n_chunks_by_table=None,
    ):
        self.libraries = list(libraries)
        self.missing_columns_by_table = missing_columns_by_table or {}
        self.chunks_by_table = chunks_by_table or {}
        self.raise_after_n_chunks_by_table = raise_after_n_chunks_by_table or {}
        self.calls = []
        self.closed = False

    def list_libraries(self):
        return self.libraries

    def describe_table(self, library, table):
        missing = self.missing_columns_by_table.get(f"{library}.{table}", set())
        cols = sorted(FULL_COLUMN_SET - set(missing))
        # See StubDB.describe_table's comment -- real describe_table() always
        # has a "type" column too; "VARCHAR" for everything is fine here
        # since these tests assert status/SQL text, not pinned-schema dtypes.
        return pd.DataFrame({"name": cols, "type": ["VARCHAR"] * len(cols)})

    def raw_sql(self, sql, return_iter=False):
        match = re.search(r"FROM (\S+)", sql)
        key = match.group(1) if match else "?"
        self.calls.append({"key": key, "sql": sql, "return_iter": return_iter})
        if return_iter:
            chunks = self.chunks_by_table.get(key)
            if chunks is None:
                # Build a default one-row chunk containing exactly the
                # columns THIS query actually selected, not a fixed
                # ["permno", "date"] -- the pinned-schema streaming path
                # (unlike round 5's data-inferred approach) requires a
                # chunk's columns to match query_columns exactly, since the
                # schema now comes from declared types for those specific
                # columns, built independently of any chunk's content.
                select_list = sql.split("FROM")[0].replace("SELECT", "")
                cols = [c.strip() for c in select_list.split(",") if c.strip()]
                chunks = [pd.DataFrame({c: [1] for c in cols})]
            raise_after = self.raise_after_n_chunks_by_table.get(key)

            def _gen(chunks=chunks, raise_after=raise_after):
                for i, c in enumerate(chunks):
                    if raise_after is not None and i == raise_after:
                        raise RuntimeError(f"simulated mid-stream failure for {key}")
                    yield c

            return _gen()
        return pd.DataFrame({"permno": [1]})

    def close(self):
        self.closed = True


def _patch_main_env(monkeypatch, tmp_path):
    """Point RAW_DIR/METADATA_DIR/MANIFEST_PATH at a tmp_path sandbox so
    main() never touches the real repo's data/ directory during tests, and
    return the paths a test needs to make assertions against.

    Also monkeypatches load_env_file to a no-op: main() unconditionally
    calls load_env_file(REPO_ROOT / ".env") first thing, which would
    otherwise read the developer's actual .env file (real WRDS credentials)
    into the test process's environment. Harmless today since connect() is
    always mocked in these tests, but a unit test shouldn't touch a real
    credentials file at all.
    """
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    manifest_path = metadata_dir / "wrds_pull_manifest.csv"
    monkeypatch.setattr(pwd, "RAW_DIR", raw_dir)
    monkeypatch.setattr(pwd, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(pwd, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(pwd, "load_env_file", lambda path: None)
    return raw_dir, metadata_dir, manifest_path


# ---------------------------------------------------------------------------
# --force flag must actually reach pull_table(), and argparse must reject
# unrecognized flags rather than silently ignoring them.
# ---------------------------------------------------------------------------


def test_main_force_flag_rewires_all_pull_table_calls(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_main_env(monkeypatch, tmp_path)

    # Pre-create an output file for crsp.dsf, as if a prior run already
    # pulled it -- idempotency would normally skip re-querying it.
    (raw_dir / "crsp").mkdir(parents=True)
    (raw_dir / "crsp" / "dsf.parquet").write_bytes(b"stale-marker")

    fake_db = FakeMainDB()
    monkeypatch.setattr(pwd, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py", "--force"])

    pwd.main()

    dsf_calls = [c for c in fake_db.calls if c["key"] == "crsp.dsf"]
    assert len(dsf_calls) == 1, (
        "with --force on the command line, crsp.dsf must be re-queried even "
        "though its output file already existed -- the flag must not be a "
        "no-op"
    )


def test_main_without_force_flag_skips_existing_output(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_main_env(monkeypatch, tmp_path)
    (raw_dir / "crsp").mkdir(parents=True)
    (raw_dir / "crsp" / "dsf.parquet").write_bytes(b"stale-marker")

    fake_db = FakeMainDB()
    monkeypatch.setattr(pwd, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py"])

    pwd.main()

    dsf_calls = [c for c in fake_db.calls if c["key"] == "crsp.dsf"]
    assert len(dsf_calls) == 0
    assert (raw_dir / "crsp" / "dsf.parquet").read_bytes() == b"stale-marker"


def test_parse_args_accepts_force_flag():
    assert pwd._parse_args([]).force is False
    assert pwd._parse_args(["--force"]).force is True


def test_parse_args_rejects_unrecognized_flag():
    # A typo like --Force or -force must error out loudly (argparse exits
    # with SystemExit) rather than being silently ignored -- the old
    # `"--force" in sys.argv` check would have just proceeded as if --force
    # hadn't been passed, skipping every already-pulled table and exiting
    # looking successful.
    with pytest.raises(SystemExit):
        pwd._parse_args(["--Force"])
    with pytest.raises(SystemExit):
        pwd._parse_args(["-force"])


def test_main_unrecognized_flag_errors_out_before_connecting(tmp_path, monkeypatch, capsys):
    _patch_main_env(monkeypatch, tmp_path)
    # connect() is deliberately NOT patched to a working fake here: if
    # argument parsing didn't fail fast, this test would blow up trying to
    # make a real WRDS connection instead of cleanly asserting SystemExit.
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py", "--Force"])

    with pytest.raises(SystemExit):
        pwd.main()


# ---------------------------------------------------------------------------
# Every column in every one of main()'s 13 pull_table() call sites: a full
# run with nothing missing must succeed for all 13 tables. Removing a
# REQUIRED column must block that table specifically. Removing an OPTIONAL
# column must NOT block that table -- it must still succeed, with the
# column noted in optional_columns_missing (Fix 2).
# ---------------------------------------------------------------------------


def test_main_full_column_set_succeeds_for_every_table(tmp_path, monkeypatch):
    """With every column main() currently asks for present in the (fake)
    schema, all 13 pull_table() call sites must succeed -- proving the
    corrected, cross-checked TABLE_SPECS/FULL_COLUMN_SET fixture actually
    matches what main() asks for today.
    """
    _raw_dir, _metadata_dir, manifest_path = _patch_main_env(monkeypatch, tmp_path)

    fake_db = FakeMainDB()
    monkeypatch.setattr(pwd, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py"])

    pwd.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    for library, table, _req, _opt in TABLE_SPECS:
        matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
        assert len(matches) == 1, f"expected exactly one manifest row for {library}.{table}"
        status = matches.iloc[0]["status"]
        assert status == "ok", (
            f"{library}.{table} did not succeed with every currently-defined "
            f"column present (got status {status!r}) -- TABLE_SPECS/"
            f"FULL_COLUMN_SET may have drifted from main()'s real calls, or "
            f"main() itself is broken"
        )
        assert matches.iloc[0]["optional_columns_missing"] == ""


# Every (library, table, required_column) triple across all 13 real call
# sites -- built from TABLE_SPECS itself (not hand-enumerated) so this
# parametrized test automatically covers every REQUIRED column of every
# table, not just a hand-picked subset. Per Fix 2, only REQUIRED columns are
# tested here -- removing an OPTIONAL column must NOT abort the pull (tested
# separately below).
REQUIRED_TABLE_COLUMN_CASES = [
    (library, table, column)
    for library, table, required, _optional in TABLE_SPECS
    for column in required
]


@pytest.mark.parametrize("library,table,column", REQUIRED_TABLE_COLUMN_CASES)
def test_main_missing_any_single_required_column_blocks_that_tables_pull(
    tmp_path, monkeypatch, library, table, column
):
    _raw_dir, _metadata_dir, manifest_path = _patch_main_env(monkeypatch, tmp_path)

    fake_db = FakeMainDB(missing_columns_by_table={f"{library}.{table}": {column}})
    monkeypatch.setattr(pwd, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py"])

    pwd.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(matches) == 1, f"expected exactly one manifest row for {library}.{table}"
    row = matches.iloc[0]
    assert row["status"] == "missing_required_columns", (
        f"{library}.{table} missing REQUIRED column {column!r} produced "
        f"status {row['status']!r} -- every required column's absence must "
        f"always abort the pull rather than silently proceeding crippled"
    )
    # And that table must not have been queried at all.
    assert not any(c["key"] == f"{library}.{table}" for c in fake_db.calls)


# Every (library, table, optional_column) triple across all 13 real call
# sites, proving the flip side of Fix 2: removing an OPTIONAL column must
# NOT abort the pull -- it must still succeed, with the column dropped from
# the query and noted in optional_columns_missing.
OPTIONAL_TABLE_COLUMN_CASES = [
    (library, table, column)
    for library, table, _required, optional in TABLE_SPECS
    for column in optional
]


@pytest.mark.parametrize("library,table,column", OPTIONAL_TABLE_COLUMN_CASES)
def test_main_missing_any_single_optional_column_does_not_block_that_tables_pull(
    tmp_path, monkeypatch, library, table, column
):
    _raw_dir, _metadata_dir, manifest_path = _patch_main_env(monkeypatch, tmp_path)

    fake_db = FakeMainDB(missing_columns_by_table={f"{library}.{table}": {column}})
    monkeypatch.setattr(pwd, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py"])

    pwd.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(matches) == 1, f"expected exactly one manifest row for {library}.{table}"
    row = matches.iloc[0]
    assert row["status"] == "ok", (
        f"{library}.{table} missing OPTIONAL column {column!r} produced "
        f"status {row['status']!r} -- an optional column's absence must "
        f"never abort the pull (Fix 2)"
    )
    assert column in row["optional_columns_missing"].split(",")
    # And the table WAS queried (unlike the required-column case).
    assert any(c["key"] == f"{library}.{table}" for c in fake_db.calls)
    # The dropped column must not appear in the actual query's SELECT list
    # (as a whole column token -- substring containment isn't enough, since
    # e.g. "at" is a substring of "datadate" and "ask" is a substring of
    # "askhi").
    matching_calls = [c for c in fake_db.calls if c["key"] == f"{library}.{table}"]
    for c in matching_calls:
        select_list = c["sql"].split("FROM")[0]
        selected_columns = {tok.strip() for tok in select_list.replace("SELECT", "").split(",")}
        assert column not in selected_columns


# ---------------------------------------------------------------------------
# I/B/E/S detail file must be the unadjusted variant (detu_epsus), matching
# the basis of statsumu_epsus/actu_epsus, not the split-adjusted det_epsus
# (which also carries look-ahead via its adjustment factor).
# ---------------------------------------------------------------------------


def test_main_ibes_detail_pull_uses_unadjusted_table(tmp_path, monkeypatch):
    _patch_main_env(monkeypatch, tmp_path)

    fake_db = FakeMainDB()
    monkeypatch.setattr(pwd, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py"])

    pwd.main()

    queried_keys = {c["key"] for c in fake_db.calls}
    assert "ibes.detu_epsus" in queried_keys, (
        "expected the split-UNADJUSTED detail file (detu_epsus) to match "
        "the basis of statsumu_epsus/actu_epsus"
    )
    assert "ibes.det_epsus" not in queried_keys, (
        "the split-ADJUSTED detail file (det_epsus) uses a different "
        "adjustment basis and carries look-ahead -- must not be pulled"
    )
    assert "value" in pwd.IBES_DETU_EPSUS_REQUIRED_COLUMNS


# ---------------------------------------------------------------------------
# Event-date filters need the RETURN_END_DATE forward buffer too, not just
# the return series (crsp.dsf/dsi/dsedelist).
# ---------------------------------------------------------------------------


def test_main_event_date_filters_use_return_end_date_buffer(tmp_path, monkeypatch):
    _patch_main_env(monkeypatch, tmp_path)

    fake_db = FakeMainDB()
    monkeypatch.setattr(pwd, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_data.py"])

    pwd.main()

    sql_by_key = {c["key"]: c["sql"] for c in fake_db.calls}

    # Event-occurred-by-X filters: must extend to RETURN_END_DATE.
    assert pwd.RETURN_END_DATE in sql_by_key["ibes.actu_epsus"]
    assert pwd.END_DATE not in sql_by_key["ibes.actu_epsus"]

    assert pwd.RETURN_END_DATE in sql_by_key["ibes.detu_epsus"]
    assert pwd.END_DATE not in sql_by_key["ibes.detu_epsus"]

    assert pwd.RETURN_END_DATE in sql_by_key["comp.fundq"]
    assert pwd.END_DATE not in sql_by_key["comp.fundq"]

    # Estimate/observation-dated-by-X filters: intentionally stay at the
    # sample cutoff (END_DATE), not the return buffer -- see reasoning
    # comments in main().
    assert pwd.END_DATE in sql_by_key["ibes.statsumu_epsus"]
    assert pwd.RETURN_END_DATE not in sql_by_key["ibes.statsumu_epsus"]

    assert pwd.END_DATE in sql_by_key["comp.funda"]
    assert pwd.RETURN_END_DATE not in sql_by_key["comp.funda"]


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

    pwd.pull_table(
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
    pwd.pull_table(
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

    pwd.pull_table(
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
        pwd.pull_table(
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
        pwd.pull_table(
            db, log, "crsp; DROP TABLE x; --", "dsf", ["permno"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_pull_table_rejects_unsafe_table_name(tmp_path):
    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    with pytest.raises(ValueError):
        pwd.pull_table(
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
        pwd.pull_table(
            db, log, "crsp", "dsf", ["permno", "date; DROP TABLE x; --"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_pull_table_rejects_unsafe_optional_column_name(tmp_path):
    db = StubDB(columns=["permno"], rows=pd.DataFrame({"permno": [1]}))
    log = []

    with pytest.raises(ValueError):
        pwd.pull_table(
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
        pwd.pull_table(
            ExplodingDB(), log, "crsp; DROP TABLE x; --", "dsf", ["permno"],
            raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        )


def test_sql_identifier_regex_rejects_trailing_newline():
    # `$` in Python regex also matches immediately before a trailing
    # newline, so "dsf\n" would incorrectly pass a "$"-anchored pattern.
    # `\Z` is a true end-of-string anchor and must reject it.
    assert pwd._SQL_IDENTIFIER_RE.match("dsf") is not None
    assert pwd._SQL_IDENTIFIER_RE.match("dsf\n") is None
    with pytest.raises(ValueError):
        pwd._validate_sql_identifier("table", "dsf\n")
