import time
from D_lib import *
t=time.time()
pit=ev.pit_sue_pct.to_numpy()
r,n,h=go(lambda e,c: np.where(pit[c]>=0.9,0.05,-1.0))
s=summarize(r); print(n,h,{k:(round(v[0]['ann']*100,2),round(v[0]['sharpe'],2),round(v[1]['alpha']*100,2),round(v[1]['t'],1)) for k,v in s.items()}, time.time()-t)
print(ev.d0prc.describe(), ev.d0ret.describe())
