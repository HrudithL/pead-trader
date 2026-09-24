# Live paper trading: running Strategy 6 (equities) and the optimal-contract Kelly
# selector (options) on free, real-time data

This document explains `scripts/live_trading/`: a system that watches for real earnings-surprise
events, sizes positions the same way the backtested Strategy 6 (equities) and the
cap30/h60d/half-Kelly optimal-contract selector (options) do, and paper-trades them autonomously
on a daily schedule -- entirely on free data, with no brokerage account required to get started.

## Why this can't just replay the backtest live

The backtests in `scripts/equity_strategy/` and `scripts/options_strategy/` are built on WRDS
(CRSP/Compustat/IBES) and OptionMetrics IvyDB data: point-in-time analyst estimate detail, delisting-
adjusted returns, official GICS/FF12 sector codes, and a full historical options chain -- all
licensed, non-free, and explicitly **not redistributed in this repo** (see `.gitignore` and
`GPU_SETUP.md`). A live system has to get equivalent signal from data that's actually free and
available in real time. Every approximation below is a deliberate, documented substitution for a
specific backtest input, not a simplification for its own sake.

| Backtest input | Live substitute | Where |
|---|---|---|
| IBES consensus EPS estimate + actual, point-in-time | yfinance `get_earnings_dates()`: EPS estimate, reported EPS, surprise % | `data_sources.get_earnings_surprise` |
| SUE = surprise standardized by std of *prior dollar surprises* | surprise % standardized by std of that ticker's own trailing (up to 8) surprise %, floor 2pp | `data_sources.get_earnings_surprise` |
| Fixed fiscal-quarter cohort for cross-sectional SUE rank | rolling trailing-window cohort (default 5 trading days of reporters) | `equity_signal`, `config.EQUITY_COHORT_WINDOW_TRADING_DAYS` |
| CRSP delisting-adjusted daily return panel | yfinance daily price history / `fast_info` | `data_sources.get_price_stats` |
| FF12 sector | GICS sector (S&P 500 constituent table, bundled at `data/live/sp500_universe.csv`) | `data_sources.load_universe` |
| Per-(decile x size x book/mkt) empirical holding period (`data/cell_decay_days.csv`) | fixed 60-trading-day hold for both legs, matching the options side's own finalized 60d operating point | `config.EQUITY_HOLDING_TRADING_DAYS`, `OPTIONS_HOLDING_TRADING_DAYS` |
| OptionMetrics historical chain + `decile_return_distributions.parquet` (not redistributed) | live yfinance option chain (nearest expiry to 60 DTE) scored against a **self-updating** decile return model that starts from a conservative prior and learns from this system's own realized trades | `data_sources.get_option_chain_candidates`, `decile_model.py` |
| GPU-batched Kelly search over ~126k historical events | the same E[log(1+f\*R)] objective, evaluated per-event (live volume is a handful of events/day) | `options_signal.py` |

**The trading universe is the S&P 500** (`data/live/sp500_universe.csv`, refreshed occasionally
from a public GICS-sector table), not the backtest's full CRSP universe -- a deliberately liquid,
easy-to-borrow-and-short subset so the live system never has to reason about a name it can't
actually paper-trade cleanly.

## Architecture

```
scripts/live_trading/
  config.py            constants -- mirrors README's currently-recommended params exactly
  data_sources.py       all live data: yfinance (earnings, prices, option chains), optional Finnhub cross-check
  decile_model.py       self-updating decile-conditioned return model backing the options Kelly selector
  equity_signal.py      Strategy 6's trim/tilt/size+sector-neutral weight construction, live cohort
  options_signal.py     Kelly-optimal contract selection (E[log(1+f*R)]) + half-Kelly sizing + premium cap
  broker_alpaca.py       equity leg: real orders against a free Alpaca *paper* brokerage account
  broker_options_sim.py  options leg: simulated fills against live option-chain quotes (see below)
  ledger.py              plain CSV/JSON state store (data/live/*)
  run_daily.py            orchestrator: scan -> equity trades -> options trades -> mark-to-market/exits
```

One invocation of `run_daily.py` is the whole daily cycle:

1. **Scan** every S&P 500 ticker for a newly-reported earnings surprise since the last time we
   looked at it; append each to the rolling cohort (`data/live/earnings_events.csv`).
2. **Equity trades**: once the rolling cohort has enough names (`EQUITY_MIN_COHORT_SIZE`), rank by
   SUE-z, trim/tilt/size/sector-neutralize exactly like Strategy 6, apply the 5%-ADV liquidity cap
   and 2.5x gross leverage cap, and open any new positions via a real Alpaca paper order.
3. **Options trades**: for each of *today's* reports that land in the extreme SUE deciles (1, 2, 9,
   10 -- matching Strategy 6's own trim), pull the live option chain nearest a 60-day expiry, run
   the half-Kelly optimal-contract selector, enforce the 30%-of-sleeve-NAV outstanding-premium cap,
   and simulate a fill.
4. **Mark-to-market**: reprice every open position, close anything that has reached its 60-trading-
   day holding horizon (equities via a real Alpaca sell order; options via a simulated close, which
   also feeds the realized outcome back into the decile model), and append today's NAV row.

## Capital and risk

Paper NAV starts at `config.INITIAL_CAPITAL_TOTAL` (default $1,000,000, override with
`PEAD_LIVE_INITIAL_CAPITAL`) split into two independently sized sleeves -- 70% equities, 30%
options -- so one strategy's sizing math never reads the other's leverage or premium usage. NAV is
computed each run as `sleeve allocation + realized P&L + unrealized P&L`, matching the backtest's
own NAV-percentage convention rather than doing literal share-count/cash-balance bookkeeping.

## Why equities get a real paper brokerage and options don't

**Equities** trade through a free [Alpaca](https://alpaca.markets) *paper* account
(`broker_alpaca.py`) -- instant approval, a real simulated-fill engine against live market data, and
an independent NAV/positions view this system's own ledger can be sanity-checked against. Set
`ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (paper keys, from the Alpaca dashboard) to enable it; without
them, orders no-op but the internal ledger still runs, so local development needs no brokerage
account at all.

**Options** have no equivalent free, instant-approval paper-trading API for single-name equity
options (Alpaca's own options paper trading requires a separate approval tier that isn't guaranteed
available immediately). Rather than block the whole system on that, `broker_options_sim.py`
simulates fills directly against the same live bid/ask chain the selector scored, net of an assumed
round-trip cost (`OPTIONS_ROUNDTRIP_COST_PCT = 0.03`, matching the backtest's own cost assumption).
This is real market prices with simulated execution against them -- the same thing "paper trading"
means for the equity leg, just without a brokerage in the loop marking it. Known limitations: no
partial fills, no slippage beyond the flat cost assumption, no assignment/early-exercise modeling.

## The decile return model is not a copy of the backtest's distribution

`decile_model.py` is the one piece worth reading closely before trusting its output. The backtest's
Kelly selector scores candidate contracts against an empirical distribution built from years of
WRDS/OptionMetrics history -- data this live system does not have and cannot legally redistribute.
Rather than fabricate a static number and call it done, the live decile model starts from a
deliberately conservative Gaussian prior (±3% drift at the SUE extremes, decaying to 0 at the
middle -- an order of magnitude smaller than the backtest's own headline PEAD effect sizes) and
updates itself, per decile, from this system's own realized, live-settled option outcomes, with a
shrinking-but-never-zero learning weight (`1/(n_obs+5)`) so it keeps adapting indefinitely. Read
`data/live/decile_return_model.json` at any time to see exactly what it currently believes and how
many live observations back each decile. **Until it has accumulated real observations (which, at
60-day holds, takes months), its edge estimate is a conservative guess, not a validated backtest
result** -- size expectations accordingly, and treat early live Sharpe/return numbers as noise.

## Running it

```bash
pip install -r requirements.txt -r requirements-live.txt

# exercise the whole pipeline against synthetic data, no network calls, no state written:
python -m scripts.live_trading.run_daily --mock-data --dry-run

# a real run against live data, writing to data/live/ (no brokerage needed -- Alpaca orders no-op
# without ALPACA_API_KEY/ALPACA_SECRET_KEY set, and the internal ledger still updates):
python -m scripts.live_trading.run_daily
```

`--dry-run` runs the full pipeline against real data without writing any ledger state or submitting
any orders -- useful for checking today's real data flows through cleanly before trusting it with
state. `--mock-data` additionally replaces every network call with synthetic data, for offline
development or CI.

## Cloud deployment: GitHub Actions, not a standing server

`.github/workflows/live_paper_trading.yml` runs `run_daily.py` once per US trading day at 12:45 UTC
(shortly before the 9:30am ET open, after both yesterday's after-close and today's before-open
reporters have posted actuals) on GitHub Actions' free tier, and commits the updated
`data/live/*.csv`/`*.json` state back to the repo at the end of each run. This was chosen over a
standing worker/VM deliberately:

- **Free and zero-maintenance** -- no server to patch, pay for, or keep alive; GitHub Actions'
  free-tier minutes comfortably cover one ~5-minute job/weekday.
- **State lives in git, not on a disk that can disappear** -- every day's positions/NAV/trade log
  is a normal commit, inspectable with `git log`/`git diff` like any other change, and trivially
  recoverable if a run ever needs to be re-run or rolled back.
- **Matches the strategy's own cadence** -- both legs hold for ~60 trading days; a daily,
  pre-market decision cycle loses nothing by not running intraday, and GitHub's cron is
  documented as best-effort (a few minutes of slack around the scheduled time), which is
  irrelevant at this holding-period scale.

Configure `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, and (optionally) `FINNHUB_API_KEY` as repository
secrets to enable real Alpaca paper fills and the Finnhub cross-check; without them the workflow
still runs end-to-end against the internal ledger. If this ever needs to run more than once a day
or with tighter timing, the same `run_daily.py` runs unmodified on any always-on host (a small
Fly.io/Render free-tier container, a cron entry on an existing box) -- nothing here is
GitHub-Actions-specific except the state-persistence-via-commit step.

## Honest limitations

- **yfinance is unofficial and can silently change shape or rate-limit** -- every fetch in
  `data_sources.py` is wrapped in a retry-with-backoff and a broad `except`, and a single ticker's
  bad data never aborts the run, but this is not a paid, SLA-backed feed. If it becomes a
  bottleneck, `finnhub_earnings_today()` is already wired up as an optional cross-check and could
  be extended into the primary earnings-calendar source (Finnhub's free tier has real rate limits
  worth checking before relying on it exclusively).
- **The rolling live cohort is not the same statistical object as a fiscal-quarter cohort** --
  cross-sectional SUE ranks computed over a trailing 5-trading-day window are noisier (fewer names,
  more sensitive to which specific days happen to cluster reports) than the backtest's full-quarter
  cohorts. `EQUITY_COHORT_WINDOW_TRADING_DAYS` and `EQUITY_MIN_COHORT_SIZE` are the two knobs to
  revisit if live equity trade frequency looks too sparse or too noisy once real data accumulates.
- **The options decile model's cold-start prior is a guess**, clearly labeled as such in
  `decile_model.py` and this document -- do not read early live options P&L as validating (or
  invalidating) the backtest's own historical Sharpe until the model has real observations behind it.
- **No transaction-cost/market-impact modeling beyond the flat assumptions already in
  `config.py`** (options: 3% round-trip; equities: none explicitly modeled beyond Alpaca's own
  zero-commission paper fills) -- reasonable for paper trading, not sufficient evidence for sizing
  real capital without a further live-cost study.
