"""Pre-registered improvement grid for Steady Drift on the pead framework.  Run with the env vars in meta/README_port.txt.
Every variant is counted; acceptance is by rule, not by picking the maximum:  Sharpe >= baseline - 0.05, alpha t >= 5,
2008-13 Sharpe >= 0.65, no-borrow check passes (marked holdings x exposure <= NAV)."""
import sys, itertools, warnings
from dataclasses import replace
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, "meta")
from pead.core.data import load_equity_universe
from pead.core.rates import daily_rf
from pead.core.metrics import summarize
from pead.strategies.equity.steady_drift import SteadyDrift

uni = load_equity_universe()
cal = uni.calendar
idx = pd.Series(uni.vwretd, index=cal)
rf = pd.Series(daily_rf(cal), index=cal)
cal_pos = {d: i for i, d in enumerate(cal)}

def market_value_check(run, u, e):
    """max over days of (marked stock value x risk exposure) / overlaid NAV  -- must stay <= 1 to have borrowed nothing."""
    pos = run.positions
    ent, ext, notional = pos["entry_row_idx"].to_numpy(), pos["exit_row_idx"].to_numpy(), pos["notional"].to_numpy()
    L = ext - ent
    rep = np.repeat(np.arange(len(pos)), L)
    off = np.arange(L.sum()) - np.repeat(np.cumsum(L) - L, L)
    rows = np.repeat(ent + 1, L) + off
    r = u.ret_raw[rows]
    # growth before each day inside each position
    lg = np.log1p(np.clip(r, -0.999, None)); cs = np.cumsum(lg)
    starts = np.cumsum(L) - L
    base = np.repeat(cs[starts] - lg[starts], L)
    grow = np.exp(cs - lg - base) * (1 + r)
    day = cal.searchsorted(u.panel_date[rows])
    val = np.bincount(day, weights=notional[rep] * grow, minlength=len(cal))
    nav = run.daily["nav"].to_numpy(); prev = np.r_[nav[0], nav[:-1]]
    return float(np.nanmax(val * e / prev))

def evaluate(label, **attrs):
    sizing_kw = {k: attrs.pop(k) for k in list(attrs) if k in ("base_unit_fraction", "max_gross_leverage", "name_cap_frac")}
    cls = type("V", (SteadyDrift,), dict(attrs, sizing=replace(SteadyDrift.sizing, **sizing_kw), name=label))
    s = cls()
    run = s.backtest(uni)
    d = run.daily.set_index("date")
    r = d["daily_return"]
    e = d["risk_exposure"].to_numpy() if "risk_exposure" in d else np.ones(len(d))
    out = {"variant": label}
    for tag, a, b in [("all", "1997-07-01", "2013-12-31"), ("h1", "2000-01-01", "2006-12-31"), ("h2", "2007-01-01", "2013-12-31"), ("e3", "2008-01-01", "2013-12-31")]:
        x = r.loc[a:b]; m = x.mean() * 252; v = x.std() * np.sqrt(252)
        nav = (1 + x).cumprod()
        out[f"{tag}_ret"] = nav.iloc[-1] ** (252 / len(x)) - 1; out[f"{tag}_sharpe"] = m / v; out[f"{tag}_dd"] = (nav / nav.cummax() - 1).min()
    x = pd.concat([r, idx], axis=1).loc["1997-07-01":"2013-12-31"].dropna(); x.columns = ["r", "m"]
    b = np.cov(x.r, x.m)[0, 1] / x.m.var(); res = x.r - b * x.m
    out.update(beta=b, alpha=res.mean() * 252, alpha_t=res.mean() / res.std() * np.sqrt(len(x)), n_pos=run.summary["n_positions"],
               liq_capped=run.summary["pct_liquidity_capped"], avg_exposure=run.summary.get("avg_risk_exposure", 1.0),
               max_mv_over_nav=market_value_check(run, uni, e))
    return out

if __name__ == "__main__":
    rows = [evaluate("baseline steady_drift")]
    grid = []
    for cap, th, tgt in itertools.product([0.70, 0.85, 0.95], [0.9, 0.8], [0.10, 0.12]):
        if (cap, th, tgt) == (0.70, 0.9, 0.10): continue
        grid.append((f"cap{cap}_top{th}_vol{tgt}", dict(max_gross_leverage=cap, top_rank=th, risk_target_vol=tgt)))
    grid += [("base_unit 0.006", dict(base_unit_fraction=0.006)), ("base_unit 0.003", dict(base_unit_fraction=0.003)),
             ("name_cap 1%", dict(name_cap_frac=0.01)), ("hold 40", dict(hold_sessions=40)), ("hold 90", dict(hold_sessions=90)),
             ("adv floor 5M", dict(min_dollar_volume=5e6)), ("adv floor 0.5M", dict(min_dollar_volume=0.5e6))]
    for lab, kw in grid:
        rows.append(evaluate(lab, **kw)); print(lab, flush=True)
    R = pd.DataFrame(rows); R.to_csv("meta/out/steady_grid.csv", index=False)
    f = R.copy()
    for c in [c for c in f if c.endswith(("_ret", "_dd")) or c == "alpha"]: f[c] = (f[c] * 100).round(2)
    pd.options.display.width = 250
    print(f[["variant", "all_ret", "all_sharpe", "all_dd", "e3_ret", "e3_sharpe", "h1_sharpe", "h2_sharpe", "beta", "alpha", "alpha_t", "n_pos", "liq_capped", "max_mv_over_nav"]].round(2).to_string(index=False))
    print("variants run:", len(R))
