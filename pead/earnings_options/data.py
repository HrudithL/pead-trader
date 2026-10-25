"""Read-only WRDS/OptionMetrics adapter; all outputs belong to this worktree."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pyarrow.dataset as ds

from .economics import expected_edge
from .structures import select_structure


def audit(data_dir,om_dir):
    data_dir,om_dir=Path(data_dir),Path(om_dir)
    inputs={
        "events":data_dir/"events/equity_events_1996_2013.parquet",
        "earnings":data_dir/"earnings/ibes_clean_1995_2014.parquet",
        "links":data_dir/"metadata/resolved_universe_ids.parquet",
        "stocknames":data_dir/"raw_wrds/crsp/stocknames.parquet",
    }
    out={"sources":{},"option_years":[],"missing_years":[],"limitations":[
        "Daily EOD quotes cannot validate intraday fills or simultaneous complex-order execution.",
        "American early assignment, historical scheduled earnings and historical option identifier validity are not established by this adapter.",
        "Raw extract ends in 2013; no claim about current-market effectiveness is supported."]}
    for key,path in inputs.items():
        if not path.exists(): raise FileNotFoundError(path)
        f=pq.ParquetFile(path)
        out["sources"][key]=dict(path=str(path.resolve()),rows=f.metadata.num_rows,columns=f.schema_arrow.names,
                                  bytes=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns)
    e=pd.read_parquet(inputs["earnings"])
    out["earnings_rows"]=len(e)
    out["consensus_strictly_prior"]=int((pd.to_datetime(e.consensus_statpers)<pd.to_datetime(e.anndats)).sum())
    out["eps_basis_counts"]=e.eps_basis_status.value_counts().to_dict()
    ev=pd.read_parquet(inputs["events"],columns=["anntims_unknown"])
    out["unknown_release_times"]=int(ev.anntims_unknown.sum())
    for year in range(1996,2014):
        path=om_dir/f"opprcd{year}.parquet"
        if path.exists(): out["option_years"].append(year)
        else: out["missing_years"].append(year)
    names=pd.read_parquet(inputs["stocknames"])
    spy=names[(names.ticker=="SPY")&(names.permno==84398)]
    out["spy_identity_present"]=not spy.empty
    return out


def event_panel(data_dir):
    """Rebuild ranks and stock features, never use cached strategy picks/labels."""
    root=Path(data_dir)
    ev=pd.read_parquet(root/"events/equity_events_1996_2013.parquet")
    earnings=pd.read_parquet(root/"earnings/ibes_clean_1995_2014.parquet")
    for frame in (ev,earnings):
        frame["permno"]=pd.to_numeric(frame.permno,errors="coerce").astype("Int64")
    cols=["permno","fpedats","anndats","consensus_statpers","eps_basis_status"]
    earnings=earnings[cols].drop_duplicates(["permno","fpedats","anndats"],keep=False)
    keys=["permno","fpedats","anndats"]
    for col in ("anndats","fpedats"):
        ev[col]=pd.to_datetime(ev[col]); earnings[col]=pd.to_datetime(earnings[col])
    ev=ev.merge(earnings,on=keys,how="left",validate="m:1")
    links=pd.read_parquet(root/"metadata/resolved_universe_ids.parquet")
    links=links[~links.permno_ambiguous & links.permno.notna()].copy()
    links["permno"]=pd.to_numeric(links.permno,errors="coerce")
    links=links.drop_duplicates("permno",keep=False)
    ev=ev.merge(links[["permno","secid"]],on="permno",how="left",validate="m:1")
    ev=ev[(ev.sue_source=="analyst")&(ev.day0_status=="ok")&ev.secid.notna()&ev.sue_analyst.notna()].copy()
    ev=ev.drop_duplicates(["permno","anndats"],keep=False).sort_values(["anndats","permno"])
    ev["release_date"]=pd.to_datetime(ev.anndats)
    # Strictly earlier quarter pool: excludes ALL later reporters and same-quarter regimes.
    pool=[];ranks=pd.Series(np.nan,index=ev.index)
    for _,g in ev.groupby(ev.release_date.dt.to_period("Q"),sort=True):
        if pool:
            ref=np.sort(np.concatenate(pool))
            x=g.sue_analyst.to_numpy()
            ranks.loc[g.index]=(np.searchsorted(ref,x,"left")+np.searchsorted(ref,x,"right"))/(2*len(ref))
        pool.append(g.sue_analyst.to_numpy())
    ev["rank"]=ranks
    ev["secid"]=ev.secid.astype("int64")
    ev["permno"]=ev.permno.astype("int64")
    print(f"Building independent stock features for {len(ev):,} events",flush=True)
    stocks=[]
    for year in range(1995,2015):
        p=root/f"raw_wrds/crsp/dsf_{year}.parquet"
        if not p.exists(): continue
        f=pd.read_parquet(p,columns=["permno","date","ret","prc","vol","cfacshr"])
        f=f[f.permno.isin(set(ev.permno)|{84398})].copy()
        f["date"]=pd.to_datetime(f.date)
        for c in ("ret","prc","vol","cfacshr"): f[c]=pd.to_numeric(f[c],errors="coerce")
        stocks.append(f)
    s=pd.concat(stocks,ignore_index=True).sort_values(["permno","date"])
    if s.duplicated(["permno","date"]).any(): raise ValueError("duplicate stock rows")
    spy=s[s.permno==84398][["date","ret"]].rename(columns={"ret":"benchmark"}).sort_values("date")
    if spy.empty: raise ValueError("SPY total return series absent")
    # No inferred holiday calendar and no zero-filled missing benchmark dates.
    spy["rf"]=0.0
    dates=pd.DatetimeIndex(spy.date)
    e=ev.copy()
    e["day0_date"]=pd.to_datetime(e.day0_date)
    baseline=e[["release_date","day0_date"]].max(axis=1)
    loc=dates.searchsorted(baseline,side="right")
    good=loc<len(dates)
    e=e.loc[good].copy();loc=loc[good]
    e["entry_date"]=dates[loc].to_numpy()
    s["dollars"]=s.prc.abs()*s.vol
    grp=s.groupby("permno",sort=False)
    s["rv"]=grp.ret.transform(lambda x:x.shift(1).rolling(60,min_periods=40).std()*np.sqrt(252))
    s["adv"]=grp.dollars.transform(lambda x:x.shift(1).rolling(21,min_periods=20).mean())
    # Training labels mature 20 sessions after entry. They never filter events.
    s["forward_return"]=grp.ret.transform(lambda x:(1+x).shift(-1).rolling(20,min_periods=20).apply(np.prod,raw=True).shift(-19)-1)
    s["label_end"]=grp.date.shift(-20)
    features=s[["permno","date","prc","rv","adv","forward_return","label_end"]].rename(columns={"date":"entry_date","prc":"spot"})
    e=e.merge(features,on=["permno","entry_date"],how="left",validate="m:1")
    reaction=s[["permno","date","ret"]].merge(spy[["date","benchmark"]],on="date",how="left")
    reaction["reaction"]=reaction.ret-reaction.benchmark
    e=e.merge(reaction[["permno","date","reaction"]],left_on=["permno","day0_date"],right_on=["permno","date"],how="left",validate="m:1")
    e["spot"]=e.spot.abs()
    names=pd.read_parquet(root/"raw_wrds/crsp/stocknames.parquet") if (root/"raw_wrds/crsp/stocknames.parquet").exists() else pd.DataFrame()
    e["sector"]="unknown"
    if len(names):
        names["namedt"]=pd.to_datetime(names.namedt);names["nameenddt"]=pd.to_datetime(names.nameenddt)
        for permno,index in e.groupby("permno").groups.items():
            n=names[names.permno==permno].sort_values("namedt")
            if n.empty:continue
            for i in index:
                valid=n[(n.namedt<=e.loc[i,"entry_date"])&(n.nameenddt>=e.loc[i,"entry_date"])]
                if len(valid)==1 and pd.notna(valid.iloc[0].siccd):
                    e.loc[i,"sector"]=f"sic_{int(valid.iloc[0].siccd)//100:02d}"
    e["event_id"]=e.permno.astype(str)+"_"+e.release_date.dt.strftime("%Y%m%d")
    e["timing_verified"]=~e.anntims_unknown
    e["consensus_verified"]=pd.to_datetime(e.consensus_statpers)<e.release_date
    e["identity_verified"]=False  # resolved_universe_ids is static, not an as-of interval link.
    e["corporate_actions_verified"]=e.eps_basis_status.eq("anndats_basis_confirmed")
    e["assignment_verified"]=False
    e["schedule_known"]=False
    e["scheduled_date"]=pd.NaT
    e["prior_rank"]=e.groupby("permno")["rank"].shift(1)
    return e.reset_index(drop=True),spy.reset_index(drop=True)


RAW_COLS=["secid","date","optionid","cp_flag","strike_price","exdate","best_bid","best_offer",
          "volume","open_interest","delta","impl_volatility","cfadj","ss_flag"]


def unambiguous_quotes(frame):
    """An unresolved duplicate quote is excluded, never picked optimistically."""
    duplicated=frame.duplicated(["secid","date","optionid"],keep=False)
    if duplicated.any():print(f"Excluded {int(duplicated.sum()):,} ambiguous raw quote rows",flush=True)
    return frame.loc[~duplicated].copy()


def scan(om_dir,year,keys=None,optionids=None):
    """Bounded-memory streaming; never modifies the physical source files."""
    path=Path(om_dir)/f"opprcd{year}.parquet"
    if not path.exists(): return pd.DataFrame()
    chunks=[]
    key_index=pd.MultiIndex.from_frame(keys[["secid","date"]].drop_duplicates()) if keys is not None else None
    predicate=None
    if optionids is not None:
        if not optionids: return pd.DataFrame()
        predicate=ds.field("optionid").isin(sorted(optionids))
    if keys is not None:
        predicate=ds.field("secid").isin(keys.secid.unique().tolist())
    scanner=ds.dataset(path,format="parquet").scanner(columns=RAW_COLS,filter=predicate,batch_size=150_000,use_threads=False)
    for batch in scanner.to_batches():
        a=batch.to_pandas()
        if optionids is not None:
            a=a[a.optionid.isin(optionids)].copy()
        if a.empty: continue
        a["date"]=pd.to_datetime(a.date)
        if key_index is not None:
            a=a[pd.MultiIndex.from_frame(a[["secid","date"]]).isin(key_index)]
        if len(a): chunks.append(a)
    if not chunks: return pd.DataFrame()
    out=pd.concat(chunks,ignore_index=True)
    out["exdate"]=pd.to_datetime(out.exdate)
    out["standard_contract"]=out.cfadj.eq(1)&out.ss_flag.astype(str).eq("0")
    out=out.rename(columns={"cp_flag":"cp","strike_price":"strike","exdate":"expiry",
                            "best_bid":"bid","best_offer":"ask","impl_volatility":"iv"})
    out["strike"]=out.strike/1000.
    return unambiguous_quotes(out)


def prepare(data_dir,om_dir,out_dir,years,max_events=None):
    out=Path(out_dir)
    out.mkdir(parents=True,exist_ok=True)
    info=audit(data_dir,om_dir)
    events_path=out/"all_events.parquet";benchmark_path=out/"benchmark.parquet"
    if events_path.exists() and benchmark_path.exists():
        events=pd.read_parquet(events_path);benchmark=pd.read_parquet(benchmark_path)
    else:
        events,benchmark=event_panel(data_dir)
        events.to_parquet(events_path,index=False);benchmark.to_parquet(benchmark_path,index=False)
    if "forward_return_1" not in events:
        from .labels import refresh_labels
        print("Building market-session labels with observed delisting returns",flush=True)
        events=refresh_labels(data_dir,out)
    for year in years:
        dest=out/f"prepared_{year}.json"
        if dest.exists():
            prior_manifest=json.loads(dest.read_text())
            if prior_manifest.get("source")!=info["sources"]:
                raise ValueError(f"{year}: source manifest changed; use a fresh cache directory")
            print(f"{year}: cache already complete",flush=True);continue
        if year not in info["option_years"]:
            print(f"{year}: no raw option data; never fabricate a cash-only year",flush=True);continue
        e=events[events.entry_date.dt.year==year].copy().sort_values(["entry_date","event_id"])
        e=e[e.timing_verified & e.consensus_verified & e["rank"].notna()]
        if max_events is not None: e=e.head(max_events)
        sessions=pd.DatetimeIndex(benchmark.date)
        # Each entry snapshot gets liquidity from exactly the previous session.
        keys=e[["secid","entry_date"]].rename(columns={"entry_date":"date"})
        prior=keys.copy();prior["date"]=sessions[sessions.searchsorted(prior.date)-1]
        print(f"{year}: scanning entry chains for {len(e):,} events",flush=True)
        raw=scan(om_dir,year,keys=pd.concat([keys,prior]))
        if raw.empty:
            print(f"{year}: no chains",flush=True);continue
        lag=raw[["secid","optionid","date","volume","open_interest"]].copy()
        nxt=sessions.searchsorted(lag.date,side="right")
        lag=lag[nxt<len(sessions)].copy();nxt=nxt[nxt<len(sessions)]
        lag["date"]=sessions[nxt]
        lag=lag.rename(columns={"volume":"prior_volume","open_interest":"prior_oi"})
        raw=raw.merge(lag,on=["secid","optionid","date"],how="left",validate="m:1")
        entry=raw.merge(keys,on=["secid","date"],how="inner")
        entry=entry.drop_duplicates(["secid","date","optionid"])
        chains={k:v for k,v in entry.groupby(["secid","date"],sort=False)}
        ids=set()
        history=events
        for index,row in e.iterrows():
            ch=chains.get((row.secid,row.entry_date))
            if ch is None: continue
            ch=ch[ch.standard_contract]
            for sleeve in ("directional","condor"):
                direction=1 if row["rank"]>=.9 else -1 if row["rank"]<=.1 else 0
                if sleeve=="directional" and (not direction or not np.isfinite(row.reaction) or direction*row.reaction<=0): continue
                s=select_structure(ch,sleeve,direction or 1)
                if s is None: continue
                ids.update(x.optionid for x in s.legs)
                stop=s.expiry-pd.Timedelta(days=7)
                horizon=max(1,min(20,int(sessions.searchsorted(stop))-int(sessions.searchsorted(row.entry_date))))
                e.loc[index,sleeve+"_edge"]=expected_edge(s,row.spot,row.rv,row.entry_date,history,hold_sessions=horizon)
        for sleeve in ("directional","condor","reversal"):
            if sleeve+"_edge" not in e:e[sleeve+"_edge"]=np.nan
        print(f"{year}: scanning daily marks for {len(ids):,} selected contracts",flush=True)
        marks=[]
        for mark_year in (year,year+1):
            if mark_year not in info["option_years"]: continue
            m=scan(om_dir,mark_year,optionids=ids)
            if len(m):marks.append(m)
        if marks:
            q=pd.concat(marks,ignore_index=True)
            # Liquidity columns needed only on entry dates; marks never backfill them.
            q=q.merge(entry[["secid","date","optionid","prior_volume","prior_oi"]],on=["secid","date","optionid"],how="left",validate="1:1")
            end=pd.Timestamp(f"{year+1}-03-31")
            q=q[(q.date>=pd.Timestamp(f"{year}-01-01"))&(q.date<=end)]
        else:
            q=entry.iloc[:0].copy()
        e.to_parquet(out/f"events_{year}.parquet",index=False)
        q.to_parquet(out/f"quotes_{year}.parquet",index=False)
        meta=dict(year=year,events=len(e),contracts=len(ids),quote_rows=len(q),max_events=max_events,
                  execution="EOD ask/bid, previous-session liquidity",benchmark="CRSP SPY 84398 ret: total return",
                  limitations=info["limitations"],source=info["sources"])
        meta["fingerprint"]=hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest()
        dest.write_text(json.dumps(meta,indent=2),encoding="utf-8")
        print(f"{year}: prepared {len(q):,} daily marks",flush=True)
    if any("label_model" not in json.loads(p.read_text()) for p in out.glob("prepared_*.json")):
        rescore(out)
    return info


def rescore(cache):
    """Recompute a corrected research price model without rescanning raw quotes."""
    cache=Path(cache)
    history=pd.read_parquet(cache/"all_events.parquet")
    sessions=pd.DatetimeIndex(pd.read_parquet(cache/"benchmark.parquet").date)
    for p in sorted(cache.glob("prepared_*.json")):
        year=int(p.stem.split("_")[-1])
        e=pd.read_parquet(cache/f"events_{year}.parquet")
        q=pd.read_parquet(cache/f"quotes_{year}.parquet")
        chains={k:v for k,v in q.groupby(["secid","date"],sort=False)}
        for i,row in e.iterrows():
            ch=chains.get((row.secid,row.entry_date))
            if ch is None:continue
            ch=ch[ch.standard_contract]
            for sleeve in ("directional","condor"):
                direction=1 if row["rank"]>=.9 else -1 if row["rank"]<=.1 else 0
                if sleeve=="directional" and (not direction or not np.isfinite(row.reaction) or direction*row.reaction<=0):continue
                s=select_structure(ch,sleeve,direction or 1)
                if s is None:continue
                horizon=max(1,min(20,int(sessions.searchsorted(s.expiry-pd.Timedelta(days=7)))-int(sessions.searchsorted(row.entry_date))))
                e.loc[i,sleeve+"_edge"]=expected_edge(s,row.spot,row.rv,row.entry_date,history,hold_sessions=horizon)
        e.to_parquet(cache/f"events_{year}.parquet",index=False)
        meta=json.loads(p.read_text());meta["label_model"]="market-session horizons 1–20; observed delisting losses retained"
        meta.pop("fingerprint",None)
        meta["fingerprint"]=hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest()
        p.write_text(json.dumps(meta,indent=2),encoding="utf-8")
        print(f"{year}: economic forecasts refreshed",flush=True)
