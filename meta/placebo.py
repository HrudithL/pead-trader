import sys; sys.path.insert(0,'meta')
from analyze_sdb import *
rng=np.random.default_rng(0)
for seed in range(3):
    rng=np.random.default_rng(seed)
    pit=ev.pit_sue_pct.to_numpy().copy()
    for q,ix in ev.groupby('ann_quarter').indices.items(): pit[ix]=rng.permutation(pit[ix])
    o=S.run(ev,P,mret,cal,start=START,sel_fn=lambda e,c: np.where(pit[c]>=0.9,0.05,-1.0),gross_cap=0.8,min_train=0)
    r=window(o['daily']); m=metrics(r); a=ab(r,mkt_s); print(f"placebo seed {seed}: ret {m['ann']*100:.2f}% alpha {a['alpha']*100:.2f}% t {a['tstat']:.1f} beta {a['beta']:.2f}")
