"""Full entry menus and actual quote-based forward labels, kept out of features."""
from pathlib import Path
import argparse,json,hashlib
import numpy as np
import pandas as pd
from .data import scan
from .candidates_v2 import pack,unpack
from .fast_candidates_v2 import enumerate_structures
from .liquidity_v2 import LiquidityPolicy

ROOT=Path(__file__).resolve().parents[2]
CACHE=ROOT/'data/selective_earnings_v2'
OLD=ROOT/'data/selective_earnings'


def entry_chains(year,events,benchmark):
    path=CACHE/f'entry_chain_{year}.parquet'
    if path.exists():return pd.read_parquet(path)
    sessions=pd.DatetimeIndex(benchmark.date)
    keys=events[['secid','entry_date']].rename(columns={'entry_date':'date'})
    prev=keys.copy();prev['date']=sessions[sessions.searchsorted(prev.date)-1]
    print(f'{year}: full entry-chain scan, {len(events):,} events',flush=True)
    raw=scan('D:/OptionMetrics/parquet',year,keys=pd.concat([keys,prev]))
    lag=raw[['secid','optionid','date','volume','open_interest']].copy()
    ix=sessions.searchsorted(lag.date,side='right');lag=lag[ix<len(sessions)].copy();ix=ix[ix<len(sessions)]
    lag['date']=sessions[ix]
    lag=lag.rename(columns={'volume':'prior_volume','open_interest':'prior_oi'})
    raw=raw.merge(lag,on=['secid','optionid','date'],how='left',validate='m:1')
    q=raw.merge(keys,on=['secid','date'],how='inner').drop_duplicates(['secid','date','optionid'])
    q.to_parquet(path,index=False)
    return q


def quote_payoff(row,quotes):
    """Return quoted per-share liquidation; failures reserve full contractual loss."""
    s=unpack(row)
    value=0.
    factors=json.loads(row.factors)
    for leg in s.legs:
        key=(int(row.secid),pd.Timestamp(row.exit_date),leg.optionid)
        r=quotes.get(key)
        if r is None or not (np.isfinite(r.bid) and np.isfinite(r.ask) and 0<=r.bid<=r.ask and str(r.ss_flag)=='0'):
            return -s.max_loss-s.cashflow,False
        factor=float(r.cfadj)/float(factors[str(leg.optionid)])
        if not np.isfinite(factor) or factor<=0:return -s.max_loss-s.cashflow,False
        value+=leg.quantity*(r.bid if leg.quantity>0 else r.ask)*factor
    upper=abs(s.legs[0].strike-s.legs[1].strike) if s.cashflow<0 else 0.
    if value>upper+.01:return -s.max_loss-s.cashflow,False
    return value,True


def prepare_year(year):
    CACHE.mkdir(parents=True,exist_ok=True)
    dest=CACHE/f'candidates_complete_{year}.parquet'
    if dest.exists():
        print(f'{year}: candidate outcomes cached',flush=True);return
    e=pd.read_parquet(OLD/'all_events.parquet')
    b=pd.read_parquet(OLD/'benchmark.parquet');sessions=pd.DatetimeIndex(b.date)
    yr=b[b.date.dt.year==year]
    e=e[(e.entry_date.dt.year==year)&e.timing_verified&e.consensus_verified&e['rank'].notna()].copy()
    # Declared calendar rule: flat by year-end; no future-quote coverage filter.
    e=e[e.entry_date<=yr.date.iloc[-22]].copy()
    q=entry_chains(year,e,b)
    q['standard_contract']=q.ss_flag.astype(str).eq('0') & np.isfinite(q.cfadj) & (q.cfadj>0)
    q=q[(q.expiry-q.date).dt.days.between(21,90)]
    chains={k:g for k,g in q.groupby(['secid','date'],sort=False)}
    rows=[]
    for event in e.sort_values(['entry_date','event_id']).itertuples():
        if not np.isfinite(event.adv) or event.adv<5_000_000 or not np.isfinite(event.rv) or event.rv<=0:continue
        ch=chains.get((event.secid,event.entry_date))
        if ch is None:continue
        direction=1 if event.rank>=.8 else -1 if event.rank<=.2 else 0
        families=[('condor',0)]
        if direction or (np.isfinite(event.reaction) and abs(event.reaction)>=.03):
            families += [('directional',1),('directional',-1)]
        for sleeve,trade_direction in families:
            menu=enumerate_structures(ch,sleeve,trade_direction or 1)
            for candidate in menu:
                s=candidate['structure']
                entry_idx=int(sessions.searchsorted(event.entry_date))
                exit_idx=min(entry_idx+20,int(sessions.searchsorted(s.expiry-pd.Timedelta(days=7))))
                if exit_idx>=len(sessions) or exit_idx<=entry_idx:continue
                expiry_date=sessions[exit_idx]
                ids=[x.optionid for x in s.legs]
                iv=float(ch[ch.optionid.isin(ids)].iv.mean())
                ratio=iv/event.rv
                if sleeve=='directional':
                    news=int(np.sign(event.rank-.5))
                    response=int(np.sign(event.reaction)) if np.isfinite(event.reaction) else 0
                    sigbin=(2*news+response)*trade_direction
                else:
                    sigbin=2 if (event.rank>=.9 or event.rank<=.1) else 1 if direction else 0
                volbin=0 if ratio<1. else 1 if ratio<1.3 else 2
                factors={str(int(x.optionid)):float(ch.loc[ch.optionid==x.optionid,'cfadj'].iloc[0]) for x in s.legs}
                for horizon,limit in (('h10',10),('h20',20),('carry',45)):
                    end_idx=min(entry_idx+limit,int(sessions.searchsorted(s.expiry-pd.Timedelta(days=2))),int(sessions.searchsorted(yr.date.iloc[-1])))
                    if end_idx<=entry_idx:continue
                    candidate_id=event.event_id+'_'+candidate['key']+'_'+horizon
                    rows.append(dict(candidate_id=candidate_id,event_id=event.event_id,secid=event.secid,
                        entry_date=event.entry_date,exit_date=sessions[end_idx],expiry=s.expiry,sleeve=sleeve,
                        direction=trade_direction,archetype=candidate['key']+'_'+horizon,horizon=horizon,
                        signal_bin=sigbin,vol_bin=volbin,iv_rv=ratio,capacity=candidate['capacity'],
                        cashflow=s.cashflow,max_loss=s.max_loss,friction=candidate['friction'],
                        legs=pack(s),factors=json.dumps(factors,sort_keys=True)))
    c=pd.DataFrame(rows)
    if c.empty:raise ValueError(f'{year}: no feasible candidate structures')
    print(f'{year}: {len(c):,} candidates across {c.event_id.nunique():,} events; scanning realized exits',flush=True)
    ids={int(d['optionid']) for text in c.legs for d in json.loads(text)}
    digest=hashlib.sha256(','.join(map(str,sorted(ids))).encode()).hexdigest()[:12]
    marks_file=CACHE/f'all_candidate_marks_{year}_{digest}.parquet'
    if marks_file.exists():marks=pd.read_parquet(marks_file)
    else:
        marks=scan('D:/OptionMetrics/parquet',year,optionids=ids)
        marks.to_parquet(marks_file,index=False)
    keep=['secid','date','optionid','bid','ask','ss_flag','cfadj']
    lookup={(int(r.secid),pd.Timestamp(r.date),int(r.optionid)):r for r in marks[keep].itertuples(index=False)}
    labels=[quote_payoff(r,lookup) for r in c.itertuples()]
    c['exit_value']=[x[0] for x in labels];c['valid_exit']=[x[1] for x in labels]
    legs_count=c.legs.map(lambda x:len(json.loads(x)))
    c['risk_unit']=c.max_loss*100+2*.65*legs_count
    c['pnl_unit']=(c.cashflow+c.exit_value)*100-2*.65*legs_count
    c['return_on_risk']=c.pnl_unit/c.risk_unit
    c.to_parquet(dest,index=False)
    info=dict(marks_file=marks_file.name,year=year,candidates=len(c),events=c.event_id.nunique(),invalid_exit_fraction=float((~c.valid_exit).mean()),
              policy='actual ask/bid, zero-volume small lot floor 1, prior OI 1%, volume 10%, friction <=30% max loss',
              labels='actual quoted fixed-horizon outcomes; never prediction features')
    (CACHE/f'manifest_complete_{year}.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    print(json.dumps(info),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--start',type=int,default=1999);p.add_argument('--end',type=int,default=2010)
    a=p.parse_args()
    if a.start<1999 or a.end>2010 or a.start>a.end:p.error('development years 1999–2010 only')
    for year in range(a.start,a.end+1):prepare_year(year)


if __name__=='__main__':main()
