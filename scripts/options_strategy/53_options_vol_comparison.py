"""
Side-by-side table of the Tier 1.5 (Kelly-optimal) options backtest vs. its volatility variants, at
the 20% and 30% outstanding-premium caps, with Sharpe by era -- the options counterpart of
equity_strategy/41's comparison. Reads only the backtest files 50 already wrote; run these first:

  46 --vol-conditioned [--vol-units return]   vol-conditioned return distributions
  47c                                          per-event ATM IV, realized vol, IV/RV
  48 --vol-conditioned [--vol-dist volterc]    vol-conditioned picks
  49 --tag vol | volterc                       forward prices for those picks
  50 --tag ... / --iv-rv-filter --output-prefix backtest_options_optimal_<variant>[_cap30]

Variants: baseline (decile-only distribution), vol (per (decile, vol tercile) cell, states in
units of each stock's own trailing vol, rescaled per event), volterc (same cells, plain return
units), ivrv (baseline picks, skip the richest prior-quarter tercile of IV/RV), vol_ivrv (both).

Output: data/backtest_options_vol_comparison.csv
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR

VARIANTS = ["", "_vol", "_volterc", "_ivrv", "_vol_ivrv"]
CAPS = {"": "20%", "_cap30": "30%"}
HORIZONS = [20, 40, 60]
ERAS = {"1996_2001": ("1996", "2001"), "2002_2007": ("2002", "2007"), "2008_2013": ("2008", "2013")}


def main():
    rows = []
    for v in VARIANTS:
        for cap_sfx, cap in CAPS.items():
            prefix = f"backtest_options_optimal{v}{cap_sfx}"
            for h in HORIZONS:
                summ = DATA_DIR / f"{prefix}_h{h}d_summary.json"
                if not summ.exists():
                    continue
                s = json.load(open(summ))
                daily = pd.read_csv(DATA_DIR / f"{prefix}_h{h}d.csv", parse_dates=["date"]).set_index("date")
                r = daily["daily_return"]
                eras = {f"sharpe_{k}": float(r.loc[a:b].mean() / r.loc[a:b].std(ddof=1) * np.sqrt(252))
                        for k, (a, b) in ERAS.items()}
                rows.append(dict(variant=v.lstrip("_") or "baseline", premium_cap=cap, horizon_days=h,
                                 annualized_return=s["annualized_return"], annualized_vol=s["annualized_vol"],
                                 sharpe=s["sharpe"], max_drawdown=s["max_drawdown"],
                                 n_positions_sized=s["n_positions_sized"], **eras))
    out = pd.DataFrame(rows)
    out.to_csv(DATA_DIR / "backtest_options_vol_comparison.csv", index=False)
    show = out[out["horizon_days"] == 60].copy()
    for c in ["annualized_return", "annualized_vol", "max_drawdown"]:
        show[c] = show[c].map(lambda x: f"{x:.2%}")
    print(show.round(2).to_string(index=False))
    print(f"\nwrote {DATA_DIR / 'backtest_options_vol_comparison.csv'}")


if __name__ == "__main__":
    main()
