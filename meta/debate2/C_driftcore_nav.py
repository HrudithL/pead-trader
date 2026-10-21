"""Drift Core (top PIT decile, 60d, inverse-variance weights, <=2% NAV/name, <=5% ADV/name, <=80% invested) at NAV tiers, realistic costs.
-> C_driftcore_nav.csv   (alpha = events' 60d return minus market over the same 60 sessions, minus costs, on NAV; index fill earns the index)"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from C_common import *
ev=pd.read_parquet("C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-evaluation-roi-74c7dd/meta/out/events_pit.parquet")
t=pd.read_pickle(OUT+"_C_ev_tmp.pkl")[["permno","day0_date","xr","raw60","univ_top500","univ_top1000"]]
ev=ev.merge(t,on=["permno","day0_date"],how="left")
ev=ev[(ev.pit_sue_pct>=0.9)&ev.xr.notna()&ev.ivol_252d.notna()&ev.avg_dollar_volume_21d.notna()].copy()
ev["yr"]=ev.day0_date.dt.year; ev["adv"]=ev.avg_dollar_volume_21d.astype(float); ev["liquidity_quintile"]=ev.liquidity_quintile.astype(float)
print("top-decile events",len(ev),"per yr",len(ev)/16.5)
def run(df,N,sp,Y,max_name=0.02,invest=0.8):
    out=[]
    for y,g in df.groupby("yr"):
        cap=np.minimum(max_name*N,0.05*g.adv.values); v=1/g.ivol_252d.values**2
        f=lambda lam: (np.minimum(cap,lam*v)*60/252).sum()
        if f(1e30)<=invest*N: p=cap
        else:
            lo,hi=0,1e30
            for _ in range(200):
                mid=(lo*hi)**0.5 if lo>0 else hi/2
                if lo==0: mid=hi/1e6 if hi>1e20 else hi/2
                lo,hi=(mid,hi) if f(mid)<invest*N else (lo,mid)
                if hi/ max(lo,1e-30) <1.0001 and lo>0: break
            p=np.minimum(cap,hi*v)
        pos=pd.DataFrame(dict(liquidity_quintile=g.liquidity_quintile.values,day0_date=g.day0_date.values,tvol_60d=g.ivol_252d.values,notional=p,avg_dollar_volume_21d=g.adv.values))
        c=2*real_cost_bps(pos,sp,Y)/1e4
        pnl=(p*(g.xr.values-c)).sum(); gross=(p*g.xr.values).sum()
        out.append(dict(yr=y,alpha_net=pnl/N,alpha_gross=gross/N,invested=(p*60/252).sum()/N,n=len(g),mean_pos=p.mean(),med_pos=np.median(p),at5=(p>=0.0499*g.adv.values).mean(),
                        cost_bp=float((c*p).sum()/p.sum()*1e4),tiny=(p<250).mean()))
    o=pd.DataFrame(out); return o
rows=[]
for u,df in [("all CRSP-like",ev),("top-1000 by cap",ev[ev.univ_top1000]),("top-500 by cap",ev[ev.univ_top500])]:
    for N in [1e5,1e6,1e7,1e8]:
        for sc,(sp,Y) in {"base":(1,0.5),"harsh":(1.5,1.0)}.items():
            o=run(df,N,sp,Y)
            for per,m in {"1996-2013":o.yr>=1997,"1997-2003":(o.yr>=1997)&(o.yr<=2003),"2004-08":(o.yr>=2004)&(o.yr<=2008),"2009-13":o.yr>=2009}.items():
                x=o[m]; rows.append(dict(universe=u,nav=N,costs=sc,period=per,alpha_net_pct_nav=x.alpha_net.mean()*100,alpha_gross_pct_nav=x.alpha_gross.mean()*100,t_years=x.alpha_net.mean()/x.alpha_net.std()*np.sqrt(len(x)) if len(x)>2 else np.nan,
                   invested_pct=x.invested.mean()*100,events_per_yr=x.n.mean(),mean_pos_usd=x.mean_pos.mean(),median_pos_usd=x.med_pos.mean(),pct_at_5pctADV=x.at5.mean()*100,rt_cost_bp=x.cost_bp.mean(),pct_pos_under_250usd=x.tiny.mean()*100))
R=pd.DataFrame(rows).round(3); R.to_csv(OUT+"C_driftcore_nav.csv",index=False)
pd.options.display.width=250; pd.options.display.max_columns=30
print(R[(R.costs=="base")&(R.period.isin(["1996-2013","2009-13"]))].to_string())
print(R[(R.costs=="harsh")&(R.period=="1996-2013")][["universe","nav","alpha_net_pct_nav","alpha_gross_pct_nav"]].to_string())
