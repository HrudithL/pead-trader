import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/"
def rd(n): return pd.read_csv(SRC+f'{n}/daily.csv.gz',parse_dates=['date']).set_index('date')
def ann(r): return (1+r).prod()**(252/len(r))-1
def sh(r): return r.mean()/r.std()*np.sqrt(252)
def mdd(r): n=(1+r).cumprod(); return (n/n.cummax()-1).min()
op=pd.read_parquet(SRC+'options_cap20/positions.parquet'); od=rd('options_cap20'); dates=od.index
prem=pd.Series(0.0,index=dates)
for a,p in zip(pd.to_datetime(op.day0_date),op.premium_paid):
    prem[(dates>=a)&(dates<a+pd.Timedelta(days=84))]+=p
og=prem/od.nav.shift(1)
for eq in ['calm_weighted','calm_rank_tilt']:
    e=rd(eq); ge=(e.gross_exposure/e.nav.shift(1)).reindex(dates).fillna(0)
    for oname in ['options_cap20','options_worst_case']:
        o=rd(oname).daily_return.reindex(dates).fillna(0); re_=e.daily_return.reindex(dates).fillna(0)
        best=None
        print(eq,oname)
        for wo in [0,1,2,3,4,5]:
            # find largest we so peak combined gross <=1
            lo,hi=0,1.0
            for _ in range(40):
                m=(lo+hi)/2
                if (m*ge+wo*og.fillna(0)).max()<=1: lo=m
                else: hi=m
            we=lo; r=we*re_+wo*o
            print(f"  opt mult {wo}: eq scale {we:.3f}  ann {ann(r)*100:.2f}%  vol {r.std()*np.sqrt(252)*100:.2f}%  sharpe {sh(r):.2f}  mdd {mdd(r)*100:.1f}%  eras sharpe",
                  [round(sh(r[a:b]),2) for a,b in [('1996','2001'),('2002','2007'),('2008','2013')]])
