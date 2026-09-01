"""Shared Fama-MacBeth quarter-clustered statistics, used by every decile-summary script."""
import numpy as np


def fama_macbeth(df, ret_col, q_col="ann_quarter"):
    """Quarter-clustered mean/SE/t-stat: average the per-quarter cross-sectional mean of
    ret_col, then take the mean/SE/t of that quarterly series (not of the raw events), so that
    events within a quarter -- which are not independent draws -- don't inflate the sample size.

    Returns (mean, se, t_stat, n_events, n_quarters). Guards against degenerate input (no rows,
    or fewer than 2 quarters, where an SE/t-stat isn't defined) -- these guards were added
    incrementally across the scripts this consolidates and are strict no-ops for every quarter
    x decile x horizon combination that actually appears in this dataset (68 quarters,
    1996-2013), which always clears n_quarters >= 2.
    """
    sub = df[[q_col, ret_col]].dropna(subset=[ret_col])
    if sub.empty:
        return np.nan, np.nan, np.nan, 0, 0
    qmeans = sub.groupby(q_col)[ret_col].mean()
    n_q = qmeans.shape[0]
    if n_q < 2:
        return np.nan, np.nan, np.nan, len(sub), n_q
    mean = qmeans.mean()
    se = qmeans.std(ddof=1) / np.sqrt(n_q)
    t = mean / se if se and se > 0 else np.nan
    return mean, se, t, len(sub), n_q
