# Master roadmap: implementing the research-library plans

Written 2026-10-27. Sequencing across MT / EQ / OPT / OV specs. Detailed refactor dependencies are in [`ENABLING_CHANGES_MASTER.md`](ENABLING_CHANGES_MASTER.md); this file says what order to do things in and why.

## Principle

The library's biggest transferable lesson is methodological, not a strategy: published PEAD results fail through look-ahead, bad accounting, cherry-picked windows and unmodeled costs. Our repo has some of the same weaknesses (see README key findings). So the order is: **measurement and guard rails first, then signal measurement, then strategy candidates, then options, then blends.** A new strategy only counts if it passes the MT-04 gates on the search/holdout protocol.

Expectation, stated honestly: the equity book is already near what these sources support (`steady_drift_plus` 12.1% / Sharpe 1.19, long-only, no borrowing). Most library ideas have low to moderate priors of beating it, and several were already tested in `meta/`. Value is mostly in (a) a more trustworthy evaluation of what we already have, (b) a few cheap-to-test signal/overlay variants, (c) a decisive answer on whether options can work at all under ask-in/bid-out fills.

## Phase 0: Guard rails, no behaviour change (about 1 week)

| Step | Specs / REFs | Output |
|---|---|---|
| 0.1 | REF-pit-test-kit, REF-ledger-recon, MT-01 (tests only), EQ-08 | Synthetic fixtures, mutation twins, `KNOWN_LEAKY` registry, ledger-to-NAV reconciliation test |
| 0.2 | REF-reference-oracle (MT-10) | Library reference code and its 8 tests running against our engines as an oracle |
| 0.3 | MT-14 / REF-data-gaps | Published data-gap register; list of downloads needing approval |
| 0.4 | REF-metrics-v2 additive (MT-03) | `stats.py` and restated metrics for all 66 existing results, side-by-side with originals |
| 0.5 | REF-trial-registry + backfill (MT-04) | Append-only trial log so Deflated Sharpe can count what we tried |

Gate: all existing NAV pins unchanged.

## Phase 1: Gates and PIT hardening (1-2 weeks)

| Step | Specs / REFs | Notes |
|---|---|---|
| 1.1 | REF-strategy-iface, REF-outcome-firewall, MT-01 LA-01/02/03 (report-only, then enforce) | `vol.walk_forward_cell_filter` needs an allow-list or pins break |
| 1.2 | **Decision D1** then REF-search-holdout, REF-eval-harness skeleton (MT-04) | `python -m pead eval/freeze/trials`, leaderboard v2 behind a flag |
| 1.3 | REF-event-time-layer read-only audit (MT-02 phase 1), REF-calendar, REF-ibes-builder | Audit report only; do not change deciles yet |
| 1.4 | REF-panel-delist (MT-05), REF-dsf-reader / `panel_quotes` | Separate data vintage, then one re-pin |
| 1.5 | REF-cost-hooks with `LegacyQuintileCost` (bit-identical), then spread/impact/borrow components (MT-06) | Hottest code in `engines/equity.py`; guard with pins |
| 1.6 | REF-run-manifest (MT-12), switch `summarize` to the active window | Restates headline numbers (needs **D3**) |
| 1.7 | MT-02 phase 3 (quarter bucket, retained events), the only change that moves deciles | Last among data fixes; one re-pin; stage in its own vintage |

Gate: re-run the four `steady_drift*` strategies and `balanced_leverage` under the new data/cost/metrics stack; publish a before/after table. This is the new honest baseline every later spec compares to.

## Phase 2: Measurement instruments (1-2 weeks, can overlap Phase 1 from 1.2)

EQ-30 (signal evaluation harness), EQ-22 (reaction/gap/volume features), EQ-02 (event feature table), EQ-21 / MT-07 (SUE variants), MT-08 + OV-01 (leave-one-out / concentration), MT-11 (red-team and placebo, REF-placebo with 200+ seeds, REF-entry-lag), MT-09 (ablation harness). Then use them on the **existing** books first: LOO and placebo on `steady_drift_plus`.

Gate: instruments reproduce known results (placebo reproduces `meta/steady_placebo.py`; harness reproduces known SUE decile spreads).

## Phase 3: Equity candidates (each is a registered `research` strategy, evaluated with the harness)

Order by expected value per effort:
1. EQ-21 SUE variants and EQ-06 seasonal SUE as rank inputs (pure signal, cheap).
2. EQ-23 reaction-confirmed `steady_drift_plus` and the entry-lag test; EQ-24 overnight-gap surprise; EQ-28 era interaction / decay-aware exposure.
3. EQ-04 path exits/stops (watch the leverage-cap leakage described in the spec), EQ-01, EQ-05 long leg, EQ-03 / MT-13 next-open entry, EQ-26 spread costs.
4. OV-02 SUE x momentum, OV-03 trend/breadth gate (must beat a matched-exposure baseline and random gates), then OV-04/07 if earlier ones show promise.
5. P2 and blocked items only if capacity remains (EQ-07/25/27/29, OV-05/06).

Rules: constants chosen on the search window; every sweep logged in the trial registry; a null result is recorded in `results/` and the spec, not discarded.

## Phase 4: Options

1. OPT-01 first and alone: multi-leg position model (REF-multileg-options, oracle `option_debit_spread_pnl(4,1,5,1.5) == 47.4`), fill model extracted from `run_options_backtest` with `options_cap20`/`cap30` NAVs bit-identical, and the **spread/liquidity report** for ATM, ITM and short-dated contracts (currently unmeasured).
2. **Go/no-go gate** on that report. If cost-to-edge says no, stop and record it; do not build OPT-03..06.
3. If go: OPT-02 (post-reaction calls), then OPT-03 (call as stock substitute), OPT-04 (pre-earnings straddle; needs a pre-event chain the current extract lacks, so it may require a new OptionMetrics pull), then OPT-05/06.

## Phase 5: Blends and promotion

Re-run `hedged_blend`-style combinations only with sleeves that passed the gates, with blend weights chosen on the search window. Update `docs/ROADMAP.md`, `docs/REPO_MAP.md`, and the paper only after promotion.

## Decisions needed from you before the relevant phase

| # | Decision | Recommendation | Needed by |
|---|---|---|---|
| D1 | Search/holdout split: 70% of announcement quarters (1996Q2..2008Q2) vs `day0 <= 2005-12-31` | 70% rule (already used by the holding-period fit). Note: the holdout then starts at the financial crisis and is already contaminated; treat as "burned" until post-2013 data exists | Phase 1.2 |
| D2 | Approve downloads: daily risk-free series, Fama-French 5 + momentum, optional EDGAR 8-K times, Compustat cash-flow pull (for OV-05), post-2013 CRSP/IBES | Approve risk-free + FF; defer others | Phase 1 / 3 |
| D3 | Restate headline Sharpe/CAGR on the active window (excludes flat warm-up days) | Yes, keeping the old numbers as `*_fullcal` | Phase 1.6 |
| D4 | Commit the library's reference files into the repo or fetch on demand (licence unverified) | Fetch on demand | Phase 0.2 |
| D5 | Gate thresholds G3-G8 in `protocols/v1.json` are proposals | Review before freezing | Phase 1.2 |
| D6 | Whether to pull post-2013 data at all | Strongly recommended: it is the only way to get a real holdout | Before any promotion |

## Risk register (program level)

- **Re-pinning risk:** changes to panel, quarter bucketing and cost hooks move committed results. Mitigation: separate vintages, flag-gated defaults, one re-pin per change class.
- **Multiple testing:** about 40 specs, many with parameters. The trial registry and DSR gate exist to stop us fooling ourselves.
- **No independent holdout** until post-2013 data is added.
- **Scope creep:** Phases 0-2 are the largest share of the value; do not skip them to chase strategies.
- **Options:** may be a clean "no" and that is an acceptable outcome.
