"""Tests for the long-only machinery behind Steady Drift: fixed holds, causal weights, first-come-first-served cap,
cash yield and the risk throttle. Synthetic, no data needed."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pead.core.data import EquityUniverse  # noqa: E402
from pead.engines.equity import (EquityRun, Sizing, apply_cash_and_risk_target, cap_open_book, run_equity_backtest,  # noqa: E402
                                 with_fixed_hold)
from pead.strategies import registry, weights as W  # noqa: E402


def _panel(lengths, start="2000-01-03"):
    """A panel of consecutive permnos, each with `lengths[i]` rows, plus one event on the first row of each."""
    cal = pd.bdate_range(start, periods=max(lengths) + 5)
    dates, permno, entry = [], [], []
    for p, n in enumerate(lengths):
        entry.append(len(dates))
        dates.extend(cal[:n])
        permno.extend([p + 1] * n)
    ev = pd.DataFrame({"permno": np.arange(1, len(lengths) + 1), "entry_row_idx": entry, "day0_date": cal[0],
                       "avg_dollar_volume_21d": 1e12, "liquidity_quintile": 3})
    ev["exit_row_idx"] = ev["entry_row_idx"] + 1
    ev["exit_date"] = cal[1]
    z = np.zeros(len(dates))
    return EquityUniverse(events=ev, ret_mktadj=z, ret_raw=z, panel_date=np.array(dates, dtype="datetime64[ns]"), calendar=cal,
                          vwretd=np.zeros(len(cal)), initial_capital=1e6, panel_permno=np.array(permno)), cal


def test_fixed_hold_is_cut_at_the_stocks_last_row_and_never_crosses_into_the_next_stock():
    uni, _ = _panel([100, 30, 1])
    out = with_fixed_hold(uni, 60)
    assert list(out.events["permno"]) == [1, 2]                         # permno 3 has no row after day0 -> dropped
    assert list(out.events["holding_days"]) == [60, 29]                 # stock 2 delists after 29 more sessions: kept, truncated
    assert (out.events["exit_row_idx"] - out.events["entry_row_idx"]).tolist() == [60, 29]
    assert (out.events["exit_row_idx"] < np.array([100, 130])).all()


def _events_for_weights(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"day0_date": pd.to_datetime("2001-01-01") + pd.to_timedelta(np.sort(rng.integers(0, 900, n)), "D"),
                         "ivol_252d": rng.uniform(0.01, 0.05, n), "tvol_60d": 0.02})


def test_causal_inverse_variance_ignores_the_future_and_the_mask():
    ev = _events_for_weights()
    mask = np.ones(len(ev), bool)
    w = W.causal_inverse_variance(ev, mask)
    cut = ev["day0_date"].iloc[2000]
    early = (ev["day0_date"] <= cut).to_numpy()
    ev2 = ev.copy()
    ev2.loc[~early, "ivol_252d"] = 0.5                                  # rewrite everything that happens after `cut`
    w2 = W.causal_inverse_variance(ev2, mask)
    assert np.allclose(w[early], w2[early], equal_nan=True)             # earlier weights cannot depend on later events
    assert (W.causal_inverse_variance(ev, np.zeros(len(ev), bool)) == 0).all()
    assert (w[: 200] == 0).all()                                        # no prior pool -> no bet
    calm, jumpy = ev["ivol_252d"].to_numpy() < 0.02, ev["ivol_252d"].to_numpy() > 0.04
    assert w[calm & (w > 0)].mean() > w[jumpy & (w > 0)].mean()


def test_chronological_cap_admits_in_announcement_order():
    target = np.array([5.0, 1.0, 1.0])                                  # the biggest bet arrives LAST
    ent, ext = np.array([0, 5, 10]), np.array([20, 20, 20])
    carry = np.zeros(20)
    chron, _, _ = cap_open_book(target[::-1].copy(), np.full(3, np.inf), 2.0, "chronological", carry, ent, ext)
    prio, _, _ = cap_open_book(target[::-1].copy(), np.full(3, np.inf), 2.0, "priority", carry, ent, ext)
    assert chron.tolist() == [1.0, 1.0, 0.0]        # early small bets fill the budget first; the late big bet gets nothing
    assert prio.tolist() == [0.0, 0.0, 2.0]         # priority needs to see the whole quarter: it hands the budget to the big one


def _run(returns, gross):
    n = len(returns)
    nav = 1e6 * np.cumprod(1 + np.asarray(returns))
    d = pd.DataFrame({"date": pd.bdate_range("2000-01-03", periods=n), "daily_return": returns, "nav": nav,
                      "daily_pnl": np.diff(nav, prepend=1e6), "gross_exposure": np.asarray(gross) * nav})
    return EquityRun(daily=d, quarterlog=pd.DataFrame(), positions=pd.DataFrame(), summary={})


def test_risk_target_never_levers_and_uses_only_the_past():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0004, 0.01, 400)
    rf = np.full(400, 0.0001)
    base = apply_cash_and_risk_target(_run(r, np.full(400, 0.6)), rf, 1e6, target_vol=0.08)
    e = base.daily["risk_exposure"].to_numpy()
    assert e.max() <= 1.0 + 1e-12 and e.min() > 0
    r2 = r.copy()
    r2[300:] *= 5                                                       # a volatility explosion from day 300 on
    alt = apply_cash_and_risk_target(_run(r2, np.full(400, 0.6)), rf, 1e6, target_vol=0.08)
    assert np.allclose(e[:301], alt.daily["risk_exposure"].to_numpy()[:301])   # exposure through day 300 is unaffected by the future
    assert alt.daily["risk_exposure"].to_numpy()[330:].mean() < e[330:].mean()  # ...and it reacts afterwards


def test_idle_cash_earns_the_cash_rate():
    rf = np.full(250, 0.0002)
    run = apply_cash_and_risk_target(_run(np.zeros(250), np.zeros(250)), rf, 1e6, target_vol=None)
    assert abs(run.daily["nav"].iloc[-1] / 1e6 - (1.0002) ** 249) < 1e-6 or run.daily["nav"].iloc[-1] > 1e6 * 1.04


def test_name_cap_limits_position_size():
    uni, _ = _panel([40, 40, 40])
    uni.events["exit_row_idx"] = uni.events["entry_row_idx"] + 20
    w = np.array([10.0, 1.0, 1.0])
    s = Sizing(base_unit_fraction=0.5, max_gross_leverage=5.0, liquidity_cap_frac=1.0, return_basis="raw", name_cap_frac=0.02)
    run = run_equity_backtest(uni, w, s)
    assert run.positions["notional"].abs().max() <= 0.02 * 1e6 + 1e-6


def test_steady_strategies_registered_without_legacy_twins():
    allr = registry.all_strategies()
    for n in ("steady_drift", "steady_drift_calm", "steady_drift_core", "steady_drift_plus"):
        s = allr[n]
        assert s.sizing.return_basis == "raw" and s.sizing.max_gross_leverage <= 1.0 and s.hold_sessions == 60
        assert f"{n}_legacy" not in allr
    assert allr["steady_drift_core"].risk_target_vol is None and allr["steady_drift_plus"].risk_target_vol == 0.12
