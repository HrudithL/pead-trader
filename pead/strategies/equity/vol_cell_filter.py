"""Strategy 9a / 9c -- walk-forward volatility-cell filter (alone and combined with inverse-vol).

Drops a (side, idio-vol tercile) cell from new entries when its own history says it has not earned its
keep, judged ONLY on quarters whose positions had fully exited. Result: filtering does not help on its
own (Sharpe 1.23 vs 1.30 baseline-equivalent run; the effect is a smooth gradient, not one broken
cell) and adds nothing once inverse-vol sizing is on (9c 1.41 < 9b 1.47). Kept as a documented
negative result: size by vol, do not filter by it."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register
from pead.strategies.vol import inverse_ivol_scale, walk_forward_cell_filter


@register
class VolCellFilter(EquityStrategy):
    name = "s9a_vol_cell_filter"
    family = "equity-volatility"
    parent = "s6_neutral_levered"
    status = "research"
    description = "Strategy 6 minus (side, ivol tercile) cells with non-positive walk-forward excess return"
    thesis = "Skip the cells that history says do not pay -- but the data shows a gradient, not a bad cell."
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
    required_columns = ("ann_quarter", "sue_rank_pct", "size_quintile", "ff12_sector", "ivol_252d_tercile",
                        "hold_ret", "exit_date", "day0_date")
    TRIM, TILT = 0.15, 1.0

    def scale(self, events):
        return walk_forward_cell_filter(events, self.TRIM)

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT, scale=self.scale(events))


@register
class InverseVolFiltered(VolCellFilter):
    name = "s9c_inverse_vol_filtered"
    description = "Inverse-vol sizing combined with the walk-forward cell filter"
    required_columns = VolCellFilter.required_columns + ("ivol_252d", "tvol_60d")

    def scale(self, events):
        return inverse_ivol_scale(events) * walk_forward_cell_filter(events, self.TRIM)
