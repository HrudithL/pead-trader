# Live paper trading

`pead/live/` paper-trades the *registered strategies* on free, real-time data. It was rebuilt from the first
implementation (a separate re-coding of Strategy 6 and a hand-written options rule) so that **live trades exactly
what the backtest runs**: the equity weights come from `EquityStrategy.weights`, position sizing from the same
`cap_notionals` function the backtest engine uses, contract choice from `pead/core/kelly.py`, and the option cost
model from the options strategy's own `OptionsSizing`. Change `PEAD_LIVE_EQUITY` / `PEAD_LIVE_OPTIONS` and a different
registered strategy trades, with no live-side code change.

```bash
pip install -r requirements.txt -r requirements-live.txt
python -m pead live run --mock-data --dry-run     # whole cycle on synthetic data: no network, writes nothing
python -m pead live run --dry-run                 # real data, no state written, no orders sent
python -m pead live run                           # a real daily cycle; state in data/live/
python -m pead live status
```

Defaults: equity `calm_weighted`, options `options_cap20`, capital split from the `hedged_blend` portfolio strategy
(0.8x equity sizing, 1.6x options premium budget, on one NAV; `PEAD_LIVE_INITIAL_CAPITAL`, default $1M).
`.github/workflows/live_paper_trading.yml` runs one cycle per trading day and commits the state files back.

## The daily cycle (`pead/live/engine.py`)

1. **Scan** every S&P 500 ticker for a newly reported earnings surprise (yfinance), standardize it into a SUE-like z-score, attach
   price, market cap, 21-day dollar volume and trailing volatility, and append it to the rolling cohort (`data/live/earnings_events.csv`).
2. **Equity.** `features.build_events` turns the cohort into the backtest's event schema; `strategy.weights(events)` produces weights;
   they are converted to dollars, capped (5% of ADV per name, gross-leverage room), and sent to the broker.
3. **Options.** For each of today's reporters, fetch the live chain (nearest 60 days), score every contract with the Kelly objective against the
   walk-forward return distribution, and buy the best if its expected log growth is positive, sized by half-Kelly against the premium budget.
4. **Mark.** Re-price every open position; close those past their holding window; append the NAV row.

## What replaces each backtest input

| Backtest input | Live substitute |
|---|---|
| IBES SUE | yfinance EPS surprise %, standardized by that ticker's own trailing (up to 8) surprises, floor 2pp |
| Fiscal-quarter cohort for ranking and neutralization | rolling window of the last 5 trading days of reporters (`COHORT_WINDOW_TRADING_DAYS`) |
| CRSP size and liquidity quintiles | rank of market cap / 21-day dollar volume within the cohort |
| FF12 sectors | GICS sector mapped to the nearest FF12 group (`config.GICS_TO_FF12`) |
| Per-cell holding windows | median backtest holding window by (decile, size quintile): `data/live/holding_days.csv` |
| Prior-quarter volatility percentile (Strategies 9/10) | percentile vs. the previous window's reporters of the same size quintile |
| Trailing volatility from CRSP | yfinance daily returns vs. SPY (`data_sources.get_vol_stats`) |
| Walk-forward return distribution (OptionMetrics-derived, licensed) | the same file if present locally (`data/metadata/decile_return_distributions.parquet`); otherwise a conservative Gaussian per decile that updates from this system's settled live trades |
| OptionMetrics quotes | yfinance option chains (mid), simulated fills at mid +/- half the strategy's cost |

## Sizing the book for the S&P 500

The backtest's `base_unit_fraction` is calibrated to ~1,400 concurrently open names (the whole CRSP universe); the S&P 500 produces about a seventh
as many reporters, so the same fraction would leave the book nearly empty. The live engine keeps the backtest's *steady-state gross exposure*
instead (`LiveEngine._unit_dollars`): one unit of weight is sized so the expected number of open positions at mean |weight| 0.5 sums to the backtest's
gross/NAV. Gross room (strategy cap x multiple x NAV, minus what is open) bounds each day's new entries.

## Execution

Equities go through a free Alpaca *paper* account (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`); without keys a `LedgerBroker` records the intent and the
internal ledger still runs, so local development needs no brokerage. Single-name equity options have no free instant-approval paper venue, so option
fills are simulated against the live quote (`broker_options_sim`). Open options are re-quoted on their **own expiry's** chain; a contract absent from it
(no bid, no open interest) is marked at zero, and an expired one at intrinsic value -- the opposite of the backtest convention that was found to
overstate the options results (STRATEGIES.md section 4.3).

## Honest limits

* The S&P 500 universe has much lower alpha per dollar than the full CRSP universe the backtests trade (largest size quintile: 28 bp/$ vs 203 in the smallest).
  Live results will be lower than backtested ones even if everything works.
* Until the decile model accumulates live observations (60-day holds: months), the options edge estimate is a prior or the backtest's own distribution --
  not evidence of a live edge. Treat early live Sharpe as noise.
* No short-borrow modeling for paper equity shorts beyond what Alpaca simulates; no option market impact.
