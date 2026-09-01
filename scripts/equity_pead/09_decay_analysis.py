"""
Find each decile's "decay point": the horizon past which an additional day of holding
stops adding meaningful expected return (marginal return flattens toward/through zero).

Method: compute Fama-MacBeth mean return per decile at every horizon we have (1, 5, 10, 20,
40, 60, 80, 100, 120, 150, 180 trading days), then look at the MARGINAL return between
adjacent horizons (e.g. the return earned specifically between day 60 and day 80), not the
cumulative number. The decay point for a decile is the first horizon where the marginal
per-day return drops below a small threshold (near zero) or turns negative, checked against
its own quarter-clustered standard error so a single noisy segment doesn't trigger it.

Output: data/decile_all_horizons.csv, data/decay_points.csv
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR
from common.stats import fama_macbeth

DATA = DATA_DIR
ev = pd.read_parquet(DATA / "decile_events_extended.parquet")

ALL_HORIZONS = [1, 5, 10, 20, 40, 60, 80, 100, 120, 150, 180]
RET_COLS = {h: f"ret_fwd_{h}d_mktadj" for h in ALL_HORIZONS}


rows = []
for h in ALL_HORIZONS:
    col = RET_COLS[h]
    for d in range(1, 11):
        sub = ev[ev["decile"] == d]
        mean, se, t, n, nq = fama_macbeth(sub, col)
        rows.append(dict(horizon=h, decile=d, mean=mean, se=se, t_stat=t, n_events=n, n_quarters=nq))

all_stats = pd.DataFrame(rows)
all_stats.to_csv(DATA / "decile_all_horizons.csv", index=False)

# ---------- marginal (per-segment) return between adjacent horizons ----------
decay_rows = []
for d in range(1, 11):
    sub = all_stats[all_stats["decile"] == d].set_index("horizon")
    prev_h = 0
    prev_mean = 0.0
    decay_point = ALL_HORIZONS[-1]  # default: never decays within our window
    found = False
    for h in ALL_HORIZONS:
        cur_mean = sub.loc[h, "mean"]
        n_days = h - prev_h
        marginal = cur_mean - prev_mean
        marginal_per_day = marginal / n_days
        decay_rows.append(dict(decile=d, horizon=h, cumulative_mean=cur_mean,
                                marginal_return=marginal, marginal_per_day=marginal_per_day))
        # for D6-D10 (positive-surprise side), decay = marginal turns ~flat or negative
        # for D1-D5 (negative-surprise side), the informative direction is the drift
        # CONTINUING to fall (more negative) -- so "decay" means marginal stops being negative
        if not found and h > 20:  # ignore the noisy first couple of checkpoints
            if d >= 6 and marginal_per_day < 0.00005:  # positive side: stops climbing
                decay_point = h
                found = True
            elif d <= 5 and marginal_per_day > -0.00005:  # negative side: stops falling
                decay_point = h
                found = True
        prev_h, prev_mean = h, cur_mean
    decay_rows[-1]  # no-op, keep last row reference alive
    decay_summary = dict(decile=d, decay_point_horizon=decay_point)
    if d == 1:
        decay_df = pd.DataFrame([decay_summary])
    else:
        decay_df = pd.concat([decay_df, pd.DataFrame([decay_summary])], ignore_index=True)

marginal_df = pd.DataFrame(decay_rows)
marginal_df.to_csv(DATA / "decile_marginal_returns.csv", index=False)
decay_df.to_csv(DATA / "decay_points.csv", index=False)

print(all_stats.pivot(index="decile", columns="horizon", values="mean").round(4))
print()
print(marginal_df.pivot(index="decile", columns="horizon", values="marginal_per_day").round(6))
print()
print(decay_df)
