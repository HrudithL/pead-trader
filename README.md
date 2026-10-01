# PEAD Trading

Post-earnings-announcement drift (PEAD) strategies on WRDS (IBES / CRSP / Compustat) and OptionMetrics data:
equity long/short books, a Kelly-selected options book, volatility-aware variants, and the blend of the two --
all run through one framework: **a strategy is one file; a runner backtests any strategy; reports and live
paper trading take the same strategy as input.**

```bash
pip install -r requirements.txt            # python 3.12 + pandas 3 reproduce the committed results (see Data)
python -m pead list                        # every strategy, family, status
python -m pead run s9b_inverse_vol         # backtest one strategy  -> results/s9b_inverse_vol/ (report included)
python -m pead run equity                  # a whole class; or 'options', 'all', 'family:equity-volatility', 's9*'
python -m pead compare                     # comprehensive cross-strategy report -> results/comprehensive/report.pdf
python -m pead portfolio                   # equity x options blend study
python -m pead live run --mock-data --dry-run   # paper-trading cycle (no network, no state)
python -m pytest tests -q
```

## Where to read

| Document | What it answers |
|---|---|
| [`docs/STRATEGIES.md`](docs/STRATEGIES.md) | What every strategy does, why it works, where its return comes from (long vs short, by decile/size/era/vol), how the Kelly options selector works, the options quote-marking error and its correction, the hedged blend, and what is *not* modeled |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The structure, how the runner parallelizes, how to add a strategy |
| [`docs/LIVE_TRADING.md`](docs/LIVE_TRADING.md) | The paper-trading engine |
| [`results/leaderboard.md`](results/leaderboard.md) | Best strategy so far per asset class, plus every run |
| [`results/comprehensive/report.pdf`](results/comprehensive/report.pdf) | League table, lineage, eras, correlations, the equity + options combination |
| [`results/<strategy>/report.pdf`](results) | One strategy's design, performance and attribution |
| [`docs/HISTORY.md`](docs/HISTORY.md) | The original README: how each strategy was derived (partly superseded) |
| [`GPU_SETUP.md`](GPU_SETUP.md) | Setting up a Linux GPU box for the data-prep stages |

## Headline results (1996-2013, $10M start; market = CRSP value-weighted: 9.9%/yr, 19% vol, Sharpe 0.59, DD -55%)

| Strategy | Return | Vol | Sharpe | Max DD | Note |
|---|---|---|---|---|---|
| `s6_neutral_levered` (Strategy 6) | 6.5% | 4.9% | 1.30 | -16.6% | the equity reference |
| `s9b_inverse_vol` | 6.7% | 4.5% | 1.47 | -15.6% | validated equity alpha sleeve |
| `s10_vol_rank_tilt` | 7.1% | 3.8% | 1.81 | -11.6% | research |
| `s6_beta_overlay` | 10.1% | 10.5% | 0.97 | -23.2% | alpha + 0.5x market: clears the S&P |
| `opt_kelly_cap20` | 4.1% | 2.3% | 1.77 | -13.8% | corrected options book (was reported as 8.6% / 3.8) |
| `hedged_blend` | 11.3% | 4.8% | 2.25 | -16.7% | 0.8x `s9b` + 1.6x `opt_kelly_cap20`, one NAV |

Short-borrow fees, financing and market impact are not modeled; see STRATEGIES.md section 6.

## Layout

```
pead/
  strategies/    one file per strategy (equity/, options/, portfolio/), auto-registered
  engines/       equity.py, options.py -- the shared simulation mechanics
  core/          data loading, metrics, results store + leaderboard, Kelly scoring
  runner.py, cli.py     parallel runner and the `python -m pead` command line
  reporting/     per-strategy and comprehensive reports, attribution, charts
  portfolio/     sleeve blending, walk-forward weights, volatility targeting
  live/          paper-trading engine (reuses the strategy objects)
  prep/          data-build stages as a dependency graph  (python -m pead prep --list)
  research/      the original numbered scripts, archived
  common/        paths, hardware detection, GPU backend, plotting, PDF helpers
results/         saved runs, reports, leaderboard
data/            small tracked result files + (untracked) licensed inputs
tests/           framework tests, incl. reproduction of the original scripts' outputs
docs/            STRATEGIES, ARCHITECTURE, LIVE_TRADING, HISTORY
```

## Data

The licensed inputs are not tracked: the WRDS pull (`data/raw_wrds`, `data/normalized_equity`, `data/earnings`,
`data/events`, `data/metadata`, `data/results`) and the OptionMetrics extract (31GB `parquet/` folder on an external
drive, `OPTIONMETRICS_DIR`, default `D:/OptionMetrics/parquet`, never copied). Derived inputs the backtests read
(`data/*.parquet`, `data/event_options/`) are rebuilt by `python -m pead prep --all` (or `--list` to see what is built).
Small result files and `data/raw_data_manifest.json` (sha256 of the raw pull; `python pead/prep/verify_raw_data.py --check`)
are tracked.

* **Never symlink or junction data into this repo** (a tracked symlink once deleted a real local copy; `scripts/git-hooks/pre-commit`
  blocks it). Point `PEAD_*_DIR` / `OPTIONMETRICS_DIR` environment variables at wherever the data lives instead
  (`pead/common/paths.py`). The master copy of the non-OptionMetrics data is `D:/OptionMetrics/pead_wrds_data/`; checkouts hold real copies.
* **Use Python 3.12 / pandas 3.** pandas 2 assigns a handful of book-to-market terciles differently, which shifts 7 of 144 decay-day cells and
  Strategy 6's return by ~0.2pp. Compare result CSVs numerically, never by text diff (float formatting differs at 1e-13).
* Known data gaps: OptionMetrics 2011 is structurally corrupt and excluded from every options result; 2013 ends in August.
