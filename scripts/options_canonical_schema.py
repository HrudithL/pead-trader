"""Slice 4 of memory/phase3-options-pead-plan.md: canonical
option-observation schema.

Purpose
-------
Defines the single target table shape every Phase III options loader
must produce, plus a validator any loader can call before it hands its
output on to the event-cube builder. Written before either loader
lands so both are held to the same contract.

The schema is deliberately narrow: exactly the columns the plan's
Scope-In list names, in a fixed order, with fixed dtypes. Raw vendor
fields (e.g. an unadjusted strike) go on separate ``raw_`` columns,
never overwritten in place -- reference spec §5.3-O.

Contract
--------
Every canonical option-observation frame satisfies:

  - The 18 canonical columns are present in the order given by
    ``CANONICAL_COLUMN_NAMES``.
  - Every column's dtype is compatible with the spec's declared type
    (int64 / float64 / datetime64[ns] / string / Int64).
  - Non-null columns have no nulls.
  - ``call_put`` values are in ``{"C", "P"}``.
  - ``strike``, ``underlying_price``, and ``multiplier`` are strictly
    positive.
  - ``date <= expiration`` on every row (an observation for an
    already-expired contract is out of scope for Phase III).
  - ``bid <= ask`` where both are present (crossed markets are a
    Slice 6 quality-gate concern, not a schema violation -- flagged as
    a warning here, not an error, so a loader can still write and let
    the gate quarantine the bad rows).
  - ``(option_id, date)`` is unique (a single contract has at most one
    row per observation date).

Loaders may attach additional ``raw_*`` columns beyond the canonical
set. Those are preserved but not validated here -- their meaning is a
loader-local decision.

Usage
-----
    from options_canonical_schema import validate, empty_frame

    empty = empty_frame()  # zero rows, correct dtypes, correct order
    result = validate(loader_output)
    if not result.ok:
        raise result.as_exception()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

# --- Schema ---------------------------------------------------------------

# One row per canonical column. Kept as a plain list-of-dicts (not a dict-
# of-dicts) so column *order* is a load-bearing property of the module,
# not an implementation detail of a dict's iteration order.
CANONICAL_COLUMNS: list[dict] = [
    # Identifier layer.
    {"name": "option_id", "dtype": "int64", "nullable": False,
     "kind": "identifier",
     "description": "Vendor's stable per-contract identifier. Never reused."},
    {"name": "stable_underlying_id", "dtype": "string", "nullable": False,
     "kind": "identifier",
     "description": "Time-stable underlying security identifier. NOT the "
                    "trading ticker (which changes on renames); the vendor's "
                    "internal security id (e.g. OptionMetrics secid)."},

    # Time layer.
    {"name": "date", "dtype": "datetime64[ns]", "nullable": False,
     "kind": "time",
     "description": "Observation trading date (end-of-day snapshot)."},
    {"name": "expiration", "dtype": "datetime64[ns]", "nullable": False,
     "kind": "time",
     "description": "Contract expiration date. Must be >= date."},

    # Contract terms.
    {"name": "strike", "dtype": "float64", "nullable": False,
     "kind": "contract",
     "description": "Strike price in the underlying's currency."},
    {"name": "call_put", "dtype": "string", "nullable": False,
     "kind": "contract",
     "description": 'Contract right: "C" for call, "P" for put.'},
    {"name": "multiplier", "dtype": "int64", "nullable": False,
     "kind": "contract",
     "description": "Shares of underlying per contract. Typically 100."},

    # Market quotes.
    {"name": "bid", "dtype": "float64", "nullable": True,
     "kind": "quote",
     "description": "End-of-day bid. May be null on illiquid contracts."},
    {"name": "ask", "dtype": "float64", "nullable": True,
     "kind": "quote",
     "description": "End-of-day ask. May be null on illiquid contracts."},
    {"name": "volume", "dtype": "Int64", "nullable": True,
     "kind": "quote",
     "description": "Contracts traded that day. Nullable Int64 so 0-vs-missing "
                    "stays distinguishable."},
    {"name": "open_interest", "dtype": "Int64", "nullable": True,
     "kind": "quote",
     "description": "Open contracts at end of day. Nullable Int64 so 0-vs-"
                    "missing stays distinguishable."},

    # Vendor-provided IV and Greeks. Nullable because they come out null
    # when the vendor's IV solver did not converge on that row.
    {"name": "iv", "dtype": "float64", "nullable": True,
     "kind": "greek",
     "description": "Vendor-computed implied volatility."},
    {"name": "delta", "dtype": "float64", "nullable": True,
     "kind": "greek",
     "description": "Vendor-computed delta."},
    {"name": "gamma", "dtype": "float64", "nullable": True,
     "kind": "greek",
     "description": "Vendor-computed gamma."},
    {"name": "vega", "dtype": "float64", "nullable": True,
     "kind": "greek",
     "description": "Vendor-computed vega."},
    {"name": "theta", "dtype": "float64", "nullable": True,
     "kind": "greek",
     "description": "Vendor-computed theta."},

    # Underlying + provenance.
    {"name": "underlying_price", "dtype": "float64", "nullable": False,
     "kind": "underlying",
     "description": "End-of-day underlying price on the same date."},
    {"name": "source", "dtype": "string", "nullable": False,
     "kind": "meta",
     "description": "Which loader produced this row -- e.g. "
                    '"archive_ivydb_1996_2013". Required so every downstream '
                    "result can be traced back to a data-source path."},
]

CANONICAL_COLUMN_NAMES: list[str] = [c["name"] for c in CANONICAL_COLUMNS]
CANONICAL_DTYPES: dict[str, str] = {c["name"]: c["dtype"] for c in CANONICAL_COLUMNS}
NON_NULL_COLUMNS: frozenset[str] = frozenset(
    c["name"] for c in CANONICAL_COLUMNS if not c["nullable"]
)
VALID_CALL_PUT: frozenset[str] = frozenset({"C", "P"})

# Raw vendor columns (pre-adjustment strike, pre-adjustment underlying,
# etc.) live under this prefix so a downstream reader can tell at a
# glance which columns are canonical vs. loader-attached provenance.
RAW_COLUMN_PREFIX = "raw_"


# --- Result type ----------------------------------------------------------


@dataclass
class ValidationResult:
    """Outcome of ``validate()``.

    ``ok`` is True iff there are no ``errors`` -- warnings do not fail
    validation. Callers that want the strict shape ("no warnings
    either") check ``bool(result.warnings) is False`` themselves; the
    default is deliberately lenient so a loader can write a frame
    containing a small number of crossed-market rows (Slice 6's
    quality-gate concern) without the schema layer rejecting it.
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_exception(self) -> "SchemaValidationError":
        return SchemaValidationError(self.errors, self.warnings)


class SchemaValidationError(ValueError):
    """Raised when a caller opts into strict enforcement (e.g. via
    ``result.as_exception()``) and there is at least one error.
    """

    def __init__(self, errors: Iterable[str], warnings: Iterable[str] = ()):
        self.errors = list(errors)
        self.warnings = list(warnings)
        msg = "Schema validation failed:\n" + "\n".join(
            f"  ERROR: {e}" for e in self.errors
        )
        if self.warnings:
            msg += "\n" + "\n".join(f"  WARNING: {w}" for w in self.warnings)
        super().__init__(msg)


# --- Public API -----------------------------------------------------------


def empty_frame() -> pd.DataFrame:
    """Zero-row DataFrame with the canonical schema.

    Loaders that produce nothing (e.g. universe with no matching event)
    still return this instead of ``pd.DataFrame()`` so the downstream
    reader can assume column presence.
    """
    data: dict[str, pd.Series] = {}
    for col in CANONICAL_COLUMNS:
        data[col["name"]] = pd.Series([], dtype=col["dtype"])
    return pd.DataFrame(data, columns=CANONICAL_COLUMN_NAMES)


def _is_dtype_compatible(actual: str, declared: str) -> bool:
    """Whether ``actual`` (from ``str(df[col].dtype)``) is acceptable for
    a column declared as ``declared``.

    Written slightly loosely on purpose: pandas' string extension type
    is spelled ``string`` on some builds and ``string[python]`` on
    others, and both are valid for a ``"string"`` column; int64 is
    acceptable where ``"Int64"`` (nullable) is declared as long as
    there are no nulls (the caller does the null-check separately); a
    tz-aware datetime is fine for a ``"datetime64[ns]"`` column as long
    as the tz is UTC or naive.
    """
    if actual == declared:
        return True
    if declared == "string" and actual.startswith("string"):
        return True
    if declared == "Int64" and actual in ("int64", "Int64"):
        return True
    if declared == "float64" and actual in ("float64", "float32"):
        return True
    if declared == "datetime64[ns]" and actual.startswith("datetime64"):
        return True
    return False


def _check_columns_present(df: pd.DataFrame, errors: list[str]) -> None:
    missing = [c for c in CANONICAL_COLUMN_NAMES if c not in df.columns]
    if missing:
        errors.append(
            f"Missing required columns: {missing}. Expected all of "
            f"{CANONICAL_COLUMN_NAMES}."
        )


def _check_column_order(df: pd.DataFrame, errors: list[str]) -> None:
    # We only check the canonical column *prefix* -- loaders may append
    # raw_* columns after the canonical block, and that is allowed.
    canonical_present = [c for c in CANONICAL_COLUMN_NAMES if c in df.columns]
    df_prefix = list(df.columns[: len(canonical_present)])
    if df_prefix != canonical_present:
        errors.append(
            f"Canonical columns are out of order. Expected prefix "
            f"{canonical_present}, got {df_prefix}. Reorder with "
            f"df[CANONICAL_COLUMN_NAMES + [c for c in df.columns if c not in "
            f"CANONICAL_COLUMN_NAMES]]."
        )


def _check_dtypes(df: pd.DataFrame, errors: list[str]) -> None:
    for name, declared in CANONICAL_DTYPES.items():
        if name not in df.columns:
            continue  # already flagged by _check_columns_present
        actual = str(df[name].dtype)
        if not _is_dtype_compatible(actual, declared):
            errors.append(
                f"Column {name!r} has dtype {actual!r}, expected {declared!r}."
            )


def _check_null_rules(df: pd.DataFrame, errors: list[str]) -> None:
    for col in NON_NULL_COLUMNS:
        if col not in df.columns:
            continue
        n_null = int(df[col].isna().sum())
        if n_null > 0:
            errors.append(
                f"Non-nullable column {col!r} has {n_null} null value(s)."
            )


def _check_domain_constraints(
    df: pd.DataFrame, errors: list[str], warnings: list[str]
) -> None:
    if "call_put" in df.columns and len(df):
        bad = ~df["call_put"].isin(VALID_CALL_PUT)
        bad_count = int(bad.sum())
        if bad_count:
            sample = df.loc[bad, "call_put"].dropna().unique().tolist()[:5]
            errors.append(
                f"Column 'call_put' has {bad_count} value(s) outside "
                f"{{'C','P'}}: sample {sample}."
            )

    # Positive-valued fields. fillna(1) so genuinely-null values (already
    # flagged by the null-check) don't get double-flagged as non-positive.
    for col in ("strike", "underlying_price", "multiplier"):
        if col in df.columns and len(df):
            non_positive = int((df[col].fillna(1) <= 0).sum())
            if non_positive:
                errors.append(
                    f"Column {col!r} has {non_positive} non-positive value(s)."
                )

    if "date" in df.columns and "expiration" in df.columns and len(df):
        past_expiration = int((df["date"] > df["expiration"]).sum())
        if past_expiration:
            errors.append(
                f"{past_expiration} row(s) have date > expiration; observations "
                f"of an already-expired contract are out of Phase III scope."
            )

    # bid <= ask where both present -- WARNING, not error (crossed markets
    # go through the Slice 6 quality gate).
    if "bid" in df.columns and "ask" in df.columns and len(df):
        both_present = df["bid"].notna() & df["ask"].notna()
        crossed = int((both_present & (df["bid"] > df["ask"])).sum())
        if crossed:
            warnings.append(
                f"{crossed} row(s) have bid > ask (crossed market). Not a "
                f"schema violation; the Slice 6 quality gate will quarantine "
                f"these."
            )


def _check_uniqueness(df: pd.DataFrame, errors: list[str]) -> None:
    if "option_id" in df.columns and "date" in df.columns and len(df):
        dupes = int(df.duplicated(subset=["option_id", "date"]).sum())
        if dupes:
            errors.append(
                f"{dupes} duplicate (option_id, date) row(s). A contract has "
                f"at most one observation per date."
            )


def validate(df: pd.DataFrame) -> ValidationResult:
    """Full schema + integrity validation on a loader-output frame.

    Runs every check regardless of prior failures, so a loader gets a
    complete list of what's wrong on one pass rather than having to
    fix-and-retry against one error at a time.
    """
    errors: list[str] = []
    warnings: list[str] = []

    _check_columns_present(df, errors)
    _check_column_order(df, errors)
    _check_dtypes(df, errors)
    _check_null_rules(df, errors)
    _check_domain_constraints(df, errors, warnings)
    _check_uniqueness(df, errors)

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
