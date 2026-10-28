"""Steady Drift Plus with the day-0 market reaction as a confirmation filter, plus entry-lag controls (EQ-23, research).

`d0_excess` = the stock's raw return on the day0 close minus the market return that day, known at the close at which the
position is entered. Variants: confirm_pos (> 0), confirm_notneg (> -2%), confirm_z (tilt by the reaction in units of
trailing volatility), contrarian (< 0, falsification), and lag1/lag2 (enter k sessions later, no filter) which measure how
much of the edge needs the same-close fill. Hypothesis from the plan: filters are noise; lag costs something.
"""
import dataclasses

import numpy as np

from pead.engines.equity import with_entry_lag
from pead.strategies import weights as W
from pead.strategies.equity.steady_drift import SteadyDriftPlus
from pead.strategies.registry import register


class SteadyDriftReaction(SteadyDriftPlus):
    mode = "none"
    lag = 0
    legacy_twin = False
    family = "equity-longonly"
    parent = "steady_drift_plus"
    status = "research"
    required_columns = SteadyDriftPlus.required_columns + ("d0_excess", "tvol_60d")
    lam, z_clip = 0.5, 3.0

    def params(self):
        return {**super().params(), "reaction_mode": self.mode, "entry_lag_sessions": self.lag}

    def weights(self, events):
        adv = events["avg_dollar_volume_21d"].astype(float).to_numpy()
        mask = (events["sue_rank_pct"].to_numpy() >= self.top_rank) & (np.nan_to_num(adv, nan=0.0) >= self.min_dollar_volume)
        d0 = events["d0_excess"].to_numpy(dtype=float) if "d0_excess" in events.columns else np.full(len(events), np.nan)
        thresholds = {"confirm_pos": lambda x: x > 0, "confirm_notneg": lambda x: x > -0.02, "contrarian": lambda x: x < 0,
                      "confirm_z": lambda x: x > 0}
        if self.mode in thresholds:
            mask = mask & np.isfinite(d0) & thresholds[self.mode](d0)
        w = W.causal_inverse_variance(events, mask)
        if self.mode == "confirm_z":
            z = d0 / np.maximum(events["tvol_60d"].to_numpy(dtype=float), 0.005)
            w = w * (1 + self.lam * np.clip(np.nan_to_num(z) / self.z_clip, -1, 1))
        return w

    def backtest(self, uni):
        ev = uni.events.copy()
        entry = ev["entry_row_idx"].to_numpy()
        day_idx = uni.calendar.get_indexer(ev["day0_date"])
        vw = np.where(day_idx >= 0, uni.vwretd[np.maximum(day_idx, 0)], np.nan)
        ev["d0_excess"] = uni.ret_raw[entry] - vw
        uni = dataclasses.replace(uni, events=ev)
        return super().backtest(with_entry_lag(uni, self.lag))


def _variant(suffix, mode, lag=0, doc=""):
    @register
    class V(SteadyDriftReaction):
        name = f"steady_drift_plus_{suffix}"
        description = doc
    V.mode, V.lag = mode, lag
    V.__name__ = f"SteadyDriftPlus_{suffix}"
    return V


_variant("confirm_pos", "confirm_pos", doc="Steady Drift Plus, only events with a positive day-0 market-adjusted reaction")
_variant("confirm_notneg", "confirm_notneg", doc="Steady Drift Plus, drop events reacting worse than -2% on day 0")
_variant("confirm_z", "confirm_z", doc="Steady Drift Plus, positive reaction required and sized up by reaction/volatility")
_variant("contrarian", "contrarian", doc="Falsification: only events with a NEGATIVE day-0 reaction")
_variant("lag1", "none", 1, "Steady Drift Plus entered one session after day0 (same-close fill sensitivity)")
_variant("lag2", "none", 2, "Steady Drift Plus entered two sessions after day0")
