"""Aggressive-sizing variants of the hand-built equity strategies.

Same signal, trim, tilt and neutralization as each base strategy; only the two dials that set how big
and how levered the book is change (base_unit_fraction, gross-leverage cap = 6x). The multiplier for
each was picked by sweeping 1.5x..12x the original base unit and taking the highest return subject to
Sharpe >= 0.6 and max drawdown >= -40% (a worse drawdown is a fund-ending event, not a risk
preference). The untouched 5%-of-ADV liquidity cap remains the real capacity constraint, and bites
roughly four times as often at this sizing.

These are generated from the base strategy classes, so a change to a base strategy's weights flows
through automatically."""
from dataclasses import replace

from pead.engines.equity import Sizing
from pead.strategies.base import EquityStrategy
from pead.strategies.equity.beta_overlay import BetaOverlay
from pead.strategies.equity.directional_netexp import DirectionalNetExposure
from pead.strategies.equity.extreme_decile import ExtremeDecile
from pead.strategies.equity.neutral_levered import NeutralLevered
from pead.strategies.equity.rank_weighted import RankWeighted
from pead.strategies.equity.sector_neutral import SectorNeutral
from pead.strategies.equity.size_balanced import SizeBalanced
from pead.strategies.equity.tilted_size_neutral import TiltedSizeNeutral
from pead.strategies.registry import register, register_instance

LEVERAGE_CEILING = 6.0
# (base class, base_unit_fraction picked by the sizing sweep)
AGGRESSIVE = {
    "extreme_decile": (ExtremeDecile, 0.009),
    "rank_weighted": (RankWeighted, 0.005),
    "size_balanced": (SizeBalanced, 0.009),
    "s4_tilted_size_neutral": (TiltedSizeNeutral, 0.007),
    "s5_sector_neutral": (SectorNeutral, 0.012),
    "s6_neutral_levered": (NeutralLevered, 0.012),
    "s7_directional": (DirectionalNetExposure, 0.006),
}


def _make(base_name, base_cls, buf):
    class Aggressive(base_cls):
        name = f"{base_name}_aggressive"
        parent = base_name
        family = "equity-aggressive"
        status = "research"
        description = f"{base_name} re-sized for return: base unit {buf}, {LEVERAGE_CEILING:g}x leverage cap"
        thesis = base_cls.thesis
        live_ready = False
        sizing = replace(base_cls.sizing, base_unit_fraction=buf, max_gross_leverage=LEVERAGE_CEILING)
        beta_overlay = 0.0
    Aggressive.__name__ = f"{base_cls.__name__}Aggressive"
    return Aggressive


for _name, (_cls, _buf) in AGGRESSIVE.items():
    register_instance(_make(_name, _cls, _buf)())


@register
class BetaOverlayAggressive(_make("s6_neutral_levered", NeutralLevered, 0.012)):
    name = "s6_beta_overlay_aggressive"
    parent = "s6_neutral_levered_aggressive"
    description = "Strategy 6 Aggressive alpha book + the same 0.5x market overlay"
    beta_overlay = 0.5
