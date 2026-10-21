"""Steady Drift: long-only, no-borrowing, risk-targeted PEAD book.  python meta/steady.py

Pre-registered design (all causal):
  signal   top decile of point-in-time SUE (trailing-365d pool)            [fixed in meta/final.py; sweeps flat]
  universe 21-day dollar volume >= $2M                                      [liquidity floor; sweeps flat]
  sizing   inverse idiosyncratic variance, no FF12 sector > 20% of a quarter's budget, <=2% NAV per name, <=5% ADV
  hold     60 sessions, entered at day0 close
  idle     cash earning the T-bill (typed-from-memory annual means: ASSUMPTION), or the index
  risk     exposure e_t = min(1, target_vol / trailing-60d realised vol of the book), set from data through t-1;
           the rest in cash earning the T-bill; 10 bp cost on each change in e
Variants tried (count reported in docs): see VARIANTS below.
"""
import sys; sys.path.insert(0, 'meta'); sys.path.insert(0, 'meta/debate2')
import numpy as np, pandas as pd
from run_meta import *
from analyze_sdb import ab
from C_common import TBILL
pit = ev.pit_sue_pct.to_numpy(); adv = ev.avg_dollar_volume_21d.to_numpy(float)
idx = pd.Series(mret, index=cal)
rf = pd.Series([TBILL[d.year] / 100 / 252 for d in cal], index=cal)

def sel(adv_floor=0.0):
    ok = (pit >= 0.9) & (np.nan_to_num(adv, nan=0) >= adv_floor)
    return lambda e, c: np.where(ok[c], 0.05, -1.0)

def book(fill, adv_floor=0.0, sector_cap=None, start="1997-07-01"):
    o = S.run(ev, P, mret, cal, start=start, sel_fn=sel(adv_floor), gross_cap=0.80 if fill == "index" else 0.70,
              fill=fill, min_train=0, sector_cap=sector_cap)
    d = o['daily'].set_index('date')
    r = d['daily_return'].copy()
    if fill == "cash":                                   # idle cash earns the T-bill
        idle = (1 - d['hold_value'].shift(1) / d['nav'].shift(1)).clip(lower=0).fillna(0)
        r = r + idle * rf
    return r.loc[start:], d['hold_over_nav'].loc[start:].max()

def vol_target(r, target, lookback=60, cost_bp=10):
    if target is None: return r, pd.Series(1.0, index=r.index)
    vol = r.rolling(lookback, min_periods=20).std().shift(1) * np.sqrt(252)
    e = (target / vol).clip(upper=1.0).fillna(1.0)
    cost = e.diff().abs().fillna(0) * cost_bp / 1e4
    return e * r + (1 - e) * rf.loc[r.index] - cost, e

def row(label, r, a="1997-07-01", b="2013-12-31"):
    m = metrics(r.loc[a:b]); x = ab(r.loc[a:b], idx.loc[a:b]); mi = metrics(idx.loc[a:b])
    calmar = m['ann'] / abs(m['maxdd'])
    return dict(variant=label, window=f"{a[:4]}-{b[:4]}", ret=m['ann'], vol=m['vol'], sharpe=m['sharpe'], maxdd=m['maxdd'], calmar=calmar,
                beta=x['beta'], alpha=x['alpha'], alpha_t=x['tstat'], index_ret=mi['ann'])

if __name__ == "__main__":
    rows = []; n = 0
    cfgs = {"A Drift Core (index fill)": dict(fill="index"),
            "B +ADV floor $2M": dict(fill="index", adv_floor=2e6),
            "C +sector cap 20%": dict(fill="index", adv_floor=2e6, sector_cap=0.20),
            "D C with cash fill (T-bill)": dict(fill="cash", adv_floor=2e6, sector_cap=0.20)}
    res = {}
    for k, c in cfgs.items():
        r, mh = book(**c); res[k] = r; n += 1
        print(f"{k:34s} max hold/NAV {mh:.3f}")
        rows.append(row(k, r))
    for tgt in [0.08, 0.10, 0.12, 0.15]:
        for base in ["C +sector cap 20%", "D C with cash fill (T-bill)"]:
            r, e = vol_target(res[base], tgt); n += 1
            rows.append(row(f"{base[0]} + vol target {int(tgt*100)}%", r)); 
    rows.append(row("INDEX (cap-weighted)", idx.loc["1997-07-01":]))
    rows.append(row("T-bill", rf.loc["1997-07-01":]))
    R = pd.DataFrame(rows); R.to_csv('meta/out/steady_frontier.csv', index=False)
    pd.options.display.width = 220
    f = R.copy()
    for c in ['ret', 'vol', 'maxdd', 'alpha', 'index_ret']: f[c] = (f[c] * 100).round(2)
    print(f.round(2).drop(columns=['window']).to_string(index=False)); print("variants run:", n)
    pd.DataFrame(res).to_parquet('meta/out/steady_base_returns.parquet')
