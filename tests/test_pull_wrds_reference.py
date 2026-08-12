"""Tests for scripts/pull_wrds_reference.py -- Phase A (small reference
tables) + Phase B (local universe resolution) of the split WRDS pull.

These tests exercise pull_wrds_reference.main() end to end against a fake
`db` (no real WRDS connection), the same way the original monolithic
pull_wrds_data.py's main()-level tests did for its Phase A/B portion.
Everything about resolve_universe()'s own join logic is tested directly
(and much more thoroughly) in tests/test_wrds_pull_lib.py -- these tests
cover main()'s ORCHESTRATION: which Phase A tables get pulled, the abort/
degrade gating around resolve_universe()'s result, and the --force /
argument-parsing plumbing.
"""

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pull_wrds_reference as ref  # noqa: E402

lib = ref.lib


# ---------------------------------------------------------------------------
# Fake DB covering pull_wrds_reference.py's 5 Phase A tables.
# ---------------------------------------------------------------------------

TABLE_SPECS = [
    ("crsp", "stocknames", lib.CRSP_STOCKNAMES_REQUIRED_COLUMNS, lib.CRSP_STOCKNAMES_OPTIONAL_COLUMNS),
    ("comp", "company", lib.COMP_COMPANY_REQUIRED_COLUMNS, lib.COMP_COMPANY_OPTIONAL_COLUMNS),
    ("wrdsapps", "ibcrsphist", lib.WRDSAPPS_IBCRSPHIST_REQUIRED_COLUMNS, lib.WRDSAPPS_IBCRSPHIST_OPTIONAL_COLUMNS),
    ("crsp", "ccmxpf_lnkhist", lib.CRSP_CCMXPF_LNKHIST_REQUIRED_COLUMNS, lib.CRSP_CCMXPF_LNKHIST_OPTIONAL_COLUMNS),
    ("ibes", "idsum", lib.IBES_IDSUM_REQUIRED_COLUMNS, lib.IBES_IDSUM_OPTIONAL_COLUMNS),
]
assert len(TABLE_SPECS) == 5

FULL_COLUMN_SET = set().union(*(set(req) | set(opt) for _l, _t, req, opt in TABLE_SPECS))


class FakeReferenceDB:
    """Stand-in for wrds.Connection covering exactly pull_wrds_reference.py's
    5 Phase A tables. Every non-streaming query returns a one-row DataFrame
    containing exactly the columns actually selected -- resolve_universe()
    then reads these back off disk, so the default fixture is built so that
    a clean, one-secid resolution goes through end to end unless a test
    overrides raw_sql for a specific table."""

    def __init__(self, libraries=("ibes", "crsp", "comp", "wrdsapps"), missing_columns_by_table=None):
        self.libraries = list(libraries)
        self.missing_columns_by_table = missing_columns_by_table or {}
        self.calls = []
        self.closed = False

    def list_libraries(self):
        return self.libraries

    def describe_table(self, library, table):
        missing = self.missing_columns_by_table.get(f"{library}.{table}", set())
        cols = sorted(FULL_COLUMN_SET - set(missing))
        return pd.DataFrame({"name": cols, "type": ["VARCHAR"] * len(cols)})

    def raw_sql(self, sql, return_iter=False):
        match = re.search(r"FROM (\S+)", sql)
        key = match.group(1) if match else "?"
        self.calls.append({"key": key, "sql": sql})
        select_list = sql.split("FROM")[0].replace("SELECT", "")
        cols = [c.strip() for c in select_list.split(",") if c.strip()]
        return pd.DataFrame({c: [1] for c in cols})

    def close(self):
        self.closed = True


def _patch_reference_env(monkeypatch, tmp_path, enforce_universe_floor=False):
    """Point RAW_DIR/METADATA_DIR/MANIFEST_PATH at a tmp_path sandbox, patch
    load_env_file to a no-op, and write a minimal one-secid OptionMetrics
    universe fixture whose cusip ("1") matches FakeReferenceDB's default
    one-row-of-1s output for crsp.stocknames (permno 1) -> ccmxpf_lnkhist
    (gvkey "1") -> ibcrsphist (ticker "1"), giving a clean end-to-end
    resolution unless a test overrides something."""
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    manifest_path = metadata_dir / "wrds_pull_manifest.csv"
    monkeypatch.setattr(lib, "RAW_DIR", raw_dir)
    monkeypatch.setattr(lib, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(lib, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(lib, "load_env_file", lambda path: None)
    if not enforce_universe_floor:
        monkeypatch.setattr(lib, "MIN_UNIVERSE_SECID_COUNT", 0)

    metadata_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "secid": [999], "cusip": ["1"], "ticker": ["OMX"],
        "issuer": ["OM FIXTURE ISSUER"], "sic": ["9999"],
    }).to_parquet(metadata_dir / "optionmetrics_universe.parquet", index=False)
    pd.DataFrame({
        "secid": pd.Series([], dtype="int64"), "cusip": pd.Series([], dtype="object"),
    }).to_parquet(metadata_dir / "optionmetrics_universe_history.parquet", index=False)

    return raw_dir, metadata_dir, manifest_path


# ---------------------------------------------------------------------------
# --force / argument parsing plumbing.
# ---------------------------------------------------------------------------


def test_force_flag_rewires_stocknames_pull(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_reference_env(monkeypatch, tmp_path)
    (raw_dir / "crsp").mkdir(parents=True)
    (raw_dir / "crsp" / "stocknames.parquet").write_bytes(b"stale-marker")

    fake_db = FakeReferenceDB()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py", "--force"])

    ref.main()

    assert any(c["key"] == "crsp.stocknames" for c in fake_db.calls), (
        "with --force, stocknames must be re-queried even though its output "
        "file already existed"
    )


def test_without_force_flag_skips_existing_stocknames(tmp_path, monkeypatch):
    raw_dir, _metadata_dir, _manifest_path = _patch_reference_env(monkeypatch, tmp_path)
    (raw_dir / "crsp").mkdir(parents=True)
    (raw_dir / "crsp" / "stocknames.parquet").write_bytes(b"stale-marker")

    fake_db = FakeReferenceDB()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    # Resolution will fail (the stale marker isn't valid Parquet) -- that's
    # fine, this test only cares whether stocknames was RE-QUERIED or not.
    with pytest.raises(SystemExit):
        ref.main()

    assert not any(c["key"] == "crsp.stocknames" for c in fake_db.calls)
    assert (raw_dir / "crsp" / "stocknames.parquet").read_bytes() == b"stale-marker"


def test_unrecognized_flag_errors_out_before_connecting(tmp_path, monkeypatch):
    _patch_reference_env(monkeypatch, tmp_path)

    def _unexpected_connect():
        raise AssertionError("connect() must not be called for a bad flag")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py", "--Force"])

    with pytest.raises(SystemExit):
        ref.main()


# ---------------------------------------------------------------------------
# Every Phase A pull_table() call site: full column set succeeds; a missing
# REQUIRED column blocks just that table (except stocknames, whose failure
# takes down the whole run since PERMNO resolution needs it); a missing
# OPTIONAL column never blocks.
# ---------------------------------------------------------------------------


def test_full_column_set_succeeds_for_every_phase_a_table(tmp_path, monkeypatch):
    _raw_dir, _metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    ref.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    for library, table, _req, _opt in TABLE_SPECS:
        matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
        assert len(matches) == 1, f"expected exactly one manifest row for {library}.{table}"
        assert matches.iloc[0]["status"] == "ok"


REQUIRED_CASES = [
    (library, table, column)
    for library, table, required, _optional in TABLE_SPECS
    for column in required
]

# Only crsp.stocknames' pull failing takes down the whole run -- it's the
# one Phase A table PERMNO resolution structurally cannot proceed without.
HARD_ABORT_TABLES = {("crsp", "stocknames")}


@pytest.mark.parametrize("library,table,column", REQUIRED_CASES)
def test_missing_required_column_blocks_that_tables_pull(tmp_path, monkeypatch, library, table, column):
    _raw_dir, _metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB(missing_columns_by_table={f"{library}.{table}": {column}})
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    if (library, table) in HARD_ABORT_TABLES:
        with pytest.raises(SystemExit):
            ref.main()
    else:
        ref.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(matches) == 1
    assert matches.iloc[0]["status"] == "missing_required_columns"
    assert not any(c["key"] == f"{library}.{table}" for c in fake_db.calls)


OPTIONAL_CASES = [
    (library, table, column)
    for library, table, _required, optional in TABLE_SPECS
    for column in optional
]


@pytest.mark.parametrize("library,table,column", OPTIONAL_CASES)
def test_missing_optional_column_does_not_block_that_tables_pull(tmp_path, monkeypatch, library, table, column):
    _raw_dir, _metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB(missing_columns_by_table={f"{library}.{table}": {column}})
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    ref.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(matches) == 1
    assert matches.iloc[0]["status"] == "ok"
    assert column in matches.iloc[0]["optional_columns_missing"].split(",")


# ---------------------------------------------------------------------------
# Per-identifier-family degrade (Fix 3): a missing GVKEY/I-B-E-S-ticker
# linking table degrades ONLY that family -- the whole run still succeeds
# and prints the "safe to proceed" message.
# ---------------------------------------------------------------------------


def test_missing_ccmxpf_lnkhist_degrades_gvkey_only_not_whole_run(tmp_path, monkeypatch, capsys):
    _raw_dir, metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB(missing_columns_by_table={"crsp.ccmxpf_lnkhist": {"gvkey"}})
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    ref.main()  # must NOT raise SystemExit

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert (manifest["status"] == "missing_required_columns").any()

    resolved_df = pd.read_parquet(metadata_dir / "resolved_universe_ids.parquet")
    assert resolved_df["gvkey"].isna().all(), (
        "GVKEY resolution was skipped -- every row's gvkey must be null, "
        "which is exactly what pull_wrds_compustat.py's "
        "load_resolved_filter_lists() reads as 'skip Compustat'"
    )
    assert resolved_df["permno"].notna().all(), "PERMNO resolution must be unaffected"
    assert resolved_df["ibes_ticker"].notna().all(), "I/B/E/S ticker resolution must be unaffected"
    assert "SUCCESS" in capsys.readouterr().out


def test_missing_ibcrsphist_degrades_ibes_ticker_only_not_whole_run(tmp_path, monkeypatch, capsys):
    _raw_dir, metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB(missing_columns_by_table={"wrdsapps.ibcrsphist": {"ticker"}})
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    ref.main()  # must NOT raise SystemExit

    resolved_df = pd.read_parquet(metadata_dir / "resolved_universe_ids.parquet")
    assert resolved_df["ibes_ticker"].isna().all()
    assert resolved_df["permno"].notna().all()
    assert resolved_df["gvkey"].notna().all()
    assert "SUCCESS" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Floor/abort checks: a resolution outcome that's zero, below-floor, or
# "attempted but suspiciously empty" must hard-abort BEFORE the "safe to
# proceed" message ever prints -- this is what stops a user from running the
# three consumer scripts against garbage.
# ---------------------------------------------------------------------------


def test_zero_resolved_secids_aborts(tmp_path, monkeypatch, capsys):
    _raw_dir, metadata_dir, _manifest_path = _patch_reference_env(monkeypatch, tmp_path)
    pd.DataFrame({
        "secid": [999], "cusip": ["NEVERMATCHES"], "ticker": ["OMX"],
        "issuer": ["OM FIXTURE ISSUER"], "sic": ["9999"],
    }).to_parquet(metadata_dir / "optionmetrics_universe.parquet", index=False)

    fake_db = FakeReferenceDB()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()

    assert "SUCCESS" not in capsys.readouterr().out


def test_resolution_rate_below_floor_aborts(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, _manifest_path = _patch_reference_env(monkeypatch, tmp_path)
    secids = list(range(1, 11))
    cusips = ["1"] + [f"NOMATCH{i}" for i in range(2, 11)]
    pd.DataFrame({
        "secid": secids, "cusip": cusips, "ticker": [f"T{i}" for i in secids],
        "issuer": [f"ISSUER {i}" for i in secids], "sic": ["9999"] * 10,
    }).to_parquet(metadata_dir / "optionmetrics_universe.parquet", index=False)

    fake_db = FakeReferenceDB()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()


def test_tiny_universe_file_rejected_by_absolute_floor_even_at_100pct_resolution(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, _manifest_path = _patch_reference_env(
        monkeypatch, tmp_path, enforce_universe_floor=True
    )
    pd.DataFrame({
        "secid": [1, 2, 3], "cusip": ["1", "1", "1"], "ticker": ["A", "B", "C"],
        "issuer": ["A CORP", "B CORP", "C CORP"], "sic": ["9999"] * 3,
    }).to_parquet(metadata_dir / "optionmetrics_universe.parquet", index=False)

    def _unexpected_connect():
        raise AssertionError("connect() must not be called -- the absolute floor must fire first")

    monkeypatch.setattr(lib, "connect", _unexpected_connect)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()


class _FakeReferenceDBUnlinkedGvkey(FakeReferenceDB):
    """crsp.ccmxpf_lnkhist links a PERMNO (999) that never resolves from the
    OM universe -- resolution is attempted (the table IS available) but
    comes back with zero GVKEYs, the "broken join" case."""

    def raw_sql(self, sql, return_iter=False):
        match = re.search(r"FROM (\S+)", sql)
        key = match.group(1) if match else "?"
        if key == "crsp.ccmxpf_lnkhist":
            self.calls.append({"key": key, "sql": sql})
            select_list = sql.split("FROM")[0].replace("SELECT", "")
            cols = [c.strip() for c in select_list.split(",") if c.strip()]
            return pd.DataFrame({c: ([999] if c == "lpermno" else [1]) for c in cols})
        return super().raw_sql(sql, return_iter=return_iter)


class _FakeReferenceDBUnlinkedIbesTicker(FakeReferenceDB):
    def raw_sql(self, sql, return_iter=False):
        match = re.search(r"FROM (\S+)", sql)
        key = match.group(1) if match else "?"
        if key == "wrdsapps.ibcrsphist":
            self.calls.append({"key": key, "sql": sql})
            select_list = sql.split("FROM")[0].replace("SELECT", "")
            cols = [c.strip() for c in select_list.split(",") if c.strip()]
            return pd.DataFrame({c: ([999] if c == "permno" else [1]) for c in cols})
        return super().raw_sql(sql, return_iter=return_iter)


def test_gvkey_attempted_but_empty_aborts(tmp_path, monkeypatch):
    _patch_reference_env(monkeypatch, tmp_path)
    fake_db = _FakeReferenceDBUnlinkedGvkey()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()


def test_ibes_ticker_attempted_but_empty_aborts(tmp_path, monkeypatch):
    _patch_reference_env(monkeypatch, tmp_path)
    fake_db = _FakeReferenceDBUnlinkedIbesTicker()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()


def test_successful_run_prints_safe_to_proceed_message(tmp_path, monkeypatch, capsys):
    _raw_dir, metadata_dir, _manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    ref.main()

    out = capsys.readouterr().out
    assert "SUCCESS" in out
    assert "pull_wrds_crsp.py" in out
    assert "pull_wrds_compustat.py" in out
    assert "pull_wrds_ibes.py" in out
    assert (metadata_dir / "resolved_universe_ids.parquet").exists()
