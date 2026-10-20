"""Conviction Tilt -- skip the unsure middle, lean on the strongest signals, balance by size.

Built after diagnosing why rank-weighting had the better Sharpe but the worse return: it traded ~4x
more positions and saturated the liquidity/leverage caps. This strategy drops the low-conviction middle
of the SUE-rank distribution (trim 0.15), leans weight toward the extremes (tilt), keeps size
neutrality, and when the leverage cap binds drops the LOWEST-conviction positions first instead of
shaving everything equally."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class ConvictionTilt(EquityStrategy):
    name = "conviction_tilt"
    family = "equity-neutralized"
    parent = "graded_bets"
    description = "Skip the middle 15% either side of average, lean toward the strongest surprises, balance by size"
    thesis = "Concentrate capital where the surprise is largest; neutralize size so it is not a hidden bet."
    sizing = Sizing(base_unit_fraction=0.002, max_gross_leverage=1.5)
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT,
                                  groups=("size_quintile",))
