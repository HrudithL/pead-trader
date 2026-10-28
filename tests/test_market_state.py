import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core.market_state import earnings_breadth, gate_scale, trend_gate  # noqa: E402
from tests.pit_kit import make_universe  # noqa: E402


def test_trend_gate_off_in_a_persistent_downtrend_and_on_during_warmup():
    r = np.r_[np.full(300, 0.001), np.full(300, -0.002)]
    g = trend_gate(r)
    assert g[:200].all() and g[299] and not g[-1]


def test_trend_gate_is_causal():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0003, 0.01, 800)
    r2 = r.copy()
    r2[500:] = rng.normal(-0.01, 0.05, 300)
    np.testing.assert_array_equal(trend_gate(r)[:500], trend_gate(r2)[:500])


def test_gate_scale_uses_previous_day_gate():
    g = np.array([True, True, False, False, True])
    np.testing.assert_array_equal(gate_scale(g, 0.5), [1.0, 1.0, 1.0, 0.5, 0.5])


def test_breadth_counts_good_events_and_requires_minimum_sample():
    n = 60
    rets = [np.r_[np.zeros(30), [0.01 if i < 50 else -0.01], np.zeros(n - 31)] for i in range(200)]
    uni = make_universe([dict(day0=30, exit=40)] * 200, rets, n_days=n)
    uni.events["sue_rank_pct"] = [0.9 if i < 100 else 0.1 for i in range(200)]
    # reactions are read at the day0 row (30), where the jump is placed only from index 31: so set the jump on row 30
    for i in range(200):
        uni.ret_raw[i * n + 30] = 0.01 if i < 50 else -0.01
    b = earnings_breadth(uni, window=5, n_min=150)
    assert np.isnan(b[:30]).all() and b[30] == 50 / 200
    assert np.isnan(earnings_breadth(uni, window=5, n_min=500)[30])


def test_entry_gate_zeroes_weights_of_events_entering_while_off():
    import pandas as pd
    from pead.strategies.equity.steady_drift_gate import SteadyDriftPlusGateEntry
    s = SteadyDriftPlusGateEntry()
    n = 6
    ev = pd.DataFrame(dict(avg_dollar_volume_21d=[1e7] * n, sue_rank_pct=[0.95] * n, ivol_252d=[0.3] * n, tvol_60d=[0.02] * n,
                           day0_date=pd.bdate_range("2000-01-03", periods=n), ann_quarter=["2000Q1"] * n,
                           gate_on_day0=[True, False, True, False, True, True]))
    w = s.weights(ev)
    assert (w[[1, 3]] == 0).all()
