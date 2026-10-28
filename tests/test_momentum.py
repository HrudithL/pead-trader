import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.strategies.equity.steady_drift_momentum import causal_rank, momentum_12_1  # noqa: E402
from tests.pit_kit import make_universe  # noqa: E402


def test_momentum_uses_row_e_minus_252_to_e_minus_21_only():
    n = 400
    r = np.zeros(n)
    r[100] = 0.10                                   # inside the window for e=300 (rows 48..279)
    r[290] = 0.50                                   # after e-21: must not count
    uni = make_universe([dict(day0=300, exit=330)], [r], n_days=n)
    assert momentum_12_1(uni)[0] == pytest.approx(0.10, rel=1e-9)
    r2 = r.copy()
    r2[300:] = 0.9                                  # anything from the entry row onward is irrelevant
    assert momentum_12_1(make_universe([dict(day0=300, exit=330)], [r2], n_days=n))[0] == pytest.approx(0.10, rel=1e-9)


def test_causal_rank_ignores_same_day_and_later_events():
    rng = np.random.default_rng(0)
    dates = pd.Series(pd.to_datetime("2000-01-03") + pd.to_timedelta(np.repeat(np.arange(0, 400, 2), 5), unit="D"))
    v = rng.normal(size=len(dates))
    base = causal_rank(v, dates, min_pool=50)
    v2 = v.copy()
    cut = dates.iloc[300]
    later = (dates > cut).to_numpy()
    v2[later] = rng.normal(size=int(later.sum())) * 10
    pert = causal_rank(v2, dates, min_pool=50)
    np.testing.assert_array_equal(base[(dates <= cut).to_numpy()], pert[(dates <= cut).to_numpy()])
    assert np.isfinite(base[(dates == cut).to_numpy()]).all()
