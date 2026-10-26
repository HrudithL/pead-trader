"""Conditional post-earnings cash-secured puts, with event-excluded volatility.

This research ledger does not establish American assignment or source-link
validity. Full strike collateral is reserved; simulated profits are conditional.
"""
from pathlib import Path
from dataclasses import dataclass,asdict
import json,hashlib,argparse
import numpy as np
import pandas as pd
from scipy.special import ndtr
from .prepare_v2 import ROOT,CACHE,OLD
from .data import scan
from .structures import Leg,Structure
from .liquidity_v2 import LiquidityPolicy,eligible,capacity
from .engine import Config,run
from .validation import evaluate
from .index_research import DATA,signals
from .corporate_actions import action_calendar

LIQ=LiquidityPolicy(min_oi=50,max_relative_spread=.25,max_absolute_spread=.50,participation=.05,oi_participation=.01,small_lot_floor=1)

@dataclass(frozen=True)
class PutPolicy:
    delta:float
    reaction:float
    iv_rv:float
    trend:bool

POLICIES={f'{name}_{gate}':PutPolicy(delta,reaction,ratio,gate=='trend') for name,delta,reaction,ratio in
          [('income',.35,.01,1.25),('drift',.60,.02,1.10),('rich',.50,0.,1.50)] for gate in ('trend','all')}

def non_event_features(stocks,announcements):
    s=stocks.sort_values(['permno','date']).reset_index(drop=True).copy()
    keys=pd.MultiIndex.from_frame(announcements[['permno','day0_date']].drop_duplicates().rename(columns={'day0_date':'date'}))
    mask=pd.MultiIndex.from_frame(s[['permno','date']]).isin(keys)
    s['non_event_ret']=pd.to_numeric(s.ret,errors='coerce').mask(mask)
    s['rv_non_event']=s.groupby('permno',sort=False).non_event_ret.transform(lambda x:x.shift(1).rolling(60,min_periods=30).std()*np.sqrt(252))
    s['prior_spot']=s.groupby('permno',sort=False).prc.shift(1).abs()
    if 'cfacpr' in s:
        factor=pd.to_numeric(s.cfacpr,errors='coerce')
        prior=factor.groupby(s.permno,sort=False).shift(1)
        ratio=(factor/prior).where((factor>0)&(prior>0))
        s['prior_spot']*=ratio
    return s

def expected_liability(spot,strike,rv,iv,hold_sessions,dte,tail=True):
    variance=rv**2*hold_sessions/252+iv**2*max(0.,dte-hold_sessions*365/252)/365
    sd=max(1e-9,np.sqrt(variance))
    def put(s):
        d1=np.log(s/strike)/sd+.5*sd;d2=d1-sd
        return strike*ndtr(-d2)-s*ndtr(-d1)
    ordinary=put(spot)
    return .99*ordinary+.01*put(.60*spot) if tail else ordinary

def features(year,all_events):
    path=CACHE/f'cash_put_features_v2_{year}.parquet'
    if path.exists():return pd.read_parquet(path)
    e=all_events[(all_events.entry_date.dt.year==year)&all_events.timing_verified&all_events.consensus_verified&
                 (all_events.reaction>0)&(all_events.adv>=5_000_000)].copy()
    wanted=set(e.permno)
    parts=[]
    for y in (year-1,year):
        s=pd.read_parquet(DATA/f'raw_wrds/crsp/dsf_{y}.parquet',columns=['permno','date','ret','prc','cfacpr'])
        s=s[s.permno.isin(wanted)].copy();s['date']=pd.to_datetime(s.date)
        for col in ('ret','prc','cfacpr'):s[col]=pd.to_numeric(s[col],errors='coerce')
        parts.append(s)
    s=non_event_features(pd.concat(parts,ignore_index=True),all_events)
    e=e.merge(s[['permno','date','rv_non_event','prior_spot']].rename(columns={'date':'entry_date'}),on=['permno','entry_date'],how='left',validate='m:1')
    # A shock buffer keeps removal of the one-time jump from implying calm ahead.
    e['forecast_rv']=np.sqrt(e.rv_non_event**2+.10**2)
    e['spot']=e.prior_spot;e['rank']=.5;e['trade_direction']=1
    e.to_parquet(path,index=False)
    return e

def menu(chain,event,policy,commission):
    c=eligible(chain,LIQ);dte=(c.expiry-c.date).dt.days
    c=c[(c.cp=='P')&dte.between(21,45)&np.isfinite(c.iv)&(c.iv/event.forecast_rv>=policy.iv_rv)&(c.bid>0)&(c.bid<c.strike)].copy()
    rows=[]
    for r in c.itertuples():
        days=(r.expiry-r.date).days;hold=min(20,max(1,int((days-4)*252/365)))
        expected=expected_liability(event.spot,r.strike,event.forecast_rv,r.iv,hold,days)
        # Current full quoted width is an explicit proxy for future exit friction.
        # European zero-rate liability remains an approximation, not an American edge proof.
        profit=r.bid-expected-(r.ask-r.bid)-2*commission/100
        if profit/r.strike<.0025:continue
        cap=max(0,int(min(np.floor(LIQ.oi_participation*r.prior_oi),max(LIQ.small_lot_floor,np.floor(LIQ.participation*r.prior_volume)))))
        if cap<1:continue
        key=(abs(abs(r.delta)-policy.delta),abs(days-30),int(r.optionid))
        leg=Leg(int(r.optionid),-1,'P',float(r.strike),r.expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
        rows.append((key,Structure('directional',(leg,),float(r.bid),float(r.strike-r.bid),r.expiry),profit/r.strike,cap))
    return sorted(rows,key=lambda x:x[0])

def prepare_menus(year,e,policies,commission=.65):
    q=pd.read_parquet(CACHE/f'entry_chain_{year}.parquet')
    q['standard_contract']=q.ss_flag.astype(str).eq('0')&np.isfinite(q.cfadj)&(q.cfadj>0)
    # The existing entry cache deliberately lags OI an extra day; conservative,
    # disclosed and retained to avoid changing the sample after seeing outcomes.
    chains={k:g for k,g in q.groupby(['secid','date'],sort=False)}
    lookup={};ids=set()
    for row in e.itertuples():
        if not np.isfinite(row.spot) or row.spot<=0 or not np.isfinite(row.forecast_rv) or row.forecast_rv<=0:continue
        ch=chains.get((row.secid,row.entry_date))
        if ch is None:continue
        memo={}
        for name,policy in policies.items():
            if row.reaction<policy.reaction:continue
            signature=(policy.delta,policy.reaction,policy.iv_rv)
            if signature not in memo:memo[signature]=menu(ch,row,policy,commission)
            candidates=memo[signature]
            if candidates:lookup[(name,row.event_id)]=candidates;ids.update(x[1].legs[0].optionid for x in candidates)
    return lookup,ids,q

def marks(year,ids,entry):
    digest=hashlib.sha256(','.join(map(str,sorted(ids))).encode()).hexdigest()[:12]
    path=CACHE/f'cash_put_marks_{year}_{digest}.parquet'
    if path.exists():return pd.read_parquet(path)
    q=scan('D:/OptionMetrics/parquet',year,optionids=ids)
    if q.empty:return entry.iloc[:0].copy()
    q['standard_contract']=q.ss_flag.astype(str).eq('0')&np.isfinite(q.cfadj)&(q.cfadj>0)
    q=q.merge(entry[['secid','date','optionid','prior_volume','prior_oi']],on=['secid','date','optionid'],how='left',validate='1:1')
    q.to_parquet(path,index=False)
    return q

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--action-aware',action='store_true');args=parser.parse_args()
    out=ROOT/('results/selective_earnings_cash_puts_actions' if args.action_aware else 'results/selective_earnings_cash_puts');out.mkdir(parents=True,exist_ok=True)
    (out/'source_versions.json').write_text(json.dumps({p.name:__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},indent=2))
    (out/'frozen_policies.json').write_text(json.dumps({k:asdict(v) for k,v in POLICIES.items()},indent=2))
    all_events=pd.read_parquet(OLD/'all_events.parquet');b=signals()
    calendar=pd.read_parquet(OLD/'benchmark.parquet').date
    actions=action_calendar(calendar) if args.action_aware else None
    if actions is not None:actions.to_csv(out/'action_calendar.csv',index=False)
    scenarios={f'{name}_{cap//1000}k':(name,cap) for name in POLICIES for cap in (100_000,1_000_000)}
    nav={k:v[1] for k,v in scenarios.items()};daily={k:[] for k in scenarios};trades={k:[] for k in scenarios};diags={k:[] for k in scenarios};skips={k:[] for k in scenarios}
    for year in range(1999,2011):
        frame=b[b.date.dt.year==year].reset_index(drop=True).copy()
        e=features(year,all_events);e=e[e.entry_date<=frame.date.iloc[-22]].copy()
        lookup,ids,entry=prepare_menus(year,e,POLICIES);q=marks(year,ids,entry)
        print(f'{year}: {len(ids):,} cash-put candidate contracts',flush=True)
        for label,(name,cap) in scenarios.items():
            policy=POLICIES[name];bench=frame.copy()
            if policy.trend:bench['risk_off']=bench.trend_sign<0
            else:bench['risk_off']=False
            events=e[e.event_id.isin([k[1] for k in lookup if k[0]==name])].copy()
            if policy.trend:events=events[events.entry_date.isin(bench.loc[~bench.risk_off,'date'])]
            cfg=Config(initial_nav=nav[label],strict=False,hold_sessions=20,expiry_buffer_days=4,sleeves=('directional',),
                require_price_edge=False,min_adv=5_000_000,directional_risk=.08,issuer_risk=.08,sector_risk=.30,
                aggregate_risk=.85,cohort_risk=.20,defer_unquoted_exits=True)
            def choose(ch,sleeve,direction,event,ctx):
                for key,structure,edge,raw_cap in lookup.get((name,event.event_id),[]):
                    used=ctx['used_lots'].get((ctx['date'],structure.legs[0].optionid),0)
                    unit=structure.max_loss*100+2*ctx['commission']
                    if min(int(ctx['risk_budget']//unit),int(ctx['free_cash']//unit),raw_cap-used)>=1:
                        return structure,edge,raw_cap
                return None
            r=run(events,q,bench,cfg,selector=choose,selector_context=True,corporate_actions=actions)
            nav[label]=float(r.daily.nav.iloc[-1]);daily[label].append(r.daily);trades[label].append(r.trades);diags[label].append(r.diagnostics);skips[label].append(r.skips)
        print(f'{year}: cash-put NAVs '+json.dumps(nav),flush=True)
    rows=[]
    for label,(name,cap) in scenarios.items():
        folder=out/label;folder.mkdir(exist_ok=True)
        d=pd.concat(daily[label],ignore_index=True);t=pd.concat(trades[label],ignore_index=True)
        d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
        pd.concat(skips[label],ignore_index=True).to_csv(folder/'skips.csv.gz',index=False)
        report=dict(policy=asdict(POLICIES[name]),action_aware=args.action_aware,starting_nav=cap,assignment_model_verified=False,source_universe_verified=False,diagnostics=diags[label],periods={})
        for period,start,end in [('discovery',1999,2004),('validation',2005,2007),('development_test',2008,2010),('all_development',1999,2010)]:
            sub=d[d.date.dt.year.between(start,end)];stats=evaluate(sub,False,True,repetitions=500)
            count=int(t.loc[t.exit_date.dt.year.between(start,end),'event_id'].nunique()) if len(t) else 0
            stats['trade_count']=count;report['periods'][period]=stats
            rows.append(dict(scenario=label,capital=cap,period=period,cagr=stats['strategy']['cagr'],sharpe=stats['strategy']['sharpe'],drawdown=stats['strategy']['max_drawdown'],spy_cagr=stats['benchmark']['cagr'],trade_count=count,mean_risk_nav=float((sub.reserved/sub.nav).mean())))
        (folder/'summary.json').write_text(json.dumps(report,indent=2))
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    selected={}
    for cap in (100_000,1_000_000):
        val=table[(table.period=='validation')&(table.capital==cap)&(table.trade_count>=20)&(table.drawdown>=-.30)]
        selected[str(cap)]=val.sort_values(['sharpe','scenario'],ascending=[False,True]).iloc[0].scenario if len(val) else None
    (out/'validation_selection.json').write_text(json.dumps(dict(selected_by_validation=selected,final_holdout_evaluated=False,investment_accepted=False),indent=2))
    print(table.to_string(index=False),flush=True)

if __name__=='__main__':main()
