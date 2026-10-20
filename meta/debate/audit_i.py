import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
f=pd.read_parquet(SRC+"/data/results/event_classification_features.parquet")[["permno","day0_date","market_cap"]].drop_duplicates(["permno","day0_date"])
f["day0_date"]=pd.to_datetime(f.day0_date); f["yr"]=f.day0_date.dt.year
for s in ["calm_rank_tilt","calm_weighted"]:
    p=pd.read_parquet(f"{SRC}/results/{s}/positions.parquet").merge(f,on=["permno","day0_date"],how="left")
    p["capq"]=p.day0_date.dt.to_period("Q")
    # S&P500-proxy: top 500 events by market cap per quarter among ALL events (not only funded)
    allf=pd.read_parquet(SRC+"/data/results/event_classification_features.parquet")[["permno","day0_date","market_cap"]].dropna()
    allf["day0_date"]=pd.to_datetime(allf.day0_date); allf["q"]=allf.day0_date.dt.to_period("Q")
    thr=allf.groupby("q").market_cap.apply(lambda x:x.nlargest(450).min()).rename("thr")  # ~500 S&P names report in a quarter; 450 of them with data
    p=p.join(thr,on="capq"); sp=p.market_cap>=p.thr
    for nm,m in [("top-450/qtr (S&P proxy)",sp),("rest",~sp)]:
        q=p[m]; print(s,nm,"share of positions %.2f of notional %.2f"%(m.mean(),q.notional.abs().sum()/p.notional.abs().sum()),
           "net bps/$ %.0f"%(q.net_pnl.sum()/q.notional.abs().sum()*1e4),"pnl share %.2f"%(q.net_pnl.sum()/p.net_pnl.sum()),
           "long bps %.0f short bps %.0f"%(q[q.side>0].net_pnl.sum()/q[q.side>0].notional.abs().sum()*1e4,q[q.side<0].net_pnl.sum()/q[q.side<0].notional.abs().sum()*1e4),
           "n/yr %.0f"%(len(q)/18))
        q["y"]=q.day0_date.dt.year
        a=q[q.y<=2008].net_pnl.sum()/q[q.y<=2008].notional.abs().sum()*1e4; b=q[q.y>2008].net_pnl.sum()/q[q.y>2008].notional.abs().sum()*1e4
        print("    bps/$ <=2008: %.0f ; 2009+: %.0f"%(a,b))
