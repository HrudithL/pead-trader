import pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
SRC='C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/'
rng=np.random.default_rng(0)
def D(s): return pd.read_csv(SRC+s+'/daily.csv.gz',parse_dates=['date']).set_index('date')
def sh(r): return r.mean()/r.std()*np.sqrt(252)
print('=== 5. calm_rank_tilt vs calm_weighted: paired block bootstrap on Sharpe diff')
a=D('calm_rank_tilt').daily_return['1996':]; b=D('calm_weighted').daily_return['1996':]; c=D('balanced_leverage').daily_return['1996':]
for nm,x,y in [('crt-cw',a,b),('cw-bl',b,c),('crt-bl',a,c)]:
    n=len(x); B=21; nb=n//B; diffs=[]
    X=x.values;Y=y.values
    for _ in range(2000):
        idx=(rng.integers(0,n-B,nb)[:,None]+np.arange(B)).ravel()
        diffs.append(sh(pd.Series(X[idx]))-sh(pd.Series(Y[idx])))
    diffs=np.array(diffs); print(nm,'diff',round(sh(x)-sh(y),2),'90% CI',np.percentile(diffs,[5,95]).round(2),'P(diff<=0)',(diffs<=0).mean().round(3))
# Sharpe SE
for nm,x in [('calm_rank_tilt',a),('calm_weighted',b)]:
    print(nm,'Sharpe',round(sh(x),2),'approx SE',round(np.sqrt((1+0.5*sh(x)**2/252)/(len(x)/252)),2))
# deflated: count of variants searched in docs: trim4, exp4, ivol^k 4, (1.02-v)^k 3, drop deciles 3, short cells 2, size tilt 2, base unit/caps 4 ~ 26 variants
from scipy.stats import norm
N=26; sr0=np.sqrt(2*np.log(N))*0.22  # annual sharpe std across trials ~ SE of Sharpe approx 0.24 over 18y
se=np.sqrt(1/18.9)
print('expected max Sharpe among',N,'null trials with SE',round(se,2),':',round(se*norm.ppf(1-1/(N*1.0)),2),'(sharpe benchmark for zero true)')
print('\n=== 6. options')
for s in ['options_cap20','options_cap30','options_worst_case','options_cap20_legacy']:
    o=pd.read_parquet(SRC+s+'/positions.parquet'); o['yr']=o.day0_date.dt.year
    tot=o.net_pnl.sum(); y=o.groupby('yr').net_pnl.sum()
    print(s,'n',len(o),'net M',round(tot/1e6,2),'premium M',round(o.premium_paid.sum()/1e6,1),'top2 yrs(2003,2013) share',round((y.get(2003,0)+y.get(2013,0))/tot,2),'ex those two yrs net M',round((tot-y.get(2003,0)-y.get(2013,0))/1e6,2))
    print('  ',(y/1e6).round(2).to_dict())
o=pd.read_parquet(SRC+'options_cap20/positions.parquet'); o['yr']=o.day0_date.dt.year
print(o[['kelly_premium','premium_paid','cost','contracts']].describe().round(3))
print('premium<0.25 share of trades',(o.kelly_premium<0.25).mean().round(2),' <0.5',(o.kelly_premium<0.5).mean().round(2),' premium$-share <0.5',(o.premium_paid[o.kelly_premium<0.5].sum()/o.premium_paid.sum()).round(2))
print('cost/premium_paid',(o.cost.sum()/o.premium_paid.sum()).round(3), 'median per-trade',(o.cost/o.premium_paid).median().round(3))
# dead quotes
for h in ['60d']:
    bid=o[f'kelly_fwd_bid_{h}']; ask=o[f'kelly_fwd_ask_{h}']; mid=o[f'kelly_fwd_mid_{h}']
    print('bid nan',bid.isna().mean().round(3),'bid==0',(bid==0).mean().round(3),'mid nan',mid.isna().mean().round(3),'ask nan',ask.isna().mean().round(3))
# entry spread proxy via 1d quote spread
b1=o.kelly_fwd_bid_1d;a1=o.kelly_fwd_ask_1d;m1=o.kelly_fwd_mid_1d
hs=((a1-b1)/2)
print('1d half-spread $ median',hs.median().round(3),' as % of entry premium median',(hs/o.kelly_premium).median().round(3),'mean',(hs/o.kelly_premium).clip(upper=2).mean().round(3),'share bid1d==0',(b1==0).mean().round(3))
# alt P&L: pay premium + half spread (use 1d spread as proxy, cap at premium), exit at bid (dead->0 if bid nan or 0), 60d
prem=o.kelly_premium; entry=prem+hs.fillna(hs.median())
for lab,exitpx in [('exit@mid(nan->0)',o.kelly_fwd_mid_60d.fillna(0)),('exit@bid(nan->0)',o.kelly_fwd_bid_60d.fillna(0)),('exit@ret col',None)]:
    if exitpx is None: continue
    r=exitpx/entry-1; w=o.premium_paid
    print(lab,'premium-weighted return on premium',round((r*w).sum()/w.sum(),3))
print('reported: sum net/premium',round(o.net_pnl.sum()/o.premium_paid.sum(),3),'; ret_on_premium wtd',round((o.ret_on_premium*o.premium_paid).sum()/o.premium_paid.sum(),3))
w=o.premium_paid; r_mid_entry=o.kelly_fwd_bid_60d.fillna(0)/prem-1
print('exit@bid, mid entry, weighted',round((r_mid_entry*w).sum()/w.sum(),3))
r2=o.kelly_fwd_bid_60d.fillna(0)/entry-1; print('exit@bid, entry mid+hs',round((r2*w).sum()/w.sum(),3))
# trades: win rate and top 1% share
ps=o.net_pnl.sort_values(ascending=False);n=len(ps)
print('top1% share',round(ps.head(int(n*.01)).sum()/ps.sum(),2),'top10%',round(ps.head(int(n*.1)).sum()/ps.sum(),2),'win',round((ps>0).mean(),2))
print('calls share',(o.kelly_side.astype(str).str.lower().str.startswith('c')).mean().round(3), o.kelly_side.value_counts().to_dict())
# daily options
d=D('options_cap20'); print('opt cap20 by year ret',d.daily_return.groupby(d.index.year).apply(lambda r:(1+r).prod()-1).round(3).to_dict())
d2=d.daily_return; yrs=d2.groupby(d2.index.year).apply(lambda r:(1+r).prod()-1)
ex=d2[~d2.index.year.isin([2003,2013])]; print('ann ex 2003,2013 (arith*252)',round(ex.mean()*252,4),'sh',round(sh(ex),2),'vs all',round(d2.mean()*252,4))
print('cap20 sharpe by era', {k:round(sh(d2[a:b]),2) for k,(a,b) in {'95-01':('1995','2001'),'02-07':('2002','2007'),'08-13':('2008','2013')}.items()})
