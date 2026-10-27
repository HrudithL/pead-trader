# Enabling changes consolidated from specs EQ-21 .. EQ-30 (batch B)

De-duplicated; every item names the specs that need it and the real code it touches (verified in the repo on 2026-10-27). Suggested order at the end. Regression gate for anything touching shared code: `python -m pytest tests -q` (incl. `tests/test_fixes.py::test_default_vintage_pins_corrected_final_nav` and `tests/test_framework.py`) plus a before/after NAV diff of the four `steady_drift*` strategies; default paths must stay bit-identical.

## REF items (restructure)
| ID | What | Where | Needed by | Regression risk |
|---|---|---|---|---|
| REF-period-split | One declared search window (day0 <= 2005-12-31) and holdout (2006-01-01 .. 2013-12-31) constants plus helpers | new `pead/core/splits.py`; `pead/core/metrics.py::ERAS` stays for reporting | all (EQ-30 owner) | none |
| REF-stats-nodeps | numpy-only Spearman IC, Newey-West, two-way clustered OLS, block bootstrap, BH-FDR (statsmodels absent) | new `pead/core/event_stats.py` (reuse `pead/core/alpha.py::_ols_hac` pattern) | EQ-30, 28, 25, 21, 24, 29 | none |
| REF-placebo-runner | Move shuffled-surprise placebo from `meta/steady_placebo.py` (uses `from steady_grid import *`, sys.path hack) into the package, generalised to any signal column | new `pead/core/placebo.py` | EQ-30, 21, 23, 24, 25, 28, 29 | low; parity-run vs prototype |
| REF-event-feature-store | Side tables under `data/event_features/*.parquet` left-joined onto events instead of widening `equity_vol_panel.parquet` each time | `pead/core/data.py::_load_events`, `pead/prep/stages.py::STAGES`, `docs/REPO_MAP.md` section 6 | EQ-22, 21, 23, 24, 25, 26, 27, 29 | low if row count/order asserted equal; pin test |
| REF-event-key | Unique event key: 105 duplicate `event_id`s (docs/FIXES.md; verified); `equity_vol_panel` lacks `fpedats`/`ibes_ticker`. Use (permno, day0_date, dup_rank) computed identically in all stages; do NOT change `pead/prep/equity/positions.py::KEEP_COLS` (forces a full rebuild and re-pin) unless the owner approves | prep stages + loader | EQ-22, 21, 24, 25, 26, 29 | none if side-keyed |
| REF-dsf-reader | Shared reader of raw CRSP dsf columns (openprc, bid, ask, vol, numtrd, cfacpr) aligned to the panel's (permno,date) row order, memoised per year | new `pead/prep/equity/lib/dsf.py` (beside `lib/panel.py::load_mktadj_panel`) | EQ-22, 24, 25, 26, 27 | none |
| REF-exposure-hook | General per-day exposure-scale series (<= 1) applied after the risk target | `pead/engines/equity.py::apply_cash_and_risk_target(exposure_scale=None)`, `pead/strategies/base.py::EquityStrategy.backtest` hook `exposure_scale(uni, run)` | EQ-28 (reusable by regime gates) | low; None path identical |
| REF-entry-lag | `with_entry_lag(uni, k)` rewriting `entry_row_idx` AND `day0_date` (engine reads entry calendar day, quarter and cost timing from `day0_date`) | `pead/engines/equity.py`, `EquityStrategy.entry_lag_sessions` | EQ-23 | none at k=0 |
| REF-cost-source | Engine cost per event from a column (spread-based) and optional extra entry bps, instead of only `COST_BPS[liquidity_quintile]` | `pead/engines/equity.py::Sizing` (3 defaulted fields), `run_equity_backtest` line ~169 and cost lines ~229-232 | EQ-26, 27 | medium: touches all 59 strategies' path; must be bit-identical by default |
| REF-signal-mask | Extract shared top-decile base mask/weights from `SteadyDrift.weights` | `pead/strategies/equity/steady_drift.py` | EQ-21, 23, 24, 25, 26, 29 | low; NAV diff |
| REF-rank-helpers | Move `prior_quarter_ranks` out of the executable module `pead/prep/equity/vol_features.py` into `pead/common/pit_rank.py`; optional `trailing_k` pool mode | those two files | EQ-25, 24 | low; `equity_vol_panel.parquet` hash must not change |

## CHANGE items (existing code modified)
| Item | File / function | Change | Why | Specs |
|---|---|---|---|---|
| C1 | `pead/core/data.py::_load_events` | Join side tables; assert same length/order | features without rebuilding vintage | 22, 21, 23-27, 29 |
| C2 | `pead/prep/stages.py::STAGES` | +Stage entries: reaction_features, sue_variants, orj_features, attention_features, quote_features, gap_history, fixed_horizon_returns | build graph | 22, 21, 24, 25, 26, 27, 29 |
| C3 | `pead/engines/equity.py` | with_entry_lag; Sizing cost fields; exposure_scale arg | see REF items | 23, 26, 28 |
| C4 | `pead/strategies/base.py` | `entry_lag_sessions`, `exposure_scale` hook, `params()` keys, `IntradayEventStrategy` base | strategies | 23, 28, 27 |
| C5 | `pead/strategies/weights.py` | add `curve_multiplier` (pure) | live-callable | 29 |
| C6 | `pead/cli.py` | `signal` subcommand | harness entry | 30 |
| C7 | `meta/steady_placebo.py` | import from `pead/core/placebo.py` | de-dup | 30 |
| C8 | `pead/reporting/comprehensive.py` (verify) | tolerate new family `equity-intraday` | registry/report | 27 |

## IMPROVE items (infrastructure weaknesses)
| Item | Weakness | Specs |
|---|---|---|
| IMP-anntims | REF-anntims-audit: day0 timing for 1996-98 (`anntims_unknown` 95/85/27%); events builder not in `pead/prep` (stages start from `data/events/equity_events_1996_2013.parquet`); produce table and `timing_known` flag; consider rebuilding day0 for unknown timestamps | 22, 23, 24, 27 |
| IMP-tbill | Real T-bill series (ROADMAP #2, `pead/core/rates.py` typed from memory) before judging cash-heavy or exposure-scaled books | 27, 28 |
| IMP-benchmark | Investable small-cap benchmark (ROADMAP #1): alpha from filters that shift size mix is not comparable | 23, 25, 29 |
| IMP-signal-log | `results/_signal_tests.csv` records every test looked at (FDR family size) | all |
| IMP-diagnostics-persist | Scripts 36-38 (revisions, dispersion, revenue, seasonal SUE) print to console only (optimization_line README); persist outputs | 21, 25 |
| IMP-data-ext | CRSP/IBES/Compustat after 2013 (ROADMAP #7) is the only way to test compression; BLOCKED inputs listed in EQ-28 | 28 |
| IMP-intraday | No intraday/TAQ data: BLOCKED for minute-level entry (EQ-27) and true OFI (EQ-26); exact missing input is consolidated trades/quotes or 1-minute bars with release timestamps, 1996-2013 | 26, 27 |
| IMP-live-gap | Live engine scores yfinance EPS surprise, not IBES dispersion SUE (ROADMAP #3); every new column needs a live-feed counterpart before `live_ready=True` | 21-29 |
| IMP-test-pin | Add test recomputing `sue_rank_pct` from `sue_analyst` (pandas 2 vs 3 deciles differ) | 21 |
| IMP-perf | `curve_multiplier` must be sort/cumsum based (no events x pool loops) | 29 |

## Suggested order
1. REF-period-split, REF-stats-nodeps, REF-placebo-runner, CLI `signal` (EQ-30) so every later spec has its gate.
2. REF-event-key, REF-event-feature-store (+C1, C2) with the pin tests green; REF-dsf-reader; IMP-anntims audit table.
3. EQ-22 reaction features; EQ-21 sue_variants (needs only ibes_clean + price); REF-signal-mask, REF-rank-helpers.
4. REF-entry-lag and EQ-23; EQ-24 on top of EQ-22.
5. REF-cost-source (highest regression risk; do after the cheap ones) and EQ-26.
6. REF-exposure-hook, IMP-tbill, then EQ-28.
7. EQ-25, EQ-29 (needs EQ-21 `sue_pct` for the pct variant), EQ-27 last (new engine, lowest prior).
