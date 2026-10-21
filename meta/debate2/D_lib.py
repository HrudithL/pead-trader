"""Perspective D harness. Reuses meta/simulate.py engine; builds H=90 paths so hold 40/60/90 can be derived."""
import sys, os
from pathlib import Path
import numpy as np, pandas as pd
M = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M))
import simulate as S
SRC = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86")
OUT = M / "out"; DOUT = Path(__file__).resolve().parent
START = "2000-01-03"
ev = pd.read_parquet(OUT / "events_pit.parquet")
mk = pd.read_parquet(SRC / "data/normalized_equity/market_benchmark.parquet", columns=["date", "vwretd", "ewretd"]).drop_duplicates("date").sort_values("date")
cal = pd.DatetimeIndex(mk["date"].to_numpy())
vw = mk.vwretd.fillna(0).to_numpy(float); ew = mk.ewretd.fillna(0).to_numpy(float)
H3 = 90

def build90():
    f = DOUT / "D_cache90.npz"
    if f.exists():
        z = np.load(f); return z["cal"], z["ret"], z["off"], z["evi"], z["d0ret"], z["d0prc"]
    frames = [pd.read_parquet(SRC / f"data/normalized_equity/equity_{y}.parquet", columns=["permno", "date", "ret", "prc"]) for y in range(1995, 2015)]
    eq = pd.concat(frames, ignore_index=True).sort_values(["permno", "date"]).drop_duplicates(["permno", "date"]).reset_index(drop=True)
    eq["ret"] = eq["ret"].fillna(0.0)
    perm = eq["permno"].to_numpy()
    last_row = pd.Series(np.arange(len(eq))).groupby(perm).max()
    key = eq[["permno", "date"]].reset_index().rename(columns={"index": "row", "date": "day0_date"})
    e = ev[["permno", "day0_date"]].merge(key, on=["permno", "day0_date"], how="left")
    row0 = e["row"].to_numpy(); last = last_row.reindex(ev["permno"]).to_numpy()
    n = np.minimum(H3, last - row0).astype(int)
    rets = eq["ret"].to_numpy(); dates = eq["date"].to_numpy(); ci = cal.searchsorted(dates)
    rep = np.repeat(np.arange(len(ev)), n)
    off = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    fr = np.repeat(row0 + 1, n) + off
    d0ret = rets[row0]; d0prc = np.abs(eq["prc"].to_numpy(float)[row0])
    np.savez(f, cal=ci[fr].astype(np.int32), ret=rets[fr].astype(np.float32), off=off.astype(np.int16), evi=rep.astype(np.int32), d0ret=d0ret, d0prc=d0prc)
    return ci[fr].astype(np.int32), rets[fr].astype(np.float32), off.astype(np.int16), rep.astype(np.int32), d0ret, d0prc

_c, _r, _o, _e, d0ret, d0prc = build90()
ev = ev.copy(); ev["d0ret"] = d0ret; ev["d0prc"] = d0prc
ev["d0ex"] = ev["d0ret"] - vw[ev["entry_cal"].to_numpy()]

def make(Hvec):
    """Return (ev2, P) with each event held Hvec[i] sessions (truncated at delisting)."""
    keep = _o < Hvec[_e]
    paths = pd.DataFrame({"ev": _e[keep], "cal": _c[keep], "ret": _r[keep]})
    e2 = ev.copy()
    cs = np.bincount(paths.ev, weights=np.log1p(np.clip(paths.ret.to_numpy(float), -.99, None)), minlength=len(ev))
    cm = np.bincount(paths.ev, weights=np.log1p(vw[paths.cal.to_numpy()]), minlength=len(ev))
    e2["excess_hold"] = np.expm1(cs) - np.expm1(cm)
    e2["exit_cal"] = paths.groupby("ev")["cal"].max().reindex(range(len(ev))).to_numpy()
    return e2, S.prepare(e2, paths, None)

_cache = {}
def getP(H):
    if H not in _cache: _cache[H] = make(np.full(len(ev), H))
    return _cache[H]

def metrics(r):
    r = r.dropna(); nav = (1 + r).cumprod(); yrs = len(r) / 252
    vol = r.std() * np.sqrt(252)
    return dict(ann=nav.iloc[-1] ** (1 / yrs) - 1, vol=vol, sharpe=r.mean() * 252 / vol, maxdd=(nav / nav.cummax() - 1).min())

def ab(r, m):
    x = pd.concat([r, m], axis=1).dropna(); x.columns = ["r", "m"]
    b = np.cov(x.r, x.m)[0, 1] / x.m.var(); res = x.r - b * x.m
    return dict(alpha=res.mean() * 252, beta=b, t=res.mean() / res.std() * np.sqrt(len(x)))

vws = pd.Series(vw, index=cal)
WINS = {"full": ("2000-01-03", "2013-12-31"), "h1": ("2000-01-03", "2006-12-31"), "h2": ("2007-01-01", "2013-12-31"), "0813": ("2008-01-01", "2013-12-31")}

def go(sel, H=60, evp=None, fillret=None, gross_cap=0.8, **kw):
    e2, P = evp if evp is not None else getP(H)
    o = S.run(e2, P, fillret if fillret is not None else vw, cal, start=START, sel_fn=sel, gross_cap=gross_cap, min_train=0, **kw)
    d = o["daily"].set_index("date"); r = d["daily_return"]
    return r, len(o["chosen"]), d.hold_over_nav.loc[START:].max()

def summarize(r):
    out = {}
    for k, (a, b) in WINS.items():
        m = metrics(r.loc[a:b]); x = ab(r.loc[a:b], vws.loc[a:b])
        out[k] = (m, x)
    return out

def pct_placebo(pit, seed):
    rng = np.random.default_rng(seed); p = pit.copy()
    for q, ix in ev.groupby("ann_quarter").indices.items(): p[ix] = rng.permutation(p[ix])
    return p
