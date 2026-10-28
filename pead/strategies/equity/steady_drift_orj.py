"""Steady Drift Plus with the overnight-gap surprise (EQ-24, research).

gap = open(day0)/close(day0-1) - 1 on split-adjusted prices, market-adjusted with the same-day cross-section median, scaled by the
stock's prior-60-session gap volatility (gap_z_adj) and ranked point-in-time (strictly earlier quarters, like sue_rank_pct).
Rules: orj_only (top decile gap rank), sue_and_orj (SUE decile AND gap rank >= .7), comp (top decile of the SUE/gap average
rank), weak_gap (SUE decile AND gap rank < .5: does a muted open mean the price already knew?).
"""
import dataclasses

import numpy as np
import pandas as pd

from pead.common.pit_rank import rank_pct
from pead.core.gap_features import add_gap_features
from pead.strategies import weights as W
from pead.strategies.equity.steady_drift import SteadyDriftPlus
from pead.strategies.registry import register


class SteadyDriftOrj(SteadyDriftPlus):
    rule = "orj_only"
    orj_top, orj_inter = 0.9, 0.7
    legacy_twin = False
    parent = "steady_drift_plus"
    status = "research"
    family = "equity-longonly"
    required_columns = SteadyDriftPlus.required_columns + ("orj_z_rank", "orj_comp_rank")

    def params(self):
        return {**super().params(), "orj_rule": self.rule, "orj_top": self.orj_top, "orj_inter": self.orj_inter}

    def weights(self, events):
        adv = events["avg_dollar_volume_21d"].astype(float).to_numpy()
        liquid = np.nan_to_num(adv, nan=0.0) >= self.min_dollar_volume
        sue = events["sue_rank_pct"].to_numpy(dtype=float)
        g = events["orj_z_rank"].to_numpy(dtype=float)
        comp = events["orj_comp_rank"].to_numpy(dtype=float)
        ok = np.isfinite(g)
        if self.rule == "orj_only":
            mask = ok & (g >= self.orj_top)
        elif self.rule == "sue_and_orj":
            mask = ok & (sue >= self.top_rank) & (g >= self.orj_inter)
        elif self.rule == "comp":
            mask = np.isfinite(comp) & (comp >= self.orj_top)
        elif self.rule == "weak_gap":
            mask = ok & (sue >= self.top_rank) & (g < 0.5)
        else:
            raise ValueError(self.rule)
        return W.causal_inverse_variance(events, mask & liquid)

    def backtest(self, uni):
        ev = add_gap_features(uni.events)
        q = ev["ann_quarter"].astype(str)
        ev["orj_z_rank"] = rank_pct(ev["gap_z_adj"], q, mode="prior_quarters")
        both = pd.concat([ev["sue_rank_pct"], ev["orj_z_rank"]], axis=1).mean(axis=1, skipna=False)
        ev["orj_comp_rank"] = rank_pct(both, q, mode="prior_quarters")
        return super().backtest(dataclasses.replace(uni, events=ev))


def _variant(suffix, rule, doc):
    @register
    class V(SteadyDriftOrj):
        name = f"steady_drift_plus_orj_{suffix}"
        description = doc
    V.rule = rule
    V.__name__ = f"SteadyDriftPlusOrj_{suffix}"


_variant("only", "orj_only", "Long-only top decile of the overnight-gap surprise (no SUE)")
_variant("sue_and_orj", "sue_and_orj", "SUE top decile that also has a top-30% overnight gap")
_variant("comp", "comp", "Top decile of the SUE/gap composite rank")
_variant("weak_gap", "weak_gap", "Falsification: SUE top decile with a muted (bottom-half) gap")
