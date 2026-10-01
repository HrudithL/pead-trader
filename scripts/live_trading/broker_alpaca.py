"""Equity execution via Alpaca's free paper-trading brokerage.

Why a real (paper) brokerage for the equity leg and not just an internal ledger like the options
leg (broker_options_sim.py): Alpaca's paper account gives real simulated fills against its live
market-data feed, a real commission/fee model (zero, but modeled explicitly rather than assumed),
and an independent NAV/positions view we can reconcile our own ledger against -- catching bugs in
our own sizing math that a self-marked internal ledger would never surface. Alpaca approves paper
accounts instantly and its free tier has no meaningful rate limit for this system's low order
volume (a handful of names a day). If ALPACA_API_KEY/SECRET_KEY aren't set, every function here
returns a clearly-labeled "no-op" result so the rest of the pipeline (and local dev / tests) still
runs without a brokerage account configured.
"""
from . import config

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
except ImportError:  # pragma: no cover
    TradingClient = None


def _client():
    if TradingClient is None or not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        return None
    return TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=True)


def is_configured():
    return _client() is not None


def get_account_nav():
    c = _client()
    if c is None:
        return None
    acct = c.get_account()
    return float(acct.equity)


def submit_market_order(ticker, side, qty):
    """side: 'buy' or 'sell'. Returns a dict describing the order (submitted or simulated)."""
    if qty <= 0:
        return {"status": "skipped", "reason": "zero_qty"}
    c = _client()
    if c is None:
        return {"status": "no_broker_configured", "ticker": ticker, "side": side, "qty": qty}
    order = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    result = c.submit_order(order)
    return {"status": "submitted", "order_id": str(result.id), "ticker": ticker, "side": side, "qty": qty}


def get_positions():
    c = _client()
    if c is None:
        return {}
    positions = c.get_all_positions()
    return {p.symbol: float(p.qty) for p in positions}
