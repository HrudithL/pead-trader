"""Steady Drift Plus behind a market-trend and/or earnings-breadth gate (OV-03, research).

trend: CRSP VW index above its 200-day mean and up over 126 sessions. breadth: share of the last 20 sessions' announcers with a
top-quartile SUE and a positive day-0 reaction exceeds c = 0.15 (fixed from the distribution of the breadth series alone, its
~22nd percentile, before any backtest). The gate at the close of t-1 scales day t's exposure to 1 (ON) or s_off; cash earns the
T-bill rate. Never levers.
"""
import dataclasses

import numpy as np

from pead.core.market_state import earnings_breadth, gate_scale, trend_gate
from pead.strategies.equity.steady_drift import SteadyDriftPlus
from pead.strategies.registry import register

BREADTH_C = 0.15


class SteadyDriftGate(SteadyDriftPlus):
    use_trend, use_breadth, s_off = True, True, 0.0
    legacy_twin = False
    parent = "steady_drift_plus"
    status = "research"
    family = "equity-longonly"

    def params(self):
        return {**super().params(), "gate_trend": self.use_trend, "gate_breadth": self.use_breadth, "gate_s_off": self.s_off,
                "breadth_c": BREADTH_C}

    def gate(self, uni):
        g = np.ones(len(uni.calendar), bool)
        if self.use_trend:
            g &= trend_gate(uni.vwretd)
        if self.use_breadth:
            b = earnings_breadth(uni)
            g &= ~(np.isfinite(b) & (b <= BREADTH_C))
        return g

    def exposure_scale(self, uni):
        return gate_scale(self.gate(uni), self.s_off)


def _variant(suffix, trend, breadth, s_off, doc, status="research"):
    @register
    class V(SteadyDriftGate):
        name = f"steady_drift_plus_{suffix}"
        description = doc
    V.use_trend, V.use_breadth, V.s_off, V.status = trend, breadth, s_off, status
    V.__name__ = f"SteadyDriftPlus_{suffix}"


_variant("trend", True, False, 0.0, "Steady Drift Plus, in cash when the market is below its 200-day mean or down over 6 months")
_variant("breadth_only", False, True, 0.0, "Control: earnings-breadth gate alone", status="reference")
_variant("gate", True, True, 0.0, "Steady Drift Plus, trend AND earnings-breadth gate (off = all cash)")
_variant("gate_half", True, True, 0.5, "Trend and breadth gate, half exposure when off")


@register
class SteadyDriftPlusGateEntry(SteadyDriftGate):
    """Variant B (weaker brake): no exposure scaling; events whose gate is OFF at their day-0 close get weight 0."""
    name = "steady_drift_plus_gate_entry"
    description = "Trend and breadth gate applied only to new entries (events entering while the gate is OFF are skipped)"

    def exposure_scale(self, uni):
        return None

    def weights(self, events):
        w = super().weights(events)
        on = events["gate_on_day0"].to_numpy(dtype=bool) if "gate_on_day0" in events.columns else np.ones(len(events), bool)
        return np.where(on, w, 0.0)

    def backtest(self, uni):
        g = self.gate(uni)
        idx = uni.calendar.get_indexer(uni.events["day0_date"])
        ev = uni.events.copy()
        ev["gate_on_day0"] = np.where(idx >= 0, g[np.maximum(idx, 0)], True)
        return super().backtest(dataclasses.replace(uni, events=ev))
