"""Slice 13 of memory/phase3-options-pead-plan.md: daily underlying-level
IV history table.

Purpose
-------
For every (``stable_underlying_id``, ``date``) pair in the archive,
emit the ATM implied vol at three standard tenors (30 / 60 / 90 days)
plus aggregate option volume and open interest. Built once over the
full sample so the ``iv_percentile_1y`` feature (Slice 14, future) has
real trailing-year history at each event date -- Slice 9's minus-30-
day event window is not enough to compute a percentile against 250
trading days of context.

Contract
--------
Output schema:

    stable_underlying_id  string   (canonical schema's ID)
    date                  date     (observation date)
    atm_iv_30d            float64  ATM IV at ~30-day tenor
    atm_iv_60d            float64  ATM IV at ~60-day tenor
    atm_iv_90d            float64  ATM IV at ~90-day tenor
    total_option_volume   int64    sum of volume across all contracts
    total_open_interest   int64    sum of open interest across all contracts

The ATM contract for each tenor bucket is the one whose ``strike`` is
closest (absolute log-moneyness) to the ``underlying_price`` on that
date, restricted to the tenor bucket's DTE range.

Definitions
-----------
- 30-day tenor bucket: DTE ∈ [15, 45]
- 60-day tenor bucket: DTE ∈ [45, 75]
- 90-day tenor bucket: DTE ∈ [75, 120]

Overlapping buckets on the seams intentionally: a 45-day contract
picks up in both the 30-day and 60-day columns, so a day with only
one usable contract at the boundary still populates both columns
rather than leaving a hole.

Nulls are emitted when a bucket has no contract with an ``iv`` value
that day, or no contract in the bucket at all. Downstream percentile
computations must handle nulls (skip them) rather than treat them as
zero.

Usage
-----
Programmatic:

    from build_iv_history import build_iv_history_frame
    iv_history = build_iv_history_frame(canonical_options_df)

CLI (one-shot batch over a year range):

    .venv/bin/python scripts/build_iv_history.py --start-year 2010 --end-year 2013
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import options_loader_archive as loader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "data" / "features" / "iv_history_daily.parquet"

# Tenor buckets: (column suffix, DTE lower bound inclusive, DTE upper
# bound inclusive). Overlapping seams are intentional (see module
# docstring).
_TENOR_BUCKETS: list[tuple[str, int, int]] = [
    ("30d", 15, 45),
    ("60d", 45, 75),
    ("90d", 75, 120),
]

_OUTPUT_COLUMNS: list[str] = [
    "stable_underlying_id",
    "date",
    "atm_iv_30d",
    "atm_iv_60d",
    "atm_iv_90d",
    "total_option_volume",
    "total_open_interest",
]


def _pick_atm_iv(group: pd.DataFrame, dte_lo: int, dte_hi: int) -> float:
    """Return the ATM IV for the tenor bucket, or NaN if none.

    Ties on absolute strike-vs-underlying distance are broken
    deterministically: sort the bucket by (abs_diff, option_id) before
    calling ``idxmin`` so the choice does not depend on the caller's
    input order.
    """
    dte = (group["expiration"] - group["date"]).dt.days
    mask = (dte >= dte_lo) & (dte <= dte_hi) & group["iv"].notna()
    bucket = group.loc[mask]
    if bucket.empty:
        return float("nan")
    abs_diff = (bucket["strike"] - bucket["underlying_price"]).abs()
    tie_break = bucket.assign(_abs_diff=abs_diff).sort_values(
        ["_abs_diff", "option_id"], kind="stable"
    )
    return float(tie_break.iloc[0]["iv"])


def build_iv_history_frame(options: pd.DataFrame) -> pd.DataFrame:
    """Turn a canonical options frame into a daily IV history frame.

    ``options`` must have the canonical column set (schema module's
    ``CANONICAL_COLUMN_NAMES``); anything satisfying
    ``options_canonical_schema.validate()`` works.

    Output has one row per (``stable_underlying_id``, ``date``) with
    ATM IV at three tenors and aggregate volume/OI.
    """
    if options.empty:
        return _empty_typed_output()

    # Canonical schema guarantees non-null grouping keys; enforce here
    # so a partially-loaded frame doesn't silently drop rows in pandas'
    # groupby (which discards NaN-keyed rows by default).
    for key in ("stable_underlying_id", "date"):
        if options[key].isna().any():
            n = int(options[key].isna().sum())
            raise ValueError(
                f"build_iv_history_frame: {n} row(s) have a null {key!r}; "
                f"the canonical schema requires this column to be non-null."
            )

    # Two-pass build: (1) aggregate volume + OI via groupby.agg, which
    # is fast and produces a clean two-column-index DataFrame, then
    # (2) compute the ATM-IV columns by iterating groups. Using
    # groupby.apply on a Series-returning function tripped up pandas
    # versions on whether grouping keys survive to reset_index; the
    # split avoids that pitfall.
    aggregates = (
        options.groupby(
            ["stable_underlying_id", "date"], sort=True
        )
        .agg(
            total_option_volume=("volume", _sum_safe),
            total_open_interest=("open_interest", _sum_safe),
        )
        .reset_index()
    )

    atm_rows: list[dict] = []
    for (uid, dte_date), group in options.groupby(
        ["stable_underlying_id", "date"], sort=True
    ):
        atm_rows.append({
            "stable_underlying_id": uid,
            "date": dte_date,
            **{
                f"atm_iv_{suffix}": _pick_atm_iv(group, lo, hi)
                for suffix, lo, hi in _TENOR_BUCKETS
            },
        })
    atm = pd.DataFrame(atm_rows)

    out = (
        aggregates.merge(atm, on=["stable_underlying_id", "date"], how="left")
        # Enforce a stable, groupby-sort-independent output order so
        # downstream consumers see the same row order across every
        # invocation, regardless of the merge's input side ordering.
        .sort_values(["stable_underlying_id", "date"], kind="stable")
        .reset_index(drop=True)
    )
    return out[_OUTPUT_COLUMNS]


def _empty_typed_output() -> pd.DataFrame:
    """Empty DataFrame with the correct column order AND the correct
    dtype per column, so an empty-batch write is round-trip-consistent
    with a non-empty one.
    """
    df = pd.DataFrame({
        "stable_underlying_id": pd.Series([], dtype="string"),
        "date": pd.Series([], dtype="datetime64[ns]"),
        "atm_iv_30d": pd.Series([], dtype="float64"),
        "atm_iv_60d": pd.Series([], dtype="float64"),
        "atm_iv_90d": pd.Series([], dtype="float64"),
        "total_option_volume": pd.Series([], dtype="int64"),
        "total_open_interest": pd.Series([], dtype="int64"),
    })
    return df[_OUTPUT_COLUMNS]


def _sum_safe(s: pd.Series) -> int:
    """Sum a nullable-integer column, returning 0 on all-null groups.

    Guards against ``pd.NA -> int()`` blowing up on Int64 columns where
    every value is null: ``.sum()`` on all-null Int64 returns ``pd.NA``
    and ``int(pd.NA)`` raises. Explicit ``fillna(0).sum()`` on the
    numpy view sidesteps both issues.
    """
    total = s.fillna(0).sum()
    if pd.isna(total):
        return 0
    return int(total)


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def build_iv_history_for_range(
    *,
    start_year: int,
    end_year: int,
    archive_root: Path = loader.DEFAULT_ARCHIVE_ROOT,
    output_path: Path = DEFAULT_OUTPUT,
    secids: list[int] | None = None,
) -> Path:
    """One-shot batch build over ``[start_year, end_year]``, one year
    at a time so the loader's DuckDB query touches only the relevant
    ``opprcd`` files.

    Writes the concatenated result to ``output_path`` (atomic rename).
    Returns the resolved output path.
    """
    frames: list[pd.DataFrame] = []
    for year in range(start_year, end_year + 1):
        yr_start = dt.date(year, 1, 1)
        yr_end = dt.date(year, 12, 31)
        year_options = loader.load_options(
            secids=secids,
            date_start=yr_start,
            date_end=yr_end,
            archive_root=archive_root,
        )
        if year_options.empty:
            continue
        frames.append(build_iv_history_frame(year_options))

    if not frames:
        history = _empty_typed_output()
    else:
        history = pd.concat(frames, ignore_index=True)

    _atomic_write_parquet(history, output_path)
    return output_path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build data/features/iv_history_daily.parquet from the archive."
    )
    p.add_argument("--start-year", type=int, required=True)
    p.add_argument("--end-year", type=int, required=True)
    p.add_argument("--archive-root", type=Path, default=loader.DEFAULT_ARCHIVE_ROOT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.start_year > args.end_year:
        raise SystemExit(
            f"--start-year ({args.start_year}) must be <= --end-year ({args.end_year})"
        )
    out = build_iv_history_for_range(
        start_year=args.start_year,
        end_year=args.end_year,
        archive_root=args.archive_root,
        output_path=args.output,
    )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
