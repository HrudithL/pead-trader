"""Simulated fills for the options leg.

There is no free, instantly-approved paper-trading API for single-name equity options. Fills are therefore
simulated against the same live bid/ask the selector scored: buy at mid plus half the round-trip cost, sell
at mid minus half. The round-trip cost is the registered options strategy's own assumption (a percentage of
premium with an optional per-share floor), so live paper results are costed exactly like the backtest.
Known limits: no partial fills, no slippage beyond that cost, no early-exercise or assignment modeling."""


def _half_cost(mid, cost_pct, min_cost_per_share):
    return max(mid * cost_pct, min_cost_per_share) / 2.0


def simulate_open(mid_price, n_contracts, cost_pct=0.03, min_cost_per_share=0.0):
    fill_price = mid_price + _half_cost(mid_price, cost_pct, min_cost_per_share)
    return {"fill_price": fill_price, "cost": fill_price * 100 * n_contracts}


def simulate_close(mid_price, n_contracts, cost_pct=0.03, min_cost_per_share=0.0):
    fill_price = max(mid_price - _half_cost(mid_price, cost_pct, min_cost_per_share), 0.0)
    return {"fill_price": fill_price, "proceeds": fill_price * 100 * n_contracts}
