"""Leaderboard v2 (MT-04 3E): active-window metrics with uncertainty, search/holdout split and a deflated Sharpe.

Ranks by the lower 95% bound of a month-block bootstrap of the Sharpe ratio, not by raw Sharpe. Columns: active-window CAGR and
Sharpe, Sharpe CI, PSR(0), holdout Sharpe and the holdout/search ratio, DSR at the registry's trial count (a LOWER BOUND: the
registry is backfilled by file count and every later variant), and a validation level: `ref` for `*_legacy` / reference runs, `V1`
otherwise (the strategy passes the look-ahead suite and declares its causality; nothing is V2+ because no strategy is frozen and the
holdout is burned). Written to results/leaderboard_v2.{md,csv}; the original leaderboard is untouched.
"""
import json

import numpy as np
import pandas as pd

from pead.common.paths import INITIAL_CAPITAL, RUNS_DIR
from pead.core import splits, stats
from pead.eval import registry as trials


def _row(name, summary, daily, win, n_boot, n_tr, var_tr):
    r = stats.returns_from_daily(daily, INITIAL_CAPITAL)
    w = stats.active_window(daily)
    x = r.loc[w.start:w.end]
    arr = x.to_numpy()
    nav = INITIAL_CAPITAL * np.cumprod(1 + arr)
    sk, ku = float(x.skew()), float(x.kurt() + 3)
    sr_d = float(arr.mean() / arr.std(ddof=1))
    boot = stats.block_bootstrap(arr, x.index, "month", n=n_boot, seed=20261027)
    hs, ss = r[r.index >= win.holdout_start], r[r.index <= win.search_end]
    hs, ss = hs[hs != 0], ss[ss != 0]
    h_sh = stats.sharpe(hs) if len(hs) > 250 else np.nan
    s_sh = stats.sharpe(ss) if len(ss) > 250 else np.nan
    dsr = stats.dsr(sr_d, len(arr), sk, ku, n_tr, var_tr) if n_tr >= 2 and np.isfinite(var_tr) else np.nan
    return dict(name=name, asset_class=summary.get("asset_class", ""), status=summary.get("status", ""),
                V="ref" if (name.endswith("_legacy") or summary.get("status") in ("reference", "unverified"))
                else ("thin" if summary.get("summary", {}).get("n_positions", 1e9) < 3000 else "V1"),
                cagr_active=stats.cagr(INITIAL_CAPITAL, nav[-1], len(arr)), sharpe=stats.sharpe(arr),
                sharpe_lo95=float(np.percentile(boot, 2.5)), sharpe_hi95=float(np.percentile(boot, 97.5)),
                psr0=stats.psr(sr_d, len(arr), sk, ku), dsr=dsr, holdout_sharpe=h_sh,
                holdout_over_search=h_sh / s_sh if np.isfinite(h_sh) and np.isfinite(s_sh) and s_sh else np.nan,
                max_drawdown=stats.max_drawdown(nav, INITIAL_CAPITAL), n_days_active=len(arr))


def build(ann_quarters, n_boot: int = 300, results_dir=None) -> pd.DataFrame:
    base = results_dir or RUNS_DIR
    win = splits.windows(ann_quarters)
    n_tr, var_tr = trials.n_trials(), trials.trial_sharpe_variance()
    rows = []
    for d in sorted(base.iterdir()):
        f, dailyf = d / "summary.json", d / "daily.csv.gz"
        if not (f.exists() and dailyf.exists()):
            continue
        meta = json.loads(f.read_text(encoding="utf-8"))
        daily = pd.read_csv(dailyf, parse_dates=["date"])
        if len(daily) < 500 or (daily["daily_pnl"] != 0).sum() < 300:
            continue
        rows.append(_row(d.name, meta, daily, win, n_boot, n_tr, var_tr))
    out = pd.DataFrame(rows)
    out["n_trials_lb"] = n_tr
    return out.sort_values(["V", "sharpe_lo95"], ascending=[False, False]).reset_index(drop=True)


def _fmt(df):
    show = df.copy()
    for c in ("cagr_active", "max_drawdown"):
        show[c] = (show[c] * 100).round(2).astype(str) + "%"
    for c in ("sharpe", "sharpe_lo95", "sharpe_hi95", "psr0", "dsr", "holdout_sharpe", "holdout_over_search"):
        show[c] = show[c].round(2)
    cols = ["name", "asset_class", "V", "cagr_active", "sharpe", "sharpe_lo95", "sharpe_hi95", "psr0", "dsr", "holdout_sharpe",
            "holdout_over_search", "max_drawdown"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in show[cols].itertuples(index=False)]
    return "\n".join(lines)


def write(df: pd.DataFrame, results_dir=None) -> None:
    base = results_dir or RUNS_DIR
    df.to_csv(base / "leaderboard_v2.csv", index=False)
    cur = df[df["V"] == "V1"]
    txt = ["# Leaderboard v2", "",
           "Ranked within validation level by the lower 95% bound of a month-block bootstrap of the active-window Sharpe (300 draws, seed 20261027). "
           f"`dsr` uses the registry's {int(df['n_trials_lb'].iloc[0])} trials, a lower bound. Holdout = 2008Q3-2013Q4 and is burned. "
           "`V1` = passes the look-ahead suite; `thin` = fewer than 3,000 funded positions (a mostly-cash book whose Sharpe is not comparable); no strategy is frozen, so none is V2 or above. `ref` = legacy or reference runs.", "",
           "## Current runs (V1)", "", _fmt(cur), "", "## Thin books (not rankable)", "", _fmt(df[df["V"] == "thin"]), "", "## Reference / legacy runs", "", _fmt(df[df["V"] == "ref"]), ""]
    (base / "leaderboard_v2.md").write_text("\n".join(txt), encoding="utf-8")
