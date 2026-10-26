"""Positive earnings price discovery expressed through fully paid ITM calls."""
from pathlib import Path
import json,hashlib,argparse
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .cash_puts import features
from .index_research import signals
from .corporate_actions import action_calendar
from .prepare_v2 import ROOT,CACHE,OLD
from .data import scan
from .structures import Leg,Structure
from .liquidity_v2 import LiquidityPolicy,eligible
from .engine import Config,run
from .validation import evaluate

POLICIES={'car3':.03,'car5':.05,'car8':.08}
LIQ=LiquidityPolicy(min_oi=50,max_relative_spread=.15,max_absolute_spread=.50,participation=.05,oi_participation=.01,small_lot_floor=1)

def menu(chain,event,fee=.65):
    c=eligible(chain,LIQ);dte=(c.expiry-c.date).dt.days
    c=c[(c.cp=='C')&np.isfinite(c.iv)&c.iv.between(.02,3.)&dte.between(70,180)&(c.delta>=.75)&(event.spot/c.strike>=1.05)&(c.ask<=event.spot)].copy()
    choices=[]
    for r in c.itertuples():
        cap=max(0,int(min(np.floor(LIQ.oi_participation*r.prior_oi),max(LIQ.small_lot_floor,np.floor(LIQ.participation*r.prior_volume)))))
        if cap<1:continue
        extrinsic=max(0.,r.ask-max(0.,event.spot-r.strike))
        cost=(r.ask-r.bid+2*fee/100+extrinsic*20/((r.expiry-r.date).days*252/365))/(r.delta*event.spot)
        key=(cost,abs((r.expiry-r.date).days-120),int(r.optionid))
        leg=Leg(int(r.optionid),1,'C',float(r.strike),r.expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
        choices.append((key,Structure('directional',(leg,),-float(r.ask),float(r.ask),r.expiry),cap))
    return sorted(choices,key=lambda x:x[0])

def prepare(year,e):
    entry=pd.read_parquet(CACHE/f'entry_chain_{year}.parquet')
    entry['standard_contract']=entry.ss_flag.astype(str).eq('0')&np.isfinite(entry.cfadj)&(entry.cfadj>0)
    groups={k:g for k,g in entry.groupby(['secid','date'],sort=False)}
    lookup={};ids=set()
    for row in e.itertuples():
        if row.reaction<.03 or not np.isfinite(row.spot) or row.spot<=0:continue
        chain=groups.get((row.secid,row.entry_date))
        if chain is None:continue
        choices=menu(chain,row)
        if choices:lookup[row.event_id]=choices;ids.update(x[1].legs[0].optionid for x in choices)
    digest=hashlib.sha256(','.join(map(str,sorted(ids))).encode()).hexdigest()[:12]
    path=CACHE/f'earnings_call_marks_{year}_{digest}.parquet'
    if path.exists():q=pd.read_parquet(path)
    elif ids:
        q=scan('D:/OptionMetrics/parquet',year,optionids=ids)
        q=q.merge(entry[['secid','date','optionid','prior_volume','prior_oi']],on=['secid','date','optionid'],how='left',validate='1:1')
        spot=pq.read_table(f'D:/OptionMetrics/parquet/secprd{year}.parquet',columns=['secid','date','close'],filters=[('secid','in',q.secid.unique().tolist())]).to_pandas()
        spot['date']=pd.to_datetime(spot.date);spot['underlying_price']=spot.close.abs()
        q=q.merge(spot[['secid','date','underlying_price']],on=['secid','date'],how='left',validate='m:1')
        q['standard_contract']=q.ss_flag.astype(str).eq('0')&np.isfinite(q.cfadj)&(q.cfadj>0)
        q.to_parquet(path,index=False)
    else:q=entry.iloc[:0].assign(underlying_price=pd.Series(dtype=float))
    return lookup,q

def select_menu(choices,ctx,event=None):
    for key,st,raw in choices:
        used=ctx['used_lots'].get((ctx['date'],st.legs[0].optionid),0)
        if event is not None and getattr(event,'portfolio_stock_limit',0)>0:
            if not ctx.get('stock_notional_complete',False):return None
            remaining=max(0.,event.portfolio_stock_limit*ctx['nav']-ctx['open_stock_notional_bound'])
            raw=min(raw,used+int(remaining//(100*event.spot)))
        unit=st.max_loss*100+2*ctx['commission']
        if min(int(ctx['risk_budget']//unit),int(ctx['free_cash']//unit),raw-used)>=1:return st,0.,raw
    return None


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--funding-stress',action='store_true');parser.add_argument('--market-gate',action='store_true');parser.add_argument('--concentrated',action='store_true');parser.add_argument('--action-aware',action='store_true');args=parser.parse_args()
    label_suffix=('_concentrated_basis_fix' if args.concentrated else '_funded' if args.funding_stress else '')+('_trend' if args.market_gate else '')+('_actions' if args.action_aware else '')
    out=ROOT/f'results/selective_earnings_long_calls{label_suffix}';out.mkdir(parents=True,exist_ok=True)
    (out/'source_versions.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},indent=2))
    (out/'frozen_policies.json').write_text(json.dumps(POLICIES,indent=2))
    all_events=pd.read_parquet(OLD/'all_events.parquet');benchmark=pd.read_parquet(OLD/'benchmark.parquet')
    if args.market_gate:benchmark=benchmark.merge(signals()[['date','trend_sign']],on='date',how='left',validate='1:1')
    actions=action_calendar(benchmark.date) if args.action_aware else None
    if actions is not None:actions.to_csv(out/'action_calendar.csv',index=False)
    scenarios={f'{name}_{cap//1000}k':(name,cap) for name in POLICIES for cap in (100000,1000000)}
    nav={k:v[1] for k,v in scenarios.items()};daily={k:[] for k in scenarios};trades={k:[] for k in scenarios};skips={k:[] for k in scenarios};diags={k:[] for k in scenarios}
    for year in range(1999,2011):
        b=benchmark[benchmark.date.dt.year==year].reset_index(drop=True)
        if args.market_gate:b['risk_off']=b.trend_sign<0
        e=features(year,all_events);e=e[(e.entry_date<=b.date.iloc[-22])&(e.reaction>=.03)].copy()
        lookup,q=prepare(year,e)
        e['assignment_verified']=True
        if args.concentrated:e['portfolio_stock_limit']=2.
        for label,(name,cap) in scenarios.items():
            events=e[(e.reaction>=POLICIES[name])&e.event_id.isin(lookup)].copy()
            if args.market_gate:events=events[events.entry_date.isin(b.loc[~b.risk_off,'date'])]
            cfg=Config(initial_nav=nav[label],strict=False,hold_sessions=20,expiry_buffer_days=7,sleeves=('directional',),
                require_price_edge=False,min_adv=5_000_000,directional_risk=.30 if args.concentrated else .06 if args.funding_stress else .03,
                issuer_risk=.30 if args.concentrated else .08 if args.funding_stress else .05,sector_risk=.50 if args.concentrated else .30 if args.funding_stress else .25,
                aggregate_risk=.80 if args.concentrated else .75 if args.funding_stress else .60,cohort_risk=.50 if args.concentrated else .25 if args.funding_stress else .15,
                current_risk_budget=args.funding_stress or args.concentrated,defer_unquoted_exits=True)
            def choose(chain,sleeve,direction,event,ctx):
                return select_menu(lookup.get(event.event_id,[]),ctx,event)
            r=run(events,q,b,cfg,selector=choose,selector_context=True,corporate_actions=actions)
            nav[label]=float(r.daily.nav.iloc[-1]);daily[label].append(r.daily);trades[label].append(r.trades);skips[label].append(r.skips);diags[label].append(r.diagnostics)
        print(f'{year}: earnings long-call NAVs '+json.dumps(nav),flush=True)
    rows=[]
    for label,(name,cap) in scenarios.items():
        folder=out/label;folder.mkdir(exist_ok=True)
        d=pd.concat(daily[label],ignore_index=True);t=pd.concat(trades[label],ignore_index=True)
        d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
        pd.concat(skips[label],ignore_index=True).to_csv(folder/'skips.csv.gz',index=False)
        report=dict(car_floor=POLICIES[name],funding_stress=args.funding_stress,market_gate=args.market_gate,concentrated=args.concentrated,action_aware=args.action_aware,starting_nav=cap,source_universe_verified=False,diagnostics=diags[label],periods={})
        for period,start,end in [('discovery',1999,2004),('validation',2005,2007),('development_test',2008,2010),('all_development',1999,2010)]:
            sub=d[d.date.dt.year.between(start,end)];stats=evaluate(sub,False,True,repetitions=500)
            count=int(t.loc[t.exit_date.dt.year.between(start,end),'event_id'].nunique()) if len(t) else 0
            stats['trade_count']=count;report['periods'][period]=stats
            rows.append(dict(scenario=label,capital=cap,period=period,cagr=stats['strategy']['cagr'],sharpe=stats['strategy']['sharpe'],drawdown=stats['strategy']['max_drawdown'],spy_cagr=stats['benchmark']['cagr'],trade_count=count,mean_risk_nav=float((sub.reserved/sub.nav).mean())))
        (folder/'summary.json').write_text(json.dumps(report,indent=2))
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    selected={}
    for cap in (100000,1000000):
        val=table[(table.period=='validation')&(table.capital==cap)&(table.trade_count>=20)&(table.drawdown>=-.30)]
        selected[str(cap)]=val.sort_values(['sharpe','scenario'],ascending=[False,True]).iloc[0].scenario if len(val) else None
    (out/'validation_selection.json').write_text(json.dumps(dict(selected_by_validation=selected,final_holdout_evaluated=False,investment_accepted=False),indent=2))
    print(table.to_string(index=False),flush=True)

if __name__=='__main__':main()
