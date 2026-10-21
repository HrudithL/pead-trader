import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
ev=pd.read_parquet(SRC+"/data/events/equity_events_1996_2013.parquet")
ev=ev[(ev.day0_status=="ok")&(ev.sue_source=="analyst")].copy()
ev["anndats"]=pd.to_datetime(ev.anndats)
print("anntims_unknown share",ev.anntims_unknown.mean().round(3))
t=pd.to_timedelta(ev.anntims.astype(str),errors="coerce")
ev["hr"]=t.dt.total_seconds()/3600
ev["bucket"]=pd.cut(ev.hr,[-0.01,0.001,9.5,16,24],labels=["00:00(unk)","<9:30","9:30-16","16+"])
print(pd.crosstab(ev.bucket,ev.day0_lag_days.astype("float"),margins=True))
ev["wk"]=ev.anndats.dt.dayofweek
print(pd.crosstab(ev.wk,ev.day0_lag_days.astype("float")))
ev["yr"]=ev.anndats.dt.year
print(ev.groupby("yr").anntims_unknown.mean().round(2).to_string())
# is the day0 return known in events? ret_fwd_1d should be day0+1 return. Compare mean |ret_fwd_1d| by lag
print(ev.groupby("day0_lag_days").ret_fwd_1d_mktadj.apply(lambda s:s.abs().mean()).round(4))
# lag1 events with timestamp before 9:30 (should be lag 0)?
print("pre-open stamped but lag1:",((ev.hr>0.001)&(ev.hr<9.5)&(ev.day0_lag_days==1)).sum())
print("after-close stamped but lag0:",((ev.hr>=16)&(ev.day0_lag_days==0)).sum())
