# EQ-30: Signal evaluation harness (IC by quarter, clustered inference, tradable-vs-total CAR, cost ladder, placebo, FDR)
- Status: planned | Priority: P0 | Effort: M | Source(s): library `strategies/07_MCRAVI8_WRDS_MINI.md` (https://github.com/mcravi8/PEAD-mini-WRDS), `13_ISHAAN_SINGH.md` (https://github.com/ishaansingh-99/earnings-surprise-pead-analyzer), `12_DYLAN_JEON.md` (https://github.com/dylan-jeon/earnings-surprise-analysis), `08_AARSH_DAYAL.md` (https://github.com/aarshdayal/EARNINGS-SURPRISE-ANALYSIS-PEAD-STRATEGY), `11_KPH3802_FMP.md` (https://github.com/KPH3802/pead-backtest), `comparisons/03_EVIDENCE_GRADING_AND_COMPARABLE_BACKTESTS.md`

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Every signal spec in this batch (EQ-21 .. EQ-29) needs the same gate before anyone looks at a backtest: does the new column rank future post-entry returns, is that inference clustered by earnings season (not by 521 or 17,000 overlapping events), does it survive the shuffled-surprise placebo, and what is the gross-to-net cost ladder. The sources each supply one piece:
- mcravi8: monthly/quarterly Spearman IC; CAPM alpha with t; a cost decomposition (gross drift, entry gap, spread, commission, borrow, financing). Its lesson is that IC 0.018 and a negative net Sharpe coexist.
- Ishaan Singh: market-model CAR with pre-event estimation window, Welch tests, block bootstrap by issuer/quarter, Benjamini-Hochberg FDR across the many cells we are about to test.
- Dylan Jeon: separate `CAR_total` (previous close to +h) from `CAR_tradable` (first fill to +h); era-interaction regression with clustered errors.
- Aarsh Dayal: cluster by calendar earnings season; report mean-arithmetic vs compounded.
We do NOT take: iid bootstrap of events, HC3-only standard errors as "clustered", `rets.mean()` called alpha (KPH), full-sample winsor cutoffs, 4x-quarterly "annualised" returns, any synthetic-data UI/FastAPI layer (Ishaan), any number the sources report (all author-reported, unverified).

## 2. Evidence grade & known defects in the source
Library grades: 07 = B (public pipeline, WRDS data private; `n_obs=7812` is daily observations not events; gross exposure 2.285x under an unaudited definition), 08 = C (21 quarters, t=1.40, p=0.178, 25 hindsight tickers), 11 = B (calendar-day delay, daily-close entry, `alpha` is a raw mean), 12 = E (CAR includes the reaction; Wikipedia sectors as-of today), 13 = E (educational, synthetic provider can be silently default). Nothing here is a performance claim; we take only methods. Defect to avoid copying: KPH `entry_delay_days` is calendar days and entry is a close that may pre-date the release.

## 3. Exact rules
Module `pead/core/event_stats.py` (pure numpy/pandas; statsmodels is NOT installed in the default env, verified `ModuleNotFoundError`; scipy/sklearn availability unverified, do not depend on them). Functions, all deterministic given `seed`:
1. `rank_ic_by_quarter(events, signal_col, ret_col, group_col="ann_quarter") -> DataFrame[quarter, n, spearman_ic]` plus `ic_summary(ic)` = mean IC, t-stat using Newey-West (Bartlett, 4 lags) over the quarterly series, fraction of positive quarters. Spearman computed by rank-then-Pearson (no scipy). Events with NaN signal or NaN return are dropped; quarters with n < 30 are dropped and counted.
2. `car_decomposition(events, uni)` returns per event: `r_pre_reaction = ret_raw[entry_row_idx]` (the day-0 session return, known at entry), `car_total_h = sum(ret_mktadj[entry-1 : entry+h])`-style from previous close, `car_tradable_h = sum(ret_mktadj[entry+1 : entry+h])` (what the engine earns; entry is the day0 close, see `pead/engines/equity.py::with_fixed_hold` docstring). Both reported side by side; only `car_tradable_h` is allowed in acceptance criteria.
3. `clustered_ols(y, X, clusters_1, clusters_2=None)` two-way cluster-robust covariance (Cameron-Gelbach-Miller) in numpy, used for the era-interaction (EQ-28) and signal-vs-return regressions: `y = a + b*signal + sector FE (+ size FE)`; default clusters `ann_quarter` and `permno`. Unit test against a closed-form one-way case.
4. `season_block_bootstrap(series_by_quarter, n=2000, seed=0)` resamples whole ann_quarters (with replacement) and returns the 2.5/97.5 percentile of the mean. Never resample events.
5. `bh_fdr(pvals, q=0.10)` Benjamini-Hochberg step-up; returns reject mask and adjusted p. Every harness invocation appends its tests to `results/_signal_tests.csv` (columns: spec_id, signal, horizon, split, stat, p) so the family size m is the real count of looks, not the count a person chose to display.
6. `cost_ladder(positions, daily)` from the engine's `EquityRun.positions` (columns gross_pnl, cost, net_pnl are produced at `pead/engines/equity.py` lines ~262) returning gross drift, entry/exit cost, and (informational, zero today) borrow. Market-neutral borrow is not modeled; long-only books have none.
7. Shuffled-surprise placebo: `shuffled_signal_run(strategy_cls, signal_col, seeds=range(5))` permutes `signal_col` within each `ann_quarter` (exactly as `meta/steady_placebo.py::run_one` does for `sue_rank_pct`) and re-runs the identical strategy; returns skill = real - mean(placebo) for return, Sharpe and CAPM alpha via `pead/core/alpha.py::alpha_stats`. See REF-placebo-runner.
8. CLI: `python -m pead signal <spec_id|column> [--ret ret_fwd_60d_mktadj] [--split search|holdout|all]` printing a one-page markdown table and writing `results/_signal/<column>.md`. Add as a new `sub.add_parser("signal")` in `pead/cli.py` (existing subcommands: list, run, report, compare, portfolio, prep, live).
Splits come from REF-period-split: search = day0_date <= 2005-12-31, holdout = 2006-01-01 .. 2013-12-31 (constants in new `pead/core/splits.py`; existing `pead/core/metrics.py::ERAS` keeps its three eras for reporting only).

## 4. Data requirements & availability in THIS repo
All present: `data/equity_vol_panel.parquet` (172,923 events: permno, day0_date, entry/exit_row_idx, decile, sue_rank_pct, ann_quarter, event_id, hold_ret, ret_fwd_60d_mktadj, size_quintile, ff12_sector, ivol_252d, tvol_60d); `EquityUniverse.ret_mktadj / ret_raw / vwretd` via `pead/core/data.py::load_equity_universe`; `data/decile_events.parquet` has ret_fwd_{1,5,10,20,40,60}d(_mktadj) and the raw `sue_analyst`. Missing: nothing for v1. Caveat: 105 duplicate `event_id` rows exist (docs/FIXES.md; verified `event_id.duplicated().sum()==105`): harness keys on row position, never merges on event_id until REF-event-key is done.

## 5. Framework mapping
- New `pead/core/event_stats.py`, `pead/core/splits.py`, `pead/core/placebo.py`; CLI hook in `pead/cli.py`; report snippet writer in `pead/reporting/` (reuse `pead/reporting/doc.py` helpers; verify API before use).
- Registered strategies: none. Reuse: `pead/core/alpha.py::alpha_stats` (HAC alpha), `pead/common/stats.py::fama_macbeth`, `pead/common/pit_rank.py::rank_pct`.
- Params: `HAC_LAGS=4`, `BOOT_N=2000`, `FDR_Q=0.10`, `MIN_QUARTER_N=30`, `PLACEBO_SEEDS=5`.
- Engine change: none.

## 5b. Build vs. change vs. restructure
1. NEW: `pead/core/event_stats.py` (functions above); `pead/core/splits.py` (REF-period-split); `pead/core/placebo.py` (REF-placebo-runner, extracted from `meta/steady_placebo.py`); `tests/test_event_stats.py`; `results/_signal_tests.csv` log.
2. CHANGE: `pead/cli.py` add `signal` subcommand (no effect on existing commands; regression risk nil beyond argparse). `meta/steady_placebo.py` to import from `pead/core/placebo.py` (prototype; do not alter its numbers; check with `python meta/steady_placebo.py` before/after).
3. RESTRUCTURE: REF-placebo-runner (the placebo currently lives only in `meta/`, imports `from steady_grid import *` and mutates `sys.path`, so it cannot be called from tests or from `pead`). REF-stats-nodeps (numpy-only clustered regression since statsmodels is absent).
4. IMPROVE: no committed record of how many signal variants were looked at (garden of forking paths); `results/_signal_tests.csv` fixes that. `core/alpha.py` regresses with rf=0 and only the CRSP VW/EW factors; the investable small-cap benchmark (ROADMAP #1) is still missing, so alpha numbers from this harness inherit that caveat.

## 6. Acceptance tests
- Synthetic: 40 quarters x 200 events, y = 0.02*signal + noise; IC mean within 0.003 of the analytic Spearman; with y independent of signal, |t| < 2 in >= 90% of 200 seeded draws.
- Clustered OLS equals the textbook one-way cluster formula on a hand-built 3-cluster example; two-way reduces to one-way when clusters_2 is constant.
- Bootstrap: same seed -> identical CI (determinism); different seed -> CI endpoints differ by < 20% of width.
- BH-FDR on the known example p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216] at q=0.05 rejects exactly the first two.
- No look-ahead: rewriting every row with `day0_date > T` (signal and return) leaves IC rows for quarters <= T unchanged; `car_tradable_h` for an event does not read `ret_mktadj[entry_row_idx]` (assert by perturbing that element).
- Placebo reproduces `meta/steady_placebo.py` skill numbers for `steady_drift_plus` within noise (3 seeds there: +4.3 pts return, +3.9 pts alpha, +0.36 Sharpe per docs/STEADY_DRIFT.md section 9; do not hard-code, compare against a fresh run).
- Existing suite still green: `python -m pytest tests -q` (docs say 29 passed, 14 skipped w/o data).

## 7. Experiment plan
Run the harness first on the control `sue_rank_pct` (hold_ret, ret_fwd_60d_mktadj) to produce the repo's own IC-by-quarter table by era (1996-01 / 2002-07 / 2008-13, `metrics.ERAS`). Expected from docs: monotone but fading decile spread (cell-matched D9-10 minus D1-2: 364 -> 249 -> 142 bp). Then every later spec calls the harness for its columns. Baselines to beat are read from `results/leaderboard.md`: `steady_drift_plus` 12.07% / Sharpe 1.19 / MaxDD -22.07%; `steady_drift` 10.59% / 1.16 / -20.00%; `steady_drift_calm` 9.55% / 1.21 / -15.90%; `steady_drift_core` 11.99% / 0.93 / -40.47%. Acceptance gate for any new strategy (from docs/STEADY_DRIFT.md section 9): Sharpe >= baseline, alpha t >= 5.5, 2008-13 Sharpe >= 0.75, marked holdings < 95% NAV, and placebo skill not lower than the baseline's.

## 8. Expected outcome / hypotheses and risks
This is tooling; the outcome is a trustworthy yes/no. Risks: (a) quarterly IC on ~170 events/quarter is noisy; the mcravi8 IC of 0.018 shows what "weak" looks like, so effect sizes will be small and CIs wide; (b) two-way clustering with 71 quarters is borderline, so report one-way by quarter as primary; (c) data ends 2013, so "holdout" 2006-2013 is not true out-of-sample for the in-sample steady_drift design (docs/STEADY_DRIFT.md section 6).

## 9. Dependencies and implementation checklist
- [ ] REF-period-split constants
- [ ] REF-stats-nodeps functions + unit tests
- [ ] REF-placebo-runner extraction, parity run vs `meta/steady_placebo.py`
- [ ] `signal` CLI subcommand
- [ ] `results/_signal_tests.csv` writer
- [ ] Baseline IC table for `sue_rank_pct` committed under `results/_signal/`
- [ ] Docs line in `docs/ARCHITECTURE.md` (Running things)
