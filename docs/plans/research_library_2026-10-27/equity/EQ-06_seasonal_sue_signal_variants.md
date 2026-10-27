# EQ-06: Seasonal-SUE (earnings momentum) signal variants and analyst-vs-seasonal combinations, event-driven
- Status: planned | Priority: P1 | Effort: M | Source(s): library `strategies/03_QUANTCONNECT_SUE.md`, `foundations/01_SIGNAL_DEFINITIONS.md` (sections 5, 6); https://www.quantconnect.com/forum/discussion/20418/151-1-standardized-unexpected-earnings-sue-from-151-trading-strategies-to-18-year-backtest-reality/p1 ; lineage Foster-Olsen-Shevlin 1984, Kakushadze "151 Trading Strategies"

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Thesis: the surprise measure matters. `SUE_seasonal = (EPS_q - EPS_{q-4}) / std(8 prior seasonal changes)` is earnings MOMENTUM (improvement vs the same quarter last year), not a consensus beat; the two can disagree and carry different information. The repo already stores both `sue_analyst` and `sue_seasonal`, but every strategy ranks only the analyst flavor (`build_deciles.py` keeps `sue_source == "analyst"`). Testing flavors on identical machinery is the cleanest use of this source.
Take: the seasonal definition (denominator = 8 PRIOR changes, current change excluded), the top-5%/top-decile style cut, equal-weight/inverse-vol comparison, and the library's ablation list (top 5/10/20%, equal-weight vs inverse-vol, SUE vs consensus SUE, sub-periods).
Do NOT take: the QuantConnect construction (monthly rebalance of a 50-name coarse universe, so ~2-3 names at 5%; Morningstar fundamentals of unknown timing; `NullRiskManagementModel`; ImmediateExecution), the 23.576% / 13.619% CAGRs as targets, and the "likely overfitting" 23-parameter set. We use it event-driven so each position is created by a dated release, not by a monthly snapshot.

## 2. Evidence grade & known defects in the source
Library grade C: controller visible, `universe.py` and `alpha.py` (where the SUE maths and horizon live) NOT visible; results are backtest-platform outputs, not statements. Reported: 2021-2026 CAGR 23.576% / Sharpe 0.635 / DD -28.9% / 225 orders; 2007-2026 CAGR 13.619% / Sharpe 0.442 / Sortino 0.485 / PSR 0.495% / DD -63.9% / 976 orders; final equity $11.09M from $1M. The five-year window sits inside the long window (not independent). Orders are not trades, PSR is not a probability of profit, QuantConnect flagged "Likely Overfitting" (23 parameters). Possible look-ahead: the Morningstar-like EPS history may carry restated values or a fiscal-period timestamp instead of a release timestamp (library: "SEC period_end is not the public-release date"). Strong recent CAGR coexisting with weak long-window risk-adjusted performance suggests market/growth exposure or a few names.

## 3. Exact rules
Signal columns (EQ-02; all PIT):
- `sue_seasonal_raw`: existing column (`eps_diff_yoy / seasonal_diff_stdev`). FIRST audit it: recompute from `eps_actual`, `eps_year_ago`, and the prior diffs and compare to the stored value; check whether the stored denominator uses 8 PRIOR diffs excluding the current one (`seasonal_n_prior_diffs` should equal the count used), the minimum count, and `seasonal_window_span_days` (flag windows spanning > 3 years). Any mismatch with the library definition is reported and a recomputed column `sue_seas_8` (K=8 prior, min 6) is used instead.
- `sue_seas_rank`: PIT percentile, `pead.common.pit_rank.rank_pct(values, quarters, mode="prior_quarters")` (the same expanding strictly-earlier pool as the analyst rank; first quarter dropped). Decile cut `rank >= 0.9`.
- `sue_seas_dm` (optional, leeyawnnn flavor): `(D_q - mean(D_{q-1..q-8})) / std(D_{q-1..q-8})`, prior-only; report its rank correlation with the others; the library measured 0.20 rank correlation / 43% same-bucket agreement between a demeaned seasonal and a consensus SUE in another dataset - we measure ours.
Arms (each long-only, same machinery as `steady_drift_plus`: universe `avg_dollar_volume_21d >= 2e6`, hold 60 sessions at the day0 close, `W.causal_inverse_variance`, cap 0.80, name cap 1%, `risk_target_vol=0.12`, `long_only_cash=True`):
- `A` analyst top decile (= `steady_drift_plus`, control);
- `S` seasonal top decile (`sue_seas_rank >= 0.9`);
- `S5` seasonal top 5% (QuantConnect's `top_percent=0.05`) - fixed constant;
- `AND` analyst rank >= 0.9 AND seasonal rank >= 0.667 (confirmation);
- `OR` either top decile; `AVG` mean of the two PIT ranks >= 0.9 (equal-weight composite).
Positive-SUE requirement: the QuantConnect narrative implies SUE > 0; enforce `sue >= 0` in all seasonal arms.
Entry/exit/sizing/cost: exactly as the control; no stop. Overlapping events for one stock: the engine allows them (about 28% of positions overlap the same stock's next event per FIXES.md); keep and report.

## 4. Data requirements & availability in THIS repo
Available (verified columns in `data/earnings/ibes_clean_1995_2014.parquet` and `data/events/equity_events_1996_2013.parquet`): `eps_actual`, `eps_actual_adj`, `eps_year_ago`, `eps_diff_yoy`, `seasonal_n_prior_diffs`, `seasonal_diff_stdev`, `seasonal_window_span_days`, `sue_seasonal`, `sue_source`. Coverage: `sue_seasonal` notna for 86.7% of analyst-source events and 100% of seasonal-source events (55,782 events whose analyst SUE is missing; those are NOT in `equity_vol_panel` today because `build_deciles.py` filters to analyst). Missing: first-reported vs restated EPS vintages (IBES actuals are as reported by IBES; restatements are a risk we cannot rule out), fiscal calendar alignment checks, Compustat `fundq.parquet` exists in `data/raw_wrds/comp/` for an independent cross-check of EPS (rdq as release date) but is not integrated.
Phase 1 uses only events already in the vol panel (analyst-source). Phase 2 (optional) extends the event universe with the 55,782 seasonal-only events, which requires rerunning `build_deciles`, `positions`, `vol_features` with a selectable source - a data-build change (REF-event-universe).

## 5. Framework mapping
File: `pead/strategies/equity/steady_drift_seasonal.py`. Base class `SteadyDriftPlus` imported from `pead/strategies/equity/steady_drift.py`; subclasses override `weights()` only: names `steady_drift_seas` (S), `steady_drift_seas5` (S5), `steady_drift_seas_and` (AND), `steady_drift_seas_avg` (AVG), `steady_drift_seas_or` (OR). `family="equity-longonly"`, `parent="steady_drift_plus"`, `status="research"`, `legacy_twin=False`, `live_ready=False` (the live feed has no IBES seasonal; see ROADMAP item 3).
Params: `rank_cut=0.9, rank_cut_and=0.667, flavor="seasonal"`. `required_columns = ("day0_date","sue_rank_pct","sue_seas_rank","avg_dollar_volume_21d","ivol_252d")`.
Mask helper in `pead/strategies/weights.py`: `rank_mask(events, cols, mode, cuts)`; keep `weights()` a pure function of event columns.

## 5b. Build vs. change vs. restructure
(1) NEW: `steady_drift_seasonal.py`; EQ-02 columns `sue_seas_8`, `sue_seas_rank`, `sue_seas_dm`; `weights.rank_mask`; `tests/test_seasonal_sue.py`; audit script `pead/prep/equity/audit_sue_seasonal.py` (recompute and diff).
(2) CHANGE: none to engines. `pead/prep/equity/build_deciles.py` only in Phase 2 (make the `sue_source` filter a parameter; risk: it feeds `positions`, `vol_features` and the options chain, so existing deciles and every pinned NAV must remain byte-identical under the default).
(3) RESTRUCTURE: REF-event-feature-store (rank columns computed once with `pit_rank.rank_pct`); REF-event-universe (Phase 2: let the event universe include seasonal-source events with an `sue_source` flag so a strategy can choose).
(4) IMPROVE: `pit_rank` docstring notes decile sizes are uneven under an expanding pool (D10 share 8-17%); the seasonal rank has the same property, so also report a trailing-4-quarter pool variant as a diagnostic (FIXES.md says trailing pools were not evaluated); the 55,782 seasonal-only events are an unused data asset; no independent EPS cross-check against Compustat `fundq`.

## 6. Acceptance tests
- Unit: synthetic firm with 12 quarterly EPS values -> hand-computed `sue_seas_8`; current change excluded from sigma; fewer than 6 priors -> NaN; sigma = 0 -> NaN (logged), never inf.
- Equivalence audit: recomputed `sue_seas_8` vs stored `sue_seasonal` on a random 5,000-event sample; report the share equal to 1e-9 and the explanation of the rest. Do not proceed to P&L if the stored column uses a window that includes the current change (look-ahead in the denominator).
- No-look-ahead: truncation invariance (rewrite all EPS and returns after a cutoff; earlier ranks and positions unchanged); rank uses strictly earlier quarters (reuse the logic in `tests/test_fixes.py`); day0 return not earned; restated-EPS fixture: a later restatement of `eps_year_ago` cannot change an earlier event's score.
- Control equivalence: arm `A` reproduces `steady_drift_plus` final NAV to the dollar.
- Determinism and AND-subset property: `AND` events are a subset of `A` events and of `S` events.
- Fiscal fixture: a firm that changes fiscal year-end gets NaN for the affected quarter, not a mis-aligned seasonal diff.

## 7. Experiment plan
Step 0 (portfolio-free): Spearman and same-decile agreement between analyst and seasonal ranks by era; 3x3 double sort of 60-session `ret_fwd_60d_mktadj` (columns already in the event table) to see whether seasonal adds information after conditioning on analyst SUE. Step 1: arms A, S, S5, AND, OR, AVG on SEARCH (before 2008) then HOLDOUT (2008-13) - constants are the cuts above, fixed beforehand, not tuned. Placebo: shuffle the flavor's rank within quarter (3 seeds). Report skill-adjusted alpha (real minus placebo; `steady_drift_plus` skill = +3.9 pts alpha). Comparators from `results/leaderboard.md`: `steady_drift_plus` 12.07% / 1.19 / -22.07%, `steady_drift` 10.59% / 1.16. Era table (1996-2001, 2002-07, 2008-13) because the analyst-SUE skill fell 9.4 -> 7.9 -> 4.1 points; an earnings-momentum flavor that decays more slowly is the actionable finding. Ablations from the library: top 5/10/20%, equal-weight vs inverse-vol, size terciles. Decision: adopt a flavor/composite only if skill-adjusted alpha exceeds `steady_drift_plus`'s with t >= 2 and 2008-13 Sharpe is not lower; count trials (6 arms).

## 8. Expected outcome / hypotheses and risks
H1: seasonal top decile is weaker or similar to analyst top decile in 1996-2013 CRSP (analyst-based SUE usually dominates in modern data); H2: `AND` gives a smaller, higher-quality book (fewer names, so more hits on the 5%-of-volume cap, as STEADY_DRIFT shows 56% of positions capped when too few names), possibly better 2008-13 Sharpe; H3: any gain is mostly the universe, not skill (placebo). Risks: QuantConnect's own long-window result (Sharpe 0.44, DD -63.9%) says the seasonal strategy is market-dominated at its construction; our long-only cash-filled book differs; restated EPS; seasonal windows spanning fiscal changes; Phase 1 omits 55,782 events; data ends 2013, a window in which seasonal-SUE returns are not known to persist; capacity and impact not modelled.

## 9. Dependencies and implementation checklist
Depends on EQ-02 (rank columns), EQ-08 (invariance kit).
- [ ] audit stored `sue_seasonal` definition (recompute and diff)
- [ ] `sue_seas_8`, `sue_seas_rank`, `sue_seas_dm` in EQ-02
- [ ] `weights.rank_mask` and the 5 registered arms
- [ ] control equivalence test (arm A == `steady_drift_plus` NAV)
- [ ] Step 0 screens, Step 1 runs (SEARCH then HOLDOUT once), placebo
- [ ] decide on Phase 2 universe extension
