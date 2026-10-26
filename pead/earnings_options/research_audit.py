"""Reproducible diagnostics; contemporaneous closes never filter past entries."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'results/selective_earnings_research'
AUDITS=[('selective_earnings_carry_intrinsic_trend_cash_ceiling','car5_100k'),
        ('selective_earnings_carry_lagged_cash','car5_100k')]


def entry_audit(family,scenario):
    t=pd.read_csv(ROOT/'results'/family/scenario/'trades.csv.gz',parse_dates=['entry_date'])
    t['optionid']=t.optionids.astype(int);parts=[]
    for year,g in t.groupby(t.entry_date.dt.year):
        sources=sorted((ROOT/'data/selective_earnings_v2').glob(f'carry_marks_lagged_{year}_*.parquet'))
        frames=[pq.read_table(p,columns=['date','secid','optionid','strike','underlying_price',
            'ask','prior_volume','prior_oi'],filters=[('optionid','in',g.optionid.unique().tolist())]).to_pandas()
            for p in sources]
        if not frames:raise ValueError(f'missing audit quote cache {year}')
        q=pd.concat(frames).drop_duplicates(['date','secid','optionid'])
        q['date']=pd.to_datetime(q.date)
        parts.append(g.merge(q,left_on=['entry_date','secid','optionid'],
            right_on=['date','secid','optionid'],how='left',validate='m:1'))
    a=pd.concat(parts,ignore_index=True)
    if a.ask.isna().any():raise ValueError('unmatched entry audit')
    a['intrinsic_at_native_close']=(a.underlying_price-a.strike).clip(lower=0)
    a['intrinsic_discount']=a.intrinsic_at_native_close-a.ask
    a['discount_over_one_percent_stock']=a.intrinsic_discount>a.underlying_price*.01
    a['minimum_lot_floor_dependency']=a.prior_volume<20
    # Closed fractions preserve entry lots and cashflows; summed rows reconcile.
    a['family']=family;a['scenario']=scenario
    summary=dict(experiment=family,scenario=scenario,closed_records=len(a),
        pricing_flags=int(a.discount_over_one_percent_stock.sum()),
        flagged_trade_pnl=float(a.loc[a.discount_over_one_percent_stock,'pnl'].sum()),
        low_volume_records=int(a.minimum_lot_floor_dependency.sum()),
        low_volume_pnl=float(a.loc[a.minimum_lot_floor_dependency,'pnl'].sum()),
        total_net_option_pnl=float(a.pnl.sum()))
    return a,summary



def settlement_trade_audit():
    targets=[(family,label) for family in ('selective_earnings_index_settlement_risk_exit',
        'selective_earnings_index_holdout') for label in ('deployed_100k','deployed_1000k')]
    records=[];annual=[]
    for family,label in targets:
        folder=ROOT/'results'/family/label
        t=pd.read_csv(folder/'trades.csv.gz',parse_dates=['entry_date','exit_date'])
        d=pd.read_csv(folder/'daily.csv.gz',parse_dates=['date'])
        for year,g in d.groupby(d.date.dt.year):
            annual.append(dict(experiment=family,scenario=label,year=int(year),
                strategy_return=float((1+g.ret).prod()-1),spy_return=float((1+g.benchmark).prod()-1),
                cash_interest=float(g.cash_interest.sum())))
        all_ids=set(int(i) for ids in t.optionids for i in str(ids).split(','))
        quotes=[]
        for year in sorted(set(t.entry_date.dt.year)|set(t.exit_date.dt.year)):
            path=ROOT/f'data/selective_earnings_index/spx_quotes_prior_v1_{year}.parquet'
            if not path.exists():raise ValueError(f'audit cache missing {year}')
            q=pq.read_table(path,columns=['date','secid','optionid','cp','strike','bid','ask','cfadj'],
                filters=[('optionid','in',list(all_ids))]).to_pandas()
            q['date']=pd.to_datetime(q.date);quotes.append(q)
        q=pd.concat(quotes,ignore_index=True)
        if not np.allclose(q.cfadj,1.):raise ValueError('unsupported adjusted SPX audit')
        lookup=q.set_index(['date','optionid'])
        sets={}
        for year in sorted(set(t.exit_date.dt.year)):
            z=pq.read_table(f'D:/OptionMetrics/parquet/secprd{year}.parquet',columns=['date','close'],
                filters=[('secid','=',118963)]).to_pandas()
            sets.update(zip(pd.to_datetime(z.date),z.close))
        for row in t.itertuples():
            ids=[int(i) for i in str(row.optionids).split(',')]
            entry=[lookup.loc[(row.entry_date,i)] for i in ids]
            lo,hi=sorted(entry,key=lambda x:x.strike)
            credit=(hi.bid-lo.ask)*100*row.contracts
            if row.reason=='cash_settlement':
                spot=sets[row.exit_date]
                exit_value=(max(0.,lo.strike-spot)-max(0.,hi.strike-spot))*100*row.contracts
            else:
                current=[lookup.loc[(row.exit_date,i)] for i in ids]
                long,short=sorted(current,key=lambda x:x.strike)
                exit_value=(long.bid-short.ask)*100*row.contracts
            expected=credit+exit_value-row.entry_fee-row.exit_fee
            records.append(dict(experiment=family,scenario=label,event_id=row.event_id,
                entry_date=row.entry_date,exit_date=row.exit_date,contracts=row.contracts,
                reason=row.reason,independent_entry_credit=credit,independent_exit_value=exit_value,
                independent_pnl=expected,ledger_pnl=row.pnl,pnl_gap=expected-row.pnl,
                entry_gap=credit-row.entry_cashflow,exit_gap=exit_value-row.exit_value))
    a=pd.DataFrame(records)
    if a[['pnl_gap','entry_gap','exit_gap']].abs().to_numpy().max()>1e-6:
        raise ValueError('independent SPX trade audit failed')
    a.to_csv(OUT/'settlement_trade_audit.csv',index=False)
    pd.DataFrame(annual).to_csv(OUT/'candidate_annual_returns.csv',index=False)
    report=dict(records=len(a),maximum_absolute_pnl_gap=float(a.pnl_gap.abs().max()),
        method='Independent entry bid/ask cashflows; early exit bid/ask; native SET intrinsic payoff; actual fees; no fitted price.')
    (OUT/'settlement_trade_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    OUT.mkdir(exist_ok=True);details=[];summaries=[]
    for family,scenario in AUDITS:
        a,s=entry_audit(family,scenario);details.append(a);summaries.append(s)
    pd.concat(details,ignore_index=True).to_csv(OUT/'call_entry_audit.csv',index=False)
    pd.DataFrame(summaries).to_csv(OUT/'call_entry_audit_summary.csv',index=False)
    metadata=dict(method='Post-run diagnostic only. No historical entries removed or repriced.',
        price_test='Ask below contemporaneous native-close intrinsic by more than1%ofstock.',
        timestamp_limit='Native stock close can follow option quote by one minute; flag is diagnostic, not proof of arbitrage.',
        liquidity_test='Prior volume<20 requires the assumed one-contract floor at5%participation.',
        pnl_limit='PnL on flagged records is attribution, not a counterfactual portfolio return.',summaries=summaries)
    (OUT/'call_entry_audit.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    metadata['settlement_trade_audit']=settlement_trade_audit()
    print(json.dumps(metadata,indent=2))


if __name__=='__main__':main()
