"""Combining strategies into one hedged book.

The raw material is each strategy's daily return stream (P&L / prior-day NAV). Two facts from the
attribution work drive the design:

  * the market-neutral equity sleeve (Balanced Leverage / Calm Weighted) has ~0 correlation with both the market AND the
    options sleeve (-0.05), so adding it to options cuts portfolio variance almost for free;
  * the options sleeve is ~98% long calls: it carries a small positive market beta (corr ~0.18), loses
    on the worst market days, and had its one bad year in 2008 -- while the equity sleeve made 10% that
    year. The equity alpha earns most of its return in the early years and decays; the options sleeve is
    the opposite. They cover each other's weak eras.

A sleeve blend is `r_p = sum(w_i * r_i)` rebalanced daily, which is exactly how a fund holding fixed
capital fractions in each sleeve behaves. Weights come from one of:

    equal        1/N of capital in each sleeve
    inverse_vol  risk parity: weight ~ 1/volatility
    max_sharpe   long-only maximum-Sharpe weights (in-sample, shown only as an upper bound)
    walk_forward max-Sharpe weights re-estimated every January from PRIOR data only (honest, tradeable)

A market hedge (short `beta x market` index position) can then be layered on top to neutralize whatever
directional exposure the blend picked up, and the whole book can be scaled to a target volatility.
"""
import numpy as np
import pandas as pd
from scipy.optimize import minimize

from pead.common.paths import RUNS_DIR
from pead.core.metrics import summarize, yearly_table

INITIAL = 1e7


def sleeve_returns(names, calendar_from=None) -> pd.DataFrame:
    """Daily-return DataFrame on the EQUITY trading calendar (the longest, 1995-2014). Options sleeves
    have no trading days in 2011 (the OptionMetrics file is corrupt) or outside 1996-2013; those days
    are zero return -- the capital sat in cash."""
    from pead.core.store import load_daily
    cols, eq_idx = {}, None
    for n in names:
        daily, asset_class = load_daily(n)
        cols[n] = daily.set_index("date")["daily_return"]
        if asset_class == "equity" and eq_idx is None:
            eq_idx = cols[n].index
    idx = eq_idx if eq_idx is not None else sorted(set().union(*[c.index for c in cols.values()]))
    return pd.DataFrame({n: s.reindex(idx) for n, s in cols.items()}).fillna(0.0)


_MKT = {}


def market_returns(index) -> pd.Series:
    from pead.reporting.charts import market_series
    key = (index[0], index[-1], len(index))
    if key not in _MKT:
        _MKT[key] = market_series(index)
    return _MKT[key]


def blend(returns: pd.DataFrame, weights: dict, market: pd.Series = None, hedge_beta: float = 0.0,
          hedge_cost_bps: float = 1.0) -> pd.Series:
    r = sum(returns[k] * w for k, w in weights.items())
    if market is not None and hedge_beta:
        r = r - hedge_beta * market.reindex(returns.index).fillna(0.0) - abs(hedge_beta) * hedge_cost_bps / 1e4 / 63
    return r


def stats(r: pd.Series) -> dict:
    nav = INITIAL * (1 + r).cumprod()
    d = pd.DataFrame({"date": r.index, "daily_pnl": np.diff(nav, prepend=INITIAL), "nav": nav.to_numpy()})
    s = summarize(d, INITIAL)
    return {k: s[k] for k in ("annualized_return", "annualized_vol", "sharpe", "sortino", "max_drawdown", "calmar",
                              "worst_day", "sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013")}


def weights_equal(returns): n = returns.shape[1]; return np.full(n, 1 / n)


def weights_inverse_vol(returns):
    iv = 1 / returns.std(ddof=1).to_numpy()
    return iv / iv.sum()


def weights_max_sharpe(returns, floor=0.0):
    mu, cov = returns.mean().to_numpy() * 252, returns.cov().to_numpy() * 252
    n = len(mu)
    obj = lambda w: -(w @ mu) / np.sqrt(w @ cov @ w)
    res = minimize(obj, np.full(n, 1 / n), bounds=[(floor, 1)] * n,
                   constraints={"type": "eq", "fun": lambda w: w.sum() - 1}, method="SLSQP")
    return res.x if res.success else weights_inverse_vol(returns)


METHODS = {"equal": weights_equal, "inverse_vol": weights_inverse_vol, "max_sharpe": weights_max_sharpe}


def walk_forward(returns: pd.DataFrame, method="max_sharpe", min_years=4, shrink=0.5) -> tuple:
    """Each calendar year trades weights estimated on prior years only (and shrunk toward equal weight,
    since two years of a noisy Sharpe is not a mandate). Returns (blended returns, weights by year)."""
    fn = METHODS[method]
    out, w_hist = [], {}
    years = sorted(set(returns.index.year))
    for y in years:
        past = returns[returns.index.year < y]
        cur = returns[returns.index.year == y]
        if len(past) < min_years * 252:
            w = weights_equal(returns)
        else:
            w = shrink * weights_equal(returns) + (1 - shrink) * fn(past)
        w_hist[y] = dict(zip(returns.columns, w))
        out.append(cur @ w)
    return pd.concat(out), pd.DataFrame(w_hist).T


def market_beta(r: pd.Series, mkt: pd.Series) -> float:
    m = mkt.reindex(r.index).fillna(0.0)
    return float(np.cov(r, m, ddof=1)[0, 1] / m.var(ddof=1))


def scale_to_vol(r: pd.Series, target_vol: float) -> tuple:
    lev = target_vol / (r.std(ddof=1) * np.sqrt(252))
    return r * lev, float(lev)


def run_portfolio_study(equity=None, options=None, verbose=True, out_dir=None):
    """Grid over (equity sleeve, options sleeve) pairs -> frontier, optimal blend, hedge, walk-forward."""
    import json
    out_dir = out_dir or RUNS_DIR / "portfolio"
    out_dir.mkdir(parents=True, exist_ok=True)
    eq_names = [equity] if equity else ["balanced_leverage", "calm_weighted", "market_boost"]
    op_names = [options] if options else ["options_cap20", "options_cap30", "options_cap20_legacy"]
    mkt = None
    rows, best = [], None
    for e in eq_names:
        for o in op_names:
            R = sleeve_returns([e, o])
            mkt = market_returns(R.index) if mkt is None else mkt
            for w_e in np.round(np.arange(0, 1.0001, 0.05), 2):
                s = stats(blend(R, {e: w_e, o: 1 - w_e}))
                rows.append(dict(equity=e, options=o, w_equity=w_e, **s))
    frontier = pd.DataFrame(rows)
    frontier.to_csv(out_dir / "frontier.csv", index=False)
    if verbose:
        top = frontier.sort_values("sharpe", ascending=False).groupby(["equity", "options"]).head(1)
        print(top[["equity", "options", "w_equity", "annualized_return", "annualized_vol", "sharpe", "max_drawdown"]].to_string(index=False))
    return frontier
