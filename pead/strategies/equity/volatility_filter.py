"""Volatility Filter (and Calm Weighted Filter) -- skip stock groups that have not paid, judged only on past data.

Drops a (side, idio-vol tercile) cell from new entries when its own history says it has not earned its
keep, judged ONLY on quarters whose positions had fully exited. Result: filtering does not help on its
own (corrected run: Sharpe 0.89 vs 1.09 for Balanced Leverage; pre-fix 1.23 vs 1.30; the effect is a smooth gradient, not one broken
cell) and adds nothing once inverse-vol sizing is on (corrected: Calm Weighted Filter 1.08 < Calm Weighted 1.24; pre-fix 1.41 < 1.47). Kept as a documented
negative result: size by vol, do not filter by it."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register
from pead.strategies.vol import inverse_ivol_scale, walk_forward_cell_filter


@register
class VolatilityFilter(EquityStrategy):
    name = "volatility_filter"
    family = "equity-volatility"
    parent = "balanced_leverage"
    status = "research"
    description = "Balanced Leverage, skipping long/short x jumpiness groups that have not made money so far"
    thesis = "Skip the cells that history says do not pay -- but the data shows a gradient, not a bad cell."
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
    required_columns = ("ann_quarter", "sue_rank_pct", "size_quintile", "ff12_sector", "ivol_252d_tercile",
                        "hold_ret", "exit_date", "day0_date")
    outcome_columns_allowed = ("hold_ret", "exit_date")   # walk_forward_cell_filter reads only quarters fully exited before the entry quarter
    TRIM, TILT = 0.15, 1.0

    def scale(self, events):
        return walk_forward_cell_filter(events, self.TRIM)

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT, scale=self.scale(events))


@register
class CalmWeightedFilter(VolatilityFilter):
    name = "calm_weighted_filter"
    description = "Calm Weighted plus the Volatility Filter"
    required_columns = VolatilityFilter.required_columns + ("ivol_252d", "tvol_60d")

    def scale(self, events):
        return inverse_ivol_scale(events) * walk_forward_cell_filter(events, self.TRIM)
