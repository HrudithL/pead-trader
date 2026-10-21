"""B_score.py: quantitative consistency scorecard for every post-fix strategy + Drift Core. Read-only inputs, writes B_*.csv."""
import os, numpy as np, pandas as pd
V='C:/Users/hrudi/Documents/BEI/_pead_consolidation/vintage_fixed/runs'
R='C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-evaluation-roi-74c7dd'
OUT=R+'/meta/debate2/'
END=pd.Timestamp('2013-12-31')
def sharpe(r): 
    return np.nan if len(r)<60 or r.std()==0 else r.mean()/r.std()*np.sqrt(252)
def metrics(r, name):
    r=r.loc[:END].copy(); r.index=pd.to_datetime(r.index)
    nz=r[r!=0]; r=r.loc[nz.index[0]:]
    out={'strategy':name,'start':str(r.index[0].date()),'ndays':len(r)}
    nav=(1+r).cumprod(); yrs=len(r)/252
    out['ann_ret']=nav.iloc[-1]**(1/yrs)-1; out['vol']=r.std()*np.sqrt(252); out['sharpe']=sharpe(r)
    q=(1+r).groupby(r.index.to_period('Q')).prod()-1; y=(1+r).groupby(r.index.year).prod()-1
    out['pct_pos_q']=(q>0).mean(); out['pct_pos_y']=(y>0).mean()
    out['worst_q']=q.min(); out['worst_year']=y.min()
    r12=nav/nav.shift(252)-1; out['worst_12m']=r12.min()
    pk=nav.cummax(); dd=nav/pk-1; out['maxdd']=dd.min()
    tr=dd.idxmin(); peak=nav.loc[:tr].idxmax(); rec=nav.loc[tr:][nav.loc[tr:]>=nav[peak]]
    out['dd_peak_to_trough_days']=int((tr-peak).days)
    out['dd_recover_days']=int((rec.index[0]-tr).days) if len(rec) else np.nan
    out['dd_unrecovered']=len(rec)==0
    out['pct_days_in_dd_gt10']=(dd<-0.10).mean()
    # ulcer
    out['ulcer']=np.sqrt((dd**2).mean())
    # eras
    for lab,a,b in [('e1','1996-01-01','2001-12-31'),('e2','2002-01-01','2007-12-31'),('e3','2008-01-01','2013-12-31')]:
        s=r.loc[a:b]; out['sh_'+lab]=sharpe(s) if len(s)>250 else np.nan
        out['ret_'+lab]=(1+s).prod()**(252/max(len(s),1))-1 if len(s)>250 else np.nan
    sh=[out['sh_e1'],out['sh_e2'],out['sh_e3']]
    out['era_min_sharpe']=np.nanmin(sh); out['era_n_pos']=int(np.nansum(np.array(sh)>0))
    # stress
    out['stress_2000_02']=(1+r.loc['2000-01-01':'2002-12-31']).prod()-1 if r.index[0]<=pd.Timestamp('2000-06-01') else np.nan
    out['stress_2008_09']=(1+r.loc['2008-01-01':'2009-12-31']).prod()-1
    # best-10 quarters dependence (log)
    lq=np.log1p(q); tot=lq.sum(); top=lq.nlargest(10).sum()
    out['top10q_share_logret']=top/tot if tot>0 else np.nan
    out['ret_ex_top10q']=np.exp((tot-top)/yrs)-1
    out['ret_ex_top5q']=np.exp((tot-lq.nlargest(5).sum())/yrs)-1
    # rolling 3y sharpe decay slope (per year), non-overlapping stepping every 63d
    rs=r.rolling(756).apply(lambda x: x.mean()/x.std()*np.sqrt(252) if x.std()>0 else np.nan,raw=True).dropna().iloc[::21]
    if len(rs)>10:
        x=(rs.index-rs.index[0]).days/365.25; out['roll3y_slope_per_yr']=np.polyfit(x,rs.values,1)[0]; out['roll3y_min']=rs.min(); out['roll3y_last']=rs.iloc[-1]
    # tail days
    out['worst_day']=r.min(); out['skew_d']=r.skew()
    out['q_sharpe']=q.mean()/q.std()*2 if q.std()>0 else np.nan
    return out,r
rows=[];series={}
for s in sorted(os.listdir(V)):
    if s.endswith('legacy') or not os.path.exists(f'{V}/{s}/daily.csv.gz'): continue
    d=pd.read_csv(f'{V}/{s}/daily.csv.gz',parse_dates=['date']).set_index('date')
    m,r=metrics(d['daily_return'],s); series[s]=r
    for c in ['gross_exposure','net_exposure','n_open_positions']:
        if c in d: 
            dd=d.loc[r.index]; m['avg_'+c]=dd[c].abs().mean()/1 if c!='n_open_positions' else dd[c].mean()
    if 'gross_exposure' in d:
        dd=d.loc[r.index]; m['avg_gross_over_nav']=(dd.gross_exposure/dd.nav.shift(1).fillna(dd.nav)).mean()
        m['net_over_gross']=(dd.net_exposure.abs()/dd.gross_exposure.replace(0,np.nan)).mean()
        m['long_share_gross']=(dd.long_exposure.abs()/dd.gross_exposure.replace(0,np.nan)).mean()
    pp=f'{V}/{s}/positions.parquet'
    if os.path.exists(pp):
        p=pd.read_parquet(pp); m['n_trades']=len(p)
        if 'holding_days' in p: m['avg_hold_days']=p.holding_days.mean()
        pn=p.net_pnl; tot=pn.sum()
        m['trade_top1pct_share_pnl']=pn.nlargest(max(1,len(p)//100)).sum()/tot if tot>0 else np.nan
        m['trade_worst1pct_pnl_over_total']=pn.nsmallest(max(1,len(p)//100)).sum()/abs(tot) if tot!=0 else np.nan
        m['trade_worst_loss_pct_nav0']=pn.min()/1e7
        if 'ret_on_notional' in p: m['trade_ret_skew']=p.ret_on_notional.skew(); m['trade_hit']=(pn>0).mean()
        if 'ff12_sector' in p: 
            sec=p.groupby('ff12_sector').notional.sum().abs(); m['sector_hhi']=((sec/sec.sum())**2).sum()
        if 'size_quintile' in p:
            sz=p.groupby('size_quintile').notional.sum().abs(); m['size_hhi']=((sz/sz.sum())**2).sum()
        if 'side' in p and 'notional' in p: m['long_share_trades']=(p.side>0).mean()
        # quarterly concentration: top-sector pnl? skip
    rows.append(m)
# Drift Core
for nm,f in [('DRIFT_CORE_events+index','sdb_rule_daily')]:
    d=pd.read_parquet(f'{R}/meta/out/{f}.parquet'); d['date']=pd.to_datetime(d['date']); d=d.set_index('date')
    m,r=metrics(d['daily_return'],nm); series[nm]=r; m['avg_hold_over_nav']=d.hold_over_nav.mean(); rows.append(m)
sl=pd.read_parquet(f'{R}/meta/out/sleeves.parquet'); sl.index=pd.to_datetime(sl.index)
sdb=series['DRIFT_CORE_events+index']
# Drift Core with cash instead of index: reconstruct approx = sdb - index*(1-hold_over_nav) 
mk=pd.read_parquet(f'{R}/meta/out/market.parquet').assign(date=lambda x:pd.to_datetime(x.date)).set_index('date').vwretd
hon=d.hold_over_nav.shift(1).fillna(0)
cash=(d.daily_return-mk*(1-hon)).loc[sdb.index]
m,r=metrics(cash,'DRIFT_CORE_cash_fill(approx)'); series['DRIFT_CORE_cash_fill(approx)']=r; rows.append(m)
m,r=metrics(mk,'INDEX_vwretd'); series['INDEX_vwretd']=r; rows.append(m)
df=pd.DataFrame(rows).set_index('strategy')
df.to_csv(OUT+'B_scorecard.csv')
pd.DataFrame(series).to_parquet(OUT+'B_daily_returns.parquet')
pd.set_option('display.width',250,'display.max_columns',50,'display.float_format',lambda x:f'{x:.3f}')
print(df[['ann_ret','vol','sharpe','pct_pos_q','pct_pos_y','worst_q','worst_12m','maxdd','dd_recover_days','sh_e1','sh_e2','sh_e3','stress_2000_02','stress_2008_09','top10q_share_logret','ret_ex_top10q','roll3y_slope_per_yr']])
