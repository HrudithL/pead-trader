import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
f=pd.read_parquet(SRC+"/data/results/event_classification_features.parquet").dropna(subset=["size_quintile","day0_date"])
f["q"]=f.day0_date.dt.to_period("Q"); f["y"]=f.day0_date.dt.year
# within-quarter or pooled?
print("size quintile share by year (pooled would drift):")
print(pd.crosstab(f.y,f.size_quintile,normalize="index").round(2).iloc[::3])
print("by-quarter std of Q1 share:",pd.crosstab(f.q,f.size_quintile,normalize="index")[1.0].std().round(4))
print(f.groupby(["y"]).market_cap.median().iloc[::4]/1e3)
# ADV window check
fr=[]
for y in range(2004,2007): fr.append(pd.read_parquet(f"{SRC}/data/normalized_equity/equity_{y}.parquet",columns=["permno","date","prc","vol","ret"]))
eq=pd.concat(fr).sort_values(["permno","date"]); eq["dv"]=eq.prc.abs()*eq.vol
eq["adv_incl"]=eq.groupby("permno").dv.transform(lambda s:s.rolling(21,min_periods=10).mean())
eq["adv_excl"]=eq.groupby("permno").adv_incl.shift(1)
m=f[(f.y==2005)].merge(eq[["permno","date","adv_incl","adv_excl","prc"]],left_on=["permno","day0_date"],right_on=["permno","date"])
m=m[m.avg_dollar_volume_21d>0]
for c in ["adv_incl","adv_excl"]:
    r=(m[c]/m.avg_dollar_volume_21d); print(c,"median ratio",r.median().round(3),"frac within 1%",((r-1).abs()<0.01).mean().round(3))
print("feature adv ($, units?) median",m.avg_dollar_volume_21d.median(),"calc median",m.adv_excl.median())
# prices / penny share among events 2005
print("price<5 share",(m.prc.abs()<5).mean().round(3),"<1:",(m.prc.abs()<1).mean().round(3))
# market cap units: market_cap vs prc*shrout
