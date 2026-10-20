"""Market Boost -- Balanced Leverage plus a half-size position in the overall stock market.

Keeps Balanced Leverage's book completely untouched and holds a separate synthetic market-index
position of 0.5x trailing NAV beside it (re-sized quarterly, 2bps cost) -- the 130/30-style
long-extension structure real funds use to add market exposure without distorting the alpha signal.
This is how the equity side clears the S&P 500's return in this sample."""
from pead.strategies.equity.balanced_leverage import BalancedLeverage
from pead.strategies.registry import register


@register
class MarketBoost(BalancedLeverage):
    name = "market_boost"
    family = "equity-overlay"
    parent = "balanced_leverage"
    description = "Balanced Leverage plus a market position worth half the account"
    thesis = "Alpha book earns the drift; the overlay earns the equity risk premium on the same capital."
    live_ready = False
    beta_overlay = 0.5
