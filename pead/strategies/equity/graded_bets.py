"""Graded Bets -- every stock gets a bet, bigger the further its surprise is from average.

Trades every decile with weight proportional to SUE-rank distance from the median: broader
diversification than the extreme-decile book, at the price of ~4x more positions (which saturates
the liquidity and leverage caps and dilutes the high-conviction names)."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class GradedBets(EquityStrategy):
    name = "graded_bets"
    family = "equity-baseline"
    description = "Bet on every stock; the more extreme the surprise, the bigger the bet"
    thesis = "Same drift as Extreme Picks but harvested across the whole cross-section for lower variance."
    sizing = Sizing(base_unit_fraction=0.002, max_gross_leverage=1.5, cap_mode="proportional")
    required_columns = ("sue_rank_pct",)

    def weights(self, events) -> np.ndarray:
        return W.rank_weighted(events)
