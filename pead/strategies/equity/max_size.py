"""Max-size variants ("<name>_max") of the hand-built equity strategies.

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
from pead.strategies.equity.market_boost import MarketBoost
from pead.strategies.equity.market_exposed import MarketExposed
from pead.strategies.equity.extreme_picks import ExtremePicks
from pead.strategies.equity.balanced_leverage import BalancedLeverage
from pead.strategies.equity.graded_bets import GradedBets
from pead.strategies.equity.sector_balanced import SectorBalanced
from pead.strategies.equity.size_balanced import SizeBalanced
from pead.strategies.equity.conviction_tilt import ConvictionTilt
from pead.strategies.registry import register, register_instance

LEVERAGE_CEILING = 6.0
# (base class, base_unit_fraction picked by the sizing sweep)
AGGRESSIVE = {
    "extreme_picks": (ExtremePicks, 0.009),
    "graded_bets": (GradedBets, 0.005),
    "size_balanced": (SizeBalanced, 0.009),
    "conviction_tilt": (ConvictionTilt, 0.007),
    "sector_balanced": (SectorBalanced, 0.012),
    "balanced_leverage": (BalancedLeverage, 0.012),
    "market_exposed": (MarketExposed, 0.006),
}


def _make(base_name, base_cls, buf):
    class Max(base_cls):
        name = f"{base_name}_max"
        parent = base_name
        family = "equity-max"
        status = "research"
        description = f"{base_name} with bigger bets for more return: base unit {buf}, {LEVERAGE_CEILING:g}x leverage cap"
        thesis = base_cls.thesis
        live_ready = False
        sizing = replace(base_cls.sizing, base_unit_fraction=buf, max_gross_leverage=LEVERAGE_CEILING)
        beta_overlay = 0.0
    Max.__name__ = f"{base_cls.__name__}Max"
    return Max


for _name, (_cls, _buf) in AGGRESSIVE.items():
    register_instance(_make(_name, _cls, _buf)())


@register
class MarketBoostMax(_make("balanced_leverage", BalancedLeverage, 0.012)):
    name = "market_boost_max"
    parent = "balanced_leverage_max"
    description = "Balanced Leverage Max plus the same half-size market position"
    beta_overlay = 0.5
