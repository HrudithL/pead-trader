"""Framework tests.

Unit tests need no data. Reproduction tests compare the new engines against the original per-strategy
scripts' committed outputs (data/backtest_v2_*.csv, data/backtest_options_*_summary.json) and are skipped
when the WRDS/OptionMetrics-derived inputs are not on this machine. Run:  python -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pead.common.paths import DATA_DIR  # noqa: E402
from pead.core import kelly  # noqa: E402
from pead.engines.equity import cap_notionals  # noqa: E402
from pead.strategies import registry, weights as W  # noqa: E402

HAVE_EQUITY = (DATA_DIR / "normalized_equity" / "market_benchmark.parquet").exists() and \
    (DATA_DIR / "positions_rankweighted_v2.parquet").exists()
HAVE_OPTIONS = (DATA_DIR / "event_options" / "optimal_forward_prices.parquet").exists()


# --------------------------------------------------------------------------------------- registry
def test_registry_discovers_all_families():
    names = set(registry.all_strategies())
    for expected in ("balanced_leverage", "calm_weighted", "options_cap20", "options_cap20_legacy", "hedged_blend", "calm_rank_tilt"):
        assert expected in names
    assert {s.asset_class for s in registry.all_strategies().values()} == {"equity", "options", "portfolio"}


def test_every_strategy_declares_metadata():
    for s in registry.all_strategies().values():
        assert s.name and s.description and s.asset_class and s.family
        assert s.status in {"validated", "research", "reference", "unverified"}
        if s.parent:
            assert s.parent in registry.all_strategies(), f"{s.name}: unknown parent {s.parent}"


def test_resolve_selectors():
    assert registry.resolve(["balanced_leverage"]) == ["balanced_leverage"]
    assert "options_cap20" in registry.resolve(["options"])
    assert all(n.startswith("s9") for n in registry.resolve(["s9*"]))
    with pytest.raises(KeyError):
        registry.resolve(["no_such_strategy"])


# ------------------------------------------------------------------------------------------ weights
def _events(n=600, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"ann_quarter": rng.choice(["2000Q1", "2000Q2"], n), "sue_rank_pct": rng.uniform(0, 1, n),
                         "size_quintile": rng.integers(1, 6, n), "ff12_sector": rng.choice(list("abcd"), n),
                         "decile": rng.integers(1, 11, n), "event_id": np.arange(n)})


def test_neutralized_tilt_is_dollar_neutral_and_trims_the_middle():
    ev = _events()
    w = W.neutralized_tilt(ev, W.centered_rank(ev), trim=0.15, tilt_power=1.0)
    assert (w[np.abs(ev["sue_rank_pct"] - 0.5) <= 0.15] == 0).all()
    for q, g in ev.assign(w=w).groupby("ann_quarter"):
        assert abs(g.loc[g.w > 0, "w"].sum() - (-g.loc[g.w < 0, "w"].sum())) < 1e-9
    assert abs(np.abs(w[w != 0]).mean() - 0.5) < 1e-9


def test_scale_reallocates_within_cells_only():
    ev = _events()
    d = W.centered_rank(ev)
    base = W.neutralized_tilt(ev, d, 0.15, 1.0)
    scaled = W.neutralized_tilt(ev, d, 0.15, 1.0, scale=np.where(ev.index % 2 == 0, 2.0, 1.0))
    cell = ev.assign(b=base, s=scaled, side=np.sign(base))
    kept = cell[cell.b != 0]
    tot = kept.groupby(["ann_quarter", "side", "ff12_sector", "size_quintile"])[["b", "s"]].apply(lambda x: x.abs().sum())
    assert np.allclose(tot["b"], tot["s"], rtol=1e-6)   # cell budgets unchanged, only the split inside moves


# ------------------------------------------------------------------------------------------ capacity
def test_cap_notionals_priority_keeps_largest_and_respects_budget():
    target = np.array([100., -80., 60., -40., 20.])
    out, status, dropped = cap_notionals(target, np.full(5, np.inf), max_gross=200.0, mode="priority")
    assert status == "priority_capped" and np.abs(out).sum() <= 200.0 + 1e-9
    assert out[0] == 100.0 and out[1] == -80.0 and out[2] == 20.0 and dropped == 2


def test_cap_notionals_proportional_and_liquidity():
    out, _, _ = cap_notionals(np.array([100., -100.]), np.array([40., np.inf]), max_gross=100.0, mode="proportional")
    assert np.isclose(np.abs(out).sum(), 100.0) and abs(out[0]) < abs(out[1])
    out, status, _ = cap_notionals(np.array([10., -10.]), np.full(2, np.inf), max_gross=100.0)
    assert status == "ok" and (out == np.array([10., -10.])).all()


# --------------------------------------------------------------------------------------------- kelly
def test_kelly_prefers_growth_to_lottery_tickets():
    ret = np.array([-0.20, -0.05, 0.05, 0.40]); prob = np.array([0.2, 0.35, 0.35, 0.10])
    # contract 0: cheap far-OTM call (rare huge payoff); contract 1: ATM call
    g, f, ev = kelly.score_contracts(np, np.array([[1.0, 1.0]]), np.array([[135.0, 100.0]]), np.array([[0.30, 4.0]]),
                                     np.ones((1, 2), bool), np.array([100.0]), ret[None, :], prob[None, :])
    assert ev[0, 0] > ev[0, 1]          # an expected-value maximizer buys the lottery ticket
    assert g[0, 1] > g[0, 0]            # the Kelly objective prefers the contract that grows capital faster
    assert f[0, 0] < f[0, 1]            # ...and sizes the lottery ticket smaller
    best = kelly.best_contract(100.0, [1, 1], [135.0, 100.0], [0.30, 4.0], ret, prob)
    assert best is not None and best[0] == 1


def test_kelly_returns_none_without_edge():
    assert kelly.best_contract(100.0, [1], [100.0], [10.0], np.array([-0.1, 0.0, 0.1]), np.full(3, 1 / 3)) is None


# ---------------------------------------------------------------------------- engines vs the originals
@pytest.mark.skipif(not HAVE_EQUITY, reason="needs the WRDS-derived equity inputs")
@pytest.mark.parametrize("name,legacy", [("balanced_leverage_legacy", "strategy6"), ("sector_balanced_legacy", "strategy5"),
                                         ("market_exposed_legacy", "strategy7"), ("extreme_picks_legacy", "extreme"),
                                         ("graded_bets_legacy", "rankweighted"), ("size_balanced_legacy", "balanced")])
def test_equity_engine_reproduces_original_script_nav(name, legacy):
    from pead.strategies.base import RunContext
    got = registry.get(name).run(RunContext()).daily["nav"].to_numpy()
    ref = pd.read_csv(DATA_DIR / f"backtest_v2_{legacy}.csv")["nav"].to_numpy()
    assert np.abs(got - ref).max() < 1.0, "NAV differs from the original per-strategy script"


@pytest.mark.skipif(not HAVE_EQUITY, reason="needs the WRDS-derived equity inputs")
def test_strategy4_matches_original_script_on_current_data():
    # `_legacy` = the original (pre-fix) leverage cap, which is what those scripts implemented.
    # the committed backtest_v2_strategy4.csv predates the current data (27.147M); the original script
    # re-run on current data ends at 27.077M, which is what the engine must hit
    from pead.strategies.base import RunContext
    res = registry.get("conviction_tilt_legacy").run(RunContext())
    assert abs(res.summary["final_nav"] - 27_077_291.9) < 1.0


@pytest.mark.skipif(not (HAVE_EQUITY and (DATA_DIR / "backtest_v2_strategy9_strategy6_baseline.csv").exists()
                            and (DATA_DIR / "equity_vol_panel.parquet").exists()),
                    reason="needs equity inputs, the volatility panel and Strategy 9 outputs")
def test_inverse_vol_matches_strategy9_script():
    from pead.strategies.base import RunContext
    got = registry.get("calm_weighted_legacy").run(RunContext()).daily["nav"].to_numpy()
    ref = pd.read_csv(DATA_DIR / "backtest_v2_strategy9_9b_inverse_ivol.csv")["nav"].to_numpy()
    assert np.abs(got - ref).max() < 1.0


@pytest.mark.skipif(not HAVE_OPTIONS, reason="needs OptionMetrics-derived option inputs")
@pytest.mark.parametrize("name,legacy", [("options_cap20_legacy", "options_optimal_h60d"), ("options_cap30_legacy", "options_optimal_cap30_h60d")])
def test_options_engine_reproduces_original_final_nav(name, legacy):
    from pead.strategies.base import RunContext
    got = registry.get(name).run(RunContext()).summary["final_nav"]
    ref = json.loads((DATA_DIR / f"backtest_{legacy}_summary.json").read_text())["final_nav"]
    assert abs(got - ref) < 1.0


# -------------------------------------------------------------- pre-fix numbers stay reproducible
@pytest.mark.skipif(not HAVE_EQUITY, reason="needs the WRDS-derived equity inputs")
def test_balanced_leverage_legacy_pins_published_nav():
    from pead.strategies.base import RunContext
    res = registry.get("balanced_leverage_legacy").run(RunContext())
    assert abs(res.summary["final_nav"] - 34_048_037.4) < 1.0
    assert res.summary["exposure_cap"] == "entry_cohort"


def test_every_equity_strategy_has_a_legacy_twin():
    allr = registry.all_strategies()
    for n, s in allr.items():
        if s.asset_class == "equity" and not n.endswith("_legacy"):
            twin = allr[f"{n}_legacy"]
            assert twin.status == "reference" and twin.parent == n
            assert twin.sizing.exposure_cap == "entry_cohort" and s.sizing.exposure_cap == "open_book"
            assert twin.sizing.base_unit_fraction == s.sizing.base_unit_fraction


@pytest.mark.skipif(not HAVE_OPTIONS, reason="needs OptionMetrics-derived option inputs")
@pytest.mark.parametrize("name,ann,sharpe", [("options_cap20_midfill", 0.04124, 1.769), ("options_cap20_legacy", 0.08581, 3.780)])
def test_options_mid_conventions_stay_reproducible(name, ann, sharpe):
    from pead.strategies.base import RunContext
    s = registry.get(name).run(RunContext()).summary
    assert abs(s["annualized_return"] - ann) < 5e-5 and abs(s["sharpe"] - sharpe) < 5e-3


# ------------------------------------------------------------------------------------------------ live
def test_live_engine_mock_dry_run(tmp_path):
    env = {**os.environ, "PEAD_LIVE_DIR": str(tmp_path)}
    out = subprocess.run([sys.executable, "-m", "pead", "live", "run", "--mock-data", "--dry-run", "--seed", "1",
                          "--limit-universe", "120"], cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "=== done ===" in out.stdout and "equity OPEN" in out.stdout
    assert not (tmp_path / "equity_positions.json").exists(), "--dry-run must not write state"
