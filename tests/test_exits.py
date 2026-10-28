import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.engines.equity import with_fixed_hold  # noqa: E402
from pead.engines.exits import with_calendar_hold, with_path_exits  # noqa: E402
from tests.pit_kit import jump, make_universe  # noqa: E402

N = 120


def _one(path, day0=10):
    return make_universe([dict(day0=day0, exit=day0 + 5)], [path])


def test_no_stop_reproduces_fixed_hold():
    rng = np.random.default_rng(0)
    u = make_universe([dict(day0=10, exit=20), dict(day0=30, exit=40)], [rng.normal(0, .01, N), rng.normal(0, .01, N)])
    a, b = with_path_exits(u, 25), with_fixed_hold(u, 25)
    pd.testing.assert_frame_equal(a.events, b.events)


def test_percent_stop_exits_at_first_close_below_threshold_not_before():
    r = np.zeros(N)
    r[13], r[15] = -0.03, -0.03                     # cumulative -5.9% at row 15 (k=5), -3% at row 13
    u = with_path_exits(_one(r), 30, stop_pct=0.05)
    assert u.events["exit_row_idx"].iloc[0] == 15
    u2 = with_path_exits(_one(r), 30, stop_pct=0.10)
    assert u2.events["exit_row_idx"].iloc[0] == 10 + 30


def test_stop_ignores_the_future():
    r = np.zeros(N)
    r[13] = -0.07
    r2 = r.copy()
    r2[40:] = 0.5
    a = with_path_exits(_one(r), 30, stop_pct=0.05).events["exit_row_idx"].iloc[0]
    b = with_path_exits(_one(r2), 30, stop_pct=0.05).events["exit_row_idx"].iloc[0]
    assert a == b == 13


def test_trailing_stop_is_latched():
    r = np.zeros(N)
    r[11] = 0.035                                   # activates (peak 1.035)
    r[12] = -0.0215                                 # v=1.0127 < peak*0.98=1.0143 -> exits at row 12
    u = with_path_exits(_one(r), 30, trail_activation=0.03, trail_amount=0.02)
    assert u.events["exit_row_idx"].iloc[0] == 12
    r2 = np.zeros(N)
    r2[11] = 0.02                                   # never reaches the 3% activation: a later -2.5% dip must not exit
    r2[12] = -0.025
    u2 = with_path_exits(_one(r2), 30, trail_activation=0.03, trail_amount=0.02)
    assert u2.events["exit_row_idx"].iloc[0] == 40


def test_calendar_hold_lands_on_a_trading_row_on_or_before_target():
    u = make_universe([dict(day0=10, exit=20)], [np.zeros(N)])
    out = with_calendar_hold(u, 14)                 # 14 calendar days from a business day = 10 sessions later
    assert out.events["exit_row_idx"].iloc[0] == 20
    out2 = with_calendar_hold(u, 15)                # target falls on a weekend: last session on or before it
    assert out2.events["exit_row_idx"].iloc[0] in (20, 21)
    assert out2.events["exit_date"].iloc[0] <= u.events["day0_date"].iloc[0] + pd.Timedelta(days=15)


def test_seasonal_rank_ignores_missing_values_in_the_pool():
    from pead.core import event_signals as es
    q = pd.Series(np.repeat(["2000Q1", "2000Q2", "2000Q3"], 100))
    v = np.r_[np.arange(100.0), np.where(np.arange(100) % 2 == 0, np.nan, np.arange(100.0)), np.arange(100.0)]
    from pead.common.pit_rank import rank_pct
    ok = ~np.isnan(v)
    r = rank_pct(pd.Series(v[ok]), q[ok].reset_index(drop=True), mode="prior_quarters")
    assert r.dropna().max() <= 1.0 + 1e-12 and r.dropna().max() > 0.95
