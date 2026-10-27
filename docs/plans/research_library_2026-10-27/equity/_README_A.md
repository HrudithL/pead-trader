# Equity specs, set A (EA1904, QuantConnect SUE, Bouddat, leeyawnnn, Purujit)

Written 2026-10-27. Documentation only; no code under `pead/` or `tests/` was touched. All specs follow the 9-section template plus `## 5b. Build vs. change vs. restructure`. Shared refactor IDs are consolidated in `_ENABLING_CHANGES_A.md` and use the same names as set B (EQ-21 to EQ-30) where they overlap: REF-event-feature-store, REF-event-key, REF-dsf-reader, REF-period-split, REF-placebo-runner, REF-anntims-audit. Search/holdout split follows set B's REF-period-split (search `day0_date <= 2005-12-31`, holdout 2006-2013); note the holdout overlaps data already used to pick `steady_drift_plus` constants.

| ID | File | Idea | Priority | Effort |
|---|---|---|---|---|
| EQ-08 | EQ-08_lookahead_invariance_test_kit.md | Truncation / future-rewrite / timing-fixture / engine-causality tests derived from the three code audits | P0 | M |
| EQ-02 | EQ-02_event_feature_table.md | PIT feature table: `sue_hist` (EA1904), seasonal flavors, daily RVOL, timing class, trend/ATR columns (reaction columns defer to EQ-22) | P0 | M |
| EQ-04 | EQ-04_path_exits_stops_trailing.md | `with_path_exits` (percent stop, latched trailing stop, session/calendar time exit), causal trend gate, engine cap-causality fix | P1 | M |
| EQ-01 | EQ-01_ea1904_sue_volume_stop.md | EA1904 classical rule, clean-room, long-only, 6 pre-registered variants | P1 | M |
| EQ-05 | EQ-05_bouddat_asymmetric_threshold_hold.md | Absolute SUE thresholds 0.635 / -3.78, 85 vs 9 calendar-day holds | P1 (long) / P2 (short) | M |
| EQ-06 | EQ-06_seasonal_sue_signal_variants.md | Seasonal-SUE (QuantConnect) and analyst/seasonal combinations, event-driven | P1 | M |
| EQ-03 | EQ-03_reaction_decomposition_entry_timing.md | Gap / intraday / drift decomposition, day0-open vs close entry, reaction-confirmation arms | P1 | L |
| EQ-07 | EQ-07_peer_spillover_lite.md | Sector-peer spillover with rewired-peer placebo (EA1904 graph-lite) | P2 | L |

Suggested order: EQ-08 -> EQ-02 -> EQ-04 -> EQ-06 / EQ-05 (long) -> EQ-01 -> EQ-03 -> EQ-05 (short) -> EQ-07. Rationale and enabling work in `_ENABLING_CHANGES_A.md`.

## Ideas skipped (reason)
- EA1904 multiplex GAT / trained attention: needs FactSet Revere supplier-customer edges and historical GICS; the author's own control shows trained = random graphs (Sharpe 2.259 vs 2.249, p=.30). Only a sector-peer proxy is kept (EQ-07).
- EA1904 25% ex-ante vol target and ATR exits as separate strategies: the vol target already exists as `risk_target_vol` / `apply_cash_and_risk_target`; an ATR stop is an option inside EQ-04. The public engine implements neither.
- EA1904 equal 1/n_active weighting and short leg: unbounded concentration (audit E7); shorts unmodelled for borrow. Long/short GAT variants not pursued.
- QuantConnect monthly top-5% rebalance on a 50-name coarse universe: ~2-3 names, no risk management, 63.9% drawdown, PSR 0.495%, "Likely Overfitting" flag; construction not reproducible (`universe.py`/`alpha.py` unseen). Its signal is kept (EQ-06).
- Bouddat fixed-$5,649 sizing, ticker joins, spread formula, `spy.fillna(0)`, future exit-price screening: defects, not ideas (tests in EQ-05 and EQ-08).
- leeyawnnn SEC 8-K acceptance timestamps and the S&P 500 2012-2026 sample: no EDGAR data in the repo and outside our 1996-2013 window; IBES `anntims` is used instead (accuracy check in EQ-02/EQ-08).
- Purujit after-hours minute-bar entry, volume ratio 0.02, ex-post vol scaling, reciprocal short ROI, sequential trade compounding, risk_per_trade/rank_cutoff (unused in the reviewed backtest): no intraday data and the maths is wrong. Only the 0.5% reaction-confirmation idea (EQ-03), the corrected latched trailing stop and the trend gate (EQ-04) survive.
- Source performance numbers (77.73%, 81.88%, 23.576%, 13.619%, 14.02%, 4.1%): unverified claims; none is used as a target anywhere.
