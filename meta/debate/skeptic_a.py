import pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
SRC='C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/'
def daily(s):
    d=pd.read_csv(SRC+s+'/daily.csv.gz',parse_dates=['date']); return d.set_index('date')
def stats(r):
    r=r.dropna(); n=len(r); yrs=n/252
    g=(1+r).prod()**(1/yrs)-1; v=r.std()*np.sqrt(252); sh=r.mean()/r.std()*np.sqrt(252) if r.std()>0 else np.nan
    nav=(1+r).cumprod(); dd=(nav/nav.cummax()-1).min()
    return dict(ann=g,vol=v,sh=sh,dd=dd)
print('=== 1. leverage ===')
rows=[]
strats=['extreme_picks','graded_bets','size_balanced','conviction_tilt','sector_balanced','balanced_leverage','calm_weighted','calm_rank_tilt','market_exposed','market_boost','market_boost_max','balanced_leverage_max','calm_rank_levered']
for s in strats:
    d=daily(s); d=d[d.index>='1996-01-01']
    g=(d.gross_exposure/d.nav.shift(1)).replace([np.inf],np.nan)
    if 'overlay_notional' in d: g=(d.gross_exposure+d.overlay_notional.abs())/d.nav.shift(1)
    st=stats(d.daily_return)
    k=1/g.max(); k99=1/g.quantile(.99); k1=1/g.mean()
    # strict: scale so max gross <=1 ; also mean gross =1 (lenient)
    r={'s':s,'ann':st['ann'],'sh':st['sh'],'g_mean':g.mean(),'g_p99':g.quantile(.99),'g_max':g.max(),
       'ann@max<=1':stats(d.daily_return*k)['ann'],'ann@p99<=1':stats(d.daily_return*min(k99,99))['ann'],'ann@mean=1':stats(d.daily_return*k1)['ann'],'dd@mean=1':stats(d.daily_return*k1)['dd']}
    rows.append(r)
L=pd.DataFrame(rows).round(3); print(L.to_string()); L.to_csv('skeptic_leverage.csv',index=False)
# overlay decomposition
d=daily('market_boost_max'); print('overlay pnl share', d.pnl_overlay.sum()/d.daily_pnl.sum(), 'overlay_notional/nav mean',(d.overlay_notional/d.nav.shift(1)).mean())
ex=(d.daily_pnl-d.pnl_overlay)/d.nav.shift(1); print('mb_max ex-overlay ret (nav incl overlay pnl) approx',stats(ex.dropna()))
bm=daily('balanced_leverage_max'); print('S6max',stats(bm.daily_return))
mk=(d.pnl_overlay/d.overlay_notional.shift(1)).replace([np.inf,-np.inf],np.nan).dropna(); print('implied mkt ann',(1+mk).prod()**(252/len(mk))-1, len(mk))
mk.to_frame('mkt').to_csv('skeptic_mkt_implied.csv')
