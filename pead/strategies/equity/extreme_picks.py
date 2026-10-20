"""Extreme Picks -- long the best earnings surprises, short the worst.

The textbook PEAD trade: long the best earnings-surprise decile (D10), short the worst (D1), every
event equal-weighted, each held for the number of days its (decile x size x book-to-market) cell's
alpha takes to decay (per-cell holding windows, not a fixed horizon)."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class ExtremePicks(EquityStrategy):
    name = "extreme_picks"
    family = "equity-baseline"
    description = "Buy the top 10% of earnings surprises, short the bottom 10%, equal bets"
    thesis = ("Analysts and investors under-react to earnings news, so stocks with the most positive "
              "surprise keep drifting up and the most negative keep drifting down for weeks.")
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=1.5, cap_mode="proportional")
    required_columns = ("decile",)

    def weights(self, events) -> np.ndarray:
        return W.extreme_decile(events)
