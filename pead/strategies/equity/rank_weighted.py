"""Strategy 2 -- Rank-weighted.

Trades every decile with weight proportional to SUE-rank distance from the median: broader
diversification than the extreme-decile book, at the price of ~4x more positions (which saturates
the liquidity and leverage caps and dilutes the high-conviction names)."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class RankWeighted(EquityStrategy):
    name = "rank_weighted"
    family = "equity-baseline"
    description = "All 10 deciles, continuous SUE-rank weight"
    thesis = "Same drift as Strategy 1 but harvested across the whole cross-section for lower variance."
    sizing = Sizing(base_unit_fraction=0.002, max_gross_leverage=1.5, cap_mode="proportional")
    required_columns = ("sue_rank_pct",)

    def weights(self, events) -> np.ndarray:
        return W.rank_weighted(events)
