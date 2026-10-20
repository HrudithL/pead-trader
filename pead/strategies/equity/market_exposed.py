"""Market Exposed -- like Balanced Leverage, but longs and shorts need not match, so it rides the market (cautionary result).

Balanced Leverage's signal, but the long and short legs are no longer forced to equal dollar totals, and
returns are RAW so real market beta is carried. Net dollar exposure is only ~10% of gross (as in every
other book) yet the book is 0.52-correlated with the market, because raw returns carry each stock's own
beta and equal dollars long and short are not equal BETA: high-SUE and low-SUE firms differ in beta. Kept as a documented counter-example
to the explicit beta overlay."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class MarketExposed(EquityStrategy):
    name = "market_exposed"
    family = "equity-directional"
    parent = "balanced_leverage"
    status = "reference"
    description = "Balanced Leverage signal, longs and shorts not forced to match, market moves left in"
    thesis = "Let net exposure float with the signal; empirically this smuggles in uncontrolled market beta."
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5, return_basis="raw")
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT, by_side=False)
