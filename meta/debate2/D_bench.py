from D_variants import *
ews=pd.Series(ew,index=cal)
px=(1+vws).cumprod(); ma=px.rolling(200).mean(); on=(px>ma).shift(1).fillna(True).astype(float)
tr=vws*on
pit=ev.pit_sue_pct.to_numpy()
def line(lbl,r):
    o=[]
    for k,(a,b) in WINS.items():
        m=metrics(r.loc[a:b]); o.append(f"{k}: {m['ann']*100:5.1f}% Sh{m['sharpe']:.2f} DD{m['maxdd']*100:.0f}")
    print(f"{lbl:28s}"," | ".join(o))
line("vw index",vws);line("ew index",ews);line("vw trend200 (cash off)",tr)
sel=lambda e,c: np.where(pit[c]>=0.9,0.05,-1.0)
for lbl,kw,bm in [("ewfill",dict(fillret=ew),ews),("trend200",dict(fillret=vw*on.to_numpy()),tr),("cashfill",dict(fill="cash"),None),("base",{},vws)]:
    r,n,h=go(sel,**kw); line(lbl,r)
    if bm is not None:
        for k,(a,b) in WINS.items():
            x=ab(r.loc[a:b],bm.loc[a:b]); print(f"    alpha vs own fill {k}: {x['alpha']*100:.2f}% t{x['t']:.1f} beta{x['beta']:.2f}")
# placebo vs own fill for ew
for sd in range(3):
    r,_,_=go(lambda e,c,pp=pct_placebo(pit,sd): np.where(pp[c]>=0.9,0.05,-1.0),fillret=ew)
    x=ab(r.loc[WINS['full'][0]:WINS['full'][1]],ews.loc[WINS['full'][0]:WINS['full'][1]]); print("ew placebo alpha vs ew",sd,round(x['alpha']*100,2),round(x['t'],1), round(metrics(r.loc[START:'2013'])['ann']*100,2))
