"""Size Balanced -- Extreme Picks with an equal budget for each company-size group.

Extreme Picks' trade, but each leg's budget is spread evenly over the size quintiles present that
quarter, so micro-caps (where most extreme surprises happen) cannot dominate the book."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class SizeBalanced(EquityStrategy):
    name = "size_balanced"
    family = "equity-baseline"
    parent = "extreme_picks"
    description = "Extreme Picks, with an equal budget for each of the five company sizes"
    thesis = "PEAD is stronger in small caps; balancing by size keeps the edge but removes a size bet."
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=1.5, cap_mode="proportional")
    required_columns = ("decile", "size_quintile", "event_id")

    def weights(self, events) -> np.ndarray:
        return W.size_balanced_extreme(events)
