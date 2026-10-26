"""Paid long-dated index spreads: explicit delta targets, no account borrowing."""
from dataclasses import dataclass,asdict
from pathlib import Path
import argparse,json,hashlib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .index_research import ROOT,CACHE,signals,index_quotes,run_year,LIQUIDITY
from .liquidity_v2 import eligible,capacity
from .structures import Leg,Structure
from .validation import evaluate


@dataclass(frozen=True)
class DebitPolicy:
    signal:str
    delta_target:float
    risk:float
    aggregate:float
    long_delta:float=.80
    short_delta:float=.25
    hold:int=45
    rv_limit:float=.45


POLICIES={
    'trend_1x':DebitPolicy('trend_both',1.,.35,.35),
    'trend_15x':DebitPolicy('trend_both',1.5,.50,.50),
    'trend_2x':DebitPolicy('trend_both',2.,.60,.60),
    'earnings_1x':DebitPolicy('agreement_both',1.,.35,.35),
    'earnings_15x':DebitPolicy('agreement_both',1.5,.50,.50),
    'earnings_2x':DebitPolicy('agreement_both',2.,.60,.60),
}


def with_spot(frame):
    path=CACHE/'spx_spot_1998_2010.parquet'
    if path.exists():s=pd.read_parquet(path)
    else:
        frames=[]
        for year in range(1998,2011):
            p=Path(f'D:/OptionMetrics/parquet/secprd{year}.parquet')
            a=pq.read_table(p,columns=['secid','date','close'],filters=[('secid','=',108105)]).to_pandas()
            a['date']=pd.to_datetime(a.date);frames.append(a)
        s=pd.concat(frames,ignore_index=True).sort_values('date')
        if s.date.duplicated().any():raise ValueError('duplicate SPX spot dates')
        s['spot']=s['close'].abs().shift(1)
        s.to_parquet(path,index=False)
    b=frame.merge(s[['date','spot']],on='date',how='left',validate='1:1')
    if not np.isfinite(b.spot).all() or (b.spot<=0).any():raise ValueError('missing prior SPX spot')
    return b


_MENU_CACHE={}


def debit_menu(chain,direction,policy,commission):
    columns=['date','expiry','optionid','cp','strike','bid','ask','delta','prior_volume','prior_oi','standard_contract']
    digest=hashlib.sha256(pd.util.hash_pandas_object(chain[columns],index=False).to_numpy().tobytes()).hexdigest()
    cache_key=(digest,direction,policy.long_delta,policy.short_delta,commission)
    if cache_key in _MENU_CACHE:return _MENU_CACHE[cache_key]
    c=eligible(chain,LIQUIDITY)
    dte=(c.expiry-c.date).dt.days
    monthly=(c.expiry.dt.day_of_week==5)&c.expiry.dt.day.between(15,21)
    c=c[dte.between(90,180)&monthly].copy()
    flag='C' if direction==1 else 'P'
    c=c[(c.cp==flag)&(c.delta*(1 if flag=='C' else -1)>0)]
    rows=[]
    if not c.empty:
        caps={int(r.optionid):max(0,int(min(np.floor(LIQUIDITY.oi_participation*r.prior_oi),max(LIQUIDITY.small_lot_floor,np.floor(LIQUIDITY.participation*r.prior_volume))))) for r in c.itertuples(index=False)}
        for expiry,g in c.groupby('expiry',sort=True):
            long=g.assign(distance=(g.delta.abs()-policy.long_delta).abs()).sort_values(['distance','optionid']).head(4)
            short=g.assign(distance=(g.delta.abs()-policy.short_delta).abs()).sort_values(['distance','optionid']).head(4)
            for lr in long.itertuples():
                for sr in short.itertuples():
                    if lr.optionid==sr.optionid or direction*(sr.strike-lr.strike)<=0:continue
                    debit=lr.ask-sr.bid;width=abs(lr.strike-sr.strike)
                    delta=abs(lr.delta-sr.delta)
                    if not 0<debit<width or delta<.20:continue
                    friction=(lr.ask-lr.bid+sr.ask-sr.bid+4*commission/100)/debit
                    if friction>.08:continue
                    cap=min(caps[int(lr.optionid)],caps[int(sr.optionid)])
                    if cap<1:continue
                    distance=abs(abs(lr.delta)-policy.long_delta)+abs(abs(sr.delta)-policy.short_delta)
                    key=(distance,abs((expiry-chain.date.iloc[0]).days-120),int(lr.optionid),int(sr.optionid))
                    legs=tuple(Leg(int(r.optionid),q,flag,float(r.strike),expiry,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume)) for r,q in ((lr,1),(sr,-1)))
                    rows.append((key,Structure('directional',legs,-debit,debit,expiry),cap,delta))
    result=sorted(rows,key=lambda x:x[0]);_MENU_CACHE[cache_key]=result
    return result


def select_debit(chain,direction,policy,context,event):
    if not context['greeks_complete'] or not np.isfinite(event.spot) or event.spot<=0:return None
    remaining=policy.delta_target*context['nav']-abs(context['open_delta_units'])*event.spot
    if remaining<=0:return None
    for key,s,raw_capacity,delta in debit_menu(chain,direction,policy,context['commission']):
        cap=min(raw_capacity,int(remaining//(100*event.spot*delta)))
        available=min(cap-context['used_lots'].get((context['date'],leg.optionid),0) for leg in s.legs)
        unit=s.max_loss*100+4*context['commission']
        lots=min(int(context['risk_budget']//unit),int(context['free_cash']//unit),available)
        if lots>=1:return s,0.,cap
    return None


def cash_sweep(frame):
    f=pd.read_csv(CACHE/'ff_daily.csv');f['date']=pd.to_datetime(f.date.astype(str),format='%Y%m%d')
    f['rf']=f.RF/100
    monthly=f.groupby(f.date.dt.to_period('M')).rf.mean().shift(1)
    b=frame.merge(f[['date','rf']],on='date',how='left',validate='1:1',suffixes=('_old',''))
    # Full-month lag avoids treating ex-post monthly T-bill return allocation as
    # a same-month known rate. It remains an explicitly labeled sweep proxy.
    b['cash_rf']=np.maximum(0.,b.date.dt.to_period('M').map(monthly)-.001/252)
    if b[['cash_rf','rf']].isna().any().any():raise ValueError('missing cash returns')
    return b.drop(columns='rf_old')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cash-sweep',action='store_true')
    a=parser.parse_args()
    out=ROOT/('results/selective_earnings_index_directional_partial_cash' if a.cash_sweep else 'results/selective_earnings_index_directional_partial')
    out.mkdir(parents=True,exist_ok=True)
    (out/'frozen_policies.json').write_text(json.dumps({k:asdict(v) for k,v in POLICIES.items()},indent=2),encoding='utf-8')
    b=with_spot(signals())
    if a.cash_sweep:b=cash_sweep(b)
    calendar=pd.read_parquet(ROOT/'data/selective_earnings/benchmark.parquet').date
    scenarios={f'{name}_{capital//1000}k':(name,capital) for name in POLICIES for capital in (100_000,1_000_000)}
    nav={k:v[1] for k,v in scenarios.items()};daily={k:[] for k in scenarios};trades={k:[] for k in scenarios};diags={k:[] for k in scenarios}
    for year in range(1999,2011):
        q=index_quotes(year,calendar)
        frame=b[b.date.dt.year==year].copy().reset_index(drop=True)
        for label,(name,capital) in scenarios.items():
            r=run_year(frame,q,POLICIES[name],nav[label],selector_function=select_debit)
            nav[label]=float(r.daily.nav.iloc[-1]);daily[label].append(r.daily);trades[label].append(r.trades);diags[label].append(r.diagnostics)
        print(f'{year}: directional NAVs '+json.dumps(nav),flush=True)
    rows=[]
    for label,(name,capital) in scenarios.items():
        folder=out/label;folder.mkdir(parents=True,exist_ok=True)
        d=pd.concat(daily[label],ignore_index=True);t=pd.concat(trades[label],ignore_index=True)
        d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
        report=dict(policy=asdict(POLICIES[name]),starting_nav=capital,cash_sweep_proxy=a.cash_sweep,diagnostics=diags[label],periods={})
        for period,start,end in [('discovery',1999,2004),('validation',2005,2007),('development_test',2008,2010),('all_development',1999,2010)]:
            sub=d[d.date.dt.year.between(start,end)]
            stats=evaluate(sub,False,True,repetitions=500)
            count=int(t.loc[t.exit_date.dt.year.between(start,end),'event_id'].nunique()) if len(t) else 0
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
