"""Slice 2 of memory/phase3-options-pead-plan.md: audit the OptionMetrics
archive on the external drive.

Purpose
-------
Re-verify the two-day-old §6b claim ("65 SAS files, 65 Parquet files, all
converted") against the actually-mounted filesystem, and emit a structured
inventory that the Slice 3 audit report reads. Read-only against the
archive; never writes back to it.

What it does
------------
1. Enumerates ``*.sas7bdat`` files directly under the archive root.
2. Enumerates ``*.parquet`` files under the archive root's ``parquet/``
   subfolder.
3. For each parquet file, opens the footer via ``pyarrow.parquet`` and
   records column count, row count (from parquet metadata -- no data
   scan), file size, and the top-level schema field names + logical
   types.
4. Cross-references SAS vs Parquet by base name; every SAS file without a
   parquet twin becomes a gap row with a reason ("no parquet file"
   / "conversion log marked corrupt" when applicable).
5. Parses ``parquet/convert_log.txt`` for ``CORRUPT/UNREADABLE`` /
   ``ERROR`` entries and cross-references them against the gaps so every
   missing conversion has an explanation instead of just a missing file.
6. Groups files into "table families" (e.g. ``opprcd`` is one family made
   of many yearly ``opprcd1996.parquet`` .. ``opprcd2013.parquet`` files),
   computes the per-family covered-year range from filenames, and lists
   any missing years inside that range.

Output artifacts (all under ``data/metadata/``, gitignored, regenerable):
    options_archive_inventory.parquet  -- one row per parquet file
    options_archive_gaps.csv           -- SAS files without a parquet twin
    options_archive_summary.json       -- top-level counts + per-family
                                          summary + findings list

Usage:
    .venv/bin/python scripts/audit_options_archive.py \\
        [--archive-root data/raw_options/OptionMetrics] \\
        [--metadata-dir data/metadata]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_ROOT = REPO_ROOT / "data" / "raw_options" / "OptionMetrics"
DEFAULT_METADATA_DIR = REPO_ROOT / "data" / "metadata"

# Filename pattern for IvyDB yearly tables: <family><YYYY>.<ext>.
# Non-yearly tables (opinfd, securd, ...) have no digit suffix and match
# with empty year group.
_FAMILY_YEAR_RE = re.compile(r"^([a-z_]+?)(\d{4})?$")

# Substrings the SAS->Parquet conversion log used when it gave up on a
# specific SAS file. Anchored on log lines like:
#   [11:02:00] CORRUPT/UNREADABLE opprcd2011.sas7bdat: A row in the file
#   was not the expected length -- skipping
_CONVERT_LOG_ERROR_TOKENS = ("CORRUPT/UNREADABLE", "ERROR", "FAILED")


def _split_family_year(base_name: str) -> tuple[str, int | None]:
    """Return (family, year) for a table basename.

    Every IvyDB table basename in this archive either ends in a 4-digit
    year (yearly slice, e.g. ``opprcd2011``) or doesn't (non-yearly, e.g.
    ``opinfd``). Anything that matches neither shape falls back to
    (basename, None) so the caller still gets a row in the inventory
    rather than silently dropping it.
    """
    match = _FAMILY_YEAR_RE.match(base_name)
    if match is None:
        return base_name, None
    family, year = match.group(1), match.group(2)
    return family, int(year) if year is not None else None


def list_sas_files(archive_root: Path) -> list[Path]:
    """SAS source files sitting directly under the archive root.

    ``sas7bndx`` sidecar index files are excluded -- they're not a data
    source, only a companion to ``sas7bdat``.
    """
    if not archive_root.exists():
        raise FileNotFoundError(
            f"Archive root not found: {archive_root}. Is the external drive "
            f"mounted? (Expected the symlink data/raw_options/OptionMetrics "
            f"to resolve to /Volumes/copyrishabh/OptionMetrics.)"
        )
    return sorted(archive_root.glob("*.sas7bdat"))


def list_parquet_files(archive_root: Path) -> list[Path]:
    """Parquet files under the archive's ``parquet/`` subfolder.

    Returns [] rather than raising if the parquet folder does not exist --
    the caller is expected to surface that in the summary as "no
    conversion present" instead of crashing the whole audit.
    """
    parquet_dir = archive_root / "parquet"
    if not parquet_dir.exists():
        return []
    return sorted(parquet_dir.glob("*.parquet"))


def read_parquet_metadata(path: Path) -> dict:
    """Non-scanning read: opens the parquet footer and returns just its
    schema + row count + file size.

    Uses ``pyarrow.parquet.ParquetFile`` which reads only the footer, so
    this stays fast even against multi-GB annual option-price files. Any
    IO / decoding failure is captured as a ``read_error`` field rather
    than raising, so one bad file does not block the whole audit.
    """
    file_size_bytes = path.stat().st_size
    try:
        pf = pq.ParquetFile(path)
        schema = pf.schema_arrow
        return {
            "num_rows": int(pf.metadata.num_rows),
            "num_columns": int(len(schema)),
            "columns": list(schema.names),
            "column_types": [str(schema.field(i).type) for i in range(len(schema))],
            "num_row_groups": int(pf.num_row_groups),
            "file_size_bytes": int(file_size_bytes),
            "read_error": None,
        }
    except Exception as exc:  # noqa: BLE001 -- any read failure is a finding
        return {
            "num_rows": None,
            "num_columns": None,
            "columns": [],
            "column_types": [],
            "num_row_groups": None,
            "file_size_bytes": int(file_size_bytes),
            "read_error": f"{type(exc).__name__}: {exc}",
        }


def parse_convert_log(log_path: Path) -> dict[str, str]:
    """Parse the SAS->Parquet conversion log for per-table failure reasons.

    Returns a dict mapping ``<sas_basename>.sas7bdat`` -> reason string,
    for every log line matching a token in ``_CONVERT_LOG_ERROR_TOKENS``.
    Returns {} if the log file is missing (older archives may not have
    one).
    """
    if not log_path.exists():
        return {}
    reasons: dict[str, str] = {}
    for line in log_path.read_text(errors="replace").splitlines():
        # Convert-log lines look like:
        #   [11:02:00] CORRUPT/UNREADABLE opprcd2011.sas7bdat: A row ...
        # or:
        #   [18:24:49] ERROR foo.sas7bdat: ...
        if not any(tok in line for tok in _CONVERT_LOG_ERROR_TOKENS):
            continue
        match = re.search(r"([a-z0-9_]+\.sas7bdat)", line)
        if match is None:
            continue
        reasons[match.group(1)] = line.strip()
    return reasons


def build_inventory(
    parquet_files: Iterable[Path],
) -> pd.DataFrame:
    """One row per parquet file, with schema + row count + family/year."""
    rows: list[dict] = []
    for path in parquet_files:
        base = path.stem
        family, year = _split_family_year(base)
        meta = read_parquet_metadata(path)
        rows.append(
            {
                "path": str(path),
                "basename": base,
                "family": family,
                "year": year,
                **meta,
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "path",
                "basename",
                "family",
                "year",
                "num_rows",
                "num_columns",
                "columns",
                "column_types",
                "num_row_groups",
                "file_size_bytes",
                "read_error",
            ]
        )
    return pd.DataFrame(rows)


def build_gaps(
    sas_files: Iterable[Path],
    parquet_files: Iterable[Path],
    convert_log_reasons: dict[str, str],
) -> pd.DataFrame:
    """SAS files without a parquet twin, one row each.

    The reason field is:
      - the convert-log entry, if the log recorded a failure for that
        specific SAS file (this is the "we tried and it broke"
        explanation);
      - otherwise the plain string "no parquet file" (this is the "we
        never tried, or the try left no log line" case, which is itself
        a finding).
    """
    parquet_stems = {p.stem for p in parquet_files}
    rows: list[dict] = []
    for sas in sas_files:
        if sas.stem in parquet_stems:
            continue
        reason = convert_log_reasons.get(sas.name, "no parquet file")
        family, year = _split_family_year(sas.stem)
        rows.append(
            {
                "sas_path": str(sas),
                "sas_basename": sas.stem,
                "family": family,
                "year": year,
                "sas_size_bytes": int(sas.stat().st_size),
                "reason": reason,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "sas_path",
            "sas_basename",
            "family",
            "year",
            "sas_size_bytes",
            "reason",
        ],
    )


def summarize_families(
    inventory: pd.DataFrame, gaps: pd.DataFrame
) -> dict[str, dict]:
    """Per-family rollup: parquet count, sas count, year range, missing
    years inside that range.

    Missing years are the arithmetic gap between the family's covered
    parquet years, not just the set of SAS files without parquet twins
    (those are also reported, but a missing year with no SAS file at all
    is a coverage gap of its own -- worth flagging separately in the
    audit).
    """
    fam_parquet: dict[str, list[int | None]] = defaultdict(list)
    for _, row in inventory.iterrows():
        fam_parquet[row["family"]].append(row["year"])

    fam_gaps: dict[str, list[int | None]] = defaultdict(list)
    for _, row in gaps.iterrows():
        fam_gaps[row["family"]].append(row["year"])

    families = sorted(set(fam_parquet) | set(fam_gaps))
    out: dict[str, dict] = {}
    for fam in families:
        # pandas boxes ints-with-NaN as float64; coerce to plain int and
        # drop NaNs so the arithmetic below (range(), set membership) is
        # not silently wrong.
        parquet_years = sorted(
            int(y) for y in fam_parquet.get(fam, []) if y is not None and pd.notna(y)
        )
        gap_years = sorted(
            int(y) for y in fam_gaps.get(fam, []) if y is not None and pd.notna(y)
        )
        year_range: tuple[int, int] | None
        missing_within_range: list[int] = []
        if parquet_years:
            year_range = (parquet_years[0], parquet_years[-1])
            covered = set(parquet_years)
            missing_within_range = [
                y for y in range(year_range[0], year_range[1] + 1) if y not in covered
            ]
        else:
            year_range = None
        out[fam] = {
            "parquet_files": len(fam_parquet.get(fam, [])),
            "sas_gaps": len(fam_gaps.get(fam, [])),
            "year_range": list(year_range) if year_range is not None else None,
            "missing_years_within_range": missing_within_range,
            "gap_years": gap_years,
        }
    return out


def collect_findings(
    inventory: pd.DataFrame,
    gaps: pd.DataFrame,
    family_summary: dict[str, dict],
) -> list[dict]:
    """Turn the inventory and gaps into a flat list of {severity, message}
    findings that Slice 3's report renders verbatim.

    Severity is a plain string: "info" for things worth reporting even
    when nothing's wrong (raw counts), "warning" for gaps that are
    explained by the convert log, "error" for anything unexpected
    (unreadable parquet, missing year inside a family's covered range
    with no corresponding SAS file, etc.).
    """
    findings: list[dict] = []

    findings.append(
        {
            "severity": "info",
            "code": "counts",
            "message": (
                f"{len(inventory)} parquet file(s) present, "
                f"{len(gaps)} SAS file(s) without a parquet twin."
            ),
        }
    )

    unreadable = inventory[inventory["read_error"].notna()]
    for _, row in unreadable.iterrows():
        findings.append(
            {
                "severity": "error",
                "code": "parquet_read_failed",
                "message": (
                    f"parquet file {row['basename']} failed to read: "
                    f"{row['read_error']}"
                ),
            }
        )

    for _, row in gaps.iterrows():
        # Convert-log-explained gaps are warnings (documented failures);
        # unexplained gaps are errors (silent missing conversion).
        severity = (
            "warning"
            if row["reason"] != "no parquet file"
            else "error"
        )
        findings.append(
            {
                "severity": severity,
                "code": "sas_without_parquet",
                "message": (
                    f"SAS file {row['sas_basename']}.sas7bdat has no parquet "
                    f"conversion. Reason: {row['reason']}"
                ),
            }
        )

    for fam, info in family_summary.items():
        missing_in_range = info["missing_years_within_range"]
        if not missing_in_range:
            continue
        gap_years = set(info["gap_years"])
        missing_not_in_gaps = [y for y in missing_in_range if y not in gap_years]
        if missing_not_in_gaps:
            findings.append(
                {
                    "severity": "error",
                    "code": "family_year_gap",
                    "message": (
                        f"Family {fam!r} covers years "
                        f"{info['year_range'][0]}-{info['year_range'][1]} but is "
                        f"missing {missing_not_in_gaps} entirely (no SAS file, no "
                        f"parquet file)."
                    ),
                }
            )

    return findings


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write a DataFrame to Parquet via a sibling temp file, then rename.

    Follows the same pattern the WRDS pull scripts use so a crashed audit
    never leaves half-written state readable to Slice 3.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)


def _atomic_write_json(obj: dict, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    tmp.replace(path)


def audit(
    archive_root: Path = DEFAULT_ARCHIVE_ROOT,
    metadata_dir: Path = DEFAULT_METADATA_DIR,
) -> dict:
    """End-to-end audit: enumerate, inspect, cross-reference, and write
    the three metadata artifacts.

    Returns the summary dict (same content that's written to the JSON
    file) so callers -- Slice 3's audit report and this slice's own
    tests -- can consume it without re-reading disk.
    """
    metadata_dir.mkdir(parents=True, exist_ok=True)

    sas_files = list_sas_files(archive_root)
    parquet_files = list_parquet_files(archive_root)
    convert_log_reasons = parse_convert_log(archive_root / "parquet" / "convert_log.txt")

    inventory = build_inventory(parquet_files)
    gaps = build_gaps(sas_files, parquet_files, convert_log_reasons)
    family_summary = summarize_families(inventory, gaps)
    findings = collect_findings(inventory, gaps, family_summary)

    # num_rows / file_size_bytes may contain None for unreadable parquet
    # files (see read_parquet_metadata's error branch). Drop those before
    # summing so pandas doesn't emit an object-downcast FutureWarning.
    total_parquet_rows = int(inventory["num_rows"].dropna().astype("int64").sum())
    total_parquet_bytes = int(
        inventory["file_size_bytes"].dropna().astype("int64").sum()
    )
    total_sas_bytes = sum(p.stat().st_size for p in sas_files)

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "archive_root": str(archive_root),
        "archive_root_resolved": str(archive_root.resolve()),
        "counts": {
            "sas_files": len(sas_files),
            "parquet_files": len(parquet_files),
            "sas_without_parquet": int(len(gaps)),
            "unreadable_parquet": int(inventory["read_error"].notna().sum()),
            "families": len(family_summary),
        },
        "sizes": {
            "total_sas_bytes": int(total_sas_bytes),
            "total_parquet_bytes": total_parquet_bytes,
            "total_parquet_rows": total_parquet_rows,
        },
        "families": family_summary,
        "findings": findings,
    }

    _atomic_write_parquet(inventory, metadata_dir / "options_archive_inventory.parquet")
    _atomic_write_csv(gaps, metadata_dir / "options_archive_gaps.csv")
    _atomic_write_json(summary, metadata_dir / "options_archive_summary.json")

    return summary


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audit the OptionMetrics archive on the external drive.",
    )
    p.add_argument(
        "--archive-root",
        type=Path,
        default=DEFAULT_ARCHIVE_ROOT,
        help=f"Path to the OptionMetrics archive (default: {DEFAULT_ARCHIVE_ROOT}).",
    )
    p.add_argument(
        "--metadata-dir",
        type=Path,
        default=DEFAULT_METADATA_DIR,
        help=f"Where to write inventory + gaps + summary (default: {DEFAULT_METADATA_DIR}).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    summary = audit(archive_root=args.archive_root, metadata_dir=args.metadata_dir)
    print(json.dumps(summary["counts"], indent=2))
    error_count = sum(1 for f in summary["findings"] if f["severity"] == "error")
    warning_count = sum(1 for f in summary["findings"] if f["severity"] == "warning")
    print(
        f"Findings: {error_count} error(s), {warning_count} warning(s). "
        f"Full summary at "
        f"{Path(args.metadata_dir).resolve() / 'options_archive_summary.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
