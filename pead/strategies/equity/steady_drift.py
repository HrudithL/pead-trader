"""Steady Drift -- a long-only, never-borrowing PEAD book with a constant-risk throttle (research).

What it does
  * Buys only the best earnings surprises (top decile of the point-in-time SUE rank) in stocks that trade at least
    $2M a day, holds each for exactly 60 trading sessions, and never sells short.
  * Sizes each position by how calm the stock is (inverse idiosyncratic variance, relative to the typical stock of
    the past year), at most 2% of NAV per name and 5% of the stock's daily dollar volume. Positions are admitted in
    announcement order, so no decision depends on events that have not happened yet.
  * Total stock cost basis is capped at 70% of NAV; the rest is cash earning the Treasury-bill rate (not the index).
  * A risk throttle scales the whole book down when its recent volatility exceeds the target (never above 100%
    invested, so it can only reduce risk, never borrow).

Why: after the methodology fixes, every market-neutral book collapses to 1-3% a year once short collateral is counted (they tie
up 1.2-5.6x NAV), and the options edge disappears at real bid/ask. What survives is the long side of the surprise
signal; docs/STEADY_DRIFT.md and docs/META_STRATEGY.md carry the evidence and the shuffled-surprise placebo.

STATUS research: the design was chosen after seeing 1996-2013; a shuffled-surprise test shows about half of the
excess return over the cap-weighted index is the event universe (small/mid-cap, low-vol), not surprise skill; the
T-bill series is typed from memory (pead/core/rates.py).
"""
import numpy as np

from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register

TOP_DECILE = 0.9
MIN_DOLLAR_VOLUME = 2e6


@register
class SteadyDrift(EquityStrategy):
    name = "steady_drift"
    family = "equity-longonly"
    parent = "calm_weighted"
    status = "research"
    live_ready = False
    legacy_twin = False                    # born after the fixes: nothing pre-fix to reproduce
    description = "Long-only best surprises, calm stocks sized up, idle money in T-bills, risk throttled to ~10% volatility"
    thesis = ("The paid leg of PEAD is the long side of the top surprise decile; size by calmness, never borrow, and keep "
              "book risk roughly constant so the edge is not swamped by market drawdowns.")
    # base_unit_fraction = NAV share of a typical-risk position; name_cap_frac caps any one name; the 0.70 cap bounds the
    # stock cost basis (the rest is cash), and 'chronological' admits first come first served.
    sizing = Sizing(base_unit_fraction=0.004, max_gross_leverage=0.70, liquidity_cap_frac=0.05, return_basis="raw",
                    cap_mode="chronological", exposure_cap="open_book", name_cap_frac=0.02)
    required_columns = ("day0_date", "sue_rank_pct", "avg_dollar_volume_21d", "ivol_252d")
    hold_sessions = 60
    long_only_cash = True
    risk_target_vol = 0.10
    top_rank = TOP_DECILE                  # act on events whose point-in-time SUE rank is at least this
    min_dollar_volume = MIN_DOLLAR_VOLUME

    def weights(self, events) -> np.ndarray:
        adv = events["avg_dollar_volume_21d"].astype(float).to_numpy()
        mask = (events["sue_rank_pct"].to_numpy() >= self.top_rank) & (np.nan_to_num(adv, nan=0.0) >= self.min_dollar_volume)
        return W.causal_inverse_variance(events, mask)


@register
class SteadyDriftCalm(SteadyDrift):
    name = "steady_drift_calm"
    parent = "steady_drift"
    description = "Steady Drift with the risk throttle at 8% volatility (smaller drawdowns, a little less return)"
    risk_target_vol = 0.08


@register
class SteadyDriftCore(SteadyDrift):
    name = "steady_drift_core"
    parent = "steady_drift"
    description = "Steady Drift without the risk throttle (same stocks, cash-filled, about 12% volatility)"
    risk_target_vol = None


@register
class SteadyDriftPlus(SteadyDrift):
    name = "steady_drift_plus"
    parent = "steady_drift"
    description = "Steady Drift with smaller, more numerous positions, 80% invested cap and a 12% risk target (more return, same or better Sharpe)"
    thesis = ("Breadth lowers the 5%-of-volume cap's bite (56% of positions capped -> 39%) and diversifies, so the same signal "
              "supports a higher risk target without a lower Sharpe.")
    # chosen from a 31-variant pre-registered grid (meta/steady_grid.py, steady_grid2.py) by rule: highest return subject to
    # Sharpe >= baseline, alpha t >= 5.5, 2008-13 Sharpe >= 0.75 and marked holdings <= 95% of NAV. Still in-sample.
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=0.80, liquidity_cap_frac=0.05, return_basis="raw",
                    cap_mode="chronological", exposure_cap="open_book", name_cap_frac=0.01)
    risk_target_vol = 0.12
