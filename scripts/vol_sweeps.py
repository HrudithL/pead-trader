"""Re-run of the docs/STRATEGIES.md section 3 parameter sweeps on the CORRECTED data and engine (open-book cap, point-in-time
deciles, search-window decay days). Each variant is an in-memory subclass of calm_weighted / calm_rank_tilt; nothing is registered
or saved except results/vol_sweeps.csv.   python scripts/vol_sweeps.py
Run with PEAD_DATA_DIR / PEAD_RAW_EQUITY_DIR pointing at the corrected data vintage (see strategy_profiles.py).
Self-check: the unmodified baselines must reproduce results/calm_weighted and results/calm_rank_tilt summary.json."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import RunContext
from pead.strategies.equity.calm_weighted import CalmWeighted
from pead.strategies.equity.calm_rank_tilt import CalmRankTilt
from pead.strategies.vol import inverse_ivol_scale, low_vol_rank_scale

ctx = RunContext()
events = ctx.equity().events

def run(base, **over):
    """over: TRIM/TILT/POWER overrides, `scale_fn(events)` extra multiplier, `K` (1/ivol exponent), sizing."""
    sizing = over.pop("sizing", base.sizing)
    scale_fn = over.pop("scale_fn", None); K = over.pop("K", None)
    attrs = dict(over)
    class V(base):
        name = "variant"
        def weights(self, ev):
            s = inverse_ivol_scale(ev) if base is CalmWeighted else low_vol_rank_scale(ev, self.POWER)
            if K is not None: s = s ** K
            if scale_fn is not None: s = s * scale_fn(ev)
            return W.neutralized_tilt(ev, W.centered_rank(ev), self.TRIM, self.TILT, scale=s)
    V.sizing = sizing
    for k, v in attrs.items(): setattr(V, k, v)
    r = V().run(ctx).summary
    return dict(ret=r["annualized_return"], vol=r["annualized_vol"], sharpe=r["sharpe"], max_dd=r["max_drawdown"],
                s_96_01=r["sharpe_1996_2001"], s_02_07=r["sharpe_2002_2007"], s_08_13=r["sharpe_2008_2013"],
                pct_liq_capped=r["pct_liquidity_capped"])

rows = []
def add(group, setting, base, **kw):
    out = run(base, **kw); rows.append(dict(group=group, setting=setting, **out)); print(group, setting, round(out["sharpe"], 3), flush=True)

# self-check against the saved runs
for nm, base in (("calm_weighted", CalmWeighted), ("calm_rank_tilt", CalmRankTilt)):
    saved = json.load(open(ROOT / "results" / nm / "summary.json"))["summary"]
    mine = run(base)
    assert abs(mine["sharpe"] - saved["sharpe"]) < 1e-9 and abs(mine["ret"] - saved["annualized_return"]) < 1e-9, nm
    rows.append(dict(group="baseline (matches saved run)", setting=nm, **mine))

for t in (0.05, 0.10, 0.20, 0.30): add("trim (calm_weighted, default 0.15)", t, CalmWeighted, TRIM=t)
for t in (0.5, 1.5, 2.0, 3.0): add("tilt exponent (calm_weighted, default 1.0)", t, CalmWeighted, TILT=t)
dec = events["decile"].to_numpy()
for lab, drop in (("D7", [7]), ("D9", [9]), ("D7+D9", [7, 9])):
    add("drop deciles (calm_weighted)", lab, CalmWeighted, scale_fn=lambda ev, d=drop: (~ev["decile"].isin(d)).to_numpy().astype(float))
for days in (40, 60):
    add("drop short holds (calm_weighted)", f"hold <= {days}d", CalmWeighted, scale_fn=lambda ev, d=days: (ev["holding_days"] > d).to_numpy().astype(float))
for k in (1, 1.5, 2, 3, 4): add("1/ivol^k (calm_weighted)", k, CalmWeighted, K=k)
for k in (1, 2, 3): add("(1.02 - vol pct)^k (calm_rank_tilt)", k, CalmRankTilt, POWER=float(k))
for base_unit, cap in ((0.003, 2.5), (0.004, 2.5), (0.004, 4.0), (0.005, 4.0), (0.005, 6.0), (0.006, 6.0)):
    add("sizing (calm_rank_tilt)", f"base unit {base_unit}, gross cap {cap}x", CalmRankTilt, sizing=Sizing(base_unit_fraction=base_unit, max_gross_leverage=cap))

out = pd.DataFrame(rows); out.to_csv(ROOT / "results" / "vol_sweeps.csv", index=False)
