"""MT-11 / REF-placebo: shuffled-surprise placebo with many seeds for one equity strategy.

    python scripts/placebo_report.py steady_drift_plus [--seeds 30] [--out results/placebo_report]

SUE ranks are permuted within each announcement quarter (signal destroyed, marginal distribution and everything else
kept), the full strategy is re-run, and the real Sharpe/CAGR are compared with the placebo distribution (empirical p).
"""
import argparse
import sys
import time
import types
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import RUNS_DIR  # noqa: E402
from pead.core.data import load_equity_universe  # noqa: E402
from pead.strategies import registry  # noqa: E402


def shuffled(strategy, seed):
    orig = type(strategy).weights

    def w(self, events):
        ev = events.copy()
        rng = np.random.default_rng(seed)
        v = ev["sue_rank_pct"].to_numpy().copy()
        for _, ix in ev.groupby("ann_quarter").indices.items():
            v[ix] = rng.permutation(v[ix])
        ev["sue_rank_pct"] = v
        return orig(self, ev)
    strategy.weights = types.MethodType(w, strategy)
    return strategy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--out", default=str(RUNS_DIR / "placebo_report"))
    a = ap.parse_args()
    uni = load_equity_universe()
    real = registry.get(a.name).backtest(uni).summary
    rows = []
    t0 = time.time()
    for seed in range(a.seeds):
        s = shuffled(type(registry.get(a.name))(), seed)
        sm = s.backtest(uni).summary
        rows.append(dict(seed=seed, cagr=sm["annualized_return"], sharpe=sm["sharpe"]))
        if seed == 0:
            print(f"one placebo run took {time.time() - t0:.0f}s")
    pl = pd.DataFrame(rows)
    p_sharpe = (1 + (pl["sharpe"] >= real["sharpe"]).sum()) / (1 + len(pl))
    res = dict(strategy=a.name, real_cagr=real["annualized_return"], real_sharpe=real["sharpe"],
               placebo_cagr_mean=pl["cagr"].mean(), placebo_cagr_max=pl["cagr"].max(),
               placebo_sharpe_mean=pl["sharpe"].mean(), placebo_sharpe_max=pl["sharpe"].max(), n_seeds=len(pl),
               empirical_p_sharpe=p_sharpe)
    p = Path(a.out) / a.name
    p.mkdir(parents=True, exist_ok=True)
    pl.to_csv(p / "placebo_runs.csv", index=False)
    pd.Series(res).to_csv(p / "placebo_summary.csv", header=["value"])
    print(pd.Series(res).to_string())


if __name__ == "__main__":
    main()
