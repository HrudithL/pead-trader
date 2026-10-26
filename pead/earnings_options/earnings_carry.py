"""Continuous fully paid earnings-call ladder; bounded stock exposure and memory."""
from pathlib import Path
import argparse,hashlib,json
from dataclasses import replace,asdict
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .cash_puts import features
from .earnings_calls import LIQ as BASE_LIQ,select_menu,POLICIES
LIQ=replace(BASE_LIQ,max_absolute_spread=None)
from .liquidity_v2 import eligible
from .prepare_v2 import ROOT,CACHE,OLD
from .index_research import signals
from .index_directional import cash_sweep
from .corporate_actions import action_calendar
from .data import scan,unambiguous_quotes
from .engine import Config,run
from .quote_store import YearQuoteStore
from .validation import evaluate

def menu(chain,event,fee=.65,intrinsic=False):
    known=chain.copy()
    if intrinsic:
        # One-share upper bound for risk admission, not a claimed source Greek.
        known['delta']=1.
    else:
        if {'prior_delta','prior_iv'}-set(chain):raise ValueError('prior-session option analytics required')
        known['delta']=known.prior_delta;known['iv']=known.prior_iv
    c=eligible(known,LIQ);dte=(c.expiry-c.date).dt.days
    mask=(c.cp=='C')&dte.between(120,270)&(c.strike>0)&(c.ask<=event.spot)
    if intrinsic:
        mask=mask&(event.spot/c.strike>=1.20)&(c.ask>=(event.spot-c.strike).clip(lower=0)-.01*event.spot)
    else:
        mask=mask&np.isfinite(c.iv)&c.iv.between(.02,3.)&(c.delta>=.75)&(event.spot/c.strike>=1.05)
    c=c[mask].copy()
    choices=[]
    for r in c.itertuples():
        cap=max(0,int(min(np.floor(LIQ.oi_participation*r.prior_oi),max(LIQ.small_lot_floor,np.floor(LIQ.participation*r.prior_volume)))))
        if cap<1:continue
        extrinsic=max(0.,r.ask-max(0.,event.spot-r.strike))
        cost=(r.ask-r.bid+2*fee/100+extrinsic*50/((r.expiry-r.date).days*252/365))/(r.delta*event.spot)
        if cost>.01:continue
        key=(cost,abs((r.expiry-r.date).days-180),int(r.optionid))
        from .structures import Leg,Structure
        leg=Leg(int(r.optionid),1,'C',float(r.strike),r.expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
        choices.append((key,Structure('directional',(leg,),-float(r.ask),float(r.ask),r.expiry),cap))
    return sorted(choices,key=lambda x:x[0])

def full_entry(year,events,calendar):
    digest=hashlib.sha256(pd.util.hash_pandas_object(events[['secid','entry_date']],index=False).to_numpy().tobytes()).hexdigest()[:12]
    path=CACHE/f'carry_entry_v3_{year}_{digest}.parquet'
    if path.exists():return pd.read_parquet(path),path
    legacy=CACHE/f'carry_entry_v2_{year}_{digest}.parquet'
    q=pd.read_parquet(legacy if legacy.exists() else CACHE/f'entry_chain_{year}.parquet')
    keys=events[['secid','entry_date']].rename(columns={'entry_date':'date'}).drop_duplicates()
    available=pd.MultiIndex.from_frame(q[['secid','date']].drop_duplicates())
    missing=keys[~pd.MultiIndex.from_frame(keys).isin(available)]
    if len(missing):
        extra=scan('D:/OptionMetrics/parquet',year,keys=missing)
        if len(extra):q=pd.concat([q,extra],ignore_index=True)
    q=q[pd.MultiIndex.from_frame(q[['secid','date']]).isin(pd.MultiIndex.from_frame(keys))].copy()
    q=unambiguous_quotes(q)
    sessions=pd.DatetimeIndex(calendar)
    previous=keys.copy();positions=sessions.searchsorted(previous.date)
    if (positions<=0).any():raise ValueError('full prior-session calendar required')
    previous['date']=sessions[positions-1]
    parts=[scan('D:/OptionMetrics/parquet',y,keys=previous[previous.date.dt.year==y]) for y in (year-1,year) if (previous.date.dt.year==y).any()]
    history=pd.concat([x for x in parts if len(x)],ignore_index=True) if any(len(x) for x in parts) else q.iloc[:0].copy()
    lag=history[['secid','date','optionid','volume','open_interest','delta','iv']].copy()
    loc=sessions.searchsorted(lag.date,side='right');good=loc<len(sessions)
    lag=lag.loc[good].copy();lag['date']=sessions[loc[good]]
    lag=lag.rename(columns={'volume':'prior_volume','open_interest':'prior_oi','delta':'prior_delta','iv':'prior_iv'})
    q=q.drop(columns=[c for c in ('prior_volume','prior_oi','prior_delta','prior_iv') if c in q])
    q=q.merge(lag,on=['secid','date','optionid'],how='left',validate='1:1')
    q.loc[q.date>pd.Timestamp('2000-11-28'),'prior_oi']=q.loc[q.date>pd.Timestamp('2000-11-28'),'open_interest']
    q['standard_contract']=q.ss_flag.astype(str).eq('0')&np.isfinite(q.cfadj)&(q.cfadj>0)
    q.to_parquet(path,index=False)
    return q,path


def prepare(all_events,benchmark,fee,calendar,intrinsic=False):
    ids_by_year={};entries={};events=[]
    for year in range(1999,2011):
        e=features(year,all_events)
        e=e[(e.reaction>=.03)&np.isfinite(e.spot)&(e.spot>0)&(e.entry_date<=benchmark.date.iloc[-51])].copy()
        entry,entry_path=full_entry(year,e,calendar)
        groups={k:g for k,g in entry.groupby(['secid','date'],sort=False)}
        ids=set();valid=[]
        for row in e.itertuples():
            ch=groups.get((row.secid,row.entry_date))
            if ch is None:continue
            choices=menu(ch,row,fee,intrinsic)
            if choices:valid.append(row.event_id);ids.update(x[1].legs[0].optionid for x in choices)
        e=e[e.event_id.isin(valid)].copy();events.append(e);entries[year]=entry_path;ids_by_year[year]=ids
        print(f'{year}: carry entry menus {len(e):,} events / {len(ids):,} contracts',flush=True)
    paths={}
    for year in range(1999,2011):
        ids=ids_by_year[year]|ids_by_year.get(year-1,set())
        digest=hashlib.sha256(','.join(map(str,sorted(ids))).encode()).hexdigest()[:12]
        path=CACHE/f'carry_marks_lagged_{year}_{digest}.parquet';paths[year]=path
        if path.exists():continue
        entry=pd.read_parquet(entries[year])
        q=scan('D:/OptionMetrics/parquet',year,optionids=ids)
        if q.empty:
            q=entry.iloc[:0].assign(underlying_price=pd.Series(dtype=float))
        else:
            q=q.merge(entry[['secid','date','optionid','prior_volume','prior_oi','prior_delta','prior_iv']],on=['secid','date','optionid'],how='left',validate='1:1')
            spot=pq.read_table(f'D:/OptionMetrics/parquet/secprd{year}.parquet',columns=['secid','date','close'],filters=[('secid','in',q.secid.unique().tolist())]).to_pandas()
            spot['date']=pd.to_datetime(spot.date);spot['underlying_price']=spot.close.abs()
            q=q.merge(spot[['secid','date','underlying_price']],on=['secid','date'],how='left',validate='m:1')
            q['standard_contract']=q.ss_flag.astype(str).eq('0')&np.isfinite(q.cfadj)&(q.cfadj>0)
        q.to_parquet(path,index=False)
        print(f'{year}: cached {len(q):,} actual carry marks including prior-year holdings',flush=True)
    return pd.concat(events,ignore_index=True),paths

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--market-gate',action='store_true');parser.add_argument('--double-fee',action='store_true');parser.add_argument('--cash-sweep',action='store_true');parser.add_argument('--intrinsic',action='store_true')
    args=parser.parse_args();suffix=('_trend' if args.market_gate else '')+('_double_fee' if args.double_fee else '')+('_cash' if args.cash_sweep else '')
    family='selective_earnings_carry_intrinsic' if args.intrinsic else 'selective_earnings_carry_lagged'
    out=ROOT/f'results/{family}{suffix}';out.mkdir(parents=True,exist_ok=True)
    (out/'source_versions.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},indent=2))
    protocol=dict(metrics_risk_free='official French daily RF',cash_interest_proxy=args.cash_sweep,prior_session_greeks=not args.intrinsic,delta_is_risk_upper_bound=args.intrinsic,moneyness_floor=1.20 if args.intrinsic else 1.05,price_floor_prior_intrinsic_pct=.01 if args.intrinsic else None,liquidity=asdict(LIQ),car_floors=POLICIES,max_tickets=8,hold_sessions=50,dte=[120,270],cost_per_stock_delta_limit=.01,
        ticket_risk=.30,issuer_admission_risk=.30,sector_admission_risk=.50,cohort_admission_risk=.50,aggregate_marked_risk=.80,
        stock_exposure_admission_bound=2.,continuous_across_years=True,market_gate=args.market_gate,commission=1.30 if args.double_fee else .65)
    (out/'frozen_policy.json').write_text(json.dumps(protocol,indent=2))
    all_events=pd.read_parquet(OLD/'all_events.parquet')
    full=pd.read_parquet(OLD/'benchmark.parquet');b=full[full.date.dt.year.between(1999,2010)].reset_index(drop=True)
    e,paths=prepare(all_events,b,protocol['commission'],full.date,args.intrinsic)
    b=cash_sweep(b)
    if not args.cash_sweep:b['cash_rf']=0.
    if args.market_gate:
        b=b.merge(signals()[['date','trend_sign']],on='date',validate='1:1');b['risk_off']=b.trend_sign<0
    actions=action_calendar(full.date);actions.to_csv(out/'action_calendar.csv',index=False)
    rows=[]
    for name,floor in POLICIES.items():
        base=e[e.reaction>=floor].copy();base['source_event_id']=base.event_id
        base['assignment_verified']=True;base['portfolio_stock_limit']=2.
        if args.market_gate:base=base[base.entry_date.isin(b.loc[~b.risk_off,'date'])]
        tickets=pd.concat([base.assign(event_id=base.event_id+f'__ticket_{slot}') for slot in range(8)],ignore_index=True)
        for capital in (100000,1000000):
            label=f'{name}_{capital//1000}k'
            cfg=Config(initial_nav=capital,strict=False,hold_sessions=50,expiry_buffer_days=14,sleeves=('directional',),
                require_price_edge=False,min_adv=5000000,directional_risk=.30,issuer_risk=.30,sector_risk=.50,
                aggregate_risk=.80,cohort_risk=.50,current_risk_budget=True,defer_unquoted_exits=True,commission=protocol['commission'])
            source=YearQuoteStore(lambda year:pd.read_parquet(paths[year]))
            memo={};memo_date=None
            def choose(ch,sleeve,direction,event,ctx):
                nonlocal memo_date
                if memo_date!=ctx['date']:memo.clear();memo_date=ctx['date']
                key=event.source_event_id
                if key not in memo:memo[key]=menu(ch,event,ctx['commission'],args.intrinsic)
                return select_menu(memo[key],ctx,event)
            result=run(tickets,source,b,cfg,selector=choose,selector_context=True,corporate_actions=actions)
            d,t=result.daily,result.trades
            folder=out/label;folder.mkdir(exist_ok=True)
            d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False);result.skips.to_csv(folder/'skips.csv.gz',index=False)
            report=dict(protocol=protocol,starting_nav=capital,diagnostics=result.diagnostics,periods={},investment_accepted=False)
            for period,start,end in [('discovery',1999,2004),('validation',2005,2007),('development_test',2008,2010),('all_development',1999,2010)]:
                sub=d[d.date.dt.year.between(start,end)];stats=evaluate(sub,False,True,repetitions=500)
                tt=t[t.exit_date.dt.year.between(start,end)] if len(t) else t
                count=int(tt.source_event_id.nunique()) if len(tt) else 0
                stats['trade_count']=count;stats['closed_ticket_count']=len(tt);report['periods'][period]=stats
                rows.append(dict(scenario=label,capital=capital,period=period,cagr=stats['strategy']['cagr'],sharpe=stats['strategy']['sharpe'],drawdown=stats['strategy']['max_drawdown'],spy_cagr=stats['benchmark']['cagr'],trade_count=count,closed_ticket_count=len(tt),mean_risk_nav=float((sub.reserved/sub.nav).mean())))
            (folder/'summary.json').write_text(json.dumps(report,indent=2))
            print(f'{label}: carry final NAV {d.nav.iloc[-1]:,.2f}',flush=True)
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    selected={}
    for cap in (100000,1000000):
        val=table[(table.period=='validation')&(table.capital==cap)&(table.trade_count>=20)&(table.drawdown>=-.30)]
        selected[str(cap)]=val.sort_values(['sharpe','scenario'],ascending=[False,True]).iloc[0].scenario if len(val) else None
    (out/'validation_selection.json').write_text(json.dumps(dict(selected_by_validation=selected,final_holdout_evaluated=False,investment_accepted=False),indent=2))
    print(table.to_string(index=False),flush=True)

if __name__=='__main__':main()
