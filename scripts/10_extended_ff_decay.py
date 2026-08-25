"""
Redo the decay-point analysis using size+book-to-market-adjusted returns instead of raw
market-adjusted returns. This matters specifically for the negative-surprise deciles: the
raw decay analysis (09_decay_analysis.py) found D1-D5 "decaying" around day 40, but that's
because the raw market-adjusted return reverses upward there -- and we already established
(Section 4b of the report) that reversal is substantially a size/value risk premium, not the
signal running out. Using the characteristic-adjusted return gives a cleaner read on when the
actual earnings-surprise signal itself stops paying.

Output: data/ff_decile_all_horizons.csv, data/ff_decay_points.csv, data/ff_marginal_returns.csv
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path("/root/pead_report/data")

ext = pd.read_parquet(DATA / "decile_events_extended.parquet")
ff = pd.read_parquet(DATA / "decile_events_ff_adjusted.parquet")

NEW_HORIZONS = [80, 100, 120, 150, 180]
ALL_HORIZONS = [1, 5, 10, 20, 40, 60, 80, 100, 120, 150, 180]

# merge the new raw horizon columns onto the FF-adjusted (usable) event set
new_cols = ["permno", "day0_date"] + [f"ret_fwd_{h}d_mktadj" for h in NEW_HORIZONS]
ff = ff.merge(ext[new_cols], on=["permno", "day0_date"], how="left", suffixes=("", "_dup"))

cell_cols = ["ann_quarter", "size_quintile", "bm_tercile"]
for h in NEW_HORIZONS:
    col = f"ret_fwd_{h}d_mktadj"
    grp = ff.groupby(cell_cols)[col]
    cell_sum = grp.transform("sum")
    cell_n = grp.transform("count")
    loo_mean = (cell_sum - ff[col].fillna(0)) / (cell_n - ff[col].notna().astype(int))
    ff[f"{col}_adj"] = ff[col] - loo_mean

ff.to_parquet(DATA / "decile_events_ff_adjusted_extended.parquet", index=False)


def fama_macbeth(df, ret_col, q_col="ann_quarter"):
    sub = df[[q_col, ret_col]].dropna(subset=[ret_col])
    qmeans = sub.groupby(q_col)[ret_col].mean()
    n_q = qmeans.shape[0]
    if n_q < 2:
        return np.nan, np.nan, np.nan, len(sub), n_q
    mean = qmeans.mean()
    se = qmeans.std(ddof=1) / np.sqrt(n_q)
    t = mean / se if se > 0 else np.nan
    return mean, se, t, len(sub), n_q


rows = []
for h in ALL_HORIZONS:
    col = f"ret_fwd_{h}d_mktadj_adj"
    for d in range(1, 11):
        sub = ff[ff["decile"] == d]
        mean, se, t, n, nq = fama_macbeth(sub, col)
        rows.append(dict(horizon=h, decile=d, mean=mean, se=se, t_stat=t, n_events=n, n_quarters=nq))

all_stats = pd.DataFrame(rows)
all_stats.to_csv(DATA / "ff_decile_all_horizons.csv", index=False)

decay_rows = []
decay_summaries = []
for d in range(1, 11):
    sub = all_stats[all_stats["decile"] == d].set_index("horizon")
    prev_h, prev_mean = 0, 0.0
    decay_point = ALL_HORIZONS[-1]
    found = False
    for h in ALL_HORIZONS:
        cur_mean = sub.loc[h, "mean"]
        n_days = h - prev_h
        marginal = cur_mean - prev_mean
        marginal_per_day = marginal / n_days
        decay_rows.append(dict(decile=d, horizon=h, cumulative_mean=cur_mean,
                                marginal_return=marginal, marginal_per_day=marginal_per_day))
        if not found and h > 20:
            if d >= 6 and marginal_per_day < 0.00005:
                decay_point = h
                found = True
            elif d <= 5 and marginal_per_day > -0.00005:
                decay_point = h
                found = True
        prev_h, prev_mean = h, cur_mean
    decay_summaries.append(dict(decile=d, decay_point_horizon=decay_point))

marginal_df = pd.DataFrame(decay_rows)
marginal_df.to_csv(DATA / "ff_marginal_returns.csv", index=False)
decay_df = pd.DataFrame(decay_summaries)
decay_df.to_csv(DATA / "ff_decay_points.csv", index=False)

print(all_stats.pivot(index="decile", columns="horizon", values="mean").round(4))
print()
print(decay_df)
