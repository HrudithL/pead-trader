import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
p=pd.read_parquet(f"{SRC}/results/options_cap20/positions.parquet"); print(p.shape); print([c for c in p.columns])
p["y"]=pd.to_datetime(p.day0_date).dt.year
print(p.groupby("y").agg(n=("net_pnl","size"),prem=("premium_paid","sum"),pnl=("net_pnl","sum")).round(0).to_string())
d=pd.read_csv(f"{SRC}/results/options_cap20/daily.csv.gz",parse_dates=["date"]); d["y"]=d.date.dt.year
print(d.groupby("y").daily_pnl.sum().round(0).to_dict())
f=pd.read_parquet(f"{SRC}/data/event_options/optimal_forward_prices.parquet"); print(f.shape); 
f["y"]=pd.to_datetime(f.day0_date).dt.year
cols=[c for c in f.columns if "fwd" in c]; print(cols)
g=f.groupby("y").agg(n=("kelly_premium","size"),prem_ok=("kelly_premium",lambda s:s.notna().mean()),growth_pos=("kelly_growth",lambda s:(s>0).mean()),
   m60_na=("kelly_fwd_mid_60d",lambda s:s.isna().mean()),m60_zero=("kelly_fwd_mid_60d",lambda s:(s==0).mean())); print(g.round(3).to_string())
if "kelly_fwd_bid_60d" in f: print("bid60==0 share",(f.kelly_fwd_bid_60d==0).mean())
# spread at entry if available
for c in ["kelly_bid","kelly_ask","kelly_premium","kelly_spread_pct"]:
    print(c, c in f.columns)
print(f.kelly_premium.describe())
# dead quotes: mid60 NaN share among active
a=f[f.kelly_growth>0]; print("active",len(a),"mid60 NaN",a.kelly_fwd_mid_60d.isna().mean().round(3),"mid60<=0.05", (a.kelly_fwd_mid_60d<=0.05).mean().round(3))
print(a.groupby("kelly_side").size())
