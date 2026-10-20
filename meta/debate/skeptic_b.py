import pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
SRC='C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/'
def pos(s):
    p=pd.read_parquet(SRC+s+'/positions.parquet'); p['an']=p.notional.abs(); p['yr']=p.day0_date.dt.year
    p['era']=pd.cut(p.yr,[1995,2001,2007,2013],labels=['96-01','02-07','08-13'])
    # excess vs same-quarter all-position avg (mimic 'common/excess')
    p['q_avg']=p.groupby('ann_quarter').hold_ret.transform('mean')
    p['excess']=(p.side*(p.hold_ret-p.q_avg))*p.an
    p['alpha_net']=p.excess-p.cost
    return p
def sharpe(r): return r.mean()/r.std()*np.sqrt(252)
mk=pd.read_csv('skeptic_mkt_implied.csv',index_col=0,parse_dates=True).mkt
for s in ['calm_weighted','calm_rank_tilt','extreme_picks','balanced_leverage']:
    p=pos(s); d=pd.read_csv(SRC+s+'/daily.csv.gz',parse_dates=['date']).set_index('date')
    print('\n=====',s,'positions',len(p),'net pnl M',p.net_pnl.sum()/1e6,'cost M',p.cost.sum()/1e6,'gross M',p.gross_pnl.sum()/1e6)
    # 2 decay
    g=p.groupby('era',observed=True).agg(n=('an','size'),notionalM=('an','sum'),net=('net_pnl','sum'),alpha=('alpha_net','sum'),cost=('cost','sum'))
    g['net_bp']=g.net/g.notionalM*1e4; g['alpha_bp']=g.alpha/g.notionalM*1e4; g['cost_bp']=g.cost/g.notionalM*1e4; g['notionalM']/=1e6; g[['net','alpha','cost']]/=1e6
    print(g.round(2))
    y=p.groupby('yr').agg(net=('net_pnl','sum'),nm=('an','sum')); y['bp']=y.net/y.nm*1e4; y['net']/=1e6; y['nm']/=1e6
    print(y.T.round(1).to_string())
    # 2008-13 daily
    for a,b in [('1996','2001'),('2002','2007'),('2008','2013'),('2010','2013'),('2009','2009')]:
        r=d.daily_return[a:b]; print(a,b,'ann',round((1+r).prod()**(252/len(r))-1,4),'sh',round(sharpe(r),2))
    # 3 short leg
    for sd,nm in [(1,'long'),(-1,'short')]:
        q=p[p.side==sd]; ny=(q.an*q.holding_days/252).sum()
        print(nm,'net M',round(q.net_pnl.sum()/1e6,2),'gross M',round(q.gross_pnl.sum()/1e6,2),'notional-years M',round(ny/1e6,2), 'mean hold',q.holding_days.mean().round(1))
    sh=p[p.side==-1]; ny=(sh.an*sh.holding_days/252).sum(); tot=p.net_pnl.sum()
    nav_years=len(d[d.index>='1996'])/252
    for fee in [0.01,0.03,0.05]:
        cost=fee*ny; print(f' borrow {fee:.0%}: cost M {cost/1e6:.2f}  total net after {(tot-cost)/1e6:.2f}  ({(tot-cost)/tot:.0%} of original); drag pp/yr on avg NAV ~{cost/d.nav.mean()/nav_years*100:.2f}')
    # by era
    for fee in [0.01,0.03,0.05]:
        ee=sh.groupby('era',observed=True).apply(lambda q:(q.an*q.holding_days/252).sum())*fee
        ne=p.groupby('era',observed=True).net_pnl.sum()
        print('  era net after',fee,((ne-ee)/1e6).round(2).to_dict())
    # drop shorts: use pnl_long daily (mktadj basis) and raw = + long_exposure*mkt
    dd=d[d.index>='1996'].copy()
    nav=dd.nav.shift(1)
    rl=dd.pnl_long/nav
    print(' long-only mkt-adj ret ann',round((1+rl.dropna()).prod()**(252/len(rl.dropna()))-1,4),'sh',round(sharpe(rl.dropna()),2),'vol',round(rl.std()*252**.5,3))
    mkk=mk.reindex(dd.index).fillna(0)
    raw=(dd.pnl_long+dd.long_exposure.shift(1)*mkk)/nav
    raw=raw.dropna(); print(' long-only RAW (adding mkt on long exposure) ann',round((1+raw).prod()**(252/len(raw))-1,4),'sh',round(sharpe(raw),2),'vol',round(raw.std()*252**.5,3),'corr mkt',round(raw.corr(mkk),2), 'avg long exp/nav',round((dd.long_exposure/dd.nav).mean(),2))
    # short restricted to low/mid beta (<1.2? use tercile)
    # alpha-neutral check: short excess alpha vs cost
    print(' short excess alpha M',round(sh.excess.sum()/1e6,2),' long excess alpha M',round(p[p.side==1].excess.sum()/1e6,2))
    # 4 concentration
    q=p.groupby('ann_quarter').net_pnl.sum().sort_values(ascending=False); tot=q.sum()
    print(' best5 q share',round(q.head(5).sum()/tot,2),'best10',round(q.head(10).sum()/tot,2),'worst q',q.index[-1],round(q.iloc[-1]/1e6,2),'worst5 sum M',round(q.tail(5).sum()/1e6,2),'pos quarters',(q>0).mean().round(2))
    print(' best3 yrs share',(y.net.sort_values(ascending=False).head(3).sum()/y.net.sum()).round(2),'2009 net M',y.net.loc[2009].round(2))
    nm=p.groupby('permno').net_pnl.sum().sort_values(ascending=False)
    print(' top20 names share',round(nm.head(20).sum()/tot,3),'top100',round(nm.head(100).sum()/tot,3),'n names',len(nm))
    ps=p.net_pnl.sort_values(ascending=False); n=len(ps)
    print(' top1% trades share of net',round(ps.head(int(n*.01)).sum()/tot,2),'bottom1%',round(ps.tail(int(n*.01)).sum()/tot,2),'top 0.1%',round(ps.head(int(n*.001)).sum()/tot,2))
    # ex-top-10-quarters sharpe: remove best 10 quarters' days
    qs=set(q.head(10).index.astype(str)); dd2=dd.copy(); dd2['q']=dd2.index.to_period('Q').astype(str)
    # 'ann_quarter' vs calendar quarter differ in hold, approximate by calendar quarter of day0 -> skip; do rolling: remove best 10 calendar quarters of daily pnl
    qc=dd2.groupby('q').daily_pnl.sum().sort_values(ascending=False); keep=dd2[~dd2.q.isin(qc.head(10).index)]
    print(' daily: best10 calendar-q share of pnl',round(qc.head(10).sum()/qc.sum(),2),' ann ret ex best10 cal-q',round(keep.daily_return.mean()*252,4), 'vs all',round(dd2.daily_return.mean()*252,4))
