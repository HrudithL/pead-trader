"""
Headline options-PEAD result: decile-sorted forward option returns (near-ATM call and near-ATM
put, entry at day0, buy-and-hold to day0+{1,5,10,20,40,60} trading days), with the same
Fama-MacBeth (across-announcement-quarter) clustered inference used for the equity result in
scripts/equity_pead/02_decile_summary.py -- same rationale: earnings cluster heavily in fiscal-quarter
"seasons", so treat the ~quarterly decile means as the unit of inference rather than treating
individual events as independent.

A call is a leveraged, convex long-the-underlying position: if PEAD exists in the underlying,
a near-ATM call's return should show the SAME decile ordering as the equity result (D10 > D1)
but amplified by leverage; a near-ATM put should show the MIRROR ordering (D1 > D10, since a put
is a leveraged short). That cross-check (calls up, puts down, roughly symmetric) is the options-
market evidence this script is built to produce.

Output:
  data/option_decile_horizon_stats.csv   -- decile x horizon x {call,put} return summary
  data/option_spread_horizon_stats.csv   -- D10-D1 spread x horizon x {call,put} summary
  data/option_coverage_stats.csv         -- match/coverage counts per decile x horizon
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR
from common.stats import fama_macbeth

DATA = DATA_DIR
panel = pd.read_parquet(DATA / "event_options" / "option_event_panel.parquet")
panel = panel[panel["decile"].notna()].copy()
panel["decile"] = panel["decile"].astype(int)

HORIZONS = [1, 5, 10, 20, 40, 60]
CP_TYPES = ["call", "put", "straddle"]


rows, cov_rows = [], []
for cp in CP_TYPES:
    for h in HORIZONS:
        col = f"{cp}_ret_fwd_{h}d"
        if col not in panel.columns:
            continue
        for d in range(1, 11):
            sub = panel[panel["decile"] == d]
            mean, se, t, n, nq = fama_macbeth(sub, col)
            rows.append(dict(cp_type=cp, horizon=h, decile=d, mean=mean, se=se, t_stat=t,
                              n_events=n, n_quarters=nq))
            n_total = sub[f"{cp}_optionid"].notna().sum() if f"{cp}_optionid" in sub else 0
            cov_rows.append(dict(cp_type=cp, horizon=h, decile=d, n_contracts_selected=n_total,
                                  n_priced=n, hit_rate=n / n_total if n_total else np.nan))

decile_stats = pd.DataFrame(rows)
decile_stats.to_csv(DATA / "option_decile_horizon_stats.csv", index=False)
cov = pd.DataFrame(cov_rows)
cov.to_csv(DATA / "option_coverage_stats.csv", index=False)

print("=== CALL mean return by decile x horizon ===")
print(decile_stats[decile_stats.cp_type == "call"].pivot(index="decile", columns="horizon",
                                                          values="mean").round(4))
print("\n=== PUT mean return by decile x horizon ===")
print(decile_stats[decile_stats.cp_type == "put"].pivot(index="decile", columns="horizon",
                                                         values="mean").round(4))
print("\n=== STRADDLE (directionless) mean return by decile x horizon ===")
print(decile_stats[decile_stats.cp_type == "straddle"].pivot(index="decile", columns="horizon",
                                                              values="mean").round(4))

spread_rows = []
for cp in CP_TYPES:
    for h in HORIZONS:
        col = f"{cp}_ret_fwd_{h}d"
        if col not in panel.columns:
            continue
        d1 = panel[panel["decile"] == 1][["ann_quarter", col]].dropna()
        d10 = panel[panel["decile"] == 10][["ann_quarter", col]].dropna()
        q1 = d1.groupby("ann_quarter")[col].mean()
        q10 = d10.groupby("ann_quarter")[col].mean()
        common = q1.index.intersection(q10.index)
        spread_q = q10.loc[common] - q1.loc[common]
        n_q = len(spread_q)
        mean = spread_q.mean()
        se = spread_q.std(ddof=1) / np.sqrt(n_q) if n_q > 1 else np.nan
        t = mean / se if se and se > 0 else np.nan
        spread_rows.append(dict(cp_type=cp, horizon=h, mean_spread=mean, se=se, t_stat=t,
                                 n_quarters=n_q, n_d1=len(d1), n_d10=len(d10)))

spread_stats = pd.DataFrame(spread_rows)
spread_stats.to_csv(DATA / "option_spread_horizon_stats.csv", index=False)
print("\n=== D10-D1 spread by horizon ===")
print(spread_stats.round(4))

print("\n=== coverage (n_priced / n_contracts_selected) at 60d horizon ===")
print(cov[cov.horizon == 60].pivot(index="decile", columns="cp_type", values="hit_rate").round(3))
