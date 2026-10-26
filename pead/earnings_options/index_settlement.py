"""Earnings-qualified monthly SPX credit ladder held to native SET cash settlement.

All profiles are development experiments. No early exit is promised: the complete
maximum loss is cash secured and remains exposed after a signal turns off.
"""
from dataclasses import asdict,dataclass,replace
import argparse,hashlib,json
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .index_research import ROOT,CACHE,SPX,signals,index_quotes,LIQUIDITY,lagged_liquidity
from .index_directional import cash_sweep
from .liquidity_v2 import eligible,capacity
from .structures import Leg,Structure
from .data import scan
from .engine import Config,run
from .quote_store import YearQuoteStore
from .validation import evaluate


@dataclass(frozen=True)
class Policy:
    ticket:float
    cohort:float
    aggregate:float


POLICIES={'moderate':Policy(.10,.20,.40),'deployed':Policy(.30,.30,.80),
          'concentrated':Policy(.60,.60,.80)}


def settlement_calendar(calendar,observations):
    """Use third-Friday SOQ, preceding exchange session for a holiday.

    Native observations can include SOQ values on other days; those are never
    substituted for a missing monthly resolution. Missing monthly data aborts.
    """
    cal=pd.DatetimeIndex(calendar)
    obs=observations.copy();obs['date']=pd.to_datetime(obs.date)
    if obs.date.duplicated().any():raise ValueError('duplicate settlement observation')
    values=obs.set_index('date')['close'];rows=[]
    for month in pd.date_range(cal.min().replace(day=1),cal.max(),freq='MS'):
        fifteenth=month.replace(day=15)
        friday=fifteenth+pd.Timedelta(days=(4-fifteenth.day_of_week)%7)
        if friday<cal.min() or friday>cal.max():continue
        day=cal[cal.searchsorted(friday,side='right')-1]
        value=float(values.get(day,np.nan))
        if not np.isfinite(value) or value<=0:raise ValueError(f'missing native settlement {day}')
        rows.append(dict(secid=SPX,expiry=friday+pd.Timedelta(days=1),settlement_date=day,value=value))
    return pd.DataFrame(rows)


def load_quotes(year,calendar):
    path=CACHE/f'spx_quotes_prior_v1_{year}.parquet'
    if path.exists():return pd.read_parquet(path)
    q=index_quotes(year,calendar).copy()
    sessions=pd.DatetimeIndex(calendar)
    first=q.date.min();idx=sessions.searchsorted(first)
    if idx<1:raise ValueError('prior-session calendar missing')
    previous=sessions[idx-1]
    keys=pd.DataFrame(dict(date=[previous],secid=[SPX]))
    prior=scan('D:/OptionMetrics/parquet',previous.year,keys=keys)
    q=lagged_liquidity(pd.concat([prior,q],ignore_index=True),calendar)
    q=q.sort_values(['optionid','date'])
    group=q.groupby('optionid',sort=False)
    sessions=pd.DatetimeIndex(calendar)
    previous=sessions[np.maximum(sessions.searchsorted(q.date)-1,0)]
    aligned=pd.to_datetime(group.date.shift()).to_numpy()==previous.to_numpy()
    q['prior_delta']=np.where(aligned,group.delta.shift(),np.nan)
    result=q[q.date.dt.year==year].copy()
    result.to_parquet(path,index=False)
    return result


def credit_menu(chain,commission,liquidity=LIQUIDITY):
    ch=chain.copy();ch['delta']=ch.prior_delta
    c=eligible(ch,liquidity)
    monthly=(c.expiry.dt.day_of_week==5)&c.expiry.dt.day.between(16,22)
    c=c[monthly&(c.cp=='P')&(c.delta<0)&(c.expiry-c.date).dt.days.between(35,75)]
    choices=[]
    for expiry,g in c.groupby('expiry',sort=True):
        shorts=g.assign(distance=(g.delta.abs()-.35).abs()).sort_values(['distance','optionid']).head(6)
        longs=g.assign(distance=(g.delta.abs()-.15).abs()).sort_values(['distance','optionid']).head(6)
        for sr in shorts.itertuples():
            for lr in longs.itertuples():
                if sr.strike<=lr.strike or abs(abs(sr.delta)-.35)>.10 or abs(abs(lr.delta)-.15)>.08:continue
                credit=sr.bid-lr.ask;width=sr.strike-lr.strike
                if not 0<credit<width:continue
                loss=width-credit
                friction=(sr.ask-sr.bid+lr.ask-lr.bid+4*commission/100)/loss
                if friction>.15:continue
                cap=capacity(g[g.optionid.isin([sr.optionid,lr.optionid])],liquidity)
                if cap<1:continue
                legs=tuple(Leg(int(r.optionid),qty,'P',float(r.strike),expiry,
                    float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
                    for r,qty in ((lr,1),(sr,-1)))
                key=(abs(abs(sr.delta)-.35)+abs(abs(lr.delta)-.15),
                     abs((expiry-sr.date).days-45),friction,int(sr.optionid),int(lr.optionid))
                choices.append((key,Structure('directional',legs,credit,loss,expiry),cap))
    return sorted(choices,key=lambda x:x[0])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--risk-exit',action='store_true')
    parser.add_argument('--double-fee',action='store_true')
    parser.add_argument('--zero-floor',action='store_true')
    args=parser.parse_args()
    suffix=('_risk_exit' if args.risk_exit else '')+('_double_fee' if args.double_fee else '')+('_zero_floor' if args.zero_floor else '')
    out=ROOT/f'results/selective_earnings_index_settlement{suffix}';out.mkdir(parents=True,exist_ok=True)
    liquidity=replace(LIQUIDITY,small_lot_floor=0) if args.zero_floor else LIQUIDITY
    commission=3. if args.double_fee else 1.50
    protocol=dict(profiles={k:asdict(v) for k,v in POLICIES.items()},signal='prior trend and earnings breadth positive; RV <=30%',
        dte=[35,75],target_dte=45,short_delta=.35,long_delta=.15,delta_source='previous session',tickets_per_week=8,
        liquidity=asdict(liquidity),commission_per_leg_each_side=commission,cash_sweep=True,
        staged_risk_exit=args.risk_exit,exit_rule='prior trend below200day or RV>30%',
        settlement='native SET SECID118963; all144 monthly SOQs; exact cash payoff',
        borrowing=False,final_holdout_evaluated=False,investment_accepted=False)
    (out/'frozen_policy.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    (out/'source_versions.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in __import__('pathlib').Path(__file__).parent.glob('*.py')},indent=2),encoding='utf-8')
    b=cash_sweep(signals());calendar=pd.read_parquet(ROOT/'data/selective_earnings/benchmark.parquet').date
    obs=pd.concat([pq.read_table(f'D:/OptionMetrics/parquet/secprd{year}.parquet',
        filters=[('secid','=',118963)],columns=['date','close']).to_pandas() for year in range(1999,2011)])
    settled=settlement_calendar(b.date,obs);settled.to_csv(out/'native_settlements.csv',index=False)
    gate=b.weekly_entry&(b.trend_sign>0)&(b.breadth_sign>0)&b.rv.le(.30)
    # Fixed calendar cutoff allows all chosen 75DTE contracts to resolve in sample.
    entries=b[gate&(b.date<=b.date.iloc[-1]-pd.Timedelta(days=75))]
    if args.risk_exit:b['risk_off']=(b.trend_sign<0)|b.rv.gt(.30)
    e=pd.DataFrame(dict(event_id='SPX_'+entries.date.dt.strftime('%Y%m%d'),secid=SPX,sector='SP500',
        entry_date=entries.date,release_date=entries.previous_date,rank=.99,reaction=.01,rv=entries.rv,adv=1e15,
        timing_verified=True,consensus_verified=True,identity_verified=True,corporate_actions_verified=True,
        assignment_verified=True,earnings_calendar_not_applicable=True))
    e['source_event_id']=e.event_id
    tickets=pd.concat([e.assign(event_id=e.event_id+f'_ticket_{i}') for i in range(8)],ignore_index=True)
    memo={};rows=[]
    def selector(ch,sleeve,direction,event,ctx):
        day=ctx['date']
        if day not in memo:memo[day]=credit_menu(ch,ctx['commission'],liquidity)
        for key,st,cap in memo[day]:
            available=min(cap-ctx['used_lots'].get((ctx['date'],leg.optionid),0) for leg in st.legs)
            unit=st.max_loss*100+4*ctx['commission']
            if min(available,int(ctx['risk_budget']//unit),int(ctx['free_cash']//unit))>=1:
                return st,0.,cap
        return None
    def close_capacity(live,ctx):
        if not np.isfinite(live[['prior_oi','prior_volume']]).all().all():return 0
        caps=np.minimum(np.floor(liquidity.oi_participation*live.prior_oi),
            np.maximum(liquidity.small_lot_floor,np.floor(liquidity.participation*live.prior_volume)))
        return max(0,int(min(cap-ctx['used_lots'].get((ctx['date'],int(row.optionid)),0)
                             for cap,row in zip(caps,live.itertuples()))))
    for name,policy in POLICIES.items():
        for capital in (100000,1000000):
            label=f'{name}_{capital//1000}k'
            cfg=Config(initial_nav=capital,sleeves=('directional',),hold_sessions=9999,strict=False,
                require_price_edge=False,min_adv=0.,directional_risk=policy.ticket,
                issuer_risk=policy.aggregate,sector_risk=policy.aggregate,aggregate_risk=policy.aggregate,
                cohort_risk=policy.cohort,commission=commission,expiry_buffer_days=1,cash_settlement=True,
                settlement_risk_exit=args.risk_exit,defer_unquoted_exits=True)
            result=run(tickets,YearQuoteStore(lambda y:load_quotes(y,calendar)),b,cfg,
                selector=selector,selector_context=True,settlements=settled,exit_capacity=close_capacity if args.risk_exit else None)
            d,t=result.daily,result.trades;folder=out/label;folder.mkdir(exist_ok=True)
            d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
            result.skips.to_csv(folder/'skips.csv.gz',index=False)
            report=dict(protocol=protocol,policy=asdict(policy),diagnostics=result.diagnostics,
                        starting_nav=capital,investment_accepted=False,periods={})
            for period,start,end in [('discovery',1999,2004),('validation',2005,2007),
                                     ('development_test',2008,2010),('all_development',1999,2010)]:
                sub=d[d.date.dt.year.between(start,end)];stats=evaluate(sub,False,True,repetitions=500)
                tt=t[t.exit_date.dt.year.between(start,end)] if len(t) else t
                count=int(tt.source_event_id.nunique()) if len(tt) else 0
                stats['trade_count']=count;report['periods'][period]=stats
                rows.append(dict(scenario=label,capital=capital,period=period,
                    cagr=stats['strategy']['cagr'],sharpe=stats['strategy']['sharpe'],
                    drawdown=stats['strategy']['max_drawdown'],spy_cagr=stats['benchmark']['cagr'],
                    trade_count=count,closed_ticket_count=len(tt),mean_risk_nav=float((sub.reserved/sub.nav).mean())))
            (folder/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(f'{label}: settled final NAV {d.nav.iloc[-1]:,.2f}; {len(t)} tickets',flush=True)
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    selected={}
    for capital in (100000,1000000):
        val=table[(table.period=='validation')&(table.capital==capital)&(table.trade_count>=20)&(table.drawdown>=-.30)]
        selected[str(capital)]=val.sort_values(['sharpe','scenario'],ascending=[False,True]).iloc[0].scenario if len(val) else None
    (out/'validation_selection.json').write_text(json.dumps(dict(selected_by_validation=selected,
        final_holdout_evaluated=False,investment_accepted=False),indent=2),encoding='utf-8')
    print(table.to_string(index=False),flush=True)


if __name__=='__main__':main()
