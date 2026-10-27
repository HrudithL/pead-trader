# MT-01: Look-ahead and accounting failure-mode regression suite
- Status: planned | Priority: P0 | Effort: M | Source(s): library `audits/03_BACKTEST_FAILURE_MODES.md`, `reproduction/05_IMPLEMENTATION_AND_PSEUDOCODE.md` section G (10 invariance tests), `reproduction/01_REPRODUCTION_PLAYBOOK.md` section 8 (proof-of-causality), `audits/02_PURUJIT_SOURCE_AUDIT.md` (P1-P8), `audits/01_EA1904_SOURCE_AUDIT.md`; repo `docs/FIXES.md` residual-leakage list

## 1. Purpose & what we take from the library
Every defect the library catalogues (unlagged return, overlapping trades compounded serially, future-window vol scaling, full-sample medians, non-latched stops, missing fees, mid fills) is turned into an automated test that runs on every strategy. The repo already fixed four such defects by hand (FIXES.md) but pinned only the *fixes*, not the *class* of defect. This suite makes "a new strategy cannot leak" a property the CI checks, and gates any new strategy (MT-04 requires `V1 = passes this suite`).

## 2. Gap analysis vs the repo today
Existing tests (verified by `grep "^def test" tests/*.py`): `test_pit_rank_uses_only_strictly_earlier_quarters`, `test_open_book_cap_bounds_daily_gross...`, `test_fixed_hold_is_cut_at_the_stocks_last_row...`, `test_causal_inverse_variance_ignores_the_future_and_the_mask`, `test_risk_target_never_levers_and_uses_only_the_past`, `test_chronological_cap_admits_in_announcement_order`, `test_bid_ask_fill_pnl_is_exactly_bid_minus_ask`, and NAV pins in `tests/test_framework.py`. These are single-function unit tests plus reproduction pins. Missing:
1. **No test that a strategy's `weights(events)` ignores outcome columns.** `pead/prep/equity/vol_features.py:115-126` writes `hold_ret` and `ret_fwd_60d_mktadj` into `equity_vol_panel.parquet`, which `pead/core/data.py::_load_events` prefers, so both (plus `exit_date`, `exit_row_idx`, `holding_days`, `holding_cell`) are handed to every `EquityStrategy.weights()` (base.py `backtest()` passes `uni.events`). Nothing prevents a strategy reading them. `vol.walk_forward_cell_filter` legitimately reads `hold_ret` under a guard (`q_done < q.start_time`) that is untested.
2. **No whole-engine future-perturbation test** (change returns after date T, NAV before T must not move). The engine documents that `cap_mode='priority'` and `'proportional'` are non-causal (equity.py comment above `order = ...`); only `'chronological'` is. No test records which registered strategies use a non-causal mode.
3. **No NAV/ledger reconciliation test**: `sum(positions.net_pnl)` vs `daily.nav` change, `nav[-1] == cap0 + cumsum(daily_pnl)` (identity by construction, but position-level attribution at equity.py `gross_pnl`/`cost` is not asserted equal).
4. **No concurrency test** (two simultaneous +10%/-10% half-NAV positions must leave NAV flat).
5. **No data-level PIT tests** on `data/events/equity_events_1996_2013.parquet` (e.g. `anntims >= 16:00 => day0 after anndats`).
6. `neutralized_tilt` group normalisation uses quarter-wide totals including later reporters (FIXES.md "Residual look-ahead", magnitude "unverified"); no test or measurement.
7. Known data defects have no pinned count: 105 duplicate `(permno, day0)` rows in `positions_rankweighted_v2` (FIXES.md), 19 duplicate `(permno, day0_date)` among analyst events and 208 events whose `anndats` quarter differs from `day0_date` quarter (both measured 2026-10-27 on `equity_events_1996_2013.parquet`).

## 3. Exact specification (codeable)
Create `tests/pit_kit.py` (REF-pit-test-kit): `make_panel(n_perm, n_days, seed)` returning an `EquityUniverse` with random returns, an events frame with every column in `required_columns` of the strategy under test, `cal`, `vwretd`; `make_events_like(real_events_sample)` for strategy-facing frames. All tests synthetic (no data dependency) unless marked `@pytest.mark.data`.

Tests (file `tests/test_lookahead.py` unless noted; IDs are stable and appear in `results/<name>/tests.json`):

| ID | Name | Assertion |
|---|---|---|
| LA-01 | outcome_firewall | For every registered `EquityStrategy` `s` (use `registry.all_strategies()`), build events frame `E`; `E2 = E.copy()` with `OUTCOME_COLUMNS = ["hold_ret","ret_fwd_60d_mktadj","exit_date","exit_row_idx","holding_days","holding_cell"]` replaced by random values. `np.array_equal(s.weights(E), s.weights(E2))` unless `s.outcome_columns_allowed` names the column; `walk_forward_cell_filter`-based strategies are tested by LA-02 instead. |
| LA-02 | guarded_outcome_use | For strategies allowed to read `hold_ret`: perturb `hold_ret` only for events with `exit_date >= quarter_start(Q)` for the entry quarter Q under test; weights of entry quarter Q must be unchanged. |
| LA-03 | future_returns_do_not_move_past_nav | Run `run_equity_backtest` on panel P and on P' where `ret_raw`/`ret_mktadj` are re-drawn for rows with date > T (T = a mid-sample quarter end). `daily.nav[date<=T]` equal to 1e-9 for `cap_mode='chronological'`; for `'priority'`/`'proportional'` the test is `xfail(strict=False, reason="documented non-causal")` and a registry check lists the strategies using them (`pead list --causality`). |
| LA-04 | no_same_close_return | One position, +10% jump on `day0`, flat after: engine P&L is 0 (engine accrues from `entry_row_idx+1`, equity.py `flat_row = ... entry_idx + 1`). |
| LA-05 | entry_shift_monotone | Shifting `entry_row_idx` and `day0_date` one session later on a synthetic drift series changes P&L by the one-day return, i.e. the gap-decomposition test of playbook section 8. |
| LA-06 | concurrency_no_serial_compounding | Two simultaneous positions each 50% of trailing NAV, +10% and -10% over the same window: final NAV = start NAV (library G6), not 0.99x. |
| LA-07 | fees_round_trip | Zero-return path, cost bps b, notional N: NAV change = -2*N*b/1e4 exactly; with b=0 NAV change = 0 (library tests 5 and G3). |
| LA-08 | pit_rank_perturbation | For `rank_pct(values, quarters)` (pead/common/pit_rank.py): changing any value in quarter q' >= q leaves ranks of quarter q events unchanged (property test, 200 random draws). Complements the existing example test. |
| LA-09 | trailing_window_causal | `volatility.trailing_window`/`total_vol`/`idio_vol`: set all returns at index >= entry_row_idx to NaN-sentinel garbage; features unchanged. Also `vol_features.prior_quarter_ranks` ignores same-quarter and later events. |
| LA-10 | risk_target_causal | `apply_cash_and_risk_target`: perturb `daily_return[t:]`, exposure `e[:t+1]` unchanged (existing test covers one case; generalise to random t). |
| LA-11 | neutralized_tilt_causality | `weights.neutralized_tilt` / `prep/equity/lib/positions.weights_size_sector_neutral`: perturb SUE of later announcers within the quarter; weights of earlier announcers must be unchanged. **Expected to FAIL today** (quarter-wide group totals). Mark `xfail(strict=True)` and, as a deliverable, run the measurement experiment `scripts/measure_neutralization_leak.py` (re-run `balanced_leverage` with an announcement-ordered cumulative normaliser) and record the delta in docs/FIXES.md. |
| LA-12 | nav_ledger_reconciliation (tests/test_accounting.py) | For every saved result: `abs(nav[-1]-cap0-daily_pnl.sum())<1`; `abs(positions.net_pnl.sum() - (nav[-1]-cap0)) < 1` for engine-only (non-overlay) runs; `gross_exposure>=|net_exposure|`; `pct_days_over_cap` consistent with recomputation. Skip overlays with a recorded reason. |
| LA-13 | cap_binding (test_accounting.py) | Realized gross over the NAV the quarter was sized on never exceeds `max_gross_leverage` for `exposure_cap='open_book'` (existing test generalised to randomised books). |
| LA-14 | annualization_labels (tests/test_metrics.py) | Constant 0.1% daily return: `summarize()['annualized_return']` equals `(1.001**252)-1`, *not* `0.001*252`; Sharpe uses 252; a monthly-sampled series is labelled by MT-03's `sampling` field. |
| LA-15 | data_pit_events (tests/test_pit_data.py, `@pytest.mark.data`) | On the real events parquet: (a) rows with `anntims >= 16:00` and `anntims_unknown==False` have `day0_date > anndats` (0 violations measured today); (b) `anntims_unknown==True` rows all have `day0_lag_days >= 1` (100% today); (c) `day0_date >= anndats`; (d) pins: duplicate `(permno,day0_date)` among analyst events == 19, `anndats_quarter != day0_quarter` count == 208, `anndats < fpedats` count == 61 -- fail when they change so a rebuild is noticed. |
| LA-16 | feature_timing (tests/test_pit_data.py) | On a 3,000-event sample of `equity_vol_panel.parquet`: `avg_dollar_volume_21d` equals the mean of `|prc|*vol` over the 21 sessions ending `entry_row_idx-1` (100% matched on 2026-10-27; rtol 1e-6). |
| LA-17 | no_midprice_fantasy | Options: entry at ask, exit at bid (existing `test_bid_ask_fill_pnl_is_exactly_bid_minus_ask`); add: with `fill_mode='bid_ask'` and zero price move, P&L equals `-(ask-bid)*contracts*100` (library G8). |
| LA-18 | duplicate_event_policy | Engine rejects or warns when two funded positions share `(permno, day0_date)`; pin current count (105 in positions) with an allow-list. |
| LA-19 | latched_stop_and_short_roi | Reserved for any future stop-loss/trailing-stop engine: price path 100->103.5->101 triggers a 3%/2% trailing stop (Purujit P6); short 100->80 returns +20% on entry notional (P9). Implemented when a stop engine exists; test file created now with `pytest.skip` stubs so the contract exists. |
| LA-20 | strategy_declares_causality | Every `EquityStrategy` subclass must set `causal = True/False` and `outcome_columns_allowed = ()`; `test_every_strategy_declares_metadata` (tests/test_framework.py) extended. |

Registry hook: add `pead/strategies/base.py::Strategy.causal: bool = True` and `outcome_columns_allowed: tuple = ()` (REF-strategy-iface). `python -m pead check [<sel>]` runs LA-01..LA-03, LA-10, LA-20 for the selected strategies and writes `results/<name>/tests.json` (consumed by MT-12 evidence grade).

## 4. Data requirements
None for LA-01..LA-14, LA-17..LA-20 (synthetic). LA-15/16 read `data/events/equity_events_1996_2013.parquet` and `data/equity_vol_panel.parquet`, `data/normalized_equity/*.parquet` (already local); skipped when absent (same pattern as `tests/legacy_vintage.py`).

## 5. Framework mapping
New: `tests/pit_kit.py`, `tests/test_lookahead.py`, `tests/test_accounting.py`, `tests/test_pit_data.py`, `tests/test_metrics.py`; `pead/cli.py` subcommand `check`; `pyproject`/`pytest.ini` markers `data`, `slow`. Output: `results/<name>/tests.json` (list of {id, status, detail}); leaderboard column `V1` (MT-12). `python -m pytest tests -q` must stay < 2 min (synthetic panels of ~5k rows).

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-pit-test-kit is the same module as EQ-08's `tests/pit_kit.py` and OV's REF-synth-fixtures (implement once, in `tests/pit_kit.py`); LA-01..LA-20 supersede/extend EQ-08's T1..T9 (EQ-08 keeps ownership of its `KNOWN_LEAKY` registry, which LA-03/LA-11 should populate).
- **NEW:** `tests/pit_kit.py` (REF-pit-test-kit); the five test modules above; `pead/core/checks.py::run_checks(strategy)` (used by `python -m pead check`); `OUTCOME_COLUMNS` constant in `pead/core/outcomes.py` (REF-outcome-firewall); `scripts/measure_neutralization_leak.py`.
- **CHANGE:** `pead/strategies/base.py::Strategy` (add `causal`, `outcome_columns_allowed`; regression risk: none to results, but `tests/test_framework.py::test_every_strategy_declares_metadata` must accept the new attrs); `pead/strategies/base.py::EquityStrategy.backtest` (pass `events` through `firewall(events, allowed)` which drops/poisons outcome columns *only inside the weights() call*; regression risk: LOW but real, because `vol.walk_forward_cell_filter` needs `hold_ret`/`exit_date` -> must be listed in `outcome_columns_allowed` for calm_weighted_filter and any strategy using it, otherwise NAV pins in tests/test_framework.py break); `pead/engines/equity.py` (add `Sizing.causal` property returning `cap_mode=='chronological'`; no numeric change).
- **RESTRUCTURE:** split `tests/test_framework.py` (reproduction pins + registry + engine mix) into `tests/repro/` (slow, vintage-dependent) and `tests/unit/` (fast); introduce pytest markers; add `conftest.py` with the shared `tests/pit_kit.py` fixtures. Existing test names are preserved to keep history.
- **IMPROVE:** (a) tests currently skip silently when `data/_prefix_vintage/` is absent (`tests/legacy_vintage.py`) -- emit a loud summary of skips; (b) the engine's non-causal cap modes are only documented in a comment -- surface in `pead list`; (c) known data defect counts (19 / 105 / 208 / 61) are pinned so a rebuild cannot change them silently.

## 6. Acceptance tests
- Each LA test has a *mutation twin* proving it can fail: e.g. LA-04 is run against a deliberately broken engine copy (`flat_row = entry_idx` instead of `entry_idx+1`) and must fail; LA-01 against a toy strategy returning `-hold_ret`; LA-06 against a serial-compounding stub. Mutation twins live in `tests/mutations/` and run in CI nightly.
- All 22 `_legacy`-twin pins and the five corrected NAV pins in `tests/test_framework.py` unchanged after the `firewall` wrapper is added (`test_default_vintage_pins_corrected_final_nav`).
- LA-11 recorded as strict xfail with the measured NAV delta attached to FIXES.md.

## 7. Rollout plan
1. Day 1: `tests/pit_kit.py` + LA-04/05/06/07/12/14 (no strategy changes needed). 2. LA-01/02/20 + `outcome_columns_allowed` declarations (run once in "report-only" mode to list offenders). 3. LA-03 with causality listing. 4. LA-15/16 data tests with pins. 5. LA-08..11. 6. Mutation twins. 7. Wire `python -m pead check` and `tests.json` into `save_result` (MT-12).

## 8. Risks
- Over-strict LA-01 breaks legitimate use of `holding_days` (a function of the fitted decay cell, search-window-derived per FIXES fix 4) -- handle by explicit allow-list with a comment, not by deleting the test.
- LA-03 runtime on the real panel (20M rows); keep it synthetic.
- Test-pass is necessary, not sufficient: library `00_START_HERE` is explicit that unit tests validate representative behaviour, not historical returns.

## 9. Dependencies and implementation checklist
Depends on: REF-pit-test-kit, REF-outcome-firewall, REF-strategy-iface. Blocks: MT-04 (gate), MT-12 (V1 flag), MT-11.
- [ ] `tests/pit_kit.py` + conftest
- [ ] LA-04/05/06/07/12/14 written, passing on current engine
- [ ] `OUTCOME_COLUMNS`, firewall wrapper, per-strategy allow-lists
- [ ] LA-01/02/20 passing for all registered strategies (offender list reviewed)
- [ ] LA-03 + `pead list --causality`
- [ ] LA-15/16 data tests with pinned counts
- [ ] LA-08/09/10 property tests; LA-11 strict xfail + measurement script
- [ ] mutation twins; nightly job
- [ ] `python -m pead check` + `tests.json`
