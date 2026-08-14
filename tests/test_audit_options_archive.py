"""Tests for scripts/audit_options_archive.py.

Uses tiny in-tmp-path fixtures rather than the real 300 GB archive, so
these run in seconds. The real-archive smoke check happens by invoking
``audit_options_archive.py`` directly against
``/Volumes/copyrishabh/OptionMetrics`` when the drive is mounted -- but
CI doesn't have that drive, so the tests here work off synthetic
``sas7bdat``/``parquet`` files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit_options_archive as aoa  # noqa: E402


def _write_parquet(path: Path, table: pa.Table | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if table is None:
        table = pa.table({"x": pa.array([1, 2, 3])})
    pq.write_table(table, path)


def _touch_sas(path: Path, content: bytes = b"fakesas") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_archive(tmp_path: Path, *, sas_names: list[str], parquet_names: list[str],
                  convert_log: str | None = None) -> Path:
    root = tmp_path / "OptionMetrics"
    root.mkdir(parents=True, exist_ok=True)
    for name in sas_names:
        _touch_sas(root / name)
    for name in parquet_names:
        _write_parquet(root / "parquet" / name)
    if convert_log is not None:
        (root / "parquet").mkdir(parents=True, exist_ok=True)
        (root / "parquet" / "convert_log.txt").write_text(convert_log)
    return root


class TestSplitFamilyYear:
    def test_yearly_file(self):
        assert aoa._split_family_year("opprcd2011") == ("opprcd", 2011)

    def test_non_yearly_file(self):
        assert aoa._split_family_year("opinfd") == ("opinfd", None)

    def test_secprd_boundary(self):
        # secprd both has a non-yearly master file (secprd.parquet) AND
        # yearly ones (secprd1997.parquet). Both should classify under
        # family "secprd" but with different years.
        assert aoa._split_family_year("secprd") == ("secprd", None)
        assert aoa._split_family_year("secprd1997") == ("secprd", 1997)


class TestParseConvertLog:
    def test_missing_log_returns_empty(self, tmp_path: Path):
        assert aoa.parse_convert_log(tmp_path / "does-not-exist.txt") == {}

    def test_captures_corrupt_lines(self, tmp_path: Path):
        log = tmp_path / "convert_log.txt"
        log.write_text(
            "[18:24:49] OK opinfd.sas7bdat -> opinfd.parquet\n"
            "[11:02:00] CORRUPT/UNREADABLE opprcd2011.sas7bdat: bad row\n"
            "[12:00:00] ERROR something.sas7bdat: ENOMEM\n"
        )
        result = aoa.parse_convert_log(log)
        assert set(result.keys()) == {"opprcd2011.sas7bdat", "something.sas7bdat"}
        assert "bad row" in result["opprcd2011.sas7bdat"]
        assert "ENOMEM" in result["something.sas7bdat"]


class TestAuditEndToEnd:
    def test_clean_archive_no_findings_beyond_counts(self, tmp_path: Path):
        root = _make_archive(
            tmp_path,
            sas_names=["opinfd.sas7bdat", "opprcd2010.sas7bdat"],
            parquet_names=["opinfd.parquet", "opprcd2010.parquet"],
        )
        summary = aoa.audit(archive_root=root, metadata_dir=tmp_path / "metadata")
        assert summary["counts"]["sas_files"] == 2
        assert summary["counts"]["parquet_files"] == 2
        assert summary["counts"]["sas_without_parquet"] == 0
        codes = {f["code"] for f in summary["findings"]}
        assert "counts" in codes
        assert "sas_without_parquet" not in codes

    def test_missing_parquet_with_log_reason_is_warning(self, tmp_path: Path):
        # Mirrors the real archive's opprcd2011 case: SAS present, log
        # marked it CORRUPT/UNREADABLE, no parquet twin.
        root = _make_archive(
            tmp_path,
            sas_names=["opprcd2010.sas7bdat", "opprcd2011.sas7bdat"],
            parquet_names=["opprcd2010.parquet"],
            convert_log=(
                "[11:02:00] CORRUPT/UNREADABLE opprcd2011.sas7bdat: bad row\n"
            ),
        )
        summary = aoa.audit(archive_root=root, metadata_dir=tmp_path / "metadata")
        gap_findings = [
            f for f in summary["findings"] if f["code"] == "sas_without_parquet"
        ]
        assert len(gap_findings) == 1
        assert gap_findings[0]["severity"] == "warning"
        assert "CORRUPT/UNREADABLE" in gap_findings[0]["message"]

    def test_missing_parquet_no_log_reason_is_error(self, tmp_path: Path):
        root = _make_archive(
            tmp_path,
            sas_names=["opprcd2010.sas7bdat", "opprcd2011.sas7bdat"],
            parquet_names=["opprcd2010.parquet"],
        )
        summary = aoa.audit(archive_root=root, metadata_dir=tmp_path / "metadata")
        gap_findings = [
            f for f in summary["findings"] if f["code"] == "sas_without_parquet"
        ]
        assert len(gap_findings) == 1
        assert gap_findings[0]["severity"] == "error"

    def test_writes_all_three_artifacts(self, tmp_path: Path):
        root = _make_archive(
            tmp_path,
            sas_names=["opinfd.sas7bdat"],
            parquet_names=["opinfd.parquet"],
        )
        metadata = tmp_path / "metadata"
        aoa.audit(archive_root=root, metadata_dir=metadata)
        assert (metadata / "options_archive_inventory.parquet").exists()
        assert (metadata / "options_archive_gaps.csv").exists()
        assert (metadata / "options_archive_summary.json").exists()
        summary_disk = json.loads(
            (metadata / "options_archive_summary.json").read_text()
        )
        assert summary_disk["counts"]["parquet_files"] == 1

    def test_missing_archive_raises_clear_error(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Archive root not found"):
            aoa.audit(
                archive_root=tmp_path / "nope",
                metadata_dir=tmp_path / "metadata",
            )

    def test_no_parquet_folder_still_writes_summary(self, tmp_path: Path):
        # Archive present, but no conversion has been run yet.
        root = tmp_path / "OptionMetrics"
        root.mkdir()
        _touch_sas(root / "opinfd.sas7bdat")
        summary = aoa.audit(
            archive_root=root, metadata_dir=tmp_path / "metadata"
        )
        assert summary["counts"]["parquet_files"] == 0
        assert summary["counts"]["sas_without_parquet"] == 1


class TestFamilySummary:
    def test_year_range_and_missing_within_range(self, tmp_path: Path):
        # Family opprcd has years {2009, 2010, 2012, 2013} in parquet and
        # a SAS-only 2011 (with log reason). Expected: year_range =
        # [2009, 2013], missing_years_within_range = [2011], and no
        # unexplained gap finding because 2011 is in gap_years.
        root = _make_archive(
            tmp_path,
            sas_names=[f"opprcd{y}.sas7bdat" for y in (2009, 2010, 2011, 2012, 2013)],
            parquet_names=[f"opprcd{y}.parquet" for y in (2009, 2010, 2012, 2013)],
            convert_log=(
                "[11:02:00] CORRUPT/UNREADABLE opprcd2011.sas7bdat: bad row\n"
            ),
        )
        summary = aoa.audit(archive_root=root, metadata_dir=tmp_path / "metadata")
        opprcd = summary["families"]["opprcd"]
        assert opprcd["year_range"] == [2009, 2013]
        assert opprcd["missing_years_within_range"] == [2011]
        # No "family_year_gap" for opprcd because 2011 is accounted for
        # in gap_years (has a SAS file with a documented reason).
        family_gaps = [
            f for f in summary["findings"] if f["code"] == "family_year_gap"
        ]
        assert family_gaps == []

    def test_unexplained_missing_year_flagged(self, tmp_path: Path):
        # Parquet years {2009, 2011}, no SAS for 2010 -- true coverage
        # gap, not just a conversion failure.
        root = _make_archive(
            tmp_path,
            sas_names=["opprcd2009.sas7bdat", "opprcd2011.sas7bdat"],
            parquet_names=["opprcd2009.parquet", "opprcd2011.parquet"],
        )
        summary = aoa.audit(archive_root=root, metadata_dir=tmp_path / "metadata")
        family_gaps = [
            f for f in summary["findings"] if f["code"] == "family_year_gap"
        ]
        assert len(family_gaps) == 1
        assert "opprcd" in family_gaps[0]["message"]
        assert "[2010]" in family_gaps[0]["message"]


class TestInventoryReadMetadata:
    def test_captures_row_count_and_columns(self, tmp_path: Path):
        table = pa.table({"secid": pa.array([1, 2, 3]), "date": pa.array([10, 20, 30])})
        _write_parquet(tmp_path / "OptionMetrics" / "parquet" / "opinfd.parquet", table)
        _touch_sas(tmp_path / "OptionMetrics" / "opinfd.sas7bdat")
        summary = aoa.audit(
            archive_root=tmp_path / "OptionMetrics",
            metadata_dir=tmp_path / "metadata",
        )
        inv = pd.read_parquet(
            tmp_path / "metadata" / "options_archive_inventory.parquet"
        )
        assert len(inv) == 1
        assert inv.iloc[0]["num_rows"] == 3
        assert inv.iloc[0]["num_columns"] == 2
        assert set(inv.iloc[0]["columns"]) == {"secid", "date"}
        assert inv.iloc[0]["read_error"] is None

    def test_unreadable_parquet_becomes_error_finding(self, tmp_path: Path):
        root = tmp_path / "OptionMetrics"
        (root / "parquet").mkdir(parents=True)
        # Write a broken "parquet" file: not a valid footer.
        (root / "parquet" / "broken.parquet").write_bytes(b"not a parquet file")
        _touch_sas(root / "broken.sas7bdat")
        summary = aoa.audit(archive_root=root, metadata_dir=tmp_path / "metadata")
        errors = [
            f for f in summary["findings"] if f["code"] == "parquet_read_failed"
        ]
        assert len(errors) == 1
        assert errors[0]["severity"] == "error"


class TestAtomicWrites:
    def test_no_tmp_files_left_on_success(self, tmp_path: Path):
        root = _make_archive(
            tmp_path,
            sas_names=["opinfd.sas7bdat"],
            parquet_names=["opinfd.parquet"],
        )
        metadata = tmp_path / "metadata"
        aoa.audit(archive_root=root, metadata_dir=metadata)
        # No .tmp sidecar should be left behind after a clean run.
        assert list(metadata.glob("*.tmp")) == []
