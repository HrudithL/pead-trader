"""Tests for scripts/options_greeks_validation.py.

QuantLib pricing is deterministic and the sampler is seeded, so these
tests are stable across runs.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import options_greeks_validation as gv  # noqa: E402


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
        "iv": 0.30,
        "delta": 0.5,
        "gamma": 0.03,
        "vega": 12.0,
        "theta": -6.0,
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
    return df


class TestBucketing:
    def test_moneyness_atm(self):
        assert gv._bucket_moneyness(0.0) == "atm"
        assert gv._bucket_moneyness(0.02) == "atm"

    def test_moneyness_otm(self):
        assert gv._bucket_moneyness(0.05) == "otm"

    def test_moneyness_deep_otm(self):
        assert gv._bucket_moneyness(0.2) == "deep_otm"

    def test_moneyness_itm(self):
        assert gv._bucket_moneyness(-0.05) == "itm"

    def test_moneyness_nan(self):
        assert gv._bucket_moneyness(float("nan")) == "unknown"

    def test_dte_boundaries(self):
        assert gv._bucket_dte(5) == "under_15"
        assert gv._bucket_dte(30) == "15_45"
        assert gv._bucket_dte(60) == "45_90"
        assert gv._bucket_dte(120) == "over_90"


class TestStratifiedSample:
    def test_deterministic_seed(self):
        rows = [
            _row(option_id=i, date=pd.Timestamp("2010-01-04") + pd.Timedelta(days=i))
            for i in range(20)
        ]
        df = _frame(*rows)
        s1 = gv.stratified_sample(df, target_size=10, seed=7)
        s2 = gv.stratified_sample(df, target_size=10, seed=7)
        assert list(s1["option_id"]) == list(s2["option_id"])

    def test_different_seed_different_sample(self):
        # 20 rows in the same year+bucket -> one big stratum. Different
        # seeds pick different rows within it.
        rows = [
            _row(option_id=i, strike=50.0 + i * 0.01)
            for i in range(20)
        ]
        df = _frame(*rows)
        s1 = gv.stratified_sample(df, target_size=5, seed=1)
        s2 = gv.stratified_sample(df, target_size=5, seed=999)
        assert set(s1["option_id"]) != set(s2["option_id"])

    def test_empty_frame_returns_empty(self):
        assert gv.stratified_sample(pd.DataFrame()).empty

    def test_invalid_rows_excluded(self):
        rows = [
            _row(option_id=1, underlying_price=-1.0),
            _row(option_id=2, underlying_price=50.0),
        ]
        df = _frame(*rows)
        s = gv.stratified_sample(df, target_size=10)
        assert 1 not in s["option_id"].tolist()
        assert 2 in s["option_id"].tolist()

    def test_target_size_respected_approximately(self):
        # 30 rows all in one stratum -> target_size caps at 30.
        rows = [_row(option_id=i, strike=50.0 + i * 0.001) for i in range(30)]
        df = _frame(*rows)
        s = gv.stratified_sample(df, target_size=10)
        assert len(s) == 10  # one stratum, per_stratum = 10


class TestPriceAndGreeks:
    def test_european_atm_call_price_positive(self):
        # Sanity check: 6-month ATM European call on non-dividend stock
        # at 30% vol prices well above zero and delta is roughly 0.5.
        obs = gv._to_ql_date(dt.date(2010, 1, 4))
        exp = gv._to_ql_date(dt.date(2010, 7, 4))
        price, delta, gamma, vega, theta = gv._price_and_greeks(
            spot=50.0, strike=50.0, rate=0.02, dividend=0.0,
            sigma=0.30, obs_date=obs, expiration=exp,
            call_put="C", style="European",
        )
        assert price > 0
        assert 0.4 < delta < 0.7  # near 0.5 for ATM
        assert gamma > 0

    def test_european_put_delta_negative(self):
        obs = gv._to_ql_date(dt.date(2010, 1, 4))
        exp = gv._to_ql_date(dt.date(2010, 7, 4))
        _, delta, _, _, _ = gv._price_and_greeks(
            spot=50.0, strike=50.0, rate=0.02, dividend=0.0,
            sigma=0.30, obs_date=obs, expiration=exp,
            call_put="P", style="European",
        )
        assert -1.0 < delta < 0.0

    def test_american_engine_runs(self):
        obs = gv._to_ql_date(dt.date(2010, 1, 4))
        exp = gv._to_ql_date(dt.date(2010, 7, 4))
        price, delta, gamma, vega, theta = gv._price_and_greeks(
            spot=50.0, strike=50.0, rate=0.02, dividend=0.0,
            sigma=0.30, obs_date=obs, expiration=exp,
            call_put="C", style="American",
        )
        assert price > 0
        assert 0.0 < delta < 1.0


class TestImpliedVol:
    def test_recovers_own_vol_european(self):
        # Price the option at 30% vol, then recover 30% vol from the price.
        obs = gv._to_ql_date(dt.date(2010, 1, 4))
        exp = gv._to_ql_date(dt.date(2010, 7, 4))
        price, *_ = gv._price_and_greeks(
            spot=50.0, strike=50.0, rate=0.02, dividend=0.0,
            sigma=0.30, obs_date=obs, expiration=exp,
            call_put="C", style="European",
        )
        iv = gv._implied_vol(
            spot=50.0, strike=50.0, rate=0.02, dividend=0.0,
            target_price=price,
            obs_date=obs, expiration=exp,
            call_put="C", style="European",
        )
        assert abs(iv - 0.30) < 0.005

    def test_impossible_price_returns_nan(self):
        # Negative target price -> solver never converges.
        obs = gv._to_ql_date(dt.date(2010, 1, 4))
        exp = gv._to_ql_date(dt.date(2010, 7, 4))
        iv = gv._implied_vol(
            spot=50.0, strike=50.0, rate=0.02, dividend=0.0,
            target_price=-1.0,
            obs_date=obs, expiration=exp,
            call_put="C", style="European",
        )
        assert math.isnan(iv)


class TestValidateGreeksSample:
    def _self_consistent_row(self, spot=50.0, strike=50.0, sigma=0.30) -> dict:
        """Build a row whose vendor_iv/delta/... are exactly what QuantLib
        would produce, so comparison is by-construction zero-diff.
        """
        obs = gv._to_ql_date(dt.date(2010, 1, 4))
        exp = gv._to_ql_date(dt.date(2010, 7, 4))
        price, delta, gamma, vega, theta = gv._price_and_greeks(
            spot=spot, strike=strike, rate=0.02, dividend=0.0,
            sigma=sigma, obs_date=obs, expiration=exp,
            call_put="C", style="American",
        )
        return _row(
            underlying_price=spot,
            strike=strike,
            date=pd.Timestamp("2010-01-04"),
            expiration=pd.Timestamp("2010-07-04"),
            iv=sigma,
            delta=delta,
            gamma=gamma,
            vega=vega,
            theta=theta,
            bid=price - 0.01,
            ask=price + 0.01,
        )

    def test_empty_input_fails_with_reason(self):
        report = gv.validate_greeks_sample(pd.DataFrame())
        assert not report.passed
        assert "empty" in report.reasons[0].lower()

    def test_below_min_sample_fails(self):
        rows = [self._self_consistent_row() for _ in range(5)]
        for i, r in enumerate(rows):
            r["option_id"] = i
        df = _frame(*rows)
        report = gv.validate_greeks_sample(
            df, thresholds=gv.VerdictThresholds(min_sample_rows=10)
        )
        assert not report.passed
        assert any("sample too small" in r for r in report.reasons)

    def test_self_consistent_sample_passes(self):
        # If the vendor Greeks match QuantLib exactly (because we built
        # them from QuantLib), median abs diffs are ~0 and the verdict
        # is pass -- as long as we have enough rows.
        rows = [self._self_consistent_row() for _ in range(60)]
        for i, r in enumerate(rows):
            r["option_id"] = i
        df = _frame(*rows)
        report = gv.validate_greeks_sample(df)
        assert report.passed, report.reasons

    def test_grossly_wrong_vendor_delta_fails(self):
        rows = [self._self_consistent_row() for _ in range(60)]
        for i, r in enumerate(rows):
            r["option_id"] = i
            r["delta"] = 0.0  # deliberately wrong -- vendor delta is way off
        df = _frame(*rows)
        report = gv.validate_greeks_sample(df)
        assert not report.passed
        assert any("delta median abs diff" in r for r in report.reasons)

    def test_per_metric_stats_populated(self):
        rows = [self._self_consistent_row() for _ in range(60)]
        for i, r in enumerate(rows):
            r["option_id"] = i
        df = _frame(*rows)
        report = gv.validate_greeks_sample(df)
        metrics = set(report.per_metric_stats["metric"])
        assert metrics == {"iv", "delta", "gamma", "vega", "theta"}


class TestReportSummary:
    def test_pass_summary(self):
        report = gv.GreeksValidationReport(passed=True)
        assert "PASS" in report.summary()

    def test_fail_summary_lists_reasons(self):
        report = gv.GreeksValidationReport(
            passed=False, reasons=["iv median abs diff 0.1 exceeds threshold 0.02"]
        )
        s = report.summary()
        assert "FAIL" in s
        assert "iv median" in s
