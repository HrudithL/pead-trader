# EQ-03: Reaction decomposition (gap / intraday / drift), open-vs-close entry timing, and a price-reaction confirmation gate
- Status: planned | Priority: P1 | Effort: L | Source(s): library `strategies/05_LEEYAWNNN_EVENT_STUDY.md`, `strategies/06_PURUJIT_AFTER_HOURS.md`, `audits/02_PURUJIT_SOURCE_AUDIT.md`, `foundations/02_EVENT_TIME_PIT.md`; https://github.com/leeyawnnn/earnings_surprise_analyzer, https://github.com/PurujitAggarwal/PEAD_strategy

> Coordination: reaction columns (gap, intraday, abnormal volume, `react`) are built by EQ-22 (other equity agent) via REF-dsf-reader and stored in the REF-event-feature-store; this spec CONSUMES them and adds only the strategy-side entry-timing mechanics (REF-entry-lag / first-row return override, referred to here as REF-entry-leg), the decomposition study and the confirmation arms.

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Thesis: most of the beat-minus-miss spread is the announcement reaction (overnight gap plus first-session move) that no strategy can buy; only the remainder is "drift". How much of the drift is capturable depends on where in the first session the fill happens. This repo enters at the day0 CLOSE (conservative: forfeits the day0 intraday leg), so the useful question is how much a day0-OPEN fill would add and what reaction-conditioning does.
Take from leeyawnnn: the decomposition protocol (pre-close -> reaction open -> reaction close -> +20 close, reported separately), clustered inference, the open-vs-close execution comparison, and the "break-even cost" idea.
Take from Purujit (idea only): require the market reaction to agree/disagree with the surprise sign as separately-ablated cells (agree / contrarian / all).
Do NOT take: any Purujit performance figure (no pinned CAGR exists), after-hours minute-bar entry (no intraday data), the volume-ratio threshold 0.02 (that is 2% of baseline, meaningless), ex-post vol scaling, the reciprocal short ROI, sequential trade compounding, the SPY 100-day gate as coded (all-risk-off disables the filter; same-day close lookahead). The regime gate is covered in EQ-04's ablation list. Do not use leeyawnnn's S&P-500, 2012-2026 numbers as targets.

## 2. Evidence grade & known defects in the source
leeyawnnn: library grade C (event study E-like, plus a costed portfolio). Reported (unverified, not re-run by the library): 17,724 announcements, 444 S&P names, 2012-2026; beat-minus-miss +2.12 pp = +1.08 overnight + 0.25 intraday + 0.78 day 1-20 (p ~ 0.0019); tradable next-open 4.1% Sharpe 0.28, next-close 1.6% Sharpe ~0.05, SPY 14.8% / 0.82; 6,339 trades; 5 bp assumed round trip, breakevens ~20 bp (open) and ~11 bp (close). Weaknesses: modern large caps only; 8-K acceptance time can lag the press release; components need not sum under other data; clustering assumptions unverified. Purujit: grade B/C; defects P1-P10 in the audit (full-history median volume leak P1; entry bar also used for the signal P2; same-day SPY close P3; `.any()` disables filter P4; trailing stop not latched P6; future-window vol scalar P7, scaled Sharpe vs raw curve P8; reciprocal short ROI P9). Everything is an anti-pattern list for us.

## 3. Exact rules
Part A - event study (no trading), `pead/analysis/reaction_decomposition.py` (NEW):
- Cohorts: `top` = `sue_rank_pct >= 0.9`, `bottom` = `sue_rank_pct <= 0.1`, plus sign cohorts `beat` (`sue_analyst_raw > 0`) vs `miss` (`< 0`).
- Per event (EQ-02 columns): `gap = open_d0/close_pre - 1`, `intraday = close_d0/open_d0 - 1`, `drift_h = close_{d0+h}/close_d0 - 1` for h in {5,10,20,40,60}; market-adjusted (subtract the compounded CRSP `vwretd`) ONLY for the close-to-close legs, i.e. reaction (close_pre -> close_d0) and drift. There is no index open price, so the gap and intraday legs are reported raw and flagged as such (their market-adjusted sum equals the reaction leg only approximately).
- Statistics: mean spread top-minus-bottom per leg; SE clustered by `ann_quarter` (and a second pass clustered by `permno`); era splits 1996-2001, 2002-07, 2008-13; by `timing_class` (BMO / AMC / UNKNOWN) and by `size_quintile`. Output `results/event_study/reaction_decomposition.csv` (+ md).
Part B - entry timing in a tradable book (same signal, universe and sizing as `steady_drift_plus`, so only the fill changes):
- `entry_mode="day0_close"` (current framework behaviour; baseline).
- `entry_mode="day0_open"`: buy at `openprc` of day0 (for BMO/INTRADAY the open already includes the gap; for AMC day0 is the next session, so its open includes the gap too). First P&L leg = `close_d0/open_d0 - 1` (REF-entry-leg), then close-to-close as usual to the same exit (day0 + 60 sessions). Extra slippage `open_slip_bps` (default 10 on liquidity quintiles 1-2, 5 otherwise; fixed beforehand, ablate 0/10/25) charged at entry.
- `entry_mode="day1_open"`: decision at day0 close, fill at day1 open: first leg = `close_d1/open_d1 - 1`; shows the cost of a one-session execution delay.
- Return basis must be `raw` for open entries (no index open price exists); the cash-fill and risk throttle wrappers in `base.py` are unchanged.
Part C - reaction gate (known at the day0 close, so entry at the close stays legal): `react = ret_reaction` (close_pre -> close_d0). Arms: `all`; `agree` (`react >= +0.5%`, the 0.5% from Purujit's after-hours threshold, fixed not tuned); `contrarian` (`react <= -0.5%` despite a top-decile beat); `flat` (|react| < 0.5%). The existing STEADY_DRIFT note says day-0 confirmation was one of 35 variants with no gain; this part tests it more precisely and, importantly, tests the CONTRARIAN cell that FinLab-type evidence (library `foundations/01_SIGNAL_DEFINITIONS.md` section 7: beat with a down reaction later drifted more) suggests.

## 4. Data requirements & availability in THIS repo
Have: `anntims`, `day0_date`, `day0_lag_days` (events), close-to-close `ret`, `prc`, `vol` (normalized panel). Have in raw only: `openprc`, `bid`, `ask`, `bidlo`, `askhi`, `numtrd` in `data/raw_wrds/crsp/dsf_YYYY.parquet` (verified columns). Missing: opens in the framework panel (REF-dsf-reader), intraday bars, after-hours prices, press-release timestamps (IBES `anntims` proxy), an index open price. CRSP `openprc` is sparse for early years and NASDAQ; step 0 of the build is a coverage table by year and exchange. If coverage in the top-decile events is below ~90%, restrict to covered events and say so (selection bias check: compare covered vs uncovered on size and SUE).

## 5. Framework mapping
- `pead/analysis/reaction_decomposition.py` (NEW package `pead/analysis/`, REF-analysis-pkg), CLI entry `python -m pead study reaction` (small addition to `pead/cli.py`) or a standalone `python -m pead.analysis.reaction_decomposition`.
- `pead/strategies/equity/steady_drift_timing.py`: classes `SteadyDriftOpen(SteadyDriftPlus)` name `steady_drift_plus_open0`, `SteadyDriftNextOpen` name `steady_drift_plus_open1`, and gate variants `steady_drift_plus_agree`, `_contrarian` (parent `steady_drift_plus`, `family="equity-longonly"`, `status="research"`, `legacy_twin=False`). Reuse `SteadyDriftPlus.sizing`, `.weights` from `pead/strategies/equity/steady_drift.py`; override `weights()` to multiply by the gate mask and override `backtest()` to call `with_entry_timing(uni, mode, slip_bps)` (NEW in `pead/engines/equity.py`, next to `with_fixed_hold`) after `with_fixed_hold`.
- Params: `entry_mode, open_slip_bps=10, react_threshold=0.005, reaction_gate in {"all","agree","contrarian","flat"}`.
- `required_columns` adds `react`, `timing_class`, `open_available` (EQ-02).

## 5b. Build vs. change vs. restructure
(1) NEW: `pead/analysis/reaction_decomposition.py`; `pead/engines/equity.py::with_entry_timing`; `pead/strategies/equity/steady_drift_timing.py`; `tests/test_entry_timing.py`; QA table `results/event_study/openprc_coverage.csv`.
(2) CHANGE: `pead/engines/equity.py::run_equity_backtest` - after `flat_ret = eq_ret[flat_row]` apply the first-row override: `flat_ret = np.where((offsets == 0) & np.isfinite(ov[rep]), ov[rep], flat_ret)` where `ov = pos["first_ret_override"]` if the column exists (absent column = unchanged; regression risk nil, pin with the existing NAV tests in `tests/test_framework.py::test_default_vintage_pins_corrected_final_nav`); extra slippage via `pos["entry_extra_bps"]` added to `cost_bps` (same optionality). `pead/strategies/base.py::EquityStrategy.backtest` for the `entry_mode` hook (see EQ-01 REF-exit-rule-hook). `pead/core/data.py::_load_panel` for open arrays (REF-dsf-reader).
(3) RESTRUCTURE: REF-entry-leg (per-position first-row return override and entry extra cost as optional event columns); REF-dsf-reader; REF-analysis-pkg (studies that are not backtests, so `pead/research/` stays an archive); REF-event-feature-store.
(4) IMPROVE: the engine charges entry cost on `day0_date`; make the cost date follow the actual fill date for `day1_open`; add `openprc` coverage reporting to `pead/prep/verify_raw_data.py`; the report should show the intraday leg separately from drift (extend `pead/reporting/attribution.py`).

## 6. Acceptance tests
- Synthetic: panel with open=100, close day0=102, then flat; `day0_open` position earns exactly +2% on day0 and 0 after, `day0_close` earns 0; `day1_open` earns the day1 open->close only; costs: extra slippage charged once at entry and exit consistent with `COST_BPS`.
- Assertion: `entry_mode != "day0_close"` with `return_basis="mktadj"` raises ValueError.
- No-look-ahead: the open fill uses only the open price of the fill session; rewrite every close after day0 and assert the `day0_open` first-row override is unchanged; for `day0_close`, set day0 return to +50% and assert P&L unchanged (the P&L must start at day0+1).
- Gate no-look-ahead: `react` for an AMC event uses the day0 (next session) close, available before the day0 close fill; truncation invariance of `react` (REF-pit-test-kit).
- Library fixtures: BMO 07:30 gap not earned by a day0-open entry; AMC 16:10 -> fills no earlier than day0 open; Friday AMC before Monday holiday -> Tuesday.
- Decomposition identity on synthetic data: `(1+gap)(1+intraday) = 1+reaction` to 1e-12.
- Determinism and event-count conservation: counts per mode differ only by the documented dropped events (missing open).

## 7. Experiment plan
Step 0: openprc coverage by year / exchange / decile. Step 1 (Part A, descriptive): the decomposition table by era and size; compare the SHARE of the spread that is drift against the library's 0.78/2.12 = 37% (their S&P 2012-26; ours is CRSP 1996-2013; no expected match) - differences are the finding, not a failure. Step 2 (Part B): `steady_drift_plus` vs `_open0` vs `_open1` with slippage 0/10/25 bp; report the intraday leg's contribution, alpha t, Sharpe; break-even slippage. Step 3 (Part C): four gate arms on `steady_drift_plus`, compared with placebo-shuffled SUE and with a random 50% subsample of events (to separate "fewer names" from "better names"). Splits: SEARCH day0_date <= 2005-12-31, HOLDOUT 2006-2013 (REF-period-split); the 0.5% threshold and slippage constants are fixed above, not selected. Comparators: `steady_drift_plus` 12.07% / Sharpe 1.19 / DD -22.07%; judge on skill-adjusted alpha and 2008-13 Sharpe; any gain must exceed the cost of slippage at 25 bp.

## 8. Expected outcome / hypotheses and risks
H1: the day0 intraday leg is positive but small (library: +0.25 pp of 2.12 in modern S&P names); the realised gain after open-auction slippage is probably near zero in small caps and may be negative. H2: the gate arms will not beat the ungated top decile (consistent with the 35-variant note) except possibly the contrarian cell, which is exploratory and must not be adopted on a single in-sample win. Risks: sparse CRSP `openprc`; opens in illiquid names are noisy and an open fill at 5% of ADV is optimistic; the 5% ADV cap is daily so the auction share is unbounded; BMO reports in early years may be mis-timed by `anntims`; the S&P/modern evidence says the gap dominates, so the correct conclusion may be "our close-entry baseline is already near the achievable"; data ends 2013.

## 9. Dependencies and implementation checklist
Depends on EQ-02 (timing class, open, react), EQ-08 (invariance kit), REF-dsf-reader, REF-entry-leg.
- [ ] REF-dsf-reader and coverage table
- [ ] `first_ret_override` / `entry_extra_bps` engine hooks (optional columns) + regression pin of existing NAVs
- [ ] `with_entry_timing`
- [ ] Part A study + cluster inference
- [ ] strategy variants registered
- [ ] tests incl. decomposition identity
- [ ] run Parts B and C with SEARCH/HOLDOUT; placebo; write verdict
