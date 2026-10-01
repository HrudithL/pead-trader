"""Live paper-trading configuration.

The strategies themselves are NOT configured here -- they are the registered strategy objects, so the
live engine trades exactly what the backtest ran. This file only says which registered strategies to
run, how much capital to give each, and the few live-only mechanics (cohort window, state files).

    PEAD_LIVE_EQUITY    registered equity strategy name   (default s9b_inverse_vol)
    PEAD_LIVE_OPTIONS   registered options strategy name   (default opt_kelly_cap20)
    PEAD_LIVE_BLEND     portfolio strategy whose sleeve multiples set the capital split (default hedged_blend)
"""
import os
from pathlib import Path

from pead.common.paths import DATA_DIR

LIVE_DIR = Path(os.environ.get("PEAD_LIVE_DIR", DATA_DIR / "live"))
LIVE_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_CSV = LIVE_DIR / "sp500_universe.csv"
HOLDING_DAYS_CSV = LIVE_DIR / "holding_days.csv"        # median backtest holding window by (decile, size quintile)

EARNINGS_EVENTS_CSV = LIVE_DIR / "earnings_events.csv"
EQUITY_POSITIONS_JSON = LIVE_DIR / "equity_positions.json"
OPTIONS_POSITIONS_JSON = LIVE_DIR / "options_positions.json"
NAV_HISTORY_CSV = LIVE_DIR / "nav_history.csv"
TRADE_LOG_CSV = LIVE_DIR / "trade_log.csv"
DECILE_MODEL_JSON = LIVE_DIR / "decile_return_model.json"
RUN_LOG = LIVE_DIR / "run_log.jsonl"

EQUITY_STRATEGY = os.environ.get("PEAD_LIVE_EQUITY", "s9b_inverse_vol")
OPTIONS_STRATEGY = os.environ.get("PEAD_LIVE_OPTIONS", "opt_kelly_cap20")
BLEND_STRATEGY = os.environ.get("PEAD_LIVE_BLEND", "hedged_blend")

INITIAL_CAPITAL = float(os.environ.get("PEAD_LIVE_INITIAL_CAPITAL", 1_000_000.0))

# The universe is the S&P 500: a liquid, borrowable subset of what the backtest traded. A rolling window
# of recent reporters replaces the backtest's fiscal-quarter cohort for cross-sectional ranking.
COHORT_WINDOW_TRADING_DAYS = 5
MIN_COHORT_SIZE = 8
DEFAULT_HOLDING_TRADING_DAYS = 60
CALENDAR_PER_TRADING_DAY = 1.45

OPTIONS_TARGET_DTE_DAYS = 60
OPTIONS_MIN_OPEN_INTEREST = 10
OPTIONS_EXPECTED_CONCURRENT_POSITIONS = 200   # pace the premium budget across ~this many open trades
OPTIONS_MEAN_HALF_KELLY = 0.04                # typical half-Kelly fraction in the backtest picks

# GICS sector -> Fama-French 12 industry group (approximate: GICS and FF12 are cut differently)
GICS_TO_FF12 = {
    "Information Technology": "BusEq", "Health Care": "Hlth", "Financials": "Money", "Energy": "Enrgy",
    "Utilities": "Utils", "Communication Services": "Telcm", "Consumer Staples": "NoDur",
    "Consumer Discretionary": "Shops", "Industrials": "Manuf", "Materials": "Chems", "Real Estate": "Other",
}

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
