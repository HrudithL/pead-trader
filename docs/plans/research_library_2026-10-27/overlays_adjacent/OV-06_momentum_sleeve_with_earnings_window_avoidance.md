# OV-06: Non-PEAD momentum sleeve for idle cash, with an earnings-window avoidance rule

- Status: planned (conditional) | Priority: P2 | Effort: L | Date: 2026-10-27
- Sources: library `strategies/21_QUANT_SCANNER_EARNINGS_AVOIDANCE.md`, `strategies/15_CROSS_SECTIONAL_MOMENTUM.md`, `strategies/22_FINLAB_MOMENTUM_BREADTH.md`, `strategies/20_FACTOR_BACKTESTER_CONCENTRATION.md`, `audits/06_..._DISCREPANCIES.md` sections A, E.

## 1. Thesis & what we take from source (and what we do NOT take)

Thesis: `steady_drift_plus` holds stock only part of the time (cap 80% of NAV, scaled by the throttle) and the rest earns the T-bill (about 2.4% average over 1997-2013 per STEADY_DRIFT). The repo's "Drift Core" filled idle money with the index (13.1% but DD -53.6%, beta 0.96) and the cash fill was chosen as the safer option. A long-only, unlevered 12-1 momentum sleeve is another possible fill with a different return source; and any non-PEAD sleeve should not carry unmanaged earnings-jump risk, because a rule that does not know about earnings can behave very differently across the announcement (quant-scanner ablation).

Take: (i) 12-1 definition (see OV-02), (ii) an earnings-proximity exclusion as a risk rule for non-PEAD positions, (iii) the four-cell design from the library: base signal outside the window / before the release / immediately after / explicit SUE rule.
Do NOT take: any momentum return (Sharpe 1.31 on 40 survivors, 37.01% CAGR on 20 survivors), long/short momentum (shorts need borrow; repo cannot model it; the long/short default script lost badly in the source's own table: Sharpe 0.28, DD -66.7%), the volatility-target leverage up to 2x (borrowing is out of scope), or the `-2.08%` 5-day figure as an effect size.

## 2. Evidence grade & known defects in source

- quant-scanner earnings ablation: grade B/C. 28 signals in the "near earnings" group vs 224 others, 50 S&P large caps, 2024-2025 only, a mean-reversion entry rule (RSI<35, below 20d MA, above 200d MA) not a momentum rule; no matched controls; the library itself says it is a "risk exclusion rule, not evidence of net-positive PEAD alpha". Transport to a 12-1 momentum sleeve is unestablished.
- Momentum sources: see OV-02 (survivorship, README vs code mismatch, NVDA dependence).
- Also relevant: audit 2026-10-26 (`docs/RETURN_TARGET_AUDIT_2026-10-26.md`): "Adding standalone reported CAGRs or blending their saved NAVs cannot establish the return of the combined funded account"; sleeve combinations must share one cash ledger.

## 3. Exact rules (codeable)

Sleeve (monthly, long-only, unlevered), formation date = last trading day of month m (index `t`), using data through `t`:
1. Universe: panel stocks with `avg_dollar_volume_21d`-equivalent >= $2M (compute from `prc*vol` rolling 21 sessions; BLOCKED for events-table reuse because non-event stocks are not in `equity_vol_panel.parquet`, see section 4) and not in the bottom size quintile.
2. Score: `mom_12_1` as in OV-02, computed at `t` for every eligible stock (rows `t-252` to `t-21`), rank cross-sectionally at `t`.
3. Hold the top decile, equal weight, each name capped at 1% of sleeve NAV, 5% of 21d ADV, for 21 sessions (pseudo-events; REF-synthetic-events). No shorts.
4. Earnings-window avoidance: exclude a name if its PREDICTED next announcement falls in `[t+1, t+21+2]` sessions. Predicted date (strictly PIT): `pred = day0_date of the same-fiscal-quarter event one year earlier + 364 days` (event table, using only events with `day0_date <= t`); fallback `last_day0 + 91 days`. Prediction quality diagnostic (before any backtest): share of actual announcements within +-3 sessions of `pred`, reported by year. Never use the ACTUAL next announcement date (that is a look-ahead; announcement dates are only partly known in advance and are not in the data as-of).
5. Four cells (library design): C1 sleeve with no avoidance; C2 sleeve with avoidance; C3 only names with predicted announcement in the window (the avoided set); C4 reference: Steady Drift's own SUE rule. Metric: net hold return, paired by month, block bootstrap.
6. Combination with Steady Drift: single shared ledger, sleeve gets the cash left after the PEAD positions at each rebalance (never above `1 - gross_pead/NAV`), risk throttle applies to the combined book.
Diagnostic V0 (cheap, ex post, no trading claim): in the events table, for momentum-ranked announcers, compare mean `ret_fwd_20d_mktadj`/`ret_fwd_40d_mktadj` whose window contains the own announcement (all events have it by construction) vs the stock's returns over non-announcement windows from the panel: this quantifies jump risk, not alpha.

## 4. Data requirements & availability in THIS repo

Available: full-panel `ret_raw`, `prc`, `vol` (parquet, `data/normalized_equity/equity_YYYY.parquet`; loader currently reads `permno, date, ret` only), events with `day0_date, fpedats`. Missing/blocked: (a) a per-stock-day eligibility table including NON-announcing stocks (ADV floor, size): must be built from `prc, vol, shrout` (note CRSP NASDAQ volume double counting before 2004: a known data issue to handle by halving or by using only NYSE/AMEX rule; unverified here, flag); (b) announcement calendar ahead of time: not available, prediction only; (c) delisting returns for non-event stocks (check `ret` includes them). ETFs/other markets irrelevant. The universe is CRSP stocks 1995-2014, so everything is available at the stock level.

## 5. Framework mapping

- Files: `pead/strategies/portfolio/` is NAV-level only (`PortfolioStrategy(name, {sleeve: weight})`, `pead/strategies/portfolio/sleeves.py`) and cannot share cash. So: `pead/engines/sleeves.py` (new shared-ledger combiner) or build pseudo-events and run through `run_equity_backtest` in one call (events of both sleeves concatenated, one `Sizing`), then apply the same `apply_cash_and_risk_target`. Recommended: pseudo-events route (no new engine).
- New: `pead/core/synthetic_events.py::monthly_topk_events(uni, score, k, hold)` returning an events frame with `permno, day0_date, entry_row_idx, exit_row_idx, exit_date, avg_dollar_volume_21d, liquidity_quintile, ff12_sector, size_quintile, sue_rank_pct (NaN), sleeve`; `pead/strategies/equity/steady_drift_mom_sleeve.py`.
- Registration: `steady_drift_plus_momsleeve` (C2 in the combined ledger), `momentum_sleeve_only` (research, status `reference`, for the paired cells).
- Params: `sleeve_cap=0.30` of NAV (fixed, not tuned), `top_pct=0.10`, `hold=21`, `avoid=True`, `pred_window=(1,23)` sessions.
- Reuse: `run_equity_backtest`, `Sizing(cap_mode="chronological", name_cap_frac)`, `apply_cash_and_risk_target`, `W.causal_inverse_variance` (optional sizing), OV-02 `mom_12_1`.
- Engine changes: none if pseudo-events are used; confirm `run_equity_backtest` tolerates NaN `sue_rank_pct` (it never reads it).

## 5b. Build vs. change vs. restructure

(1) NEW
- `pead/core/synthetic_events.py`; `pead/core/event_calendar.py` (`predict_next_announcement(events, as_of)`; diagnostic on prediction quality); the sleeve strategy; `tests/test_synthetic_events.py`; `tests/test_event_calendar.py`.
- Full-panel eligibility cache `data/cache/panel_eligibility.npz` (ADV21, price, size rank per row).
(2) CHANGE
- `pead/core/data.py::_load_panel`: currently reads only `permno, date, ret`. Add optional columns `prc, vol, shrout` into a SEPARATE cache (do not change the existing `equity_panel.npz` key, so existing runs are unaffected). Regression risk: any change to `_load_panel` return tuple would break `load_equity_universe`; add a new function `load_panel_extras()` instead.
- `EquityUniverse`: add optional `panel_prc, panel_vol, panel_shrout` fields with defaults None (dataclass order: put after `panel_permno`).
(3) RESTRUCTURE
- REF-synthetic-events and REF-shared-cash-ledger (the audit of 2026-10-26 forbids summing standalone NAVs). REF-panel-hygiene (zero-filled returns, NASDAQ volume convention). REF-event-features. REF-validation-suite.
(4) IMPROVE
- Event dates: `fpedats` plus `day0_date` allow the seasonal predictor; check the `day0_status` and `anntims_unknown` flags in `data/events/equity_events_1996_2013.parquet` and exclude uncertain dates from the predictor.
- Capacity/impact: flat bps cost by liquidity quintile (`COST_BPS`) understates turnover costs of a monthly sleeve (about 49% monthly turnover in the source); add a turnover-based cost sensitivity.

## 6. Acceptance tests

- Prediction is causal: deleting all events after `t` leaves `pred` unchanged for formation date `t`.
- Pseudo-events never reference rows beyond `t`; holds cut at stock last row (reuse `with_fixed_hold` behaviour).
- Avoidance removes exactly the names whose predicted window overlaps, on a synthetic calendar with known dates.
- Shared-ledger invariant: PEAD gross + sleeve gross <= `max_gross_leverage` x NAV on every day (`max_gross_over_nav`).
- Determinism; sleeve with `sleeve_cap=0` reproduces `steady_drift_plus` bit-for-bit.

## 7. Experiment plan

Baselines: `steady_drift_plus` (12.07% / 1.19 / -22.07%), Drift Core index fill (13.1% / 0.71 / -53.6% in STEADY_DRIFT table, 1997-2013 window; different harness) and `steady_drift_core` (11.99% / 0.93 / -40.47%) as the "more equity risk" references. Order: (1) prediction-quality diagnostic; (2) V0 jump-risk diagnostic; (3) paired cells C1-C4 on the standalone sleeve; (4) combined ledger only if C2 > C1 on a paired bootstrap AND the sleeve has positive alpha after beta to the market (use `alpha_stats`) AND is not just the small-cap premium (compare with a size-matched random-top-decile control). Windows: 1997-2007 development, 2008-13 reported (not a clean holdout). OV-01 stress mandatory; momentum's 2009 crash is the specific year to leave out.

## 8. Expected outcome and honest risks

Low expected benefit and high effort: the sleeve adds equity beta in exactly the regimes where the book already suffers (2000-02, 2008), and the library offers no admissible evidence for long-only momentum on a point-in-time universe. The avoidance rule mostly trades away breadth (for quarterly reporters a 23-session window out of roughly 63 sessions per quarter, so roughly a third of names are excluded at any formation date). The announcement predictor may be inaccurate for irregular reporters. Only worth building after OV-01..OV-03 and only if the user wants a diversifier for idle cash; otherwise the earnings-window rule has NO consumer in the current repo (Steady Drift is event-driven and intentionally trades the earnings window). Close if V0/C1 show no alpha beyond the size-matched control.

## 9. Dependencies and implementation checklist

Depends on: OV-01, OV-02 (`mom_12_1`), REF-synthetic-events, REF-shared-cash-ledger (or pseudo-events route), REF-panel-hygiene, REF-event-features, REF-validation-suite.
- [ ] `load_panel_extras()` + eligibility cache; check NASDAQ volume convention
- [ ] Announcement predictor + prediction-quality diagnostic
- [ ] `monthly_topk_events` + tests
- [ ] Standalone sleeve cells C1-C4 with paired bootstrap
- [ ] Combined ledger run; stress; decision note
