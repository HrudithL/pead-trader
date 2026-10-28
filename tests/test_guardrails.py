"""Outcome firewall, strategy causality metadata (LA-01, LA-20) and the trial registry."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core import stats  # noqa: E402
from pead.core.outcomes import OUTCOME_COLUMNS, firewall  # noqa: E402
from pead.eval import registry  # noqa: E402
from pead.strategies.registry import all_strategies  # noqa: E402


def _events(n=40):
    rng = np.random.default_rng(0)
    d = {c: rng.normal(size=n) for c in OUTCOME_COLUMNS}
    d.update(ann_quarter=np.repeat(["2000Q1", "2000Q2"], n // 2), sue_rank_pct=rng.uniform(size=n))
    return pd.DataFrame(d)


def test_firewall_drops_only_disallowed_outcomes_and_leaves_input():
    e = _events()
    f = firewall(e)
    assert not set(OUTCOME_COLUMNS) & set(f.columns) and {"ann_quarter", "sue_rank_pct"} <= set(f.columns)
    g = firewall(e, allowed=("hold_ret", "exit_date"))
    assert {"hold_ret", "exit_date"} <= set(g.columns) and "holding_days" not in g.columns
    assert set(OUTCOME_COLUMNS) <= set(e.columns)


def test_every_equity_strategy_declares_causality_metadata():
    for name, s in all_strategies().items():
        assert isinstance(s.causal, bool) and isinstance(s.outcome_columns_allowed, tuple), name
        assert set(s.outcome_columns_allowed) <= set(OUTCOME_COLUMNS), name


def test_only_the_walk_forward_filter_strategies_may_read_outcomes():
    allowed = {n for n, s in all_strategies().items() if s.outcome_columns_allowed}
    assert {n.removesuffix("_legacy") for n in allowed} == {"volatility_filter", "calm_weighted_filter"}


def test_trial_registry_is_append_only_and_feeds_dsr(tmp_path):
    p = tmp_path / "trials.jsonl"
    assert registry.n_trials(path=p) == 0
    for i, sr in enumerate([0.02, 0.05, 0.01, 0.07]):
        registry.log_trial(f"s{i}", "fam_a", sr, {"k": i}, path=p)
    registry.log_trial("x", "fam_b", 0.03, path=p)
    assert registry.n_trials(path=p) == 5 and registry.n_trials("fam_a", p) == 4
    before = p.read_text()
    registry.log_trial("y", "fam_a", 0.04, path=p)
    assert p.read_text().startswith(before)
    v = registry.trial_sharpe_variance("fam_a", p)
    assert v > 0
    assert stats.dsr(0.08, 4000, 0.0, 3.0, registry.n_trials("fam_a", p), v) < stats.psr(0.08, 4000, 0.0, 3.0)


def test_manifest_fields_are_written_by_save_result(tmp_path, monkeypatch):
    import json
    import pead.core.store as store
    from pead.strategies.base import BacktestResult
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(store, "update_leaderboard", lambda: None)
    daily = pd.DataFrame({"date": pd.bdate_range("2000-01-03", periods=5), "daily_pnl": 1.0, "nav": 1.0, "daily_return": 0.0})
    r = BacktestResult("toy", "equity", daily, {"sharpe": 1.0}, pd.DataFrame(), None, {"name": "toy", "params": {"a": 1}})
    d = store.save_result(r)
    run = json.loads((d / "summary.json").read_text())["run"]
    assert {"git_rev", "python", "git_dirty", "data_fingerprint", "params_hash", "pandas"} <= set(run)
    assert len(run["params_hash"]) == 12
