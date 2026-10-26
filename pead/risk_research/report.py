"""Reproducible comparisons, uncertainty and explicit failed investment gates."""
from pathlib import Path
import argparse,json
import numpy as np
import pandas as pd


def statistics(daily):
    d=daily.sort_values('date')
    if d.empty or d.date.duplicated().any():raise ValueError('unique nonempty daily dates required')
    a=d[['ret','rf','benchmark']].to_numpy(float)
    if not np.isfinite(a).all() or (a<=-1).any():raise ValueError('invalid daily returns')
    r,rf,m=a.T;w=np.r_[1.,np.cumprod(1+r)];x=r-rf;y=m-rf
    sd=x.std(ddof=1);beta=np.cov(x,y)[0,1]/y.var(ddof=1) if y.var(ddof=1)>1e-15 else 0.
    tail=r[r<=np.quantile(r,.05)]
    return dict(cagr=float(w[-1]**(252/len(r))-1),annual_vol=float(r.std(ddof=1)*np.sqrt(252)),
        excess_sharpe=float(x.mean()/sd*np.sqrt(252)) if sd>1e-12 else 0.,
        max_drawdown=float(np.min(w/np.maximum.accumulate(w)-1)),
        beta=float(beta),market_alpha=float((x.mean()-beta*y.mean())*252),
        worst_day=float(r.min()),daily_expected_shortfall5=float(tail.mean()),sessions=len(r))


def paired_difference(candidate,parent,repetitions=1000,seed=20261026):
    a=candidate.sort_values('date').reset_index(drop=True)
    b=parent.sort_values('date').reset_index(drop=True)
    if not a.date.equals(b.date) or not np.allclose(a.rf,b.rf):raise ValueError('identical dates and RF required')
    if repetitions<1:raise ValueError('positive repetitions required')
    groups=[g.index.to_numpy() for _,g in a.groupby(pd.to_datetime(a.date).dt.to_period('M'),sort=True)]
    rng=np.random.default_rng(seed);n=len(groups);draw=[]
    # Calendar blocks are jointly sampled; portfolio and benchmark relationships
    # survive each draw. Intervals condition on the existing research search.
    for _ in range(repetitions):
        starts=rng.integers(n,size=int(np.ceil(n/3)))
        months=np.concatenate([(i+np.arange(3))%n for i in starts])[:n]
        ix=np.concatenate([groups[i] for i in months])
        def sample(d):
            z=d.iloc[ix].copy();z['date']=pd.bdate_range('2000-01-03',periods=len(z));return statistics(z)
        ca,pa=sample(a),sample(b)
        draw.append([ca[k]-pa[k] for k in ['cagr','excess_sharpe','max_drawdown','market_alpha']])
    q=np.quantile(draw,[.025,.5,.975],axis=0)
    return {k:dict(zip(['p025','p50','p975'],map(float,q[:,i])))
            for i,k in enumerate(['cagr','sharpe','max_drawdown','market_alpha'])}


def factor_alpha(daily,factors,lags=60):
    """Three-factor intercept with Bartlett/Newey-West uncertainty.

    FF3 does not identify nonlinear option alpha or surprise-specific skill.
    """
    d=daily.merge(factors,on='date',validate='1:1').dropna(subset=['MKT_RF','SMB','HML','RF'])
    y=d.ret.to_numpy()-d.RF.to_numpy()/100
    x=np.column_stack([np.ones(len(d)),d[['MKT_RF','SMB','HML']].to_numpy()/100])
    if len(d)<10:return {}
    inv=np.linalg.pinv(x.T@x);coef=inv@x.T@y;u=y-x@coef;xu=x*u[:,None]
    meat=xu.T@xu
    for lag in range(1,min(lags,len(d)-1)+1):
        cross=xu[lag:].T@xu[:-lag];meat+=(1-lag/(lags+1))*(cross+cross.T)
    se=np.sqrt(max(0.,(inv@meat@inv)[0,0]))
    return dict(ff3_alpha=float(coef[0]*252),ff3_alpha_hac_low=float((coef[0]-1.96*se)*252),
        ff3_alpha_hac_high=float((coef[0]+1.96*se)*252),ff3_n=len(d))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]/'results/options_risk_synthesis')
    p.add_argument('--source',type=Path,default=Path('C:/Users/hrudi/.codex/worktrees/selective-earnings-research/PEAD_Trading'))
    p.add_argument('--bootstrap',type=int,default=1000)
    args=p.parse_args();rows=[];yearly=[];pairs={}
    factors=pd.read_csv(args.source/'data/selective_earnings_index/ff_daily.csv')
    factors['date']=pd.to_datetime(factors.date.astype(str),format='%Y%m%d')
    for trial in sorted(args.root.glob('*')):
        if not (trial/'complete.json').exists() or 'smoke' in trial.name:continue
        protocol=json.loads((trial/'protocol.json').read_text());loaded={}
        for folder in sorted(trial.iterdir()):
            if not folder.is_dir() or not (folder/'daily.csv.gz').exists():continue
            d=pd.read_csv(folder/'daily.csv.gz',parse_dates=['date'])
            t=pd.read_csv(folder/'trades.csv.gz')
            audit=json.loads((folder/'audit.json').read_text());stats=statistics(d)
            stats.update(factor_alpha(d,factors));label=folder.name.rsplit('_',1)[0]
            trades=int(t.entry_date.nunique()) if len(t) else 0
            stats.update(trial=trial.name,policy=label,capital=int(folder.name.rsplit('_',1)[1]),
                segment=protocol['segment'],stress=protocol['stress'],distinct_entries=trades,
                missing_option_mark_calls=audit['missing_option_mark_calls'],
                missing_stock_marks=audit['missing_stock_marks'],cash_residual=audit['cash_residual'],
                cash_interest=audit['cash_interest'],financing=audit['financing'],
                stock_fees=audit['stock_fees'],option_fees=audit['option_fees'],
                max_carry_risk_nav=float(d.carry_risk_nav.max()),
                min_free_cash=float(d.free_cash.min()),
                stock_pnl=float(t.loc[t.kind.eq('stock'),'pnl'].sum()) if len(t) else 0.,
                carry_pnl=float(t.loc[t.kind.eq('carry'),'pnl'].sum()) if len(t) else 0.,
                hedge_pnl=float(t.loc[t.kind.eq('hedge'),'pnl'].sum()) if len(t) else 0.)
            stats['numerical_target_pass']=bool(stats['cagr']>=.12 and stats['excess_sharpe']>=1 and
                stats['max_drawdown']>=-.20 and trades>=20 and abs(audit['cash_residual'])<.01 and
                audit['missing_stock_marks']==0 and audit['missing_option_mark_calls']==0)
            stats['investment_accepted']=False
            rows.append(stats);loaded[folder.name]=d
            for year,g in d.groupby(d.date.dt.year):
                yearly.append(dict(trial=trial.name,scenario=folder.name,year=int(year),**statistics(g)))
        for key,d in loaded.items():
            policy,capital=key.rsplit('_',1)
            parent=('equity_funded' if policy.startswith('equity_') else 'carry_risk20')+'_'+capital
            if parent in loaded and parent!=key:
                pairs[trial.name+'/'+key]=dict(parent=parent,
                    intervals=paired_difference(d,loaded[parent],args.bootstrap))
    table=pd.DataFrame(rows)
    table.to_csv(args.root/'comparison.csv',index=False)
    pd.DataFrame(yearly).to_csv(args.root/'yearly.csv',index=False)
    (args.root/'paired_uncertainty.json').write_text(json.dumps(pairs,indent=2),encoding='utf-8')
    if len(table):print(table[['trial','policy','capital','cagr','excess_sharpe','max_drawdown','financing','numerical_target_pass']].to_string(index=False))


if __name__=='__main__':main()
