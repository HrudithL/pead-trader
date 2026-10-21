import sys; sys.path.insert(0,'meta')
import numpy as np, pandas as pd
from run_meta import *
def ab(r, m):
    x=pd.concat([r,m],axis=1).dropna(); x.columns=['r','m']
    b=np.cov(x.r,x.m)[0,1]/x.m.var(); a=(x.r-b*x.m).mean()*252
    ex=x.r-x.m; te=ex.std()*np.sqrt(252)
    t=(x.r-b*x.m).mean()/(x.r-b*x.m).std()*np.sqrt(len(x))
    return dict(alpha=a,beta=b,tstat=t,excess=ex.mean()*252,IR=ex.mean()*252/te)
mkt_s=pd.Series(mret,index=cal).loc[START:"2013-12-31"]
def go(label, **kw):
    o=S.run(ev,P,mret,cal,start=START,**kw); d=o['daily']; r=window(d)
    m=metrics(r); a=ab(r,mkt_s)
    print(f"{label:34s} ret {m['ann']*100:5.2f}% vol {m['vol']*100:5.2f}% Sh {m['sharpe']:4.2f} DD {m['maxdd']*100:6.1f}% | beta {a['beta']:.2f} alpha {a['alpha']*100:5.2f}% (t {a['tstat']:.1f}) excess {a['excess']*100:5.2f}% IR {a['IR']:.2f} | n={len(o['chosen'])} maxH/NAV {d.hold_over_nav.loc[d.date>=START].max():.3f}")
    return o
if __name__=="__main__":
    o=go("base")
    d=o['daily'].set_index('date'); r=d.daily_return
    y=(1+r.loc[START:]).groupby(r.loc[START:].index.year).prod()-1; yi=(1+mkt_s).groupby(mkt_s.index.year).prod()-1
    print(pd.DataFrame({'sdb':y,'index':yi,'diff':y-yi}).round(3).T.to_string())
    print("avg holdings/NAV", d.hold_over_nav.loc[START:].mean().round(3))
    for th in [0.0,0.01,0.025,0.04]: go(f"theta={th}",theta=th)
    go("cost x2",cost_mult=2.0); go("cost x3",cost_mult=3.0)
    go("cash fill (no index)",fill="cash")
    go("risk_power=0 (no inv-var)",risk_power=0)
    go("gross_cap 0.5",gross_cap=0.5); go("gross_cap 0.95",gross_cap=0.95)
    go("name_cap 0.5%",name_cap=0.005)
