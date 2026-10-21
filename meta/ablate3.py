import sys; sys.path.insert(0,'meta')
from analyze_sdb import *
pit=ev.pit_sue_pct.to_numpy(); liq=ev.liquidity_quintile.to_numpy(float); beta=ev.beta_252d.to_numpy(float); sz=ev.size_quintile.to_numpy(float)
def rule(th, mask=None): 
    return lambda e,c: np.where((pit[c]>=th)&(True if mask is None else mask[c]), 0.05, -1.0)
print("--- live-fit: restrict universe by size (proxy for S&P-like names) ---")
go("size quintile 5 only (largest)",sel_fn=rule(0.9,sz==5),gross_cap=0.8)
go("size quintiles 4-5",sel_fn=rule(0.9,sz>=4),gross_cap=0.8)
go("size quintiles 1-3",sel_fn=rule(0.9,sz<=3),gross_cap=0.8)
go("liquidity quintile >=4",sel_fn=rule(0.9,liq>=4),gross_cap=0.8)
print("--- delisting stress: events truncated before 60 sessions take an extra loss ---")
trunc=(ev.n_fwd<60).to_numpy(); print("truncated share of SUE>=0.9 events:",trunc[pit>=0.9].mean().round(4))
import copy
for shock in [-0.2,-0.5]:
    e2=ev.copy(); e2.loc[trunc,'excess_hold']+=shock
    # apply shock to last path row return
    P2=dict(P); r=P['r'].copy(); last=np.r_[P['starts'][1:],len(r)]-1
    idx=last[trunc]; r[idx]=(1+r[idx])*(1+shock)-1; P2['r']=r
    o=S.run(e2,P2,mret,cal,start=START,sel_fn=rule(0.9),gross_cap=0.8); m=metrics(window(o['daily'])); print(f"extra {shock:.0%} loss on truncated: ret {m['ann']*100:.2f}% Sh {m['sharpe']:.2f}")
print("--- sub-windows for chosen rule ---")
o=S.run(ev,P,mret,cal,start=START,sel_fn=rule(0.9),gross_cap=0.8); r=window(o['daily'])
for a,b in [("2000-01-01","2003-12-31"),("2004-01-01","2007-12-31"),("2008-01-01","2013-12-31"),("2010-01-01","2013-12-31")]:
    m=metrics(r.loc[a:b]); mi=metrics(mkt_s.loc[a:b]); ax=ab(r.loc[a:b],mkt_s.loc[a:b]); print(a[:4],b[:4],f"rule {m['ann']*100:6.2f}% Sh {m['sharpe']:.2f} | index {mi['ann']*100:6.2f}% Sh {mi['sharpe']:.2f} | alpha {ax['alpha']*100:.2f}% t {ax['tstat']:.1f} beta {ax['beta']:.2f}")
