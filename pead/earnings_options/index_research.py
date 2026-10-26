"""Liquid-index expression of earnings-response breadth; all experiments recorded."""
from dataclasses import dataclass,asdict
from pathlib import Path
import argparse,json,hashlib
import numpy as np
import pandas as pd
from .data import scan
from .structures import Leg,Structure
from .liquidity_v2 import LiquidityPolicy,eligible,capacity
from .engine import Config,run
from .validation import metrics,evaluate

ROOT=Path(__file__).resolve().parents[2]
DATA=Path('C:/Users/hrudi/Documents/BEI/PEAD_Trading/data')
CACHE=ROOT/'data/selective_earnings_index'
SPX=108105


@dataclass(frozen=True)
class Policy:
    signal:str
    short_delta:float
    long_delta:float
    risk:float
    aggregate:float
    hold:int


POLICIES={
    'defensive':Policy('agreement_put',.25,.10,.03,.20,20),
    'trend_carry':Policy('trend_put',.25,.10,.06,.30,45),
    'earnings_carry':Policy('agreement_put',.35,.15,.06,.30,45),
    'trend_two_sided':Policy('trend_both',.25,.10,.06,.30,20),
    'earnings_two_sided':Policy('agreement_both',.35,.15,.06,.30,45),
    'active_trend':Policy('trend_put',.35,.15,.09,.40,45),
}
LIQUIDITY=LiquidityPolicy(min_oi=100,max_relative_spread=.50,max_absolute_spread=5.,participation=.05,oi_participation=.02,small_lot_floor=1)


def lagged_liquidity(quotes,calendar):
    q=quotes.sort_values(['optionid','date']).copy()
    sessions=pd.DatetimeIndex(calendar)
    positions=sessions.searchsorted(q.date)
    previous=sessions[np.maximum(positions-1,0)]
    group=q.groupby('optionid',sort=False)
    same=pd.to_datetime(group.date.shift()).to_numpy()==previous.to_numpy()
    q['prior_volume']=np.where(same,group.volume.shift().fillna(0),0.)
    old_oi=group.open_interest.shift()
    # Vendor documentation: after 2000-11-28 the current row's OI already
    # represents the preceding session and is available before today's close.
    q['prior_oi']=np.where(q.date>pd.Timestamp('2000-11-28'),q.open_interest,np.where(same,old_oi,0.))
    q['standard_contract']=q.ss_flag.astype(str).eq('0')&np.isfinite(q.cfadj)&(q.cfadj>0)
    return q


def index_quotes(year,calendar):
    path=CACHE/f'spx_quotes_{year}.parquet'
    if path.exists():return pd.read_parquet(path)
    dates=pd.DatetimeIndex(calendar)
    dates=dates[dates.year==year]
    keys=pd.DataFrame(dict(date=dates,secid=SPX))
    q=scan('D:/OptionMetrics/parquet',year,keys=keys)
    if q.empty:raise ValueError(f'{year}: SPX quotes absent')
    q=lagged_liquidity(q,calendar)
    q.to_parquet(path,index=False)
    print(f'{year}: cached {len(q):,} native SPX quote rows',flush=True)
    return q


def signals(end_year=2010):
    path=CACHE/'signals_1999_2010.parquet'
    if path.exists():return pd.read_parquet(path)
    b=pd.read_parquet(ROOT/'data/selective_earnings/benchmark.parquet')
    b=b[b.date.dt.year<=end_year].copy().reset_index(drop=True)
    e=pd.read_parquet(DATA/'events/equity_events_1996_2013.parquet')
    e['day0_date']=pd.to_datetime(e.day0_date)
    e=e[(e.day0_status=='ok')&~e.anntims_unknown&(e.day0_date.dt.year<=end_year)&e.permno.notna()].copy()
    for flag in ('permno_ambiguous','permno_contested'):
        if flag in e:e=e[~e[flag].fillna(True)]
    e['permno']=pd.to_numeric(e.permno).astype('int64')
    e=e.drop_duplicates(['permno','day0_date'],keep=False)
    observed=[]
    for year in range(1996,end_year+1):
        wanted=e[e.day0_date.dt.year==year][['permno','day0_date']].rename(columns={'day0_date':'date'})
        if wanted.empty:continue
        s=pd.read_parquet(DATA/f'raw_wrds/crsp/dsf_{year}.parquet',columns=['permno','date','ret'])
        s['date']=pd.to_datetime(s.date);s['ret']=pd.to_numeric(s.ret,errors='coerce')
        matched=wanted.merge(s,on=['permno','date'],how='left',validate='1:1')
        matched=matched.merge(b[['date','benchmark']],on='date',how='left',validate='m:1')
        matched=matched[np.isfinite(matched.ret)&np.isfinite(matched.benchmark)]
        matched['news']=np.sign(matched.ret-matched.benchmark)
        observed.append(matched[['date','news']])
    reaction=pd.concat(observed,ignore_index=True)
    calendar=pd.DatetimeIndex(b.date)
    ix=calendar.searchsorted(reaction.date,side='right')
    reaction=reaction[ix<len(calendar)].copy();ix=ix[ix<len(calendar)]
    reaction['available_date']=calendar[ix]
    agg=reaction.groupby('available_date').news.agg(['sum','count']).reindex(calendar,fill_value=0)
    total=agg['sum'].rolling(21,min_periods=1).sum()
    count=agg['count'].rolling(21,min_periods=1).sum()
    breadth=(total/count).where(count>=30)
    neutral=breadth.shift(1).rolling(252,min_periods=60).mean()
    b['earnings_breadth']=breadth.to_numpy()
    # Each company reaction was already delayed to the following session.
    # These observations are known before today's index quote, not at its close.
    b['breadth_sign']=np.sign(breadth-neutral).fillna(0).astype(int).to_numpy()
    wealth=(1+b.benchmark).cumprod().shift(1)
    b['trend_sign']=np.where(wealth>wealth.rolling(200,min_periods=200).mean(),1,-1)
    b['rv']=b.benchmark.shift(1).rolling(21,min_periods=20).std()*np.sqrt(252)
    b['previous_date']=b.date.shift(1)
    b=b[b.date.dt.year.between(1999,end_year)].copy()
    b['entry_week']=b.date.dt.to_period('W-FRI')
    b['weekly_entry']=~b.entry_week.duplicated()
    b=b.drop(columns='entry_week')
    b.to_parquet(path,index=False)
    print(f'Built earnings-response breadth from {len(reaction):,} observed historical announcements',flush=True)
    return b


def policy_direction(frame,policy):
    trend=frame.trend_sign.to_numpy(dtype=int);breadth=frame.breadth_sign.to_numpy(dtype=int)
    if policy.signal=='trend_put':direction=np.where(trend>0,1,0)
    elif policy.signal=='agreement_put':direction=np.where((trend>0)&(breadth>0),1,0)
    elif policy.signal=='trend_both':direction=trend.copy()
    elif policy.signal=='agreement_both':direction=np.where(trend==breadth,trend,0)
    else:raise ValueError('unknown signal')
    direction=np.where((frame.rv.to_numpy()>getattr(policy,"rv_limit",.30))|~np.isfinite(frame.rv),0,direction)
    return direction


def select_credit(chain,direction,policy,context):
    c=eligible(chain,LIQUIDITY)
    dte=(c.expiry-c.date).dt.days
    # Historical standard monthly SPX expiries; avoid AM-settlement day entirely.
    monthly=(c.expiry.dt.day_of_week==5)&c.expiry.dt.day.between(15,21)
    c=c[dte.between(35,75)&monthly].copy()
    flag='P' if direction==1 else 'C'
    c=c[(c.cp==flag)&(c.delta*(1 if flag=='C' else -1)>0)]
    if c.empty:return None
    expiries=c[['expiry','date']].drop_duplicates()
    expiries['distance']=((expiries.expiry-expiries.date).dt.days-45).abs()
    best=None;best_key=None
    for expiry in expiries.sort_values(['distance','expiry']).expiry:
        g=c[c.expiry==expiry].copy()
        short=g.assign(distance=(g.delta.abs()-policy.short_delta).abs()).sort_values(['distance','optionid']).head(3)
        long=g.assign(distance=(g.delta.abs()-policy.long_delta).abs()).sort_values(['distance','optionid']).head(3)
        for sr in short.itertuples():
            for lr in long.itertuples():
                if sr.optionid==lr.optionid or sr.delta*lr.delta<=0:continue
                if direction*(sr.strike-lr.strike)<=0:continue
                credit=sr.bid-lr.ask;width=abs(sr.strike-lr.strike)
                if not 0<credit<width:continue
                loss=width-credit
                friction=(sr.ask-sr.bid+lr.ask-lr.bid+4*context['commission']/100)/loss
                if friction>.15:continue
                rows=g[g.optionid.isin([sr.optionid,lr.optionid])]
                cap=capacity(rows,LIQUIDITY)
                available=min(cap-context['used_lots'].get((context['date'],int(r.optionid)),0) for r in (sr,lr))
                unit=loss*100+4*context['commission']
                lots=min(int(context['risk_budget']//unit),int(context['free_cash']//unit),available)
                if lots<1:continue
                distance=abs(abs(sr.delta)-policy.short_delta)+abs(abs(lr.delta)-policy.long_delta)
                key=(distance,abs((expiry-context['date']).days-45),int(sr.optionid),int(lr.optionid))
                if best_key is None or key<best_key:
                    legs=tuple(Leg(int(r.optionid),q,flag,float(r.strike),expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume)) for r,q in ((lr,1),(sr,-1)))
                    best=Structure('directional',legs,credit,loss,expiry),0.,cap
                    best_key=key
    return best


def run_year(frame,quotes,policy,initial_nav,selector_function=select_credit):
    b=frame.copy();direction=policy_direction(b,policy)
    b['risk_off']=direction==0;b['direction_signal']=direction
    entries=b[b.weekly_entry&~b.risk_off].copy()
    # No entries in the final week; any remaining book closes at declared year-end.
    entries=entries[entries.date<b.date.iloc[-5]]
    e=pd.DataFrame(dict(event_id='SPX_'+entries.date.dt.strftime('%Y%m%d'),secid=SPX,
        sector='diversified_SP500',entry_date=entries.date,release_date=entries.previous_date,
        rank=.5,reaction=0.,rv=entries.rv,adv=1e15,spot=entries.spot if "spot" in entries else np.nan,trade_direction=pd.Series(direction,index=b.index).loc[entries.index],
        timing_verified=True,consensus_verified=True,identity_verified=True,corporate_actions_verified=True,
        assignment_verified=True,last_trade_buffer_days=2,schedule_known=False,earnings_calendar_not_applicable=True,scheduled_date=pd.NaT))
    cfg=Config(initial_nav=initial_nav,strict=False,hold_sessions=policy.hold,expiry_buffer_days=4,
        sleeves=('directional',),require_price_edge=False,min_adv=0.,directional_risk=policy.risk,
        current_risk_budget=bool(getattr(policy,"delta_target",0)),aggregate_risk=policy.aggregate,issuer_risk=policy.aggregate,sector_risk=policy.aggregate,
        cohort_risk=policy.risk,commission=1.50)
    def selector(ch,sleeve,trade_direction,event,context):return selector_function(ch,trade_direction,policy,context,event) if getattr(policy,"delta_target",0) else selector_function(ch,trade_direction,policy,context)
    return run(e,quotes,b,cfg,selector=selector,selector_context=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--end',type=int,default=2010)
    a=parser.parse_args()
    if a.end!=2010:parser.error('frozen development sample ends in 2010')
    CACHE.mkdir(parents=True,exist_ok=True)
    out=ROOT/'results/selective_earnings_index';out.mkdir(parents=True,exist_ok=True)
    (out/'frozen_policies.json').write_text(json.dumps({k:asdict(v) for k,v in POLICIES.items()},indent=2),encoding='utf-8')
    b=signals(a.end)
    calendar=pd.read_parquet(ROOT/'data/selective_earnings/benchmark.parquet').date
    scenarios={f'{name}_{capital//1000}k':(name,capital) for name in POLICIES for capital in (100_000,1_000_000)}
    nav={k:v[1] for k,v in scenarios.items()};daily={k:[] for k in scenarios};trades={k:[] for k in scenarios};diagnostics={k:[] for k in scenarios}
    for year in range(1999,2011):
        q=index_quotes(year,calendar)
        frame=b[b.date.dt.year==year].copy().reset_index(drop=True)
        for label,(name,capital) in scenarios.items():
            r=run_year(frame,q,POLICIES[name],nav[label])
            nav[label]=float(r.daily.nav.iloc[-1]);daily[label].append(r.daily);trades[label].append(r.trades);diagnostics[label].append(r.diagnostics)
        print(f'{year}: index NAVs '+json.dumps(nav),flush=True)
    rows=[]
    for label,(name,capital) in scenarios.items():
        folder=out/label;folder.mkdir(parents=True,exist_ok=True)
        d=pd.concat(daily[label],ignore_index=True);t=pd.concat(trades[label],ignore_index=True)
        d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
        report=dict(policy=asdict(POLICIES[name]),starting_nav=capital,diagnostics=diagnostics[label],periods={})
        for period,start,end in [('discovery',1999,2004),('validation',2005,2007),('development_test',2008,2010),('all_development',1999,2010)]:
            sub=d[d.date.dt.year.between(start,end)]
            stats=evaluate(sub,False,True,repetitions=500)
            count=int(t.exit_date.dt.year.between(start,end).sum()) if len(t) else 0
            stats['trade_count']=count;stats['mean_risk_nav']=float((sub.reserved/sub.nav).mean())
            report['periods'][period]=stats
            rows.append(dict(scenario=label,capital=capital,period=period,cagr=stats['strategy']['cagr'],sharpe=stats['strategy']['sharpe'],drawdown=stats['strategy']['max_drawdown'],spy_cagr=stats['benchmark']['cagr'],trade_count=count,mean_risk_nav=stats['mean_risk_nav']))
        (folder/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    selected={}
    for capital in (100_000,1_000_000):
        val=table[(table.period=='validation')&(table.capital==capital)&(table.trade_count>=20)&(table.drawdown>=-.30)]
        selected[str(capital)]=val.sort_values(['sharpe','scenario'],ascending=[False,True]).iloc[0].scenario if len(val) else None
    (out/'validation_selection.json').write_text(json.dumps(dict(selected_by_validation=selected,final_holdout_evaluated=False,investment_accepted=False),indent=2),encoding='utf-8')
    print(table.to_string(index=False),flush=True)


if __name__=='__main__':main()
