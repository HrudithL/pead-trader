"""Earnings-qualified monthly SPX credit ladder held to native SET cash settlement.

All profiles are development experiments. No early exit is promised: the complete
maximum loss is cash secured and remains exposed after a signal turns off.
"""
from dataclasses import asdict,dataclass
import hashlib,json
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .index_research import ROOT,CACHE,SPX,signals,index_quotes,LIQUIDITY
from .index_directional import cash_sweep
from .liquidity_v2 import eligible,capacity
from .structures import Leg,Structure
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
    q=index_quotes(year,calendar).sort_values(['optionid','date']).copy()
    group=q.groupby('optionid',sort=False)
    sessions=pd.DatetimeIndex(calendar)
    previous=sessions[np.maximum(sessions.searchsorted(q.date)-1,0)]
    aligned=pd.to_datetime(group.date.shift()).to_numpy()==previous.to_numpy()
    q['prior_delta']=np.where(aligned,group.delta.shift(),np.nan)
    return q


def credit_menu(chain,commission):
    ch=chain.copy();ch['delta']=ch.prior_delta
    c=eligible(ch,LIQUIDITY)
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
                cap=capacity(g[g.optionid.isin([sr.optionid,lr.optionid])],LIQUIDITY)
                if cap<1:continue
                legs=tuple(Leg(int(r.optionid),qty,'P',float(r.strike),expiry,
                    float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
                    for r,qty in ((lr,1),(sr,-1)))
                key=(abs(abs(sr.delta)-.35)+abs(abs(lr.delta)-.15),
                     abs((expiry-sr.date).days-45),friction,int(sr.optionid),int(lr.optionid))
                choices.append((key,Structure('directional',legs,credit,loss,expiry),cap))
    return sorted(choices,key=lambda x:x[0])


def main():
    out=ROOT/'results/selective_earnings_index_settlement';out.mkdir(parents=True,exist_ok=True)
    protocol=dict(profiles={k:asdict(v) for k,v in POLICIES.items()},signal='prior trend and earnings breadth positive; RV <=30%',
        dte=[35,75],target_dte=45,short_delta=.35,long_delta=.15,delta_source='previous session',tickets_per_week=8,
        liquidity=asdict(LIQUIDITY),commission_per_leg_each_side=1.50,cash_sweep=True,
        settlement='native SET SECID118963; all144 monthly SOQs; no early exit',
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
    e=pd.DataFrame(dict(event_id='SPX_'+entries.date.dt.strftime('%Y%m%d'),secid=SPX,sector='SP500',
        entry_date=entries.date,release_date=entries.previous_date,rank=.99,reaction=.01,rv=entries.rv,adv=1e15,
        timing_verified=True,consensus_verified=True,identity_verified=True,corporate_actions_verified=True,
        assignment_verified=True,earnings_calendar_not_applicable=True))
    e['source_event_id']=e.event_id
    tickets=pd.concat([e.assign(event_id=e.event_id+f'_ticket_{i}') for i in range(8)],ignore_index=True)
    memo={};memo_day=None;rows=[]
    def selector(ch,sleeve,direction,event,ctx):
        nonlocal memo_day
        if memo_day!=ctx['date']:memo.clear();memo_day=ctx['date']
        if 'menu' not in memo:memo['menu']=credit_menu(ch,ctx['commission'])
        for key,st,cap in memo['menu']:
            available=min(cap-ctx['used_lots'].get((ctx['date'],leg.optionid),0) for leg in st.legs)
            unit=st.max_loss*100+4*ctx['commission']
            if min(available,int(ctx['risk_budget']//unit),int(ctx['free_cash']//unit))>=1:
                return st,0.,cap
        return None
    for name,policy in POLICIES.items():
        for capital in (100000,1000000):
            label=f'{name}_{capital//1000}k';memo.clear();memo_day=None
            cfg=Config(initial_nav=capital,sleeves=('directional',),hold_sessions=9999,strict=False,
                require_price_edge=False,min_adv=0.,directional_risk=policy.ticket,
                issuer_risk=policy.aggregate,sector_risk=policy.aggregate,aggregate_risk=policy.aggregate,
                cohort_risk=policy.cohort,commission=1.50,expiry_buffer_days=1,cash_settlement=True)
            result=run(tickets,YearQuoteStore(lambda y:load_quotes(y,calendar)),b,cfg,
                selector=selector,selector_context=True,settlements=settled)
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
