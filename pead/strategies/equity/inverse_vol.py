"""Strategy 9b -- Strategy 6 sized by inverse idiosyncratic volatility.

Within every (quarter, side, sector, size) cell, each event's weight is scaled by 1/ivol, so calm
stocks get more capital than wild ones. Rationale: the earnings drift is worth more per unit of risk
in low-vol names (the SUE x vol interaction is t = -2.4 to -2.7 in Fama-MacBeth), and the engine's
cost/liquidity model charges the same bps regardless of vol. Raises Sharpe 1.30 -> 1.47 with no fitted
parameter, in all three sub-eras.

`InverseVolVolMatched` re-sizes it to Strategy 6's exact volatility to answer "at the SAME risk, how
much more return": 7.31% vs 6.49%."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register
from pead.strategies.vol import inverse_ivol_scale


@register
class InverseVol(EquityStrategy):
    name = "s9b_inverse_vol"
    family = "equity-volatility"
    parent = "s6_neutral_levered"
    description = "Strategy 6 weights x 1/ivol_252d within each neutralization cell"
    thesis = "PEAD is a stronger, cleaner signal in low-vol stocks; size positions by inverse risk."
    live_ready = True
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
    required_columns = ("ann_quarter", "sue_rank_pct", "size_quintile", "ff12_sector", "ivol_252d", "tvol_60d")
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT,
                                  scale=inverse_ivol_scale(events))


@register
class InverseVolVolMatched(InverseVol):
    name = "s9b_inverse_vol_matched"
    parent = "s9b_inverse_vol"
    description = "9b with base unit solved so annualized vol equals Strategy 6's (4.94%)"
    sizing = Sizing(base_unit_fraction=0.003468808347716693, max_gross_leverage=2.5)
