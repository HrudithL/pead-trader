import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
c=pd.read_csv(SRC+"/data/cell_decay_days.csv"); print(len(c)); print(c.decay_day.describe().round(1).to_dict()); print("at floor (<=31):",(c.decay_day<=31).mean().round(2),"never decayed:",(c.decay_day>=150).mean().round(2))
p=pd.read_parquet(SRC+"/data/equity_vol_panel.parquet")
p["q"]=p.ann_quarter.astype(str); qs=sorted(p.q.unique()); cut=qs[int(len(qs)*0.7)]; print("70% cut at",cut,"of",qs[0],qs[-1])
p["per"]=np.where(p.q<cut,"train70","holdout30")
ext=p[p.decile.isin([1,10])]
t=ext.groupby(["per","decile"]).agg(n=("hold_ret","size"),hold=("hold_ret","mean"),f60=("ret_fwd_60d_mktadj","mean"),hd=("holding_days","mean"))
print(t.round(4))
for per in ["train70","holdout30"]:
    g=t.loc[per]; print(per,"D10-D1 hold-window spread bps",round((g.loc[10,'hold']-g.loc[1,'hold'])*1e4),"fixed60 spread bps",round((g.loc[10,'f60']-g.loc[1,'f60'])*1e4))
# fraction of ret from days after median? skip. Per-cell consistency: for fine cells, compare spread sign
p["yr"]=p.q.str[:4].astype(int)
x=ext.assign(yr=p.yr).groupby(["yr","decile"]).hold_ret.mean().unstack(); x["spread_bps"]=(x[10]-x[1])*1e4; print(x.spread_bps.round(0).to_dict())
