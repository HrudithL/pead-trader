import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
m=pd.read_parquet(SRC+"/data/normalized_equity/market_benchmark.parquet"); print(m.columns.tolist())
m=m.drop_duplicates("date").sort_values("date").set_index("date")
for s in ["calm_rank_tilt","calm_weighted","size_balanced"]:
    d=pd.read_csv(f"{SRC}/results/{s}/daily.csv.gz",parse_dates=["date"]).set_index("date")
    p=pd.read_parquet(f"{SRC}/results/{s}/positions.parquet")
    cal=d.index; idx={x:i for i,x in enumerate(cal)}
    e=p.day0_date.map(idx).to_numpy(); x=p.exit_date.map(idx).to_numpy()
    b=p.beta_252d.fillna(1.0).clip(-1,4).to_numpy()   # beta_252d vs vwretd
    n=len(cal)
    def book(v):
        dd=np.zeros(n+1); np.add.at(dd,e+1,v); np.add.at(dd,x+1,-v); return np.cumsum(dd[:n])
    bexp=book(p.notional.to_numpy()*(b-1.0))   # beta-1 excess exposure (engine assumed beta=1 hedge)
    vw=m.vwretd.reindex(cal).fillna(0).to_numpy()
    hidden=bexp*vw
    tot=d.daily_pnl.sum()
    print(s,"hidden mkt P&L from (beta-1) mismatch $%.2fM of total $%.2fM (%.1f%%)"%(hidden.sum()/1e6,tot/1e6,hidden.sum()/tot*100),
      "mean excess-beta exposure/NAV %.3f"%np.mean(bexp/d.nav.to_numpy()))
    # raw net exposure P&L: net_exposure*vw
    print("  net_exposure*vwretd $%.2fM (engine ignores since mktadj)"%((d.net_exposure*vw).sum()/1e6))
    X=np.c_[np.ones(n),vw]; coef=np.linalg.lstsq(X,d.daily_return.to_numpy(),rcond=None)[0]; print("  regression daily_return on vwretd: beta %.3f"%coef[1], "corr %.3f"%np.corrcoef(vw,d.daily_return)[0,1])
    # down market days
    dn=vw<-0.01; print("  avg return on days mkt<-1%%: %.4f%% vs all %.4f%%"%(d.daily_return[dn].mean()*100,d.daily_return.mean()*100))
    # financing: no interest on cash, NAV. Average rf 1996-2013 ~3%: interest on NAV omitted
    # sizing NAV feedback: Sharpe computed on NAV w/o interest
