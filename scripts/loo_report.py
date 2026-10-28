"""MT-08 / OV-01: concentration and leave-one-out stress for one equity strategy (re-runs the engine, read-only on data).

    python scripts/loo_report.py steady_drift_plus [--out results/loo_report]

Removes (a) the single best name, (b) the best 5 names, (c) the best announcement quarter, (d) the best sector by funded
P&L, re-runs the full strategy backtest on the remaining events, and compares CAGR / Sharpe with the base run.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import RUNS_DIR  # noqa: E402
from pead.core.data import load_equity_universe  # noqa: E402
from pead.core.outcomes import firewall  # noqa: E402
from pead.strategies import registry  # noqa: E402


def run_with_mask(strategy, uni, drop):
    w = np.asarray(strategy.weights(firewall(uni.events, strategy.outcome_columns_allowed)), dtype=float).copy()
    w[drop] = 0.0
    from pead.engines.equity import run_equity_backtest
    import types
    # reuse the strategy's own post-processing by swapping its weights()
    orig = strategy.weights
    strategy.weights = types.MethodType(lambda self, ev: w, strategy)
    try:
        return strategy.backtest(uni)
    finally:
        strategy.weights = orig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--out", default=str(RUNS_DIR / "loo_report"))
    a = ap.parse_args()
    s = registry.get(a.name)
    uni = load_equity_universe()
    ev = uni.events
    base = s.backtest(uni)
    pos = base.positions
    tot = pos["net_pnl"].sum()
    by_name = pos.groupby("permno")["net_pnl"].sum().sort_values(ascending=False)
    by_q = pos.groupby(pos["day0_date"].dt.to_period("Q"))["net_pnl"].sum().sort_values(ascending=False)
    by_sec = pos.groupby("ff12_sector")["net_pnl"].sum().sort_values(ascending=False)
    cases = {
        "base": np.zeros(len(ev), bool),
        "drop_best_name": ev["permno"].isin(by_name.index[:1]).to_numpy(),
        "drop_best_5_names": ev["permno"].isin(by_name.index[:5]).to_numpy(),
        "drop_best_quarter": (ev["day0_date"].dt.to_period("Q") == by_q.index[0]).to_numpy(),
        "drop_best_sector": (ev["ff12_sector"] == by_sec.index[0]).to_numpy(),
    }
    rows = []
    for k, m in cases.items():
        r = base if k == "base" else run_with_mask(s, uni, m)
        sm = r.summary
        rows.append(dict(case=k, n_events_removed=int(m.sum()), cagr=sm["annualized_return"], sharpe=sm["sharpe"],
                         max_drawdown=sm["max_drawdown"]))
        print(rows[-1])
    out = pd.DataFrame(rows).round(4)
    conc = dict(top1_name_share=by_name.iloc[0] / tot, top5_name_share=by_name.iloc[:5].sum() / tot,
                top10_name_share=by_name.iloc[:10].sum() / tot, best_quarter=str(by_q.index[0]),
                best_quarter_share=by_q.iloc[0] / tot, best_sector=str(by_sec.index[0]), best_sector_share=by_sec.iloc[0] / tot,
                n_funded=len(pos), total_net_pnl=tot)
    p = Path(a.out) / a.name
    p.mkdir(parents=True, exist_ok=True)
    out.to_csv(p / "loo.csv", index=False)
    pd.Series(conc).to_csv(p / "concentration.csv", header=["value"])
    print(out.to_string(index=False))
    print(pd.Series(conc).to_string())


if __name__ == "__main__":
    main()
