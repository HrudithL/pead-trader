"""Unit tests for scripts/pull_wrds_data.py -- only the parts that don't need
a real WRDS connection: load_env_file, and pull_table's column-matching /
required-columns / idempotency logic against a stub `db` object.
"""

import re
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


# ---------------------------------------------------------------------------
# Round 3 fixes
# ---------------------------------------------------------------------------
#
# Item 1: ParquetWriter schema mismatch across chunks
# ---------------------------------------------------------------------------


def test_stream_query_to_parquet_unifies_schema_across_chunks(tmp_path):
    """Reproduces the confirmed bug: crsp.dsf-style data where a column
    (e.g. numtrd, NASDAQ-only) is all-NULL in the first chunk and has real
    int values in a later chunk. A fixed-schema ParquetWriter built from the
    first chunk's inferred (null-type) schema raises when a later
    differently-typed chunk is written. The fix must accumulate pyarrow
    Tables and combine them with pa.concat_tables(..., promote_options=
    "default") instead, writing once at the end.
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


# ---------------------------------------------------------------------------
# Items 2, 3, 4, 6: main()-level integration harness
# ---------------------------------------------------------------------------
#
# These need a fake WRDS connection standing in for the whole account (not
# just one table), since the bugs are in main()'s call sites, not in
# pull_table's own (already-correct, already-tested) logic.

# Union of every column name requested by ANY pull_table(...) call in
# main(), post-fix, so a FakeMainDB with the full set never spuriously
# triggers "missing_required_columns" or "no_matching_columns" for a table
# under test unless a test deliberately removes one column.
FULL_COLUMN_SET = {
    "ticker", "cusip", "oftic", "cname", "statpers", "measure", "fiscalp",
    "fpi", "curcode", "usfirm", "fpedats", "numest", "numup", "numdown",
    "medest", "meanest", "stdev", "highest", "lowest", "pends", "anndats",
    "anntims", "value", "curr_act", "sdates", "estimator", "analys",
    "revdats", "revtims", "permno", "date", "ret", "retx", "prc", "vol",
    "shrout", "cfacpr", "cfacshr", "bid", "ask", "bidlo", "askhi",
    "openprc", "numtrd", "dlstdt", "dlret", "dlstcd", "permco", "namedt",
    "nameenddt", "comnam", "shrcd", "exchcd", "siccd", "gvkey", "datadate",
    "fyearq", "fqtr", "atq", "ceqq", "saleq", "niq", "epspxq", "rdq",
    "indfmt", "datafmt", "popsrc", "consol", "conm", "sic", "gsector",
    "fyear", "at", "ceq", "sale", "ni", "epspx", "ncusip", "score",
    "sdate", "edate", "lpermno", "linktype", "linkprim", "linkdt",
    "linkenddt",
}


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
    return the paths a test needs to make assertions against."""
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    manifest_path = metadata_dir / "wrds_pull_manifest.csv"
    monkeypatch.setattr(pwd, "RAW_DIR", raw_dir)
    monkeypatch.setattr(pwd, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(pwd, "MANIFEST_PATH", manifest_path)
    return raw_dir, metadata_dir, manifest_path


# ---------------------------------------------------------------------------
# Item 2: --force flag must actually reach pull_table()
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


# ---------------------------------------------------------------------------
# Item 3: required_columns must cover load-bearing payload/validity columns
# ---------------------------------------------------------------------------

REQUIRED_COLUMN_ADDITIONS = [
    ("crsp", "dsf", "ret"),
    ("ibes", "actu_epsus", "value"),
    ("ibes", "statsumu_epsus", "meanest"),
    ("ibes", "statsumu_epsus", "fpi"),
    ("ibes", "statsumu_epsus", "fpedats"),
    ("crsp", "ccmxpf_lnkhist", "linkdt"),
    ("crsp", "ccmxpf_lnkhist", "linkenddt"),
    ("wrdsapps", "ibcrsphist", "permno"),
    ("wrdsapps", "ibcrsphist", "sdate"),
    ("wrdsapps", "ibcrsphist", "edate"),
    ("crsp", "stocknames", "permno"),
]


@pytest.mark.parametrize("library,table,column", REQUIRED_COLUMN_ADDITIONS)
def test_main_newly_required_columns_block_pull_when_missing(
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
        f"{column!r} must be required, not merely desired, so the pull is "
        f"skipped entirely rather than silently proceeding crippled"
    )


# ---------------------------------------------------------------------------
# Item 4: I/B/E/S detail file must be the unadjusted variant (detu_epsus),
# matching the basis of statsumu_epsus/actu_epsus, not the split-adjusted
# det_epsus (which also carries look-ahead via its adjustment factor).
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


# ---------------------------------------------------------------------------
# Item 6: event-date filters need the RETURN_END_DATE forward buffer too,
# not just the return series (crsp.dsf/dsi/dsedelist).
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
# Items 5 & 7: atomic writes -- a failed (re-)write must never destroy or
# truncate a previously-good out_path.
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


# ---------------------------------------------------------------------------
# Item 8: mid-stream exception on a fresh (no prior file) run must leave no
# output file behind at all -- neither partial nor stale.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Item 11: defensive identifier validation before SQL string construction
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
