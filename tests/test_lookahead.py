"""Look-ahead and accounting guard rails (MT-01: LA-03..LA-10, LA-12 subset). Synthetic only."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.pit_rank import rank_pct  # noqa: E402
from pead.engines import equity as eq  # noqa: E402
from pead.engines.equity import Sizing, apply_cash_and_risk_target, run_equity_backtest  # noqa: E402
from tests.pit_kit import jump, make_universe  # noqa: E402

N = 120
KW = dict(base_unit_fraction=0.5, max_gross_leverage=2.5, liquidity_cap_frac=1e9, cap_mode="chronological")


@pytest.fixture
def no_costs(monkeypatch):
    monkeypatch.setattr(eq, "COST_BPS", {q: 0 for q in range(1, 6)})
    monkeypatch.setattr(eq, "DEFAULT_COST_BPS", 0)


def test_la04_no_same_close_return(no_costs):
    """A +10% jump on the entry day itself is never earned: the engine accrues from entry_row_idx + 1."""
    uni = make_universe([dict(day0=10, exit=30)], [jump(N, 10, 0.10)])
    run = run_equity_backtest(uni, np.ones(1), Sizing(**KW))
    assert abs(run.daily["nav"].iloc[-1] - 1e6) < 1e-6
    uni2 = make_universe([dict(day0=10, exit=30)], [jump(N, 11, 0.10)])      # the next session IS earned
    run2 = run_equity_backtest(uni2, np.ones(1), Sizing(**KW))
    assert run2.daily["nav"].iloc[-1] == pytest.approx(1e6 + 0.5e6 * 0.10)


def test_la05_entry_shift_changes_pnl_by_one_day_return(no_costs):
    path = np.zeros(N)
    path[11], path[12] = 0.02, 0.03
    a = run_equity_backtest(make_universe([dict(day0=10, exit=30)], [path]), np.ones(1), Sizing(**KW))
    b = run_equity_backtest(make_universe([dict(day0=11, exit=30)], [path]), np.ones(1), Sizing(**KW))
    assert a.daily["nav"].iloc[-1] - b.daily["nav"].iloc[-1] == pytest.approx(0.5e6 * 0.02)


def test_la06_concurrent_positions_do_not_compound_serially(no_costs):
    uni = make_universe([dict(day0=5, exit=40), dict(day0=5, exit=40)], [jump(N, 20, 0.10), jump(N, 20, -0.10)])
    run = run_equity_backtest(uni, np.ones(2), Sizing(**KW))
    assert run.daily["nav"].iloc[-1] == pytest.approx(1e6, abs=1e-6)


def test_la07_round_trip_fee_is_exact():
    uni = make_universe([dict(day0=5, exit=40)], [np.zeros(N)])              # liquidity_quintile 3 -> 10 bps each way
    run = run_equity_backtest(uni, np.ones(1), Sizing(**KW))
    assert 1e6 - run.daily["nav"].iloc[-1] == pytest.approx(2 * 0.5e6 * 10 / 1e4)


def test_la03_future_returns_do_not_move_past_nav():
    rng = np.random.default_rng(1)
    events = [dict(day0=int(d), exit=int(d) + 25) for d in rng.integers(2, 80, 12)]
    rets = [rng.normal(0, 0.01, N) for _ in events]
    T = 70
    rets2 = [np.r_[r[:T + 1], rng.normal(0, 0.05, N - T - 1)] for r in rets]
    sz = Sizing(**KW)
    a = run_equity_backtest(make_universe(events, rets), np.ones(len(events)), sz).daily["nav"].to_numpy()
    b = run_equity_backtest(make_universe(events, rets2), np.ones(len(events)), sz).daily["nav"].to_numpy()
    assert np.abs(a[:T + 1] - b[:T + 1]).max() < 1e-9
    assert np.abs(a[T + 5:] - b[T + 5:]).max() > 0         # the perturbation is real, not a no-op


def test_la08_pit_rank_ignores_current_and_later_quarters():
    rng = np.random.default_rng(2)
    quarters = pd.Series(np.repeat(np.arange(8), 25))
    for _ in range(50):
        v = pd.Series(rng.normal(size=len(quarters)))
        base = rank_pct(v, quarters, mode="prior_quarters")
        q = int(rng.integers(2, 8))
        v2 = v.copy()
        later = (quarters > q).to_numpy()
        v2[later] = rng.normal(size=int(later.sum())) * 10
        pert = rank_pct(v2, quarters, mode="prior_quarters")
        upto = (quarters <= q).to_numpy()
        np.testing.assert_array_equal(base[upto].to_numpy(), pert[upto].to_numpy())


def test_la10_risk_target_exposure_is_causal():
    rng = np.random.default_rng(3)
    n = 400
    cal = pd.bdate_range("2000-01-03", periods=n)
    r = rng.normal(0, 0.01, n)

    def run_for(ret):
        nav = 1e6 * np.cumprod(1 + ret)
        d = pd.DataFrame({"date": cal, "daily_pnl": np.diff(nav, prepend=1e6), "nav": nav, "daily_return": ret,
                          "gross_exposure": np.full(n, 5e5)})
        return eq.EquityRun(daily=d, quarterlog=pd.DataFrame(), positions=pd.DataFrame(), summary={})

    rf = np.full(n, 0.0001)
    e0 = apply_cash_and_risk_target(run_for(r), rf, 1e6, target_vol=0.05).daily["risk_exposure"].to_numpy()
    for t in rng.integers(100, 390, 10):
        r2 = r.copy()
        r2[t:] = rng.normal(0, 0.2, n - t)
        e1 = apply_cash_and_risk_target(run_for(r2), rf, 1e6, target_vol=0.05).daily["risk_exposure"].to_numpy()
        np.testing.assert_allclose(e0[:t + 1], e1[:t + 1])


def test_la12_ledger_reconciles_with_nav(no_costs):
    rng = np.random.default_rng(4)
    events = [dict(day0=int(d), exit=int(d) + 20) for d in rng.integers(2, 90, 15)]
    rets = [rng.normal(0, 0.02, N) for _ in events]
    run = run_equity_backtest(make_universe(events, rets), rng.choice([-1.0, 1.0], len(events)), Sizing(**KW))
    d = run.daily
    assert abs(d["nav"].iloc[-1] - 1e6 - d["daily_pnl"].sum()) < 1e-6
    assert abs(run.positions["net_pnl"].sum() - (d["nav"].iloc[-1] - 1e6)) < 1e-6
    assert (d["gross_exposure"] >= d["net_exposure"].abs() - 1e-9).all()
