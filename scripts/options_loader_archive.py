"""Slice 5b of memory/phase3-options-pead-plan.md: Path B archive loader.

Purpose
-------
Reads the OptionMetrics IvyDB parquet archive under
``data/raw_options/OptionMetrics/parquet/`` (symlinked to
``/Volumes/copyrishabh/OptionMetrics``) and emits rows conforming to
the canonical option-observation schema defined in
``scripts/options_canonical_schema.py``.

Contract
--------
Loader interface:

    load_options(
        secids: Iterable[int] | None = None,
        date_start: pd.Timestamp | date | str | None = None,
        date_end:   pd.Timestamp | date | str | None = None,
        archive_root: Path = DEFAULT_ARCHIVE_ROOT,
    ) -> pd.DataFrame

The returned frame satisfies ``options_canonical_schema.validate()``.
Filters push down through DuckDB directly to the parquet files, so a
query for one ``secid`` over one year touches only that year's
``opprcd`` file and the matching ``secid`` slice of it, not the whole
archive.

Field mapping (IvyDB -> canonical):

    opprcd.optionid          -> option_id
    opprcd.secid             -> stable_underlying_id     (formatted "ivydb_secid_<int>")
    opprcd.date              -> date
    opprcd.exdate            -> expiration
    opprcd.strike_price / 1000 -> strike                 (IvyDB stores strike * 1000)
    opprcd.cp_flag           -> call_put                 (already "C"/"P")
    opprcd.best_bid          -> bid
    opprcd.best_offer        -> ask
    opprcd.volume            -> volume
    opprcd.open_interest     -> open_interest
    opprcd.impl_volatility   -> iv
    opprcd.delta             -> delta
    opprcd.gamma             -> gamma
    opprcd.vega              -> vega
    opprcd.theta             -> theta
    secprd.close             -> underlying_price         (joined on secid + date)
    (constant 100)           -> multiplier               (per plan Assumption:
                                                         "multiplier is 100
                                                         unless the data says
                                                         otherwise" -- IvyDB's
                                                         opinfd has no
                                                         multiplier field, so
                                                         the default holds)
    (constant string)        -> source                   "archive_ivydb_1996_2013"

Raw provenance columns (``raw_strike_price``, ``raw_symbol``,
``raw_root``, ``raw_suffix``) are attached after the canonical block so
downstream slices (contract selection, adjustment handling) have access
to the vendor's original values without re-reading the archive.

Rows dropped at load time
-------------------------
Only rows the canonical schema literally rejects are dropped here:

  - rows without an ``underlying_price`` match (secprd missing that
    secid-date -- the observation is unusable without a spot price for
    the canonical schema's ``underlying_price`` non-null rule);
  - rows with ``date > exdate`` (observations of an already-expired
    contract are out of scope by design);
  - rows with a non-``{C, P}`` ``cp_flag``.

Everything else (crossed markets, extreme IV outliers, stale quotes,
zero volume, adjusted contracts) is preserved for the Slice 6 quality
gate to classify. The loader's job is to produce a canonical frame,
not to police it.

Usage
-----
    df = load_options(
        secids=[101594, 106203],
        date_start="2010-01-01",
        date_end="2010-03-31",
    )
    from options_canonical_schema import validate
    result = validate(df)
    assert result.ok
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import options_canonical_schema as sch  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_ROOT = REPO_ROOT / "data" / "raw_options" / "OptionMetrics"

# Data source tag written to every row's ``source`` column. Explicit and
# versioned so a later loader (e.g. a re-derivation from re-converted
# SAS files after a fixed opprcd2011) shows up as a distinct value on
# downstream results.
SOURCE_TAG: str = "archive_ivydb_1996_2013"

# Default multiplier for equity options (100 shares per contract).
# IvyDB's ``opinfd`` in this archive does not carry a multiplier field
# (see plan Update 2026-08-14 / plan Assumptions: "multiplier is 100
# unless the data says otherwise"). This is the "unless" default; the
# Slice 6 quality gate is the escape hatch for detecting non-standard
# multipliers if a future data source carries them.
DEFAULT_MULTIPLIER: int = 100

# IvyDB stores ``strike_price`` scaled by 1000 (integer cents-of-a-mill
# convention). Divide on load so canonical ``strike`` is in the
# underlying's currency at face value.
STRIKE_PRICE_SCALE: int = 1000

# opprcd filename pattern: ``opprcd<YYYY>.parquet``. Enumerated at load
# time so a missing year (e.g. opprcd2011 in this archive, per Slice 2's
# findings) shows up as "no rows for that window" rather than an
# unhandled file-not-found deep in the join.
_OPPRCD_GLOB = "opprcd????.parquet"


class ArchiveLoaderError(RuntimeError):
    """Raised when the archive is not in the shape the loader expects.

    Distinct from a schema-validation failure on loader output --
    schema failures raise ``SchemaValidationError``; archive-shape
    failures raise this so callers can distinguish "the data is missing"
    from "the loader emitted an invalid frame."
    """


def _resolve_archive_root(archive_root: Path) -> Path:
    """Return the parquet-holding directory, or raise a pointer error."""
    archive_root = Path(archive_root)  # accept str inputs too
    parquet_dir = archive_root / "parquet"
    if not parquet_dir.exists():
        raise ArchiveLoaderError(
            f"OptionMetrics parquet directory not found at {parquet_dir}. "
            f"Is the external drive mounted? (Expected the symlink "
            f"{archive_root} to resolve to /Volumes/copyrishabh/OptionMetrics.)"
        )
    return parquet_dir


def _list_opprcd_years(parquet_dir: Path) -> list[int]:
    """Yearly ``opprcd<YYYY>.parquet`` files present, sorted ascending."""
    years: list[int] = []
    for path in sorted(parquet_dir.glob(_OPPRCD_GLOB)):
        stem = path.stem  # opprcd2010
        try:
            years.append(int(stem[len("opprcd"):]))
        except ValueError:
            continue  # e.g. an unrelated file that happened to match the glob
    return sorted(years)


def _list_secprd_files(parquet_dir: Path) -> list[str]:
    """Every ``secprd*.parquet`` path (both the master ``secprd.parquet``
    and the yearly ``secprd<YYYY>.parquet`` files, if both exist)
    as strings suitable for DuckDB's ``read_parquet`` list argument.

    IvyDB archives sometimes have both a master and yearly cuts. Both
    are unioned for the join -- DuckDB de-duplicates on the (secid,
    date) join key anyway because ``secprd`` is one row per
    (secid, date).
    """
    return [str(p) for p in sorted(parquet_dir.glob("secprd*.parquet"))]


def _select_opprcd_files_for_window(
    parquet_dir: Path,
    date_start: dt.date | None,
    date_end: dt.date | None,
) -> list[str]:
    """Only the ``opprcd`` yearly files whose year overlaps the window.

    A no-op (returns every year) if either bound is None. Trims the
    file list DuckDB has to scan -- a query for one month in 2010 does
    not open opprcd1996..opprcd2013's footers just to filter them out.
    """
    all_years = _list_opprcd_years(parquet_dir)
    if date_start is None and date_end is None:
        return [str(parquet_dir / f"opprcd{y}.parquet") for y in all_years]
    y_lo = date_start.year if date_start is not None else all_years[0]
    y_hi = date_end.year if date_end is not None else all_years[-1]
    return [
        str(parquet_dir / f"opprcd{y}.parquet")
        for y in all_years
        if y_lo <= y <= y_hi
    ]


def _to_date(value) -> dt.date | None:
    """Coerce a Timestamp / date / str / None into a plain ``date``.

    Explicitly returns None on None so callers can pass through
    unfiltered semantics without a special case.
    """
    if value is None:
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    return pd.Timestamp(value).date()


def _build_query(
    opprcd_files: Sequence[str],
    secprd_files: Sequence[str],
    secids: Sequence[int] | None,
    date_start: dt.date | None,
    date_end: dt.date | None,
) -> tuple[str, list]:
    """SQL + positional parameters. DuckDB pushes the WHERE clauses down
    to the parquet reader, so a filter on secid or date range does not
    materialise the whole opprcd year in memory before filtering.

    ``secprd`` files are pre-aggregated with a MAX(close) GROUP BY
    ``(secid, date)`` inside the join subquery. This is not stylistic:
    the archive routinely carries both a master ``secprd.parquet``
    AND yearly ``secprd<YYYY>.parquet`` files (see Slice 2's inventory:
    19 parquet files across 1 master + 17 yearlies, all covering the
    same date-range in overlapping ways). Without the GROUP BY, the
    read_parquet union would produce two rows per ``(secid, date)`` --
    one from the master, one from the yearly -- and the LEFT-hand JOIN
    would fan out to two option rows per contract-day. The subsequent
    canonical dedup would then silently pick whichever ``close`` row
    happened to arrive first, which can differ between duplicates in
    the (rare) case they disagree.
    """
    op_files_sql = "[" + ", ".join(f"'{f}'" for f in opprcd_files) + "]"
    sp_files_sql = "[" + ", ".join(f"'{f}'" for f in secprd_files) + "]"

    where_parts: list[str] = []
    params: list = []
    if secids is not None:
        # Empty secid list is handled by the caller (returns empty frame
        # before this function is called); by the time we get here,
        # ``secids`` is non-empty.
        placeholders = ", ".join(["?"] * len(secids))
        where_parts.append(f"o.secid IN ({placeholders})")
        params.extend(int(s) for s in secids)
    if date_start is not None:
        where_parts.append("o.date >= ?")
        params.append(date_start)
    if date_end is not None:
        where_parts.append("o.date <= ?")
        params.append(date_end)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sql = f"""
    SELECT
        CAST(o.optionid AS BIGINT)                       AS option_id,
        CAST(o.secid    AS BIGINT)                       AS secid_int,
        o.date                                            AS date,
        o.exdate                                          AS expiration,
        CAST(o.strike_price AS DOUBLE) / {STRIKE_PRICE_SCALE} AS strike,
        o.cp_flag                                         AS call_put,
        o.best_bid                                        AS bid,
        o.best_offer                                      AS ask,
        o.volume                                          AS volume_raw,
        o.open_interest                                   AS open_interest_raw,
        o.impl_volatility                                 AS iv,
        o.delta                                           AS delta,
        o.gamma                                           AS gamma,
        o.vega                                            AS vega,
        o.theta                                           AS theta,
        s.close                                           AS underlying_price,
        o.strike_price                                    AS raw_strike_price,
        o.symbol                                          AS raw_symbol,
        o.root                                            AS raw_root,
        o.suffix                                          AS raw_suffix
    FROM read_parquet({op_files_sql}) AS o
    JOIN (
        SELECT secid, date, MAX(close) AS close
        FROM read_parquet({sp_files_sql})
        GROUP BY secid, date
    ) AS s
      ON s.secid = o.secid AND s.date = o.date
    {where_sql}
    """
    return sql, params


def _to_canonical_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Post-process the DuckDB result into a canonical-schema-compliant
    DataFrame: cast dtypes, drop unusable rows, insert constants, order
    columns.

    Rows dropped here mirror the "Rows dropped at load time" list in
    the module docstring. Everything else stays and is the Slice 6
    quality gate's problem.
    """
    if raw.empty:
        return sch.empty_frame()

    df = raw.copy()

    # Drop rows without a matched underlying_price (JOIN would already
    # eliminate them, but a NULL price would slip through if the join
    # matched a placeholder row; check defensively).
    df = df[df["underlying_price"].notna()]

    # Drop rows with expiration on or before the observation date.
    df = df[df["date"] <= df["expiration"]]

    # Drop rows with a non-{C,P} cp_flag (a handful of vendor-specific
    # values, e.g. non-standard adjusted contracts marked differently).
    df = df[df["call_put"].isin(sch.VALID_CALL_PUT)]

    if df.empty:
        return sch.empty_frame()

    # Canonical dtypes.
    df["option_id"] = df["option_id"].astype("int64")
    df["stable_underlying_id"] = (
        "ivydb_secid_" + df["secid_int"].astype("int64").astype("string")
    ).astype("string")
    # pd.to_datetime on date32[day] returns datetime64[us] on newer
    # pandas; the canonical schema requires datetime64[ns], so force
    # the cast rather than let precision drift.
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    df["expiration"] = pd.to_datetime(df["expiration"]).astype("datetime64[ns]")
    df["strike"] = df["strike"].astype("float64")
    df["call_put"] = df["call_put"].astype("string")
    df["bid"] = df["bid"].astype("float64")
    df["ask"] = df["ask"].astype("float64")
    df["volume"] = df["volume_raw"].astype("Int64")
    df["open_interest"] = df["open_interest_raw"].astype("Int64")
    df["iv"] = df["iv"].astype("float64")
    df["delta"] = df["delta"].astype("float64")
    df["gamma"] = df["gamma"].astype("float64")
    df["vega"] = df["vega"].astype("float64")
    df["theta"] = df["theta"].astype("float64")
    df["underlying_price"] = df["underlying_price"].astype("float64")

    # Constants.
    df["multiplier"] = DEFAULT_MULTIPLIER
    df["multiplier"] = df["multiplier"].astype("int64")
    df["source"] = SOURCE_TAG
    df["source"] = df["source"].astype("string")

    # Canonical column order + raw provenance columns after.
    raw_cols = [c for c in df.columns if c.startswith(sch.RAW_COLUMN_PREFIX)]
    df = df[sch.CANONICAL_COLUMN_NAMES + raw_cols]

    # De-duplicate on (option_id, date) -- the archive occasionally has
    # a duplicate row when a corporate action produced two records for
    # the same contract-day. Keep the first; the quality gate reports
    # duplicates separately.
    df = df.drop_duplicates(subset=["option_id", "date"]).reset_index(drop=True)

    return df


def load_options(
    *,
    secids: Iterable[int] | None = None,
    date_start=None,
    date_end=None,
    archive_root: Path = DEFAULT_ARCHIVE_ROOT,
) -> pd.DataFrame:
    """Load canonical option observations from the Path B archive.

    Parameters
    ----------
    secids
        Restrict to these OptionMetrics ``secid`` values. ``None`` for
        the full universe -- expensive; the archive has ~10K securities
        and hundreds of millions of option-days.
    date_start, date_end
        Inclusive date bounds. Any acceptable pandas timestamp form
        (``Timestamp``, ``date``, ``datetime``, ISO string). ``None`` on
        either side to omit that bound.
    archive_root
        Root of the IvyDB archive. Defaults to
        ``data/raw_options/OptionMetrics``.

    Returns
    -------
    pd.DataFrame
        Canonical option observations. Passes
        ``options_canonical_schema.validate()``. Includes ``raw_*``
        provenance columns after the canonical block.

    Raises
    ------
    ArchiveLoaderError
        If the parquet directory is missing (drive unmounted, path
        typo, ...).
    """
    parquet_dir = _resolve_archive_root(archive_root)

    ds = _to_date(date_start)
    de = _to_date(date_end)

    secid_list = list(secids) if secids is not None else None
    # An explicitly-empty secid filter means "no securities in scope";
    # short-circuit before building SQL, so we don't hand DuckDB a
    # syntactically-invalid ``secid IN ()`` clause.
    if secid_list is not None and len(secid_list) == 0:
        return sch.empty_frame()

    opprcd_files = _select_opprcd_files_for_window(parquet_dir, ds, de)
    if not opprcd_files:
        return sch.empty_frame()
    secprd_files = _list_secprd_files(parquet_dir)
    if not secprd_files:
        raise ArchiveLoaderError(
            f"No secprd*.parquet files found in {parquet_dir}. Cannot fill "
            f"the underlying_price column without a secprd source."
        )

    sql, params = _build_query(
        opprcd_files=opprcd_files,
        secprd_files=secprd_files,
        secids=secid_list,
        date_start=ds,
        date_end=de,
    )

    con = duckdb.connect()
    try:
        raw = con.execute(sql, params).df()
    finally:
        con.close()

    return _to_canonical_frame(raw)
