"""Purged chronological partitions and paired block-bootstrap investment gates."""
import numpy as np
import pandas as pd


def purged_folds(events, train_years=5, validation_years=1, test_years=1):
    if min(train_years,validation_years,test_years)<1:
        raise ValueError("fold lengths must be positive")
    e=events.copy()
    e["entry_date"]=pd.to_datetime(e.entry_date)
    e["label_end"]=pd.to_datetime(e.label_end)
    if e.empty: return []
    start=e.entry_date.min().normalize()
    validation_start=start+pd.DateOffset(years=train_years)
    folds=[]
    while validation_start+pd.DateOffset(years=validation_years)<e.entry_date.max():
        test_start=validation_start+pd.DateOffset(years=validation_years)
        test_end=test_start+pd.DateOffset(years=test_years)
        tr=e.index[(e.entry_date<validation_start)&(e.label_end<validation_start)]
        va=e.index[(e.entry_date>=validation_start)&(e.entry_date<test_start)&(e.label_end<test_start)]
        te=e.index[(e.entry_date>=test_start)&(e.entry_date<test_end)]
        if len(tr) and len(va) and len(te):
            folds.append(dict(train=tr.tolist(),validation=va.tolist(),test=te.tolist(),
                              validation_start=str(validation_start.date()),test_start=str(test_start.date()),
                              test_end=str(test_end.date())))
        validation_start+=pd.DateOffset(years=test_years)
    return folds


def metrics(returns,rf=None):
    r=np.asarray(returns,dtype=float)
    if len(r)==0 or not np.isfinite(r).all() or (r<=-1).any():
        raise ValueError("finite nonempty returns greater than -1 required")
    wealth=np.r_[1.,np.cumprod(1+r)]
    excess=r if rf is None else r-np.asarray(rf)
    sd=np.std(excess,ddof=1) if len(r)>1 else 0.
    return dict(cagr=float(wealth[-1]**(252/len(r))-1),
                sharpe=float(np.mean(excess)/sd*np.sqrt(252)) if sd>1e-12 else 0.,
                max_drawdown=float(np.min(wealth/np.maximum.accumulate(wealth)-1)))


def alpha(returns,benchmark,rf):
    y=np.asarray(returns)-np.asarray(rf)
    x=np.asarray(benchmark)-np.asarray(rf)
    coef=np.linalg.lstsq(np.column_stack([np.ones(len(x)),x]),y,rcond=None)[0]
    return float(coef[0]*252)


def evaluate(daily,execution_valid=False,benchmark_total_return=False,repetitions=1000,seed=20261025,block_months=3):
    if repetitions<1 or block_months<1: raise ValueError("positive bootstrap repetitions and block months required")
    d=daily.sort_values("date").copy()
    if d.date.duplicated().any(): raise ValueError("duplicate evaluation dates")
    a=d[["ret","benchmark","rf"]].to_numpy(dtype=float)
    if not np.isfinite(a).all(): raise ValueError("nonfinite evaluation returns")
    sm=metrics(a[:,0],a[:,2]);bm=metrics(a[:,1],a[:,2])
    annual_alpha=alpha(a[:,0],a[:,1],a[:,2])
    rng=np.random.default_rng(seed)
    # Resample calendar months jointly, keeping the actual daily returns within
    # each month. Circular consecutive-month blocks preserve local dependence.
    groups=[g.index.to_numpy() for _,g in d.reset_index(drop=True).groupby(pd.to_datetime(d.date.to_numpy()).to_period("M"),sort=True)]
    n=len(groups)
    draws=[]
    for _ in range(repetitions):
        starts=rng.integers(n,size=int(np.ceil(n/block_months)))
        months=np.concatenate([(s+np.arange(block_months))%n for s in starts])[:n]
        sample=a[np.concatenate([groups[j] for j in months])]
        m=metrics(sample[:,0],sample[:,2])
        draws.append([m["cagr"],m["sharpe"],m["max_drawdown"],alpha(sample[:,0],sample[:,1],sample[:,2])])
    quant=np.quantile(draws,[.025,.5,.975],axis=0) if draws else np.full((3,4),np.nan)
    confidence={key:dict(zip(("p025","p50","p975"),map(float,quant[:,i]))) for i,key in enumerate(("cagr","sharpe","max_drawdown","alpha"))}
    gates=dict(execution_valid=bool(execution_valid),benchmark_total_return=bool(benchmark_total_return),
               minimum_three_years=len(d)>=3*252,
               cagr=sm["cagr"]>max(.12,bm["cagr"])+.02,
               sharpe=sm["sharpe"]>=bm["sharpe"],alpha=annual_alpha>0,
               alpha_confidence=bool(confidence["alpha"]["p025"]>0),
               drawdown=abs(sm["max_drawdown"])<=1.25*abs(bm["max_drawdown"]))
    return dict(strategy=sm,benchmark=bm,alpha=annual_alpha,bootstrap=confidence,
                bootstrap_alpha_nonpositive=float(np.mean(np.asarray(draws)[:,3]<=0)) if draws else None,
                gates=gates,accepted=all(gates.values()),sessions=len(d),
                method=f"paired circular {block_months}-month blocks; market alpha only, not multifactor alpha")
