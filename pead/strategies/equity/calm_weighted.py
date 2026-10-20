"""Calm Weighted -- Balanced Leverage, with bigger bets on calmer stocks (sized by inverse idiosyncratic volatility).

Within every (quarter, side, sector, size) cell, each event's weight is scaled by 1/ivol, so calm
stocks get more capital than wild ones. Rationale: the earnings drift is worth more per unit of risk
in low-vol names (the SUE x vol interaction is t = -2.4 to -2.7 in Fama-MacBeth), and the engine's
cost/liquidity model charges the same bps regardless of vol. Raises Sharpe with no fitted
parameter, in all three sub-eras (corrected run: Balanced Leverage 1.09 -> Calm Weighted 1.24; pre-fix 1.30 -> 1.47).

`CalmWeightedMatched` re-sizes it to Balanced Leverage's exact volatility to answer "at the SAME risk, how
much more return" (corrected run: 5.83% vs 5.38%; pre-fix 7.31% vs 6.49%)."""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register
from pead.strategies.vol import inverse_ivol_scale


@register
class CalmWeighted(EquityStrategy):
    name = "calm_weighted"
    family = "equity-volatility"
    parent = "balanced_leverage"
    description = "Balanced Leverage, with each bet divided by how jumpy the stock is"
    thesis = "PEAD is a stronger, cleaner signal in low-vol stocks; size positions by inverse risk."
    live_ready = True
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
    required_columns = ("ann_quarter", "sue_rank_pct", "size_quintile", "ff12_sector", "ivol_252d", "tvol_60d")
    TRIM, TILT = 0.15, 1.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT,
                                  scale=inverse_ivol_scale(events))


@register
class CalmWeightedMatched(CalmWeighted):
    name = "calm_weighted_matched"
    parent = "calm_weighted"
    description = "Calm Weighted resized so its ups and downs match Balanced Leverage closely (sized on the pre-fix book; same volatility to within 0.1 point after the fixes)"
    sizing = Sizing(base_unit_fraction=0.003468808347716693, max_gross_leverage=2.5)
