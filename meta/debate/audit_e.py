import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
ev=pd.read_parquet(SRC+"/data/events/equity_events_1996_2013.parquet")
ev=ev[(ev.day0_status=="ok")&(ev.sue_source=="analyst")].copy()
p=pd.read_parquet(SRC+"/data/equity_vol_panel.parquet")
print("analyst ok events",len(ev),"positions",len(p), "ratio",len(p)/len(ev))
ev["day0_date"]=pd.to_datetime(ev.day0_date)
k=ev.merge(p[["permno","day0_date","holding_days","decile"]].drop_duplicates(["permno","day0_date"]),on=["permno","day0_date"],how="left",indicator=True)
k["in"]=k._merge=="both"
print(k.groupby("in").n_fwd_sessions.describe().round(1))
print("n_fwd_sessions<60 share: in",(k[k["in"]].n_fwd_sessions<60).mean().round(4),"out",(k[~k["in"]].n_fwd_sessions<60).mean().round(4))
print("ret_fwd_60 missing: in",k[k["in"]].ret_fwd_60d.isna().mean().round(3),"out",k[~k["in"]].ret_fwd_60d.isna().mean().round(3))
# holding days vs n_fwd_sessions among kept
kk=k[k["in"]]
print("holding_days dist",kk.holding_days.describe().round(1).to_dict())
print("kept with n_fwd<holding:",(kk.n_fwd_sessions<kk.holding_days).sum())
# panel ret tail: how many daily returns > +/-50% (no delisting return); minimum price
fr=[pd.read_parquet(f"{SRC}/data/normalized_equity/equity_{y}.parquet",columns=["permno","date","prc","ret"]) for y in (2001,2002,2008,2009)]
e=pd.concat(fr)
print("ret NaN share",e.ret.isna().mean().round(4),"prc<1 share",(e.prc.abs()<1).mean().round(4))
# decile of dropped events: long (D10) vs short (D1) count
print(p.decile.value_counts().sort_index().to_dict())
# fraction of held (kept) positions with price<5 / <1 at day0 for D1 shorts
