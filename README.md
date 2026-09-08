# PEAD Trading Research

Post-earnings-announcement drift (PEAD) research and trading strategy development on the
WRDS-pulled IBES/CRSP/Compustat data in `data/raw_wrds/`, `data/normalized_equity/`,
`data/earnings/`, and `data/events/` (not tracked in this repo -- see [Data](#data) below).

This repo documents the full evolution: from first establishing that the PEAD effect exists in
this data, through building and comparing eight distinct trading strategy designs, to an
evidence-based research pass on how to push the strategy's return higher without simply adding
uncontrolled risk, to extending the whole question to the options market using OptionMetrics IvyDB
data. Every numbered script is independently re-runnable, and the run order below reproduces the
entire project from raw data to final backtest results.

## Results summary

All figures are 1996-2013, market-adjusted (or, for Strategy 7, raw) daily returns, $10,000,000
initial capital, true NAV-percentage Sharpe ratios (see the [note on the Sharpe
calculation](#note-a-bug-i-found-and-fixed-in-the-sharperisk-metrics) below).

| Strategy | Design | Ann. Return | Ann. Vol | Sharpe | Max DD |
|---|---|---|---|---|---|
| Extreme decile L/S | Long D10 / short D1 only, per-cell holding periods | 4.91% | 5.32% | 0.93 | -18.5% |
| Rank-weighted | All 10 deciles, continuous SUE-rank weight | 3.88% | 3.86% | 1.01 | -14.0% |
| Balanced | Extreme decile + size-quintile neutral | 4.74% | 5.26% | 0.91 | -17.5% |
| Strategy 4 | Trimmed universe, tilted weight, size-neutral, priority-based leverage-cap trimming | 5.26% | 4.41% | 1.18 | -13.1% |
| Strategy 5 | Strategy 4 + FF12-sector neutral | 4.06% | 3.02% | **1.33** | **-10.8%** |
| Strategy 6 | Strategy 5 + leverage cap raised 1.5x -> 2.5x | 6.49% | 4.94% | 1.30 | -16.6% |
| Strategy 6 + 0.5x beta overlay | Strategy 6 + a separate market-index position sized to 0.5x trailing NAV | **10.08%** | 10.51% | 0.97 | -23.2% |
| Strategy 7 | Strategy 5/6's signal, but the long and short legs are no longer forced dollar-equal each quarter | 7.30% | 6.56% | 1.11 | -15.5% |

**Currently recommended for live consideration: Strategy 6 (pure alpha) or Strategy 6 + 0.5x beta
overlay (if a return closer to a 10-15% target, with correspondingly more market-correlated risk,
is preferred over a pure, uncorrelated alpha stream).** Strategy 7 is documented for completeness
and as a cautionary result -- see [Strategy 7's caveat](#why-strategy-7-is-not-simply-a-cheaper-way-to-add-beta) below.

A ninth design, **Strategy 8**, has since been built (see "Equity-strategy GPU roadmap" below) --
it blends a walk-forward-trained ML score with the SUE-rank tilt and picks its own
(trim/tilt/leverage/sizing) constants via a real GPU-batched search instead of the hand-picked
values strategies 4-7 use, the same escalation in compute the options side went through in its own
roadmap. Its pipeline is fully built and `--mock-data`-verified end to end, but **not yet run for
real**: this dev machine has neither the raw WRDS pull nor a GPU attached (see "Data" below) --
running it for real, and adding its row to this table, is the next step on a machine that has both.

## Strategy evolution, in order

1. **Extreme decile L/S** -- the simplest form of the classic PEAD trade: long the most positive
   earnings-surprise decile, short the most negative, holding for a period determined empirically
   per (decile x size x book-to-market) cell rather than a fixed number of days.
2. **Rank-weighted** -- trades every decile with a continuous weight proportional to SUE rank
   distance from the median, for better cross-sectional diversification.
3. **Balanced** -- the extreme-decile trade, but neutralized by size quintile each quarter so no
   single size bucket dominates the book.
4. **Strategy 4** -- built after diagnosing *why* rank-weighted's Sharpe was better but its return
   worse than extreme decile's: rank-weighted was trading ~4x more positions, saturating both the
   liquidity cap (99% of positions) and the leverage cap (90% of quarters), and the leverage cap's
   old proportional scale-down diluted its strongest and weakest convictions equally. Strategy 4
   trims the low-conviction middle of the SUE-rank distribution, tilts weight toward the extremes,
   keeps size-neutrality, and replaces proportional cap-trimming with priority-based trimming
   (lowest-conviction positions get dropped first, not everyone shaved equally).
5. **Strategy 5** -- a diagnostic found Strategy 4's book concentrating up to 29% of its gross
   weight in a single FF12 sector in some quarters (vs. 8.3% if evenly spread across 12 sectors),
   an unpriced, PEAD-unrelated risk. Adding sector neutrality alongside size neutrality fixed it:
   best Sharpe (1.33) and shallowest drawdown (-10.8%) of any strategy built.
6. **Strategy 6** -- testing whether the 1.5x leverage cap was actually costing Strategy 5
   anything found that, for this specific well-diversified signal, it wasn't: raising it to 2.5x
   (the point where it stops binding) improved both return (5.08%->6.49%, at buf=0.003) *and*
   Sharpe, because there was no concentration risk left for the cap to be protecting against.
7. **Strategy 6 + 0.5x beta overlay** and **Strategy 7** -- research pass on whether a genuinely
   higher return (10-15%, matching equity-market-like targets) is achievable, built from actual
   findings rather than more parameter tuning. See below.

## Why the strategy's return trails the S&P 500, and what actually closes the gap

Every strategy above uses market-adjusted returns and (mostly) dollar-neutral long/short
construction by design -- it deliberately carries close to zero market beta. That means it
structurally forgoes the equity risk premium that makes up most of the S&P 500's long-run return;
comparing its nominal return to the S&P's isn't apples to apples. As an external sanity check,
AQR's live Equity Market Neutral Fund (QMNNX) returns 6.19%/7.03% vol/Sharpe 0.69 and *also*
trails the S&P 500 (13.38% over the same window), with ~zero correlation to it -- that is what a
professionally-run market-neutral strategy is supposed to look like. Strategy 6 already beats that
real fund's Sharpe (1.30 vs. 0.69).

Institutional practice for closing this gap, confirmed against the actual research literature
(Frazzini/Israel/Moskowitz on real-world trading costs; Acadian on 130/30 mechanics -- see
citations in `scripts/equity_strategy/24_finalize_strategy6.py` and the conversation history), is not to trade
PEAD harder on its own -- it's usually blended into a multi-factor book and implemented via a
130/30-style long-extension structure: keep the alpha book's market-neutral construction intact,
and add market exposure back as a *separate* position on top of it, rather than distorting the
alpha signal itself to let beta leak in. That's exactly what **Strategy 6 + 0.5x beta overlay**
does (`scripts/equity_strategy/25_strategy6_beta_overlay.py`): the alpha book is untouched, and a synthetic
market-index position sized to 0.5x trailing NAV (rebalanced on the same quarterly cadence,
2bps overlay transaction cost) is held alongside it. Result: 10.08% return, Sharpe 0.97 -- inside
the 10-15% target band, with much better risk-adjusted and drawdown characteristics than simply
holding the market (9.94% return, Sharpe 0.59, -55.5% max drawdown).

### Why Strategy 7 is not simply a cheaper way to add beta

Strategy 7 (`scripts/equity_strategy/26_strategy7_unconstrained_netexposure.py`) tests the other way to let some
market exposure through: stop forcing the long leg's and short leg's dollar totals to match every
quarter (Strategy 5/6's neutralization does this by construction, crossing its diversification
groups with side). Removing that constraint required switching from market-adjusted to raw
returns (market-adjusting would have silently canceled out the very net exposure this is trying to
let through), and that switch reintroduced full single-name market beta on *every* position, long
and short -- not just a small, controlled net-dollar tilt. The result: despite averaging only
-5.5% net exposure as a fraction of gross (essentially flat), Strategy 7's correlation to the
market came out at **0.52**, much higher than intended, because dollar-neutral is not the same
thing as beta-neutral (positive- and negative-SUE firms structurally differ in average beta, so
even equal dollar amounts long and short don't cancel market risk the way market-adjusting the
underlying returns does). Strategy 7 is kept in the repo and fully runnable as a documented,
tested result, not a recommended production design: it delivers a real return/Sharpe (7.30%/1.11)
but with a much less controllable source of market exposure than the explicit beta overlay above.

## Note: a bug I found and fixed in the Sharpe/risk metrics

Every backtest script (17 onward) originally computed daily returns as
`daily_pnl / INITIAL_CAPITAL` (a fixed constant) rather than `daily_pnl / prior_day_NAV`. Since
NAV compounds up over the ten-year backtest, later-period dollar P&L swings were measured against
a stale, smaller denominator, overstating volatility and understating Sharpe. Returns and
drawdowns (computed directly from the NAV series) were unaffected, and the relative ranking of
strategies doesn't change, but the true Sharpe ratios are meaningfully better than the ones
originally reported mid-project. The results table above and `data/results_summary_v2_FINAL.csv`
use the corrected `nav.pct_change()`-based calculation.

## Repository layout

`scripts/` is organized as a small package, not a flat pile of numbered files: a `common/`
module of code genuinely shared across every stage, four domain folders that each answer one
question, and a `legacy/` attic for superseded-but-kept-for-history code. Original script numbers
are unchanged (they encode run order and are cited by exact name inside the PDF reports below) --
only the folder each one lives in is new.

```
scripts/
  common/                 # cross-domain shared code, imported by everything below
    paths.py              # every data/reports/raw-data location, env-var overridable
    stats.py              # fama_macbeth() -- one implementation, not five
    report_pdf.py          # shared ReportLab stylesheet + h1/h2/fig/make_table/... helpers
    plotting.py            # shared matplotlib style for every chart script
    gpu.py                 # device backend (numpy/cupy), --device/--smoke-test flags, checkpoint/
                            # status logging (StageTimer) -- shared by BOTH GPU-tiered pipelines
                            # below, factored out of options_strategy/lib/gpu.py so equity_strategy
                            # didn't need a second copy

  equity_pead/            # "does PEAD exist in equities?" -- v1 evidence pipeline + PEAD_Report.pdf
  equity_strategy/         # backtest engines, all 8 hand-built strategies, Strategy 8's GPU
                            # pipeline, and the two showcase/development reports
    lib/positions.py        # size_balance(), weights_size_sector_neutral() -- shared weight
                             # construction, used by strategies 3/5-8
    lib/gpu.py               # equity-specific mock daily-panel/position/feature generators,
                              # built on common/gpu.py's device backend
    run_pipeline.py            # concurrent driver: runs every independent stage (11-35) in
                                # parallel across CPU cores, GPU-tiered stages (32-34) serialized
                                # to one at a time -- see "Equity-strategy GPU roadmap" below

  options_pead/            # "does the drift show up in options?" -- evidence pipeline + PEAD_Options_Report.pdf
  options_strategy/         # contract selection/pricing + the GPU-tiered backtested strategy + its own report
    lib/options.py           # streamed/filtered opprcd scans, contract picker (moved from options_lib.py)
    lib/gpu.py                # options-specific mock-data generators, built on common/gpu.py's device backend
    lib/hw.py                  # CPU/RAM/GPU detection, --jobs defaults shared by every stage + run_pipeline.py
    run_pipeline.py            # hardware-aware concurrent scheduler for the whole pipeline (scripts 35-52)

  legacy/
    05_build_pdf.py         # original cloud-sandbox evidence report, superseded by equity_pead/32
```

See "Options-strategy GPU roadmap" below for what each numbered script in `options_strategy/`
(35-52) actually does -- that section has its own full script listing.

`common/paths.py` resolves every location relative to the repo root by default (replacing a set of
dead, cloud-sandbox-specific absolute paths -- `/root/pead_report/...`, `/mnt/user-data/...` --
that most scripts originally hardcoded and that never actually resolved on any machine this repo
has been checked out on). Each one can still be overridden with an environment variable, e.g.
`OPTIONMETRICS_DIR=/content/drive/MyDrive/OptionMetrics/parquet` to run the options pipeline from a
Google Drive mount on a Colab GPU runtime instead of the local `D:/OptionMetrics/parquet`. A script
is run exactly the same way as before, just from its new path, e.g.
`python scripts/equity_strategy/17_backtest_v2.py`.

## Run order

The full pipeline, in the order each stage depends on the last. Everything is re-runnable from
this repo plus the raw WRDS pull already present under `data/`.

**v1 pipeline (initial PEAD-exists-in-the-data report, decile sorts, Fama-MacBeth stats, a
Fama-French-style characteristic robustness check, and the original fixed-notional backtest
engine):**
```
scripts/equity_pead/01_build_deciles.py        # decile construction from IBES SUE + CRSP/Compustat
scripts/equity_pead/02_decile_summary.py       # Fama-MacBeth quarter-clustered decile statistics
scripts/equity_pead/03_subsample.py            # subsample similarity tests (sector, size, era)
scripts/equity_pead/04_charts.py               # decile drift / spread charts
scripts/legacy/05_build_pdf.py                 # assembles the PDF report (legacy, see below)
scripts/equity_pead/06_ff_adjustment.py        # size + book-to-market characteristic adjustment
scripts/equity_pead/07_ff_charts.py            # characteristic-adjusted charts
scripts/equity_pead/08_extended_horizons.py    # checkpointed return horizons out to 180 days
scripts/equity_pead/09_decay_analysis.py       # early (checkpoint-based) decay-day estimate
scripts/equity_pead/10_extended_ff_decay.py    # characteristic-adjusted decay analysis
scripts/equity_strategy/11_positions.py        # v1 position construction (3 strategies)
scripts/equity_strategy/12_backtest_engine.py  # v1 fixed-notional backtest engine
scripts/equity_strategy/13_strategy_charts.py  # v1 NAV / drawdown / exposure charts
```

**v2 pipeline (daily-precision decay curve, decile x size x BM holding-period cells,
trailing-NAV compounding backtest, and every strategy from "the initial 3" onward -- all in
`equity_strategy/`):**
```
14_daily_decay.py                       # 200-day daily decay curve + holding-period cells
15_decay_days_v2.py                     # windowed-slope decay-day detection (fixes 14's noise)
16_positions_v2.py                      # per-cell holding periods for the initial 3 strategies
17_backtest_v2.py                       # trailing-NAV backtest: extreme, rankweighted, balanced
18_param_sweep.py                       # base_unit_fraction / liquidity_cap_frac sweep
19_param_sweep_extended.py              # wider sweep to find the sizing "elbow"
20_strategy4_tilted.py                  # Strategy 4: trimmed/tilted, priority-based cap
21_leverage_and_improvements.py         # leverage-cap justification test + sector diagnostic
22_sector_neutral_and_cadence.py        # sector-neutral test + monthly-cadence test
23_finalize_strategy5.py                # Strategy 5: + sector-neutral, finalized
24_finalize_strategy6.py                # Strategy 6: + leverage cap raised to 2.5x
25_strategy6_beta_overlay.py            # Strategy 6 + 0.5x market beta overlay
26_strategy7_unconstrained_netexposure.py  # Strategy 7: no long=short dollar constraint
27_ear_signal_diagnostic.py             # EAR (announcement-window return) signal research, leak-free
28_sue_reversal_test.py                 # SUE lag-4 autocorrelation / reversal test
```

**Two negative results (27, 28) are included deliberately.** Not every research thread in this
project improved the strategy, and both are documented rather than dropped:

- **Script 27** tests whether a firm's *announcement-window* return (EAR, the day-0 price
  reaction itself, not just the SUE number) adds information beyond SUE rank. It caught its own
  look-ahead bug along the way: an earlier version of this test used the day0-to-day0+1 return as
  the EAR input, which mechanically overlaps with a position's own first day of held P&L (since
  positions start accruing the day after entry) and produced an implausible, ever-increasing
  Sharpe as EAR weight increased. Once fixed to use only day0's own return (fully realized before
  day0+1 trading begins), the result is a flat Sharpe (~1.0-1.06) regardless of EAR blend weight --
  no real improvement, so EAR is not incorporated into any numbered strategy.
- **Script 28** tests the Bernard & Thomas (1990) finding that a firm's own SUE tends to reverse at
  a 4-fiscal-quarter lag (they report roughly -0.24 autocorrelation). In this data the lag-4 SUE
  autocorrelation is **+0.05**, and the decile breakdown shows persistence, not reversal: firms
  with the highest SUE four quarters ago still average the highest SUE now, and vice versa. There
  is no reversal signal to trade here, which is why none of strategies 4-7 attempt to fade a firm's
  own earnings-surprise history.

Each v2 backtest script writes `data/backtest_v2_<name>.csv` (daily NAV/exposure series),
`data/backtest_v2_<name>_summary.json` (headline stats), and, where applicable,
`data/backtest_v2_<name>_quarterlog.csv` (the trailing-NAV sizing decision made each quarter).

**Reports (built from the above, locally runnable, in `equity_strategy/`):**
```
29_v2_strategy_charts.py                # NAV/drawdown/comparison charts for all 8 strategies
30_build_strategy_showcase_report.py    # reports/PEAD_Strategy_Showcase.pdf -- performance
31_build_development_report.py          # reports/PEAD_Strategy_Development.pdf -- the "why"
```
`scripts/equity_pead/32_build_evidence_report.py` builds `reports/PEAD_Report.pdf` -- does PEAD
exist (v1 evidence). `scripts/legacy/05_build_pdf.py` (the original, cloud-sandbox-only version of
the evidence report, including the now-superseded 3-strategy v1 backtest section) is left in place
for history but superseded by `32_build_evidence_report.py` for local regeneration.

**Strategy 8: GPU-tiered equity pipeline (extends the v2 backtest engine above with a learned
signal and a real hyperparameter search instead of the hand-picked constants strategies 4-7 use --
see "Equity-strategy GPU roadmap" below for what each script does):**
```
scripts/equity_strategy/lib/gpu.py                    # device backend (numpy/cupy) + mock-data generators, built on common/gpu.py
scripts/equity_strategy/32_gpu_feature_panel.py        # Tier 1: trailing-window momentum/vol features + fwd_realized_ret label
scripts/equity_strategy/33_gpu_walkforward_signal.py   # Tier 2: walk-forward-trained MLP, hand-rolled directly against numpy/cupy
scripts/equity_strategy/34_gpu_param_sweep.py          # Tier 3: GPU-batched sweep over trim/tilt/ML-blend-weight/sizing/leverage
scripts/equity_strategy/35_finalize_strategy8.py       # Tier 4: full daily-precision backtest at the sweep-selected combo
scripts/equity_strategy/run_pipeline.py                 # concurrent driver: every independent stage (11-35) in parallel
```

**Options pipeline (extends the same SUE deciles into the options market via OptionMetrics IvyDB
data, mounted separately at `OPTIONMETRICS_DIR` -- default `D:/OptionMetrics/parquet/`, not
tracked in this repo, ~100GB, see [Data](#data) below). Split across `options_pead/` (the evidence
question) and `options_strategy/` (contract selection and pricing, the substrate for that
evidence and for a future options backtest):**
```
scripts/options_strategy/lib/options.py         # shared helpers: streamed/filtered opprcd scans, contract picker
scripts/options_pead/33_build_trading_calendar.py     # OM trading-day calendar from secprd*.parquet (1996-2013 ex-2011)
scripts/options_pead/34_link_events_secid.py          # rebuilds the decile event panel locally, links permno -> secid
scripts/options_strategy/35_select_entry_contracts.py # per event: near-ATM call + put (delta~+-0.50, DTE>=95d) at day0
scripts/options_strategy/36_forward_option_prices.py  # same-contract buy-and-hold mid price at +{1,5,10,20,40,60}d
scripts/options_pead/37_decile_summary_options.py     # decile x horizon stats for call/put/straddle, quarter-clustered
scripts/options_pead/38_charts_options.py             # decile drift, spread, coverage charts (figures 18-23)
scripts/options_pead/39_build_options_report.py       # reports/PEAD_Options_Report.pdf
```
**2011 is excluded from every options result:** `opprcd2011.sas7bdat` (14.6GB) is present on the
source drive but structurally corrupt -- confirmed independently with two different SAS readers
(pyreadstat and pandas both fail, on the first row/chunk respectively). That year's
underlying-price file (`secprd2011`) converted cleanly, so the corruption is specific to the
option-price file. 2013 is also truncated at end-August in this OptionMetrics extract. See
`PEAD_Options_Report.pdf` Section 1 for the full writeup.

**Options-strategy GPU pipeline (turns the above descriptive result into an actual, increasingly
compute-hungry backtested strategy, in `options_strategy/` alongside 35-36 -- see the full roadmap
section below):**
```
scripts/options_strategy/lib/gpu.py                        # device backend (numpy/cupy), mock-data generators, checkpoint logging
scripts/options_strategy/lib/hw.py                         # CPU/RAM/GPU detection, --jobs defaults for the per-year scan scripts
scripts/options_strategy/40_options_backtest.py            # Tier 1: real capital-sized, cost-aware options P&L backtest
scripts/options_strategy/46_build_return_distributions.py  # Tier 1.5: empirical decile-conditioned return distribution
scripts/options_strategy/47_scan_full_chain_entries.py     # Tier 1.5 data: every strike in the day0 chain, not just near-ATM
scripts/options_strategy/47b_attach_underlying_price.py    # Tier 1.5 data: underlying spot price per event (from secprd)
scripts/options_strategy/48_optimal_contract_selector.py   # Tier 1.5: GPU Kelly-optimal strike selection across the full chain
scripts/options_strategy/49_forward_prices_optimal.py      # Tier 1.5 data: forward mid prices for the selected contracts
scripts/options_strategy/50_options_backtest_optimal.py    # Tier 1.5: backtests the Kelly-optimal picks, vs. Tier 1's near-ATM
scripts/options_strategy/41_build_daily_option_paths.py    # Tier 2 data: full daily price path per position (not just 6 checkpoints)
scripts/options_strategy/42_gpu_exit_optimizer.py          # Tier 2: GPU dynamic stop-loss/profit-target exit-rule search
scripts/options_strategy/43_gpu_param_sweep.py             # Tier 3: GPU-batched sweep over strike/DTE/sizing/exit-rule combos
scripts/options_strategy/44_ml_contract_selector.py        # Tier 3: Optuna + PyTorch model, IV/liquidity/sector features
scripts/options_strategy/45_joint_portfolio_optimizer.py   # Tier 4: joint equity+options+beta allocation search
scripts/options_strategy/run_pipeline.py                   # hardware-aware concurrent scheduler: all 16 stages, dependency-ordered, skips completed, logs status
scripts/options_strategy/51_options_strategy_charts.py     # NAV/comparison/validation charts (figures 24-26)
scripts/options_strategy/52_build_options_strategy_report.py  # reports/PEAD_Options_Strategy_Report.pdf
```

**Note on the reorganization itself:** the numbered scripts were moved into the domain folders
above (and their internal duplication -- `fama_macbeth`, the PDF report boilerplate, matplotlib
styling, path constants -- was consolidated into `common/`) without changing any algorithm, weight
formula, NAV/Sharpe calculation, or backtest/selection logic -- including the GPU pipeline above,
which landed on `main` after this reorganization branch was created and was merged in and
relocated the same way, not rewritten. `04, 07, 13, 29` (charts) and `30, 31, 32` (reports) were
re-run after the move and diffed against the previously committed output to confirm no drift;
the GPU pipeline's `--device cpu --mock-data --smoke-test` path was similarly rerun after
relocation to confirm every script still starts, imports, and writes its declared output; the rest
of the pipeline needs the raw WRDS/OptionMetrics pulls (or, for Tier 3/4, an actual GPU) to
re-verify end-to-end, which aren't present in every checkout. Two already-built PDFs' embedded
source citations still name the old flat `scripts/NN_....py` paths rather than the new nested
ones, because regenerating them needs data this repo doesn't always have on hand:
`PEAD_Options_Report.pdf` (needs `data/event_options/`) and `scripts/legacy/05_build_pdf.py`'s own
historical output (superseded regardless, see above). Their source code is already updated; only
those two already-built PDF files are stale until someone reruns them with full data access.

## Reports

Five PDFs in `reports/`, meant to be read in this order:

1. **`PEAD_Report.pdf`** -- does the PEAD effect actually exist in this data? Decile-sorted event
   study, quarter-clustered significance test, size/book-to-market robustness check, and
   sector/size/era subsampling.
2. **`PEAD_Strategy_Showcase.pdf`** -- given that it exists, how well did each of the 8 ways of
   trading it actually perform? Full metrics, NAV curves, drawdowns, and a recommendation.
3. **`PEAD_Strategy_Development.pdf`** -- why was each strategy built the way it was? The
   diagnostic reasoning behind each iteration, in plain language, including two deliberate dead
   ends (the EAR signal test and the SUE reversal test).
4. **`PEAD_Options_Report.pdf`** -- does the same decile-sorted drift show up in the *options*
   market? 126,008 earnings events (1996-2013 ex-2011) matched to a near-ATM call and/or put;
   the D10-D1 spread on near-ATM calls is +14.38% at 60 days (t=4.69, quarter-clustered) versus
   +3.05% for the underlying stock over the same events, near-ATM puts show the mirror pattern
   (-10.79%, t=-6.29), and a long-straddle robustness cut shows a smaller but still significant
   decile-ordered residual (+3.98%, t=5.22) -- so leverage amplifies the same directional signal,
   with a real but secondary volatility component alongside it.
5. **`PEAD_Options_Strategy_Report.pdf`** -- given that it exists, does an actual backtested
   options strategy work? Turns the descriptive result above into a real, capital-sized, cost-aware
   backtest (Tier 1: 9.51% ann. return, Sharpe 1.04 at 60d), then replaces the near-ATM contract
   heuristic with a Kelly-criterion selector scoring every strike in the real day0 chain against an
   empirical, walk-forward return distribution (Tier 1.5: 15.31% ann. return, Sharpe 3.56) --
   documents the three real bugs found and fixed getting there, and the GPU compute roadmap
   (Tiers 2-4) built for a dedicated 5090 machine to push further.

## Options-strategy GPU roadmap

`PEAD_Options_Report.pdf` established that the PEAD signal shows up in options, amplified by
leverage (call D10-D1 spread +14.38% at 60d vs. +3.05% for the underlying) -- but that's still a
decile-sorted *descriptive* result on fixed-horizon forward mid prices, not a backtested strategy
with position sizing, costs, or an exit rule. This section is that missing strategy, built as four
tiers of deliberately increasing compute cost, each one only justified by a concrete question the
tier before it couldn't answer:

| Tier | Script(s) | Question it answers | Compute | Where it runs |
|---|---|---|---|---|
| 1 | `40_options_backtest.py` | What's the actual Sharpe/return of trading the decile signal via options, with real capital sizing and costs? | CPU, seconds | here |
| 1.5 | `46-50` | Script 35/40 always take the near-ATM (~50-delta) contract by assumption. Is that actually the best contract in the real day0 chain? | Data extraction: minutes (comparable to script 35/36's scan cost). Kernel: GPU tensor scan over events x strikes x return-distribution states. | ran for real this session, CPU; GPU-batchable for the full chain at scale |
| 2 | `41_build_daily_option_paths.py`, `42_gpu_exit_optimizer.py` | Fixed horizons ({1..60}d) leave return on the table if the position should really be closed on a stop/target hit mid-holding -- what's the best dynamic exit rule? | Data extraction: hours (heavier OM scan). Kernel: GPU dense-array scan, batched over positions x parameter combos. | data extraction on the 5090; kernel runs anywhere, GPU preferred |
| 3 | `43_gpu_param_sweep.py`, `44_ml_contract_selector.py` | Tier 1/2 hand-picked (trim, tilt, sizing, exit-rule) constants -- what does a real sweep find, and can a model beat "decile membership" as the entry signal using IV/liquidity/sector features? | GPU-batched sweep across a large parameter grid; Optuna-driven PyTorch training (borrows the Optuna search pattern from `reference/options_content/MSTR_Options_Storage/panel_model.py`, retargeted at OptionMetrics data with a plain feed-forward net) | 5090, unattended, hours-to-a-day |
| 4 | `45_joint_portfolio_optimizer.py` | Given Tiers 1-3's best options sleeve, Strategy 6 (equity), and the beta overlay all exist independently -- what's the best JOINT allocation and multi-leg structure across all three? | Largest combinatorial search in the project; explicitly the "compute is allowed to be very large" tier | 5090, unattended, multi-day |

### Tier 1.5: mathematically-optimal contract selection (not just near-ATM)

Every prior script (35, 40) picks a contract by MONEYNESS ALONE -- closest to 50-delta. That
throws away the entire rest of the day0 chain, and it's exactly the thing the prior-art tooling in
`Options_Content` (`optionExpReturn_v08.py`'s `calcMaxER`) was actually built to do differently:
score EVERY strike by expected return under a probability distribution of the underlying's future
price, and take the argmax. This tier rebuilds that idea properly instead of copying it as-is --
the original's own scenario table (`return_states.csv`) is a hand-typed 3-point guess (`{0%: 25%,
+20%: 50%, 0%: 25%}`) reused for every stock on every day, which is not "mathematically proven"
by any reasonable definition; and its `calcMaxER` ranks by raw expected value, which is the
textbook way a leveraged, convex instrument selector blows up (a deep-OTM option with a tiny
chance of a huge payoff has unbounded E[R] while losing money almost every time).

- **`46_build_return_distributions.py`** replaces the hand-typed scenario table with an empirical,
  decile-conditioned distribution built from this project's own 182k+ real historical PEAD events
  -- quantile-bucketed (not fixed-width) so a fixed number of states always carries equal
  probability and the tails are each represented by their own real conditional mean, not a guess.
  **A bug this script's own first real run caught and fixed:** built straight from RAW forward
  returns, every decile's distribution came out net-positive (1996-2013 was a net up market), which
  swamps the much smaller decile-specific PEAD tilt with ambient market beta. The fix: build the
  distribution's *shape* from market-adjusted returns (isolating the real PEAD component) and
  re-center it by the real historical market drift for that horizon (recovered as
  `mean(raw - mktadj)`) -- see the script's docstring for the full account.
- **`47_scan_full_chain_entries.py`** / **`47b_attach_underlying_price.py`** fetch every strike (not
  just one) in each event's day0 chain plus the underlying's own spot price. **A second bug this
  session's first real run caught:** OptionMetrics stores `strike_price` in 1/1000ths of a dollar; comparing
  it directly against a computed underlying price without dividing by 1000 makes every put look
  like a riskless, guaranteed-payoff bet against an absurdly inflated strike (confirmed by the
  telltale symptom: puts dominating even the highest SUE decile, and Kelly fractions clustering
  near 100% -- both signs of a "free money" artifact, not a real edge). Fixed at the source and
  corrected on the already-scanned data without re-running the OM scan.
- **`48_optimal_contract_selector.py`** is the actual selector: for each event, GPU-batches
  (events x strikes x return-distribution states) into one payoff tensor, then for a grid of
  candidate KELLY FRACTIONS finds each contract's expected LOG growth `E[log(1+f*R)]` and its own
  best fraction `f*` -- comparing contracts by that growth rate, not raw E[R]. This is the
  principled fix for "leverage means huge profits": a wild OTM lottery-ticket strike is still
  evaluated, it just gets correctly found to have a tiny optimal `f*` and near-zero growth
  contribution, rather than being ranked first the way naive expected-value would rank it.
  **Walk-forward, not full-sample:** each event is scored only against the distribution built from
  quarters strictly before its own announcement quarter (joined on `ann_quarter` ==
  `as_of_quarter`) -- events in script 46's 8-quarter warmup window are dropped, not backfilled.
  Running this for real on the 113,927 events that survive the warmup cut produced a clean,
  monotonic validation: the selector picks a PUT for 32% of D1 (lowest SUE) events vs. just 5% of
  D10 events -- the math recovers the PEAD direction on its own, from data alone, with no
  call/put side hard-coded anywhere in the selection logic.
- **`49_forward_prices_optimal.py`** / **`50_options_backtest_optimal.py`** backtest the
  Kelly-optimal picks for real (same checkpoint mark-to-market method as script 40), sized by each
  position's own **half-Kelly** fraction (full Kelly is a well-documented over-bettor once the
  probability distribution is itself an estimate, not known exactly -- half-Kelly is the standard
  practitioner correction for that estimation risk), distributed across each quarter's ~1,000+
  concurrent candidates in proportion to their own Kelly fraction (sizing each one independently
  off its own single-bet-optimal fraction was tried first and collapsed the whole 17-year backtest
  to ~46 funded positions total -- found and fixed on the first real run), and only taking events
  with `kelly_growth > 0` (a genuine, math-derived edge) rather than script 40's SUE-rank `TRIM`
  cutoff.

  | | Tier 1 (near-ATM) | Tier 1.5 (Kelly-optimal, walk-forward) |
  |---|---|---|
  | 60d ann. return | 9.51% | 15.31% |
  | 60d Sharpe | 1.04 | 3.56 |
  | 60d max drawdown | -25.9% | -21.4% |

  **Two look-ahead bugs were found and fixed to get to this number** (full account in
  `46_build_return_distributions.py`'s docstring): a full-sample distribution originally let a
  1998 trade get priced with data through 2013 (the same category of mistake script 27's docstring
  already documents catching for the EAR signal), and per-position Kelly sizing that ignored how
  many other candidates were competing for the same capital cap first collapsed the whole 17-year
  backtest to ~46 funded positions. Both are fixed. The Sharpe barely moved after the walk-forward
  fix (~4.0 -> 3.56), which was itself worth checking rather than trusting: the day-level P&L was
  inspected directly, and the worst days in the whole 17-year series land on 2008-10-24 and
  2008-11-20 (the financial crisis) and September 2002 (another real stress window) -- real,
  economically sensible tail risk showing up where it should, not a flat/smoothed artifact. The
  Sharpe is high enough (3.5+) to reflect a REAL diversification effect from spreading a fixed 20%
  premium budget across ~1,200 mostly-independent single-stock earnings bets per quarter (Grinold's
  "breadth" argument: aggregate risk-adjusted return scales with the number of quasi-independent
  bets combined) rather than a bug, but it is still **not something to treat as directly
  achievable**: this many small trades (~4,600/year) would face real bid/ask friction on illiquid
  strikes likely worse than the flat 3% assumption, real capacity/market-impact limits no backtest
  captures, and early-window Kelly estimates built on only the just-past 8-quarter minimum are
  necessarily noisier than later ones that benefit from a decade-plus of accumulated history --
  none of which this backtest models.

### Cross-machine execution model

Every script above is a plain, argparse-driven `.py` file -- no Jupyter notebooks anywhere in this
pipeline. That's deliberate: it's the only form that runs identically in all three places this
code needs to run:

1. **Here** (this Windows dev machine, CPU only) -- for writing and correctness-testing the logic.
2. **Google Colab from THIS Windows machine**, via the
   [Colab CLI](https://github.com/googlecolab/google-colab-cli) (`google-colab-cli`, released June
   2026). It's officially **Linux/macOS only** -- not installable directly on Windows -- but that's
   solved by **WSL2**, not by giving up on the CLI or converting scripts to notebooks: WSL2 is a
   real Linux kernel running locally on this same machine, so the CLI runs on it exactly as it
   would on bare-metal Linux, no code changes and no notebook conversion needed anywhere in this
   repo. (This is the mirror image of the earlier WSL discussion for the 5090 box: WSL was
   unnecessary THERE because it's already native Linux; it's exactly what's needed HERE because
   this machine is Windows.) One-time setup, run by you (installing a Windows feature + a reboot
   isn't something this session can do on your behalf):
   ```powershell
   wsl --install          # run from an elevated PowerShell; reboots when done
   ```
   Already done on this machine: `google-colab-cli` 0.6.0 is installed in WSL2 Ubuntu via
   `uv tool install google-colab-cli` (see "Status: installed" under Authenticating, below). From
   the WSL terminal -- which sees this repo directly at
   `/mnt/c/Users/hrudi/Documents/BEI/PEAD_Trading/...`, no separate clone needed for local testing:
   ```bash
   colab new -s pead --gpu T4   # first run ever prints an auth URL -- see "Authenticating" below
   ```

   **Important mechanical note:** `colab run <script.py>` and `colab exec -f <script.py>` transmit
   only that ONE file's contents into the remote kernel -- they do not upload a directory. Every
   script here (`sys.path.insert(...); from lib.gpu import ...` / `from common.paths import ...`)
   depends on sibling package directories (`scripts/common/`, and each domain's own `lib/` --
   `scripts/options_strategy/lib/gpu.py`, `scripts/options_strategy/lib/options.py`) actually being
   present on the VM's filesystem at the same relative layout, and an exec'd code string may not
   even have a real `__file__` to resolve those sibling paths from. Don't use the single-file
   `colab run`/`colab exec -f` shortcuts for anything in this repo -- use `colab ssh` for a real
   remote shell instead, which behaves exactly like running the scripts anywhere else (and, being a
   real shell with the full `scripts/` tree present, needs no path changes for the reorganized
   layout beyond the ones already made):
   ```bash
   zip -r pead_scripts.zip scripts/ requirements.txt requirements-gpu.txt
   colab upload -s pead pead_scripts.zip pead_scripts.zip
   colab ssh -s pead                                     # drops into a real shell on the VM
   #   (on the VM:)
   unzip pead_scripts.zip
   pip install -r requirements-gpu.txt                   # torch/numpy/pandas ship with Colab already
   python scripts/options_strategy/42_gpu_exit_optimizer.py --device cuda --mock-data --smoke-test
   exit
   colab stop -s pead
   ```

   **Zero-setup fallback, if you'd rather test something right now without installing WSL2:** open
   any Colab notebook in a browser (you already have Jupyter-based Colab access) with a GPU
   runtime, and paste the same three commands into one cell with `!` prefixes (`!pip install -r
   requirements-gpu.txt`, `!python scripts/options_strategy/42_gpu_exit_optimizer.py ...`, after
   uploading the repo via the notebook's file browser or a Drive mount). This does NOT require converting any script
   to a notebook -- the notebook is just a remote console; the code being run is still the exact
   same `.py` files. It's just less repeatable/scriptable than the WSL2+CLI path (a human has to
   click through the browser each time), which is why the CLI is the recommended path going
   forward, not just a one-off convenience.
3. **The 5090 box** (bare-metal Linux) -- for the real runs. Being native Linux, not
   Windows+WSL, it gets first-class support for the whole NVIDIA stack directly; WSL is a
   Windows-only workaround needed on THIS machine (above), not on the 5090.

**Status: installed.** `google-colab-cli` 0.6.0 is installed inside this machine's WSL2 Ubuntu via
`uv tool install google-colab-cli` (no `sudo`/system changes needed -- `uv` installs to
`~/.local/bin`, already on `PATH` in every new WSL shell via `~/.bashrc`). Run `colab version` from
a WSL terminal to confirm.

**Authenticating (a one-time, interactive step you do yourself -- it's your Google account, so
this can't be done on your behalf):** this installed version's default auth strategy is actually
`oauth2` (`colab --help` shows `[default: oauth2]` -- simpler than GitHub's README text suggests,
and no separate Google Cloud project/`gcloud` setup needed). From a WSL terminal:
```bash
colab new -s pead --gpu T4
```
This prints an authorization URL -- open it in a browser, sign in with the Google account your
Colab Pro subscription is on, copy the code Google shows you, and paste it back at the terminal
prompt. That mints a token cached at `~/.config/colab-cli/token.json`, reused automatically by
every later command (no need to repeat `--auth oauth2` -- it's already the default).

**GPU library choice: pip-only PyTorch + CuPy, not conda/RAPIDS.** The prior GPU scripts this
project's roadmap draws on (copied into `reference/options_content/` -- see that folder's own
README for what's there and why) use RAPIDS (cudf/cupy) for vectorized dataframe joins, but the
actual GPU-bound work in this pipeline (Tier 2's dense position x day x
parameter-combo scan, Tier 3's model training) is array/tensor computation, not database-style
joins -- CuPy arrays and PyTorch tensors cover it completely. Both install with a single `pip
install` and have much better unattended-Linux reliability than a conda-resolved RAPIDS
environment, which matters a lot given the very limited hands-on time budgeted for the 5090 box
(see "Unattended execution" below). See `requirements-gpu.txt`.

**Every GPU script takes `--device {cpu,cuda}` and `--mock-data`** (`scripts/options_strategy/lib/gpu.py`): the
array backend (numpy vs. cupy) is chosen at runtime, so the identical code path is exercised at
every scale with zero code changes, and `--mock-data` fabricates a synthetic panel with the same
schema and the same decile-ordered drift actually measured in `data/option_spread_horizon_stats.csv`
/ a synthetic daily price path with matching statistical character -- so correctness and GPU-batch
behavior can be fully validated (on Colab or here) without ever touching the licensed OptionMetrics
data.

**Every per-year OptionMetrics-scan script (35/36/41/47/47b/49) takes `--jobs N`**
(`scripts/options_strategy/lib/hw.py`): each of those scripts' year loops is embarrassingly
parallel (every year reads its own `opprcd{year}.parquet`/`secprd{year}.parquet`, independently of
every other year), so `--jobs > 1` scans several years at once in separate worker processes,
using more of the box's CPU cores/RAM per stage. The default is capped rather than set to every
core, because on this dev machine those years all compete for one physical external drive's I/O --
raise `--jobs` explicitly once a machine's actual storage (e.g. the 5090's local NVMe) is known to
tolerate more concurrent readers.

### Data portability: the OptionMetrics drive itself never moves data, only itself

The OptionMetrics IvyDB extract lives permanently on one external drive and is never copied
anywhere -- not into this repo, not onto the 5090's own disk. It gets to the 5090 by physically
plugging that drive into it. Two consequences for the code:

- `OM_DIR` (`scripts/options_strategy/lib/options.py`, sourced from `common.paths.OPTIONMETRICS_DIR`) reads from the `OPTIONMETRICS_DIR` environment variable
  (default `D:/OptionMetrics/parquet`, this machine's path) rather than being hardcoded, since the
  mount path is different on Linux (e.g. `/media/<user>/OptionMetrics/parquet` or wherever it
  auto-mounts) -- set the env var once on the 5090 box rather than editing any script.
- The actually-required subset is the `parquet/` subfolder specifically -- **31GB** (measured
  directly with `du`), not the ~100GB this README previously estimated, and much less than the
  full drive's 618GB (which also holds the original `.sas7bdat` files and other conversions this
  pipeline never reads).
- `options_lib.scan_year_for_keys` retries a year's scan up to 4 times with a pause on
  `OSError: Error reading bytes from file` -- an intermittent, non-deterministic read failure
  observed at a *different* (non-corrupt) year on 3 separate runs while building this pipeline's
  own data, almost certainly a USB/controller hiccup rather than file corruption (the one
  genuinely corrupt file is `opprcd2011`, confirmed separately). Left unhandled, this would
  silently kill an unattended multi-day run for no real reason.

### Unattended execution: plug in, kick off, walk away for days

Physical access to the 5090 is limited to short windows, and the real Tier 3/4 runs are meant to
run for days between check-ins. Every stage script is therefore built resumable and checkpointed
rather than "run once, all or nothing":

- Scripts 35/36/41/47/47b/49 write one output file **per year** and skip a year whose output file
  already exists on restart -- a crash or reboot loses at most the year(s) in progress, not the
  whole run. Each of these scripts now also parallelizes its own year loop across `--jobs` worker
  processes (`scripts/options_strategy/lib/hw.py` picks a default; override explicitly on a
  machine whose OM drive can serve more concurrent readers than this project's single external
  HDD).
- `scripts/options_strategy/lib/gpu.py`'s `StageTimer` records start/done/failed + elapsed time for every stage to
  `logs/pipeline_status.json` (`common.paths.LOGS_DIR`, repo-root-relative; cross-process-locked so
  concurrent stages don't race on it), so checking in after a few days means reading one small JSON
  file, not scrolling raw stdout.
- `scripts/options_strategy/run_pipeline.py` is THE single command to kick off before walking
  away: it's not just a sequential runner, it's a hardware-aware SCHEDULER that reads the real
  dependency graph across all 16 stages (35-52) and runs everything that can run concurrently at
  once instead of one stage at a time -- CPU-only stages alongside the one GPU-bound stage that's
  allowed to run at a time, OM-scan stages capped by `--io-parallel` (disk-bound, so this defaults
  to 1 rather than assuming the drive tolerates concurrent readers), all auto-tuned to the box's
  actual CPU core count/RAM/VRAM (`--dry-run` previews the schedule without running anything). It
  skips a stage whose declared output already exists, and is safe to run under `tmux`/`nohup` so a
  dropped SSH session (or none at all -- physical-access-only is fine too) doesn't kill the run.

**5090 box setup:** see [`GPU_SETUP.md`](GPU_SETUP.md) for the complete path from a bare Linux
checkout to a running backtest -- getting the code (`git`), getting the data (the physical drive
now also carries a staged copy of the raw WRDS pull alongside the OptionMetrics extract, via
`scripts/setup_gpu_box.sh`), the Python environment, and the single command
(`scripts/run_all_strategies.py`) that builds every equity strategy AND every options tier,
sequentially, each one given the whole machine -- plus how to read its checkpoint/failure logs if
the box crashes mid-run. `options_strategy/run_pipeline.py` (below) and `equity_strategy/
run_pipeline.py` remain available as the per-domain, concurrent-stage schedulers if you want only
one domain or prefer that concurrency model instead.

## Equity-strategy GPU roadmap

Every strategy above (1-7) tilts and sizes positions off ONE hand-built signal (SUE rank), with
constants (trim, tilt_power, base_unit_fraction, leverage cap) chosen by looking at a handful of
sweep results and picking what looked best. That is exactly the same starting point the options
side was at before its own GPU roadmap (see above): the strategy works, but nothing about it
scales with more compute. Strategy 8 is the equity side going through that same escalation --
four tiers, each only justified by a question the tier before it couldn't answer, reusing the
`common/gpu.py` device backend (numpy on CPU, cupy on GPU, selected by `--device`) the options
pipeline already established rather than inventing a second one:

| Tier | Script | Question it answers | Compute |
|---|---|---|---|
| 1 | `32_gpu_feature_panel.py` | Every prior strategy uses SUE rank alone -- does a firm's OWN trailing momentum/volatility carry extra information, and what's the position's actual realized return (the label a model would need)? | Dense (n_events x 252-day window) trailing gather, GPU-batchable |
| 2 | `33_gpu_walkforward_signal.py` | Can a model combining SUE rank with size/sector/momentum/vol/EAR beat SUE rank alone, trained walk-forward (leak-free) instead of fit once on the whole sample? | One MLP retrain per calendar-year fold, expanding window -- more history each fold, genuinely GPU-bound at full scale |
| 3 | `34_gpu_param_sweep.py` | Tier 1/2 still leave (trim, tilt_power, how much to trust the ML score vs. SUE rank, sizing, leverage cap) to hand-tune -- what does a real grid search find? | GPU-batched: every (base_unit_fraction, leverage) combo simulated as one extra array axis per quarter |
| 4 | `35_finalize_strategy8.py` | Given the sweep's selected combo, what's the real daily-precision backtest (matching every prior strategy's own NAV-loop convention)? | CPU, seconds -- the search already did the expensive part |

### Design choices worth calling out

- **No new dependency for the model.** `33_gpu_walkforward_signal.py`'s MLP is hand-rolled directly
  against the `xp` backend (numpy/cupy) -- forward pass, backward pass, and Adam optimizer all
  written out explicitly -- rather than using torch (which the options side's
  `44_ml_contract_selector.py` already depends on). That means the whole equity GPU pipeline needs
  nothing beyond `cupy`, already in `requirements-gpu.txt` for the options pipeline, and
  `--device cpu --mock-data` works with zero extra installs anywhere numpy already runs.
- **Walk-forward, not fit-once.** Exactly the discipline `options_strategy/48_optimal_contract_selector.py`
  applies to contract selection: each year's predictions come from a model trained only on strictly
  earlier data (expanding window, `--warmup-years` years of history required before the first
  fold), with feature standardization computed from that fold's training window only. Events in
  the warmup window get no ML score and Strategy 8 does not trade them -- the same honest cost the
  options roadmap's Tier 1.5 walk-forward cut pays for its own warmup window.
- **Two different kinds of loop in the sweep, on purpose.** `trim`/`tilt_power`/the ML-blend weight
  change WHICH positions are held, so each combination needs its own weight vector -- a (small)
  Python loop. `base_unit_fraction`/`max_gross_leverage` only rescale and cap an already-fixed
  weight vector, so that axis is the one actually vectorized as an extra array dimension: every
  (sizing, leverage) combination's full quarterly NAV path is simulated in one batch of dense array
  ops per quarter, not one Python-level backtest per combination.
- **Search window vs. holdout, not full-sample.** Combos are ranked by Sharpe on the first
  `--search-frac` (default 70%) of quarters only; the remaining quarters are reported for the
  winning combo but never used to choose it -- the same "don't let the search see the answer"
  logic the options roadmap's Kelly selector was built around, applied here to hyperparameter
  selection instead of contract selection. The sweep itself uses quarterly-granularity P&L to keep
  the grid search cheap; `35_finalize_strategy8.py` reruns only the ONE selected combo at full
  daily precision for the real reported numbers.
- **Every stage is independently `--mock-data`-testable**, same convention as
  `options_strategy/42_gpu_exit_optimizer.py`: each script can fabricate its own synthetic daily
  panel + positions (with a real decile-ordered return spread injected into the mock data, not
  pure noise) rather than requiring the stage before it to have actually been run, so
  `run_pipeline.py --mock-data --smoke-test` exercises the full 32->33->34->35 chain in seconds
  with no raw WRDS data or GPU present. **Status: this has been run and passes** on this CPU-only
  dev machine; a real run (real WRDS data, ideally a real GPU) has not been done here -- see
  "Data" below for why, and the "Currently recommended" note at the top of this README.

### `run_pipeline.py`: using the actual hardware, not just running scripts one at a time

The README's own "Run order" section above shows every script invoked one at a time -- correct,
but it leaves a dedicated multi-core/GPU machine mostly idle, since most of that run order is not
actually a dependency chain, just the order the strategies were historically built in. Once
`16_positions_v2.py` has written `positions_rankweighted_v2.parquet`, scripts 17-22 and 26-28 (and,
for Strategy 8, 32) all read only THAT one file (+ the raw equity panel) and write to entirely
distinct output files -- nothing about running `21_leverage_and_improvements.py` requires
`20_strategy4_tilted.py` to have finished first, they just happen to be numbered in research order.

`scripts/equity_strategy/run_pipeline.py` is a single command that runs every stage whose
dependencies (built from what each script actually reads/writes, not its number) are satisfied,
concurrently, as real OS-level subprocesses:

- A `ThreadPoolExecutor` drives up to `--max-workers` stages at once (default `os.cpu_count()`) --
  each worker thread just blocks inside `subprocess.run`, releasing the GIL for the stage's whole
  runtime, so the actual parallel work happens across separate OS processes/cores, not threads
  fighting each other for the interpreter lock.
- Each subprocess gets `OMP_NUM_THREADS`/`MKL_NUM_THREADS`/`OPENBLAS_NUM_THREADS`/
  `NUMEXPR_NUM_THREADS` set to a fair share of the machine's cores, so N concurrently-running
  stages don't each try to grab every core for their own numpy/pandas BLAS calls and thrash each
  other -- this is the actual "use the cores and threads properly" mechanism.
- The three GPU-tiered stages (32-34) are additionally capped to at most ONE running at a time --
  there is exactly one physical GPU to share -- while still running concurrently alongside
  unrelated CPU-only stages.
- A stage whose declared output files already exist is skipped (`--force` to override); if a stage
  fails, only the stages that actually (transitively) depend on it are skipped -- the rest of the
  independent graph still finishes rather than the whole run stopping on one broken branch.
- Per-stage stdout/stderr goes to its own file under `logs/equity_pipeline/<stage>.log` (concurrent
  stages writing to one shared log would interleave into something unreadable), with a run-level
  summary at `logs/equity_pipeline_status.json`.

```bash
# real run (needs the raw WRDS pull under data/normalized_equity/, data/events/, etc.):
python scripts/equity_strategy/run_pipeline.py --device cuda

# validate the whole Strategy 8 GPU pipeline (32-35) in seconds, no raw data or GPU needed --
# 11-31 have no --mock-data path (they need real data unconditionally) and are skipped in this mode:
python scripts/equity_strategy/run_pipeline.py --device cpu --mock-data --smoke-test

# also build 29-31 (charts + PDF reports) -- needs data/results_summary_v2_FINAL.csv, which is
# manually curated (not generated by any script), so this is opt-in rather than default:
python scripts/equity_strategy/run_pipeline.py --device cuda --include-reports
```

## Data

Raw and large intermediate data files are not tracked in this repo (see `.gitignore`) -- they are
either the original WRDS pull (`data/raw_wrds/`, `data/normalized_equity/`, `data/earnings/`,
`data/events/`) or regeneratable derived output (`data/*.parquet` event/position files, tens of
MB each, rebuilt by running the scripts above in order). Small result files that document actual
findings (backtest CSVs, summary JSONs, sweep results, the decay-day cell table) are tracked under
`data/` since they're lightweight and are the actual evidence behind every claim in this README
and in the conversation history that produced it.

The options pipeline additionally reads raw OptionMetrics IvyDB files from an external drive (path
configurable via `OPTIONMETRICS_DIR`, see `scripts/common/paths.py` and the GPU roadmap section
above -- 31GB in the `parquet/` subfolder this pipeline actually reads, not tracked in this repo,
not reproducible from anything checked in here, and never copied anywhere, including onto this
repo's own disk) and writes its own regeneratable intermediates to `data/event_options/`. In the
main checkout, `data/events/`, `data/earnings/`, `data/metadata/`, and `data/results/` are NTFS
junctions to keep the raw WRDS pull from being duplicated on disk; an individual worktree may
instead hold real copies of those folders (as one used to build the GPU pipeline above did), so
the whole pipeline is runnable from inside that one directory tree without depending on the main
checkout's junction targets -- check `data/events/` etc. for junction vs. real-copy status in any
given checkout before assuming either.

## Requirements

See `requirements.txt`. Python 3.11+, pandas, numpy, reportlab (PDF report generation). Both
GPU-tiered pipelines -- options-strategy (scripts 40+) and equity-strategy Strategy 8 (scripts
32-35) -- additionally need `requirements-gpu.txt` (PyTorch, CuPy, Optuna) only on a machine
actually running their GPU tiers; every script in both pipelines still runs on `--device cpu` with
just the base requirements (equity's `33_gpu_walkforward_signal.py` needs only CuPy of the three,
since its model is hand-rolled against the numpy/cupy backend rather than PyTorch).
