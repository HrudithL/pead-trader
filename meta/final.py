"""Final strategy: Drift Core.  python meta/final.py   (writes meta/out/final_*.csv)"""
import sys; sys.path.insert(0,'meta')
from analyze_sdb import *
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/"
pit=ev.pit_sue_pct.to_numpy()
rule=lambda e,c: np.where(pit[c]>=0.9,0.05,-1.0)
ld=lambda n: pd.read_csv(SRC+n+"/daily.csv.gz",parse_dates=['date']).set_index('date')['daily_return']
idxs=pd.Series(mret,index=cal)
rows=[]
def rec(label,r,a,b):
    m=metrics(r.loc[a:b]); x=ab(r.loc[a:b],idxs.loc[a:b]); mi=metrics(idxs.loc[a:b])
    rows.append(dict(strategy=label,window=f"{a[:4]}-{b[:4]}",ret=m['ann'],vol=m['vol'],sharpe=m['sharpe'],maxdd=m['maxdd'],beta=x['beta'],alpha=x['alpha'],alpha_t=x['tstat'],index_ret=mi['ann'],index_sharpe=mi['sharpe'],index_dd=mi['maxdd']))
for a,b,st in [("1997-07-01","2013-12-31","1997-07-01"),("2000-01-03","2013-12-31","2000-01-03"),("2008-01-01","2013-12-31","2000-01-03")]:
    o=S.run(ev,P,mret,cal,start=st,sel_fn=rule,gross_cap=0.80,min_train=0)
    d=o['daily'].set_index('date'); r=d['daily_return']
    chk=d.hold_over_nav.loc[st:].max()
    rec("Drift Core: events only (rest in index)",r,a,b)
    ro=ld('options_cap20'); rw=ld('options_worst_case')
    # options satellite: premium capital <=20% of NAV; the other 80% runs Drift Core; unused premium capital stays in cash (conservative)
    rec("Drift Core 80% + options satellite 20% (corrected marks)",0.8*r+ro.reindex(r.index).fillna(0),a,b)
    rec("Drift Core 80% + options satellite 20% (dead options = 0)",0.8*r+rw.reindex(r.index).fillna(0),a,b)
    o2=S.run(ev,P,mret,cal,start=st,sel_fn=rule,gross_cap=0.55,fill="cash",min_train=0)
    rec("Drift Core cash-filled (no index; lower beta)",o2['daily'].set_index('date')['daily_return'],a,b)
    print(f"start {st}: max holdings/NAV {chk:.3f}, events {len(o['chosen'])}")
    rec("INDEX (CRSP value-weighted)",idxs,a,b)
    for nm in ['calm_rank_tilt','calm_weighted','hedged_blend','market_boost_max']:
        rec(f"[published, levered/legacy] {nm}",ld(nm),a,b)
R=pd.DataFrame(rows); R.to_csv('meta/out/final_table.csv',index=False)
pd.options.display.width=250
for w,g in R.groupby('window',sort=False):
    print("\n==",w); print(g.drop(columns='window').assign(**{c:(g[c]*100).round(2) for c in ['ret','vol','maxdd','alpha','index_ret','index_dd']}).round(2).to_string(index=False))
