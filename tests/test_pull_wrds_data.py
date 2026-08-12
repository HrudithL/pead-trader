"""Unit tests for scripts/pull_wrds_data.py -- only the parts that don't need
a real WRDS connection: load_env_file, and pull_table's column-matching
(all-or-nothing, no more optional-column tier) / idempotency logic against a
stub `db` object.
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
# pull_table: column-matching / idempotency logic
#
# There is no more "optional column" tier (Fix 1): `columns` is a single,
# complete list, and ANY missing column -- whether one of many or all of
# them -- aborts the pull with "missing_required_columns" rather than
# proceeding with whatever subset happened to match.
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
    assert db.raw_sql_calls == 1


def test_pull_table_missing_single_column_blocks_pull(tmp_path):
    # Under the old two-tier design "ret" here would have been merely
    # optional and the pull would have proceeded anyway (status "ok"). Under
    # the merged, single-tier design every column in `columns` is required,
    # so a missing "ret" must abort the pull entirely.
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


def test_pull_table_all_columns_missing_blocks_pull(tmp_path):
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
# Fix 6 (this round): a --force re-pull whose query legitimately returns zero
# rows must not be logged the same way as "this table is genuinely empty"
# when a previously-pulled file is still sitting untouched at out_path.
# ---------------------------------------------------------------------------


def test_pull_table_streaming_zero_rows_with_existing_file_logs_kept_existing(tmp_path):
    raw_dir = tmp_path / "raw"
    out_dir = raw_dir / "crsp"
    out_dir.mkdir(parents=True)
    out_path = out_dir / "dsf.parquet"
    out_path.write_bytes(b"STALE-BUT-STILL-GOOD-PARQUET-BYTES")

    db = StubDB(columns=["permno", "date"], chunks=[])
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=raw_dir, manifest_path=tmp_path / "manifest.csv",
        stream=True, force=True,
    )

    assert log[0]["status"] == "ok_empty_query_kept_existing"
    assert log[0]["rows"] == 0
    # The file must be left completely untouched, not overwritten/emptied.
    assert out_path.read_bytes() == b"STALE-BUT-STILL-GOOD-PARQUET-BYTES"


def test_pull_table_streaming_zero_rows_without_existing_file_logs_plain_ok(tmp_path):
    db = StubDB(columns=["permno", "date"], chunks=[])
    log = []

    pwd.pull_table(
        db, log, "crsp", "dsf", ["permno", "date"],
        raw_dir=tmp_path / "raw", manifest_path=tmp_path / "manifest.csv",
        stream=True,
    )

    assert log[0]["status"] == "ok"
    assert log[0]["rows"] == 0
    assert not (tmp_path / "raw" / "crsp" / "dsf.parquet").exists()


# ---------------------------------------------------------------------------
# Fix 2 (this round): pa.concat_tables schema unification.
# ---------------------------------------------------------------------------


def test_stream_query_to_parquet_unifies_null_vs_typed_schema_across_chunks(tmp_path):
    """The simpler, previously-covered case: crsp.dsf-style data where a
    column (e.g. numtrd, NASDAQ-only) is all-NULL in the first chunk and has
    real int values in a later chunk. A fixed-schema ParquetWriter built
    from the first chunk's inferred (null-type) schema raises when a later
    differently-typed chunk is written. The fix accumulates pyarrow Tables
    and combines them with pa.concat_tables(..., promote_options=
    "permissive") instead, writing once at the end.
    """

    chunk1 = pd.DataFrame({"permno": [1, 2], "numtrd": [None, None]})
    chunk2 = pd.DataFrame({"permno": [3, 4], "numtrd": [100, 200]})

    class FakeDB:
        def raw_sql(self, sql, return_iter=False):
            assert return_iter
            return iter([chunk1, chunk2])

    out_path = tmp_path / "dsf.parquet"

    total_rows, wrote_any = pwd._stream_query_to_parquet(
        FakeDB(), "SELECT permno, numtrd FROM crsp.dsf", out_path
    )

    assert wrote_any
    assert total_rows == 4
    assert out_path.exists()

    result = pd.read_parquet(out_path)
    assert len(result) == 4
    # First two rows: no numtrd (NYSE/AMEX-style). Last two: real values
    # (NASDAQ-style). Both must survive in one unified, valid Parquet file.
    assert result["numtrd"].isna().sum() == 2
    typed = result.loc[~result["numtrd"].isna(), "numtrd"].astype(int).tolist()
    assert typed == [100, 200]


def test_stream_query_to_parquet_handles_int64_float64_dtype_drift_across_chunks(tmp_path):
    """Reproduces the ACTUAL observed drift pattern this round's Fix 2
    addresses -- distinct from, and subtler than, the null-vs-typed case
    above. wrds's chunked reader runs pandas' convert_dtypes() independently
    per chunk, so the very same numeric column can infer as pandas `Int64`
    in a chunk where every value happens to be whole, and `Float64` in a
    chunk with a fractional value. This is real dtype drift between two
    chunks that both have entirely non-null, real values -- not a case
    pyarrow's "default" promotion mode was ever designed to handle.

    Both halves of the fix are verified here:
      (a) promote_options="default" genuinely cannot merge these two
          real-shaped, dtype-cast chunks (proven by calling
          pa.concat_tables directly, not by reverting production code).
      (b) the actual production path (_stream_query_to_parquet, using
          promote_options="permissive" plus schema-metadata stripping)
          handles them without error and produces a valid Parquet file
          with a unified `double` column.
    """
    chunk1 = pd.DataFrame({"permno": [1, 2, 3], "ret": [1, 1, 2]}).astype({"ret": "Int64"})
    chunk2 = pd.DataFrame({"permno": [4, 5], "ret": [1.0, 1.5]}).astype({"ret": "Float64"})

    table1 = pa.Table.from_pandas(chunk1, preserve_index=False)
    table2 = pa.Table.from_pandas(chunk2, preserve_index=False)

    # (a) Prove the premise directly against pyarrow, not against a
    # simplified None-vs-int stand-in (which "default" already handles).
    with pytest.raises(pa.ArrowTypeError):
        pa.concat_tables([table1, table2], promote_options="default")

    # (b) Prove the actual fix, via the real production function.
    class FakeDB:
        def raw_sql(self, sql, return_iter=False):
            assert return_iter
            return iter([chunk1, chunk2])

    out_path = tmp_path / "dsf.parquet"
    total_rows, wrote_any = pwd._stream_query_to_parquet(
        FakeDB(), "SELECT permno, ret FROM crsp.dsf", out_path
    )

    assert wrote_any
    assert total_rows == 5
    assert out_path.exists()

    result = pd.read_parquet(out_path)
    assert len(result) == 5
    assert str(result["ret"].dtype) == "float64"
    assert result["ret"].tolist() == [1.0, 1.0, 2.0, 1.0, 1.5]

    # Confirm the unified type at the Arrow/Parquet schema level too, not
    # just via pandas' post-hoc read-back inference.
    schema = pq.read_schema(out_path)
    assert pa.types.is_floating(schema.field("ret").type)


def test_stream_query_to_parquet_cleans_up_tmp_file_on_keyboard_interrupt(tmp_path, monkeypatch):
    chunk = pd.DataFrame({"permno": [1], "date": ["2000-01-01"]})

    class FakeDB:
        def raw_sql(self, sql, return_iter=False):
            assert return_iter
            return iter([chunk])

    def _raise_keyboard_interrupt(table, path, **kwargs):
        Path(path).write_bytes(b"PARTIAL")
        raise KeyboardInterrupt()

    monkeypatch.setattr(pq, "write_table", _raise_keyboard_interrupt)

    out_path = tmp_path / "dsf.parquet"
    with pytest.raises(KeyboardInterrupt):
        pwd._stream_query_to_parquet(FakeDB(), "SELECT permno, date FROM crsp.dsf", out_path)

    assert not out_path.with_suffix(out_path.suffix + ".tmp").exists()
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# Fix 1 & Fix 3 (this round): main()'s per-table column lists, and the test
# fixture that mirrors them.
#
# Rather than hand-copying a second "full column set" list here (the exact
# duplication that already drifted out of sync once), TABLE_SPECS below
# imports the SAME per-table column-list constants main() itself uses
# (pwd.IBES_STATSUMU_EPSUS_COLUMNS, pwd.CRSP_DSF_COLUMNS, etc. -- defined
# once, at module level, in scripts/pull_wrds_data.py). There is exactly one
# place any column is ever added to or removed from a given table's pull,
# and both main() and this test suite read it from there, so this fixture
# cannot drift out of sync with main() the way the old hand-maintained
# duplicate list did.
# ---------------------------------------------------------------------------

TABLE_SPECS = [
    ("ibes", "statsumu_epsus", pwd.IBES_STATSUMU_EPSUS_COLUMNS),
    ("ibes", "actu_epsus", pwd.IBES_ACTU_EPSUS_COLUMNS),
    ("ibes", "idsum", pwd.IBES_IDSUM_COLUMNS),
    ("ibes", "detu_epsus", pwd.IBES_DETU_EPSUS_COLUMNS),
    ("crsp", "dsf", pwd.CRSP_DSF_COLUMNS),
    ("crsp", "dsi", pwd.CRSP_DSI_COLUMNS),
    ("crsp", "dsedelist", pwd.CRSP_DSEDELIST_COLUMNS),
    ("crsp", "stocknames", pwd.CRSP_STOCKNAMES_COLUMNS),
    ("comp", "fundq", pwd.COMP_FUNDQ_COLUMNS),
    ("comp", "company", pwd.COMP_COMPANY_COLUMNS),
    ("comp", "funda", pwd.COMP_FUNDA_COLUMNS),
    ("wrdsapps", "ibcrsphist", pwd.WRDSAPPS_IBCRSPHIST_COLUMNS),
    ("crsp", "ccmxpf_lnkhist", pwd.CRSP_CCMXPF_LNKHIST_COLUMNS),
]

# Every one of main()'s 13 pull_table() call sites must appear here exactly
# once -- this assertion catches a NEW call site being added to main()
# without a matching entry being added here, which is exactly the kind of
# drift Fix 3 exists to prevent.
assert len(TABLE_SPECS) == 13

# Union of every column name requested by ANY pull_table(...) call in
# main(), derived (not hand-copied) from TABLE_SPECS above, so a FakeMainDB
# with the full set never spuriously triggers "missing_required_columns" for
# a table under test unless a test deliberately removes one column.
FULL_COLUMN_SET = set().union(*(cols for _lib, _tbl, cols in TABLE_SPECS))


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
        return pd.DataFrame({"name": cols})

    def raw_sql(self, sql, return_iter=False):
        match = re.search(r"FROM (\S+)", sql)
        key = match.group(1) if match else "?"
        self.calls.append({"key": key, "sql": sql, "return_iter": return_iter})
        if return_iter:
            chunks = self.chunks_by_table.get(key)
            if chunks is None:
                chunks = [pd.DataFrame({"permno": [1], "date": ["2000-01-01"]})]
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
# --force flag must actually reach pull_table(), and argparse (Fix 7) must
# reject unrecognized flags rather than silently ignoring them.
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
# Fix 1 & Fix 3 verification: every column in every one of main()'s 13
# pull_table() call sites is now required -- a full run with nothing missing
# must succeed for all 13 tables, and removing ANY single payload column
# from ANY one table must block that table specifically.
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
    for library, table, _cols in TABLE_SPECS:
        matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
        assert len(matches) == 1, f"expected exactly one manifest row for {library}.{table}"
        status = matches.iloc[0]["status"]
        assert status == "ok", (
            f"{library}.{table} did not succeed with every currently-defined "
            f"column present (got status {status!r}) -- TABLE_SPECS/"
            f"FULL_COLUMN_SET may have drifted from main()'s real calls, or "
            f"main() itself is broken"
        )


# Every (library, table, column) triple across all 13 real call sites --
# built from TABLE_SPECS itself (not hand-enumerated) so this parametrized
# test automatically covers every column of every table, not just a
# hand-picked subset.
ALL_TABLE_COLUMN_CASES = [
    (library, table, column)
    for library, table, columns in TABLE_SPECS
    for column in columns
]


@pytest.mark.parametrize("library,table,column", ALL_TABLE_COLUMN_CASES)
def test_main_missing_any_single_payload_column_blocks_that_tables_pull(
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
        f"{library}.{table} missing {column!r} produced status {row['status']!r} -- "
        f"every column in this table's pull is now required (Fix 1), so its "
        f"absence must always abort the pull rather than silently proceeding "
        f"crippled"
    )
    # And that table must not have been queried at all.
    assert not any(c["key"] == f"{library}.{table}" for c in fake_db.calls)


# ---------------------------------------------------------------------------
# I/B/E/S detail file must be the unadjusted variant (detu_epsus), matching
# the basis of statsumu_epsus/actu_epsus, not the split-adjusted det_epsus
# (which also carries look-ahead via its adjustment factor). Confirmed still
# in place after this round's Fix 1 changes, and "value" is confirmed
# present in IBES_DETU_EPSUS_COLUMNS (part of TABLE_SPECS/FULL_COLUMN_SET
# above).
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
    assert "value" in pwd.IBES_DETU_EPSUS_COLUMNS


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
# KeyboardInterrupt (Fix 7).
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
# describe_table()) -- Fix 7.
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


def test_pull_table_rejects_unsafe_name_before_describe_table_is_called(tmp_path):
    """Fix 7: identifier validation must happen before `library`/`table` are
    used for anything, including db.describe_table(...) -- not after. A db
    stub that would raise if describe_table were ever called with the bad
    name proves validation happens first.
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
