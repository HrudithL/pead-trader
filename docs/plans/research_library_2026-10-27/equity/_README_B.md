# Equity spec sheets, batch B (EQ-21 .. EQ-30)

Assignment: library strategies 07 (mcravi8), 08 (Aarsh Dayal), 11 (KPH3802), 12 (Dylan Jeon), 13 (Ishaan Singh), 17 (Albert Perez), 23 (Lan et al.), 24 (UK order flow). Documentation only; no code under `pead/` or `tests/` was touched. Enabling work shared across specs is consolidated in `_ENABLING_CHANGES_B.md`. All baselines are from `results/leaderboard.md`: `steady_drift_plus` 12.07% / Sharpe 1.19 / MaxDD -22.07%; `steady_drift` 10.59% / 1.16 / -20.00%; `steady_drift_calm` 9.55% / 1.21 / -15.90%; `steady_drift_core` 11.99% / 0.93 / -40.47%. Library performance figures are author-reported and unverified.

## Specs (priority order)
| ID | File | Priority | Effort | What |
|---|---|---|---|---|
| EQ-30 | EQ-30_signal_evaluation_harness.md | P0 | M | IC by quarter, season-clustered inference, tradable-vs-total CAR, cost ladder, placebo, FDR log, `python -m pead signal` |
| EQ-22 | EQ-22_reaction_feature_build.md | P0 | M | Gap / intraday / day-0 excess / 2-day / abnormal-volume features + event feature store loader |
| EQ-21 | EQ-21_sue_variants_signal.md | P0 | M | Floored-dispersion, price-scaled, percent-of-consensus, seasonal, composite SUE rank columns + strategy variants |
| EQ-23 | EQ-23_steady_drift_reaction_confirmed.md | P1 | M | Day-0 reaction confirmation / contrarian falsification, `with_entry_lag` robustness |
| EQ-28 | EQ-28_decay_aware_exposure_era_interaction.md | P1 | M | Era-interaction test on tradable CAR; strictly-past decay-aware exposure scale (never above 1) |
| EQ-26 | EQ-26_spread_bounce_flow_proxy.md | P1 | M | Quoted-spread cost model, entry bounce/ask slippage, daily order-pressure proxy |
| EQ-24 | EQ-24_orj_gap_surprise_signal.md | P1 | M | Price-based overnight-gap surprise (ORJ proxy) and SUE+gap composite |
| EQ-25 | EQ-25_attention_proxies_interaction.md | P2 | M | Abnormal volume, coverage, size interactions (two-sided test of Lan vs US neglect story) |
| EQ-29 | EQ-29_walkforward_response_curve.md | P2 | M | Walk-forward bucket/isotonic conviction multiplier |
| EQ-27 | EQ-27_post_earnings_next_day_continuation.md | P2 | L | Next-session open-to-close gap continuation (daily-data version); needs a new intraday engine |

## Key facts verified in the repo that shaped the specs
- The repo's primary `sue_analyst` is already the mcravi8 definition, `(actual - mean)/stdev`, with `numest >= 2` and `stdev >= 0.01` (file `data/earnings/ibes_clean_1995_2014.parquet`). Porting mcravi8's SUE is a no-op; only denominator alternatives and composites are new (EQ-21).
- `data/normalized_equity` has no open/bid/ask; raw `data/raw_wrds/crsp/dsf_*.parquet` does (openprc, bid, ask, numtrd, vol): the source of all gap/spread features (REF-dsf-reader).
- `anntims_unknown` is 95% (1996), 85% (1997), 27% (1998), <= 4% afterwards: day0 timing is unreliable before 1999 and every reaction-based test needs a `timing_known` cut (REF-anntims-audit).
- `meta/debate2/D_results_A.csv` already tested day-0 confirmation, size/sector-matched PIT ranks, hold 40/90, thresholds, price/ADV floors: all within noise of the top-decile rule. Day-0 negative-reaction events were clearly worse (alpha 2.1%, t 2.08), contradicting the FinLab "beat/down" cell for this universe.
- statsmodels is not installed in the default environment; harness must be numpy-only.
- The engine takes entry date/quarter/cost timing from `day0_date` (`run_equity_backtest`), so any entry-shift helper must rewrite it.

## Audit 06 cross-check
`audits/06_ADJACENT_CODE_AND_REPORT_DISCREPANCIES.md` covers cross-sectional momentum, ATR momentum, QuantConnect SUE and FinLab; none of its items touch sources 07, 08, 11, 12, 13, 17, 23, 24. Relevant general lessons used: section E item 1 (lag signals one bar: `with_entry_lag` in EQ-23), item 3 (leave-one-out/best-name tests: EQ-27, EQ-30), item 5 (cost units both legs: EQ-26), item 7 (event logs and hashes: EQ-30 `_signal_tests.csv`). The QuantConnect seasonal-SUE note (incomplete controller) is why EQ-21 uses the repo's own `sue_seasonal` rather than the posted controller.

## Ideas considered and skipped (with reason)
| Idea / source | Reason |
|---|---|
| mcravi8 long D10 / short D1 week x market-cap decile book with 20 bp + 50 bp borrow | Already covered and negative: 22 market-neutral books earn 1-3% after borrow and collateral (docs/STEADY_DRIFT.md section 1); short leg nets -4.4 pts; no borrow model exists; author's own result is Sharpe -0.333. Size-matched and sector-matched PIT ranks were run (`sizematch` 10.5% / 0.584, `sectormatch` 11.0% / 0.610 vs base 10.8% / 0.599 in D_results_A): noise. Only its evaluation lessons are kept (EQ-30). |
| mcravi8 30-day hold, weekly cohorts | Hold sweeps in repo: 40 and 90 sessions worse than 60 (8.7% / 7.9%, docs section 9). |
| Aarsh Dayal 25-ticker, 521-event, 60-day long/short study | Tiny hindsight universe, 21 quarters, t=1.40; method (season clustering, 4x quarterly caveat) absorbed in EQ-30; nonlinearity absorbed in EQ-29. |
| KPH3802 FMP/yfinance pipeline, SQLite `pead_trades` store | Data not in repo; calendar-day delays, adjusted-close entry; bucket idea kept as EQ-29, event store is redundant with `results/<name>/positions.parquet`. |
| Ishaan Singh platform (FastAPI, React UI, synthetic provider) | Engineering demo, no alpha, synthetic-data risk; its statistical ideas (market-model CAR, BH-FDR, block bootstrap) are in EQ-30. Market-model CAR on [-250,-30] is covered by the existing `beta_252d`/`ivol_252d` columns; adding a second abnormal-return flavor was judged not worth a spec. |
| Dylan Jeon post-2020 compression test itself | BLOCKED: data end 2013 (needs CRSP/IBES 2014+, ROADMAP #7). Proxy built as EQ-28 (2005 break inside the sample). |
| Perez same-session open-to-close trade with delayed-minute entry tests | BLOCKED: no intraday bars. Only the daily next-session version is specified (EQ-27). |
| Lan et al. exact ORJ, China A-share quarterly portfolio, 6.78% quarterly excess | Formula and code inaccessible; market not comparable; proxy EQ-24. |
| UK OFI with Lee-Ready/TAQ signing, options spread study | BLOCKED: no tick/quote data (needs TAQ or equivalent with release timestamps); daily proxy EQ-26; options belong to the options workstream. |
| Price-based surprise for events without IBES consensus (34.7k `sue_source == none`) | Genuinely new breadth but requires a new event universe and day0 resolution; flagged as follow-up in EQ-24, not specified here. |
| Analyst-level stale-estimate pruning from `detu_epsus.parquet` | Large effort (14.6M rows) with low expected gain given `consensus_staleness_days` median 14; revisions/dispersion-as-information belong to the other equity agent. |
