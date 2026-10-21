import pandas as pd, numpy as np, json
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
for s in ["calm_rank_tilt","calm_weighted","balanced_leverage","size_balanced","balanced_leverage_max","market_exposed"]:
    d=pd.read_csv(f"{SRC}/results/{s}/daily.csv.gz",parse_dates=["date"])
    p=pd.read_parquet(f"{SRC}/results/{s}/positions.parquet")
    sm=json.load(open(f"{SRC}/results/{s}/summary.json"))
    g=d.gross_exposure/d.nav; sh=-d.short_exposure/d.nav; lo=d.long_exposure/d.nav
    r=d.daily_return
    def sharpe(x): return x.mean()/x.std()*np.sqrt(252)
    yrs=d.date.dt.year
    print(f"\n== {s}: cap {sm['params']['max_gross_leverage']} exp_cap? {sm.get('summary',{}).get('exposure_cap')} gross/NAV mean {g.mean():.2f} p99 {g.quantile(.99):.2f} max {g.max():.2f} days>cap {(g>sm['params']['max_gross_leverage']+1e-9).mean():.2%}; short/NAV mean {sh.mean():.2f}, net/gross {(d.net_exposure/d.gross_exposure).mean():.3f}")
    print(" sharpe 96-01 %.2f 02-07 %.2f 08-13 %.2f ; last 30%% of days %.2f first 70%% %.2f" % (sharpe(r[yrs<=2001]),sharpe(r[(yrs>=2002)&(yrs<=2007)]),sharpe(r[yrs>=2008]), sharpe(r.iloc[int(len(r)*.7):]), sharpe(r.iloc[:int(len(r)*.7)])))
    print(" cols",[c for c in p.columns][:40]) if s=="calm_rank_tilt" else None
    # pnl by size quintile & by price?
    p["bps"]=p.net_pnl/p.notional.abs()*1e4
    t=p.groupby("size_quintile").agg(n=("net_pnl","size"),gross_notional=("notional",lambda x:x.abs().sum()),net_pnl=("net_pnl","sum"),bps=("bps","mean"))
    t["pnl_share"]=t.net_pnl/t.net_pnl.sum(); print(t.round(3).to_string())
    ts=p.groupby("side").agg(net_pnl=("net_pnl","sum"),gross=("gross_pnl","sum"),cost=("cost","sum")); print(ts.round(0).to_string())
    # borrow haircut: 1%/yr, 3% on size q1-2 on short notional-days
    hd=(p.exit_date-p.day0_date).dt.days/365.0
    sh_=p[p.side<0]; 
    for fee_s,fee in [("1%/yr all",lambda q:0.01),("1% big/ 5% Q1-2/ 2% else",lambda q:np.where(q<=2,0.05,np.where(q<=3,0.02,0.01)))]:
        cost=(sh_.notional.abs()*hd.loc[sh_.index]*fee(sh_.size_quintile)).sum()
        yrsn=len(d)/252
        print(f" borrow {fee_s}: ${cost/1e6:.2f}M = {cost/yrsn/ 1e6/ (d.nav.mean()/1e6)*100:.2f}% of mean NAV per yr  vs ann return {sm['summary']['annualized_return']:.2%}")
    if s=="calm_rank_tilt":
        print(p.beta_252d.describe().round(2).to_dict() if "beta_252d" in p else "no beta col")
