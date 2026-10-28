"""Event-level signal statistics (EQ-30): rank IC by announcement quarter with quarter-clustered inference.

numpy/pandas only. A signal is a column of the events frame; the outcome is a forward return column. For each
announcement quarter we take the Spearman rank correlation (the quarter's IC); the headline is the mean IC and its
t-statistic across quarters (quarters are the independent units, so this is the clustered standard error).
"""
import numpy as np
import pandas as pd


def quarterly_ic(events: pd.DataFrame, signal: str, outcome: str, quarter: str = "ann_quarter", min_n: int = 30) -> pd.Series:
    d = events[[signal, outcome, quarter]].dropna()
    out = {}
    for q, g in d.groupby(quarter):
        if len(g) >= min_n and g[signal].nunique() > 1:
            out[q] = g[signal].rank().corr(g[outcome].rank())
    return pd.Series(out, dtype=float).sort_index()


def ic_summary(ic: pd.Series) -> dict:
    n = len(ic)
    if n < 3:
        return dict(n_quarters=n)
    m, sd = ic.mean(), ic.std(ddof=1)
    return dict(n_quarters=n, mean_ic=float(m), ic_t=float(m / (sd / np.sqrt(n))), pct_positive=float((ic > 0).mean()))


def decile_spread(events: pd.DataFrame, signal: str, outcome: str, quarter: str = "ann_quarter", n_bins: int = 10) -> dict:
    """Mean outcome of the top bin minus the bottom bin, ranked within quarter; t-stat across quarters."""
    d = events[[signal, outcome, quarter]].dropna().copy()
    d["bin"] = d.groupby(quarter)[signal].transform(lambda x: pd.qcut(x.rank(method="first"), n_bins, labels=False)
                                                  if len(x) >= n_bins * 3 else np.nan)
    top = d[d["bin"] == n_bins - 1].groupby(quarter)[outcome].mean()
    bot = d[d["bin"] == 0].groupby(quarter)[outcome].mean()
    s = (top - bot).dropna()
    if len(s) < 3:
        return dict(n_quarters=len(s))
    return dict(n_quarters=len(s), spread=float(s.mean()), spread_t=float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))),
                top=float(top.mean()), bottom=float(bot.mean()))


def clustered_ols(y, X, clusters):
    """OLS with one-way cluster-robust (CR1) standard errors. Returns (beta, se)."""
    y, X = np.asarray(y, float), np.asarray(X, float)
    XtX_inv = np.linalg.pinv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    codes = pd.factorize(np.asarray(clusters))[0]
    G, n, k = codes.max() + 1, len(y), X.shape[1]
    S = np.zeros((G, k))
    np.add.at(S, codes, X * e[:, None])
    meat = S.T @ S
    adj = G / (G - 1) * (n - 1) / (n - k)
    return b, np.sqrt(np.diag(adj * XtX_inv @ meat @ XtX_inv))


def era_interaction(events: pd.DataFrame, y: str, signal: str, break_date, quarter="ann_quarter", controls=("ff12_sector", "size_quintile")):
    """y = a + b*s + c*post + d*(s*post) + control FE, errors clustered by announcement quarter. `signal` should be a rank in [0,1].
    Returns dict with d (the change in the signal's payoff after the break), its cluster t, and b."""
    d = events[[y, signal, quarter, "day0_date", *controls]].dropna().copy()
    post = (d["day0_date"] >= pd.Timestamp(break_date)).astype(float).to_numpy()
    s = d[signal].to_numpy(float)
    cols = [np.ones(len(d)), s, post, s * post]
    for c in controls:
        cols.extend(pd.get_dummies(d[c], drop_first=True, dtype=float).to_numpy().T)
    b, se = clustered_ols(d[y].to_numpy(float), np.column_stack(cols), d[quarter].astype(str).to_numpy())
    return dict(n=len(d), b=float(b[1]), b_t=float(b[1] / se[1]), d=float(b[3]), d_t=float(b[3] / se[3]), post_effect=float(b[2]))
