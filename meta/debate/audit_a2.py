import pandas as pd, numpy as np, sys
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
sys.path.insert(0,SRC)
# do NOT import pead package (writes caches); reimplement pit_rank
def rank_pct(values, quarters, mode):
    if mode=="within": return values.groupby(quarters).rank(pct=True,method="average")
    codes,_=pd.factorize(quarters,sort=True); v=values.to_numpy(float); out=np.full(len(v),np.nan); pool=np.empty(0)
    for c in range(codes.max()+1):
        m=codes==c
        if len(pool): out[m]=(np.searchsorted(pool,v[m],"left")+np.searchsorted(pool,v[m],"right"))/2/len(pool)
        pool=np.sort(np.concatenate([pool,v[m]]))
    return pd.Series(out,index=values.index)
ev=pd.read_parquet(SRC+"/data/events/equity_events_1996_2013.parquet")
ev=ev[ev.day0_status=="ok"]
for src in ["sue_analyst","sue_primary"]:
    e=ev[ev[src].notna()].copy() if src=="sue_primary" else ev[ev.sue_source=="analyst"].copy()
    e["q"]=pd.to_datetime(e.anndats).dt.to_period("Q")
    v=e[src].round(8)
    e["w"]=rank_pct(v,e.q,"within"); e["p"]=rank_pct(v,e.q,"prior")
    # same-day-or-earlier in-quarter rank (true PIT within quarter)
    e=e.sort_values("anndats")
    def dec(x): return np.minimum(np.ceil(x*10),10)
    e=e.dropna(subset=["p"])
    dw,dp=dec(e.w),dec(e.p)
    print(src,len(e),"decile changed:",(dw!=dp).mean().round(3),"mean|dd|",(dw-dp).abs().mean().round(3),"changed by>=2",((dw-dp).abs()>=2).mean().round(3))
    ext_w=(dw.isin([1,10])); ext_p=dp.isin([1,10])
    print(" extreme membership: w only",(ext_w&~ext_p).sum(),"p only",(~ext_w&ext_p).sum(),"both",(ext_w&ext_p).sum())
    # D10-D1 spread of 60d mktadj fwd under each
    for h in ["ret_fwd_20d_mktadj","ret_fwd_60d_mktadj"]:
        for nm,d in [("within",dw),("prior",dp)]:
            g=e.groupby(d)[h].mean()
            print(" ",h,nm,"D10-D1 bps",round((g.loc[10]-g.loc[1])*1e4,1))
    # in-quarter timing: position of announcement within quarter, and decile shift vs sign
    e["doq"]=(e.anndats-e.q.dt.start_time).dt.days
    e["sh"]=dw-dp
    print(e.groupby(pd.cut(e.doq,[-1,14,28,42,60,100]))["sh"].agg(["mean",lambda s:(s!=0).mean()]).round(3))
    # in-quarter PIT: rank vs strictly earlier same-quarter + prior quarters (same-day-or-earlier pool)
