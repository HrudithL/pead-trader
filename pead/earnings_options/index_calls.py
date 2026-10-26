"""Predeclared fully funded SPY stock-replacement calls; no short legs."""
from pathlib import Path
from dataclasses import dataclass,asdict,replace
import json,argparse
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .index_research import ROOT,CACHE,signals,lagged_liquidity
from .index_directional import cash_sweep
from .data import scan
from .structures import Leg,Structure
from .liquidity_v2 import LiquidityPolicy,eligible,capacity
from .engine import Config,run
from .validation import evaluate

SPY=109820
LIQ=LiquidityPolicy(min_oi=100,max_relative_spread=.10,max_absolute_spread=.50,participation=.05,oi_participation=.02,small_lot_floor=1)

@dataclass(frozen=True)
class CallPolicy:
    recovery:bool
    target:float
    risk:float
    capacity_selection:bool=False

POLICIES={f'{signal}_{target:g}x':CallPolicy(signal=='recovery',target,risk)
          for signal in ('trend','recovery') for target,risk in ((1.5,.45),(2.,.55),(3.,.70))}

def prepare_signals():
    b=signals().copy()
    full=pd.read_parquet(ROOT/'data/selective_earnings/benchmark.parquet')
    wealth=(1+full.benchmark).cumprod().shift(1)
    short=wealth>wealth.rolling(50,min_periods=50).mean()
    b=b.merge(pd.DataFrame(dict(date=full.date,above50=short)),on='date',validate='1:1')
    path=CACHE/'spy_spot_1998_2010.parquet'
    if path.exists():s=pd.read_parquet(path)
    else:
        parts=[pq.read_table(f'D:/OptionMetrics/parquet/secprd{y}.parquet',columns=['secid','date','close'],filters=[('secid','=',SPY)]).to_pandas() for y in range(1998,2011)]
        s=pd.concat(parts,ignore_index=True).sort_values('date');s['date']=pd.to_datetime(s.date)
        if s.date.duplicated().any():raise ValueError('duplicate native SPY price')
        s['underlying_price']=s.close.abs();s['spot']=s.underlying_price.shift(1)
        s.to_parquet(path,index=False)
    b=b.merge(s[['date','spot']],on='date',how='left',validate='1:1')
    if not np.isfinite(b.spot).all() or (b.spot<=0).any():raise ValueError('missing SPY spot')
    return b,s

def quotes(year,calendar,spot):
    path=CACHE/f'spy_calls_{year}.parquet'
    if path.exists():return known_quote_inputs(pd.read_parquet(path),spot)
    dates=pd.DatetimeIndex(calendar);dates=dates[dates.year==year]
    q=scan('D:/OptionMetrics/parquet',year,keys=pd.DataFrame(dict(date=dates,secid=SPY)))
    q=q[(q.cp=='C')&((q.expiry-q.date).dt.days<=365)].copy()
    q=lagged_liquidity(q,calendar)
    q=q.merge(spot[['date','underlying_price']],on='date',how='left',validate='m:1')
    q['delta_is_upper_bound']=q.delta.isna()&(q.underlying_price/q.strike>=1.15)
    q.loc[q.delta_is_upper_bound,'delta']=1.
    if not np.isfinite(q.underlying_price).all():raise ValueError('missing mark underlying')
    q.to_parquet(path,index=False)
    print(f'{year}: cached {len(q):,} native SPY call quotes',flush=True)
    return known_quote_inputs(q,spot)

def known_quote_inputs(q,spot):
    q=q.copy()
    q['prior_underlying_price']=q.date.map(spot.set_index('date').spot)
    q.loc[q.delta_is_upper_bound,'delta']=np.nan
    q['delta_is_upper_bound']=q.delta.isna()&(q.prior_underlying_price/q.strike>=1.15)
    q.loc[q.delta_is_upper_bound,'delta']=1.
    return q

def select_call(chain,policy,event,ctx):
    if not ctx['greeks_complete']:return None
    remaining=policy.target*ctx['nav']-max(0.,ctx['open_delta_units'])*event.spot
    if remaining<=0:return None
    c=eligible(chain,LIQ)
    dte=(c.expiry-c.date).dt.days
    c=c[dte.between(180,365)&(c.delta>=.85)&(c.ask<=c.prior_underlying_price)&(c.prior_underlying_price/c.strike>=1.15)].copy()
    if c.empty:return None
    c['distance']=(c.delta-.95).abs();c['maturity']=((c.expiry-c.date).dt.days-270).abs()
    choices=[]
    for r in c.sort_values(['distance','maturity','optionid']).itertuples():
        raw=max(0,int(min(np.floor(LIQ.oi_participation*r.prior_oi),max(LIQ.small_lot_floor,np.floor(LIQ.participation*r.prior_volume)))))
        used=ctx['used_lots'].get((ctx['date'],int(r.optionid)),0)
        # Upper-bound Greeks overstate rather than conceal delta exposure.
        exposure_lots=int((remaining+.10*policy.target*ctx['nav'])//(100*event.spot*r.delta))
        available=min(raw-used,exposure_lots)
        unit=r.ask*100+2*ctx['commission']
        lots=min(int(ctx['risk_budget']//unit),int(ctx['free_cash']//unit),available)
        if lots<1:continue
        leg=Leg(int(r.optionid),1,'C',float(r.strike),r.expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
        choice=Structure('directional',(leg,),-float(r.ask),float(r.ask),r.expiry),0.,used+available
        if not policy.capacity_selection:return choice
        score=(-lots*100*event.spot*r.delta,(r.ask-r.bid+2*ctx['commission']/100)/max(.01,r.delta*event.spot),abs(r.delta-.95),r.optionid)
        choices.append((score,choice))
    return min(choices,key=lambda x:x[0])[1] if choices else None

def run_year(frame,q,policy,nav,fee=1.):
    b=frame.copy();bull=b.trend_sign>0
    if policy.recovery:bull=bull|(b.above50&(b.breadth_sign>0))
    bull=bull&(b.rv<=.60)&np.isfinite(b.rv)
    b['risk_off']=~bull;b['direction_signal']=bull.astype(int)
    entries=b[b.weekly_entry&bull&(b.date<b.date.iloc[-5])].copy()
    base=pd.DataFrame(dict(event_id='SPY_'+entries.date.dt.strftime('%Y%m%d'),secid=SPY,sector='SP500',
        entry_date=entries.date,release_date=entries.previous_date,rank=.5,reaction=0.,rv=entries.rv,
        adv=1e15,spot=entries.spot,trade_direction=1,timing_verified=True,consensus_verified=True,
        identity_verified=True,corporate_actions_verified=True,assignment_verified=True,
        schedule_known=False,earnings_calendar_not_applicable=True,scheduled_date=pd.NaT))
    e=pd.concat([base.assign(event_id=base.event_id+f'_{slot}') for slot in range(4)],ignore_index=True)
    cfg=Config(initial_nav=nav,strict=False,hold_sessions=63,expiry_buffer_days=30,commission=fee,
        sleeves=('directional',),require_price_edge=False,min_adv=0.,directional_risk=policy.risk,
        aggregate_risk=policy.risk,issuer_risk=policy.risk,sector_risk=policy.risk,cohort_risk=policy.risk,current_risk_budget=True,defer_unquoted_exits=True)
    def choose(ch,sleeve,direction,event,context):return select_call(ch,policy,event,context)
    return run(e,q,b,cfg,selector=choose,selector_context=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cash-sweep',action='store_true');parser.add_argument('--double-fee',action='store_true');parser.add_argument('--capacity-selection',action='store_true')
    args=parser.parse_args();policies={k:replace(v,capacity_selection=args.capacity_selection) for k,v in POLICIES.items()};suffix=('_capacity' if args.capacity_selection else '')+('_cash' if args.cash_sweep else '')+('_double_fee' if args.double_fee else '')
    out=ROOT/f'results/selective_earnings_calls_deferred{suffix}';out.mkdir(parents=True,exist_ok=True)
    (out/'source_versions.json').write_text(json.dumps({p.name:__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},indent=2))
    (out/'frozen_policies.json').write_text(json.dumps({k:asdict(v) for k,v in policies.items()},indent=2))
    b,s=prepare_signals()
    if args.cash_sweep:b=cash_sweep(b)
    calendar=pd.read_parquet(ROOT/'data/selective_earnings/benchmark.parquet').date
    scenarios={f'{name}_{cap//1000}k':(name,cap) for name in policies for cap in (100_000,1_000_000)}
    nav={k:v[1] for k,v in scenarios.items()};daily={k:[] for k in scenarios};trades={k:[] for k in scenarios};diags={k:[] for k in scenarios};skips={k:[] for k in scenarios}
    for year in range(2005,2011):
        q=quotes(year,calendar,s);frame=b[b.date.dt.year==year].copy().reset_index(drop=True)
        for label,(name,cap) in scenarios.items():
            r=run_year(frame,q,policies[name],nav[label],2. if args.double_fee else 1.)
            nav[label]=float(r.daily.nav.iloc[-1]);daily[label].append(r.daily);trades[label].append(r.trades);diags[label].append(r.diagnostics);skips[label].append(r.skips)
        print(f'{year}: call NAVs '+json.dumps(nav),flush=True)
    rows=[]
    for label,(name,cap) in scenarios.items():
        folder=out/label;folder.mkdir(exist_ok=True)
        d=pd.concat(daily[label],ignore_index=True);t=pd.concat(trades[label],ignore_index=True)
        d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
        pd.concat(skips[label],ignore_index=True).to_csv(folder/'skips.csv.gz',index=False)
        report=dict(policy=asdict(policies[name]),starting_nav=cap,cash_sweep_proxy=args.cash_sweep,diagnostics=diags[label],periods={})
        for period,start,end in [('validation',2005,2007),('development_test',2008,2010),('all_development',2005,2010)]:
            sub=d[d.date.dt.year.between(start,end)];stats=evaluate(sub,False,True,repetitions=500)
            count=int(t.loc[t.exit_date.dt.year.between(start,end),'event_id'].nunique()) if len(t) else 0
            stats['trade_count']=count;stats['cash_interest']=float(sub.cash_interest.sum());report['periods'][period]=stats
            rows.append(dict(scenario=label,capital=cap,period=period,cagr=stats['strategy']['cagr'],sharpe=stats['strategy']['sharpe'],drawdown=stats['strategy']['max_drawdown'],spy_cagr=stats['benchmark']['cagr'],trade_count=count,mean_risk_nav=float((sub.reserved/sub.nav).mean()),cash_interest=stats['cash_interest']))
        (folder/'summary.json').write_text(json.dumps(report,indent=2))
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    selected={}
    for cap in (100_000,1_000_000):
        val=table[(table.period=='validation')&(table.capital==cap)&(table.trade_count>=20)&(table.drawdown>=-.30)]
        selected[str(cap)]=val.sort_values(['sharpe','scenario'],ascending=[False,True]).iloc[0].scenario if len(val) else None
    (out/'validation_selection.json').write_text(json.dumps(dict(selected_by_validation=selected,final_holdout_evaluated=False,investment_accepted=False),indent=2))
    print(table.to_string(index=False),flush=True)

if __name__=='__main__':main()
