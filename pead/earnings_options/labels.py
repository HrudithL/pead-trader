"""Market-session training horizons, retaining observed CRSP delisting losses."""
from pathlib import Path
import numpy as np
import pandas as pd


def forward_labels(events,stocks,calendar,delistings=None,max_horizon=20):
    """Label end is explicit. Missing returns stay missing, never become zero.

    After an observed delisting return, proceeds are held as zero-interest cash.
    A missing delisting return invalidates the label; no exchange-average return
    is invented. The calendar is the benchmark calendar, not surviving stock rows.
    """
    e=events.copy()
    cal=pd.DatetimeIndex(calendar)
    stocks=stocks.copy();stocks["date"]=pd.to_datetime(stocks.date)
    groups={k:g for k,g in stocks.groupby("permno",sort=False)}
    dl=delistings.copy() if delistings is not None else pd.DataFrame()
    if len(dl):dl["dlstdt"]=pd.to_datetime(dl.dlstdt)
    for h in range(1,max_horizon+1):
        e[f"forward_return_{h}"]=np.nan
        e[f"label_end_{h}"]=pd.NaT
    for permno,indices in e.groupby("permno",sort=False).groups.items():
        s=groups.get(permno)
        if s is None:continue
        if s.date.duplicated().any():raise ValueError("duplicate stock label dates")
        series=s.set_index("date").ret.reindex(cal).to_numpy(dtype=float,na_value=np.nan,copy=True)
        if len(dl):
            for row in dl[dl.permno==permno].itertuples():
                pos=cal.searchsorted(row.dlstdt)
                if pos>=len(cal):continue
                if pd.isna(row.dlret) or not np.isfinite(row.dlret):
                    series[pos:]=np.nan
                else:
                    base=series[pos] if np.isfinite(series[pos]) else 0.
                    series[pos]=(1+base)*(1+row.dlret)-1
                    series[pos+1:]=0.
        valid=np.isfinite(series)&(series>=-1)
        zero=valid&(series==-1)
        logs=np.zeros(len(series));mask=valid&~zero
        logs[mask]=np.log1p(series[mask])
        cumulative=np.r_[0.,np.cumsum(logs)]
        missing=np.r_[0,np.cumsum(~valid)]
        zeros=np.r_[0,np.cumsum(zero)]
        start=cal.searchsorted(pd.to_datetime(e.loc[indices,"entry_date"]))
        for h in range(1,max_horizon+1):
            end=start+h
            ok=end<len(cal)
            ix=np.asarray(indices)[ok];st=start[ok];en=end[ok]
            values=np.expm1(cumulative[en+1]-cumulative[st+1])
            values[zeros[en+1]-zeros[st+1]>0]=-1.
            values[missing[en+1]-missing[st+1]>0]=np.nan
            e.loc[ix,f"forward_return_{h}"]=values
            e.loc[ix,f"label_end_{h}"]=cal[en].to_numpy()
    e["forward_return"]=e[f"forward_return_{max_horizon}"]
    e["label_end"]=e[f"label_end_{max_horizon}"]
    return e


def refresh_labels(data_dir,cache):
    root,cache=Path(data_dir),Path(cache)
    e=pd.read_parquet(cache/"all_events.parquet")
    b=pd.read_parquet(cache/"benchmark.parquet")
    stocks=[]
    for year in range(1995,2015):
        p=root/f"raw_wrds/crsp/dsf_{year}.parquet"
        if p.exists():
            s=pd.read_parquet(p,columns=["permno","date","ret"])
            s=s[s.permno.isin(e.permno.unique())].copy()
            s["ret"]=pd.to_numeric(s.ret,errors="coerce")
            stocks.append(s)
    dl=pd.read_parquet(root/"raw_wrds/crsp/dsedelist.parquet")
    dl["dlret"]=pd.to_numeric(dl.dlret,errors="coerce")
    result=forward_labels(e,pd.concat(stocks,ignore_index=True),b.date,dl)
    result.to_parquet(cache/"all_events.parquet",index=False)
    return result
