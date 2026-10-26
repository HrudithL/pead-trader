"""One frozen high-risk SPX settlement candidate, untouched2012-2013 test.

The candidate fails the research register's30% drawdown selector. This evaluation
tests the user return hurdle, not a claim that the risk has become acceptable.
"""
from dataclasses import asdict
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .index_settlement import POLICIES,load_quotes,credit_menu,settlement_calendar
from .index_research import ROOT,SPX,LIQUIDITY,signals
from .index_directional import cash_sweep
from .engine import Config,run
from .quote_store import YearQuoteStore
from .validation import evaluate


def main():
    out=ROOT/'results/selective_earnings_index_holdout';out.mkdir(parents=True,exist_ok=True)
    frozen=out/'frozen_before_holdout.json'
    if not frozen.exists():raise ValueError('explicit frozen holdout protocol required')
    protocol=json.loads(frozen.read_text(encoding='utf-8'))
    if protocol['policy']!='deployed' or protocol['years']!=[2012,2013]:raise ValueError('unexpected holdout protocol')
    for name,digest in protocol['source_hashes'].items():
        if hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()!=digest:
            raise ValueError(f'holdout source changed after freeze: {name}')
    policy=POLICIES['deployed']
    if asdict(policy)!=protocol['risk']:raise ValueError('holdout risk changed after freeze')
    (out/'source_versions.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in __import__('pathlib').Path(__file__).parent.glob('*.py')},indent=2),encoding='utf-8')
    all_signals=signals(2013)
    # Extending the available calendar must not revise development signals.
    original=signals(2010)
    keys=['date','benchmark','rf','trend_sign','breadth_sign','rv','weekly_entry']
    prefix=all_signals[all_signals.date.dt.year<=2010]
    pd.testing.assert_frame_equal(original[keys].reset_index(drop=True),prefix[keys].reset_index(drop=True))
    b=cash_sweep(all_signals);b=b[b.date.dt.year.between(2012,2013)&(b.date<=pd.Timestamp(protocol['coverage_end']))].reset_index(drop=True)
    calendar=pd.read_parquet(ROOT/'data/selective_earnings/benchmark.parquet').date
    obs=pd.concat([pq.read_table(f'D:/OptionMetrics/parquet/secprd{year}.parquet',
        filters=[('secid','=',118963)],columns=['date','close']).to_pandas() for year in (2012,2013)])
    settled=settlement_calendar(b.date,obs);settled.to_csv(out/'native_settlements.csv',index=False)
    b['risk_off']=(b.trend_sign<0)|b.rv.gt(.30)
    gate=b.weekly_entry&(b.trend_sign>0)&(b.breadth_sign>0)&b.rv.le(.30)
    entries=b[gate&(b.date<=b.date.iloc[-1]-pd.Timedelta(days=75))]
    e=pd.DataFrame(dict(event_id='SPX_'+entries.date.dt.strftime('%Y%m%d'),secid=SPX,sector='SP500',
        entry_date=entries.date,release_date=entries.previous_date,rank=.99,reaction=.01,rv=entries.rv,adv=1e15,
        timing_verified=True,consensus_verified=True,identity_verified=True,corporate_actions_verified=True,
        assignment_verified=True,earnings_calendar_not_applicable=True))
    e['source_event_id']=e.event_id
    tickets=pd.concat([e.assign(event_id=e.event_id+f'_ticket_{i}') for i in range(8)],ignore_index=True)
    memo={}
    def selector(ch,sleeve,direction,event,ctx):
        day=ctx['date']
        if day not in memo:memo[day]=credit_menu(ch,ctx['commission'])
        for key,st,cap in memo[day]:
            available=min(cap-ctx['used_lots'].get((day,leg.optionid),0) for leg in st.legs)
            unit=st.max_loss*100+4*ctx['commission']
            if min(available,int(ctx['risk_budget']//unit),int(ctx['free_cash']//unit))>=1:return st,0.,cap
        return None
    def close_capacity(live,ctx):
        if not np.isfinite(live[['prior_oi','prior_volume']]).all().all():return 0
        caps=np.minimum(np.floor(LIQUIDITY.oi_participation*live.prior_oi),
            np.maximum(LIQUIDITY.small_lot_floor,np.floor(LIQUIDITY.participation*live.prior_volume)))
        return max(0,int(min(cap-ctx['used_lots'].get((ctx['date'],int(row.optionid)),0)
                             for cap,row in zip(caps,live.itertuples()))))
    rows=[]
    for capital in (100000,1000000):
        label=f'deployed_{capital//1000}k';folder=out/label;folder.mkdir(exist_ok=True)
        cfg=Config(initial_nav=capital,sleeves=('directional',),hold_sessions=9999,strict=False,
            require_price_edge=False,min_adv=0.,directional_risk=policy.ticket,issuer_risk=policy.aggregate,
            sector_risk=policy.aggregate,aggregate_risk=policy.aggregate,cohort_risk=policy.cohort,
            commission=1.50,expiry_buffer_days=1,cash_settlement=True,settlement_risk_exit=True,defer_unquoted_exits=True)
        result=run(tickets,YearQuoteStore(lambda y:load_quotes(y,calendar)),b,cfg,
            selector=selector,selector_context=True,settlements=settled,exit_capacity=close_capacity)
        d,t=result.daily,result.trades
        d.to_csv(folder/'daily.csv.gz',index=False);t.to_csv(folder/'trades.csv.gz',index=False)
        result.skips.to_csv(folder/'skips.csv.gz',index=False)
        stats=evaluate(d,False,True,repetitions=1000)
        count=int(t.source_event_id.nunique()) if len(t) else 0
        rows.append(dict(scenario=label,capital=capital,period='final_holdout',cagr=stats['strategy']['cagr'],
            sharpe=stats['strategy']['sharpe'],drawdown=stats['strategy']['max_drawdown'],
            spy_cagr=stats['benchmark']['cagr'],trade_count=count,closed_ticket_count=len(t),
            mean_risk_nav=float((d.reserved/d.nav).mean())))
        report=dict(protocol=protocol,starting_nav=capital,diagnostics=result.diagnostics,
            periods={'final_holdout':stats},final_holdout_evaluated=True,investment_accepted=False)
        (folder/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(f'{label}: frozen holdout NAV {d.nav.iloc[-1]:,.2f}',flush=True)
    table=pd.DataFrame(rows);table.to_csv(out/'comparison.csv',index=False)
    (out/'holdout_evaluated.json').write_text(json.dumps(dict(final_holdout_evaluated=True,
        investment_accepted=False,no_post_holdout_tuning=True),indent=2),encoding='utf-8')
    print(table.to_string(index=False),flush=True)


if __name__=='__main__':main()
