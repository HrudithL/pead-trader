# PEAD Trading

> **Current state (2026-10-21):** start at `docs/REPO_MAP.md`. The methodology fixes are in `docs/FIXES.md`; the best equity strategy that never borrows and never looks ahead is `steady_drift_plus` (`docs/STEADY_DRIFT.md`); open items are in `docs/ROADMAP.md`. The text below is the original project history and uses pre-correction numbers.


Post-earnings-announcement drift (PEAD) strategies on WRDS (IBES / CRSP / Compustat) and OptionMetrics data:
equity long/short books, a Kelly-selected options book, volatility-aware variants, and the blend of the two --
all run through one framework: **a strategy is one file; a runner backtests any strategy; reports and live
paper trading take the same strategy as input.**

```bash
pip install -r requirements.txt            # python 3.12 + pandas 3 reproduce the committed results (see Data)
python -m pead list                        # every strategy, family, status
python -m pead run calm_weighted         # backtest one strategy  -> results/calm_weighted/ (report included)
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
| `balanced_leverage` | 5.4% | 4.9% | 1.09 | -18.6% | the equity reference (was reported as 6.5% / 1.30) |
| `calm_weighted` | 5.6% | 4.5% | 1.24 | -17.5% | validated equity alpha sleeve (was 6.7% / 1.47) |
| `calm_rank_tilt` | 6.0% | 4.0% | 1.47 | -13.6% | research (was 7.1% / 1.81) |
| `market_boost` | 9.2% | 10.4% | 0.90 | -25.1% | alpha + 0.5x market: no longer clears the S&P (9.9%) (was 10.1% / 0.97) |
| `options_cap20` | 0.3% | 1.9% | 0.16 | -18.9% | options bought at the ask / sold at the bid: the edge is gone (mid-fill reference `options_cap20_midfill`: 4.4% / 1.85; originally reported 8.6% / 3.8) |
| `hedged_blend` | 4.9% | 4.5% | 1.09 | -21.8% | 0.8x `calm_weighted` + 1.6x `options_cap20`, one NAV (was 11.3% / 2.25; weights not re-optimised) |

**The numbers above are the corrected ones.** Four methodology defects (leverage cap, decile look-ahead, option fills at mid, holding periods fitted on the full sample) overstated every earlier headline; see [`docs/FIXES.md`](docs/FIXES.md) for what changed, the per-fix ladder, and how to reproduce the old numbers (`<name>_legacy`). Strategy constants were not re-selected, so these are still partly in-sample.

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
`data/events`, `data/metadata`, `data/results`) and the OptionMetrics extract (32.9GB, 131-file `parquet/` folder on an external
drive, `OPTIONMETRICS_DIR`, default `D:/OptionMetrics/parquet`, never copied). Derived inputs the backtests read
(`data/*.parquet`, `data/event_options/`) are rebuilt by `python -m pead prep --all` (or `--list` to see what is built).
`data/` holds the corrected point-in-time derived vintage; the complete pre-fix one is kept, git-ignored, in
`data/_prefix_vintage/` (how to select it: `docs/FIXES.md`). Small result files and `data/raw_data_manifest.json` (sha256 of the raw pull; `python pead/prep/verify_raw_data.py --check`)
are tracked.

* **Never symlink or junction data into this repo** (a tracked symlink once deleted a real local copy; `scripts/git-hooks/pre-commit`
  blocks it). Point `PEAD_*_DIR` / `OPTIONMETRICS_DIR` environment variables at wherever the data lives instead
  (`pead/common/paths.py`). The master copy of the non-OptionMetrics data is `D:/OptionMetrics/pead_wrds_data/`; checkouts hold real copies.
* **Use Python 3.12 / pandas 3.** pandas 2 assigns a handful of book-to-market terciles differently, which shifts 7 of 144 decay-day cells and
  Balanced Leverage's return by ~0.2pp. Compare result CSVs numerically, never by text diff (float formatting differs at 1e-13).
* Known data gaps: OptionMetrics 2011 is structurally corrupt and excluded from every options result; 2013 ends in August.
