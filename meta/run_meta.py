"""Run the unlevered Selective Drift Book and its ablations.  python meta/run_meta.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulate as S

OUT = Path(__file__).resolve().parent / "out"
START = "2000-01-03"
ev = pd.read_parquet(OUT / "events_pit.parquet")
paths = pd.read_parquet(OUT / "event_paths.parquet")
mk = pd.read_parquet(OUT / "market.parquet")
cal = pd.DatetimeIndex(mk["date"])
mret = mk["vwretd"].to_numpy(float)
P = S.prepare(ev, paths, mk)


def metrics(ret: pd.Series, rf=0.0):
    ret = ret.dropna()
    n = len(ret)
    nav = (1 + ret).cumprod()
    yrs = n / 252
    ann = nav.iloc[-1] ** (1 / yrs) - 1
    vol = ret.std() * np.sqrt(252)
    dd = (nav / nav.cummax() - 1).min()
    return dict(ann=ann, vol=vol, sharpe=(ret.mean() - rf / 252) * 252 / vol if vol else np.nan, maxdd=dd)


def window(daily, a=START, b="2013-12-31"):
    d = daily.set_index("date")
    return d.loc[a:b, "daily_return"]


def show(label, r):
    m = metrics(r)
    print(f"{label:38s} {m['ann']*100:6.2f}%  vol {m['vol']*100:5.2f}%  Sharpe {m['sharpe']:5.2f}  maxDD {m['maxdd']*100:6.1f}%")
    return m


if __name__ == "__main__":
    mkt_s = pd.Series(mret, index=cal)
    print("== benchmarks, %s-2013 ==" % START[:4])
    show("CRSP value-weighted index (buy&hold)", mkt_s.loc[START:])
    SRC = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results")
    for nm in ["calm_rank_tilt", "calm_weighted", "balanced_leverage", "options_cap20", "hedged_blend", "market_boost"]:
        d = pd.read_csv(SRC / nm / "daily.csv.gz", parse_dates=["date"]).set_index("date")["daily_return"]
        show(f"[existing, as published] {nm}", d.loc[START:"2013-12-31"])
    print("\n== Selective Drift Book (long-only, unlevered, walk-forward) ==")
    base = S.run(ev, P, mret, cal, start=START)
    d = base["daily"]
    r = show("base (index fill, gross<=85%)", window(d))
    print("   events traded:", len(base["chosen"]), " max holdings/NAV: %.2f" % d["hold_over_nav"].loc[d.date >= START].max())
    d.to_parquet(OUT / "sdb_base_daily.parquet")
