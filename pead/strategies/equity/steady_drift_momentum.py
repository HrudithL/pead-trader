"""Steady Drift Plus x prior 12-1 month momentum (OV-02, research): does momentum add to the SUE top decile?

mom_12_1 = return from row e-252 to row e-21 of the stock's own panel (e = day0 row), known at the entry close.
mom_rank_pct = percentile among events whose day0 is in the previous 365 days (strictly earlier), pool >= 200.
Variants: filter (rank >= .5), tilt (weight x (0.5 + rank)), contrarian control (rank < .5) and random-drop controls that
remove the same share of names without looking at momentum.
"""
import dataclasses

import numpy as np
import pandas as pd

from pead.strategies import weights as W
from pead.strategies.equity.steady_drift import SteadyDriftPlus
from pead.strategies.registry import register


def momentum_12_1(uni) -> np.ndarray:
    cum = np.cumsum(np.log1p(np.clip(uni.ret_raw, -0.99, None)))
    e = uni.events["entry_row_idx"].to_numpy()
    ok = e >= 252
    i0, i1 = np.maximum(e - 252, 0), np.maximum(e - 21, 0)
    same = uni.panel_permno[i0] == uni.panel_permno[e]
    span = (uni.panel_date[e] - uni.panel_date[i0]).astype("timedelta64[D]").astype(int)
    valid = ok & same & (span >= 330) & (span <= 400)
    return np.where(valid, np.expm1(cum[i1] - cum[i0]), np.nan)


def causal_rank(values: np.ndarray, dates: pd.Series, window_days: int = 365, min_pool: int = 200) -> np.ndarray:
    d = pd.to_datetime(dates).to_numpy()
    order = np.argsort(d, kind="stable")
    ds, vs = d[order], values[order]
    out = np.full(len(values), np.nan)
    lo_td = np.timedelta64(window_days, "D")
    for day in np.unique(ds):
        a = np.searchsorted(ds, day - lo_td, side="left")
        b = np.searchsorted(ds, day, side="left")                      # strictly earlier days
        pool = vs[a:b]
        pool = np.sort(pool[np.isfinite(pool)])
        if len(pool) < min_pool:
            continue
        m = (ds == day)
        idx = np.flatnonzero(m)
        v = vs[idx]
        out[order[idx]] = np.where(np.isfinite(v), np.searchsorted(pool, v, side="right") / len(pool), np.nan)
    return out


class SteadyDriftMomentum(SteadyDriftPlus):
    mode = "none"
    seed = 0
    legacy_twin = False
    parent = "steady_drift_plus"
    status = "research"
    family = "equity-longonly"
    required_columns = SteadyDriftPlus.required_columns + ("mom_rank_pct",)

    def params(self):
        return {**super().params(), "momentum_mode": self.mode, "seed": self.seed}

    def weights(self, events):
        adv = events["avg_dollar_volume_21d"].astype(float).to_numpy()
        mask = (events["sue_rank_pct"].to_numpy() >= self.top_rank) & (np.nan_to_num(adv, nan=0.0) >= self.min_dollar_volume)
        r = events["mom_rank_pct"].to_numpy(dtype=float)
        if self.mode == "filter":
            mask = mask & np.isfinite(r) & (r >= 0.5)
        elif self.mode == "contrarian":
            mask = mask & np.isfinite(r) & (r < 0.5)
        elif self.mode == "random_drop":
            keep = np.random.default_rng(self.seed).uniform(size=len(events)) >= 0.5
            mask = mask & keep
        w = W.causal_inverse_variance(events, mask)
        if self.mode == "tilt":
            w = w * np.where(np.isfinite(r), 0.5 + r, 1.0)
        return w

    def backtest(self, uni):
        ev = uni.events.copy()
        mom = momentum_12_1(uni)
        ev["mom_12_1"] = mom
        ev["mom_rank_pct"] = causal_rank(mom, ev["day0_date"])
        return super().backtest(dataclasses.replace(uni, events=ev))


def _variant(suffix, mode, seed=0, doc=""):
    @register
    class V(SteadyDriftMomentum):
        name = f"steady_drift_plus_mom_{suffix}"
        description = doc
    V.mode, V.seed = mode, seed
    V.__name__ = f"SteadyDriftPlusMom_{suffix}"


_variant("filter", "filter", doc="Steady Drift Plus, only events whose prior 12-1 month momentum rank is in the top half")
_variant("tilt", "tilt", doc="Steady Drift Plus, weights tilted by momentum rank (0.5-1.5x)")
_variant("contrarian", "contrarian", doc="Control: only events with bottom-half momentum")
for _s in range(5):
    _variant(f"randdrop{_s}", "random_drop", _s, f"Control: random half of the base events dropped (seed {_s})")
