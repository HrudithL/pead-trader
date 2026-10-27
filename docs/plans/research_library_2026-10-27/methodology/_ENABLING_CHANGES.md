# Methodology area: master register of enabling changes (REF) and build order
Written 2026-10-27. De-duplicated across MT-01..MT-14 and reconciled with the other areas' registers (`equity/_ENABLING_CHANGES_A.md`, `_B.md`, `options/_ENABLING_CHANGES.md`, `overlays_adjacent/_ENABLING_CHANGES.md`). Facts cited were verified in the repo on 2026-10-27. Regression risk is stated against the NAV pins in `tests/test_framework.py` (five corrected NAVs, 22 legacy twins) unless noted.

## 1. How to read this
Every REF is implemented once. Where another area already named the same thing, the table gives both names and the single recommended home. "Blocks" lists what cannot start (or cannot be graded) before the item exists.

## 2. REF register
| ID | What | Home (file) | Needed by | Regression risk | Blocks |
|---|---|---|---|---|---|
| REF-pit-test-kit (= REF-synth-fixtures OV, `tests/pit_kit.py` EQ-08) | Synthetic panel/events fixtures, mutation twins, `KNOWN_LEAKY` registry, pytest markers (`data`, `slow`, `reference`) | `tests/pit_kit.py`, `tests/conftest.py` | MT-01, 08, 09, 10, 11; EQ-08; all OV; every new strategy | none | MT-01 and every gate |
| REF-outcome-firewall | `OUTCOME_COLUMNS` and a wrapper so `weights(events)` cannot see `hold_ret`, `ret_fwd_60d_mktadj`, `exit_date`, `exit_row_idx`, `holding_days`, `holding_cell` unless declared | `pead/core/outcomes.py`, `pead/strategies/base.py::EquityStrategy.backtest` | MT-01 LA-01/02, MT-13 (new outcome columns), EQ feature tables | LOW-MEDIUM: `vol.walk_forward_cell_filter` needs `hold_ret`/`exit_date`; allow-list or NAV pins break | every strategy promotion |
| REF-strategy-iface | Strategy base-class contract additions: `causal`, `outcome_columns_allowed`, `search_params`, `cost_model`, `quantity`, `variant(**kw)`, `required_columns` incl. flavour columns | `pead/strategies/base.py` | MT-01, 04, 06, 09, 12; EQ `exit_rule` / `entry_lag_sessions`; OV `exposure_overlay`; OPT option strategies | LOW (defaults); update `test_every_strategy_declares_metadata` | MT-04 gate, MT-09 |
| REF-event-time-layer (= REF-anntims-audit EQ, REF-event-clock OPT, IMP-anntims) | `pead/core/event_time.py`: `first_executable_session`, `timing_class`, `quarter_of`, `report_lag_days`, `assert_event_invariants`; materialised as `event_clock.parquet` shared by equity and options | `pead/core/event_time.py`; `pead/prep/events_audit.py` | MT-02, 07, 11 R7, 13; EQ-02/03/22-27; OPT-02/04; live engine | HIGH when `quarter_of` is applied (208 events change bucket; re-pin) -- stage in a separate vintage first | MT-07, MT-13, options timing |
| REF-ibes-builder | Recreate missing builders of `ibes_clean_1995_2014.parquet`, `equity_events_1996_2013.parquet`, `event_classification_features.parquet` under `pead/prep/earnings/` (today none is in `pead/prep/stages.py`; scratch logic in git-ignored `earnings_build.tmp/data.py`) | `pead/prep/earnings/*` | MT-02 E2/E18, MT-07; evidence grade A | none if stored files stay authoritative until bit-exact | grade A/B, MT-07 provenance |
| REF-calendar | Single trading-calendar helper (market-benchmark dates, `searchsorted` rules, half-day list) used by event-time layer, engine, options | `pead/core/calendar.py` (wraps `data.py::_load_panel` calendar and `om_trading_calendar.parquet`) | MT-02, 13; OPT `trading_calendar.py` | none | REF-event-time-layer |
| REF-panel-delist (= REF-panel-hygiene OV; EQ IMPROVE delisting) | Compound `dsedelist.dlret`, explicit missing-return mask instead of `fillna(0.0)`, one panel builder shared by `core/data.py` and `prep/equity/lib/panel.py` | `pead/core/panel.py` | MT-05; EQ-04/05 (stops, shorts); OV-06 | MEDIUM: NAVs move slightly; flag-gated, then re-pin | MT-05 ladder |
| REF-dsf-reader (EQ) = `panel_quotes` stage (MT-06/13) | Read `openprc, bid, ask, numtrd` from raw `dsf_YYYY.parquet`, aligned to the (permno,date) panel order; shared cache | `pead/prep/equity/panel_quotes.py`, `pead/core/data.py` | MT-06 spread, MT-13 open fills; EQ-02/03/26 | cache-key change only | MT-06, MT-13 |
| REF-event-key (EQ) | Unique key `(permno, day0_date, dup_rank)`; 19 duplicates among analyst events, 105 in `positions_rankweighted_v2` | prep stages + loader | MT-01 LA-18, MT-02 E25; EQ side tables | none if side-keyed | REF-event-feature-store |
| REF-cost-hooks (= REF-cost-model / REF-cost-source / REF-borrow-fee) | `CostModel` protocol; `LegacyQuintileCost` bit-identical; spread/impact/borrow/financing components; options friction params | `pead/costs/`, `pead/engines/equity.py` lines ~169, 220-232 | MT-06, MT-11 R5, MT-04 G3; EQ-01/03/04/05/26/27; OPT; live | MEDIUM: hottest code; guard = all pins unchanged with legacy model | cost stress, short-leg strategies |
| REF-metrics-v2 | `pead/core/stats.py`: active window, CAGR, Sharpe, Sortino, drawdown incl. initial NAV, PSR, DSR, block bootstrap, HAC alpha (multi-factor), era tables; `summarize` delegates | `pead/core/stats.py`, `metrics.py`, `alpha.py`, `store.py` | MT-03, 04, 08, 09; EQ-30; OV; paper | MEDIUM: headline Sharpe/CAGR restate (balanced_leverage Sharpe 1.092 -> 1.129 and CAGR 5.38% -> 5.76% on the active window); keep legacy keys as `*_fullcal` | MT-04, leaderboard v2 |
| REF-stats-nodeps (EQ) | numpy-only IC, clustered OLS, BH-FDR | `pead/core/event_stats.py` | EQ-30 etc. | none | -- (shares `_ols_hac` with REF-metrics-v2) |
| REF-tbill-real (= REF-rf-series) | Real daily risk-free series replacing typed-from-memory annual averages (`pead/core/rates.py`) | `pead/core/rates.py`, `data/external/` | MT-03 excess Sharpe, MT-06 financing; OV-03; OPT IMP-rates | MEDIUM: `steady_drift*` cash legs re-run | any cash-leg result quoted externally |
| REF-factor-data | Daily FF5 + momentum factors (WRDS `ff` schema visible; verify entitlement) or Ken French download (needs user approval) | `data/external/`, `pead/core/alpha.py` | MT-03, MT-09; OV-02 | none | multi-factor alpha claims |
| REF-trial-registry (= trial CSVs of OV REF-variant-grid and OPT REF-search-holdout) | Append-only `results/_trials.jsonl`, `trial_scope`, backfill | `pead/eval/registry.py` | MT-04, 03 (DSR), 12; every sweep | LOW (runner hook; concurrent writers) | DSR, leaderboard v2 |
| REF-search-holdout (= REF-period-split EQ) | One `splits.py` reading `protocols/v1.json`: `search_mask`, `split_windows`, `purged_folds`. DECISION NEEDED: 70% of announcement quarters = 1996Q2..2008Q2 (code-backed, 49 of 71) vs EQ-30's `day0 <= 2005-12-31` (39 of 71) | `pead/core/splits.py`, `pead/eval/protocols/v1.json` | MT-04, 08, 09, 11; EQ-30; OPT-02..06; OV | none (new); `daily_decay.py:52` reads the same fraction | all validation |
| REF-eval-harness | `python -m pead eval|freeze|trials`, gates G1-G8, leaderboard v2 | `pead/eval/`, `pead/core/store.py::update_leaderboard`, `pead/cli.py` | MT-04; gates every new strategy | MEDIUM: `paper.py`/`comprehensive.py` read `leaderboard.json`/`summary.json` | promotion of any strategy |
| REF-validation-suite (OV-01 owner) | `pead/validation/`: stress (LOO), placebo, paired bootstrap, era splits; CLI `stress`/`validate` | `pead/validation/*` | MT-08, 09, 11 provide the specs; OV-01..07 | none | MT-04 G6-G8 |
| REF-placebo-runner (= REF-placebo) | Importable shuffled-signal placebo (>=200 seeds), within (quarter, size) variant, p-values | `pead/validation/placebo.py` (re-export from `pead/core/placebo.py` if EQ insists) | MT-11, EQ-30 and others | low: parity run vs `meta/steady_placebo.py` (3 seeds) | MT-04 G8 |
| REF-entry-lag (EQ-B) = MT-11 `with_entry_shift` | `with_entry_lag(uni, k)`, negative k allowed for time-inversion; rewrites `entry_row_idx` and `day0_date` | `pead/engines/equity.py` | MT-11 R1/R2; EQ-23 | none at k=0 | MT-11 |
| REF-entry-leg (EQ-A) = MT-13 `entry_fill='open'` | Day0 partial-return leg for open entries, raw basis | `pead/engines/equity.py` | MT-13; EQ-03 | none for `close` | MT-13 |
| REF-signal-flavors | `pead/signals/sue.py` + flag bitmask + experiment E1 | `pead/signals/` | MT-07; EQ-02/06/21/22; live features | none by default | flavour use in promoted strategies |
| REF-reference-oracle | Library `pead_reference.py` copied byte-identical into `tests/reference/` + adapters | `tests/reference/` | MT-10, 03, 07; future multi-leg ledger | none | accounting grade A |
| REF-run-manifest | `manifest.json` per run, evidence grade, quantity taxonomy, hash cache, results schema v2 | `pead/core/manifest.py`, `store.py::save_result` | MT-12, 04; leaderboard | LOW (additive); `load_result` must tolerate absence (66 folders) | grade A/B/C |
| REF-ablation-harness | Ladders, event-set pairing, matched-vol comparison, paired bootstrap orchestration | `pead/eval/ablation.py`, ladders YAML | MT-09; OV-02; EQ studies | none | paired claims |
| REF-ledger-recon | Position-level to daily NAV reconciliation test (`sum(positions.net_pnl)`), exposure invariants | `tests/test_accounting.py` | MT-01 LA-12/13 | none | MT-01 |
| REF-multileg-options (= REF-leg-engine + REF-fill-model OPT) | Multi-leg options position model: signed legs, per-leg bid/ask fills, commissions on leg-sides, two-sided liquidation, assignment hooks. Oracle: reference `option_debit_spread_pnl(4,1,5,1.5) == 47.4` | `pead/engines/fills.py`, `options_structures.py` (OPT) | OPT-02/04/05/06; MT-06 options friction; MT-10 XV-08 | HIGH for the extraction from `run_options_backtest` (guard: `options_cap20` 10,481,798.4 and `options_cap30` NAVs) | any spread/straddle strategy |
| REF-data-gaps | `data/metadata/data_gaps.csv` + `python -m pead data-gaps --check`; external-download stage kind that never runs under `--all` | `pead/core/gaps.py`, `pead/prep/stages.py` | MT-14; all areas | none | approvals for downloads |

## 3. CHANGE register (existing code; one line each; risks as above)
1. `pead/core/metrics.py::summarize/drawdown` (active window, initial-capital drawdown) -- MEDIUM presentational.
2. `pead/core/alpha.py::alpha_stats` (delegate; HAC auto lag; factors) -- LOW.
3. `pead/core/store.py::save_result/_git_rev/update_leaderboard` (manifest, dirty flag, leaderboard v2, quantity guard) -- MEDIUM for consumers.
4. `pead/core/data.py::_load_panel/_load_events` (dlret, missing mask, cache key, event side tables) -- MEDIUM, re-pin NAVs.
5. `pead/engines/equity.py::run_equity_backtest` (cost hook, carry, entry leg/lag, `entry_fill`) -- the hottest edit; every change gated by "bit-identical when defaults".
6. `pead/strategies/base.py` (interface additions; outcome firewall; `variant`).
7. `pead/prep/equity/build_deciles.py:120-133` (quarter bucket, retained untradable events, flavour parameter) -- HIGH for pins; separate vintage first.
8. `pead/prep/equity/daily_decay.py:52` (read protocol fraction) -- none.
9. `pead/runner.py::_run_one/run_strategies` (trial logging, job specs beyond registered names) -- LOW-MEDIUM.
10. `pead/reporting/paper.py::inference_section` (import DSR from `stats`, registry trial counts) -- changes published DSR numbers.
11. `pead/reporting/attribution.py::borrow_cost_sensitivity` (hard-coded `1e7`, delegate to cost model).
12. `pead/core/rates.py` (real series).

## 4. RESTRUCTURE register
- Package layout: add `pead/eval/` (protocol, registry, harness, ablation), `pead/validation/` (OV-owned stress/placebo/bootstrap), `pead/costs/`, `pead/signals/`, `pead/prep/earnings/`; `pead/core/` gains `event_time.py`, `calendar.py`, `panel.py`, `splits.py`, `stats.py`, `manifest.py`, `outcomes.py`. Keep `pead/research/` an archive (nothing imports it; AGENTS.md).
- Tests: split into `tests/unit` (fast, synthetic), `tests/repro` (vintage pins), `tests/reference` (oracle), with markers; `tests/legacy_vintage.py` unchanged.
- Results schema v2: `summary.json` (v1 keys preserved) + `manifest.json` + `evaluation.json` + `frozen.json` + `redteam.json` + `concentration.csv` + `cost_sweep.csv`; global `results/_trials.jsonl`; `leaderboard.json` `schema_version: 2`.
- Config handling: env vars read at import time (FIXES.md) -> recorded in the manifest; add `EXTERNAL_DIR` to `pead/common/paths.py`; one protocol file instead of scattered constants (`SEARCH_FRAC`, `ERAS`, cost dict, sweeps).
- CLI: add `check`, `eval`, `freeze`, `trials`, `stats`, `costs`, `ablate`, `redteam`, `manifest`, `grade`, `audit`, `decomp`, `data-gaps` as thin subcommands of `pead/cli.py`; prep stays under `prep`.
- Status vocabulary: keep `validated|research|reference|unverified` (AGENTS.md meaning unchanged); add orthogonal `V` level and `evidence_grade`.

## 5. IMPROVE register (weaknesses to fix)
1. Headline metrics include 316 flat warm-up days (balanced_leverage) and trailing flat tail; cash books start at day 0 -> not comparable (MT-03).
2. Leaderboard = raw argmax; no uncertainty, window or trial count (MT-04).
3. No untouched holdout exists; all constants chosen on the full sample (FIXES D6) -> only `burned` holdouts until data after 2013 (MT-04, MT-14 D15).
4. Event/IBES/classification builders absent from the repo; 97.6% EPS basis assumed (MT-02, MT-07).
5. Outcome columns visible to strategy code (MT-01).
6. Delisting returns unused; missing returns zero-filled (MT-05).
7. Costs static and uncalibrated; no borrow/impact/financing in the engine (MT-06).
8. T-bill series typed from memory (MT-14 D12).
9. Local but unused data: `openprc`, `bid/ask`, `rdq`, `dsedelist`, `detu_epsus` (MT-14 D5-D8, D2).
10. Tests skip silently without vintage data; reference oracle absent (MT-01, MT-10).
11. Placebo uses 3 seeds; sweeps and ablations are scripts in `meta/` with private engine wrappers (MT-09, MT-11).
12. Reproducibility metadata: git short hash without dirty flag; data hashes absent; env vars unrecorded (MT-12).

## 6. Recommended build order (with blockers)
Wave 0 (no behaviour change; unblock everyone; 1-3 days)
1. REF-pit-test-kit + REF-ledger-recon + MT-01 LA-04/05/06/07/12/14 (tests only).
2. REF-reference-oracle (MT-10 XV-01/09) and REF-data-gaps (MT-14 register published).
3. REF-metrics-v2 additive (`stats.py`, `restat_all.py` over the 66 results) -- publishes restated metrics without touching existing outputs.
4. REF-trial-registry + backfill (read-only).
Wave 1 (gates; 3-6 days)
5. REF-strategy-iface + REF-outcome-firewall + MT-01 LA-01/02/03/20 (report-only first).
6. REF-search-holdout (decide the split) + REF-eval-harness skeleton (`freeze`/`evaluate` producing V0/V1, leaderboard v2 behind a flag).
7. REF-event-time-layer read-only audit stage (MT-02 phase 1) + REF-calendar.
Wave 2 (data and engine hooks; each in its own vintage then re-pin)
8. REF-panel-delist (MT-05) and REF-dsf-reader / `panel_quotes` (shared by MT-06/13).
9. REF-cost-hooks with `LegacyQuintileCost` (zero numeric change), then spread/impact/borrow components.
10. REF-metrics-v2 switch of `summarize` to the active window + REF-run-manifest (additive) + leaderboard v2 switch.
11. MT-02 phase 3 (quarter bucket, retained events) -- the only change that moves deciles; do last among data fixes; one re-pin.
Wave 3 (research instruments)
12. REF-validation-suite pieces (LOO, placebo with 200 seeds, paired bootstrap) with MT-08/11/09 specs; REF-entry-lag; REF-ablation-harness.
13. REF-signal-flavors + REF-ibes-builder; REF-entry-leg / MT-13; REF-factor-data and REF-tbill-real once downloads approved.
14. REF-multileg-options (OPT-owned) with the reference's 47.4 oracle.

Dependency edges (A -> B means A blocks B): pit-test-kit -> {outcome-firewall, validation-suite, ablation-harness}; metrics-v2 -> {eval-harness, ablation-harness, validation-suite}; trial-registry -> {eval-harness DSR gate}; search-holdout -> {eval-harness, all OPT/EQ/OV validation}; event-time-layer -> {signal-flavors, entry-leg, ibes-builder verification}; panel-delist + dsf-reader -> {cost-hooks spread model, entry-leg}; cost-hooks -> {eval-harness G3, EQ short-leg and stop strategies}; strategy-iface -> {outcome-firewall, ablation `variant`, manifest}; eval-harness + manifest -> promotion of any new strategy from any area.

## 7. Decisions needed from the owner
1. Split definition (70% quarters = 1996Q2..2008Q2, recommended, vs `day0 <= 2005-12-31`).
2. Approve downloads: daily risk-free (FRED or WRDS `crsp_a_treasuries`), FF5+MOM (Ken French or WRDS `ff`), optional EDGAR 8-K times.
3. Approve restating headline Sharpe/CAGR on the active window (presentational) before any paper regeneration.
4. Whether to commit the library's reference files (licence statement not verified) or keep them downloaded-on-demand.
5. Pre-registered gate constants in `protocols/v1.json` (G3-G8 thresholds are proposals).
