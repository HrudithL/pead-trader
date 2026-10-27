# OV-07: SUE-anchored multifactor composite rank with a walk-forward factor-IC harness

- Status: planned (gated on OV-02 / OV-05) | Priority: P2 | Effort: M | Date: 2026-10-27
- Sources: library `strategies/19_MULTIFACTOR_RANKING_SYSTEM.md`, `strategies/18_CASH_FLOW_QUALITY_MOMENTUM.md`, `strategies/20_FACTOR_BACKTESTER_CONCENTRATION.md`, `reproduction/04_PAIRED_FACTOR_AND_RISK_ABLATIONS.md` section 6, `audits/03_BACKTEST_FAILURE_MODES.md` section 5.

## 1. Thesis & what we take from source (and what we do NOT take)

Thesis: if OV-02 / OV-05 show that momentum or quality carries information incremental to SUE, a single causal score is a cleaner way to combine them than stacking filters, and a rank-IC table (SUE alone vs SUE + factor) is the right evidence format.

Take: the pipeline shape (winsorize 1-99%, cross-sectional z-score, sign flip, weighted sum), factor IC (Spearman score vs forward return), quintile spread, forward decay, and the sensitivity grids (size, cost, rebalance) as diagnostics; the rule "establish incremental IC against a fixed baseline before any learned weights".
Do NOT take: the hand-chosen weights (momentum 15/35/20, trend 15/10, liquidity 5), the 100-name fixed S&P 500 subset, 2020-2025 in-sample result (301.3% total return, 26.6% CAGR, Sharpe 1.08, -27.4% DD), top-10 equal-weight monthly portfolio, or the author's `LookaheadValidator` as proof of no leakage.

## 2. Evidence grade & known defects in source

Grade B/C (library row 19-20): fixed current names (survivorship), explicitly in-sample, no frozen out-of-sample, the factor IC and sensitivity plots are on the same 2020-25 data used to pick weights, an attractive composite equity curve "without an earnings-only ablation cannot establish incremental PEAD alpha" (library). Concentration: Factor Backtester's composite (momentum + price-high proxy) lifted CAGR to 40.79% but in a 20-name set where removing NVDA cost 11 points. Verdict: infrastructure ideas only.

## 3. Exact rules (codeable)

Event-level composite on the same selection base as the book (events with ADV >= $2M), using causal pool ranks (function from OV-02):
```
f_k      = percentile rank (causal, previous 365 days of events) of factor k, k in {sue_rank_pct, mom_rank_pct, quality_rank, lowvol_rank}
score_i  = f_sue_i + lambda * sum_k w_k * (f_k_i - 0.5)           # SUE-anchored: other factors can only nudge
select   : score_i >= 0.9   (same threshold as today), then causal inverse-variance weights as today
```
Pre-registered: `lambda in {0.10, 0.25}`; `w_k` equal across the factors that PASSED their own V0 diagnostic in OV-02/OV-05 (so at most 2 factors); no fitted weights in v1. Optional v2 (only if v1 survives): weights = shrunk trailing rank-IC, i.e. `w_k = 0.5 * IC_k_trailing / sum |IC|` where `IC_k_trailing` is the mean rank correlation of `f_k` with `ret_fwd_60d_mktadj` over events whose 60-day window ENDED before the current event's day0 (purge: `exit_date < day0_i`; column `exit_date` exists), min 12 quarters (mirrors `walk_forward_cell_filter` in `pead/strategies/vol.py`, which already enforces "positions had ALL exited before Q began").
IC harness (diagnostic, standalone): per announcement quarter, Spearman IC of each factor and of `score` vs `ret_fwd_60d_mktadj`; table of mean IC, IC t-stat clustered by quarter, quintile spread, by era; incremental IC = IC(score) - IC(sue alone) with paired bootstrap across quarters.
Selection-count control: report number selected per year; if lambda changes it by more than 20% compare with a random-reselection control of the same size.

## 4. Data requirements & availability in THIS repo

Available now: `sue_rank_pct`, `ivol_252d_pct` (leak-free trailing-vol percentile within size quintile, built in `pead/prep/equity/vol_features.py`), `ret_fwd_60d_mktadj`, `exit_date`, plus OV-02 momentum (computable). Quality only as ROA proxy (OV-05) until the `oancfy` pull. No market/ETF data needed. BLOCKED items: cash-flow quality (see OV-05); the library's liquidity and RSI/trend factors are irrelevant (liquidity is a floor already, trend unsupported by evidence here).

## 5. Framework mapping

- Files: `pead/validation/factor_ic.py` (new harness), `pead/strategies/equity/steady_drift_composite.py` (new), reuse `pead/core/event_features.py`.
- Registration: `steady_drift_plus_composite` (lambda 0.25 default, factors from config), no registration of the IC harness.
- Composition: subclass `SteadyDriftPlus`; `weights()` computes `score`, applies the existing mask and `causal_inverse_variance`.
- Params: `lam=0.25`, `factors=("mom_rank_pct",)`, `base_thr=0.9`, `pool_days=365`.
- Reuse (verified): `W.causal_inverse_variance`, `W.centered_rank`, `vol.walk_forward_cell_filter` (pattern for purged trailing statistics), `alpha_stats`.
- Engine changes: none.

## 5b. Build vs. change vs. restructure

(1) NEW
- `pead/validation/factor_ic.py`; composite strategy; `tests/test_factor_ic.py`.
(2) CHANGE
- `pead/strategies/weights.py`: add `causal_rank_pct` here (if OV-02 placed it in `event_features.py`, import from there instead; one definition only). Regression: pure addition.
- `pead/reporting/single.py`: optional IC table section.
(3) RESTRUCTURE
- REF-event-features (factor columns as registered features), REF-validation-suite, REF-variant-grid.
(4) IMPROVE
- Trial registry: log every (factor set, lambda) tried in `results/_experiments/trials.csv` so deflated-Sharpe style adjustments are possible (library audit 03 section 5); today the repo has no such log although it has run 35 + 31 variants by hand (STEADY_DRIFT).
- Purge/embargo helper reused by any trailing-statistic feature (currently duplicated ad hoc inside `walk_forward_cell_filter`).

## 6. Acceptance tests

- Purge: trailing IC for event i uses no event with `exit_date >= day0_i`.
- Composite with `lam=0` equals `steady_drift_plus` selection and NAV bit-for-bit.
- Score is invariant to rewriting future events (causal pool test).
- IC harness on synthetic data with a planted factor recovers the planted IC within tolerance; zero for noise.
- Determinism.

## 7. Experiment plan

Gate: run the IC harness first. Proceed to a backtest only if incremental IC of at least one added factor is positive in each of three eras (1996-2001, 2002-07, 2008-13) with clustered |t| >= 2 for the pooled sample. Baseline `steady_drift_plus` (12.07% / 1.19 / -22.07%). Variants: lambda 0.10 / 0.25 x one or two passed factors (max 4 runs). Acceptance rule and controls as OV-02 (random reselection, shuffled-SUE placebo, matched-count). OV-01 stress. If v2 (IC-weighted) is built, nested walk-forward: weights frozen at each quarter start; report separately from v1.

## 8. Expected outcome and honest risks

Likely no improvement: the repo's prior searches found nothing beating the plain top-decile rule except breadth. Composite scores re-rank near the 0.9 boundary, so the effect is mostly which marginal events get swapped, a small, noisy change that is hard to distinguish from selection noise. The risk of a fitted look-ahead through "which factors passed" is real: the factor choice must be made from the pre-2008 diagnostic or the choice is contaminated. Value of the item is the IC harness (reusable, cheap), even if the composite is rejected.

## 9. Dependencies and implementation checklist

Depends on: OV-01, OV-02, (OV-05 for quality), REF-event-features, REF-validation-suite, REF-variant-grid.
- [ ] IC harness + tests; run on SUE, momentum, ivol percentile
- [ ] Decide factors using pre-2008 evidence only (written before backtests)
- [ ] Composite strategy v1 (no fitted weights) and runs
- [ ] Trial log; stress; decision note
- [ ] v2 only if v1 is accepted
