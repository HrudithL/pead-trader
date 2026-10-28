"""OV-03 G4: random gates with the same ON fraction and mean run lengths as the trend+breadth gate (read-only).

    python scripts/gate_placebo.py [--seeds 300] [--s-off 0.5]

The alpha book is run once; each placebo only swaps the exposure scale, so it measures what ANY de-risking at those durations gives.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pead.strategies.base as base  # noqa: E402
from pead.common.paths import RUNS_DIR  # noqa: E402
from pead.core.data import load_equity_universe  # noqa: E402
from pead.strategies import registry  # noqa: E402

_cache = {}
_orig = base.run_equity_backtest


def _memo(uni, w, sizing):
    key = id(uni)
    if key not in _cache:
        _cache[key] = _orig(uni, w, sizing)
    return _cache[key]


def runs(g):
    ch = np.flatnonzero(np.diff(g.astype(int)) != 0)
    edges = np.r_[0, ch + 1, len(g)]
    lens = np.diff(edges)
    first_on = g[0]
    on = lens[0::2] if first_on else lens[1::2]
    off = lens[1::2] if first_on else lens[0::2]
    return on.mean(), (off.mean() if len(off) else np.inf)


def random_gate(n, l_on, l_off, rng):
    g = np.ones(n, bool)
    for t in range(1, n):
        g[t] = (rng.uniform() >= 1 / l_on) if g[t - 1] else (rng.uniform() < 1 / l_off)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--s-off", type=float, default=0.5)
    a = ap.parse_args()
    base.run_equity_backtest = _memo
    uni = load_equity_universe()
    real_s = registry.get("steady_drift_plus_gate" if a.s_off == 0 else "steady_drift_plus_gate_half")
    g = real_s.gate(uni)
    l_on, l_off = runs(g)
    print(f"gate ON {g.mean():.3f}, mean ON run {l_on:.0f}, mean OFF run {l_off:.0f}")
    s = registry.get("steady_drift_plus")
    real = real_s.backtest(uni).summary
    rows = []
    for seed in range(a.seeds):
        rg = random_gate(len(g), l_on, l_off, np.random.default_rng(seed))
        s.exposure_scale = lambda uni_, rg=rg: np.where(np.concatenate([[True], rg[:-1]]), 1.0, a.s_off)
        sm = s.backtest(uni).summary
        rows.append(dict(seed=seed, on_frac=rg.mean(), cagr=sm["annualized_return"], sharpe=sm["sharpe"], max_dd=sm["max_drawdown"]))
    s.exposure_scale = lambda uni_: None
    pl = pd.DataFrame(rows)
    res = dict(real_cagr=real["annualized_return"], real_sharpe=real["sharpe"], real_max_dd=real["max_drawdown"],
               placebo_sharpe_mean=pl.sharpe.mean(), placebo_sharpe_p95=pl.sharpe.quantile(.95),
               placebo_dd_mean=pl.max_dd.mean(), placebo_dd_p5=pl.max_dd.quantile(.05), placebo_cagr_mean=pl.cagr.mean(),
               p_sharpe=(1 + (pl.sharpe >= real["sharpe"]).sum()) / (1 + len(pl)),
               p_dd=(1 + (pl.max_dd >= real["max_drawdown"]).sum()) / (1 + len(pl)))
    out = RUNS_DIR / "gate_placebo"
    out.mkdir(parents=True, exist_ok=True)
    pl.to_csv(out / f"gate_placebo_soff{a.s_off}.csv", index=False)
    pd.Series(res).to_csv(out / f"gate_placebo_soff{a.s_off}_summary.csv", header=["value"])
    print(pd.Series(res).round(4).to_string())


if __name__ == "__main__":
    main()
