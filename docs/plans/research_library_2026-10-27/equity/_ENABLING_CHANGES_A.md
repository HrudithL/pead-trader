# Enabling changes consolidated (set A: EQ-01 to EQ-08)

De-duplicated list of the refactors (REF), changes to existing code (CHANGE) and infrastructure weaknesses (IMPROVE) the set A specs need. Names marked (B) are shared with the other equity set (EQ-21 to EQ-30) and must be implemented once. File and function references were verified against the repo on 2026-10-27.

## REF items
| ID | What | Needed by | Notes |
|---|---|---|---|
| REF-event-feature-store (B) | Side tables `data/event_features/*.parquet` joined onto events by `pead/core/data.py::_load_events`, instead of widening `equity_vol_panel.parquet`; new prep stages in `pead/prep/stages.py` | EQ-01, 02, 03, 04, 05, 06, 07 | `Stage(...)` mechanism verified (stages.py, `vol_features`). Join asserts unchanged row count. |
| REF-event-key (B) | Unique key `(permno, day0_date, dup_rank)`; `event_id` has about 105 duplicates | EQ-02 and every side table | `positions.py::KEEP_COLS` has no `fpedats` or `ibes_ticker`. |
| REF-dsf-reader (B) | Read `openprc, bid, ask, numtrd` etc. from `data/raw_wrds/crsp/dsf_YYYY.parquet`, aligned to the panel `(permno, date)` sort so rows match `entry_row_idx`; the normalized panel and `equity_panel.npz` drop them | EQ-02, EQ-03 | Verified dsf columns: permno, date, ret, prc, shrout, cfacpr, cfacshr, retx, vol, bid, ask, bidlo, askhi, openprc, numtrd. |
| REF-period-split (B) | `pead/core/splits.py`: search `day0_date <= 2005-12-31`, holdout 2006-2013; helper to summarise a daily frame on a window | all | |
| REF-placebo-runner (B) | Importable shuffled-signal placebo in `pead/core/placebo.py` (today only in `meta/placebo.py`, `meta/steady_placebo.py`) | EQ-01, 03, 05, 06, 07 | Skill = real minus placebo, via `pead/core/alpha.py::alpha_stats`. |
| REF-anntims-audit (B) | Independent re-derivation of `day0_date` from `anndats`, `anntims` and the calendar: the day0 builder is NOT in the repo (only `data/events/equity_events_1996_2013.parquet`) | EQ-02, EQ-08 | `pead/prep/equity/verify_day0.py`. |
| REF-exit-rule-hook | `EquityStrategy.exit_rule: Optional[ExitRule]`; `backtest()` resolves it, else `hold_sessions` -> `with_fixed_hold` as today | EQ-01, 04, 05 | Keeps `steady_drift*` files unchanged. |
| REF-path-exits | `ExitRule`, `with_path_exits(uni, rule)` in `pead/engines/equity.py` (percent stop, latched trailing, session/calendar time exit, per-event hold column, `exit_reason`); share the permno run-end helper with `with_fixed_hold` | EQ-01, 04, 05 | Must reproduce `with_fixed_hold` exactly when only `max_hold` is set. |
| REF-cap-sched-exit | Engine keeps scheduled exits (`sched_exit_*`) for `cap_open_book` and `carry`, actual exits for P&L, so stop-outs cannot leak future information into admission | EQ-04 (hence 01, 05) | Verified: `run_equity_backtest` feeds `exit_cal` to `cap_open_book`. |
| REF-entry-leg | Optional event columns `first_ret_override` (replaces the first P&L-day return) and `entry_extra_bps`; raw return basis only | EQ-03 (EQ-01 `day1_open`) | Related to set B's REF-entry-lag (shift entry by k sessions); both can share `with_entry_timing`. |
| REF-cost-model | Cost multiplier (1x/2x/3x) and extra entry/stop slippage in `run_equity_backtest` (`COST_BPS` is a fixed dict today) | EQ-01, 03, 04 | |
| REF-borrow-fee | `Sizing.short_borrow_apr`, `Sizing.short_margin`; borrow charged at cover; short collateral in `apply_cash_and_risk_target` | EQ-05 (short leg) | Defaults zero = no change to existing numbers. |
| REF-event-source | Builder of event rows (entry/exit rows plus trailing features) from arbitrary `(permno, date)` pairs; `EquityStrategy.load_universe` hook; refactor of `pead/prep/equity/vol_features.py` | EQ-07 | Engineering heavy; last in order. |
| REF-event-universe | Make the `build_deciles.py` source filter selectable (adds 55,782 seasonal-only events) | EQ-06 phase 2 | Default outputs must stay byte-identical. |
| REF-analysis-pkg | `pead/analysis/` for non-backtest studies (decomposition, bucket screens), keeping `pead/research/` an archive | EQ-03, 05 | |
| REF-pit-test-kit | `tests/pit_kit.py` and `tests/test_pit_invariance.py` with `KNOWN_LEAKY` | EQ-08, gates all | Moves `_panel` from `tests/test_steady_drift.py`. |

## CHANGE items (existing code, regression risk)
1. `pead/engines/equity.py::run_equity_backtest`: optional columns `first_ret_override`, `entry_extra_bps`, `sched_exit_*`; borrow charge; cost multiplier. Risk: none when the columns are absent; pin with the `tests/test_framework.py` NAV tests and `test_default_vintage_pins_corrected_final_nav`.
2. `pead/engines/equity.py`: add `ExitRule`, `with_path_exits`, `with_entry_timing`; extract the run-end helper from `with_fixed_hold` (risk: `tests/test_steady_drift.py::test_fixed_hold_is_cut_at_the_stocks_last_row_and_never_crosses_into_the_next_stock`).
3. `pead/strategies/base.py::EquityStrategy.backtest/params/run`: `exit_rule`, `entry_mode`, `load_universe`; extra params keys (reports print them).
4. `pead/core/data.py::_load_events` (side-table join) and `_load_panel` plus `_panel_sources` (open arrays; the cache key invalidates and `equity_panel.npz` rebuilds once).
5. `pead/reporting/attribution.py` near line 39 uses the precomputed `hold_ret` (per-cell holding return from the decay build), which is stale for fixed-hold and path-exit strategies; switch to engine position P&L (`positions.gross_pnl`, `ret_on_notional`). Verify first, then fix.
6. `pead/engines/equity.py::apply_cash_and_risk_target`: the idle-cash formula assumes a long-only book; extend for short collateral (EQ-05 short leg).
7. `pead/prep/equity/build_deciles.py`: source filter parameter (EQ-06 phase 2 only).
8. `pead/cli.py`: optional `study` and `audit` subcommands; `pead/runner.py`: do not assume a single universe per worker (EQ-07).
9. `tests/test_steady_drift.py`: import the shared helper from the kit.

## IMPROVE items
- Delisting returns: `data/raw_wrds/crsp/dsedelist.parquet` exists but is not integrated; it matters for stops, short legs and 9-day covers.
- The day0 rule is not under version control; `anntims` timezone and accuracy unverified; 12.6% of events have unknown time (treated as after-close, conservative).
- `openprc` coverage by year and exchange unknown; add to `pead/prep/verify_raw_data.py`.
- Trial-count bookkeeping (`trials` in `Strategy.meta()`) for deflated-Sharpe style correction (EA1904 audit E11).
- Reports: show the day0 intraday leg and exit reasons separately; a long/short attribution column for asymmetric books (`pnl_long` and `pnl_short` are already in `daily`).
- Placebo and split helpers are scripts in `meta/`, not importable.
- No investable small-cap benchmark (ROADMAP item 1) limits every verdict; the T-bill series is typed from memory (`pead/core/rates.py`).
- Tests skip silently without data; add skip-count reporting and a documented pytest target.
- Known residual leaks (`neutralized_tilt` quarter-wide normalisation) are registered as KNOWN_LEAKY, not fixed.

## Suggested implementation order
1. REF-period-split, REF-event-key, REF-event-feature-store (with set B), REF-pit-test-kit skeleton (EQ-08 T1, T3, T5, T6, T9).
2. REF-dsf-reader; EQ-02 (sue_hist, seasonal flavors, rvol, timing, trend/atr); T2 hook per table; REF-anntims-audit.
3. REF-exit-rule-hook + REF-path-exits + REF-cap-sched-exit (EQ-04), then T4; fix attribution `hold_ret` (CHANGE 5).
4. REF-placebo-runner (with set B), REF-cost-model.
5. EQ-06 (no engine change), EQ-05 long leg, EQ-01.
6. REF-entry-leg, then EQ-03 (needs REF-dsf-reader).
7. REF-borrow-fee, EQ-05 short leg.
8. REF-event-source, EQ-07 (only if the portfolio-free event study in EQ-07 shows a signal).
