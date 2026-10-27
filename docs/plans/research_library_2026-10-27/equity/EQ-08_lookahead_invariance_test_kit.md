# EQ-08: Look-ahead / event-timing invariance test kit (truncation, future-perturbation, timing fixtures, engine-causality checks)
- Status: planned | Priority: P0 (gate for every other signal/exit spec) | Effort: M | Source(s): library `audits/01_EA1904_SOURCE_AUDIT.md` (E2, section D tests), `audits/02_PURUJIT_SOURCE_AUDIT.md` (section 5 counterexamples, P1-P4, P7), `audits/04_ALADIN_SOURCE_AUDIT.md` (section B), `foundations/02_EVENT_TIME_PIT.md` (quick test fixtures), `reproduction/05_IMPLEMENTATION_AND_PSEUDOCODE.md` (invariance cases); EA1904, Purujit and Bouddat repos named in those audits

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Thesis: all three assigned code audits found the same class of bug (a signal or scale that sees information from the future of its trade: unlagged returns, full-history medians, future exit prices, full-sample imputation, future-window volatility). The repo already fixed four such defects after the fact (docs/FIXES.md) and lists residuals (`neutralized_tilt` normalises by quarter-wide totals; fix-4 back-off thresholds; leave-one-out benchmark). A reusable kit that every strategy and feature builder must pass turns those one-off audits into a permanent gate.
Take: the library's executable counterexamples as pytest cases: truncation invariance ("appending future rows must leave an earlier score unchanged"), future-window perturbation ("rewriting returns after exit must not change entry features or sizing"), release-time fixtures (16:05 vs 15:59, Friday AMC + Monday holiday, half-day, estimate revised after the release), same-day multi-event NAV accounting (two +2% trades at 50% NAV = +2%, not +4.04%), delisted-before-exit kept, exit cost charged on both sides, short ROI on fixed notional.
Do NOT take: any performance claim; fixtures that need intraday bars or a vendor feed we do not have (marked skipped with reason); the library reference Python (`reproduction/pead_reference.py`) as code to import - it is third-party scaffolding; reimplement the assertions against this repo's engine.

## 2. Evidence grade & known defects in the source
The audits are code-reading, not runs (library states so); counterexamples are derived, not observed in a specific number. The value here is the test design, not any figure. Known gap in the audits that we must cover ourselves: they do not examine engine-level causality (cap/admission logic), which is where this repo has had its own look-ahead (leverage cap fix 1, FIXES.md).

## 3. Exact rules (the tests)
`tests/pit_kit.py` (helpers) + `tests/test_pit_invariance.py` (cases). All use small synthetic universes built like `_panel()` in `tests/test_steady_drift.py` (verified helper that builds an `EquityUniverse` with `panel_permno`), plus an optional real-data tier skipped when `data/` is absent (pattern used by `tests/test_framework.py`).
T1 Strategy-weights truncation invariance (generic, parametrised over `registry.all_strategies()` of asset_class equity): for a cutoff date C, `weights(events)[day0 <= C] == weights(events[day0 <= C])` (equal_nan). Strategies known to fail today are listed in an explicit `KNOWN_LEAKY` dict with the reason (expected: those using `W.neutralized_tilt`, which divides by quarter-wide totals per FIXES.md "Residual look-ahead"); a NEW strategy not in the dict must pass, and a strategy in the dict that starts passing fails the test (forces the dict to shrink).
T2 Feature-builder future-rewrite: for each side table in the REF-event-feature-store (EQ-02, EQ-22), rebuild with all raw inputs dated after C replaced by garbage (NaN, 1e9, sign-flipped returns); outputs for events with day0 <= C identical. Cutoff chosen mid-sample plus an event-day cutoff (events on C itself).
T3 Entry-return exclusion: for `entry_mode="day0_close"`, set the day0 return of every event to +50% in `ret_raw`; the book's P&L through the whole run is unchanged (the engine starts P&L at `entry_row_idx + 1`). For open entry (EQ-03), the first leg must equal close/open of the fill session only.
T4 Exit-path causality (needed by EQ-01/04/05): perturbing returns AFTER a position's actual exit leaves its exit row, notional and P&L unchanged; perturbing returns after a position's ENTRY but making it stop out earlier must not change the admission decisions of any position entered earlier in the same quarter (the cap uses scheduled exits; see EQ-04 section 5b, the engine-cap causality item).
T5 Sizing causality: trailing-NAV sizing is quarterly; rewriting P&L of quarter q+1 must not change notionals of quarter q (existing property, assert it; also for `causal_inverse_variance` the already-present test in `tests/test_steady_drift.py` is the template).
T6 Throttle causality: `apply_cash_and_risk_target` exposure on day t is unchanged when returns from t+1 onward are rewritten (exists in `tests/test_steady_drift.py` per STEADY_DRIFT.md; keep, add `apply_beta_overlay`).
T7 Timing fixtures (data tier, against `data/events/equity_events_1996_2013.parquet`, because the day0 builder is NOT in this repo - see section 5b): (a) every event with `anntims >= 16:00` has `day0_date > anndats` on trading days; (b) events with `anntims < 09:30` and a normal trading day have `day0_date == anndats`; (c) `day0_date` is always a date present in the permno's CRSP panel; (d) the Friday-AMC-before-Monday-holiday cases resolve to the next real session; (e) `anntims_unknown` events are shifted a session (conservative). Report counts of violations rather than asserting zero for (a)/(b) unless the data agree.
T8 Accounting fixtures: two simultaneous positions each +2% at 50% of NAV give +2% NAV (engine on a 2-stock synthetic panel); entry and exit costs both charged (Purujit/EA1904 E5); position in a delisted stock stays in P&L to its last row (`with_fixed_hold` test exists; extend to `with_path_exits`); short fixed-notional ROI (when REF-borrow-fee lands).
T9 Determinism: running a strategy twice in the same process and in two processes (`pead.runner`) yields identical `final_nav` (`==`), identical `positions` frames.
T10 Benchmark sanity (library E8): equal-weight "buy and hold all" comparator must not be labelled as index; `uni.vwretd` is the only index series; assert report label.

## 4. Data requirements & availability in THIS repo
Synthetic tier needs nothing. Real tier needs `data/` (skipped otherwise, like the existing pinned-NAV tests). Verified available: `anntims`, `anntims_unknown`, `day0_date`, `day0_lag_days` in `equity_events_1996_2013.parquet`; no code for the day0 resolution rule exists in the repo (only `meta/debate/audit_a1.py` prints value counts), so T7 can validate the data but not the rule. No intraday bars, so Purujit's bar-level fixtures are skipped (documented).

## 5. Framework mapping
Files: `tests/pit_kit.py` (NEW: `truncate_events`, `perturb_future`, `make_universe`, `assert_weights_causal`), `tests/test_pit_invariance.py` (NEW), shared fixtures moved out of `tests/test_steady_drift.py::_panel` into `tests/pit_kit.py` (that test file then imports them; behaviour unchanged). Registry hook: `pead/strategies/registry.py::all_strategies()` (verified). Optional CLI: `python -m pead audit <strategy>` runs T1/T3/T9 on one strategy against real data and writes `results/<strategy>/pit_audit.json` (new subcommand in `pead/cli.py`). Params: cutoffs as constants in the kit (two default cutoffs: 2003-06-30, 2009-12-31).

## 5b. Build vs. change vs. restructure
(1) NEW: `tests/pit_kit.py`; `tests/test_pit_invariance.py`; `KNOWN_LEAKY` registry; optional `python -m pead audit`; `pead/prep/equity/verify_day0.py` (independent re-derivation of day0 from `anndats`, `anntims`, CRSP calendar `dsi.parquet`/panel, compared with the stored column - REF-anntims-audit as named in EQ-22).
(2) CHANGE: `tests/test_steady_drift.py` (import shared `_panel` from the kit; low risk); `pead/strategies/weights.py::neutralized_tilt` is NOT changed here (it is a known leak; fixing alters the market-neutral books' numbers and their `_legacy` twins - out of scope, only registered as known-leaky); the engine only if T4 fails (see EQ-04 CHANGE).
(3) RESTRUCTURE: REF-pit-test-kit (the kit itself; every other spec's "no-look-ahead test" calls into it instead of re-implementing); REF-exit-rule-hook interplay for T4; REF-period-split constants imported by the kit for sample windows.
(4) IMPROVE: day0 rule not under version control (data/events is an input, builder unknown) - add `verify_day0.py` and a README section; no CI is configured to run tests (add a `pytest -q` target in docs); equity tests skip silently without data - add a one-line report of how many tests were skipped; `pead/reporting/` should print the PIT audit status per strategy.

## 6. Acceptance tests (tests of the tests)
- Mutation check: a deliberately leaky weight function (uses `events["ivol_252d"].median()` over all events) FAILS T1; the same function using strictly earlier events PASSES. A deliberately unlagged engine variant (P&L from `entry_row_idx`) FAILS T3.
- Library counterexamples reproduced as xfail-free tests: EA1904 unlagged `signal[t] * ret[t]` toy (the kit must flag it), Purujit future-window vol scalar (flag), Bouddat whole-sample dispersion fill (flag).
- `KNOWN_LEAKY` sanity: `sector_balanced` or another `neutralized_tilt` strategy appears there and fails T1 for the stated reason.
- Existing suite still passes: `python -m pytest tests -q` (29 passed, 14 skipped per STEADY_DRIFT.md section 8; skip counts reported).
- Kit runtime < 60 s on the synthetic tier.

## 7. Experiment plan
No backtest. Run T1 across all registered equity strategies once and publish the pass/fail table (including the legacy twins) as `docs/PIT_AUDIT.md`; expected finding: the `steady_drift*` family passes, `neutralized_tilt` family fails. Re-run the kit for every new strategy before its first holdout evaluation (a failed kit blocks the holdout). Output is a gate for judging EQ-01/03/04/05/06/07 against `steady_drift_plus`; it has no return comparator.

## 8. Expected outcome / hypotheses and risks
Expected: finds at least one more leak among legacy strategies (residual list in FIXES.md says magnitude unverified) and possibly the engine-cap causality issue for non-deterministic exits. Risks: false failures from legitimately shared information (e.g. `sue_rank_pct` uses an expanding pool of earlier QUARTERS; a cutoff inside a quarter is fine, but cross-sectional ranks within a quarter would fail - that is the point); floating tolerance; the real-data tier is slow; kit may give false comfort for leakage that is statistical (selection of constants on the full sample) rather than structural - it cannot detect in-sample tuning, which is why the holdout protocol (REF-period-split) remains separate; data ends 2013.

## 9. Dependencies and implementation checklist
Depends on nothing; gates EQ-01 to EQ-07. Coordinates with EQ-22 (feature store, REF-anntims-audit) and EQ-30 (harness).
- [ ] move `_panel` helper to `tests/pit_kit.py`
- [ ] T1 with `KNOWN_LEAKY` and mutation checks
- [ ] T2 hook for each side table as it is created
- [ ] T3, T5, T6, T8, T9 on synthetic data
- [ ] T4 once `with_path_exits` exists
- [ ] T7 data-tier checks + `verify_day0.py`
- [ ] publish `docs/PIT_AUDIT.md` table
