# EQ-28: PEAD compression handling: era-interaction test (tradable vs total CAR) and a strictly-past decay-aware exposure scale
- Status: planned | Priority: P1 | Effort: M | Source(s): library `strategies/12_DYLAN_JEON.md` (https://github.com/dylan-jeon/earnings-surprise-analysis, `analysis/calculate_pead_full.py`), `foundations/02_EVENT_TIME_PIT.md` (Dylan caution), `foundations/07_ACADEMIC_CONTEXT.md`; repo: `docs/ROADMAP.md` items 6 and 7, `docs/STEADY_DRIFT.md` section 10 item 2

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Dylan Jeon reports a negative `surprise x post2020` interaction (-0.0074, p=0.039, R2=0.028, HC3 errors, author-reported) on a 3-session CAR that includes the announcement reaction. The repo has its own, better-identified decay evidence on the tradable window: skill alpha fell 9.4 -> 7.9 -> 4.1 pts across 1997-2001, 2002-07, 2008-13 (docs/STEADY_DRIFT.md section 4), D10-D1 cell spread 364 -> 249 -> 142 bp. Take: the interaction regression design done correctly (post-entry outcome, clustered errors, once per event per cycle, pinned end date), and the idea of a monitored decay. Do NOT take: current-constituent Wikipedia sectors, whole-sample 1/99 winsorisation, `STUDY_END=date.today()`, previous-close CAR as the outcome, any claim about 2020 (our data end in 2013, see BLOCKED below).

## 2. Evidence grade & known defects in the source
Library grade E (event study, not a costed strategy). Defects the library lists: `car_3d` is t-1 to t+1 (two return intervals) and includes the reaction; `car_20d` is t-1 to t+20 so "drift" includes the announcement; CAR approximated as stock minus SPY simple return; HC3 not cluster-robust; ~21,878 events over 499 present-day S&P 500 names (survivorship); different SUE metric and event dates from other studies, so not a replication of leeyawnnn. Treat all numbers as unverified claims.

## 3. Exact rules
Part A, era-interaction report (no trading):
- Outcomes (both reported): `y_total = sum(ret_mktadj[r-1 .. r+h-1])-style from the previous close` (author-compatible) and `y_tradable = hold_ret / ret_fwd_60d_mktadj` (post-entry, from the day0 close; vol panel columns verified). h in {20, 60} (repo-native horizons; `ret_fwd_{1,5,10,20,40,60}d_mktadj` exist in `data/decile_events.parquet`).
- Regressor `s = sue_rank_pct - 0.5` (PIT rank, not raw percent surprise, so no winsor cutoffs); also `s_raw = clip(sue_pct, -5, 5)` from EQ-21 as a robustness column.
- Break dates (pre-registered; primary = 2005-01-01, the start of the Reg NMS / electronic-trading regime; sensitivity 2001-04-09 decimalisation, 2008-01-01): `post = day0_date >= break`.
- Model: `y = a + b*s + c*post + d*(s*post) + sector FE + size-quintile FE + e`. Errors clustered by `ann_quarter` (primary) and two-way with `permno`. One observation per (permno, ann_quarter): if duplicates, keep the first. Report d, its cluster-robust t, and the equivalent with `y_total` to show how much of the compression is in the reaction vs in the drift.
Part B, decay-aware exposure scale (a strategy):
- At each trading day t compute `spread_t` = mean post-entry mktadj return over the last `W=750` calendar days of D10 events whose first `K` post-entry sessions are complete by t-1 (K=20 default; K=60 alternative), minus the same mean for all events in the pool (events completing in the window). Compute only from `ret_mktadj` rows <= t-1.
- `ref_t = expanding mean of spread_u for u <= t-1` (starts after 3 years; before that scale = 1).
- Scale `s_t = clip(spread_t / ref_t, s_min, 1.0)`, `s_min = 0.4`. Never above 1 so never levered (preserves the no-borrowing rule).
- Applied as an additional multiplier on exposure in `apply_cash_and_risk_target` (`e_t = min(1, vol-target) * s_t`), with the rest in T-bills; 10 bp per unit change cost as today.
- Strategies: `steady_drift_plus_decay` (K=20), `steady_drift_plus_decay60`.
Post-2013: BLOCKED for any out-of-sample claim; exact missing inputs: CRSP daily stock file 2014-present, IBES summary/actuals 2014-present (statsumu_epsus, actu_epsus), Compustat fundq, CCM links (ROADMAP #7). Until then "holdout" means 2006-2013 only and the decay scale is judged by whether it would have helped in 2008-13 using only past data.

## 4. Data requirements & availability in THIS repo
Available: everything for Part A and B within 1996-2013 (vol panel, `uni.ret_mktadj`, `decile_events.parquet` forward returns). Duplicate key issue (105 duplicate event_ids): use row position, not event_id. Missing: post-2013 data (BLOCKED above); transaction-level 2020s microstructure; sector history as-of each date is not an issue here because `ff12_sector` in the panel is built from CRSP/Compustat SIC (verify `sic_fallback_used` counts).

## 5. Framework mapping
- Part A: `pead/core/event_stats.py::era_interaction(events, y_col, break_date, clusters)` using `clustered_ols` from EQ-30; CLI via `python -m pead signal era --ret ...`.
- Part B: new strategy file `pead/strategies/equity/steady_drift_decay.py`: `SteadyDriftDecay(SteadyDriftPlus)` overriding a new hook `exposure_scale(self, uni, run) -> np.ndarray` (REF-exposure-hook); `EquityStrategy.backtest()` in `pead/strategies/base.py` calls it after `run_equity_backtest` and passes the series into `apply_cash_and_risk_target(..., exposure_scale=...)`.
- Engine change: `pead/engines/equity.py::apply_cash_and_risk_target` gains `exposure_scale: np.ndarray = None` (length = number of calendar days; default None leaves behaviour identical).
- Params: `decay_window_days=750`, `decay_horizon_sessions=20`, `s_min=0.4`, `min_history_days=1095`, `break_date="2005-01-01"`.

## 5b. Build vs. change vs. restructure
1. NEW: `era_interaction` + `tests`; `steady_drift_decay.py`; `exposure_scale` implementation helper `pead/core/decay.py::rolling_spread(uni, events, K, W)`.
2. CHANGE: `pead/engines/equity.py::apply_cash_and_risk_target` (optional arg; with None the output must be bit-identical for the four steady_drift strategies: pinned by `tests/test_steady_drift.py::...throttle...` and a before/after NAV diff); `pead/strategies/base.py::EquityStrategy` (new hook default returns None; add to `params()`).
3. RESTRUCTURE: REF-exposure-hook (today only `risk_target_vol` and `long_only_cash` flags exist; a general per-day scale series replaces ad-hoc flags and is reusable by regime/trend-gate ideas); REF-period-split; REF-stats-nodeps.
4. IMPROVE: ROADMAP #2 (real T-bill) because a scale < 1 parks money in the typed-from-memory cash series; ROADMAP #7 data extension is the only real fix for "compression"; add to the leaderboard report a `by_era` column set (metrics.ERAS already has three eras; surface skill alpha by era from the placebo).

## 6. Acceptance tests
- Part A synthetic: generate y = (0.05 - 0.03*post)*s + noise with 40 quarters; recovered d within 2 SE of -0.03; with d=0 true, rejection rate <= 10% at 5% level over 200 seeds (clustered, not HC3).
- Part B no look-ahead (essential): rewrite `ret_mktadj` for all rows after day T; assert `s_t` for t <= T+1 is unchanged; events enter the spread only after their K-th post-entry session (test with an event whose K-th session is t exactly: excluded at t, included at t+1).
- Never above 1: `max(s_t) <= 1`; marked holdings <= 95% NAV preserved.
- Default parity: strategy with `exposure_scale=None` equals `steady_drift_plus`.
- Determinism.

## 7. Experiment plan
1. Part A on search window (day0 <= 2005-12-31 cannot test a 2005 break), so run it on the full 1996-2013 sample with the three pre-registered breaks and report all three (family of 3 x 2 outcomes x 2 horizons in the FDR log). Expected: negative d on `y_tradable` consistent with the repo's skill-alpha fade; compare magnitude of d for `y_total` vs `y_tradable`.
2. Part B vs `steady_drift_plus` (12.07% / 1.19 / -22.07%, 2008-13 Sharpe about 0.79 per docs): choose K and `s_min` on events/returns through 2005 only, report 2006-2013 once. Placebo: shuffle SUE rank within quarter (decay estimate then reflects noise; scale should hover near ref). Judge by Sharpe and drawdown, not return (scale < 1 mechanically lowers return).
3. Compare with a naive alternative: static era-based haircut (not tradable, ex post) to show how much was available.

## 8. Expected outcome / hypotheses and risks
H1: the interaction on the tradable outcome is negative and significant at quarter-cluster level for the 2005 break (the repo already measures a 9.4 -> 4.1 pt fade). H2: Part B improves 2008-13 Sharpe slightly at best and lowers return; the delay inherent in requiring completed windows (20 sessions) means the scale reacts to a fade only after it is under way. Risks: strong in-sample bias (design after seeing 1996-2013); noisy 3-year spread estimate (events per window ~ 13,000 but D10 spread SE is wide); a scale tied to realised spread can also cut exposure right after crashes when the drift rebounds; compression after 2013 cannot be tested here.

## 9. Dependencies and implementation checklist
- [ ] EQ-30 `clustered_ols`, split constants
- [ ] REF-exposure-hook with parity test
- [ ] `rolling_spread` + causal tests
- [ ] Strategy file + registry
- [ ] Pre-registration note of breaks and K, W, s_min in the file docstring
- [ ] Data extension (ROADMAP #7) tracked as BLOCKED prerequisite for any claim beyond 2013
