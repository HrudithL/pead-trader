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
