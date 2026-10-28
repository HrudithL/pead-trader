"""pead.core.stats (MT-03) and the legacy-metric labelling checks (LA-14)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core import stats  # noqa: E402
from pead.core.metrics import summarize  # noqa: E402
from tests.pit_kit import random_daily_frame  # noqa: E402


def _constant(n=504, r=0.001, cap0=1e6):
    nav = cap0 * (1 + r) ** np.arange(1, n + 1)
    return pd.DataFrame({"date": pd.bdate_range("2000-01-03", periods=n), "nav": nav,
                         "daily_pnl": np.diff(nav, prepend=cap0), "daily_return": np.full(n, r)})


def test_la14_cagr_is_compounded_not_arithmetic():
    d = _constant()
    s = summarize(d, 1e6)
    assert s["annualized_return"] == pytest.approx(1.001 ** 252 - 1)
    assert stats.cagr(1e6, d["nav"].iloc[-1], len(d)) == pytest.approx(1.001 ** 252 - 1)
    assert stats.arithmetic_annualized(d["daily_return"]) == pytest.approx(0.252)


def test_drawdown_counts_the_initial_capital():
    nav = np.array([990_000.0, 1_000_000.0, 1_010_000.0])
    assert stats.max_drawdown(nav, 1_000_000.0) == pytest.approx(-0.01)
    assert stats.max_drawdown(np.array([1.1e6, 1.2e6]), 1e6) == 0.0


def test_active_window_skips_flat_warmup_and_tail():
    d = random_daily_frame(300)
    d.loc[:49, ["daily_pnl", "daily_return"]] = 0.0
    d.loc[250:, ["daily_pnl", "daily_return"]] = 0.0
    w = stats.active_window(d)
    assert w.start == d["date"].iloc[50] and w.end == d["date"].iloc[249]
    assert stats.active_summary(d, 1e6)["n_days_active"] == 200


def test_sharpe_matches_legacy_summary():
    d = random_daily_frame(800, seed=5)
    r = stats.returns_from_daily(d, 1e6)
    assert stats.sharpe(r) == pytest.approx(summarize(d, 1e6)["sharpe"], rel=1e-9)


def test_sortino_uses_all_observations():
    r = np.array([0.01, -0.02, 0.03, -0.01])
    expected = r.mean() * 252 / (np.sqrt(np.mean(np.minimum(r, 0) ** 2)) * np.sqrt(252))
    assert stats.sortino(r) == pytest.approx(expected)


def test_psr_dsr_known_values():
    s, T = 0.1, 250
    z = s * np.sqrt(T - 1) / np.sqrt(1 + (3 - 1) / 4 * s ** 2)
    assert stats.psr(s, T, 0.0, 3.0) == pytest.approx(sps.norm.cdf(z))
    assert stats.psr(s, T, 0.0, 3.0, sr_bench=s) == pytest.approx(0.5)
    assert stats.expected_max_sr(1, 0.01) == 0.0
    assert stats.expected_max_sr(100, 0.01) > stats.expected_max_sr(10, 0.01) > 0
    assert stats.dsr(s, T, 0.0, 3.0, 100, 0.01) < stats.psr(s, T, 0.0, 3.0)
    mtrl = stats.minimum_track_record_length(s, 0.0, 3.0)
    assert stats.psr(s, int(np.ceil(mtrl)), 0.0, 3.0) == pytest.approx(0.95, abs=0.005)


def test_bootstrap_is_seed_stable_and_block_ci_wider_for_ar1():
    rng = np.random.default_rng(7)
    n = 1500
    e = rng.normal(0, 0.01, n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.6 * x[i - 1] + e[i]
    idx = pd.bdate_range("2000-01-03", periods=n)
    a = stats.block_bootstrap(x + 0.0004, idx, "month", n=300, seed=1)
    b = stats.block_bootstrap(x + 0.0004, idx, "month", n=300, seed=1)
    np.testing.assert_array_equal(a, b)
    iid = stats.block_bootstrap(x + 0.0004, idx, 1, n=300, seed=1)
    assert a.std() > iid.std()


def test_paired_bootstrap_of_identical_series_is_zero():
    d = random_daily_frame(300, seed=9)
    r = d["daily_return"].to_numpy()
    draws = stats.paired_block_bootstrap(r, r, d["date"], n=50, seed=3)
    assert np.allclose(draws, 0.0)


def test_alpha_hac_recovers_known_alpha_and_beta():
    rng = np.random.default_rng(11)
    T = 3000
    m = rng.normal(0.0004, 0.01, T)
    r = 0.0002 + 0.7 * m + rng.normal(0, 0.002, T)
    out = stats.alpha_hac(r, m)
    assert out["alpha_ann"] == pytest.approx(0.0002 * 252, abs=0.02)
    assert out["loadings"][0] == pytest.approx(0.7, abs=0.02)
    assert out["lags"] == int(np.floor(4 * (T / 100) ** (2 / 9)))
