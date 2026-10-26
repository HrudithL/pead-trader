"""Run frozen funded synthesis candidates; every output is development evidence.

Example: python -m pead.risk_research.study --family options --segment development
Input caches are read-only in the explicit source checkout; new caches are local
and ignored. No source worktree or consumed holdout record is modified.
"""
from pathlib import Path
from dataclasses import asdict,replace
import argparse,hashlib,json,time
import numpy as np
import pandas as pd
from .policy import Policy,LIQUIDITY,hedge_menu,lagged_beta
from .ledger import Ledger
from pead.earnings_options.index_settlement import credit_menu
from pead.strategies.weights import causal_inverse_variance
from pead.engines.equity import COST_BPS

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_SOURCE=Path('C:/Users/hrudi/.codex/worktrees/selective-earnings-research/PEAD_Trading')
DEFAULT_DATA=Path('C:/Users/hrudi/Documents/BEI/PEAD_Trading/data')


def policies(family):
    options=[Policy('carry_risk20',carry=True),
             Policy('carry_risk20_vol10',carry=True,market_vol_target=.10),
             Policy('carry_protected',carry=True,hedge=True,require_hedge=True)]
    equity=[Policy('equity_funded',equity_scale=1.),
            Policy('equity_cash80',equity_scale=.8),
            Policy('equity_hedged',equity_scale=1.,hedge=True),
            Policy('equity_leverage120',equity_scale=1.2)]
    return options if family=='options' else equity if family=='equity' else options+equity


def inputs(source,segment):
    cache=source/'data/selective_earnings_index'
    b=pd.read_parquet(cache/'signals_1999_2013.parquet').sort_values('date')
    f=pd.read_csv(cache/'ff_daily.csv');f['date']=pd.to_datetime(f.date.astype(str),format='%Y%m%d')
    f['rf']=pd.to_numeric(f.RF)/100
    # Prior complete month, known ex ante; rounded French factor is a proxy.
    monthly=f.groupby(f.date.dt.to_period('M')).rf.mean().shift(1)
    b=b.drop(columns='rf').merge(f[['date','rf']],on='date',validate='1:1')
    b['cash_rf']=np.maximum(0.,b.date.dt.to_period('M').map(monthly)-.001/252)
    if segment=='development':b=b[b.date.dt.year.between(1999,2010)]
    else:b=b[b.date.dt.year.between(2012,2013)&b.date.le('2013-08-30')]
    if b[['rf','cash_rf','benchmark']].isna().any().any():raise ValueError('incomplete market series')
    sets=pd.concat([pd.read_csv(source/f'results/{folder}/native_settlements.csv',
        parse_dates=['expiry','settlement_date']) for folder in
        ['selective_earnings_index_settlement_risk_exit','selective_earnings_index_holdout']])
    if sets.expiry.duplicated().any():raise ValueError('duplicate SET')
    return b.reset_index(drop=True),sets


def quotes_for_year(source,year,calendar):
    cache=source/'data/selective_earnings_index';p=cache/f'spx_quotes_prior_v1_{year}.parquet'
    if p.exists():return pd.read_parquet(p)
    # 2001 lacks the newer cached prior-delta vintage. Derive strictly adjacent
    # session deltas from the original complete native SPX quote cache.
    q=pd.read_parquet(cache/f'spx_quotes_{year}.parquet')
    prior=pd.read_parquet(cache/f'spx_quotes_{year-1}.parquet')
    prior=prior[prior.date==prior.date.max()]
    q=pd.concat([prior,q],ignore_index=True).sort_values(['optionid','date'])
    cal=pd.DatetimeIndex(calendar);ix=cal.searchsorted(q.date)
    old=q.groupby('optionid',sort=False)
    previous=cal[np.maximum(ix-1,0)]
    aligned=old.date.shift().to_numpy()==previous.to_numpy()
    q['prior_delta']=np.where(aligned,old.delta.shift(),np.nan)
    return q[q.date.dt.year==year].copy()


def spot_for_year(source,year,om):
    p=source/'data/selective_earnings_index/spx_spot_1998_2010.parquet'
    if year<=2010:q=pd.read_parquet(p)
    else:
        q=pd.read_parquet(om/f'secprd{year}.parquet',columns=['secid','date','close'],filters=[('secid','=',108105)])
    q=q[q.date.dt.year==year]
    if q.date.duplicated().any():raise ValueError('duplicate native spot')
    return q.set_index('date')['close'].abs().to_dict()


def stock_events(data,b):
    ev=pd.read_parquet(data/'equity_vol_panel.parquet')
    mask=(ev.sue_rank_pct>=.9)&(ev.avg_dollar_volume_21d>=2e6)
    ev['causal_weight']=causal_inverse_variance(ev,mask)
    timing=pd.read_parquet(data/'events/equity_events_1996_2013.parquet',columns=[
        'permno','day0_date','anntims_unknown','day0_status','permno_ambiguous','permno_contested'])
    timing=timing[~timing.duplicated(['permno','day0_date'],keep=False)]
    timing=timing[(~timing.anntims_unknown)&timing.day0_status.eq('ok')&
                  ~timing.permno_ambiguous.fillna(True)&~timing.permno_contested.fillna(True)]
    ev=ev[ev.causal_weight>0].merge(timing[['permno','day0_date']],on=['permno','day0_date'],validate='m:1')
    ev=ev[~ev.duplicated(['permno','day0_date'],keep=False)].copy()
    cal=pd.DatetimeIndex(b.date);ix=cal.searchsorted(ev.day0_date,side='right')
    valid=(ix+60<len(cal))&(ev.day0_date>=cal.min())
    ev=ev[valid].copy();ix=ix[valid]
    ev['entry_date']=cal[ix];ev['planned_exit']=cal[ix+60]
    ev['cost']=ev.liquidity_quintile.map(COST_BPS).fillna(15)/1e4
    return ev.sort_values(['entry_date','permno']).reset_index(drop=True)


def equity_day_inputs(data,year,ids):
    q=pd.read_parquet(data/f'normalized_equity/equity_{year}.parquet',
        columns=['date','permno','ret','prc','prc_is_bidask_avg'],filters=[('permno','in',list(ids))])
    if q.duplicated(['permno','date']).any():raise ValueError('duplicate stock rows')
    return {day:g for day,g in q.groupby('date',sort=False)}


def manage_hedge(book,menu,resolutions,end):
    if not book.policy.hedge:return
    old=[p for p in book.options if p['role']=='hedge']
    monthly_roll=not book.daily or book.date.to_period('M')!=pd.Timestamp(book.daily[-1]['date']).to_period('M')
    if old and not monthly_roll:return
    for p in old:book.close_option(p,book.chain,'monthly_roll')
    if any(p['role']=='hedge' for p in book.options):book.skip('hedge_roll_incomplete');return
    if book.date>end-pd.Timedelta(days=120):return
    if not menu:book.skip('no_hedge_menu');return
    target=0.
    if book.policy.carry:
        # Fund protection for a complete contemplated 5% cohort at this entry.
        target=book.policy.hedge_cover*book.previous_nav*book.policy.ticket/book.spot
    elif len(book.stock_history)>=60:
        s=pd.Series(book.stock_history+[0.]);m=pd.Series(book.market_history+[0.])
        beta=float(lagged_beta(s,m).iloc[-1])
        target=.5*beta*book.previous_nav/book.spot
    if target<=0:book.skip('hedge_beta_warmup_or_zero');return
    for st,cap in menu:
        if st.expiry not in resolutions:continue
        units=-sum(x.quantity*x.delta*100 for x in st.legs)
        if units<=0:continue
        lots=min(cap,max(1,int(np.ceil(target/units))))
        if book.open_option(st,lots,'hedge',resolutions[st.expiry][0],book.chain):return
    book.skip('hedge_unaffordable_or_capacity')


def step(book,row,chain,spot,stock,events,credit,hedges,resolutions,end):
    returns={} if stock is None else dict(zip(stock.permno,stock.ret))
    actual={ex:v for ex,(day,v) in resolutions.items() if day==row.date}
    book.begin(row.date,row.cash_rf,row.benchmark,chain,returns,spot,actual)
    # Evaluation uses the published contemporaneous risk-free proxy; cash earns
    # only the separately lagged/haircut series applied above.
    book.rf=row.rf;book.chain=chain
    if book.policy.exit_on_signal and (row.trend_sign<0 or row.rv>.30):
        for p in list(book.options):
            if p['role']=='carry':book.close_option(p,chain,'risk_off')
    manage_hedge(book,hedges,resolutions,end)
    if book.policy.equity_scale:
        if not hasattr(book,'quarter') or book.quarter!=row.date.to_period('Q'):
            book.quarter=row.date.to_period('Q');book.quarter_nav=book.previous_nav
        risk_scale=1.
        if len(book.stock_history)>=20:
            vol=np.std(book.stock_history[-60:],ddof=1)*np.sqrt(252)
            if vol>0:risk_scale=min(1.,book.policy.stock_vol_target/vol)
        cap=.8*book.policy.equity_scale*risk_scale*book.previous_nav
        if book.stock_value>cap:
            fraction=1-cap/book.stock_value
            for p in list(book.stocks):book.close_stock(p,'risk_trim',fraction)
        prices={} if stock is None else {int(r.permno):r for r in stock.itertuples()}
        for e in events.itertuples():
            quote=prices.get(int(e.permno))
            if quote is None or quote.prc_is_bidask_avg:book.skip('stock_entry_quote');continue
            already=sum(p['value'] for p in book.stocks if p['permno']==e.permno)
            desired=min(book.quarter_nav*.003*e.causal_weight*book.policy.equity_scale*risk_scale,
                        max(0.,book.previous_nav*.01*book.policy.equity_scale-already),
                        max(0.,cap-book.stock_value))
            if book.policy.hedge:
                desired=min(desired,max(0.,book.cash-book.collateral-
                    max(0.,book.hedge_budget-book.premium_spent)))
            book.open_stock(e.permno,abs(quote.prc),desired,e.cost*(2 if book.policy.commission==3 else 1),
                            e.planned_exit,e.avg_dollar_volume_21d)
    gate=row.weekly_entry and row.trend_sign>0 and row.breadth_sign>0 and row.rv<=.30
    if book.policy.carry and gate and row.date<=end-pd.Timedelta(days=120):
        if any(p.get('pending') for p in book.options):book.skip('pending_option_exit')
        else:
            for _ in range(8):
                funded=False
                for _,st,cap in credit:
                    if st.expiry not in resolutions:continue
                    delta=sum(x.quantity*x.delta*100 for x in st.legs)
                    room=book.carry_room(st.expiry,delta)
                    lots=min(cap,int(room//(st.max_loss*100+4*book.policy.commission)))
                    if lots>0 and book.open_option(st,lots,'carry',resolutions[st.expiry][0],chain):
                        funded=True;break
                if not funded:book.skip('no_carry_risk_cash_capacity_or_hedge');break
    book.finish()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--data',type=Path,default=DEFAULT_DATA)
    p.add_argument('--optionmetrics',type=Path,default=Path('D:/OptionMetrics/parquet'))
    p.add_argument('--segment',choices=['development','recent'],default='development')
    p.add_argument('--family',choices=['options','equity','all'],default='all')
    p.add_argument('--stress',choices=['base','double_fee','slippage','lot_floor','funding4'],default='base')
    p.add_argument('--label',default='initial')
    p.add_argument('--smoke',action='store_true')
    args=p.parse_args();chosen=policies(args.family)
    chosen=[replace(x,commission=3.) if args.stress=='double_fee' else
            replace(x,slippage=.01) if args.stress=='slippage' else
            replace(x,small_lot_floor=1) if args.stress=='lot_floor' else
            replace(x,financing_spread=.04) if args.stress=='funding4' else x for x in chosen]
    name=f'{args.label}_{args.segment}_{args.family}_{args.stress}'+('_smoke' if args.smoke else '')
    out=ROOT/'results/options_risk_synthesis'/name
    if out.exists():raise ValueError(f'trial already exists; use a new label: {out}')
    out.mkdir(parents=True)
    protocol=dict(policies=[asdict(x) for x in chosen],segment=args.segment,stress=args.stress,
        source=str(args.source),data=str(args.data),development_only=True,
        targets=dict(cagr=.12,sharpe=1.,drawdown=-.20),started_utc=pd.Timestamp.now(tz='UTC').isoformat(),
        source_hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in Path(__file__).parent.glob('*.py')})
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    b,settlements=inputs(args.source,args.segment)
    if args.smoke:b=b.iloc[:180].copy()
    resolutions={r.expiry:(r.settlement_date,r.value) for r in settlements.itertuples()}
    allcal=pd.read_parquet(args.source/'data/selective_earnings/benchmark.parquet').date
    ev=stock_events(args.data,b) if args.family!='options' else pd.DataFrame(columns=['entry_date','permno'])
    event_groups={d:g for d,g in ev.groupby('entry_date')}
    capitals=[100_000,1_000_000] if args.family=='options' else [1_000_000,10_000_000]
    books=[Ledger(cap,policy) for policy in chosen for cap in capitals]
    empty_events=ev.iloc[:0];start=time.monotonic()
    for year,yearrows in b.groupby(b.date.dt.year,sort=True):
        q=quotes_for_year(args.source,int(year),allcal)
        qgroups={day:g for day,g in q.groupby('date',sort=False)}
        spots=spot_for_year(args.source,int(year),args.optionmetrics)
        stocks=equity_day_inputs(args.data,int(year),ev.permno.unique()) if len(ev) else {}
        for row in yearrows.itertuples(index=False):
            chain=qgroups.get(row.date,q.iloc[:0]);spot=spots.get(row.date)
            if spot is None:raise ValueError(f'missing native SPX spot {row.date}')
            menus={}
            for floor in {x.policy.small_lot_floor for x in books}:
                liq=replace(LIQUIDITY,small_lot_floor=floor)
                needcarry=any(x.policy.carry for x in books)
                gate=row.weekly_entry and row.trend_sign>0 and row.breadth_sign>0 and row.rv<=.30
                credit=credit_menu(chain,3. if args.stress=='double_fee' else 1.5,liq) if needcarry and gate else []
                hedges=hedge_menu(chain,3. if args.stress=='double_fee' else 1.5,liq) if any(x.policy.hedge for x in books) else []
                menus[floor]=(credit,hedges)
            for book in books:
                credit,hedges=menus[book.policy.small_lot_floor]
                step(book,row,chain,spot,stocks.get(row.date),event_groups.get(row.date,empty_events),
                     credit,hedges,resolutions,b.date.iloc[-1])
        print(f'{name}: {year}; elapsed {time.monotonic()-start:.1f}s; '+
              ', '.join(f'{x.policy.name}/{x.initial:g}={x.nav:,.0f}' for x in books),flush=True)
        del q,qgroups,stocks
    for book in books:
        if book.options:raise ValueError('terminal option positions remain')
        for position in list(book.stocks):book.close_stock(position,'sample_end')
        # All stock entries have predeclared exits before segment end.
        if book.stocks:raise ValueError('terminal stocks remain')
        folder=out/f'{book.policy.name}_{int(book.initial)}';folder.mkdir()
        pd.DataFrame(book.daily).to_csv(folder/'daily.csv.gz',index=False)
        pd.DataFrame(book.trades).to_csv(folder/'trades.csv.gz',index=False)
        audit=dict(cash_residual=book.reconciliation(),cash_interest=book.cash_interest,
            financing=book.financing,stock_fees=book.stock_fees,option_fees=book.option_fees,
            missing_option_mark_calls=book.missing_marks,missing_stock_marks=book.missing_stock_marks,
            skips=book.skips,final_nav=book.nav,investment_accepted=False)
        (folder/'audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    (out/'complete.json').write_text(json.dumps(dict(completed=True,elapsed_seconds=time.monotonic()-start)),encoding='utf-8')


if __name__=='__main__':main()
