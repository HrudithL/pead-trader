"""Tests for scripts/options_canonical_schema.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import options_canonical_schema as sch  # noqa: E402


def _valid_row(**overrides) -> dict:
    """One-row worth of canonical data with sane defaults; overrides
    replace individual fields for the pathological-case tests.
    """
    row = {
        "option_id": 1001,
        "stable_underlying_id": "SEC123",
        "date": pd.Timestamp("2010-01-04"),
        "expiration": pd.Timestamp("2010-06-18"),
        "strike": 50.0,
        "call_put": "C",
        "multiplier": 100,
        "bid": 2.5,
        "ask": 2.7,
        "volume": 100,
        "open_interest": 500,
        "iv": 0.3,
        "delta": 0.5,
        "gamma": 0.02,
        "vega": 0.1,
        "theta": -0.01,
        "underlying_price": 51.0,
        "source": "archive_ivydb_1996_2013",
    }
    row.update(overrides)
    return row


def _valid_frame(*rows: dict) -> pd.DataFrame:
    if not rows:
        rows = (_valid_row(),)
    df = pd.DataFrame(list(rows))
    df = df[sch.CANONICAL_COLUMN_NAMES]
    for col in sch.CANONICAL_COLUMNS:
        df[col["name"]] = df[col["name"]].astype(col["dtype"])
    return df


class TestSchemaDefinition:
    def test_canonical_column_count_matches_plan(self):
        # Plan Scope-In lists 17 fields plus source == 18.
        assert len(sch.CANONICAL_COLUMN_NAMES) == 18

    def test_column_names_unique(self):
        assert len(set(sch.CANONICAL_COLUMN_NAMES)) == len(sch.CANONICAL_COLUMN_NAMES)

    def test_call_put_domain(self):
        assert sch.VALID_CALL_PUT == frozenset({"C", "P"})

    def test_non_null_columns_include_identifiers(self):
        for required in (
            "option_id",
            "stable_underlying_id",
            "date",
            "expiration",
            "strike",
            "call_put",
            "multiplier",
            "underlying_price",
            "source",
        ):
            assert required in sch.NON_NULL_COLUMNS

    def test_nullable_columns_include_quotes_and_greeks(self):
        # Explicit: bid/ask can be null (illiquid), Greeks can be null
        # (solver didn't converge). If someone tightens these to
        # non-null, this test tells them why the design says otherwise.
        for nullable in ("bid", "ask", "volume", "open_interest",
                         "iv", "delta", "gamma", "vega", "theta"):
            assert nullable not in sch.NON_NULL_COLUMNS


class TestEmptyFrame:
    def test_columns_present_and_ordered(self):
        empty = sch.empty_frame()
        assert list(empty.columns) == sch.CANONICAL_COLUMN_NAMES

    def test_dtypes_declared(self):
        empty = sch.empty_frame()
        for col in sch.CANONICAL_COLUMNS:
            assert sch._is_dtype_compatible(str(empty[col["name"]].dtype), col["dtype"])

    def test_zero_rows(self):
        assert len(sch.empty_frame()) == 0

    def test_empty_frame_passes_validation(self):
        result = sch.validate(sch.empty_frame())
        assert result.ok
        assert result.errors == []


class TestValidateHappyPath:
    def test_single_valid_row_passes(self):
        result = sch.validate(_valid_frame())
        assert result.ok, result.errors
        assert result.warnings == []


class TestValidateColumnPresence:
    def test_missing_column_flagged(self):
        df = _valid_frame().drop(columns="strike")
        result = sch.validate(df)
        assert not result.ok
        assert any("strike" in e for e in result.errors)

    def test_extra_raw_column_allowed(self):
        # Loaders may attach raw_* columns; they should not break validation
        # as long as they come AFTER the canonical prefix.
        df = _valid_frame()
        df["raw_original_strike"] = df["strike"]
        result = sch.validate(df)
        assert result.ok, result.errors


class TestValidateColumnOrder:
    def test_swapped_columns_flagged(self):
        df = _valid_frame()
        cols = list(df.columns)
        cols[0], cols[1] = cols[1], cols[0]
        df = df[cols]
        result = sch.validate(df)
        assert not result.ok
        assert any("out of order" in e for e in result.errors)


class TestValidateDtypes:
    def test_wrong_dtype_flagged(self):
        df = _valid_frame()
        df["strike"] = df["strike"].astype("int64")
        result = sch.validate(df)
        assert not result.ok
        assert any("strike" in e and "dtype" in e for e in result.errors)

    def test_nullable_int_accepts_int64(self):
        df = _valid_frame()
        df["volume"] = df["volume"].astype("int64")
        result = sch.validate(df)
        assert result.ok, result.errors


class TestValidateNullRules:
    def test_null_in_non_null_column_flagged(self):
        df = _valid_frame()
        df.loc[0, "underlying_price"] = float("nan")
        result = sch.validate(df)
        assert not result.ok
        assert any("underlying_price" in e for e in result.errors)

    def test_null_in_nullable_column_ok(self):
        df = _valid_frame()
        df.loc[0, "bid"] = float("nan")
        df.loc[0, "iv"] = float("nan")
        result = sch.validate(df)
        assert result.ok, result.errors


class TestValidateDomain:
    def test_bad_call_put_flagged(self):
        df = _valid_frame(_valid_row(call_put="X"))
        result = sch.validate(df)
        assert not result.ok
        assert any("call_put" in e and "'X'" in e for e in result.errors)

    def test_lowercase_call_put_flagged(self):
        # Case matters: 'c' and 'p' are NOT valid, only 'C' and 'P'.
        df = _valid_frame(_valid_row(call_put="c"))
        result = sch.validate(df)
        assert not result.ok
        assert any("call_put" in e for e in result.errors)

    def test_negative_strike_flagged(self):
        df = _valid_frame(_valid_row(strike=-1.0))
        result = sch.validate(df)
        assert not result.ok
        assert any("strike" in e and "non-positive" in e for e in result.errors)

    def test_zero_strike_flagged(self):
        # "Strictly positive" means zero must be rejected too.
        df = _valid_frame(_valid_row(strike=0.0))
        result = sch.validate(df)
        assert not result.ok
        assert any("strike" in e and "non-positive" in e for e in result.errors)

    def test_zero_multiplier_flagged(self):
        df = _valid_frame(_valid_row(multiplier=0))
        result = sch.validate(df)
        assert not result.ok
        assert any("multiplier" in e and "non-positive" in e for e in result.errors)

    def test_infinite_strike_flagged(self):
        df = _valid_frame(_valid_row(strike=float("inf")))
        result = sch.validate(df)
        assert not result.ok
        assert any("strike" in e and "infinite" in e for e in result.errors)

    def test_infinite_underlying_price_flagged(self):
        df = _valid_frame(_valid_row(underlying_price=float("-inf")))
        result = sch.validate(df)
        assert not result.ok
        assert any("underlying_price" in e and "infinite" in e for e in result.errors)

    def test_negative_volume_flagged(self):
        df = _valid_frame(_valid_row(volume=-5))
        result = sch.validate(df)
        assert not result.ok
        assert any("volume" in e and "negative" in e for e in result.errors)

    def test_negative_open_interest_flagged(self):
        df = _valid_frame(_valid_row(open_interest=-1))
        result = sch.validate(df)
        assert not result.ok
        assert any("open_interest" in e and "negative" in e for e in result.errors)

    def test_zero_volume_ok(self):
        # Zero volume on a truly illiquid contract is a real, valid state.
        df = _valid_frame(_valid_row(volume=0, open_interest=0))
        result = sch.validate(df)
        assert result.ok, result.errors

    def test_expiration_before_date_flagged(self):
        df = _valid_frame(_valid_row(
            date=pd.Timestamp("2010-07-01"),
            expiration=pd.Timestamp("2010-06-30"),
        ))
        result = sch.validate(df)
        assert not result.ok
        assert any("date > expiration" in e for e in result.errors)

    def test_crossed_market_is_warning_not_error(self):
        df = _valid_frame(_valid_row(bid=3.0, ask=2.5))
        result = sch.validate(df)
        # Crossed market flagged as warning; row still passes schema check.
        assert result.ok, result.errors
        assert any("crossed market" in w for w in result.warnings)


class TestValidateUniqueness:
    def test_duplicate_option_id_date_flagged(self):
        row = _valid_row()
        df = _valid_frame(row, row)  # exact duplicate on option_id and date
        result = sch.validate(df)
        assert not result.ok
        assert any("duplicate" in e for e in result.errors)

    def test_same_option_different_date_ok(self):
        r1 = _valid_row(date=pd.Timestamp("2010-01-04"))
        r2 = _valid_row(date=pd.Timestamp("2010-01-05"))
        result = sch.validate(_valid_frame(r1, r2))
        assert result.ok, result.errors


class TestValidationResult:
    def test_ok_true_when_no_errors(self):
        result = sch.ValidationResult(ok=True, errors=[], warnings=["crossed"])
        assert result.ok is True

    def test_as_exception_carries_messages(self):
        result = sch.ValidationResult(
            ok=False, errors=["bad thing"], warnings=["watch out"]
        )
        exc = result.as_exception()
        assert isinstance(exc, sch.SchemaValidationError)
        assert "bad thing" in str(exc)
        assert "watch out" in str(exc)


class TestDtypeCompat:
    @pytest.mark.parametrize("actual,declared,ok", [
        ("int64", "int64", True),
        ("string", "string", True),
        ("string[python]", "string", True),
        ("Int64", "Int64", True),
        ("int64", "Int64", True),   # non-null int is fine where Int64 declared
        ("float64", "float64", True),
        # float32 is rejected for float64 columns: single-precision loses
        # meaningful precision on prices/Greeks/IV, and a loader that
        # produced float32 needs to upcast before validation.
        ("float32", "float64", False),
        ("datetime64[ns]", "datetime64[ns]", True),
        ("datetime64[ns, UTC]", "datetime64[ns]", True),
        # Non-UTC timezones are rejected: downstream event-cube joins
        # assume a UTC / naive time axis and would silently produce
        # off-by-hours results at DST boundaries.
        ("datetime64[ns, US/Eastern]", "datetime64[ns]", False),
        ("datetime64[ns, Asia/Tokyo]", "datetime64[ns]", False),
        ("int64", "float64", False),
        ("object", "string", False),
        ("float64", "int64", False),
    ])
    def test_compat_matrix(self, actual, declared, ok):
        assert sch._is_dtype_compatible(actual, declared) is ok
