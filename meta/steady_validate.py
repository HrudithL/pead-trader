"""Validation of Steady Drift (cash fill, ADV>=$2M, sector cap 20%, 10% vol target).  python meta/steady_validate.py"""
import sys; sys.path.insert(0, 'meta'); sys.path.insert(0, 'meta/debate2')
import numpy as np, pandas as pd
import steady as T
from steady import *
adv_ok = np.nan_to_num(adv, nan=0) >= 2e6
def run_cfg(pitv, cost_mult=1.0, tgt=0.10, fill="cash", start="1997-07-01", **kw):
    ok = (pitv >= 0.9) & adv_ok
    o = S.run(ev, P, mret, cal, start=start, sel_fn=lambda e, c: np.where(ok[c], 0.05, -1.0), gross_cap=0.70 if fill == "cash" else 0.75,
              fill=fill, min_train=0, sector_cap=0.20, cost_mult=cost_mult, **kw)
    d = o['daily'].set_index('date'); r = d['daily_return'].copy()
    if fill == "cash":
        idle = (1 - d['hold_value'].shift(1) / d['nav'].shift(1)).clip(lower=0).fillna(0); r = r + idle * rf
    r = r.loc[start:]
    return vol_target(r, tgt)[0], d['hold_over_nav'].loc[start:].max()
def line(label, r, a="1997-07-01", b="2013-12-31"):
    m = metrics(r.loc[a:b]); x = ab(r.loc[a:b], idx.loc[a:b]); mi = metrics(idx.loc[a:b])
    print(f"{label:34s} {m['ann']*100:6.2f}% vol {m['vol']*100:5.2f}% Sh {m['sharpe']:5.2f} DD {m['maxdd']*100:6.1f}% | alpha {x['alpha']*100:5.2f}% (t {x['tstat']:.1f}) beta {x['beta']:.2f} | index {mi['ann']*100:5.2f}% DD {mi['maxdd']*100:5.1f}%")
final, mh = run_cfg(pit); print("max holdings/NAV (no-borrow check):", round(mh, 3))
line("Steady Drift 10% vol target", final)
for a, b in [("1997-07-01", "2001-12-31"), ("2002-01-01", "2007-12-31"), ("2008-01-01", "2013-12-31"), ("2000-01-03", "2013-12-31"), ("2010-01-01", "2013-12-31")]:
    line(f"  window {a[:4]}-{b[:4]}", final, a, b)
print("\n-- costs --")
for cm in [2.0, 3.0]: line(f"cost x{cm:g}", run_cfg(pit, cost_mult=cm)[0])
print("\n-- placebo: SUE ranks shuffled within each quarter (3 seeds) --")
for seed in range(3):
    rng = np.random.default_rng(seed); pp = pit.copy()
    for q, ix in ev.groupby('ann_quarter').indices.items(): pp[ix] = rng.permutation(pp[ix])
    line(f"placebo seed {seed}", run_cfg(pp)[0])
print("\n-- calendar years: Steady Drift vs index vs T-bill --")
y = lambda r: ((1 + r).groupby(r.index.year).prod() - 1) * 100
Y = pd.DataFrame({'steady': y(final), 'index': y(idx.loc[final.index]), 'tbill': y(rf.loc[final.index])}).round(1)
print(Y.T.to_string()); Y.to_csv('meta/out/steady_years.csv')
q = (1 + final).groupby(final.index.to_period('Q')).prod() - 1
print(f"positive quarters {100*(q>0).mean():.0f}%  worst quarter {100*q.min():.1f}%  positive years {100*(Y.steady>0).mean():.0f}%  worst 12m {100*((1+final).rolling(252).apply(np.prod,raw=True)-1).min():.1f}%")
print("avg exposure e:", round(vol_target(run_cfg(pit, tgt=None)[0], 0.10)[1].mean(), 2))
final.to_frame('daily_return').to_parquet('meta/out/steady_final_daily.parquet')
