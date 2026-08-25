"""
Headline PEAD result: decile-sorted market-adjusted forward returns across horizons,
with Fama-MacBeth (across-announcement-quarter) clustered standard errors.

Why Fama-MacBeth here: the existing pipeline's own event_study_stats.json explicitly
flags that its i.i.d. standard errors understate uncertainty because earnings cluster
heavily in fiscal-quarter "seasons". Per the user's decision, this report clusters by
announcement quarter. Fama-MacBeth is the standard way to do this in the PEAD literature:
compute the decile's mean return separately within each announcement quarter, then treat
those ~72 quarterly means as the unit of inference (mean of means, se = std(quarterly
means) / sqrt(#quarters)). This is robust to arbitrary within-quarter correlation across
firms (which is exactly the clustering concern) at the cost of only using quarter-to-quarter
variation for inference.

Output:
  data/decile_horizon_stats.csv   -- decile x horizon x return-type summary
  data/spread_horizon_stats.csv   -- D10-D1 spread x horizon summary
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path("/root/pead_report/data")
ev = pd.read_parquet(DATA / "decile_events.parquet")

HORIZONS = [1, 5, 10, 20, 40, 60]
RET_COLS = {h: f"ret_fwd_{h}d_mktadj" for h in HORIZONS}


def fama_macbeth(df, group_col, ret_col, q_col="ann_quarter"):
    """Return (mean, se, t, n_events, n_quarters) via Fama-MacBeth over q_col."""
    sub = df[[group_col, q_col, ret_col]].dropna(subset=[ret_col])
    qmeans = sub.groupby(q_col)[ret_col].mean()
    n_q = qmeans.shape[0]
    mean = qmeans.mean()
    se = qmeans.std(ddof=1) / np.sqrt(n_q)
    t = mean / se if se > 0 else np.nan
    return mean, se, t, len(sub), n_q


rows = []
for h in HORIZONS:
    col = RET_COLS[h]
    for d in range(1, 11):
        sub = ev[ev["decile"] == d]
        mean, se, t, n, nq = fama_macbeth(sub, "decile", col)
        rows.append(dict(horizon=h, decile=d, mean=mean, se=se, t_stat=t, n_events=n, n_quarters=nq))

decile_stats = pd.DataFrame(rows)
decile_stats.to_csv(DATA / "decile_horizon_stats.csv", index=False)
print(decile_stats.pivot(index="decile", columns="horizon", values="mean").round(4))

# D10 - D1 spread, computed AT THE QUARTER LEVEL (D10 quarter-mean minus D1 quarter-mean),
# so the spread's own Fama-MacBeth SE correctly reflects that D1 and D10 quarterly means
# are not independent draws (both come from the same quarter's cross-section).
spread_rows = []
for h in HORIZONS:
    col = RET_COLS[h]
    d1 = ev[ev["decile"] == 1][["ann_quarter", col]].dropna()
    d10 = ev[ev["decile"] == 10][["ann_quarter", col]].dropna()
    q1 = d1.groupby("ann_quarter")[col].mean()
    q10 = d10.groupby("ann_quarter")[col].mean()
    common = q1.index.intersection(q10.index)
    spread_q = q10.loc[common] - q1.loc[common]
    n_q = len(spread_q)
    mean = spread_q.mean()
    se = spread_q.std(ddof=1) / np.sqrt(n_q)
    t = mean / se if se > 0 else np.nan
    spread_rows.append(dict(
        horizon=h, mean_spread=mean, se=se, t_stat=t, n_quarters=n_q,
        n_d1=len(d1), n_d10=len(d10),
    ))

spread_stats = pd.DataFrame(spread_rows)
spread_stats.to_csv(DATA / "spread_horizon_stats.csv", index=False)
print()
print(spread_stats.round(4))
