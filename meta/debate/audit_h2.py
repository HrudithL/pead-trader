import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
p=pd.read_parquet(f"{SRC}/results/options_cap20/positions.parquet")
p["y"]=pd.to_datetime(p.day0_date).dt.year
na=p.kelly_fwd_mid_60d.isna(); dead=na&(p.kelly_fwd_bid_60d==0)&(p.kelly_fwd_ask_60d>0)
nodata=na&~dead
print("funded",len(p),"final mid NaN",na.mean().round(3),"of which dead (bid=0)",dead.mean().round(3),"no quote at all (ffilled stale)",nodata.mean().round(3))
for nm,m in [("live final mid",~na),("dead",dead),("no-quote ffilled",nodata)]:
    print(nm,"n",m.sum(),"premium $M",(p.premium_paid[m].sum()/1e6).round(1),"net pnl $M",(p.net_pnl[m].sum()/1e6).round(2),"ret/prem",(p.net_pnl[m].sum()/p.premium_paid[m].sum()).round(3))
print("no-quote by year",nodata.groupby(p.y).mean().round(2).to_dict())
# pnl if no-quote positions were marked to 0 at exit (worst) vs as carried
alt=p.net_pnl.copy(); 
# carried exit price = last finite mid among checkpoints
cols=[f"kelly_fwd_mid_{h}d" for h in (1,5,10,20,40,60)]
last=p[cols].ffill(axis=1).iloc[:,-1]
loss=(p.contracts*100*last)[nodata]
print("pnl if no-quote exits were worth 0: total $M",((p.net_pnl.sum()-loss.sum())/1e6).round(2),"vs reported",(p.net_pnl.sum()/1e6).round(2))
print("1.5 half: ",((p.net_pnl.sum()-0.5*loss.sum())/1e6).round(2))
print("2013 share of total pnl",(p.net_pnl[p.y==2013].sum()/p.net_pnl.sum()).round(2), "top3 yrs",p.groupby('y').net_pnl.sum().nlargest(3).round(0).to_dict())
# entry premium vs spread: entry ask/bid for sized positions? only fwd at 1d.. ask0 not in this file -> mid fills
print("ask0 cols present:", [c for c in p.columns if c.endswith('_0d')])
# relative bid-ask spread at 1d checkpoint
sp=(p.kelly_fwd_ask_1d-p.kelly_fwd_bid_1d)/((p.kelly_fwd_ask_1d+p.kelly_fwd_bid_1d)/2)
print("rel spread at +1d: median %.3f mean %.3f; premium-weighted %.3f"%(sp.median(),sp.mean(),np.average(sp.fillna(sp.median()),weights=p.premium_paid)))
print("sided: puts share",(p.side<0).mean().round(3))
