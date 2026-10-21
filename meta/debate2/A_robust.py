"""A_robust.py: (1) event-level surprise profile with cell-matched residual, real vs within-quarter SUE shuffle placebo;
(2) robustness of R (book-level) to richer matching cells; (3) traits of best trades."""
import numpy as np, pandas as pd
from pathlib import Path
D=Path(__file__).resolve().parent
E=pd.read_parquet(D/"A_events.parquet"); z=np.load(D/"A_paths.npz"); CS,CM=z["CS"],z["CM"]
E["beta"]=E.beta_252d.astype(float).fillna(1.0).clip(-1,4)
yr=E.day0_date.dt.year.to_numpy(); E["era"]=np.where(yr<=2001,"1996-01",np.where(yr<=2007,"2002-07","2008-13"))
E["q"]=E.ann_quarter.astype(str)
def code(*cols):
    df=pd.DataFrame({c:(E[c].astype(str) if c in("q","ff12_sector") else E[c].astype(float).fillna(-1)) for c in cols}); return pd.factorize(pd.MultiIndex.from_frame(df))[0]
def loo_chain(A, chain, mn=10):
    """leave-one-out mean of A over first cell (in chain order, coarse->fine are tried fine first) with n>=mn"""
    out=np.full(len(A),np.nan)
    for c in chain[::-1]:   # finest first
        S=np.bincount(c,A,minlength=c.max()+1); N=np.bincount(c,minlength=c.max()+1)
        n=N[c]-1; v=np.where(n>=mn,(S[c]-A)/np.maximum(n,1),np.nan)
        out=np.where(np.isnan(out),v,out)
    return out
cq=code("q"); cqs=code("q","size_quintile"); cqsv=code("q","size_quintile","ivol_252d_tercile")
cqsvl=code("q","size_quintile","ivol_252d_tercile","liquidity_quintile")
cqsvb=code("q","size_quintile","ivol_252d_tercile","beta_t") if False else None
E["beta_t"]=pd.qcut(E.beta,3,labels=False).astype(float)
cqsvb=code("q","size_quintile","ivol_252d_tercile","beta_t")
cqsec=code("q","ff12_sector"); cqsecs=code("q","ff12_sector","size_quintile")
k=59; A60=(CS[:,k]-E.beta.to_numpy()*CM[:,k]).astype(float); E["A60"]=A60
E["R60"]=A60-loo_chain(A60,[cq,cqs,cqsv]); E["R60_liq"]=A60-loo_chain(A60,[cq,cqs,cqsv,cqsvl]); E["R60_beta"]=A60-loo_chain(A60,[cq,cqs,cqsv,cqsvb]); E["R60_sec"]=A60-loo_chain(A60,[cq,cqsec,cqsecs])
ev=E[E.decile.notna()].copy()
# placebo: shuffle PIT sue rank within quarter, re-derive decile by the same thresholds (rank-based deciles within quarter)
rng=np.random.default_rng(1)
def qstat(df,col,mask):   # quarter-cluster t of mean col difference d10-d1... returns mean, t
    return df
res=[]
def spread(df,col):
    out={}
    for e,g in list(df.groupby("era"))+[("ALL",df)]:
        qs=g.groupby("q").apply(lambda x: x.loc[x.dec>=9,col].mean()-x.loc[x.dec<=2,col].mean(), include_groups=False).dropna()
        d10=g.groupby("q").apply(lambda x: x.loc[x.dec==10,col].mean(), include_groups=False).dropna()
        out[e]=(qs.mean()*1e4, qs.mean()/(qs.std(ddof=1)/np.sqrt(len(qs))), d10.mean()*1e4, d10.mean()/(d10.std(ddof=1)/np.sqrt(len(d10))))
    return out
ev["dec"]=ev.decile.astype(int)
real=spread(ev,"R60")
print("REAL top(9-10) minus bottom(1-2) cell-matched residual, bp per 60d [spread, t, d10 alone bp, t]"); [print(k_,[round(x,1) for x in v]) for k_,v in real.items()]
for nm,col in (("liq-matched","R60_liq"),("beta-matched","R60_beta"),("sector-matched","R60_sec")):
    r=spread(ev,col); print(nm,{k_:round(v[0],1) for k_,v in r.items()},{k_:round(v[2],1) for k_,v in r.items()})
# placebo: shuffle dec within quarter
pl=[]
for s in range(20):
    f=ev[["q","era","R60"]].copy(); f["dec"]=ev.groupby("q")["dec"].transform(lambda x: rng.permutation(x.to_numpy()))
    r=spread(f,"R60"); pl.append({k_:v[0] for k_,v in r.items()})
pl=pd.DataFrame(pl); print("PLACEBO (shuffled SUE within quarter) mean/sd of spread bp:"); print(pl.agg(["mean","std"]).round(1))
# raw (not matched) A60: how much of top-decile and bottom-decile raw alpha is universe
for e,g in list(ev.groupby("era"))+[("ALL",ev)]:
    print(e,"mean A60 bp by decile:", (g.groupby("dec").A60.mean()*1e4).round(0).to_dict())
E[["permno","day0_date","decile","sue_rank_pct","era","A60","R60","R60_liq","R60_beta","R60_sec"]].to_parquet(D/"A_event_R60.parquet")
pd.DataFrame({k_:real[k_] for k_ in real},index=["spread_bp","t","d10_bp","d10_t"]).T.to_csv(D/"A_event_decile_spread_by_era.csv")
# decile profile table
prof=ev.groupby(["era","dec"]).agg(n=("R60","size"),A60_bp=("A60",lambda x:x.mean()*1e4),R60_bp=("R60",lambda x:x.mean()*1e4)).round(1); prof.to_csv(D/"A_event_decile_profile.csv")
