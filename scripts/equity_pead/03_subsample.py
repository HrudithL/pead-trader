"""
Subsample analysis: does the D10-D1 PEAD spread differ across FF12 industry sectors,
firm-size quintiles, and eras -- and can groups that don't differ be pooled?

Method per group dimension (sector / size / era):
  1. For each group value, compute the D10-D1 spread the same Fama-MacBeth way as
     02_decile_summary.py (quarter-level D10 mean minus D1 mean, averaged over quarters
     the group actually has both deciles present in), using the +60d market-adjusted
     return as the headline horizon.
  2. One-way ANOVA (scipy) across groups' quarterly spread observations -- a formal test
     of "do these groups have the same average spread", not just eyeballing point estimates.
  3. A simple tiering: sort groups by point estimate, then greedily merge adjacent groups
     whose 95% CI (mean_spread +/- 1.96*se) overlap into the same tier. This is a
     conservative, transparent way to say "these groups can be treated as one bucket"
     without a heavier clustering machine (k-means, etc.) that the data (12 sectors, 5
     size bins, 5 eras) doesn't really need.

Output: data/subsample_<dim>.csv for dim in {sector, size, era}, plus a combined tiers CSV.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR

DATA = DATA_DIR
ev = pd.read_parquet(DATA / "decile_events.parquet")

HORIZON_COL = "ret_fwd_60d_mktadj"


def quarterly_spread_series(df, group_col, group_val):
    sub = df[df[group_col] == group_val]
    d1 = sub[sub["decile"] == 1][["ann_quarter", HORIZON_COL]].dropna()
    d10 = sub[sub["decile"] == 10][["ann_quarter", HORIZON_COL]].dropna()
    q1 = d1.groupby("ann_quarter")[HORIZON_COL].mean()
    q10 = d10.groupby("ann_quarter")[HORIZON_COL].mean()
    common = q1.index.intersection(q10.index)
    return (q10.loc[common] - q1.loc[common]), len(sub)


def summarize_dimension(df, group_col, min_quarters=8):
    results = []
    series_map = {}
    for gv in sorted(df[group_col].dropna().unique(), key=str):
        spread_q, n_events = quarterly_spread_series(df, group_col, gv)
        n_q = len(spread_q)
        if n_q < min_quarters:
            continue
        mean = spread_q.mean()
        se = spread_q.std(ddof=1) / np.sqrt(n_q)
        t = mean / se if se > 0 else np.nan
        p = 2 * (1 - stats.t.cdf(abs(t), df=n_q - 1)) if se > 0 else np.nan
        results.append(dict(
            group=gv, mean_spread=mean, se=se, t_stat=t, p_value=p,
            ci_low=mean - 1.96 * se, ci_high=mean + 1.96 * se,
            n_events=n_events, n_quarters=n_q,
        ))
        series_map[gv] = spread_q
    out = pd.DataFrame(results).sort_values("mean_spread").reset_index(drop=True)

    # one-way ANOVA across groups' quarterly spread observations
    arrays = [series_map[g].values for g in out["group"]]
    if len(arrays) >= 2:
        f_stat, anova_p = stats.f_oneway(*arrays)
    else:
        f_stat, anova_p = np.nan, np.nan

    # greedy adjacent-CI-overlap tiering, sorted by point estimate
    tiers = []
    tier_id = 0
    prev_high = None
    for _, row in out.iterrows():
        if prev_high is None or row["ci_low"] > prev_high:
            tier_id += 1
            prev_high = row["ci_high"]
        else:
            prev_high = max(prev_high, row["ci_high"])
        tiers.append(tier_id)
    out["tier"] = tiers

    return out, f_stat, anova_p


for dim, col in [("sector", "ff12_sector"), ("size", "size_quintile"), ("era", "era")]:
    out, f_stat, anova_p = summarize_dimension(ev, col)
    out.to_csv(DATA / f"subsample_{dim}.csv", index=False)
    print(f"\n=== {dim} (ANOVA F={f_stat:.3f}, p={anova_p:.4f}) ===")
    print(out.round(4).to_string(index=False))
