import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core import decay, event_stats as es  # noqa: E402
from tests.pit_kit import make_universe  # noqa: E402


def _uni(seed, n_days=1500, n_events=600, bad_after=None):
    rng = np.random.default_rng(seed)
    days = rng.integers(5, n_days - 70, n_events)
    rets = []
    for d in days:
        r = rng.normal(0, 0.01, n_days)
        if bad_after is None or d < bad_after:
            r[d + 1:d + 25] += 0.004
        rets.append(r)
    u = make_universe([dict(day0=int(d), exit=int(d) + 60) for d in days], rets, n_days=n_days)
    u.events["sue_rank_pct"] = np.where(rng.uniform(size=n_events) < 0.2, 0.95, 0.5)
    u.events.loc[u.events["sue_rank_pct"] > 0.9, "sue_rank_pct"] = 0.95
    return u, days, rets


def test_scale_is_bounded_and_off_before_history():
    u, _, _ = _uni(0)
    s = decay.decay_scale(u, k=20)
    assert s.max() <= 1.0 and s.min() >= 0.4 and (s[:200] == 1.0).all()


def test_scale_is_causal_future_returns_do_not_move_past_scale():
    u, days, rets = _uni(1)
    T = 1000
    s0 = decay.decay_scale(u, k=20)
    rets2 = [np.r_[r[:T + 1], np.random.default_rng(9).normal(0, 0.05, len(r) - T - 1)] for r in rets]
    u2 = make_universe([dict(day0=int(d), exit=int(d) + 60) for d in days], rets2, n_days=1500)
    u2.events["sue_rank_pct"] = u.events["sue_rank_pct"].to_numpy()
    s1 = decay.decay_scale(u2, k=20)
    np.testing.assert_allclose(s0[:T + 1], s1[:T + 1])


def test_clustered_ols_recovers_era_interaction():
    rng = np.random.default_rng(3)
    nq, n = 40, 300
    q = np.repeat(np.arange(nq), n)
    day = pd.to_datetime("1996-01-01") + pd.to_timedelta(q * 160, unit="D")
    post = (day >= pd.Timestamp("2005-01-01")).astype(float)
    s = rng.uniform(size=nq * n)
    y = (0.05 - 0.03 * post) * s + rng.normal(0, 0.1, nq * n)
    ev = pd.DataFrame(dict(y=y, s=s, ann_quarter=q.astype(str), day0_date=day, ff12_sector="A", size_quintile=1))
    out = es.era_interaction(ev, "y", "s", "2005-01-01")
    assert abs(out["d"] - (-0.03)) < 2 * abs(out["d"] / out["d_t"]) and out["d_t"] < -2
