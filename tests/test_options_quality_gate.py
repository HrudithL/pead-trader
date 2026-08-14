"""Tests for scripts/options_quality_gate.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import options_canonical_schema as sch  # noqa: E402
import options_quality_gate as qg  # noqa: E402


def _row(**overrides) -> dict:
    row = {
        "option_id": 1,
        "stable_underlying_id": "SEC1",
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
        "source": "test",
        "raw_suffix": "",
    }
    row.update(overrides)
    return row


def _frame(*rows: dict) -> pd.DataFrame:
    if not rows:
        rows = (_row(),)
    df = pd.DataFrame(list(rows))
    # Coerce dtypes to a canonical-ish shape so cross-row operations
    # (shift, groupby) behave predictably. This isn't full schema
    # conformance -- it's whatever the QG needs.
    df["volume"] = df["volume"].astype("Int64")
    df["open_interest"] = df["open_interest"].astype("Int64")
    df["date"] = pd.to_datetime(df["date"])
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["raw_suffix"] = df["raw_suffix"].astype("string")
    return df.reset_index(drop=True)


class TestCleanFrame:
    def test_all_clean_rows_pass(self):
        df = _frame(_row(), _row(option_id=2))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 2
        assert result.quarantine.empty
        assert result.warnings.empty
        assert all(len(r) == 0 for r in result.clean["quarantine_reasons"])

    def test_summary_string(self):
        df = _frame(_row())
        result = qg.run_quality_gate(df)
        assert "1 input rows" in result.summary()
        assert "1 clean" in result.summary()

    def test_counts_include_every_rule(self):
        df = _frame(_row())
        result = qg.run_quality_gate(df)
        for rule in qg.QUALITY_RULES:
            assert rule.code in result.counts


class TestCrossedMarketRule:
    def test_bid_gt_ask_quarantined(self):
        df = _frame(_row(bid=3.0, ask=2.5))
        result = qg.run_quality_gate(df)
        assert len(result.quarantine) == 1
        assert "Q001" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_bid_eq_ask_not_flagged(self):
        df = _frame(_row(bid=2.5, ask=2.5))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 1


class TestNonPositiveQuoteRules:
    def test_negative_bid_quarantined(self):
        df = _frame(_row(bid=-1.0))
        result = qg.run_quality_gate(df)
        assert "Q002" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_negative_ask_quarantined(self):
        df = _frame(_row(ask=-1.0))
        result = qg.run_quality_gate(df)
        assert "Q003" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_zero_bid_not_error_but_warning_when_both_zero(self):
        # Zero bid alone (with a positive ask) is fine -- deep OTM.
        df = _frame(_row(bid=0.0, ask=0.05))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 1
        # Both zero -> Q101 warning, still in clean.
        df2 = _frame(_row(bid=0.0, ask=0.0))
        result2 = qg.run_quality_gate(df2)
        assert len(result2.clean) == 1
        assert len(result2.warnings) == 1
        assert "Q101" in result2.warnings.iloc[0]["warning_reasons"]


class TestNonStandardMultiplier:
    def test_multiplier_10_quarantined(self):
        df = _frame(_row(multiplier=10))
        result = qg.run_quality_gate(df)
        assert "Q004" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_multiplier_100_not_flagged(self):
        df = _frame(_row(multiplier=100))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 1


class TestImpossibleDTE:
    def test_expiration_before_date_quarantined(self):
        df = _frame(_row(
            date=pd.Timestamp("2010-07-01"),
            expiration=pd.Timestamp("2010-06-30"),
        ))
        result = qg.run_quality_gate(df)
        assert "Q005" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_expiration_on_date_not_flagged(self):
        df = _frame(_row(
            date=pd.Timestamp("2010-06-30"),
            expiration=pd.Timestamp("2010-06-30"),
        ))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 1


class TestStrikeScaling:
    def test_strike_way_above_underlying_quarantined(self):
        # underlying 50, strike 50000 -> ratio 1000, well above 100 ceiling.
        df = _frame(_row(underlying_price=50.0, strike=50000.0))
        result = qg.run_quality_gate(df)
        assert "Q006" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_strike_way_below_underlying_quarantined(self):
        # underlying 50, strike 0.001 -> ratio 0.00002, below 0.01 floor.
        df = _frame(_row(underlying_price=50.0, strike=0.001))
        result = qg.run_quality_gate(df)
        assert "Q006" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_deep_itm_deep_otm_within_band_not_flagged(self):
        # underlying 100, strike 5 (ratio 0.05) -- deep ITM but within band.
        # underlying 100, strike 500 (ratio 5) -- deep OTM but within band.
        df = _frame(
            _row(option_id=1, underlying_price=100.0, strike=5.0),
            _row(option_id=2, underlying_price=100.0, strike=500.0),
        )
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 2


class TestAdjustedContract:
    def test_non_empty_raw_suffix_quarantined(self):
        df = _frame(_row(raw_suffix="A"))
        result = qg.run_quality_gate(df)
        assert "Q007" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_empty_raw_suffix_not_flagged(self):
        df = _frame(_row(raw_suffix=""))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 1

    def test_missing_raw_suffix_column_no_crash(self):
        df = _frame(_row()).drop(columns="raw_suffix")
        result = qg.run_quality_gate(df)
        assert result.counts["Q007"] == 0
        assert len(result.clean) == 1


class TestExtremeIV:
    def test_iv_above_500pct_quarantined(self):
        df = _frame(_row(iv=6.0))
        result = qg.run_quality_gate(df)
        assert "Q008" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_negative_iv_quarantined(self):
        df = _frame(_row(iv=-0.1))
        result = qg.run_quality_gate(df)
        assert "Q008" in result.quarantine.iloc[0]["quarantine_reasons"]

    def test_null_iv_not_flagged(self):
        df = _frame(_row(iv=float("nan")))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 1


class TestDuplicateOptionDate:
    def test_both_duplicates_quarantined(self):
        # keep=False: both duplicate rows go to quarantine, not just one.
        df = _frame(_row(option_id=1), _row(option_id=1))
        result = qg.run_quality_gate(df)
        assert len(result.quarantine) == 2
        assert all(
            "Q009" in r for r in result.quarantine["quarantine_reasons"]
        )

    def test_same_option_different_date_not_flagged(self):
        df = _frame(
            _row(option_id=1, date=pd.Timestamp("2010-01-04")),
            _row(option_id=1, date=pd.Timestamp("2010-01-05")),
        )
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 2


class TestStaleQuoteRule:
    def test_unchanged_from_prior_day_flagged_as_warning(self):
        df = _frame(
            _row(option_id=1, date=pd.Timestamp("2010-01-04"), bid=2.5, ask=2.7),
            _row(option_id=1, date=pd.Timestamp("2010-01-05"), bid=2.5, ask=2.7),
        )
        result = qg.run_quality_gate(df)
        # Both rows in clean (warning, not error). Second row is the
        # one flagged; first has no prior day.
        assert len(result.clean) == 2
        assert len(result.warnings) == 1
        assert result.warnings.iloc[0]["date"] == pd.Timestamp("2010-01-05")

    def test_changed_quotes_not_flagged(self):
        df = _frame(
            _row(option_id=1, date=pd.Timestamp("2010-01-04"), bid=2.5, ask=2.7),
            _row(option_id=1, date=pd.Timestamp("2010-01-05"), bid=2.6, ask=2.8),
        )
        result = qg.run_quality_gate(df)
        assert result.warnings.empty

    def test_prior_day_of_different_option_not_a_source(self):
        # Same (bid, ask) on same date across two different options
        # should NOT flag the second as stale-from-prior-day.
        df = _frame(
            _row(option_id=1, date=pd.Timestamp("2010-01-04"), bid=2.5, ask=2.7),
            _row(option_id=2, date=pd.Timestamp("2010-01-04"), bid=2.5, ask=2.7),
        )
        result = qg.run_quality_gate(df)
        assert result.warnings.empty


class TestMultipleReasons:
    def test_row_failing_two_rules_has_both_codes(self):
        # Crossed market AND negative bid.
        df = _frame(_row(bid=-1.0, ask=-2.0))
        result = qg.run_quality_gate(df)
        reasons = result.quarantine.iloc[0]["quarantine_reasons"]
        assert "Q001" in reasons  # crossed market (-1 > -2)
        assert "Q002" in reasons  # non-positive bid
        assert "Q003" in reasons  # non-positive ask


class TestExclusionContract:
    def test_quarantine_row_not_in_clean(self):
        df = _frame(_row(option_id=1, bid=3.0, ask=2.5),
                    _row(option_id=2))
        result = qg.run_quality_gate(df)
        assert 1 not in result.clean["option_id"].tolist()
        assert 1 in result.quarantine["option_id"].tolist()
        assert 2 in result.clean["option_id"].tolist()

    def test_warning_row_stays_in_clean(self):
        # Both-zero bid/ask: warning-only, must appear in clean.
        df = _frame(_row(bid=0.0, ask=0.0))
        result = qg.run_quality_gate(df)
        assert len(result.clean) == 1


class TestMissingColumn:
    def test_missing_required_column_raises_helpful_error(self):
        # Drop 'bid' -- crossed-market rule needs it.
        df = _frame(_row()).drop(columns="bid")
        with pytest.raises(ValueError, match="requires a column"):
            qg.run_quality_gate(df)


class TestEmptyInput:
    def test_empty_frame_returns_empty_result(self):
        df = _frame().iloc[0:0]  # keep the columns, drop the rows
        result = qg.run_quality_gate(df)
        assert result.clean.empty
        assert result.quarantine.empty
        assert result.warnings.empty
        # Every rule reports 0 flagged.
        for rule in qg.QUALITY_RULES:
            assert result.counts[rule.code] == 0

    def test_empty_warnings_has_warning_reasons_column(self):
        # Round-1 review fix: the empty branch used to give `warnings`
        # a `quarantine_reasons` column instead of `warning_reasons`,
        # silently breaking any caller that accessed the wrong name.
        df = _frame().iloc[0:0]
        result = qg.run_quality_gate(df)
        assert "warning_reasons" in result.warnings.columns
        assert "quarantine_reasons" not in result.warnings.columns


class TestIndexUniqueness:
    def test_non_unique_index_rejected(self):
        # Round-1 review fix: cross-row rules (Q009, Q102) do positional
        # ops that silently misalign under a duplicated index. Enforce
        # the precondition at entry rather than mis-flag downstream.
        df = _frame(_row(option_id=1), _row(option_id=2))
        df.index = [0, 0]  # deliberately duplicate
        with pytest.raises(ValueError, match="unique"):
            qg.run_quality_gate(df)
