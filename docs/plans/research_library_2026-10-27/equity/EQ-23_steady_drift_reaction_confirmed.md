# EQ-23: Steady Drift with reaction confirmation (day-0 excess return filter / tilt; beat-with-weak-reaction contrarian cell) and an entry-lag robustness harness
- Status: planned | Priority: P1 | Effort: M | Source(s): library `strategies/08_AARSH_DAYAL.md` (https://github.com/aarshdayal/EARNINGS-SURPRISE-ANALYSIS-PEAD-STRATEGY), `foundations/01_SIGNAL_DEFINITIONS.md` section 7 (FinLab beat/down-reaction cell, https://finlab.finance/en/blog/us-earnings-surprise-strategy), `strategies/06_PURUJIT_AFTER_HOURS.md` (price confirmation idea, other agent), `strategies/17_ALBERT_PEREZ_POST_EARNINGS_INTRADAY.md`, `audits/06_ADJACENT_CODE_AND_REPORT_DISCREPANCIES.md` section E item 1 (lag every signal one bar)

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Aarsh proposes response-conditioned filtering ("strong beats + negative price reaction vs strong beats + strong initial gap"); the library's FinLab number says beat/down-reaction stocks later drifted more (+0.84% over 60 sessions) than beat/up (-0.04%), which argues AGAINST buy-the-pop. Both are author-reported descriptive results. The repo already tested the "buy-the-pop" side with `d0pos` etc. (below), so this spec (a) pre-registers the confirmation filters with the repo's causal machinery, (b) adds the contrarian cell as a falsification, (c) adds an entry-lag test (`with_entry_lag`) answering the audit's "lag every signal by one bar". We do NOT take: Aarsh's 25 hand-picked tickers, 4x-quarterly annualisation, sign-flipped shorts (long-only repo rule), event-level means as strategy returns.

## 2. Evidence grade & known defects in the source
Aarsh grade C (521 events, 21 quarters, t=1.40, p=0.178; extreme-positive vs extreme-negative CAR p=0.5086; the event-level surprise slope p=0.0219 is not a tradable-portfolio result). FinLab cell stats: author-reported, 19,083 announcements, rank IC 0.012 (library). Prior in-repo evidence (meta/debate2/D_results_A.csv, prototype engine, 1997-2013): base 10.8% / Sharpe 0.599 / alpha 6.2% (t 6.08) / 2008-13 alpha 3.4%; `d0pos` (d0 excess > 0) 11.0% / 0.609 / 6.4% (t 6.48) / 3.6%; `d0notneg` (> -2%) 11.1% / 0.616 / 6.5% (t 6.53) / 3.6%; `d0strong` (> +3%) 10.2% / 0.555 / 5.6%; `d0neg` (< 0) 6.5% / 0.398 / 2.1% (t 2.08). Reading: negative-reaction beats are WORSE here, the opposite of the FinLab cell, and positive confirmation is a within-noise gain. Day-0 return here includes the overnight gap and is known at the close entry. Defect to respect: day0 mis-dating in 1996-98 (EQ-22 section 2).

## 3. Exact rules
Universe, SUE rank, sizing, hold: identical to `steady_drift_plus` (top decile `sue_rank_pct >= 0.9`, `avg_dollar_volume_21d >= 2e6`, 60 sessions, causal inverse variance, 80% cap, 1% name cap, 12% risk target). Add reaction variable `d0_excess` (EQ-22), measured on the reaction session, decision at the day0 close, fill at the day0 close (existing repo convention).
Variants (class attribute `mode`):
- `confirm_pos`: also require d0_excess > 0.
- `confirm_notneg`: require d0_excess > -0.02.
- `confirm_z`: require d0_excess / max(tvol_60d, 0.005) > 0 and apply tilt w *= 1 + lam*clip(z/3, -1, 1), lam = 0.5.
- `contrarian`: require d0_excess < 0 (falsification; expected to fail per D_results_A `d0neg`).
- `lag1`, `lag2`: no filter; enter k sessions after day0 close (entry_row_idx += k, exit unchanged at entry+60, k in {1,2}); the d0..d0+k excess return is known but not earned. Implemented by `with_entry_lag(uni, k)` (REF-entry-lag).
Missing d0_excess -> event excluded for filtered modes (counted). No shorts, stops, or leverage; costs per engine.

## 4. Data requirements & availability in THIS repo
`d0_excess` from EQ-22 (built from `EquityUniverse.ret_raw` and `vwretd`; no new raw data). Everything else is in `equity_vol_panel.parquet`. Not available: pre-announcement drift/expectation (e.g. guidance), intraday timestamp of the release. Proxy for "beat but weak reaction = market expected more": `d0_excess` itself; alternative requires whisper/revision data (see revision work by the other equity agent).

## 5. Framework mapping
- New `pead/strategies/equity/steady_drift_reaction.py`: `SteadyDriftReaction(SteadyDriftPlus)` with `mode`, registered subclasses `steady_drift_plus_confirm_pos`, `_confirm_notneg`, `_confirm_z`, `_contrarian`, `_lag1`, `_lag2` (family `equity-longonly`, parent `steady_drift_plus`, status `research`, `legacy_twin=False`). `required_columns += ("d0_excess","tvol_60d")`. Mask composition mirrors `SteadyDrift.weights` then `W.causal_inverse_variance(events, mask)`.
- `lag` modes override `EquityStrategy.backtest` (verified in `pead/strategies/base.py`): `uni = with_entry_lag(uni, k)` before `with_fixed_hold`. The hold is then entry+60, so total span 60+k sessions from day0.
- New function in `pead/engines/equity.py`: `with_entry_lag(uni, k)` mirroring `with_fixed_hold` (shift `entry_row_idx` by k within the permno run using the same `last`-row logic; drop events whose shifted entry passes the last row). IMPORTANT, verified in `run_equity_backtest`: the engine takes the entry calendar day, entry quarter and cost timing from `day0_date` (`entry_cal = pos["day0_date"].map(date_to_idx)`), so `with_entry_lag` must overwrite `day0_date` with the date of the lagged entry row (`panel_date[entry_row_idx+k]`) and keep the original in `day0_date_orig`; otherwise P&L would start on the right row but costs and quarterly sizing would be booked k sessions early (a silent timing bug). `EquityStrategy` gets `entry_lag_sessions: int = 0` and applies it in `backtest()` before the fixed hold.
- Params: `d0_min=0.0` / `-0.02`, `lam=0.5`, `z_clip=3`, `entry_lag_sessions=0`.

## 5b. Build vs. change vs. restructure
1. NEW: `steady_drift_reaction.py`; `with_entry_lag` (REF-entry-lag); `tests/test_reaction_confirmation.py`; side table from EQ-22.
2. CHANGE: `pead/engines/equity.py` add `with_entry_lag` (pure addition; regression risk none if default k=0 short-circuits); `pead/strategies/base.py::EquityStrategy.backtest` and `.params()` (adds `entry_lag_sessions`; `params()` keys feed the report meta, so snapshot tests that compare `params` dicts, if any, need updating).
3. RESTRUCTURE: share the top-decile mask logic between `steady_drift.py` and the new file (extract `SteadyDrift._base_mask(events)`); REF-event-feature-store prerequisite.
4. IMPROVE: `steady_drift` reports compute alpha vs CRSP VW only (ROADMAP #1: no small-cap benchmark); a filter that reduces small-cap exposure will look different for reasons unrelated to surprise: report size-quintile mix before/after each filter and the placebo skill.

## 6. Acceptance tests
- Synthetic: 10 events with scripted d0_excess; each mode selects exactly the expected set; `lag1` entry row = day0 row + 1 and never crosses into the next permno (reuse the `_panel([100,30,1])` fixture of `tests/test_steady_drift.py`).
- No look-ahead: rewriting `ret_raw` at rows > entry_row_idx leaves the mask and weights bit-identical; with `lag1`, mask uses only rows <= day0 (assert).
- Determinism; `mode=none` equals `steady_drift_plus` NAV exactly.
- No-borrow check: marked holdings <= 95% NAV (`summary["max_book_gross_over_nav"]` / positions) for each variant.

## 7. Experiment plan
Baselines from `results/leaderboard.md`: `steady_drift_plus` 12.07% / 1.19 / -22.07%; `steady_drift` 10.59% / 1.16 / -20.00%. Run six variants plus controls on search window (day0 <= 2005-12-31), pick one filter threshold by search-window Sharpe, report holdout 2006-2013 once. Gate: Sharpe >= baseline, alpha t >= 5.5, 2008-13 Sharpe >= 0.75, holdings < 95%. Ablations: `timing_known` events only; exclude 1996-98; size-quintile-neutral comparison; placebo (shuffle `sue_rank_pct`, keep d0_excess real) to see whether the filter alone creates the return. `lag1/lag2` read against the main run to quantify how much of the edge needs a same-close fill (a large drop would flag the MOC/decision-at-close assumption as the real risk).

## 8. Expected outcome / hypotheses and risks
H1 (from D_results_A): `confirm_pos/notneg` change Sharpe by < +0.03 and alpha t by < +0.5: noise. H2: `contrarian` underperforms base by 3-4 pts return. H3: lag1 costs 0.5-1.5 pts a year (the day-0 to day-1 continuation is part of the drift); unknown, measured not assumed. Risks: filters cut the event count 30-50% (n 15,915 -> 11,348 in D_results_A), raising the 5%-of-volume cap bite less but concentration more; every filter is selected on the same 17 years; the library's contrary FinLab result is for a different universe and construction (ETF-gated), so no weight on it.

## 9. Dependencies and implementation checklist
- [ ] EQ-22 `d0_excess` column and loader (REF-event-feature-store)
- [ ] REF-entry-lag in engine + test
- [ ] Strategy file + registry
- [ ] Parity test with `steady_drift_plus`
- [ ] EQ-30 harness (placebo, split, FDR log)
- [ ] Search-window selection note, one holdout read, leaderboard row only if status research
