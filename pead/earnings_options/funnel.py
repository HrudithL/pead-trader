"""Unique-event diagnostics for raw earnings option chains."""
import numpy as np
import pandas as pd


def diagnose(events,quotes):
    counts=[]
    def record(name,frame):
        counts.append(dict(stage=name,events=frame[["secid","date"]].drop_duplicates().shape[0],contracts=len(frame)))
    q=quotes.copy()
    record("raw_chain",q)
    q=q[q.standard_contract]
    record("standard_settlement",q)
    good=np.isfinite(q[["bid","ask","delta"]].to_numpy(dtype=float)).all(axis=1)
    q=q[good&(q.bid>=0)&(q.ask>=q.bid)&(q.ask>0)].copy()
    record("valid_quote",q)
    q["dte"]=(q.expiry-q.date).dt.days
    q=q[q.dte.between(30,60)]
    record("30_60_dte",q)
    mid=(q.bid+q.ask)/2
    q=q[mid>=.5]
    record("premium_50c",q)
    q=q[(q.ask-q.bid)/((q.ask+q.bid)/2)<=.10]
    record("spread_10pct",q)
    q=q[q.prior_oi>=250]
    record("prior_oi_250",q)
    q=q[q.prior_volume>=50]
    record("prior_volume_50",q)
    from .structures import select_structure
    pairs=[]
    for (secid,date),chain in q.groupby(["secid","date"]):
        for direction in (1,-1):
            s=select_structure(chain,"directional",direction)
            if s:pairs.append(dict(secid=secid,date=date,direction=direction,expiry=s.expiry))
    pairs=pd.DataFrame(pairs,columns=["secid","date","direction","expiry"])
    counts.append(dict(stage="two_leg_delta_pair",events=pairs[["secid","date"]].drop_duplicates().shape[0],contracts=len(pairs)))
    return pd.DataFrame(counts),pairs


def main():
    import argparse,json
    from pathlib import Path
    from .data import scan
    p=argparse.ArgumentParser();p.add_argument("--year",type=int,default=2007)
    args=p.parse_args()
    root=Path(__file__).resolve().parents[2]
    cache=root/"data/selective_earnings_v2";cache.mkdir(parents=True,exist_ok=True)
    old=root/"data/selective_earnings"
    e=pd.read_parquet(old/"all_events.parquet")
    e=e[(e.entry_date.dt.year==args.year)&e.timing_verified&e.consensus_verified&e["rank"].notna()].copy()
    b=pd.read_parquet(old/"benchmark.parquet");sessions=pd.DatetimeIndex(b.date)
    raw_path=cache/f"entry_chain_{args.year}.parquet"
    if raw_path.exists():q=pd.read_parquet(raw_path)
    else:
        keys=e[["secid","entry_date"]].rename(columns={"entry_date":"date"})
        prev=keys.copy();prev["date"]=sessions[sessions.searchsorted(prev.date)-1]
        print(f"Scanning {len(e):,} event chains",flush=True)
        raw=scan("D:/OptionMetrics/parquet",args.year,keys=pd.concat([keys,prev]))
        lag=raw[["secid","optionid","date","volume","open_interest"]].copy()
        ix=sessions.searchsorted(lag.date,side="right");lag=lag[ix<len(sessions)].copy();ix=ix[ix<len(sessions)]
        lag["date"]=sessions[ix]
        lag=lag.rename(columns={"volume":"prior_volume","open_interest":"prior_oi"})
        raw=raw.merge(lag,on=["secid","optionid","date"],how="left",validate="m:1")
        q=raw.merge(keys,on=["secid","date"],how="inner").drop_duplicates(["secid","date","optionid"])
        q.to_parquet(raw_path,index=False)
    funnel,pairs=diagnose(e,q)
    out=root/"results/selective_earnings_options_v2";out.mkdir(parents=True,exist_ok=True)
    funnel.to_csv(out/f"funnel_{args.year}.csv",index=False)
    print(funnel.to_string(index=False),flush=True)
    print("CFADJ",q.cfadj.value_counts(dropna=False).head().to_dict(),flush=True)
    print("SS",q.ss_flag.value_counts(dropna=False).to_dict(),flush=True)
    for col in ("prior_volume","prior_oi"):
        print(col,q[col].describe(percentiles=[.1,.5,.9,.99]).to_dict(),flush=True)
    known=q[q.prior_volume.notna()]
    print("liq lag match",len(known),"of",len(q),flush=True)


if __name__=="__main__":main()
