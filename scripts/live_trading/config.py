"""Config and paths for the live paper-trading engine.

Mirrors the finalized, currently-recommended backtest parameters documented in README.md:
- Equities: Strategy 6 (scripts/equity_strategy/24_finalize_strategy6.py) -- trim=0.15,
  tilt_power=1.0, size-quintile + GICS-sector neutral, 2.5x gross leverage cap, 5% ADV liquidity
  cap per name.
- Options: the optimal-contract Kelly selector at the 30%-premium-cap, 60-day-hold, half-Kelly
  operating point (data/backtest_options_optimal_cap30_h60d_summary.json) -- the one actually
  reported in README.md's options section.

Everything here is overridable by environment variable so the same code runs unmodified locally,
in CI, and on whatever always-on worker ends up hosting this.
"""
import os
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import REPO_ROOT, DATA_DIR  # noqa: E402

LIVE_DIR = Path(os.environ.get("PEAD_LIVE_DIR", DATA_DIR / "live"))
LIVE_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_CSV = LIVE_DIR / "sp500_universe.csv"

# --- state files (plain CSV/JSON, not a DB, so every run's diff is human-readable in git) -------
EARNINGS_EVENTS_CSV = LIVE_DIR / "earnings_events.csv"
EQUITY_POSITIONS_JSON = LIVE_DIR / "equity_positions.json"
OPTIONS_POSITIONS_JSON = LIVE_DIR / "options_positions.json"
NAV_HISTORY_CSV = LIVE_DIR / "nav_history.csv"
TRADE_LOG_CSV = LIVE_DIR / "trade_log.csv"
DECILE_MODEL_JSON = LIVE_DIR / "decile_return_model.json"
RUN_LOG = LIVE_DIR / "run_log.jsonl"

# --- capital --------------------------------------------------------------------------------
# Paper NAV is tracked entirely in this repo's state files, split into two independently sized
# sleeves so one strategy's sizing math never reads the other's leverage. Equities additionally
# mirror through a real Alpaca *paper* brokerage account (see broker_alpaca.py) so equity fills,
# commissions-are-zero assumptions, and NAV are cross-checked against a real paper-trading venue;
# options have no such free venue (see LIVE_TRADING.md), so they are simulated entirely here.
INITIAL_CAPITAL_TOTAL = float(os.environ.get("PEAD_LIVE_INITIAL_CAPITAL", 1_000_000.0))
EQUITY_SLEEVE_FRAC = 0.7
OPTIONS_SLEEVE_FRAC = 0.3

# --- equity strategy (Strategy 6) ------------------------------------------------------------
EQUITY_TRIM = 0.15
EQUITY_TILT_POWER = 1.0
EQUITY_MAX_GROSS_LEVERAGE = 2.5
EQUITY_LIQUIDITY_CAP_FRAC = 0.05          # per-name cap: <= 5% of 21d avg dollar volume
EQUITY_BASE_UNIT_FRACTION = 0.003
EQUITY_HOLDING_TRADING_DAYS = 60          # matches the options side's finalized 60d hold; the
                                           # backtest's per-cell decay days (data/cell_decay_days.csv)
                                           # need the WRDS (decile, size, book/mkt) cell join this
                                           # live system does not have -- see LIVE_TRADING.md.
EQUITY_COHORT_WINDOW_TRADING_DAYS = 5     # a rolling "quarter" substitute: live SUE rank is computed
                                           # cross-sectionally against the trailing N trading days of
                                           # reporters, not a fixed fiscal quarter (see LIVE_TRADING.md)
EQUITY_MIN_COHORT_SIZE = 5                # need at least this many concurrent reporters to rank

# --- options strategy (Kelly-optimal contract selector, cap30/h60d/half-Kelly) ----------------
OPTIONS_PREMIUM_CAP_FRAC = 0.30           # cap on total outstanding premium as a fraction of sleeve NAV
OPTIONS_HOLDING_TRADING_DAYS = 60
OPTIONS_KELLY_MULTIPLIER = 0.5            # half-Kelly, matching the finalized backtest
OPTIONS_ROUNDTRIP_COST_PCT = 0.03         # assumed bid/ask + fee slippage, matching the backtest
OPTIONS_KELLY_FRACTION_GRID = [round(0.05 + 0.05 * i, 2) for i in range(19)]  # 0.05..0.95
OPTIONS_MIN_OPEN_INTEREST = 10
OPTIONS_MAX_STRIKES_PER_EVENT = 30
OPTIONS_TARGET_DTE_DAYS = 60              # look for the expiry closest to a 60-calendar-day hold

# --- API keys (all optional; every data source degrades gracefully without one, see data_sources.py)
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER_BASE_URL = os.environ.get("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")  # optional cross-check for the earnings calendar

TRADING_DAYS_PER_YEAR = 252
