import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
s="calm_rank_tilt"
p=pd.read_parquet(f"{SRC}/results/{s}/positions.parquet")
fr=[pd.read_parquet(f"{SRC}/data/normalized_equity/equity_{y}.parquet",columns=["permno","date","prc"]) for y in range(1995,2015)]
e=pd.concat(fr); e["prc"]=e.prc.abs()
m=p.merge(e,left_on=["permno","day0_date"],right_on=["permno","date"],how="left")
m["pb"]=pd.cut(m.prc,[0,1,5,10,20,1e9],labels=["<1","1-5","5-10","10-20",">20"])
t=m.groupby("pb",observed=True).agg(n=("net_pnl","size"),notional=("notional",lambda x:x.abs().sum()),net_pnl=("net_pnl","sum"))
t["notional_share"]=t.notional/t.notional.sum(); t["pnl_share"]=t.net_pnl/t.net_pnl.sum(); print(t.round(3))
sh=m[m.side<0].groupby("pb",observed=True).agg(n=("net_pnl","size"),short_notional=("notional",lambda x:x.abs().sum()),pnl=("net_pnl","sum")); print("short leg by price\n",sh.round(0))
# market cap / ADV of positions: median ADV and 5% cap in $
print("median ADV $M q1:",(m[m.size_quintile==1].avg_dollar_volume_21d.median()/1e6).round(2),"; median |notional| $",m.notional.abs().median().round(0))
# capacity scaling
adv=m.avg_dollar_volume_21d.fillna(np.inf).to_numpy(); n=m.notional.abs().to_numpy(); pnl=m.gross_pnl.to_numpy(); scale_ret=m.gross_pnl/m.notional.abs()
for k in [1,3,10,30]:
    capn=np.minimum(n*k,0.05*adv); 
    print(f"scale x{k}: avg capital deployed vs linear {capn.sum()/(n.sum()*k):.2f}; share positions capped {(n*k>0.05*adv).mean():.2f}")
# costs sensitivity
cost=p.cost.sum(); gp=p.gross_pnl.sum(); print("gross $%.1fM cost $%.1fM net $%.1fM; cost x2,x4 net:"%(gp/1e6,cost/1e6,(gp-cost)/1e6),[(round((gp-c*cost)/1e6,1)) for c in (2,4)])
print("cost bps by liq quintile (one-way applied twice):",{1:50,2:30,3:20,4:14,5:10})
print("avg holding days",p.holding_days.mean().round(1),"cost bps per $ avg", (p.cost.sum()/p.notional.abs().sum()*1e4).round(1))
# Return per $ by liquidity quintile
print(p.assign(b=p.net_pnl/p.notional.abs()*1e4).groupby("liquidity_quintile").b.mean().round(0).to_dict())
# fraction of PnL from short 'micro' etc.
