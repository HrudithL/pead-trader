"""Tests for scripts/options_loader_archive.py.

Uses tiny synthetic IvyDB-shaped parquet fixtures under tmp_path, so
these run in seconds and stay CI-clean. A separate real-archive smoke
test lives at the bottom, gated on the drive being mounted.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import options_canonical_schema as sch  # noqa: E402
import options_loader_archive as loader  # noqa: E402


# --- Fixture builders ----------------------------------------------------


def _write_opprcd_year(root: Path, year: int, rows: list[dict]) -> None:
    parquet_dir = root / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df), parquet_dir / f"opprcd{year}.parquet")


def _write_secprd(root: Path, rows: list[dict], *, filename: str = "secprd.parquet") -> None:
    parquet_dir = root / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df), parquet_dir / filename)


def _opprcd_row(**overrides) -> dict:
    row = {
        "secid": 100.0,
        "date": dt.date(2010, 1, 4),
        "symbol": "AAPL   100115C00050000",
        "symbol_flag": "N",
        "exdate": dt.date(2010, 1, 15),
        "last_date": dt.date(2010, 1, 4),
        "cp_flag": "C",
        "strike_price": 50000.0,   # IvyDB scales strike * 1000
        "best_bid": 2.5,
        "best_offer": 2.7,
        "volume": 100.0,
        "open_interest": 500.0,
        "impl_volatility": 0.3,
        "delta": 0.5,
        "gamma": 0.02,
        "vega": 0.1,
        "theta": -0.01,
        "optionid": 999001.0,
        "cfadj": 1.0,
        "ss_flag": "N",
        "root": "AAPL",
        "suffix": "",
    }
    row.update(overrides)
    return row


def _secprd_row(**overrides) -> dict:
    row = {
        "secid": 100.0,
        "date": dt.date(2010, 1, 4),
        "low": 50.0,
        "high": 52.0,
        "close": 51.0,
        "volume": 1_000_000.0,
        "return": 0.01,
        "cfadj": 1.0,
        "open": 50.5,
        "cfret": 1.0,
        "shrout": 100_000.0,
    }
    row.update(overrides)
    return row


def _make_minimal_archive(tmp_path: Path) -> Path:
    root = tmp_path / "OptionMetrics"
    _write_opprcd_year(root, 2010, [_opprcd_row()])
    _write_secprd(root, [_secprd_row()])
    return root


# --- Tests ---------------------------------------------------------------


class TestArchiveDiscovery:
    def test_missing_archive_raises_pointer_error(self, tmp_path: Path):
        with pytest.raises(loader.ArchiveLoaderError, match="parquet directory not found"):
            loader.load_options(archive_root=tmp_path / "nope")

    def test_list_opprcd_years_sorted(self, tmp_path: Path):
        root = tmp_path / "arch"
        _write_opprcd_year(root, 2011, [_opprcd_row()])
        _write_opprcd_year(root, 2009, [_opprcd_row()])
        _write_opprcd_year(root, 2010, [_opprcd_row()])
        assert loader._list_opprcd_years(root / "parquet") == [2009, 2010, 2011]

    def test_year_selection_narrows_files(self, tmp_path: Path):
        root = tmp_path / "arch"
        for y in (2009, 2010, 2011, 2012):
            _write_opprcd_year(root, y, [_opprcd_row()])
        picked = loader._select_opprcd_files_for_window(
            root / "parquet",
            date_start=dt.date(2010, 6, 1),
            date_end=dt.date(2011, 3, 31),
        )
        assert [Path(p).stem for p in picked] == ["opprcd2010", "opprcd2011"]

    def test_year_selection_no_bounds_returns_all(self, tmp_path: Path):
        root = tmp_path / "arch"
        for y in (2009, 2010):
            _write_opprcd_year(root, y, [_opprcd_row()])
        picked = loader._select_opprcd_files_for_window(
            root / "parquet", date_start=None, date_end=None
        )
        assert len(picked) == 2


class TestToDate:
    def test_none_passes_through(self):
        assert loader._to_date(None) is None

    def test_string_iso(self):
        assert loader._to_date("2010-01-04") == dt.date(2010, 1, 4)

    def test_timestamp(self):
        assert loader._to_date(pd.Timestamp("2010-01-04")) == dt.date(2010, 1, 4)

    def test_date_passes_through(self):
        assert loader._to_date(dt.date(2010, 1, 4)) == dt.date(2010, 1, 4)


class TestLoadOptionsHappyPath:
    def test_returns_canonical_frame(self, tmp_path: Path):
        root = _make_minimal_archive(tmp_path)
        df = loader.load_options(archive_root=root)
        result = sch.validate(df)
        assert result.ok, result.errors
        assert len(df) == 1

    def test_field_mapping_correct(self, tmp_path: Path):
        root = _make_minimal_archive(tmp_path)
        df = loader.load_options(archive_root=root)
        row = df.iloc[0]
        assert row["option_id"] == 999001
        assert row["stable_underlying_id"] == "ivydb_secid_100"
        assert row["strike"] == 50.0  # 50000 / 1000
        assert row["call_put"] == "C"
        assert row["bid"] == 2.5
        assert row["ask"] == 2.7
        assert row["underlying_price"] == 51.0
        assert row["multiplier"] == 100
        assert row["source"] == "archive_ivydb_1996_2013"

    def test_raw_columns_preserved(self, tmp_path: Path):
        root = _make_minimal_archive(tmp_path)
        df = loader.load_options(archive_root=root)
        # raw provenance columns follow the canonical block.
        assert "raw_strike_price" in df.columns
        assert "raw_symbol" in df.columns
        assert df["raw_strike_price"].iloc[0] == 50000.0

    def test_empty_frame_when_no_matches(self, tmp_path: Path):
        root = _make_minimal_archive(tmp_path)
        df = loader.load_options(
            archive_root=root, secids=[999999]  # secid not in archive
        )
        assert df.empty
        assert list(df.columns) == sch.CANONICAL_COLUMN_NAMES

    def test_empty_secids_list_returns_empty(self, tmp_path: Path):
        # Round-1 review fix: secids=[] is a valid caller state (universe
        # collapsed to nothing) and must short-circuit rather than
        # produce SQL ``secid IN ()`` (a DuckDB syntax error).
        root = _make_minimal_archive(tmp_path)
        df = loader.load_options(archive_root=root, secids=[])
        assert df.empty
        assert list(df.columns) == sch.CANONICAL_COLUMN_NAMES

    def test_secid_filter_applied(self, tmp_path: Path):
        root = tmp_path / "arch"
        _write_opprcd_year(
            root, 2010,
            [_opprcd_row(secid=100.0, optionid=1.0),
             _opprcd_row(secid=200.0, optionid=2.0)],
        )
        _write_secprd(
            root,
            [_secprd_row(secid=100.0), _secprd_row(secid=200.0)],
        )
        df = loader.load_options(archive_root=root, secids=[100])
        assert len(df) == 1
        assert df.iloc[0]["stable_underlying_id"] == "ivydb_secid_100"

    def test_date_range_applied(self, tmp_path: Path):
        root = tmp_path / "arch"
        # Use an exdate late in the year so all three observation dates
        # (Jan / Feb / Mar) are all before expiration -- otherwise the
        # loader legitimately drops them as expired-contract rows.
        _write_opprcd_year(
            root, 2010,
            [
                _opprcd_row(date=dt.date(2010, 1, 4), optionid=1.0,
                            exdate=dt.date(2010, 6, 18)),
                _opprcd_row(date=dt.date(2010, 2, 4), optionid=2.0,
                            exdate=dt.date(2010, 6, 18)),
                _opprcd_row(date=dt.date(2010, 3, 4), optionid=3.0,
                            exdate=dt.date(2010, 6, 18)),
            ],
        )
        _write_secprd(
            root,
            [
                _secprd_row(date=dt.date(2010, 1, 4)),
                _secprd_row(date=dt.date(2010, 2, 4)),
                _secprd_row(date=dt.date(2010, 3, 4)),
            ],
        )
        df = loader.load_options(
            archive_root=root,
            date_start="2010-02-01",
            date_end="2010-02-28",
        )
        assert len(df) == 1
        assert df.iloc[0]["date"] == pd.Timestamp("2010-02-04")


class TestUnusableRowsDropped:
    def test_row_without_underlying_dropped(self, tmp_path: Path):
        root = tmp_path / "arch"
        # opprcd row for secid=100 date=2010-01-04, but secprd only has
        # secid=100 date=2010-01-05 -- the join misses.
        _write_opprcd_year(root, 2010, [_opprcd_row(date=dt.date(2010, 1, 4))])
        _write_secprd(root, [_secprd_row(date=dt.date(2010, 1, 5))])
        df = loader.load_options(archive_root=root)
        assert df.empty

    def test_expired_contract_row_dropped(self, tmp_path: Path):
        root = tmp_path / "arch"
        # date > exdate -- expired.
        _write_opprcd_year(
            root, 2010,
            [_opprcd_row(date=dt.date(2010, 2, 1),
                         exdate=dt.date(2010, 1, 15))],
        )
        _write_secprd(root, [_secprd_row(date=dt.date(2010, 2, 1))])
        df = loader.load_options(archive_root=root)
        assert df.empty

    def test_non_cp_flag_row_dropped(self, tmp_path: Path):
        root = tmp_path / "arch"
        _write_opprcd_year(root, 2010, [_opprcd_row(cp_flag="X")])
        _write_secprd(root, [_secprd_row()])
        df = loader.load_options(archive_root=root)
        assert df.empty

    def test_crossed_market_preserved(self, tmp_path: Path):
        # Crossed markets are Slice 6's problem, NOT the loader's --
        # the loader emits the row and the schema layer emits a warning.
        root = tmp_path / "arch"
        _write_opprcd_year(root, 2010,
                           [_opprcd_row(best_bid=3.0, best_offer=2.5)])
        _write_secprd(root, [_secprd_row()])
        df = loader.load_options(archive_root=root)
        assert len(df) == 1
        result = sch.validate(df)
        assert result.ok  # not a schema error
        assert any("crossed market" in w for w in result.warnings)

    def test_null_greeks_preserved(self, tmp_path: Path):
        # Vendor didn't converge on the IV solver -- iv/Greeks are NaN.
        # This is a valid, common state; the row still passes canonical
        # validation because iv/Greeks are nullable.
        root = tmp_path / "arch"
        _write_opprcd_year(
            root, 2010,
            [_opprcd_row(impl_volatility=None, delta=None,
                         gamma=None, vega=None, theta=None)],
        )
        _write_secprd(root, [_secprd_row()])
        df = loader.load_options(archive_root=root)
        assert len(df) == 1
        row = df.iloc[0]
        assert pd.isna(row["iv"])
        assert pd.isna(row["delta"])
        assert sch.validate(df).ok


class TestDeduplication:
    def test_duplicate_option_date_dropped(self, tmp_path: Path):
        root = tmp_path / "arch"
        _write_opprcd_year(
            root, 2010,
            [_opprcd_row(optionid=1.0), _opprcd_row(optionid=1.0)],
        )
        _write_secprd(root, [_secprd_row()])
        df = loader.load_options(archive_root=root)
        # Exact duplicate -- keep one.
        assert len(df) == 1

    def test_secprd_master_and_yearly_do_not_fan_out(self, tmp_path: Path):
        # Round-1 review fix: the real archive has BOTH a master
        # secprd.parquet AND yearly secprd<YYYY>.parquet files covering
        # the same (secid, date) pairs. Without the GROUP BY in the
        # join subquery, the read_parquet union would produce two rows
        # per (secid, date) and the JOIN would fan out to two option
        # rows per contract-day. Verify by writing both a master and a
        # yearly with the same (secid, date), then loading and
        # asserting one row per option.
        root = tmp_path / "arch"
        _write_opprcd_year(root, 2010, [_opprcd_row(optionid=1.0)])
        _write_secprd(root, [_secprd_row()], filename="secprd.parquet")
        _write_secprd(root, [_secprd_row()], filename="secprd2010.parquet")
        df = loader.load_options(archive_root=root)
        assert len(df) == 1
        assert df.iloc[0]["option_id"] == 1


class TestPushdownFileSelection:
    def test_out_of_range_year_files_not_read(self, tmp_path: Path):
        # If date_start=2010-01-01 and date_end=2010-12-31, opprcd2009
        # should not be in the file list (evidenced by _select_opprcd_
        # files_for_window in isolation).
        root = tmp_path / "arch"
        for y in (2009, 2010, 2011):
            _write_opprcd_year(root, y, [_opprcd_row()])
        _write_secprd(root, [_secprd_row()])
        picked = loader._select_opprcd_files_for_window(
            root / "parquet",
            date_start=dt.date(2010, 1, 1),
            date_end=dt.date(2010, 12, 31),
        )
        # Confirms opprcd2009 and opprcd2011 are NOT in the scan set.
        assert [Path(p).stem for p in picked] == ["opprcd2010"]


class TestRealArchive:
    """Smoke test against the real /Volumes/copyrishabh/OptionMetrics
    archive. Skipped when the drive isn't mounted, so CI stays green.
    """

    ARCHIVE = loader.DEFAULT_ARCHIVE_ROOT

    @pytest.fixture(autouse=True)
    def _skip_if_no_drive(self):
        if not (self.ARCHIVE / "parquet").exists():
            pytest.skip("External drive not mounted; skipping real-archive smoke test.")

    def test_tiny_slice_loads_and_validates(self):
        # One day, no secid filter -- pulls one full day of opprcd for
        # 2010 which is O(300K rows) -- big enough to catch real dtype
        # / mapping bugs, small enough to run in a few seconds against
        # the parquet.
        df = loader.load_options(
            date_start="2010-01-04",
            date_end="2010-01-04",
        )
        assert not df.empty
        result = sch.validate(df)
        # Real archive has warnings (crossed markets, ...); errors == 0
        # is the acceptance bar.
        assert result.ok, result.errors[:5]
