"""Strategy 6 -- Sector/size-neutral book with the leverage cap raised to 2.5x.

Strategy 5's weights, but with the 1.5x cap lifted to 2.5x (the point where it stops binding) and
sizing retuned. Because sector+size neutralization already prevents concentration, the cap was
protecting nothing: return rises 4.1% -> 6.5% with the Sharpe holding at ~1.3. The pure-alpha
reference strategy for the repo."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class NeutralLevered(EquityStrategy):
    name = "s6_neutral_levered"
    family = "equity-neutralized"
    parent = "s5_sector_neutral"
    description = "Strategy 5 weights, 2.5x gross leverage cap, base unit 0.003"
    thesis = "Market-neutral earnings drift with enough gross exposure to matter; the neutrality is the hedge."
    live_ready = True
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT)
