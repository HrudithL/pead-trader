"""A_sides.py: per-side component tables, event-level decile R profile, robustness of R to richer matching."""
import numpy as np, pandas as pd
from pathlib import Path
D=Path(__file__).resolve().parent
P=pd.read_pickle(D/"A_positions_attr.pkl"); T=pd.read_csv(D/"A_decomp_by_book_era.csv")
import json
V=Path("C:/Users/hrudi/Documents/BEI/_pead_consolidation/vintage_fixed/runs")
pd.set_option("display.width",250); pd.set_option("display.max_columns",30)
rows=[]
for b in ["balanced_leverage","calm_weighted","calm_rank_tilt","conviction_tilt","sector_balanced","extreme_picks","graded_bets","size_balanced","market_boost","market_exposed"]:
    p=P[b]; d=pd.read_csv(V/b/"daily.csv.gz",parse_dates=["date"]); d=d[(d.date.dt.year>=1996)&(d.date.dt.year<=2013)]
    for e in ["ALL","1996-01","2002-07","2008-13"]:
        g=p if e=="ALL" else p[p.era==e]
        yrs=18.0 if e=="ALL" else 6.0
        dd=d if e=="ALL" else d[(d.date.dt.year//1>=int(e[:4]))&(d.date.dt.year<=int("20"+e[-2:]))]
        navm=dd.nav.mean()
        for sd,nm in ((1,"LONG"),(-1,"SHORT")):
            h=g[g.side==sd]
            rows.append(dict(book=b,era=e,leg=nm,net=h.net_pnl.sum()/navm/yrs*100,cost=-h.cost.sum()/navm/yrs*100,beta=h.pnl_beta.sum()/navm/yrs*100,U=h.pnl_U.sum()/navm/yrs*100,S=h.pnl_S.sum()/navm/yrs*100,V=h.pnl_V.sum()/navm/yrs*100,R=h.pnl_R.sum()/navm/yrs*100,gross_notional_share=h.notional.abs().sum()/g.notional.abs().sum()))
S=pd.DataFrame(rows); S.to_csv(D/"A_side_components.csv",index=False)
print(S[S.book.isin(["balanced_leverage","calm_rank_tilt"])].round(2).to_string())
# decile profile (all trades, ALL books pooled by decile and side): mean signed R per trade-dollar
for b in ["balanced_leverage"]:
    p=P[b]; p=p.assign(w=p.notional.abs())
    g=p.groupby(["decile"]).apply(lambda x: pd.Series(dict(n=len(x),pnl_net_M=x.net_pnl.sum()/1e6,cost_M=x.cost.sum()/1e6,R_M=x.pnl_R.sum()/1e6,UVSB_M=(x.pnl_U+x.pnl_S+x.pnl_V+x.pnl_beta).sum()/1e6,R_bp_per_dollar=1e4*x.pnl_R.sum()/x.w.sum(),net_bp=1e4*x.net_pnl.sum()/x.w.sum())),include_groups=False)
    print(b);print(g.round(2))
