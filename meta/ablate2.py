import sys; sys.path.insert(0,'meta')
from analyze_sdb import *
pit=ev.pit_sue_pct.to_numpy(); liq=ev.liquidity_quintile.to_numpy(float); beta=ev.beta_252d.to_numpy(float)
def rule(th, bmax=99): return lambda e,c: np.where((pit[c]>=th)&(beta[c]<bmax), 0.05, -1.0)
for th in [0.7,0.8,0.9,0.95]:
    go(f"SUE>={th}, invvar, cap.80",sel_fn=rule(th),gross_cap=0.80)
go("SUE>=0.9 beta<1.3",sel_fn=rule(0.9,1.3),gross_cap=0.80)
go("SUE>=0.9 beta<1.0",sel_fn=rule(0.9,1.0),gross_cap=0.80)
go("SUE>=0.9 cost x2",sel_fn=rule(0.9),gross_cap=0.80,cost_mult=2)
go("SUE>=0.9 cost x3",sel_fn=rule(0.9),gross_cap=0.80,cost_mult=3)
go("SUE>=0.9 cash fill",sel_fn=rule(0.9),gross_cap=0.60,fill="cash")
o=go("SUE>=0.9 (chosen)",sel_fn=rule(0.9),gross_cap=0.80)
r=window(o['daily'])
for a,b in [("2000-01-01","2006-12-31"),("2007-01-01","2013-12-31")]:
    m=metrics(r.loc[a:b]); mi=metrics(mkt_s.loc[a:b]); print(a[:4],b[:4],f"rule {m['ann']*100:.2f}% Sh {m['sharpe']:.2f} DD {m['maxdd']*100:.1f} | index {mi['ann']*100:.2f}% Sh {mi['sharpe']:.2f}")
o['daily'].to_parquet('meta/out/sdb_rule_daily.parquet')
