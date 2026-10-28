"""EQ-03 part A: where does the top-minus-bottom SUE return arrive? gap, intraday, then post-entry drift (read-only).

    python scripts/reaction_decomposition.py

Legs per event: gap = open(d0)/close(d-1)-1 and intraday = close(d0)/open(d0)-1 (raw; no index open exists), reaction = close-to-close
market-adjusted return on d0, drift_h = market-adjusted return over the h sessions after the d0 close. Top = sue_rank_pct >= .9,
bottom <= .1. Spread t-statistics are clustered by announcement quarter.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import RUNS_DIR  # noqa: E402
from pead.core import splits  # noqa: E402
from pead.core.data import load_equity_universe  # noqa: E402
from pead.core.gap_features import add_gap_features  # noqa: E402

HORIZONS = (5, 10, 20, 40, 60)


def main():
    uni = load_equity_universe()
    ev = add_gap_features(uni.events)
    e = ev["entry_row_idx"].to_numpy()
    pp = uni.panel_permno
    cum = np.concatenate([[0.0], np.cumsum(np.log1p(np.clip(uni.ret_mktadj, -0.99, None)))])
    ev["reaction"] = uni.ret_mktadj[e]
    for h in HORIZONS:
        end = np.minimum(e + h, len(pp) - 1)
        ok = (e + h < len(pp)) & (pp[end] == pp[e])
        ev[f"drift_{h}"] = np.where(ok, np.expm1(cum[end + 1] - cum[e + 1]), np.nan)
    ev["q"] = ev["ann_quarter"].astype(str)
    ev["era"] = pd.cut(ev["day0_date"].dt.year, [1995, 2001, 2007, 2014], labels=["1996-01", "2002-07", "2008-13"])
    s_q, _ = splits.split_quarters(ev["q"])
    ev["window"] = np.where(ev["q"].isin([str(x) for x in s_q]), "search", "holdout")
    legs = ["gap", "intraday", "reaction"] + [f"drift_{h}" for h in HORIZONS]
    top, bot = ev[ev["sue_rank_pct"] >= 0.9], ev[ev["sue_rank_pct"] <= 0.1]
    rows = []
    for gname, key in (("all", None), ("search", "window"), ("holdout", "window"), ("1996-01", "era"), ("2002-07", "era"), ("2008-13", "era")):
        t_, b_ = (top, bot) if key is None else (top[top[key] == gname], bot[bot[key] == gname])
        for leg in legs:
            a = t_.groupby("q")[leg].mean()
            b = b_.groupby("q")[leg].mean()
            d = (a - b).dropna()
            rows.append(dict(group=gname, leg=leg, top=t_[leg].mean(), bottom=b_[leg].mean(), spread=d.mean(),
                             t_cluster_q=d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 else np.nan, n_top=int(t_[leg].notna().sum())))
    out = pd.DataFrame(rows).round(4)
    p = RUNS_DIR / "event_study"
    p.mkdir(parents=True, exist_ok=True)
    out.to_csv(p / "reaction_decomposition.csv", index=False)
    print(out[out.group.isin(["all", "search", "holdout"])].to_string(index=False))


if __name__ == "__main__":
    main()
