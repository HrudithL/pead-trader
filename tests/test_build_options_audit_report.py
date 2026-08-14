"""Tests for scripts/build_options_audit_report.py.

Uses tiny synthetic summary JSON fixtures rather than the real archive
summary, so these run in seconds and stay CI-clean. A separate smoke
test at the bottom builds the real report against the actually-audited
archive summary if it's present -- it's marked so it's skipped in
environments where the audit hasn't been run yet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_options_audit_report as boar  # noqa: E402


def _make_summary(
    *,
    families: dict[str, dict] | None = None,
    findings: list[dict] | None = None,
    counts: dict | None = None,
) -> dict:
    return {
        "generated_at": "2026-08-14T12:00:00+00:00",
        "archive_root": "/tmp/fake-archive",
        "archive_root_resolved": "/tmp/fake-archive",
        "counts": counts or {
            "sas_files": 3,
            "parquet_files": 2,
            "sas_without_parquet": 1,
            "unreadable_parquet": 0,
            "families": 2,
        },
        "sizes": {"total_sas_bytes": 0, "total_parquet_bytes": 0, "total_parquet_rows": 0},
        "families": families or {
            "opprcd": {
                "parquet_files": 2,
                "sas_gaps": 1,
                "year_range": [2010, 2013],
                "missing_years_within_range": [2011, 2012],
                "gap_years": [2011],
            },
            "opinfd": {
                "parquet_files": 1,
                "sas_gaps": 0,
                "year_range": None,
                "missing_years_within_range": [],
                "gap_years": [],
            },
        },
        "findings": findings or [
            {"severity": "info", "code": "counts", "message": "2 parquet, 1 gap"},
            {
                "severity": "warning",
                "code": "sas_without_parquet",
                "message": "SAS file opprcd2011.sas7bdat has no parquet conversion. Reason: CORRUPT/UNREADABLE",
            },
        ],
    }


class TestLoadSummary:
    def test_reads_valid_json(self, tmp_path: Path):
        summary = _make_summary()
        summary_path = tmp_path / "s.json"
        summary_path.write_text(json.dumps(summary))
        assert boar.load_summary(summary_path) == summary

    def test_missing_summary_raises_pointer(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="audit_options_archive.py"):
            boar.load_summary(tmp_path / "nope.json")


class TestFamiliesFrame:
    def test_columns_and_relevance_classification(self):
        summary = _make_summary()
        df = boar._families_frame(summary)
        assert set(df.columns) == {
            "family",
            "description",
            "parquet_files",
            "sas_gaps",
            "year_range",
            "relevance",
        }
        opprcd_row = df[df["family"] == "opprcd"].iloc[0]
        assert opprcd_row["relevance"] == "required for Phase III"
        opinfd_row = df[df["family"] == "opinfd"].iloc[0]
        assert opinfd_row["relevance"] == "required for Phase III"

    def test_sorted_required_first(self):
        summary = _make_summary(
            families={
                "vsurfd": {  # not-needed
                    "parquet_files": 0,
                    "sas_gaps": 5,
                    "year_range": None,
                    "missing_years_within_range": [],
                    "gap_years": [],
                },
                "opprcd": {  # required
                    "parquet_files": 5,
                    "sas_gaps": 0,
                    "year_range": [2009, 2013],
                    "missing_years_within_range": [],
                    "gap_years": [],
                },
                "hvold": {  # informational (not required, not in NOT_NEEDED)
                    "parquet_files": 5,
                    "sas_gaps": 0,
                    "year_range": [2009, 2013],
                    "missing_years_within_range": [],
                    "gap_years": [],
                },
            }
        )
        df = boar._families_frame(summary)
        # Required first, then not_needed, then informational.
        assert df["family"].tolist() == ["opprcd", "vsurfd", "hvold"]

    def test_year_range_formatted_when_present(self):
        df = boar._families_frame(_make_summary())
        opprcd = df[df["family"] == "opprcd"].iloc[0]
        assert opprcd["year_range"] == "2010-2013"
        opinfd = df[df["family"] == "opinfd"].iloc[0]
        assert opinfd["year_range"] == "non-yearly"


class TestFindingsSeveritySplit:
    def test_relevant_vs_not_relevant(self):
        summary = _make_summary(
            findings=[
                {
                    "severity": "error",
                    "code": "sas_without_parquet",
                    "message": "SAS file vsurfd2010.sas7bdat has no parquet conversion. Reason: no parquet file",
                },
                {
                    "severity": "error",
                    "code": "sas_without_parquet",
                    "message": "SAS file opprcd2011.sas7bdat has no parquet conversion. Reason: bad row",
                },
                {
                    "severity": "warning",
                    "code": "sas_without_parquet",
                    "message": "SAS file stdopd2010.sas7bdat has no parquet conversion. Reason: no parquet file",
                },
            ]
        )
        err_total, warn_total, rel_errs, rel_warns = boar._findings_severity_summary(
            summary
        )
        assert err_total == 2
        assert warn_total == 1
        # Only opprcd is required, so exactly 1 error and 0 warnings are relevant.
        assert len(rel_errs) == 1
        assert "opprcd" in rel_errs[0]["message"]
        assert rel_warns == []


class TestNarrativeBodies:
    def test_summary_narrative_flags_relevance_present(self):
        summary = _make_summary(
            findings=[
                {
                    "severity": "error",
                    "code": "sas_without_parquet",
                    "message": "SAS file opprcd2011.sas7bdat has no parquet conversion.",
                }
            ]
        )
        text = boar._summary_narrative(summary)
        assert "Phase III actually needs" in text

    def test_summary_narrative_flags_no_relevant_findings(self):
        summary = _make_summary(
            findings=[
                {"severity": "info", "code": "counts", "message": "hi"},
                {
                    "severity": "error",
                    "code": "sas_without_parquet",
                    "message": "SAS file vsurfd2010.sas7bdat has no parquet conversion.",
                },
            ]
        )
        text = boar._summary_narrative(summary)
        assert "none touch tables Phase III actually needs" in text

    def test_relevant_findings_body_empty_case(self):
        summary = _make_summary(
            findings=[
                {"severity": "info", "code": "counts", "message": "hi"},
                {
                    "severity": "error",
                    "code": "sas_without_parquet",
                    "message": "SAS file vsurfd2010.sas7bdat has no parquet conversion.",
                },
            ]
        )
        body = boar._relevant_findings_body(summary)
        assert "No errors or warnings" in body

    def test_other_findings_body_lists_affected_families(self):
        summary = _make_summary(
            families={
                "opprcd": {"parquet_files": 1, "sas_gaps": 0, "year_range": [2010, 2010], "missing_years_within_range": [], "gap_years": []},
                "vsurfd": {"parquet_files": 0, "sas_gaps": 1, "year_range": None, "missing_years_within_range": [], "gap_years": []},
                "stdopd": {"parquet_files": 0, "sas_gaps": 1, "year_range": None, "missing_years_within_range": [], "gap_years": []},
            },
            findings=[
                {"severity": "error", "code": "sas_without_parquet", "message": "SAS file vsurfd2010.sas7bdat has no parquet conversion."},
                {"severity": "error", "code": "sas_without_parquet", "message": "SAS file stdopd2010.sas7bdat has no parquet conversion."},
            ],
        )
        body = boar._other_findings_body(summary)
        # Both non-required families should appear in the "affected" list.
        assert "vsurfd" in body
        assert "stdopd" in body


class TestFormatFamiliesTable:
    def test_empty_frame_produces_placeholder(self):
        assert boar._format_families_table(pd.DataFrame()) == "(no families in inventory)"

    def test_header_row_present(self):
        df = boar._families_frame(_make_summary())
        text = boar._format_families_table(df)
        assert text.splitlines()[0].startswith("family")


class TestEndToEndRender:
    def test_writes_pdf_to_output_path(self, tmp_path: Path):
        # weasyprint needs system libs; if any of pango/cairo isn't
        # installed on this host, skip rather than crash.
        try:
            import weasyprint  # noqa: F401
        except Exception:  # pragma: no cover
            pytest.skip("weasyprint not importable in this environment")
        summary_path = tmp_path / "s.json"
        summary_path.write_text(json.dumps(_make_summary()))
        out = tmp_path / "audit.pdf"
        result = boar.build_options_audit_report(
            summary_json=summary_path, output_path=out
        )
        assert result == out
        assert out.exists()
        # weasyprint's minimum PDF file size is a few kB; a truncated
        # render (say < 500 bytes) would be a red flag.
        assert out.stat().st_size > 1_000


class TestRequiredFamilyGuardRails:
    def test_required_and_not_needed_do_not_overlap(self):
        # Sanity check that we haven't accidentally listed a family
        # in both PHASE3_REQUIRED_FAMILIES and PHASE3_NOT_NEEDED_FAMILIES.
        overlap = set(boar.PHASE3_REQUIRED_FAMILIES) & set(
            boar.PHASE3_NOT_NEEDED_FAMILIES
        )
        assert overlap == set()

    def test_required_families_all_have_descriptions(self):
        # If a required family shows up in the report table without a
        # description, the reader sees "(no description)" for something
        # that's supposed to matter -- catches copy-paste omissions.
        missing = [
            fam for fam in boar.PHASE3_REQUIRED_FAMILIES if fam not in boar._FAMILY_DESCRIPTIONS
        ]
        assert missing == [], f"Required families missing description: {missing}"
