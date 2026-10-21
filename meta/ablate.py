import sys; sys.path.insert(0,'meta')
from analyze_sdb import *
pit=ev.pit_sue_pct.to_numpy(); liq=ev.liquidity_quintile.to_numpy(float); beta=ev.beta_252d.to_numpy(float)
def top(th): return lambda e,c: np.where(pit[c]>=th, 0.05, -1.0)
def thin_top(e,c): return np.where((pit[c]>=0.9)&(liq[c]<=2), 0.05, -1.0)
def thin_lowb_top(e,c): return np.where((pit[c]>=0.8)&(liq[c]<=2)&(beta[c]<0.9), 0.05, -1.0)
def allev(e,c): return np.full(len(c),0.05)
print("--- controls (same engine, no model) ---")
go("ALL events equal-ish (universe control)",sel_fn=allev,risk_power=0)
go("pit SUE>=0.9, equal weight",sel_fn=top(0.9),risk_power=0)
go("pit SUE>=0.9 thin, equal weight",sel_fn=thin_top,risk_power=0)
go("SUE>=0.8 thin lowbeta, eq weight",sel_fn=thin_lowb_top,risk_power=0)
go("pit SUE>=0.9, inv-var weight",sel_fn=top(0.9))
print("--- model, halves ---")
o=go("model base")
r=window(o['daily'])
for a,b in [("2000-01-01","2006-12-31"),("2007-01-01","2013-12-31")]:
    m=metrics(r.loc[a:b]); mi=metrics(mkt_s.loc[a:b]); print(a[:4],b[:4],f"model {m['ann']*100:.2f}% Sh {m['sharpe']:.2f} DD {m['maxdd']*100:.1f} | index {mi['ann']*100:.2f}% Sh {mi['sharpe']:.2f} DD {mi['maxdd']*100:.1f}")
cf=np.array([c for q,c in o['coefs']]); import simulate
names=['const']+simulate.FEATURES
print(pd.DataFrame({'first':cf[0],'last':cf[-1],'mean':cf.mean(0)},index=names).round(4))
