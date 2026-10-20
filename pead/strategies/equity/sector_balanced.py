"""Sector Balanced -- Conviction Tilt plus an equal budget for each industry group.

A diagnostic found Conviction Tilt's book putting up to 29% of gross weight in one FF12 sector in some
quarters (8.3% if spread evenly): an unpriced bet unrelated to earnings drift. Neutralizing each
(side, size, sector) cell to an equal budget removes it. Best Sharpe / shallowest drawdown of the
hand-built strategies at its conservative 1.5x leverage cap."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register


@register
class SectorBalanced(EquityStrategy):
    name = "sector_balanced"
    family = "equity-neutralized"
    parent = "conviction_tilt"
    description = "Conviction Tilt, also balanced across 12 industry groups; conservative 1.5x borrowing limit"
    thesis = "Same drift, with sector and size risk diversified away so only the earnings signal is left."
    sizing = Sizing(base_unit_fraction=0.0015, max_gross_leverage=1.5)
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT)
