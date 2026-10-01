"""Strategy 6 + 0.5x market beta overlay.

Keeps Strategy 6's alpha book completely untouched and holds a separate synthetic market-index
position of 0.5x trailing NAV beside it (re-sized quarterly, 2bps cost) -- the 130/30-style
long-extension structure real funds use to add market exposure without distorting the alpha signal.
This is how the equity side clears the S&P 500's return in this sample."""
from pead.strategies.equity.neutral_levered import NeutralLevered
from pead.strategies.registry import register


@register
class BetaOverlay(NeutralLevered):
    name = "s6_beta_overlay"
    family = "equity-overlay"
    parent = "s6_neutral_levered"
    description = "Strategy 6 + synthetic market position of 0.5x trailing NAV"
    thesis = "Alpha book earns the drift; the overlay earns the equity risk premium on the same capital."
    live_ready = False
    beta_overlay = 0.5
