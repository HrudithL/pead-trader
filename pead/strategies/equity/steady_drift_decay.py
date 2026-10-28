"""Steady Drift Plus with a decay-aware exposure scale (EQ-28 part B, research).

Scales the risk exposure down (never up, never below 40%) when the top-SUE-decile drift realised over the last two years is
weak relative to its own history, using only returns completed through the prior close. Cash earns the T-bill rate meanwhile.
"""
from pead.core.decay import decay_scale
from pead.strategies.equity.steady_drift import SteadyDriftPlus
from pead.strategies.registry import register


class SteadyDriftDecay(SteadyDriftPlus):
    k = 20
    s_min = 0.4
    legacy_twin = False
    parent = "steady_drift_plus"
    status = "research"
    family = "equity-longonly"

    def params(self):
        return {**super().params(), "decay_horizon_sessions": self.k, "decay_s_min": self.s_min, "decay_window_days": 750}

    def exposure_scale(self, uni):
        return decay_scale(uni, k=self.k, s_min=self.s_min)


@register
class SteadyDriftPlusDecay(SteadyDriftDecay):
    name = "steady_drift_plus_decay"
    description = "Steady Drift Plus, exposure scaled down when the recent 20-session top-decile drift is weak"


@register
class SteadyDriftPlusDecay60(SteadyDriftDecay):
    name = "steady_drift_plus_decay60"
    description = "Steady Drift Plus, exposure scaled by the recent 60-session top-decile drift"
    k = 60
