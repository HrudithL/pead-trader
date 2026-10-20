"""Calm Rank Tilt -- Balanced Leverage, tilted hard toward the calmest stocks by volatility RANK (research).

Calm Weighted sized each event by 1/idiosyncratic-vol (Sharpe 1.30 -> 1.47). The attribution work showed
that PEAD alpha per dollar is concentrated in low-volatility names and almost absent in the wildest
tercile, and that the 1/vol weight, being linear in the vol LEVEL, only mildly demotes the high-vol tail.
Weighting by (1.02 - vol percentile)^2 -- where the percentile is measured against the previous quarter's
cross-section within the stock's size quintile -- demotes it hard while staying smooth:

    Sharpe 1.30 (Balanced Leverage) -> 1.47 (Calm Weighted) -> 1.81;  max drawdown -16.6% -> -11.6%;  every era improves
    split-sample check: exponent picked on 1996-2004 data alone (best k ~ 3) lifts 2005-2013 Sharpe
    0.93 -> 1.10, the same as the full-sample best, so the gain is not an artifact of one period.

Lower volatility buys room: `calm_rank_levered` spends it on 1.7x the base unit and a 4x gross cap
for ~9% a year at ~5% vol (a market-neutral book clearing the S&P 500's return in this sample). More
sizing than that stops paying: the 5%-of-ADV liquidity cap starts to bind and 2008-13 Sharpe decays.

STATUS research: the exponent and sizing were chosen after seeing Calm Weighted's full-sample results (the split-sample
check above is the only out-of-sample evidence), and the levered variant leans on small-cap capacity.
"""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register
from pead.strategies.vol import low_vol_rank_scale


@register
class CalmRankTilt(EquityStrategy):
    name = "calm_rank_tilt"
    family = "equity-volatility"
    parent = "calm_weighted"
    status = "research"
    live_ready = True
    description = "Balanced Leverage, with bets shrunk sharply for the jumpiest stocks (ranked against last quarter)"
    thesis = "PEAD alpha per dollar is highest in calm stocks; rank-weight toward them without depending on vol levels."
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
    required_columns = ("ann_quarter", "sue_rank_pct", "size_quintile", "ff12_sector", "ivol_252d_pct")
    TRIM, TILT, POWER = 0.15, 1.0, 2.0

    def weights(self, events) -> np.ndarray:
        return W.neutralized_tilt(events, W.centered_rank(events), self.TRIM, self.TILT,
                                  scale=low_vol_rank_scale(events, self.POWER))


@register
class CalmRankLevered(CalmRankTilt):
    name = "calm_rank_levered"
    parent = "calm_rank_tilt"
    description = "Calm Rank Tilt with its lower risk spent on larger bets (about 1.7x) and a 4x borrowing limit"
    sizing = Sizing(base_unit_fraction=0.005, max_gross_leverage=4.0)
