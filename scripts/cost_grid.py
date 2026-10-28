"""MT-06 cost sensitivity: re-run strategies with a flat round-trip cost (bps of notional) replacing the liquidity-quintile table.

    python scripts/cost_grid.py [names...]      (default: steady_drift_plus steady_drift_plus_gate_half steady_drift_plus_gate_entry)

Round-trip c bps = c/2 charged at entry and c/2 at exit. The committed table is 10/14/20/30/50 bps round trip (liquidity quintiles
5..1); the grid brackets it. Short-borrow and market impact are NOT modelled (long-only books).
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pead.engines.equity as eq  # noqa: E402
from pead.common.paths import RUNS_DIR  # noqa: E402
from pead.core.data import load_equity_universe  # noqa: E402
from pead.strategies import registry  # noqa: E402

GRID = (0, 5, 10, 20, 40, 80, 160)


def main():
    names = sys.argv[1:] or ["steady_drift_plus", "steady_drift_plus_gate_half", "steady_drift_plus_gate_entry"]
    uni = load_equity_universe()
    saved = (dict(eq.COST_BPS), eq.DEFAULT_COST_BPS)
    rows = []
    try:
        for n in names:
            for rt in GRID:
                one_way = rt / 2.0
                eq.COST_BPS.update({q: one_way for q in range(1, 6)})
                eq.DEFAULT_COST_BPS = one_way
                sm = registry.get(n).backtest(uni).summary
                rows.append(dict(strategy=n, round_trip_bps=rt, cagr=sm["annualized_return"], sharpe=sm["sharpe"],
                                 max_dd=sm["max_drawdown"], total_costs_m=sm.get("total_transaction_costs", float("nan")) / 1e6))
    finally:
        eq.COST_BPS.update(saved[0])
        eq.DEFAULT_COST_BPS = saved[1]
    out = pd.DataFrame(rows).round(4)
    p = RUNS_DIR / "cost_grid"
    p.mkdir(parents=True, exist_ok=True)
    out.to_csv(p / "cost_grid.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
