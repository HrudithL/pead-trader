# Library candidates EQ-01, 03, 04, 05, 06 (2026-10-28)

Code: `pead/strategies/equity/library_candidates.py` (15 variants of `steady_drift_plus`, all long-only, same sizing and 12% risk throttle), `pead/engines/exits.py` (`with_path_exits`, `with_calendar_hold`), `pead/core/event_signals.py`, `scripts/reaction_decomposition.py`. Search 1996Q2..2008Q2 / holdout 2008Q3..2013Q4 (burned). All 15 trials are in the registry.

Baseline `steady_drift_plus`: 12.07% / Sharpe 1.19 / MDD -22.1%; search Sharpe 1.38; holdout Sharpe 0.87, CAGR 9.7%, MDD -18.7%.

| variant | spec | full CAGR / Sharpe / MDD | search Sh | holdout Sh / CAGR |
|---|---|---|---|---|
| seas_s (seasonal top decile) | EQ-06 | 8.47% / 0.92 / -22.2% | 0.94 | 0.87 / 8.1% |
| seas_s5 (top 5%) | EQ-06 | 5.49% / 1.04 / -16.2% | 1.25 | 0.55 / 2.7% |
| seas_and (analyst >= .9 and seasonal >= .667) | EQ-06 | 9.56% / 1.17 / -21.6% | 1.45 | 0.72 / 6.7% |
| seas_or | EQ-06 | 10.67% / 1.01 / -22.3% | 1.08 | 0.88 / 9.9% |
| seas_avg (mean rank >= .9) | EQ-06 | 6.86% / 1.19 / -17.7% | 1.59 | 0.52 / 3.4% |
| stop5 (5% close stop) | EQ-04 | 9.81% / 1.30 / -19.2% | 1.73 | 0.61 / 5.1% |
| stop10 | EQ-04 | 10.67% / 1.21 / -22.5% | 1.52 | 0.71 / 7.1% |
| trail (3% activation, 2% latched trail) | EQ-04 | 8.18% / 1.17 / -16.7% | 1.70 | 0.55 / 4.8% |
| ea1904 (seasonal SUE >= 1.5, rvol >= 1.5, 20 sessions, 5% stop) | EQ-01 | 4.96% / 0.92 / -17.6% | 1.46 | 0.04 / 0.1% |
| ea1904_nostop | EQ-01 | 5.76% / 0.83 / -18.0% | 1.32 | 0.08 / 0.3% |
| bouddat (analyst SUE >= 0.635, day0 + 85 calendar days) | EQ-05 | 8.27% / 0.79 / -22.3% | 0.84 | 0.69 / 7.6% |
| bouddat_60s (control) | EQ-05 | 8.39% / 0.80 / -21.5% | 0.84 | 0.71 / 7.9% |
| react_agree (top decile, day-0 reaction >= +0.5%) | EQ-03C | 10.85% / 1.22 / -22.1% | 1.49 | 0.83 / 8.8% |
| react_contra (reaction <= -0.5%) | EQ-03C | 5.32% / 1.04 / -14.1% | 1.49 | 0.56 / 3.7% |
| react_flat (|reaction| < 0.5%) | EQ-03C | 4.01% / 2.27 / -2.8% | 3.23 | 0.86 / 1.9% |

(`react_flat` holds only 1,286 positions, so the book is mostly T-bills; its Sharpe is an artefact, not a finding.)

## Readings
- **EQ-06 seasonal SUE:** weaker than analyst SUE everywhere; no combination beats the analyst-only book in the holdout (best, `seas_or`, 0.88 vs 0.87, with lower full-period Sharpe 1.01). The seasonal rank correlates only 0.24 with the analyst rank. Null.
- **EQ-04 stops:** a 5% close stop lifts full-period Sharpe (1.30) and search Sharpe (1.73) but is clearly worse in the holdout (0.61 vs 0.87): the stop sells names that later recovered in 2008-09. Trailing and 10% stops show the same pattern. A classic in-sample improvement that does not hold; not promoted. (Close-based stops give no gap-through slippage, so the real result would be worse.)
- **EQ-01 EA1904-style:** works in the search window (Sharpe 1.3-1.5) and has no edge in the holdout (Sharpe 0.04-0.08); the library's own headline numbers rest on look-ahead-prone accounting. Not promoted. Deviation: the spec's `sue_hist` is approximated by this repo's `sue_seasonal`; `rvol_d` = day-0 volume over the prior 20-session mean (CRSP `vol`).
- **EQ-05 Bouddat long leg:** the 0.635 threshold selects about half of all events (a broad screen), diluting the top-decile signal: Sharpe 0.79 vs 1.19. The 85-calendar-day hold makes no difference vs 60 sessions. Short leg (P2) not built. Null.
- **EQ-03 part C:** requiring a positive day-0 reaction (+0.5%) keeps Sharpe 1.22 / 10.85% (holdout 0.83), still below the base on return and not better in the holdout; the contrarian cell (beat with a falling price) is worse (5.3%, holdout 0.56), contradicting the library's FinLab-type hint. Null, consistent with EQ-23.
- **EQ-03 part A (event study, `results/event_study/reaction_decomposition.csv`):** the top-minus-bottom SUE spread is 6.8 points on day 0 (5.3 of it overnight gap, 1.5 intraday; t about 25), which the close-entry book cannot capture, then 2.8 points of 60-session drift (t 5.0). The drift spread is 3.6 points in the search window and 1.0 in the holdout (t 1.1), where the bottom decile's later drift turns positive (+2.2% at 60 sessions): the short side reverses after 2008, the long side keeps earning. Day-0 reaction (gap + intraday) does not shrink in the holdout (8.6 points).
- **Not done:** EQ-03 part B (open and day-1-open entry modes; needs a first-leg entry in the engine), EQ-04 ATR stop and trend gate (OV-03 covers the gate), EQ-05 short leg, EQ-06 recomputed `sue_seas_8` audit.

Overall: none of the 15 improves on `steady_drift_plus` out of sample; the stop variants are the clearest example of search-window improvement that fails in the holdout.
