"""MT-09: paired block-bootstrap comparison of every steady_drift_plus_* candidate against the base, same dates for both.

    python scripts/paired_ablation.py [--parent steady_drift_plus]

For each candidate with a saved daily series, the draw statistic is Sharpe(candidate) - Sharpe(parent) over month blocks resampled
jointly (2,000 draws, seed 20261027), on the full active window and on the burned holdout alone.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import INITIAL_CAPITAL, RUNS_DIR  # noqa: E402
from pead.core import splits, stats  # noqa: E402
from pead.core.data import load_equity_universe  # noqa: E402


def series(name):
    d = pd.read_csv(RUNS_DIR / name / "daily.csv.gz", parse_dates=["date"])
    return stats.returns_from_daily(d, INITIAL_CAPITAL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent", default="steady_drift_plus")
    a = ap.parse_args()
    win = splits.windows(load_equity_universe().events["ann_quarter"])
    parent = series(a.parent)
    rows = []
    for d in sorted(RUNS_DIR.glob(f"{a.parent}_*")):
        if not (d / "daily.csv.gz").exists():
            continue
        c = series(d.name)
        idx = parent.index.intersection(c.index)
        for label, m in (("full", slice(None)), ("holdout", idx >= win.holdout_start)):
            ii = idx[m] if label == "holdout" else idx
            p_, c_ = parent.loc[ii].to_numpy(), c.loc[ii].to_numpy()
            keep = (p_ != 0) | (c_ != 0)
            p_, c_, ii = p_[keep], c_[keep], ii[keep]
            if len(ii) < 250:
                continue
            draws = stats.paired_block_bootstrap(c_, p_, ii, "month", n=1000, seed=20261027)
            rows.append(dict(candidate=d.name.replace(a.parent + "_", ""), window=label, dsharpe=stats.sharpe(c_) - stats.sharpe(p_),
                             lo95=np.percentile(draws, 2.5), hi95=np.percentile(draws, 97.5),
                             p_better=float((draws > 0).mean())))
    out = pd.DataFrame(rows).round(3)
    p = RUNS_DIR / "paired_ablation"
    p.mkdir(parents=True, exist_ok=True)
    out.to_csv(p / f"{a.parent}.csv", index=False)
    print(out.sort_values(["window", "dsharpe"], ascending=[True, False]).to_string(index=False))


if __name__ == "__main__":
    main()
