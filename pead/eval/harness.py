"""Evaluation harness (MT-04, first cut): search vs holdout metrics for a saved run.

Works from `results/<name>/daily.csv.gz` (no re-backtest). Returns per-window active-window stats, the holdout/search
degradation and a DSR computed from the trial registry. Gate logic beyond G3/G5 (alpha, bootstrap, LOO, placebo) lands with
the stress modules; levels above V1 are not awarded here.
"""
import json

import numpy as np
import pandas as pd

from pead.common.paths import INITIAL_CAPITAL, RUNS_DIR
from pead.core import splits, stats
from pead.eval import registry


def _window_stats(r: pd.Series) -> dict:
    r = r[r.index.notna()]
    nz = np.flatnonzero(r.to_numpy() != 0)
    if len(nz) < 30:
        return dict(n_days=int(len(nz)))
    x = r.iloc[nz[0]:nz[-1] + 1]
    nav = INITIAL_CAPITAL * np.cumprod(1 + x.to_numpy())
    sk, ku = float(x.skew()), float(x.kurt() + 3)
    sr_d = float(x.mean() / x.std(ddof=1))
    return dict(n_days=int(len(x)), cagr=stats.cagr(INITIAL_CAPITAL, nav[-1], len(x)), sharpe=stats.sharpe(x),
                sharpe_daily=sr_d, max_drawdown=stats.max_drawdown(nav, INITIAL_CAPITAL), skew=sk, kurtosis=ku,
                psr_0=stats.psr(sr_d, len(x), sk, ku))


def evaluate(name: str, ann_quarters, protocol_id: str = "v1", family: str = None, results_dir=None) -> dict:
    proto = splits.load_protocol(protocol_id)
    w = splits.windows(ann_quarters, proto)
    base = (results_dir or RUNS_DIR) / name
    daily = pd.read_csv(base / "daily.csv.gz", parse_dates=["date"])
    r = stats.returns_from_daily(daily, INITIAL_CAPITAL)
    search, hold = r[r.index <= w.search_end], r[r.index >= w.holdout_start]
    out = dict(name=name, protocol=protocol_id, holdout_kind=w.kind, search_end=str(w.search_end.date()),
               search=_window_stats(search), holdout=_window_stats(hold))
    if "sharpe" in out["search"] and "sharpe" in out["holdout"]:
        out["degradation_sharpe_ratio"] = out["holdout"]["sharpe"] / out["search"]["sharpe"]
        n = registry.n_trials(family)
        v = registry.trial_sharpe_variance(family)
        h = out["holdout"]
        if n >= 2 and np.isfinite(v):
            out["dsr_holdout"] = stats.dsr(h["sharpe_daily"], h["n_days"], h["skew"], h["kurtosis"], n, v)
            out["dsr_n_trials"] = n
        else:
            out["dsr_holdout"] = None
            out["dsr_note"] = "trial registry has <2 trials for this family; DSR withheld"
    return out


def write_evaluation(ev: dict, results_dir=None) -> None:
    p = (results_dir or RUNS_DIR) / ev["name"] / "evaluation.json"
    p.write_text(json.dumps(ev, indent=2, default=float), encoding="utf-8")
