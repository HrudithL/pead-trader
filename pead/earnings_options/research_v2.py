"""Chronological, actual-quote research for the finite v2 payoff hypotheses."""
from dataclasses import replace,asdict
from pathlib import Path
import argparse,hashlib,json
import numpy as np
import pandas as pd
from .prepare_v2 import CACHE,OLD,ROOT
from .payoff_v2 import select_decisions
from .candidates_v2 import unpack
from .engine import Config,run
from .data import scan
from .validation import metrics,evaluate


PROFILES={
    'cautious':dict(min_events=100,z=1.645,risk=.005,aggregate=.25),
    'balanced':dict(min_events=50,z=1.0,risk=.005,aggregate=.25),
    'opportunistic':dict(min_events=50,z=0.,risk=.01,aggregate=.40),
}


def decisions(history,profile):
    p=PROFILES[profile]
    return select_decisions(history,history,min_events=p['min_events'],z=p['z'],min_edge=.01,retain_all=True)


def daily_marks(year,selected,benchmark):
    selected=selected[selected.entry_date.dt.year==year].drop_duplicates('candidate_id')
    digest=hashlib.sha256('\n'.join(sorted(selected.candidate_id)).encode()).hexdigest()[:12]
    dest=CACHE/f'daily_marks_{year}_{digest}.parquet'
    if dest.exists():return pd.read_parquet(dest)
    if selected.empty:
        return pd.read_parquet(OLD/f'quotes_{year}.parquet').iloc[:0].copy()
    ids=set();keys=[]
    for row in selected.itertuples():
        ids.update(x['optionid'] for x in json.loads(row.legs))
        dates=benchmark.loc[benchmark.date.between(row.entry_date,row.exit_date),'date']
        keys.extend((row.secid,d) for d in dates)
    needed=pd.DataFrame(keys,columns=['secid','date']).drop_duplicates()
    print(f'{year}: scanning daily marks for {selected.event_id.nunique():,} model-selected events',flush=True)
    manifest=json.loads((CACHE/f'manifest_complete_{year}.json').read_text())
    q=pd.read_parquet(CACHE/manifest['marks_file'])
    q=q[q.optionid.isin(ids)]
    key_index=pd.MultiIndex.from_frame(needed)
    q=q[pd.MultiIndex.from_frame(q[['secid','date']]).isin(key_index)].copy()
    if q.empty:raise ValueError(f'{year}: no selected daily marks')
    q['standard_contract']=q.ss_flag.astype(str).eq('0')
    entry=pd.read_parquet(CACHE/f'entry_chain_{year}.parquet',columns=['secid','date','optionid','prior_volume','prior_oi'])
    q=q.merge(entry,on=['secid','date','optionid'],how='left',validate='1:1')
    q.to_parquet(dest,index=False)
    return q


def add_sectors(events):
    names=pd.read_parquet('C:/Users/hrudi/Documents/BEI/PEAD_Trading/data/raw_wrds/crsp/stocknames.parquet')
    names['namedt']=pd.to_datetime(names.namedt);names['nameenddt']=pd.to_datetime(names.nameenddt)
    byname={k:g.sort_values('namedt') for k,g in names.groupby('permno')}
    e=events.copy()
    for permno,index in e.groupby('permno').groups.items():
        n=byname.get(permno)
        if n is None:continue
        dates=pd.DatetimeIndex(n.namedt)
        for i in index:
            pos=dates.searchsorted(e.loc[i,'entry_date'],side='right')-1
            if pos>=0 and n.iloc[pos].nameenddt>=e.loc[i,'entry_date'] and pd.notna(n.iloc[pos].siccd):
                e.loc[i,'sector']=f'sic_{int(n.iloc[pos].siccd)//100:02d}'
    return e


def run_profile(events,quotes,benchmark,selected,cfg):
    if selected.empty:return run(events.iloc[:0],quotes,benchmark,cfg)
    lookup={k:list(g.itertuples()) for k,g in selected.groupby(['event_id','sleeve'],sort=False)}
    e=events[events.event_id.isin(selected.event_id)].copy()
    directional=selected[selected.sleeve=='directional'].drop_duplicates('event_id').set_index('event_id').direction
    holding=(selected.exit_date-selected.entry_date).dt.days.clip(lower=1)
    priority=selected.assign(rate=selected.predicted_edge/holding).groupby('event_id').rate.max()
    e['trade_direction']=e.event_id.map(directional).fillna(0).astype(int)
    e['entry_priority']=e.event_id.map(priority).fillna(0.)
    def selector(chain,sleeve,trade_direction,event,context):
        best=None;best_score=-np.inf
        for decision in lookup.get((event.event_id,sleeve),[]):
            s=unpack(decision)
            fee=context['commission']*len(s.legs)
            risk=s.max_loss*100+2*fee
            funded=s.max_loss*100+2*fee
            available=min(decision.capacity-context['used_lots'].get((context['date'],leg.optionid),0) for leg in s.legs)
            lots=max(0,min(int(context['risk_budget']//risk),int(context['free_cash']//funded),int(available)))
            if not lots:continue
            days=max(1,(decision.exit_date-decision.entry_date).days)
            score=decision.predicted_edge*risk*lots/days
            if score>best_score:
                best_score=score;best=(s,decision.predicted_edge,decision.capacity,decision.exit_date,decision.direction,decision.candidate_id)
        return best
    return run(e,quotes,benchmark,cfg,selector=selector,selector_context=True)



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',type=int,default=1999);parser.add_argument('--end',type=int,default=2010)
    parser.add_argument('--output',type=Path,default=ROOT/'results/selective_earnings_options_v2')
    args=parser.parse_args()
    if args.start<1999 or args.end>2010:parser.error('reserved final holdout cannot be read here')
    frames=[]
    for year in range(1999,args.end+1):
        path=CACHE/f'candidates_complete_{year}.parquet'
        if not path.exists():raise FileNotFoundError(f'{path}: complete flexible-menu preparation first')
        frames.append(pd.read_parquet(path))
    history=pd.concat(frames,ignore_index=True)
    benchmark=pd.read_parquet(OLD/'benchmark.parquet')
    trend=(1+benchmark.benchmark).rolling(126,min_periods=126).apply(np.prod,raw=True).shift(1)-1
    regime=pd.Series(np.where(trend>=0,1.,-1.),index=benchmark.date)
    history['market_regime']=history.entry_date.map(regime).fillna(0.)
    print(f'{len(history):,} actual quoted candidate outcomes; fitting prior-only quarterly forecasts',flush=True)
    chosen={name:decisions(history,name) for name in PROFILES}
    union=pd.concat(chosen.values(),ignore_index=True).drop_duplicates('candidate_id')
    events=add_sectors(pd.read_parquet(OLD/'all_events.parquet'))
    benchmark=pd.read_parquet(OLD/'benchmark.parquet')
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    for name,d in chosen.items():
        d.to_parquet(CACHE/f'decisions_{name}.parquet',index=False)
        print(f'{name}: {len(d):,} selected candidate decisions',flush=True)
    scenarios={name:(name,1_000_000,.65) for name in PROFILES}
    scenarios.update({name+'_100k':(name,100_000,.65) for name in PROFILES})
    scenarios['balanced_double_fee']=('balanced',1_000_000,1.30)
    daily={k:[] for k in scenarios};trades={k:[] for k in scenarios};diags={k:[] for k in scenarios}
    nav={k:v[1] for k,v in scenarios.items()}
    for year in range(args.start,args.end+1):
        b=benchmark[benchmark.date.dt.year==year].copy()
        q=daily_marks(year,union,benchmark)
        for label,(profile,initial,commission) in scenarios.items():
            settings=PROFILES[profile]
            cfg=Config(initial_nav=nav[label],strict=False,sleeves=('directional','condor'),
                hold_sessions=20,expiry_buffer_days=2,condor_stops=False,rank_tail=.20,min_adv=5_000_000,
                iv_rv_threshold=0.,min_edge_fraction=.01,commission=commission,
                directional_risk=settings['risk'],condor_risk=settings['risk'],aggregate_risk=settings['aggregate'],
                issuer_risk=.02,sector_risk=.10,cohort_risk=.05)
            # IV/RV acceptance is learned from prior actual option outcomes;
            # zero threshold intentionally removes the earlier crude RV screen.
            d=chosen[profile];d=d[d.entry_date.dt.year==year]
            e=events[(events.entry_date.dt.year==year)&events.entry_date.isin(b.date)]
            r=run_profile(e,q,b,d,cfg)
            daily[label].append(r.daily);trades[label].append(r.trades);diags[label].append(r.diagnostics)
            nav[label]=float(r.daily.nav.iloc[-1])
        print(f'{year}: research NAVs '+json.dumps(nav),flush=True)
    rows=[]
    for label,(profile,initial,commission) in scenarios.items():
        folder=out/label;folder.mkdir(parents=True,exist_ok=True)
        d=pd.concat(daily[label],ignore_index=True)
        t=pd.concat(trades[label],ignore_index=True)
        d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
        report=dict(profile=profile,starting_nav=initial,commission=commission,parameters=PROFILES[profile],
                    diagnostics=diags[label],trade_count=len(t),periods={})
        for period,start,end in [('discovery',1999,2004),('validation',2005,2007),('development_test',2008,2010),('all_development',args.start,args.end)]:
            sub=d[d.date.dt.year.between(start,end)]
            if sub.empty:continue
            stats=evaluate(sub,False,True,repetitions=500)
            count=int(t.exit_date.dt.year.between(start,end).sum()) if len(t) else 0
            stats['trade_count']=count;stats['mean_risk_nav']=float((sub.reserved/sub.nav).mean())
            stats['accepted']=False
            report['periods'][period]=stats
            rows.append(dict(scenario=label,period=period,cagr=stats['strategy']['cagr'],sharpe=stats['strategy']['sharpe'],
                             drawdown=stats['strategy']['max_drawdown'],spy_cagr=stats['benchmark']['cagr'],
                             trade_count=count,mean_risk_nav=stats['mean_risk_nav']))
        (folder/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        if len(t):t.groupby('sleeve').pnl.agg(['count','sum','mean']).to_csv(folder/'sleeve_attribution.csv')
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    print(table.to_string(index=False),flush=True)
    # Select on validation only. Test numbers cannot choose the model.
    val=table[(table.period=='validation')&table.scenario.isin(PROFILES)&(table.trade_count>=50)&(table.drawdown>=-.25)]
    selected=val.sort_values(['sharpe','scenario'],ascending=[False,True]).iloc[0].scenario if len(val) else None
    (out/'validation_selection.json').write_text(json.dumps(dict(selected_by_validation=selected,
        rule='highest validation Sharpe among three predefined profiles, >=50 trades and drawdown <=25%',
        target='12–15% NAV CAGR in unseen tests, with benchmark/risk/provenance gates',
        final_holdout_evaluated=False,investment_accepted=False),indent=2),encoding='utf-8')


if __name__=='__main__':main()
