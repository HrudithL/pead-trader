"""Sleeve combination + walk-forward allocator.  python meta/combine.py"""
import sys; sys.path.insert(0,'meta')
import numpy as np, pandas as pd
from run_meta import metrics, START, cal, mret
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/"
def ld(n): return pd.read_csv(SRC+n+"/daily.csv.gz",parse_dates=['date']).set_index('date')
idx=pd.Series(mret,index=cal)
sdb=pd.read_parquet('meta/out/sdb_rule_daily.parquet').set_index('date')['daily_return']
# SDB variant that holds cash instead of index (lower beta)
# option sleeve: premium capital = 20% of sleeve NAV => per unit of capital = r_o/0.20 ; unused premium sits in cash
o=ld('options_cap20'); r_o=o['daily_return']; r_ow=ld('options_worst_case')['daily_return']
# long/short sleeve scaled to NEVER need borrowing: capital need = long + 1.5*short (Reg-T 150% on shorts)
c=ld('calm_rank_tilt'); need=(c.long_exposure+1.5*(-c.short_exposure))/c.nav
k=1.0/need.max(); fee=0.02/252*(-c.short_exposure/c.nav)
r_ls=k*c['daily_return']-k*fee
print(f"L/S sleeve: peak capital need {need.max():.2f}x NAV -> scale k={k:.2f}; avg need {need.mean():.2f}")
S=pd.DataFrame({'SDB':sdb,'OPT_per_cap':r_o/0.20,'OPT_worst_per_cap':r_ow/0.20,'LS':r_ls,'INDEX':idx}).loc[START:'2013-12-31'].fillna(0)
print(S.corr().round(2))
def show(label,r):
    m=metrics(r); print(f"{label:46s} {m['ann']*100:6.2f}% vol {m['vol']*100:5.2f}% Sh {m['sharpe']:5.2f} DD {m['maxdd']*100:6.1f}%"); return m
for n in ['SDB','OPT_per_cap','LS','INDEX']: show(n+" (per unit capital)",S[n])
def mix(w,opt='OPT_per_cap'): return w[0]*S['SDB']+w[1]*S[opt]+w[2]*S['LS']
print("\n-- fixed splits (weights = SDB / options-capital / L-S capital; sum = 1, no borrowing) --")
profiles={'Growth  80/10/10':(0.8,0.1,0.1),'Balanced 60/20/20':(0.6,0.2,0.2),'Defensive 30/30/40':(0.3,0.3,0.4),'Options-free 70/0/30':(0.7,0,0.3)}
for k_,w in profiles.items():
    show(k_,mix(w)); 
    if w[1]>0: show("   ... options at worst-case marks",mix(w,'OPT_worst_per_cap'))
# walk-forward allocator: each Jan, weights ∝ max(trailing-3y Sharpe,0) shrunk 50% to equal, sum=1, min 10% per sleeve; uses only past data
names=['SDB','OPT_per_cap','LS']
W=pd.DataFrame(index=S.index,columns=names,dtype=float)
for yr in range(2000,2014):
    a=pd.Timestamp(f"{yr}-01-01"); past=S.loc[a-pd.Timedelta(days=365*3):a-pd.Timedelta(days=1),names]
    if len(past)<250: w=np.ones(3)/3
    else:
        sh=(past.mean()/past.std()*np.sqrt(252)).clip(lower=0.05); w=(sh/sh.sum()).to_numpy()
        w=0.5*w+0.5/3
    w=np.maximum(w,0.10); w=w/w.sum()
    W.loc[S.index.year==yr]=w
r_wf=(W*S[names]).sum(axis=1)
print("\n-- walk-forward allocator (trailing 3y Sharpe, shrunk, floors 10%) --")
show("walk-forward allocator",r_wf); show("   ... options at worst-case marks",(W*S[['SDB','OPT_worst_per_cap','LS']].to_numpy()).sum(axis=1))
print(W.groupby(W.index.year).first().round(2).T.to_string())
show("INDEX buy&hold",S['INDEX'])
yrs=lambda r:(1+r).groupby(r.index.year).prod()-1
pd.DataFrame({'balanced':yrs(mix(profiles['Balanced 60/20/20'])),'walkfwd':yrs(r_wf),'SDB':yrs(S['SDB']),'index':yrs(S['INDEX'])}).round(3).to_csv('meta/out/yearly_returns.csv')
print((pd.DataFrame({'balanced':yrs(mix(profiles['Balanced 60/20/20'])),'walkfwd':yrs(r_wf),'SDB':yrs(S['SDB']),'index':yrs(S['INDEX'])})*100).round(1).T.to_string())
S.to_parquet('meta/out/sleeves.parquet'); W.to_parquet('meta/out/wf_weights.parquet')
