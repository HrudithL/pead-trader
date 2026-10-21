"""Shuffled-surprise placebo on the framework version: SUE ranks permuted within each quarter, everything else identical."""
import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "meta")
from steady_grid import *
from pead.strategies.equity.steady_drift import SteadyDrift, SteadyDriftPlus

def run_one(base, seed=None):
    class P(base):
        def weights(self, events):
            if seed is None: return super().weights(events)
            ev = events.copy(); rng = np.random.default_rng(seed)
            v = ev["sue_rank_pct"].to_numpy().copy()
            for q, ix in ev.groupby("ann_quarter").indices.items(): v[ix] = rng.permutation(v[ix])
            ev["sue_rank_pct"] = v
            return super().weights(ev)
    run = P().backtest(uni); r = run.daily.set_index("date")["daily_return"]
    x = pd.concat([r, idx], axis=1).loc["1997-07-01":"2013-12-31"].dropna(); x.columns = ["r", "m"]
    b = np.cov(x.r, x.m)[0, 1] / x.m.var(); res = x.r - b * x.m
    nav = (1 + x.r).cumprod()
    return dict(ret=nav.iloc[-1] ** (252 / len(x)) - 1, sharpe=x.r.mean() / x.r.std() * np.sqrt(252), alpha=res.mean() * 252, t=res.mean() / res.std() * np.sqrt(len(x)))
if __name__ == "__main__":
    for base in (SteadyDrift, SteadyDriftPlus):
        real = run_one(base); pl = [run_one(base, s) for s in range(3)]
        print(base.__name__, "REAL ret %.2f%% Sh %.2f alpha %.2f%% (t %.1f)" % (real["ret"]*100, real["sharpe"], real["alpha"]*100, real["t"]))
        for i, p in enumerate(pl): print("   placebo %d ret %.2f%% Sh %.2f alpha %.2f%% (t %.1f)" % (i, p["ret"]*100, p["sharpe"], p["alpha"]*100, p["t"]))
        print("   skill (real - mean placebo): ret %.2f pts, alpha %.2f pts, Sharpe %.2f" % ((real["ret"]-np.mean([p["ret"] for p in pl]))*100, (real["alpha"]-np.mean([p["alpha"] for p in pl]))*100, real["sharpe"]-np.mean([p["sharpe"] for p in pl])))
