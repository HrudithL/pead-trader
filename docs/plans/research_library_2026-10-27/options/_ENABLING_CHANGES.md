# Enabling changes for the options specs (consolidated, de-duplicated)

Items are named with stable IDs used inside OPT-01..OPT-06. Type: REF = restructure / shared refactor, CHG = change to existing code, NEW = new shared module/data build, IMP = improvement of current weakness. "Regression guard" is what must stay bit-identical.

## Suggested order

1. REF-search-holdout (S)
2. REF-fill-model (S) then REF-engine-prefix (S), together, with the regression guard
3. REF-event-clock (S)
4. REF-chain-v2 (M/L: scan of 33 GB; run 2 sample years first)
5. REF-spread-diag (S) - gate: read the report before building anything else
6. REF-path-quotes (M)
7. REF-exit-rules (M)
8. REF-overlay (M) with CHG-equityrun-exposure
9. REF-leg-engine (L) with REF-assignment (S)
10. REF-index-underlying (S) - only for OPT-06
11. IMP-* items in parallel (IMP-rates early: needed for OPT-03, OPT-05, OPT-06)

## Table

| ID | Type | What | Real code touched / created | Regression risk and guard | Needed by |
|---|---|---|---|---|---|
| REF-search-holdout | REF | One helper `search_mask(quarters, frac=0.7)` mirroring `PEAD_DECAY_SEARCH_FRAC` (first 70% of announcement quarters; 49 of 71 per `docs/FIXES.md` fix 4) plus an all-trials CSV writer | new `pead/common/search_window.py`; read `pead/prep/equity/daily_decay.py` for the existing rule | none (new) | 02, 03, 04, 05, 06 |
| REF-fill-model | REF | Extract bid/ask/dead-quote logic from `run_options_backtest` into `pead/engines/fills.py` with signed legs, `spread_mult`, commission, locked/crossed rejection; short legs close at ASK | `pead/engines/options.py` (inline block using `fill_mode`, `dead_quote_mark`, `kelly_fwd_{bid,ask}_*`), new `pead/engines/fills.py` | HIGH: guard = final NAVs of `options_cap20` 10,481,798.4, `options_cap30` 9,841,406.8, plus add pins for `options_worst_case`, `options_cap20_midfill`, `options_cap20_legacy` (`tests/test_framework.py`, `tests/test_fixes.py::test_bid_ask_fill_pnl_is_exactly_bid_minus_ask`, `test_mid_fill_ignores_quote_columns_and_needs_none`) | 01-06 |
| REF-engine-prefix | REF/CHG | `run_options_backtest(..., prefix="kelly", weights_col="kelly_fraction", exit_col=None)`: replace hard-coded `kelly_growth/premium/fraction/side/fwd_*`; optional per-position `exit_h`; side split generalized (`pnl_by_side`) | `pead/engines/options.py`; `OptionsSizing` new fields with defaults = current behaviour | HIGH as above (same guard) | 02, 04 |
| REF-event-clock | NEW | `event_clock.parquet`: `event_id`, D0, Dm1, D0+1, BMO/AMC/intraday/unknown, `timing_clean`; stable `event_id=f"{secid}_{day0_date:%Y%m%d}"` (the id exists in `optimal_forward_prices` but not in `decile_events_secid`) | new `pead/prep/options/event_clock.py`; reads `decile_events_secid.parquet` (`anntims`, `anntims_unknown`, `day0_lag_days`, `n_events_same_permno_day0`) and `data/metadata/om_trading_calendar.parquet` | none (new); fixtures from Library 02 | 01, 02, 04 |
| REF-chain-v2 | NEW | Multi-expiry (DTE 7-150, optionally to 400 for OPT-03), multi-session (Dm1, D0, D0+1) all-strike snapshots with bid, ask, greeks, OI, volume, flags; plus `OPPRCD_COLS_V2` | new `pead/prep/options/chain_snapshots.py`; reuse `scan_year_for_keys`; `pead/prep/options/lib/options.py::OPPRCD_COLS` gets a sibling constant (do not edit the existing one) | none for existing stages if the old constant is untouched | 02, 03, 04, 05, 06 |
| REF-spread-diag | NEW | Spread / liquidity / quote-presence report by year, delta, DTE, decile, OI; coverage of qualifying contracts | new `pead/reporting/option_spreads.py` | none | all (gate) |
| REF-path-quotes | NEW | Forward quote table for arbitrary contract lists and a checkpoint grid, long format (`event_id, leg_idx, h, bid, ask, mid, iv, delta, has_quote, dead`); generalizes `forward_prices_optimal.py` (which only handles the Kelly and naive picks and horizons 0,1,5,10,20,40,60) | new `pead/prep/options/forward_quotes.py` | none | 02, 03, 05, 06 |
| REF-exit-rules | NEW | Causal exit evaluation on checkpoint bids: time stop, stop-loss, take-profit, delta collapse, next-earnings guard; outputs `exit_h` per position | new `pead/engines/exits.py` | none; unit tests that exits use only data up to the exit checkpoint | 02, 05 |
| REF-leg-engine | REF | Multi-leg structure engine; extract `_allocate_quarter_budget` (premium, max-loss and collateral budgets) out of `run_options_backtest`; the same telescoping P&L property; daily series schema identical | new `pead/engines/options_structures.py`; refactor in `pead/engines/options.py` | HIGH for the extraction (same guard as REF-fill-model); new engine is additive | 02 (spread ablation), 04 (optional), 05, 06 |
| REF-overlay | REF | `OverlayStrategy` base: runs a registered parent via `pead.strategies.registry.get`, applies an adjustment, returns a full-account `BacktestResult`; resolves how an options overlay plugs into `pead/strategies/portfolio/sleeves.py` (today `PortfolioStrategy.run` only sums weighted daily returns of whole sleeves) | `pead/strategies/base.py` (add class), new modules under `pead/strategies/portfolio/` | LOW additive; blends `hedged_blend`, `calm_blend`, `legacy_blend` unchanged | 03, 06 |
| CHG-equityrun-exposure | CHG | Expose per-position entry/exit/size and the daily gross-over-NAV / risk-target scalar on `EquityRun` | `pead/engines/equity.py` (`run_equity_backtest`, `apply_cash_and_risk_target`), `pead/strategies/base.py::EquityStrategy.backtest` | MEDIUM: guard = all 22 equity pinned NAVs (`tests/test_framework.py`), `steady_drift*` pins (`tests/test_steady_drift.py`), final NAV `steady_drift_plus` 91,947,400.4581; add columns only | 03, 06 |
| REF-assignment | REF | Expiry, European cash settlement (SPX on SET), American early-exercise heuristics for short puts, forced-close rules on checkpoints | new `pead/engines/assignment.py` | none | 05, 06 |
| REF-index-underlying | NEW | SPX level and SET settlement series, index dividends sanity (`indexd.parquet`, `secprd{year}.parquet`, `idxdvd.parquet` at `D:\OptionMetrics\parquet`); verify SET secid coverage by year | new `pead/prep/options/index_chain.py` and `data/metadata/spx_level.parquet` | none | 06 |
| IMP-coverage | IMP | Document and test the gaps: 2011 `opprcd` corrupt/absent, picks start 1998, per-year event counts (1998: 7,174 picks ... 2013: 7,574; none for 2011) | tests + report note | none | all |
| IMP-rates | IMP | Replace typed-from-memory `TBILL_PCT` (`pead/core/rates.py`, ROADMAP #2) with a real series | `pead/core/rates.py` | MEDIUM: changes the four `steady_drift*` results; keep the old function under a flag until pins are re-baselined | 03, 05, 06 |
| IMP-greeks-attribution | IMP | P&L split into delta / vega / theta / spread using gamma, vega, theta now kept; extend `pead/reporting/` | reporting package | none | 02, 04 |
| IMP-live-fill | IMP | `pead/live/broker_options_sim.py` fills at mid minus half with a strategy flat cost; align to `fills.py` before any live use | `pead/live/broker_options_sim.py` | LOW (live only) | later |
| IMP-dividends | IMP | Single-name dividend series for call-substitute cost and early-exercise reasoning (not in the repo's option extract) | prep from CRSP distributions (verify availability in `data/normalized_equity`) | none | 03, 05 |
| IMP-tests | IMP | New test modules: `tests/test_options_infra.py`, `test_tierb.py`, `test_itm_substitute.py`, `test_straddle.py`, `test_risk_reversal.py`, `test_index_overlay.py`; payoff-diagram tests on synthetic chains | tests/ | none | all |
| IMP-hardware | IMP | Chain scan reads 131 files / 32.9 GB from external `D:`; unplugged twice on 2026-10-20; open owner decision on copying (`docs/REPO_MAP.md`); resumable per-year outputs as in existing stages | prep stages | none | 04, 03 |

## Specs needing each item

- OPT-01: all REF/NEW/IMP items above (it defines them).
- OPT-02: REF-search-holdout, REF-fill-model, REF-engine-prefix, REF-event-clock, REF-chain-v2, REF-spread-diag, REF-path-quotes, REF-exit-rules, (REF-leg-engine for the spread ablation), IMP-greeks-attribution, IMP-tests.
- OPT-03: REF-search-holdout, REF-fill-model, REF-chain-v2 (long DTE), REF-path-quotes, REF-overlay, CHG-equityrun-exposure, IMP-rates, IMP-dividends.
- OPT-04: REF-search-holdout, REF-fill-model, REF-engine-prefix, REF-event-clock, REF-chain-v2 (Dm1/D0, DTE 7-45), REF-spread-diag, IMP-hardware, (REF-leg-engine optional).
- OPT-05: REF-search-holdout, REF-fill-model, REF-chain-v2, REF-path-quotes, REF-exit-rules, REF-leg-engine, REF-assignment, IMP-rates, IMP-dividends.
- OPT-06: REF-search-holdout, REF-fill-model, REF-overlay, CHG-equityrun-exposure, REF-assignment, REF-index-underlying, IMP-rates.
