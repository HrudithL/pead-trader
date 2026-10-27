# OV-03: Market trend and earnings-breadth exposure gate (with a gate-only ablation)

- Status: planned | Priority: P1 | Effort: M | Date: 2026-10-27
- Sources: library `strategies/02_FINLAB.md` (sections 5, 7, 9), `strategies/16_FINLAB_TEN_SYSTEM_FAMILY.md` (sections 1, 5, 7), `strategies/22_FINLAB_MOMENTUM_BREADTH.md`, `audits/06_..._DISCREPANCIES.md` section D, `comparisons/03_...` ("Approval checklist": earnings component isolated from baseline market momentum).

## 1. Thesis & what we take from source (and what we do NOT take)

Thesis: Steady Drift's remaining weakness is market drawdown (DD -22.07% for `steady_drift_plus`, -40.47% with no throttle, 2008 and 2000-02 episodes). The existing 60-day volatility throttle reacts with a lag. A faster, price-based regime brake (index below its 200-day average / negative 126-day momentum) and/or a measure of how healthy the current earnings season is (breadth) might cut exposure earlier.

Take: the gate definitions (`QQQ>SMA200 and 126d return>0`, 20-session breadth of "good beat with positive reaction" events) translated to our data: CRSP value-weighted index instead of QQQ, our PIT SUE rank instead of EPS-surprise>5%, and the paired ablation (gate-only vs gate+breadth).
Do NOT take: TQQQ/TECL/IEF/GLD/SHY rotation, any leverage, monthly rebalance + 8% touched stop, or any CAGR (32.0%, 29.2% OOS). Do NOT assume breadth adds anything: the source never ran "gate only, breadth disabled" and says the QQQ trend dominates long risk-off stretches; for the momentum variant breadth never switched risk off.

## 2. Evidence grade & known defects in source

Grade C (library matrix row 02): proprietary data and engine, refreshed twice (39.5% in June to 32.0% in October 2026 after dividend/split/stop-cost corrections), 2016-2026 only (one bull market for Nasdaq), 3x ETFs where market timing dominates (library: "do not credit leveraged CAGR to direct PEAD"). Decile table non-monotonic (D8 1.80% > D10 1.34% monthly). The only parameter evidence is a 3x3 neighbourhood where 150d and 200d columns were identical. All five stock-factor systems share MDD -21.1%, i.e. the risk engine, not the factor, sets the tail. Conclusion: the breadth idea is unproven even in its own source; the trend gate is a generic market-timing rule whose value on a cash-filled long-only small/mid-cap book is an open empirical question. Effective independent regimes in our sample: essentially 2000-02, 2007-09 (two); so any improvement is weak evidence.

## 3. Exact rules (codeable)

Market state series on `uni.calendar` (length n), from `uni.vwretd` (CRSP value-weighted, total return):
```
idx_t   = cumprod(1 + vwretd_t)
sma_t   = rolling_mean(idx, 200, min_periods=200)
mom_t   = idx_t / idx_{t-126} - 1
trend_t = (idx_t > sma_t) & (mom_t > 0)          # NaN during warm-up -> treated as ON
```
Earnings breadth (uses only events with `day0_date <= t`, i.e. known at close of t):
```
E_t    = events with day0_date in the last 20 sessions up to and including t
good_t = |{i in E_t : sue_rank_pct_i >= b_thr and ret_day0_i > 0}|      # ret_day0_i = uni.ret_raw[entry_row_idx_i] (day0 reaction)
B2_t   = good_t / |E_t|           if |E_t| >= 150 else NaN               # share of recent announcers (primary)
B1_t   = good_t / N_listed_t      # FinLab-like: N_listed_t = number of distinct permnos with a panel row on t (variant)
breadth_ok_t = (B2_t > c) or isnan(B2_t)
```
Defaults to pre-register: `b_thr = 0.75` (approx "clear beat"; sensitivity 0.6, 0.9), threshold `c` is a fixed constant, never fitted to returns. The placeholder grid `c in {0.15, 0.20, 0.25}` is only a guess: the first step is a diagnostic that prints the observed distribution of B2 (no returns involved), after which the three `c` values are fixed once, before any backtest. The 3x3 neighbourhood grid is those 3 values of c x SMA window {150, 200, 250}.
Gate: `gate_t = trend_t and breadth_ok_t` (cells below). Exposure: the throttle is `e_t = min(1, target/vol_{t-1})` in `apply_cash_and_risk_target`; the gated exposure is `e'_t = e_t * (1 if gate_{t-1} else s_off)`, `s_off in {0.0, 0.5}`, applied to day t's return (signal through close t-1, `shift(1)`). Cost: existing `cost_bps=10` per unit change of exposure; off-portion earns `rf` (existing `(1-e)*rf`).
Paired ablation cells (all on identical positions): G0 none (baseline); G1 trend only; G2 breadth only; G3 trend+breadth; G4 placebo gate (random ON/OFF series with the same ON fraction and same mean run lengths as G3, 500 seeds; reports the distribution of Sharpe/DD changes obtainable by ANY de-risking at those durations); G5 growth regime (exploratory): `median(sue_seasonal of events in last 63 sessions) > 0` (requires joining `sue_seasonal` from `data/events/equity_events_1996_2013.parquet`; not in the vol panel). Variant B (entry gate): events with `gate_{day0}` false get weight 0 (column `gate_on_day0` via REF-event-features) so the pure `weights()` contract holds; this changes only new entries and is a weaker brake.
Primary decision statistic: paired daily difference `r_G3 - r_G1` and `r_G1 - r_G0` with moving-block bootstrap (block 21 sessions) CI (library paired protocol, `reproduction/04` section 7), plus fraction of days where breadth changes the decision.

## 4. Data requirements & availability in THIS repo

Available: `uni.vwretd`, `uni.calendar`, `uni.ret_raw`, `events.entry_row_idx`, `events.sue_rank_pct`, `events.day0_date` (all in `EquityUniverse`). T-bill series is typed from memory (`pead/core/rates.py`, ROADMAP item 2): when the gate shifts capital to cash this assumption matters more than before (2000-03 yields 1.0-5.8%); fixing it is a prerequisite for quoting any number. No ETFs, no QQQ/TECL data: the Nasdaq proxy is unavailable, so the trend index is the CRSP VW market (a different, less volatile series). `sue_seasonal` only in the raw events file (join on `permno, day0_date`; uniqueness must be verified because `n_events_same_permno_day0` exists). BLOCKED: nothing; but "growth regime" variant G5 uses a proxy (seasonal SUE) since the library itself never defines an exact growth gate.

## 5. Framework mapping

- Files: `pead/core/market_state.py` (new: `trend_gate(vwretd, sma=200, mom=126)`, `earnings_breadth(events, uni, window=20, b_thr=0.75, n_min=150)`, `gate_series(...)`), `pead/strategies/equity/steady_drift_gate.py` (new).
- Registration: `steady_drift_plus_trend` (G1), `steady_drift_plus_gate` (G3), `steady_drift_plus_gate_half` (`s_off=0.5`), `steady_drift_plus_breadth_only` (G2, control, status `reference`).
- Composition: subclass `SteadyDriftPlus`; set `exposure_gate = ("trend","breadth")`; `EquityStrategy.backtest` obtains `m = self.exposure_overlay(uni)` and passes `exposure_mult=m` into `apply_cash_and_risk_target`.
- Params: `sma_window=200`, `mom_window=126`, `breadth_window=20`, `b_thr=0.75`, `c`, `s_off=0.0`, `n_min=150`.
- Reuse: `apply_cash_and_risk_target` (`pead/engines/equity.py`; vol estimate there uses the un-overlaid return series `r`, so the gate does not distort the vol estimate), `daily_rf`, `summarize`, `alpha_stats`, `uni.extras` dict (exists on `EquityUniverse`; unused today).
- Engine change: one optional argument (below).

## 5b. Build vs. change vs. restructure

(1) NEW
- `pead/core/market_state.py`; `pead/strategies/equity/steady_drift_gate.py`; `pead/validation/paired.py` (paired daily-difference bootstrap, placebo-gate generator `random_gate_like(gate, n=500, seed)`); tests `tests/test_market_state.py`.
(2) CHANGE
- `pead/engines/equity.py::apply_cash_and_risk_target(run, rf, initial_capital, target_vol, lookback, cost_bps, cash_yield)`: add `exposure_mult: np.ndarray = None`; compute `e = e * exposure_mult` after the `min(1, ...)` line and before the `de = np.abs(np.diff(e, prepend=1.0))` cost line; record `out["gate_mult"]`. Assert `exposure_mult` is already lagged (caller's responsibility, documented) and `0 <= m <= 1` (never levers). Regression risk: none when `None`; verify bit-identical NAVs for all four `steady_drift*` against committed results.
- `pead/strategies/base.py::EquityStrategy.backtest`: call `self.exposure_overlay(uni)` (default returns None) and pass through; condition `if self.long_only_cash or self.risk_target_vol` must also fire when only a gate is set (a gate with `risk_target_vol=None` is legal).
(3) RESTRUCTURE
- REF-exposure-hook: the composable exposure overlay interface described above (strategy-level hook + engine argument) instead of copying the throttle function per overlay.
- REF-market-state: causal daily market-state panel (trend, breadth, later volatility/dispersion) computed once from `EquityUniverse` and cached in `uni.extras`.
- REF-event-features (for variant B) and REF-validation-suite/REF-placebo.
(4) IMPROVE
- Replace the typed-from-memory T-bill (REF-tbill-real) before judging a gate that moves money to cash.
- Reporting: `daily.csv.gz` already carries `risk_exposure`, `idle_cash_frac`; add `gate_mult` and per-regime attribution in `pead/reporting/single.py`.
- The portfolio must stay long-only: add a test that `risk_exposure * gate_mult <= 1` always (no-borrow invariant).

## 6. Acceptance tests

- Causality: rewriting `vwretd` and event data after date t leaves `gate_s` unchanged for all `s <= t`; and the exposure applied on day t depends only on gate through t-1 (mirror the throttle's existing test in `tests/test_steady_drift.py`).
- Breadth boundary test: events on day t enter `E_t` only if `day0_date <= t`; an event on t+1 changes nothing at t.
- Warm-up: first 199 sessions gate ON; NaN breadth (|E|<150) treated as ON.
- Gate-off with `s_off=0` yields daily return exactly `rf - cost` on flat days (synthetic).
- Invariants: `0 <= e' <= 1`; `gate=all ON` equals baseline NAV bit-for-bit.
- Placebo generator reproduces ON fraction and mean run length within tolerance; seeded determinism.

## 7. Experiment plan

Baseline: `steady_drift_plus` (leaderboard 12.07% / 9.97% vol / Sharpe 1.19 / DD -22.07%); also `steady_drift_calm` (8% target: 9.55% / 1.21 / -15.90%) is the right comparator for "reduce risk without a gate", since a lower target is the trivial alternative to a gate. A gate only counts if it beats the equal-average-exposure ablation: run `steady_drift_plus` at the risk target that matches the gate's average exposure (`avg_risk_exposure` in summary). Windows: develop on 1997-2007 (includes 2000-02), evaluate on 2008-13 but note it is not a clean holdout (design already tuned on it, STEADY_DRIFT limits). Pre-registered acceptance (repo rule, STEADY_DRIFT section 9): Sharpe >= baseline, alpha t >= 5.5, 2008-13 Sharpe >= 0.75, marked holdings < 95% NAV; plus (a) max drawdown improves by more than the G4 placebo-gate median, (b) improvement is not confined to one episode (OV-01 LOO-year: drop 2008 and drop 2000-02 each; benefit must remain in at least one), (c) parameter neighbourhood 3x3 all pass. Report G1 vs G3 first; if G3 does not beat G1 the breadth component is dropped and the item reduces to a plain trend gate.

## 8. Expected outcome and honest risks

Expected: trend gate reduces 2008 drawdown but also lags rebounds (2009), cost 10 bp per switch plus T-bill carry; the vol throttle already de-risks after volatility rises, so incremental drawdown benefit may be small. Breadth likely redundant (source implies the same). Risks: (i) timing rules fitted to two crashes; (ii) whipsaw in 2002-03 and 2010-11 (STEADY_DRIFT lists 2002 -0.1%, 2008, 2011 -1.8% as the only down years, so the book is already benign: upside to improvement is small); (iii) turning the book into a market-timing strategy contradicts the current "constant risk" design principle; (iv) the CRSP VW trend is not the Nasdaq trend used in the source. If accepted, status stays `research`. Reject if G1 does not beat the matched-exposure baseline.

## 9. Dependencies and implementation checklist

Depends on: OV-01, REF-exposure-hook, REF-market-state, REF-validation-suite (paired bootstrap, placebo), REF-tbill-real (strongly recommended), REF-event-features (variant B only).
- [ ] `exposure_mult` argument + hook with regression test
- [ ] `market_state.py` with tests (causality, warm-up, boundaries)
- [ ] First diagnostic: print distribution of B2 and duty cycle of trend gate; THEN fix `c` grid
- [ ] Run G0..G4 (and G5 if join verified), matched-exposure baseline, 3x3 neighbourhood
- [ ] Paired block bootstrap CIs; OV-01 stress; placebo-SUE comparison
- [ ] Decision note in `results/_experiments/ov03.md`; docs update only if accepted
