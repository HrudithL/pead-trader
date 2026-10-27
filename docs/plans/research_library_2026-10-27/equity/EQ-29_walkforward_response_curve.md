# EQ-29: Walk-forward surprise-response curve (bucket / isotonic expected return per surprise level, estimated only on completed past events) as position weights
- Status: planned | Priority: P2 | Effort: M | Source(s): library `strategies/11_KPH3802_FMP.md` (https://github.com/KPH3802/pead-backtest, `pead_scoring.py` buckets, "lock rank cutoffs in a rolling estimation period"), `strategies/08_AARSH_DAYAL.md` (hump-shaped surprise response; isotonic/spline test), `foundations/01_SIGNAL_DEFINITIONS.md` section 2

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Two sources say the surprise-to-return map may be non-linear: KPH scores events by magnitude buckets (positive 5-10, 10-20, 20-30, 30-50, 50+ percent; negative 5-10, 10-20, 20-30, 30+) and Aarsh notes the very largest positive surprises need not drift most. The steady_drift rule is a hard cut (top decile) and the repo's 66 variants never fitted the shape. Take: estimate the expected post-entry market-adjusted return per surprise bucket using ONLY events already completed at the decision date, shrink towards the grand mean, and use it as a conviction multiplier on the existing causal inverse-variance weights. Do NOT take: KPH's in-sample ranking of buckets over the full backtest sample (the library flags exactly that overstatement), calendar-day horizons (2/4/8 weeks measured with `timedelta`), per-event mean "alpha" as performance, FMP data, and the short-side buckets (long-only).

## 2. Evidence grade & known defects in the source
KPH grade B: raw mean directional returns with iid t-tests; `market_cap` from current metadata; no portfolio NAV. Aarsh grade C: t=1.40, p=0.178 at quarter level; event slope p=0.0219. Neither is a performance anchor. Repo prior: decile-profile CSVs exist (`meta/debate2/A_event_decile_profile.csv`, `A_event_decile_spread_by_era.csv`); the 35-variant search found top decile best and a top-two-decile book 1.5-2 pts worse (docs/STEADY_DRIFT.md section 9), which argues the curve is steep at the top, not hump-shaped, at decile resolution; the unexplored part is the extreme tail (top 1-3%), and the percent-surprise (not rank) bucket.

## 3. Exact rules
Bucket variable `B` (two alternatives, pre-registered): (i) `rank_bucket`: PIT sue rank in bins [0.5,0.7), [0.7,0.8), [0.8,0.9), [0.9,0.95), [0.95,0.98), [0.98,1.0]; (ii) `pct_bucket`: `sue_pct` from EQ-21 with KPH's edges {5,10,20,30,50}% on the beat side (events with |M| < E0 excluded).
Target `y_i` = `hold_ret` (post-entry sum of daily mktadj returns over the 60-session hold; defined in `pead/prep/equity/vol_features.py`) for events in the pool.
Pool at decision date T (the event's day0): events whose EXIT date < T (fully completed; with a 60-session hold the pool lags by about 3 months). Expanding window, at least 1,500 events, else the weight multiplier is 1.0 for events passing the base rule.
Estimate: `mu_b = (n_b * ybar_b + m * ybar_all) / (n_b + m)`, m = 200 (proposed). Isotonic alternative: pool-adjacent-violators (PAV) fit of y on the bucket index (numpy implementation, weights = counts), refit quarterly.
Weight: base rule `sue_rank_pct >= 0.8` & ADV >= $2M (wider than top decile so the curve decides). `w_i = causal_inverse_variance(events, mask) * clip((mu_b - mu_floor) / (mu_top - mu_floor), 0, 1.5)` where `mu_floor = estimated mean of the 0.5-0.7 bucket`, `mu_top = estimated mean of the 0.9-0.95 bucket` (so the existing top-decile weighting gets multiplier about 1). Events with multiplier 0 get no position.
Sizing, hold, cash, risk target: identical to `steady_drift_plus`. Long-only.
Registered: `steady_drift_plus_curve_rank`, `steady_drift_plus_curve_pct`, `steady_drift_plus_curve_iso`.

## 4. Data requirements & availability in THIS repo
Available: `hold_ret`, `exit_date`, `sue_rank_pct`, ADV (vol panel); `sue_pct` after EQ-21. Missing: nothing. Caveat: `hold_ret` in the panel is computed on the panel's per-cell holding window (`holding_days`), while steady_drift uses a fixed 60-session window; compute the pool target from `uni.ret_mktadj` over the same fixed window as the trade (`with_fixed_hold` rows), not the stored `hold_ret`, to avoid a mismatch between estimate and trade (add as a derived array inside the weight builder or as a side-table column `ret_fix60` in EQ-22's stage).

## 5. Framework mapping
- Side-table column `ret_fix60` (post-entry sum of `ret_mktadj` over rows entry+1..min(entry+60, last)) and `exit_date_fix60` added by a new prep stage `fixed_horizon_returns` (`pead/prep/equity/fixed_horizon_returns.py`) using the same last-row logic as `pead/engines/equity.py::with_fixed_hold`.
- Weight function `curve_multiplier(events, bucket_col, ...)` in `pead/strategies/weights.py` (pure function of events columns, so the live engine can call it; keep it free of data loading). It is causal by construction: for each event, pool = events with `exit_date_fix60 < day0_date`; implement with sorted arrays and `searchsorted` exactly like `causal_inverse_variance`.
- Strategy file `pead/strategies/equity/steady_drift_curve.py`. `required_columns` includes `ret_fix60, exit_date_fix60, day0_date`.
- Engine change: none.
- Params: `m_shrink=200`, `min_pool=1500`, `base_rank=0.8`, `mult_cap=1.5`, `bucket="rank"|"pct"|"iso"`.

## 5b. Build vs. change vs. restructure
1. NEW: `fixed_horizon_returns.py`; `curve_multiplier`; `steady_drift_curve.py`; `pead/core/isotonic.py` (PAV, ~30 lines); `tests/test_curve_weights.py`.
2. CHANGE: `pead/strategies/weights.py` (new function only; existing ones untouched); `pead/prep/stages.py::STAGES`; `pead/core/data.py::_load_events` (REF-event-feature-store).
3. RESTRUCTURE: REF-event-feature-store; REF-event-key. Reconcile `hold_ret` (cell windows) vs fixed 60-session returns: document in `vol_features.py` docstring that steady_drift-family estimates must use the fixed-window column.
4. IMPROVE: the engine loop in `weights.causal_inverse_variance` is O(days) Python over unique days (fine); `curve_multiplier` must avoid O(events x pool): use cumulative sums per bucket indexed by completion order. Unit-test performance on 170k events (< 10 s).

## 6. Acceptance tests
- Synthetic: y generated as a hump over buckets; the estimated curve converges to the truth as the pool grows; before `min_pool`, multiplier == 1.
- No look-ahead (essential): rewrite `ret_fix60` for every event whose exit_date >= T; the multiplier for events with day0 <= T is unchanged. Also test the boundary: an event exiting on the same day as another's day0 is NOT in that pool.
- Monotone invariant for iso variant: fitted curve non-decreasing.
- Determinism; NaN `ret_fix60` events never enter the pool.
- No-borrow check: marked holdings <= 95% NAV.

## 7. Experiment plan
Descriptive first (EQ-30): realised mean `ret_fix60` per bucket by era with season-block bootstrap CIs, for both bucket types, including the 98-100% tail (hump check). Then strategies vs `steady_drift_plus` 12.07% / 1.19 / -22.07% and `steady_drift` 10.59% / 1.16 / -20.00%, gate as in EQ-30. Ablations: m_shrink {100, 200, 500}, base_rank {0.7, 0.8}, pool length cap (trailing 5 years vs expanding), mult_cap {1.0, 1.5}. Search 1996-2005 for hyper-parameters, holdout 2006-2013 once; shuffled-rank placebo (shuffle SUE rank within quarter, which destroys the learned curve; skill must exceed the control's +4.3 pts return / +3.9 pts alpha).

## 8. Expected outcome / hypotheses and risks
H1: curve steep and monotone through 0.95, mild flattening in the top 2% (weak hump, insignificant at the quarter-block level). H2: `curve_*` strategies match `steady_drift_plus` within noise and mainly change the number of positions. Honest prior: no improvement beyond noise, in line with the earlier 66-variant record. Risks: learning lag of about 3 months and a regime shift in the curve (decay, EQ-28); pool size small early (1996-2000 has few completed events, so early performance equals the baseline by construction); added hyper-parameters; extreme tail buckets thin (about 2% of 170k events = 3.4k events total).

## 9. Dependencies and implementation checklist
- [ ] EQ-21 (`sue_pct`) for the pct variant (rank variant has no dependency)
- [ ] `fixed_horizon_returns` stage
- [ ] REF-event-feature-store, REF-event-key
- [ ] `curve_multiplier` + causality tests + performance test
- [ ] Strategies registered; EQ-30 harness; hyper-parameter choice on search window only
