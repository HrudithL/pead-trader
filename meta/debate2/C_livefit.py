"""Perspective C part 5 + Drift Core repricing / NAV tiers.   -> C_livefit_signal.csv, C_driftcore_nav.csv
Reads meta/out/events_pit.parquet, event_paths.parquet, market.parquet (read-only) and V/data/decile_events.parquet."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from C_common import *
M="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-evaluation-roi-74c7dd/meta/out/"
ev=pd.read_parquet(M+"events_pit.parquet"); pa=pd.read_parquet(M+"event_paths.parquet"); mk=pd.read_parquet(M+"market.parquet")
ev["ev"]=np.arange(len(ev))
pa["l"]=np.log1p(pa.ret.clip(lower=-0.999)); g=pa.groupby("ev")
ev["raw60"]=np.expm1(g.l.sum().reindex(ev.ev).values)
cl=np.r_[0,np.cumsum(np.log1p(mk.vwretd.values))]            # cl[i] = sum of first i market returns; cal index i -> row i
c0=g.cal.min().reindex(ev.ev).values.astype(int); c1=g.cal.max().reindex(ev.ev).values.astype(int)
ev["mkt60"]=np.expm1(cl[c1+1]-cl[c0]); ev["xr"]=ev.raw60-ev.mkt60
de=pd.read_parquet(V+"data/decile_events.parquet",columns=["permno","day0_date","market_cap","sue_analyst","sue_seasonal","sue_rank_pct","ann_quarter"]).drop_duplicates(["permno","day0_date"])
ev=ev.merge(de.rename(columns={"ann_quarter":"aq2"}),on=["permno","day0_date"],how="left")
ev["yr"]=ev.day0_date.dt.year; ev["qtr"]=ev.day0_date.dt.to_period("Q")
ev["cap_rank_q"]=ev.groupby("qtr").market_cap.rank(ascending=False,method="first")
ev["univ_top500"]=ev.cap_rank_q<=500*ev.groupby("qtr").market_cap.transform("count")/ev.groupby("qtr").market_cap.transform("count")  # placeholder, replaced below
ev["univ_top500"]=ev.cap_rank_q<=500; ev["univ_top1000"]=ev.cap_rank_q<=1000
print("events/qtr",ev.groupby("qtr").size().describe()[["mean","min","max"]].values, "top500 share of events",ev.univ_top500.mean())
# ---- live-style signal emulation: sue_analyst / std(own prior 8 sue_analyst), ranked within a rolling 7-calendar-day cohort of the universe
ev=ev.sort_values(["permno","day0_date"])
s=ev.groupby("permno").sue_analyst
prior_std=s.transform(lambda x:x.shift(1).rolling(8,min_periods=3).std())
floor=prior_std.quantile(0.10)
ev["live_z"]=ev.sue_analyst/np.maximum(prior_std.fillna(prior_std.median()),floor)
ev=ev.sort_values("day0_date").reset_index(drop=True)
def rolling_rank(df,col):
    out=np.full(len(df),np.nan); d=df.day0_date.values; v=df[col].values
    days=np.unique(d)
    for day in days:
        m=(d>=day-np.timedelta64(7,'D'))&(d<=day)            # window ends today (causal)
        idx=np.where(d==day)[0]
        lo=np.searchsorted(d,day-np.timedelta64(7,'D'),'left'); hi=np.searchsorted(d,day,'right')
        pool=np.sort(v[lo:hi][~np.isnan(v[lo:hi])])
        if len(pool)<8: continue
        out[idx]=(np.searchsorted(pool,v[idx],'left')+np.searchsorted(pool,v[idx],'right'))/2/len(pool)
    return out
liveR={}
for u in ["all","top1000","top500"]:
    sub=ev if u=="all" else ev[ev["univ_"+u]]
    liveR[u]=pd.Series(rolling_rank(sub.reset_index(),"live_z"),index=sub.index)
# ---- research PIT rank recomputed WITHIN the universe (trailing 365d, earlier events only) for fair comparison
def pit_rank(sub,col):
    d=sub.day0_date.values; v=sub[col].values; out=np.full(len(sub),np.nan)
    for day in np.unique(d):
        lo=np.searchsorted(d,day-np.timedelta64(365,'D'),'left'); hi=np.searchsorted(d,day,'left'); m0,m1=hi,np.searchsorted(d,day,'right')
        if hi-lo<(500 if len(sub)>100000 else 150): continue
        pool=np.sort(v[lo:hi]); x=v[m0:m1]; out[m0:m1]=(np.searchsorted(pool,x,'left')+np.searchsorted(pool,x,'right'))/2/len(pool)
    return out
def qstats(df,mask,label,u,sig):
    g=df[mask].copy(); g["net_xr"]=g.xr-g.cost2
    gg=g.groupby("qtr").agg(x=("xr","mean"),nx=("net_xr","mean"),n=("xr","size"))
    return dict(universe=u,signal=sig,leg=label,events=len(g),per_year=len(g)/17.5,mean_xr_60d=g.xr.mean(),mean_net_xr_60d=g.net_xr.mean(),
        t_gross=gg.x.mean()/gg.x.std()*np.sqrt(len(gg)),t_net=gg.nx.mean()/gg.nx.std()*np.sqrt(len(gg)),
        xr_2009_13=g[g.yr>=2009].xr.mean(),net_2009_13=g[g.yr>=2009].net_xr.mean())
# realistic per-event round-trip cost at 1% participation, same model as the rest (fraction)
tmp=pd.DataFrame(dict(liquidity_quintile=ev.liquidity_quintile.astype(float),day0_date=ev.day0_date,tvol_60d=ev.ivol_252d.astype(float),
     notional=0.01*ev.avg_dollar_volume_21d.astype(float),avg_dollar_volume_21d=ev.avg_dollar_volume_21d.astype(float)))
ev["cost2"]=2*real_cost_bps(tmp,1.0,0.5)/1e4
res=[]
for u in ["all","top1000","top500"]:
    sub=ev if u=="all" else ev[ev["univ_"+u]]
    sub=sub.sort_values("day0_date")
    for sig,col in [("research PIT sue_analyst (in-universe pool)","sue_analyst"),("live-style self-standardized z, 7d cohort",None)]:
        if col:
            r=pd.Series(pit_rank(sub.reset_index(drop=True),col),index=sub.index)
        else:
            r=liveR[u].reindex(sub.index) if False else pd.Series(rolling_rank(sub.reset_index(drop=True),"live_z"),index=sub.index)
        d=sub.assign(rk=r)
        d=d[d.rk.notna()]
        res.append(qstats(d,d.rk>=0.9,"top decile long",u,sig)); res.append(qstats(d,d.rk<=0.1,"bottom decile (short)",u,sig))
        # also rank correlation with the research signal
        if not col: res[-1]["spearman_with_research"]=d[["live_z","sue_analyst"]].corr(method="spearman").iloc[0,1]
R=pd.DataFrame(res); R.to_csv(OUT+"C_livefit_signal.csv",index=False)
pd.options.display.width=250; pd.options.display.max_columns=30; print(R.round(4).to_string())
ev[["permno","day0_date","xr","raw60","mkt60","cost2","univ_top500","univ_top1000"]].to_pickle(OUT+"_C_ev_tmp.pkl")
