# OV-02: 12-1 momentum as a paired factor with SUE (confirmation filter / weight tilt)

- Status: planned | Priority: P1 | Effort: M | Date: 2026-10-27
- Sources: library `strategies/15_CROSS_SECTIONAL_MOMENTUM.md`, `strategies/22_FINLAB_MOMENTUM_BREADTH.md`, `strategies/20_FACTOR_BACKTESTER_CONCENTRATION.md`, `reproduction/04_PAIRED_FACTOR_AND_RISK_ABLATIONS.md` (grid: "Trend: no filter; 12-1 momentum; SMA200 gate"), `audits/06_..._DISCREPANCIES.md` section A.

## 1. Thesis & what we take from source (and what we do NOT take)

Thesis: among top-SUE-decile announcers, stocks with positive intermediate-term price momentum (12-1) may drift more (information confirmed by the market) and stocks with negative momentum may be value traps or low-quality beats. Also a control: part of the "surprise" return could be momentum that SUE merely proxies.

Take: the 12-1 definition and its one-month skip; the idea that momentum should be treated as a control factor and tested as `SUE x momentum` interaction versus additive (library stefsamaha section 8).
Do NOT take: any return level (Sharpe 1.31 / 28.7% CAGR equivalent), the 40-name current-winner universe, the full-sample `min_coverage=.95` eligibility screen, the long/short default script, or FinLab's 1.84 pp/month decile spread (a top-500 liquid-US universe, 2016-2026, ETF-gate engine; our universe is all CRSP announcers with a $2M ADV floor, 1996-2013).

## 2. Evidence grade & known defects in source

- stefsamaha: grade C. README says long-only top decile (Sharpe 1.31, DD -23.5%); default `run_backtest.py` builds a 100%/100% long-short book; vol overlay absent from the script; 40 hand-picked current large caps (survivorship); coverage screen uses future data; zero costs; Config end 2025 vs README 2026.
- FinLab momentum: grade C. Proprietary data/engine; momentum breadth threshold "never switched risk off", so the ETF CAGR (29.9%) is the QQQ trend + ETF engine, not the factor.
- kelvinli: grade B but 20 survivors and NVDA-dependent (37.01% to 26.10% CAGR).
Conclusion: the library gives a definition and a hypothesis, no admissible evidence that momentum improves a PEAD book. Everything must be established on our own data.

## 3. Exact rules (codeable)

Feature, per event i with panel row `e = entry_row_idx` (the day0 row; the engine earns from `e+1`, so everything through row `e` is known at the day0 close):
```
lr = log1p(ret_raw)                       # uni.ret_raw (ret filled 0 where missing)
C = cumsum(lr)                            # over the (permno, date)-sorted panel
mom_12_1_i = exp(C[e-21] - C[e-252]) - 1  # return from row e-252 to row e-21 (skips the last 21 sessions incl. day0)
```
Valid only if `panel_permno[e-252] == panel_permno[e]` (same stock the whole way) and `panel_date[e-252]` is 330..400 calendar days before `panel_date[e]`; otherwise NaN. NaN momentum: event keeps weight under V1/V2 only if `na_policy="neutral"` (rank 0.5), default drop under V1, neutral under V2.

Causal cross-sectional rank (same construction as `weights.causal_inverse_variance`): `mom_rank_pct_i` = percentile of `mom_12_1_i` among events whose `day0_date` lies in `[day0_i - 365d, day0_i)` (strictly earlier days), pool >= 200 else NaN. Rank among announcers (not all stocks) is deliberate: it matches how `sue_rank_pct` is built and needs no full-panel cross-section.

Selection base is unchanged from `steady_drift_plus`: `sue_rank_pct >= 0.9`, `avg_dollar_volume_21d >= 2e6`, then `causal_inverse_variance`.

Variants (fixed in advance; no others without a new pre-registration):
- V0 diagnostic only (no backtest): within the selection base, terciles of `mom_rank_pct`; report mean `ret_fwd_60d_mktadj` (column exists in `equity_vol_panel.parquet`, verified) and mean `hold_ret`, by era (1996-2001, 2002-07, 2008-13); regression `ret_fwd_60d_mktadj ~ sue_rank_pct + mom_rank_pct + sue_rank_pct*mom_rank_pct` on ALL events with quarter fixed effects and SEs clustered by `ann_quarter`. Go/no-go for V1-V3: tercile spread (top minus bottom) has the same sign in all three eras and clustered |t| >= 2.
- V1 filter: mask &= `mom_rank_pct >= 0.5`.
- V2 tilt: weight *= `(0.5 + mom_rank_pct)` (mean ~1, range 0.5-1.5); NaN -> 1.0.
- V3 contrarian control: mask &= `mom_rank_pct < 0.5`. If V3 ~ V1 the effect is noise or a pure "fewer names" effect.
- V4 no-op control with same number of events as V1 removed at random (seeded, 20 draws): the null for "dropping half the names".
Timing: decision at day0 close, same convention as the existing book (entry at the day0 close; STEADY_DRIFT section 3).

## 4. Data requirements & availability in THIS repo

Available: `uni.ret_raw`, `uni.panel_permno`, `uni.panel_date`, `events.entry_row_idx`, `events.day0_date` (all in `EquityUniverse` / `equity_vol_panel.parquet`). `ret_raw` is CRSP total return with NaN filled 0 (`pead/core/data.py::_load_panel`) so gaps look like flat days; the date-span check above is the guard. No price level needed. Warm-up: events before ~1996-04 have no 252-row history (panel loads from 1995, `PANEL_YEARS = range(1995, 2015)`), so first events are NaN. No ETFs or other markets involved. Delisting returns: CRSP `ret` includes DLRET handling only if the extract did; unverified here (flag, check `prep`).

## 5. Framework mapping

- Files: `pead/core/event_features.py` (new; function `mom_12_1(uni) -> np.ndarray`, `causal_rank_pct(values, day0, window_days=365, min_pool=200)`), `pead/strategies/equity/steady_drift_mom.py` (new, auto-registered).
- Registration names: `steady_drift_plus_momfilter` (V1), `steady_drift_plus_momtilt` (V2), `steady_drift_plus_momcontra` (V3, status `reference`, control only). Parent `steady_drift_plus`; subclass it and override `weights()` and `extra_features`.
- Composition: wrapper-by-subclass of `SteadyDriftPlus` (so same `Sizing`, 12% throttle); mask/tilt applied to the output of `W.causal_inverse_variance(events, mask)`.
- Params: `mom_skip=21`, `mom_lookback=252`, `mom_cut=0.5`, `tilt_floor=0.5`, `na_policy`.
- Reuse (verified names): `W.causal_inverse_variance`, `W.centered_rank`, `pead.strategies.vol.IVOL_CLIP`, `SteadyDriftPlus` (`pead/strategies/equity/steady_drift.py`), `with_fixed_hold`, `apply_cash_and_risk_target`.
- Engine changes: none.

## 5b. Build vs. change vs. restructure

(1) NEW
- `pead/core/event_features.py` (feature registry: `mom_12_1`, `causal_rank_pct`; disk cache `data/cache/event_features_<hash>.parquet` git-ignored, keyed like `equity_panel.npz` on source size/mtime).
- `pead/strategies/equity/steady_drift_mom.py`; `tests/test_event_features.py`; V0 diagnostic script/function `pead/validation/factor_diag.py` (tercile table + clustered regression).
(2) CHANGE
- `pead/strategies/base.py::EquityStrategy`: new attribute `extra_features: tuple = ()`; in `backtest`, after `with_fixed_hold`, call `attach_event_features(uni, self.extra_features)` which returns a universe whose `events` has the extra columns (new copy; never mutates cached universe). Why: `weights(events)` must stay a pure function of event columns (class docstring in `base.py`). Regression risk: none for strategies with `extra_features=()` (assert bit-identical NAVs).
- `EquityStrategy.params()` add `extra_features` for the summary.
(3) RESTRUCTURE
- REF-event-features: a causal per-event feature registry in `pead/core/event_features.py` consumed by OV-02, OV-03 (entry-gate variant), OV-05, OV-06, OV-07. Contract: pure function of `EquityUniverse`, uses only rows with index <= `entry_row_idx`, returns a Series aligned to `uni.events`.
- REF-variant-grid and REF-validation-suite (see `_ENABLING_CHANGES.md`).
(4) IMPROVE
- `ret_raw` zero-fill of missing returns hides gaps (REF-panel-hygiene): add a `panel_gap` boolean array or at least a date-span guard used here.
- Live engine cannot compute these features: keep `live_ready = False` (`pead/live` is not changed).
- Known open issue from STEADY_DRIFT section 6: size/liquidity breakpoints are cross-sectional within the announcement quarter (small look-ahead). Do not copy that pattern into the new features.

## 6. Acceptance tests

- No look-ahead: build a synthetic panel, compute `mom_12_1`, then overwrite all returns after row `e` with random noise: feature unchanged for event at `e`. Also perturb rows `e-20..e` (the skipped month): feature unchanged.
- `causal_rank_pct` unchanged when any later event is deleted or rewritten (copy the pattern of the `causal_inverse_variance` test in `tests/test_steady_drift.py`).
- Cross-permno guard: event whose lookback spans a different permno gets NaN.
- Known-value test: constant daily return r over 252 rows gives `(1+r)**231 - 1`.
- Determinism: two runs produce identical NAV; V4 random control seeded.
- Regression: `extra_features=()` strategies identical to committed results.

## 7. Experiment plan

Baseline: `steady_drift_plus` (leaderboard 12.07% / Sharpe 1.19 / DD -22.07%; section-9 window 1997-07: 12.4% / 1.14 / -22.1%, alpha 8.2% t 6.4, 2008-13 Sharpe 0.79, placebo 7.3-8.8%). Search window: events with `day0_date` 1996-2007 for choosing nothing (all variants fixed above); report 2008-13 separately but it is NOT a holdout (STEADY_DRIFT: design chosen after seeing 1996-2013). Acceptance rule reused from the repo's grid (STEADY_DRIFT section 9): Sharpe >= baseline, alpha t >= 5.5, 2008-13 Sharpe >= 0.75, marked holdings < 95% NAV; additionally the improvement must exceed the 5th-95th range of V4 random-drop controls and survive the shuffled-SUE placebo comparison (skill-adjusted alpha must not fall). Then OV-01 stress on any survivor; leave out the largest year. Ablation: V1 vs V2 vs V3 vs V4; also momentum-only top decile (no SUE) as a pure momentum benchmark inside the same engine to measure the SUE-versus-momentum overlap (check: correlation of daily returns, overlap of selected events).

## 8. Expected outcome and honest risks

Prior: small. The repo already found 35 variants "none beat the top-decile rule by more than noise" (STEADY_DRIFT section 2D) and 31 more in section 9. Momentum could reduce volatility (drop falling knives) but also reduces breadth, which the repo found to be the only thing that raised Sharpe (more names). Risks: halves the event count (more 5%-ADV cap bites unless base size raised), momentum crash exposure in 2009 rebound (low-mom stocks rally hardest, a V1 filter would miss them), correlation of momentum with high vol which inverse-variance weights already penalise. Most likely outcome: V0 shows no robust gradient and the item closes cheaply; that is a valid result.

## 9. Dependencies and implementation checklist

Depends on: OV-01 (stress), REF-event-features, REF-variant-grid, REF-validation-suite (placebo).
- [ ] `event_features.py` with `mom_12_1` and `causal_rank_pct` plus tests
- [ ] `EquityStrategy.extra_features` + `attach_event_features` in `backtest`
- [ ] V0 diagnostic (tercile table, clustered regression); write go/no-go to `results/_experiments/ov02_v0.md`
- [ ] If go: V1-V4 runs, placebo, OV-01 stress, era table
- [ ] Update docs/STEADY_DRIFT.md only if accepted; record all variants tried (trial count) in the experiment note
