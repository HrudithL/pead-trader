"""Internal simulated fills for the options leg.

There is no free, instantly-approved paper-trading API for single-name equity options the way
Alpaca provides for equities (Alpaca's own options paper trading requires an options-trading
agreement/approval tier that isn't guaranteed to be instant or available in every account). Rather
than block the whole live system on that, options fills are simulated here using the same live
bid/ask chain quotes the selector scored (mid price, minus config.OPTIONS_ROUNDTRIP_COST_PCT to
account for crossing the spread and any fees) -- i.e. real market prices, simulated execution
against them, which is what "paper trading" means for the equity leg too, just without a
brokerage in the loop marking it. See LIVE_TRADING.md for the honest limitations this implies
(no partial fills, no slippage beyond the flat cost assumption, no assignment/early-exercise
modeling).
"""
from . import config


def simulate_open(mid_price, n_contracts, side):
    """side: 'buy' (long the option) -- this strategy is always long calls/puts, never short."""
    fill_price = mid_price * (1 + config.OPTIONS_ROUNDTRIP_COST_PCT / 2)
    cost = fill_price * 100 * n_contracts
    return {"fill_price": fill_price, "cost": cost}


def simulate_close(mid_price, n_contracts):
    fill_price = mid_price * (1 - config.OPTIONS_ROUNDTRIP_COST_PCT / 2)
    proceeds = max(fill_price, 0.0) * 100 * n_contracts
    return {"fill_price": fill_price, "proceeds": proceeds}
