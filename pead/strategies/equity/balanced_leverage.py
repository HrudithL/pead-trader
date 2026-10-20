"""Balanced Leverage -- Sector Balanced, but allowed to hold bigger positions (2.5x the account).

Sector Balanced's weights, but with the 1.5x cap lifted to 2.5x (the point where it stops binding) and
sizing retuned. Because sector+size neutralization already prevents concentration, the cap was
protecting nothing: return rises with the Sharpe holding about level (corrected run: 3.1% -> 5.4%, Sharpe 1.08 -> 1.09; the earlier 4.1% -> 6.5% / 1.3 was pre-fix). The pure-alpha
reference strategy for the repo."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class BalancedLeverage(EquityStrategy):
    name = "balanced_leverage"
    family = "equity-neutralized"
    parent = "sector_balanced"
    description = "Sector Balanced, allowed to hold up to 2.5x the account in positions"
    thesis = "Market-neutral earnings drift with enough gross exposure to matter; the neutrality is the hedge."
    live_ready = True
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT)
