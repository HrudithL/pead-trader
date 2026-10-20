"""Equity capacity frontier on the CORRECTED data and engine: base-unit sweep of the balanced_leverage construction at a 6x gross cap.

Replaces data/sweep_aggressive_strategy6.csv (pre-correction). Same grid as the old file (multiples of balanced_leverage's own base unit,
1.5x..12x), run with the current engine (open-book cap, point-in-time deciles, search-window decay days). Nothing is registered or saved
except the output CSV.   python scripts/capacity_sweep.py [out.csv]
Run with PEAD_DATA_DIR / PEAD_RAW_EQUITY_DIR pointing at the corrected data vintage (see scripts/vol_sweeps.py).
Self-check: the 4x row (base unit 0.012) must reproduce results/balanced_leverage_max."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pead.strategies.base import RunContext
from pead.strategies.equity.balanced_leverage import BalancedLeverage

MULTS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]
CAP = 6.0
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results" / "capacity_sweep.csv"
ctx = RunContext()
base_b = BalancedLeverage.sizing.base_unit_fraction

rows = []
for m in MULTS:
    class V(BalancedLeverage):
        name = "variant"
        sizing = replace(BalancedLeverage.sizing, base_unit_fraction=base_b * m, max_gross_leverage=CAP)
    s = V().run(ctx).summary
    rows.append(dict(buf_multiplier=m, base_unit_fraction=base_b * m, annualized_return=s["annualized_return"], annualized_vol=s["annualized_vol"],
                     sharpe=s["sharpe"], max_drawdown=s["max_drawdown"], avg_leverage_vs_initial_capital=s["avg_leverage_vs_initial_capital"],
                     pct_liquidity_capped=s["pct_liquidity_capped"]))
    print(m, round(s["annualized_return"], 4), round(s["sharpe"], 3), flush=True)

df = pd.DataFrame(rows)
saved = json.load(open(ROOT / "results" / "balanced_leverage_max" / "summary.json"))["summary"]
chk = df[(df.buf_multiplier - 4.0).abs() < 1e-9].iloc[0]
assert abs(chk.annualized_return - saved["annualized_return"]) < 1e-9 and abs(chk.sharpe - saved["sharpe"]) < 1e-9, "4x row != balanced_leverage_max"
df.to_csv(OUT, index=False)
print("wrote", OUT)
