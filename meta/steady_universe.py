import sys; sys.path.insert(0,'meta'); sys.path.insert(0,'meta/debate2')
from steady_validate import *   # re-runs validation prints; fine
print("\n== live-fit universes (size quintile of announcers; 5 = largest ~top 20%) ==")
sz = ev.size_quintile.to_numpy(float)
for lab, m in [("size quintile >=4", sz >= 4), ("size quintile 5 only", sz == 5), ("size quintile <=3", sz <= 3)]:
    ok = (pit >= 0.9) & adv_ok & m
    o = S.run(ev, P, mret, cal, start="1997-07-01", sel_fn=lambda e, c: np.where(ok[c], 0.05, -1.0), gross_cap=0.70, fill="cash", min_train=0, sector_cap=0.20)
    d = o['daily'].set_index('date'); r = d['daily_return'] + (1 - d['hold_value'].shift(1) / d['nav'].shift(1)).clip(lower=0).fillna(0) * rf
    line(lab, vol_target(r.loc["1997-07-01":], 0.10)[0]); line("   2008-13", vol_target(r.loc["1997-07-01":], 0.10)[0], "2008-01-01", "2013-12-31")
