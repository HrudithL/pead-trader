"""Tests for scripts/build_iv_history.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_iv_history as bih  # noqa: E402


def _row(**overrides) -> dict:
    row = {
        "option_id": 1,
        "stable_underlying_id": "SEC1",
        "date": pd.Timestamp("2010-01-04"),
        "expiration": pd.Timestamp("2010-02-03"),  # ~30 DTE
        "strike": 50.0,
        "call_put": "C",
        "multiplier": 100,
        "bid": 2.5,
        "ask": 2.7,
        "volume": 100,
        "open_interest": 500,
        "iv": 0.30,
        "delta": 0.5,
        "gamma": 0.02,
        "vega": 0.1,
        "theta": -0.01,
        "underlying_price": 50.0,
        "source": "test",
    }
    row.update(overrides)
    return row


def _frame(*rows: dict) -> pd.DataFrame:
    df = pd.DataFrame(list(rows) if rows else [_row()])
    df["date"] = pd.to_datetime(df["date"])
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["volume"] = df["volume"].astype("Int64")
    df["open_interest"] = df["open_interest"].astype("Int64")
    return df


class TestBuildFrameShape:
    def test_columns_match_output_spec(self):
        df = _frame(_row())
        out = bih.build_iv_history_frame(df)
        assert list(out.columns) == bih._OUTPUT_COLUMNS

    def test_empty_input_returns_empty(self):
        empty = _frame().iloc[0:0]
        out = bih.build_iv_history_frame(empty)
        assert out.empty
        assert list(out.columns) == bih._OUTPUT_COLUMNS

    def test_one_row_per_underlying_date(self):
        # Multiple contracts on same (SEC1, 2010-01-04) -> ONE output row.
        df = _frame(
            _row(option_id=1, strike=45.0),
            _row(option_id=2, strike=50.0),
            _row(option_id=3, strike=55.0),
        )
        out = bih.build_iv_history_frame(df)
        assert len(out) == 1
        assert out.iloc[0]["stable_underlying_id"] == "SEC1"


class TestATMPick:
    def test_closest_strike_wins(self):
        # underlying=50; 3 contracts at 45/50/55; ATM should be 50 → its iv.
        df = _frame(
            _row(option_id=1, strike=45.0, iv=0.35),
            _row(option_id=2, strike=50.0, iv=0.30),  # ATM
            _row(option_id=3, strike=55.0, iv=0.32),
        )
        out = bih.build_iv_history_frame(df)
        assert out.iloc[0]["atm_iv_30d"] == pytest.approx(0.30)

    def test_null_iv_contract_skipped_for_atm(self):
        # ATM by strike is contract 2, but its iv is NaN. Next-closest
        # non-null-iv contract picks up.
        df = _frame(
            _row(option_id=1, strike=45.0, iv=0.35),
            _row(option_id=2, strike=50.0, iv=float("nan")),
            _row(option_id=3, strike=55.0, iv=0.32),
        )
        out = bih.build_iv_history_frame(df)
        # Contracts 1 and 3 are equidistant from 50; the deterministic
        # tie-break sorts by (abs_diff, option_id) so option_id=1
        # wins, iv=0.35.
        assert out.iloc[0]["atm_iv_30d"] == pytest.approx(0.35)

    def test_bucket_boundary_dte_covered_by_both_seams(self):
        # A 45-day DTE contract falls in BOTH the 30-day (15-45) and
        # 60-day (45-75) buckets, so both columns populate from it.
        df = _frame(_row(
            date=pd.Timestamp("2010-01-01"),
            expiration=pd.Timestamp("2010-02-15"),  # 45 DTE
        ))
        out = bih.build_iv_history_frame(df)
        assert not pd.isna(out.iloc[0]["atm_iv_30d"])
        assert not pd.isna(out.iloc[0]["atm_iv_60d"])

    def test_out_of_bucket_dte_yields_nan(self):
        # 5-DTE contract -> no bucket covers it. All three IV columns NaN.
        df = _frame(_row(
            date=pd.Timestamp("2010-01-01"),
            expiration=pd.Timestamp("2010-01-06"),  # 5 DTE
        ))
        out = bih.build_iv_history_frame(df)
        assert pd.isna(out.iloc[0]["atm_iv_30d"])
        assert pd.isna(out.iloc[0]["atm_iv_60d"])
        assert pd.isna(out.iloc[0]["atm_iv_90d"])

    def test_60_and_90_day_tenors(self):
        df = _frame(
            _row(option_id=1,
                 date=pd.Timestamp("2010-01-01"),
                 expiration=pd.Timestamp("2010-03-02"),   # 60 DTE
                 iv=0.28),
            _row(option_id=2,
                 date=pd.Timestamp("2010-01-01"),
                 expiration=pd.Timestamp("2010-04-01"),   # 90 DTE
                 iv=0.26),
        )
        out = bih.build_iv_history_frame(df)
        assert out.iloc[0]["atm_iv_60d"] == pytest.approx(0.28)
        assert out.iloc[0]["atm_iv_90d"] == pytest.approx(0.26)


class TestAggregates:
    def test_volume_and_oi_sum(self):
        df = _frame(
            _row(option_id=1, volume=100, open_interest=500),
            _row(option_id=2, volume=200, open_interest=750),
        )
        out = bih.build_iv_history_frame(df)
        assert out.iloc[0]["total_option_volume"] == 300
        assert out.iloc[0]["total_open_interest"] == 1250

    def test_null_volume_treated_as_zero_in_sum(self):
        df = _frame(
            _row(option_id=1, volume=100, open_interest=500),
            _row(option_id=2, volume=pd.NA, open_interest=pd.NA),
        )
        out = bih.build_iv_history_frame(df)
        assert out.iloc[0]["total_option_volume"] == 100
        assert out.iloc[0]["total_open_interest"] == 500


class TestMultipleGroups:
    def test_two_underlyings_two_rows(self):
        df = _frame(
            _row(option_id=1, stable_underlying_id="SEC1"),
            _row(option_id=2, stable_underlying_id="SEC2"),
        )
        out = bih.build_iv_history_frame(df)
        assert len(out) == 2
        assert set(out["stable_underlying_id"]) == {"SEC1", "SEC2"}

    def test_two_dates_two_rows(self):
        df = _frame(
            _row(option_id=1, date=pd.Timestamp("2010-01-04")),
            _row(option_id=1, date=pd.Timestamp("2010-01-05")),
        )
        out = bih.build_iv_history_frame(df)
        assert len(out) == 2

    def test_output_sorted_by_group_key(self):
        # groupby sort=True should produce output sorted by
        # (stable_underlying_id, date).
        df = _frame(
            _row(option_id=1, stable_underlying_id="SEC2",
                 date=pd.Timestamp("2010-01-04")),
            _row(option_id=2, stable_underlying_id="SEC1",
                 date=pd.Timestamp("2010-01-05")),
            _row(option_id=3, stable_underlying_id="SEC1",
                 date=pd.Timestamp("2010-01-04")),
        )
        out = bih.build_iv_history_frame(df)
        ids = out[["stable_underlying_id", "date"]].to_records(index=False).tolist()
        assert ids == sorted(ids)


class TestBatchBuild:
    def test_writes_file_and_returns_path(self, tmp_path: Path, monkeypatch):
        # Patch loader to return a synthetic frame for one specific year.
        called: dict = {}

        def fake_load_options(**kwargs):
            called["kwargs"] = kwargs
            if kwargs["date_start"].year == 2010:
                return _frame(_row())
            return _frame().iloc[0:0]

        monkeypatch.setattr(bih.loader, "load_options", fake_load_options)
        out_path = tmp_path / "iv.parquet"
        result = bih.build_iv_history_for_range(
            start_year=2010, end_year=2010, output_path=out_path
        )
        assert result == out_path
        assert out_path.exists()
        df = pd.read_parquet(out_path)
        assert len(df) == 1

    def test_multi_year_concatenates(self, tmp_path: Path, monkeypatch):
        def fake_load_options(**kwargs):
            year = kwargs["date_start"].year
            return _frame(
                _row(stable_underlying_id=f"SEC_{year}",
                     date=pd.Timestamp(f"{year}-06-15"))
            )

        monkeypatch.setattr(bih.loader, "load_options", fake_load_options)
        out_path = tmp_path / "iv.parquet"
        bih.build_iv_history_for_range(
            start_year=2010, end_year=2012, output_path=out_path
        )
        df = pd.read_parquet(out_path)
        assert set(df["stable_underlying_id"]) == {"SEC_2010", "SEC_2011", "SEC_2012"}

    def test_empty_range_still_writes_empty_file(self, tmp_path: Path, monkeypatch):
        def fake_load_options(**kwargs):
            return _frame().iloc[0:0]

        monkeypatch.setattr(bih.loader, "load_options", fake_load_options)
        out_path = tmp_path / "iv.parquet"
        result = bih.build_iv_history_for_range(
            start_year=2010, end_year=2010, output_path=out_path
        )
        assert result.exists()
        df = pd.read_parquet(out_path)
        assert df.empty
        assert list(df.columns) == bih._OUTPUT_COLUMNS


class TestCLI:
    def test_reversed_range_rejected(self, monkeypatch):
        with pytest.raises(SystemExit, match="must be <="):
            bih.main(["--start-year", "2011", "--end-year", "2010"])


class TestRoundOneReviewFixes:
    def test_null_group_key_raises(self):
        # Round-1 review fix: schema says these are non-null; guard
        # against silent groupby-drop when a partially-loaded frame
        # slips through.
        df = _frame(_row())
        df.loc[0, "stable_underlying_id"] = None
        with pytest.raises(ValueError, match="non-null"):
            bih.build_iv_history_frame(df)

    def test_tie_break_deterministic(self):
        # Round-1 review fix: two contracts equidistant from underlying
        # -- pick reproducibly (lowest option_id) regardless of input
        # row order.
        rows_ordered = [
            _row(option_id=1, strike=45.0, iv=0.35),
            _row(option_id=2, strike=55.0, iv=0.40),
        ]
        rows_reversed = list(reversed(rows_ordered))
        out1 = bih.build_iv_history_frame(_frame(*rows_ordered))
        out2 = bih.build_iv_history_frame(_frame(*rows_reversed))
        assert out1.iloc[0]["atm_iv_30d"] == out2.iloc[0]["atm_iv_30d"]

    def test_all_null_volume_column_no_crash(self):
        # Round-1 review fix: all-null Int64 sum is pd.NA in pandas;
        # _sum_safe converts to 0 rather than raising.
        df = _frame(_row())
        df["volume"] = pd.array([pd.NA], dtype="Int64")
        df["open_interest"] = pd.array([pd.NA], dtype="Int64")
        out = bih.build_iv_history_frame(df)
        assert out.iloc[0]["total_option_volume"] == 0
        assert out.iloc[0]["total_open_interest"] == 0

    def test_empty_output_has_typed_columns(self):
        # Round-1 review fix: empty batch used to have object-dtype
        # placeholders; now returns proper int64/float64/datetime64 so
        # a round-trip through parquet stays type-consistent.
        empty = _frame().iloc[0:0]
        out = bih.build_iv_history_frame(empty)
        assert str(out["date"].dtype).startswith("datetime64")
        assert out["total_option_volume"].dtype == "int64"
        assert out["atm_iv_30d"].dtype == "float64"

    def test_output_sorted_regardless_of_input_order(self):
        # Round-1 review fix: explicit sort_values after merge means
        # output row order is stable across shuffled inputs.
        df_ordered = _frame(
            _row(option_id=1, stable_underlying_id="SEC_A",
                 date=pd.Timestamp("2010-01-04")),
            _row(option_id=2, stable_underlying_id="SEC_B",
                 date=pd.Timestamp("2010-01-04")),
        )
        df_reversed = _frame(
            _row(option_id=2, stable_underlying_id="SEC_B",
                 date=pd.Timestamp("2010-01-04")),
            _row(option_id=1, stable_underlying_id="SEC_A",
                 date=pd.Timestamp("2010-01-04")),
        )
        out1 = bih.build_iv_history_frame(df_ordered)
        out2 = bih.build_iv_history_frame(df_reversed)
        assert list(out1["stable_underlying_id"]) == list(out2["stable_underlying_id"])
        assert list(out1["stable_underlying_id"]) == ["SEC_A", "SEC_B"]
