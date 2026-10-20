"""Order execution for paper trading.

`Broker` is the interface the engine talks to. `AlpacaBroker` routes equity orders to a free Alpaca PAPER
account (real simulated fills against a live feed, and an independent NAV to reconcile our own ledger
against); `LedgerBroker` records the intent only, so the engine runs with no brokerage configured (CI,
local dev). Single-name equity options have no free instant-approval paper venue, so option fills are
always simulated against the live quote by `broker_options_sim` (real prices, simulated execution)."""
from . import broker_alpaca


class Broker:
    name = "base"

    def submit(self, ticker, side, qty):
        raise NotImplementedError

    def account_nav(self):
        return None


class LedgerBroker(Broker):
    name = "ledger-only"

    def submit(self, ticker, side, qty):
        return {"status": "no_broker_configured", "ticker": ticker, "side": side, "qty": qty}


class AlpacaBroker(Broker):
    name = "alpaca-paper"

    def submit(self, ticker, side, qty):
        return broker_alpaca.submit_market_order(ticker, side, qty)

    def account_nav(self):
        return broker_alpaca.get_account_nav()


def default_broker() -> Broker:
    return AlpacaBroker() if broker_alpaca.is_configured() else LedgerBroker()
