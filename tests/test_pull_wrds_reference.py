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
    """Point RAW_DIR/METADATA_DIR at a tmp_path sandbox, patch load_env_file
    to a no-op, and write a minimal one-secid OptionMetrics universe
    fixture whose cusip ("1") matches FakeReferenceDB's default one-row-of-1s
    output for crsp.stocknames (permno 1) -> ccmxpf_lnkhist (gvkey "1") ->
    ibcrsphist (ticker "1"), giving a clean end-to-end resolution unless a
    test overrides something.

    Finding 7: this script writes to its OWN manifest file
    (wrds_pull_manifest_reference.csv, via lib.manifest_path_for("reference")
    -- computed fresh from METADATA_DIR, so patching METADATA_DIR alone is
    enough; no separate MANIFEST_PATH patch is needed anymore."""
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    monkeypatch.setattr(lib, "RAW_DIR", raw_dir)
    monkeypatch.setattr(lib, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(lib, "load_env_file", lambda path: None)
    manifest_path = lib.manifest_path_for("reference")
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

# Fix 3 (round 3 review): a missing required column for ANY Phase A table --
# not just crsp.stocknames -- now blocks the whole run. Before this round,
# crsp.stocknames failing was the ONLY one that took down the whole run (its
# failure independently hard-aborts via resolve_universe()'s own
# UniverseResolutionError, since PERMNO resolution structurally can't
# proceed without it); comp.company/ibes.idsum/crsp.ccmxpf_lnkhist/
# wrdsapps.ibcrsphist failing would silently let the run proceed to a
# "SUCCESS -- safe to run in parallel" message. Fix 3 closes that gap by
# checking the run log for ANY non-success status (see
# wrds_pull_lib.find_run_log_failures) before promoting/declaring success --
# with no special exemption for ccmxpf_lnkhist/ibcrsphist, since those two
# already had a SEPARATE, narrower exemption (a library genuinely being
# UNAVAILABLE on this account, which never even attempts the pull -- see
# test_library_availability_persisted_with_none_comp_lib_when_compustat_
# unavailable) that Fix 3 does not touch; what Fix 3 newly catches is the
# table being AVAILABLE but the pull itself genuinely failing.


@pytest.mark.parametrize("library,table,column", REQUIRED_CASES)
def test_missing_required_column_blocks_that_tables_pull(tmp_path, monkeypatch, library, table, column):
    _raw_dir, metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB(missing_columns_by_table={f"{library}.{table}": {column}})
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    matches = manifest[(manifest["library"] == library) & (manifest["table"] == table)]
    assert len(matches) == 1
    assert matches.iloc[0]["status"] == "missing_required_columns"
    assert not any(c["key"] == f"{library}.{table}" for c in fake_db.calls)
    assert not (metadata_dir / "resolved_universe_ids.parquet").exists(), (
        "Fix 3: a Phase A pull failure must prevent the resolved-ids file "
        "from ever being promoted"
    )


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
# Per-identifier-family degrade at the resolve_universe() LAYER (original
# Fix 3, unchanged this round): a missing/unpullable GVKEY/I-B-E-S-ticker
# linking table degrades ONLY that family inside resolve_universe() itself
# -- it does not raise UniverseResolutionError, and the resolved-ids rows
# come back with a null gvkey/ibes_ticker column instead. This is tested
# directly (and much more thoroughly) in test_wrds_pull_lib.py
# (test_resolve_universe_missing_ccmxpf_lnkhist_skips_gvkey_only /
# test_resolve_universe_missing_ibcrsphist_skips_ibes_ticker_only).
#
# What changes THIS round (Fix 3, round 3 review): whether main() still
# treats the OVERALL run as safe to promote/declare success when the
# underlying Phase A pull for that linking table genuinely FAILED (as
# opposed to the table simply being unavailable on this account at all,
# which is a separate, narrower case -- see
# test_library_availability_persisted_with_none_comp_lib_when_compustat_
# unavailable below, where the pull is never even attempted and the run
# still succeeds). A real pull failure for ANY Phase A table -- including
# these two linking tables -- now blocks promotion, matching the same
# blanket "any non-success status fails the run" check the three consumer
# scripts already apply to themselves.
# ---------------------------------------------------------------------------


def test_ccmxpf_lnkhist_pull_failure_now_blocks_whole_run(tmp_path, monkeypatch, capsys):
    """Before Fix 3 (round 3 review), a genuine crsp.ccmxpf_lnkhist pull
    failure (the table IS available on this account, but this particular
    pull attempt failed -- as opposed to the library being unavailable at
    all) still let the whole run proceed to SUCCESS, since GVKEY resolution
    degrading gracefully at the resolve_universe() layer masked the
    underlying Phase A failure from main()'s success check entirely (there
    wasn't one). Fix 3 closes that gap."""
    _raw_dir, metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB(missing_columns_by_table={"crsp.ccmxpf_lnkhist": {"gvkey"}})
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert (manifest["status"] == "missing_required_columns").any()
    assert not (metadata_dir / "resolved_universe_ids.parquet").exists(), (
        "a Phase A pull failure must prevent the resolved-ids file from "
        "ever being promoted, even though GVKEY resolution itself degraded "
        "gracefully rather than raising"
    )
    out = capsys.readouterr().out
    assert "SUCCESS" not in out
    assert "safe to run" not in out.lower()


def test_ibcrsphist_pull_failure_now_blocks_whole_run(tmp_path, monkeypatch, capsys):
    """Same as test_ccmxpf_lnkhist_pull_failure_now_blocks_whole_run above,
    for the wrdsapps.ibcrsphist / I-B-E-S-ticker family."""
    _raw_dir, metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = FakeReferenceDB(missing_columns_by_table={"wrdsapps.ibcrsphist": {"ticker"}})
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    assert (manifest["status"] == "missing_required_columns").any()
    assert not (metadata_dir / "resolved_universe_ids.parquet").exists(), (
        "a Phase A pull failure must prevent the resolved-ids file from "
        "ever being promoted, even though I/B/E/S ticker resolution itself "
        "degraded gracefully rather than raising"
    )
    out = capsys.readouterr().out
    assert "SUCCESS" not in out
    assert "safe to run" not in out.lower()


class _FakeReferenceDBCompCompanyQueryFails(FakeReferenceDB):
    """comp.company's real query raises outright (e.g. a genuine WRDS query
    error) -- every OTHER Phase A table pulls fine. comp.company is not
    itself read by resolve_universe() at all (see resolve_universe()'s
    parameter list: it only reads the OM universe files, crsp.stocknames,
    crsp.ccmxpf_lnkhist, and wrdsapps.ibcrsphist), so PERMNO/GVKEY/I-B-E-S
    ticker resolution all complete perfectly normally despite this failure
    -- this is the starkest version of the exact scenario Fix 3 exists to
    catch."""

    def raw_sql(self, sql, return_iter=False):
        match = re.search(r"FROM (\S+)", sql)
        key = match.group(1) if match else "?"
        if key == "comp.company":
            self.calls.append({"key": key, "sql": sql})
            raise RuntimeError("simulated WRDS query failure for comp.company")
        return super().raw_sql(sql, return_iter=return_iter)


def test_comp_company_query_failure_blocks_promotion_even_though_resolution_itself_succeeds(
    tmp_path, monkeypatch, capsys,
):
    """The literal scenario Fix 3's own task description names: comp.company
    (a table resolve_universe() doesn't even read) has a genuine pull
    failure while every table PERMNO/GVKEY/I-B-E-S-ticker resolution
    actually needs pulls and resolves cleanly. Before Fix 3, this let
    pull_wrds_reference.py print SUCCESS and promote the resolved-ids file
    anyway, silently hiding the comp.company failure from anyone reading
    just the final console message."""
    _raw_dir, metadata_dir, manifest_path = _patch_reference_env(monkeypatch, tmp_path)

    fake_db = _FakeReferenceDBCompCompanyQueryFails()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    with pytest.raises(SystemExit):
        ref.main()

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    company_rows = manifest[(manifest["library"] == "comp") & (manifest["table"] == "company")]
    assert len(company_rows) == 1
    assert company_rows.iloc[0]["status"] == "query_failed"
    # PERMNO/GVKEY/I-B-E-S ticker resolution itself must have gone through
    # cleanly -- proving this abort is NOT a resolution-rate/canary failure,
    # only Fix 3's new run-log check.
    stocknames_rows = manifest[(manifest["library"] == "crsp") & (manifest["table"] == "stocknames")]
    assert stocknames_rows.iloc[0]["status"] == "ok"
    assert not (metadata_dir / "resolved_universe_ids.parquet").exists(), (
        "Fix 3: a Phase A failure elsewhere (comp.company) must prevent "
        "promotion even though universe resolution itself would have "
        "succeeded using only the tables that DID pull correctly"
    )
    out = capsys.readouterr().out
    assert "SUCCESS" not in out
    assert "safe to run" not in out.lower()


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


# ---------------------------------------------------------------------------
# Finding 1: aborted runs must not leave a resolved-universe file that still
# passes the "does it exist" check the 3 consumer scripts' guard uses.
# ---------------------------------------------------------------------------


def test_aborted_run_leaves_no_final_resolved_ids_file_and_downstream_guard_rejects_it(tmp_path, monkeypatch):
    """Before the fix, resolve_universe() wrote directly to the final
    resolved_universe_ids.parquet path, so an ABORTED run (the resolution-
    rate floor check failing AFTER resolution had already run and written
    that file) still left a fully-formed file at the exact path the 3
    consumer scripts' guard (require_resolved_universe_file) checks with a
    bare path.exists() -- i.e. a run this script itself judged unsafe could
    still let those scripts proceed against it. This proves the DOWNSTREAM
    GUARD itself rejects the post-abort state (not just that
    pull_wrds_reference.py exits 1, which the pre-existing
    test_resolution_rate_below_floor_aborts already covered)."""
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

    final_path = metadata_dir / "resolved_universe_ids.parquet"
    assert not final_path.exists(), (
        "an aborted run (resolution rate below floor) must NOT leave a "
        "file at the path the 3 consumer scripts' guard checks for "
        "existence"
    )
    # The actual proof this finding cares about: the DOWNSTREAM guard every
    # consumer script calls first must also reject this state, not merely
    # "pull_wrds_reference.py itself exited 1".
    with pytest.raises(SystemExit):
        lib.require_resolved_universe_file(final_path)


def test_aborted_run_does_not_clobber_a_prior_successful_runs_final_file(tmp_path, monkeypatch):
    """A --force re-run that fails must not destroy a PREVIOUSLY-good
    resolved_universe_ids.parquet a human/downstream script might still be
    relying on -- the atomic os.replace only happens after every check
    passes, so an old good file already at the final path is left
    completely untouched by a subsequent failing run."""
    _raw_dir, metadata_dir, _manifest_path = _patch_reference_env(
        monkeypatch, tmp_path, enforce_universe_floor=False,
    )
    final_path = metadata_dir / "resolved_universe_ids.parquet"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"prior-good-run-marker")

    secids = list(range(1, 11))
    cusips = ["1"] + [f"NOMATCH{i}" for i in range(2, 11)]
    pd.DataFrame({
        "secid": secids, "cusip": cusips, "ticker": [f"T{i}" for i in secids],
        "issuer": [f"ISSUER {i}" for i in secids], "sic": ["9999"] * 10,
    }).to_parquet(metadata_dir / "optionmetrics_universe.parquet", index=False)

    fake_db = FakeReferenceDB()
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py", "--force"])

    with pytest.raises(SystemExit):
        ref.main()

    assert final_path.read_bytes() == b"prior-good-run-marker", (
        "a failing run must leave a pre-existing final resolved-ids file "
        "completely untouched"
    )


# ---------------------------------------------------------------------------
# Finding 4: library-alias-resolution persistence, so pull_wrds_compustat.py
# can read the ACTUAL resolved Compustat library name.
# ---------------------------------------------------------------------------


def test_library_availability_persisted_with_correct_alias_when_compustat_exposed_as_compa(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, _manifest_path = _patch_reference_env(monkeypatch, tmp_path)
    fake_db = FakeReferenceDB(libraries=("ibes", "crsp", "compa", "wrdsapps"))
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    ref.main()

    info = lib.load_library_availability()
    assert info["comp_lib"] == "compa"
    assert info["have_comp"] is True
    assert info["have_crsp"] is True
    assert info["have_ibes"] is True
    # Phase A's own comp.company pull must ALSO have gone to compa.company,
    # not a hardcoded comp.company -- proving the resolved alias is used
    # consistently, not just recorded.
    assert any(c["key"] == "compa.company" for c in fake_db.calls)
    assert not any(c["key"] == "comp.company" for c in fake_db.calls)


def test_library_availability_persisted_with_none_comp_lib_when_compustat_unavailable(tmp_path, monkeypatch):
    _raw_dir, metadata_dir, _manifest_path = _patch_reference_env(monkeypatch, tmp_path)
    fake_db = FakeReferenceDB(libraries=("ibes", "crsp"))
    monkeypatch.setattr(lib, "connect", lambda: fake_db)
    monkeypatch.setattr(sys, "argv", ["pull_wrds_reference.py"])

    ref.main()  # must not raise -- GVKEY resolution just degrades

    info = lib.load_library_availability()
    assert info["comp_lib"] is None
    assert info["have_comp"] is False
