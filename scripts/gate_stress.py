"""OV-03 stress: SMA x breadth-threshold grid, entry-gate variant, and an expanding-window walk-forward for the half-exposure gate.

    python scripts/gate_stress.py

The alpha book is run once; every grid cell only swaps the exposure scale (causal, signal through t-1). Every cell is logged to the
trial registry (family equity-longonly). Walk-forward: for each test year from 2001, pick the cell with the best Sharpe over all
earlier years only, apply it in that year, and join the out-of-sample years; compared with the base and the fixed default cell.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pead.strategies.base as base  # noqa: E402
from pead.common.paths import INITIAL_CAPITAL, RUNS_DIR  # noqa: E402
from pead.core import splits, stats  # noqa: E402
from pead.core.data import load_equity_universe  # noqa: E402
from pead.core.market_state import earnings_breadth, gate_scale, trend_gate  # noqa: E402
from pead.eval import registry as trials  # noqa: E402
from pead.strategies import registry  # noqa: E402

SMAS, CS, S_OFF = (150, 200, 250), (0.13, 0.15, 0.18), 0.5
_cache, _orig = {}, base.run_equity_backtest


def _memo(uni, w, sizing):
    if id(uni) not in _cache:
        _cache[id(uni)] = _orig(uni, w, sizing)
    return _cache[id(uni)]


def main():
    base.run_equity_backtest = _memo
    uni = load_equity_universe()
    s = registry.get("steady_drift_plus")
    b2 = earnings_breadth(uni)
    ann_q = uni.events["ann_quarter"]
    win = splits.windows(ann_q)
    series = {}
    s.exposure_scale = lambda u: None
    series["base"] = s.backtest(uni).daily.set_index("date")["daily_return"]
    rows = []
    for sma in SMAS:
        tg = trend_gate(uni.vwretd, sma=sma)
        for c in CS:
            g = tg & ~(np.isfinite(b2) & (b2 <= c))
            sc = gate_scale(g, S_OFF)
            s.exposure_scale = lambda u, sc=sc: sc
            run = s.backtest(uni)
            r = run.daily.set_index("date")["daily_return"]
            series[(sma, c)] = r
            sm = run.summary
            sr, ho = r[r.index <= win.search_end], r[r.index >= win.holdout_start]
            rows.append(dict(sma=sma, c=c, cagr=sm["annualized_return"], sharpe=sm["sharpe"], max_dd=sm["max_drawdown"],
                             on_frac=float(g.mean()), search_sharpe=stats.sharpe(sr[sr != 0]), holdout_sharpe=stats.sharpe(ho[ho != 0]),
                             holdout_dd=stats.max_drawdown(INITIAL_CAPITAL * (1 + ho).cumprod(), INITIAL_CAPITAL)))
            trials.log_trial(f"steady_drift_plus_gate_half[sma={sma},c={c}]", "equity-longonly",
                             stats.sharpe(sr[sr != 0]) / np.sqrt(252), {"sma": sma, "c": c, "s_off": S_OFF},
                             note="OV-03 grid, search window", window="search")
    s.exposure_scale = lambda u: None
    grid = pd.DataFrame(rows).round(4)
    # walk-forward
    cells = [k for k in series if k != "base"]
    years = sorted(set(series["base"].index.year))
    oos, picks = [], []
    for y in years:
        if y < 2001:
            continue
        past = {k: series[k][series[k].index.year < y] for k in cells}
        past = {k: v[v != 0] for k, v in past.items()}
        best = max(cells, key=lambda k: stats.sharpe(past[k]) if len(past[k]) > 250 else -9)
        picks.append(dict(year=y, sma=best[0], c=best[1]))
        oos.append(series[best][series[best].index.year == y])
    wf = pd.concat(oos)
    ref = series[(200, 0.15)].loc[wf.index]
    bs = series["base"].loc[wf.index]
    cmp_ = pd.DataFrame([
        dict(name="base steady_drift_plus", sharpe=stats.sharpe(bs[bs != 0]), mdd=stats.max_drawdown(INITIAL_CAPITAL * (1 + bs).cumprod(), INITIAL_CAPITAL)),
        dict(name="fixed default gate (200, 0.15)", sharpe=stats.sharpe(ref[ref != 0]), mdd=stats.max_drawdown(INITIAL_CAPITAL * (1 + ref).cumprod(), INITIAL_CAPITAL)),
        dict(name="walk-forward selected gate", sharpe=stats.sharpe(wf[wf != 0]), mdd=stats.max_drawdown(INITIAL_CAPITAL * (1 + wf).cumprod(), INITIAL_CAPITAL))]).round(4)
    out = RUNS_DIR / "gate_stress"
    out.mkdir(parents=True, exist_ok=True)
    grid.to_csv(out / "grid.csv", index=False)
    pd.DataFrame(picks).to_csv(out / "walk_forward_picks.csv", index=False)
    cmp_.to_csv(out / "walk_forward_compare.csv", index=False)
    print(grid.to_string(index=False)); print(cmp_.to_string(index=False)); print(pd.DataFrame(picks).to_string(index=False))


if __name__ == "__main__":
    main()
