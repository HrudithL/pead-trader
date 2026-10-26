"""Fixed-rule capital and commission stress; never selects a winning setting.

Run: python -m pead.earnings_options.diagnostics
"""
from dataclasses import replace
import json
from pathlib import Path
import pandas as pd
from .cli import DEFAULT_CACHE,ROOT,load_prepared
from .engine import Config,run
from .validation import metrics


def main():
    e,q,b,_=load_prepared(DEFAULT_CACHE,1999,2010)
    base=Config(strict=False)
    rows=[]
    for label,cfg in (("base_1m",base),("capital_100k",replace(base,initial_nav=100_000)),
                      ("capital_10m",replace(base,initial_nav=10_000_000)),
                      ("double_commission",replace(base,commission=1.30))):
        r=run(e,q,b,cfg)
        row=dict(scenario=label,initial_nav=cfg.initial_nav,commission=cfg.commission,
                 trade_count=len(r.trades),**metrics(r.daily.ret))
        row["mean_reserved_nav"]=float((r.daily.reserved/r.daily.nav).mean())
        row["max_reserved_nav"]=float((r.daily.reserved/r.daily.nav).max())
        row["win_fraction"]=float((r.trades.pnl>0).mean()) if len(r.trades) else None
        row["net_pnl"]=float(r.trades.pnl.sum()) if len(r.trades) else 0.
        rows.append(row)
        print(json.dumps(row),flush=True)
    out=ROOT/"results/selective_earnings_options"
    pd.DataFrame(rows).to_csv(out/"capital_cost_stress.csv",index=False)
    (out/"capital_cost_stress.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")


if __name__=="__main__":main()
