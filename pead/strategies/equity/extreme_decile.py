"""Strategy 1 -- Extreme decile long/short.

The textbook PEAD trade: long the best earnings-surprise decile (D10), short the worst (D1), every
event equal-weighted, each held for the number of days its (decile x size x book-to-market) cell's
alpha takes to decay (per-cell holding windows, not a fixed horizon)."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class ExtremeDecile(EquityStrategy):
    name = "extreme_decile"
    family = "equity-baseline"
    description = "Long D10 / short D1, equal-weighted, per-cell holding periods"
    thesis = ("Analysts and investors under-react to earnings news, so stocks with the most positive "
              "surprise keep drifting up and the most negative keep drifting down for weeks.")
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=1.5, cap_mode="proportional")
    required_columns = ("decile",)

    def weights(self, events) -> np.ndarray:
        return W.extreme_decile(events)
