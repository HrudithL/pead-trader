"""Strategy 4 -- Trimmed, tilted, size-neutral, priority-capped.

Built after diagnosing why rank-weighting had the better Sharpe but the worse return: it traded ~4x
more positions and saturated the liquidity/leverage caps. Strategy 4 drops the low-conviction middle
of the SUE-rank distribution (trim 0.15), leans weight toward the extremes (tilt), keeps size
neutrality, and when the leverage cap binds drops the LOWEST-conviction positions first instead of
shaving everything equally."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class TiltedSizeNeutral(EquityStrategy):
    name = "s4_tilted_size_neutral"
    family = "equity-neutralized"
    parent = "rank_weighted"
    description = "Trim 0.15, linear tilt, size-neutral per (quarter, side), priority-based cap trimming"
    thesis = "Concentrate capital where the surprise is largest; neutralize size so it is not a hidden bet."
    sizing = Sizing(base_unit_fraction=0.002, max_gross_leverage=1.5)
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT,
                                  groups=("size_quintile",))
