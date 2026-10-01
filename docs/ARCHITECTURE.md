# Architecture

One idea: **a strategy is one file; everything else is shared machinery that takes a strategy as input.**

```
                       ┌────────────────────────────────────────────────────────────┐
  pead/prep/           │ build the INPUTS (slow, licensed data: WRDS, OptionMetrics) │   python -m pead prep
  stages.py + equity/  └──────────────────────────────┬─────────────────────────────┘
  + options/                                          │ parquet/csv under data/
                       ┌──────────────────────────────▼─────────────────────────────┐
  pead/strategies/     │ ONE FILE PER STRATEGY: weights(events) / pick + sizing      │
  equity/ options/     │ registered by name, auto-discovered                         │
  portfolio/           └──────────────────────────────┬─────────────────────────────┘
                                                      │ Strategy object
                       ┌──────────────────────────────▼─────────────────────────────┐
  pead/runner.py       │ RUNNER: pick strategies -> worker processes -> engine -> save │  python -m pead run
  pead/engines/        │   engines/equity.py   quarterly trailing-NAV book           │
                       │   engines/options.py  premium-budgeted options book          │
                       └───────────┬───────────────────────────────┬────────────────┘
                                   │ results/<name>/                │ same Strategy objects
                       ┌───────────▼──────────────┐     ┌───────────▼───────────────┐
  pead/reporting/      │ per-run report (md + pdf) │     │ pead/live/  paper trading │  python -m pead live
  pead/portfolio/      │ leaderboard               │     │ events -> weights -> orders│
                       │ comprehensive report      │     └───────────────────────────┘
                       └──────────────────────────┘
```

## The pieces

| Package | What it owns | What it must NOT do |
|---|---|---|
| `pead/strategies/` | what to trade and how big (weights, picks, sizing parameters) | load data, write files, schedule work |
| `pead/engines/` | the mechanics every strategy shares: quarterly resizing off trailing NAV, liquidity and leverage caps, costs, daily P&L, per-position and per-leg attribution | know about any particular strategy |
| `pead/core/` | data loading (`data.py`, cached), metrics (`metrics.py`), results storage and the leaderboard (`store.py`), Kelly scoring (`kelly.py`) | |
| `pead/runner.py`, `pead/cli.py` | parallel execution and the command line | |
| `pead/reporting/` | per-strategy report, comprehensive cross-strategy report, attribution, charts | recompute a backtest (it reads saved results) |
| `pead/portfolio/` | combining sleeves: weights, walk-forward weights, volatility targeting, hedge diagnostics | |
| `pead/live/` | daily paper-trading cycle driven by the registered strategies | re-implement any strategy logic |
| `pead/prep/` | the data build as a dependency graph (`python -m pead prep --list`) | |
| `pead/research/` | the original numbered scripts (01-53), kept for history | be imported by anything |

## Running things

```bash
python -m pead list                              # every strategy: family, status, live-ready
python -m pead run s9b_inverse_vol               # one strategy  -> results/s9b_inverse_vol/
python -m pead run s6_neutral_levered opt_kelly_cap20   # several, in parallel worker processes
python -m pead run equity                        # a whole asset class ('options', 'portfolio', 'all', 'family:equity-volatility', 's9*')
python -m pead run all --jobs 8                  # everything, capped at 8 workers
python -m pead report s9b_inverse_vol            # rebuild a report from a saved run
python -m pead compare                           # comprehensive cross-strategy report -> results/comprehensive/
python -m pead portfolio                         # equity x options blend frontier -> results/portfolio/
python -m pead prep --list                       # data-build stages and whether their outputs exist
python -m pead live run --mock-data --dry-run    # paper-trading cycle, no network, no state
python -m pytest tests -q                        # framework tests (+ reproduction of the original scripts)
```

### How the work is parallelized

Strategies are independent simulations over shared read-only data, so the runner gives each its own **worker
process** (`pead/common/hardware.py::plan_workers` sizes the pool from cores and RAM, ~3GB per worker, and pins
each worker's BLAS pool to one thread so N workers do not fight over the cores). Each worker loads the daily
panel from a binary cache (`data/cache/equity_panel.npz`, ~1s after the first run instead of ~12s) and runs its
strategies. One equity strategy takes 1-3 seconds; the whole suite of 28 runs in under a minute.

The **GPU is not used by the backtests** -- they are NumPy array code on a few million position-days and gain
nothing from it. It is used where it earns its keep, in the prep stages: the Kelly contract selector scores
every strike of every event as one (events x strikes x return-states) tensor (`pead/core/kelly.py` runs on NumPy
or CuPy; `--device cuda`). The prep scheduler serializes GPU stages to one at a time and caps the OptionMetrics
disk scans (`--io-parallel`), because those stages contend for one drive.

## Adding a strategy

Create `pead/strategies/equity/my_idea.py`:

```python
from pead.engines.equity import Sizing
from pead.strategies import weights as W
from pead.strategies.base import EquityStrategy
from pead.strategies.registry import register

@register
class MyIdea(EquityStrategy):
    name = "my_idea"
    family = "equity-experiments"
    parent = "s6_neutral_levered"           # lineage, shown in the comprehensive report
    status = "research"                      # validated | research | reference | unverified
    description = "one line"
    thesis = "why it should make money"
    sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)

    def weights(self, events):               # events: one row per earnings event (see features below)
        return W.neutralized_tilt(events, W.centered_rank(events), trim=0.15, tilt_power=1.0)
```

That is the whole integration: `python -m pead run my_idea` backtests it, saves `results/my_idea/`, builds its
report, updates the leaderboard, and `python -m pead compare` includes it in the cross-strategy report. The module
docstring is rendered into the report as the strategy's design notes, so write it for a reader.

`events` columns available to a strategy: `ann_quarter, decile, sue_rank_pct, size_quintile, ff12_sector,
liquidity_quintile, avg_dollar_volume_21d, holding_days, tvol_60d, ivol_252d, beta_252d, tvol_60d_pct,
ivol_252d_pct, *_tercile`. `weights()` must be a pure function of those columns -- that is what lets the live
engine call the same code on today's reporters (declare `required_columns` and set `live_ready = True` once the
live feed can supply them).

Options strategies subclass `OptionsStrategy` and call `run_options_backtest`; portfolio strategies are
`PortfolioStrategy(name, {sleeve: weight})` instances (see `strategies/portfolio/sleeves.py`). A new KIND of
engine (say, a futures overlay) is a new module in `pead/engines/` plus a base class -- the runner, store,
reports and leaderboard need no change.

## Results layout

```
results/<strategy>/summary.json     stats + metadata + git rev   (tracked)
results/<strategy>/daily.csv.gz     NAV, P&L, leg splits, exposures (tracked)
results/<strategy>/quarterlog.csv   per-quarter sizing decisions (tracked)
results/<strategy>/report.md|pdf    the per-strategy report (tracked)
results/<strategy>/positions.parquet  every funded position with its P&L (git-ignored, regenerable)
results/leaderboard.json|md         best strategy so far per asset class and metric
results/comprehensive/              cross-strategy report
```

## Numbers you can trust

The new engines were validated against the original per-strategy scripts, not just re-derived: Strategies 1-3, 5-7
and the aggressive variants reproduce the originals' NAV to the dollar, Strategy 9's variants match its script, the
options engine reproduces the original final NAV to the dollar (`tests/test_framework.py`). Strategy 4 differs from
its committed file by design: that file predates the current data (the original script re-run today ends at the
engine's number). The data-prep stages were moved and re-run end to end: rebuilt inputs are identical to floating-
point noise.
