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

  equity_pead/            # "does PEAD exist in equities?" -- v1 evidence pipeline + PEAD_Report.pdf
  equity_strategy/         # backtest engines, all 8 strategies, and the two showcase/development reports
    lib/positions.py        # size_balance(), shared by the two position-construction scripts

  options_pead/            # "does the drift show up in options?" -- evidence pipeline + PEAD_Options_Report.pdf
  options_strategy/         # option contract selection + forward pricing (the substrate for a future options backtest)
    lib/options.py           # streamed/filtered opprcd scans, contract picker (moved from options_lib.py)

  legacy/
    05_build_pdf.py         # original cloud-sandbox evidence report, superseded by equity_pead/32
```

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

**Note on the reorganization itself:** the numbered scripts were moved into the domain folders
above (and their internal duplication -- `fama_macbeth`, the PDF report boilerplate, matplotlib
styling, path constants -- was consolidated into `common/`) without changing any algorithm, weight
formula, or NAV/Sharpe calculation. `04, 07, 13, 29` (charts) and `30, 31, 32` (reports) were
re-run after the move and diffed against the previously committed output to confirm no drift;
the rest of the pipeline needs the raw WRDS/OptionMetrics pulls to re-verify end-to-end, which
aren't present in every checkout. Two already-built PDFs' embedded source citations still name
the old flat `scripts/NN_....py` paths rather than the new nested ones, because regenerating them
needs data this repo doesn't always have on hand: `PEAD_Options_Report.pdf` (needs
`data/event_options/`) and `scripts/legacy/05_build_pdf.py`'s own historical output (superseded
regardless, see above). Their source code is already updated; only those two already-built PDF
files are stale until someone reruns them with full data access.

## Reports

Four PDFs in `reports/`, meant to be read in this order:

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

## Data

Raw and large intermediate data files are not tracked in this repo (see `.gitignore`) -- they are
either the original WRDS pull (`data/raw_wrds/`, `data/normalized_equity/`, `data/earnings/`,
`data/events/`) or regeneratable derived output (`data/*.parquet` event/position files, tens of
MB each, rebuilt by running the scripts above in order). Small result files that document actual
findings (backtest CSVs, summary JSONs, sweep results, the decay-day cell table) are tracked under
`data/` since they're lightweight and are the actual evidence behind every claim in this README
and in the conversation history that produced it.

The options pipeline additionally reads raw OptionMetrics IvyDB files from `OPTIONMETRICS_DIR`
(`scripts/common/paths.py`; defaults to `D:/OptionMetrics/parquet/`, an external drive, ~100GB, not
tracked in this repo and not reproducible from anything checked in here -- override the env var to
point at a different mount, e.g. a Colab Drive mount) and writes its own regeneratable
intermediates to `data/event_options/`. In this git
worktree, `data/events/`, `data/earnings/`, `data/metadata/`, and `data/results/` are NTFS
junctions to the corresponding folders in the main checkout rather than copies, so the raw WRDS
pull isn't duplicated on disk.

## Requirements

See `requirements.txt`. Python 3.11+, pandas, numpy, reportlab (PDF report generation).
