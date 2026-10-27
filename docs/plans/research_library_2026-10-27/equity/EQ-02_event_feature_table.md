# EQ-02: Event feature table (SUE flavors, daily RVOL, reaction decomposition, announcement-timing class)
- Status: planned | Priority: P0 (enabling; EQ-01/03/04/05/06/07 read it) | Effort: M | Source(s): library `foundations/01_SIGNAL_DEFINITIONS.md`, `foundations/02_EVENT_TIME_PIT.md`, `strategies/01_EA1904.md`, `03_QUANTCONNECT_SUE.md`, `05_LEEYAWNNN_EVENT_STUDY.md`; https://github.com/EA1904/pead-sp500-research, https://www.quantconnect.com/forum/discussion/20418/, https://github.com/leeyawnnn/earnings_surprise_analyzer

> Coordination: the other equity agent's EQ-22 (reaction-session feature build) owns the gap/intraday/abnormal-volume columns and the `dup_rank` key, and EQ-21 owns the dispersion-floored/price-scaled SUE variants; this spec therefore only builds what the assigned sources add: `sue_hist`, `sue_seas_8`/`_dm`/`_rank`, `timing_class`, `rvol_d`, `trend_ok`, `atr_proxy`. Where items 5 and 6 below duplicate EQ-22 columns (reaction decomposition, `react`), reuse EQ-22's columns and delete the duplicate; do not build twice. Shared refactors use the common IDs REF-event-feature-store, REF-event-key, REF-dsf-reader, REF-period-split, REF-placebo-runner.

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Take: a single, audited, point-in-time (PIT) per-event feature table so that every signal experiment from the library differs only in the rule applied to it, not in how inputs were built. The library's contract (SIGNAL_DEFINITIONS section 10): every feature carries a computation cutoff, a lookback, and an assertion `as_of <= decision_time`.
Features to take: (a) `sue_hist` (EA1904 definition: surprise over rolling std of the firm's own prior surprises), (b) `sue_analyst_raw` and `sue_seasonal_raw` as already built in `data/earnings/ibes_clean_1995_2014.parquet`, (c) daily relative volume (RVOL), (d) reaction decomposition components (pre-announcement close -> day0 open -> day0 close), (e) timing class BMO / INTRADAY / AMC / UNKNOWN from IBES `anntims`.
Do NOT take: EA1904's undisclosed "high-frequency volume expansion" (no intraday data here; we only define a daily proxy and label it a proxy), the FactSet Revere / GICS graph edges, any feature built from yfinance/FMP vintages.

## 2. Evidence grade & known defects in the source
Library grades: EA1904 C/D; QuantConnect C; leeyawnnn C. None reproducible. Defects relevant to feature building: EA1904's sigma window/min-count unpublished (we pre-register K=8, min 4, see 3); QuantConnect's `universe.py` (SUE denominator) unseen; leeyawnnn's SUE is "demeaned seasonal" and shares only 0.20 rank correlation with a consensus SUE, so flavors must never be mixed under one label; the table therefore stores flavors in separate columns and a rank-correlation report between them.

## 3. Exact rules
Key: the shared event key of REF-event-key as defined in EQ-22: `(permno, day0_date, dup_rank)` (the plain `event_id = permno_day0` column of `equity_vol_panel.parquet` has ~105 duplicates, so it is NOT unique). Storage follows REF-event-feature-store as defined in EQ-22: side tables under `data/event_features/` (this spec writes `data/event_features/sue_flavors.parquet`) joined by the loader, rather than widening `equity_vol_panel`. One row per event in `equity_vol_panel` (172,923 rows); the join is m:1 and must assert the row count is unchanged (the pattern used at `pead/prep/equity/build_deciles.py` lines ~66-79 after a duplicate-key problem on `(permno, day0_date)` with `n_events_same_permno_day0`).
Columns (all computed with data dated strictly before the event's decision time, defined as the day0 close for daily-bar features):
1. `timing_class`: from `anntims` in `data/events/equity_events_1996_2013.parquet`: `< 09:30:00` BMO; `[09:30, 16:00)` INTRADAY; `>= 16:00` AMC; `anntims_unknown == True` (12.6% of events, `anntims` is `00:00:00`) UNKNOWN. Verified in data: hours >= 16 have `day0_lag_days > 0` 100% of the time, hours 00-15 about 99% day0 = anndats; unknown events are shifted a session (lag 1 in 29,741 of ~33k) so the repo already treats UNKNOWN as AMC (conservative). Keep that and expose the class so strategies can drop UNKNOWN as an ablation. Verify IBES `anntims` is US Eastern (WRDS documents it so; confirm on a handful of known AMC reporters before relying on the 09:30/16:00 cut).
2. `sue_analyst_raw`, `sue_seasonal_raw`, `consensus_numest`, `consensus_staleness_days`, `consensus_stdev`: copied from the event table (not re-derived); `sue_source` kept.
3. `sue_hist` (EA1904): `s_e = eps_actual_adj - consensus_meanest_adj`; `sue_hist = s_e / sigma_i`, `sigma_i = std(s_{i,-1..-K})` over the K=8 most recent PRIOR events of the same `permno` whose `anndats` is strictly earlier than this event's `anndats`, requiring >= 4 valid priors, `sigma_i >= sigma_floor` where `sigma_floor = max(0.01, 5th percentile of sigma over earlier events)` else NaN; the current surprise is excluded from sigma. Per-share basis: put `eps_actual_adj` and consensus on the same share basis using the `cfacshr_*` / `eps_basis_status` columns already in `ibes_clean_1995_2014.parquet`; drop (NaN) events where `eps_basis_status` signals a basis mismatch (inspect the value set first; do not guess). Log exclusion reason per row in `sue_hist_reason`.
4. `rvol_d`: `vol_day0 / median(vol over the 20 sessions ending day0 - 1)`; requires >= 15 valid prior sessions; `vol` from `data/normalized_equity/equity_YYYY.parquet`. Also `rvol_d_prev = vol_{day0-1}/median(prior 20)` as a pre-event control. Note NASDAQ volume double counting before ~2004 changes levels across exchanges; the RVOL is a ratio within the same stock so this only matters across the NASDAQ regime break; report mean RVOL by year to show it.
5. Reaction decomposition (needs REF-dsf-reader): `ret_gap = openprc_day0 / close_{pre} - 1`, `ret_intraday = close_day0 / openprc_day0 - 1`, `ret_reaction = close_day0 / close_{pre} - 1` where `close_pre` is the last close before the release (BMO / INTRADAY: close of day0-1; AMC: close of day0-1 which is the announcement session itself, because day0 is already shifted to the next session). All prices `|prc|` split-adjusted with `cfacpr`; set NaN when `prc_is_bidask_avg` or `openprc` missing or zero.
6. Post-reaction drift labels for diagnostics only (never used in trading rules): `drift_20` = close_{day0+20}/close_day0 - 1, mkt-adjusted with `vwretd`.
6b. Columns requested by other specs: `sue_seas_8` (K=8 prior seasonal changes, min 6, current excluded), `sue_seas_dm` (demeaned seasonal, leeyawnnn flavor), `sue_seas_rank` (= `pit_rank.rank_pct(sue_seas_8, ann_quarter, mode="prior_quarters")`) (EQ-06); `react = ret_reaction`, `open_available` (EQ-03); `trend_ok` (index above its 100-session SMA at day0-1 close, EQ-04); `atr_proxy` (mean |ret| of the 14 sessions ending day0, EQ-04); `peer_*` columns (EQ-07).
7. `as_of_assert`: a boolean column that is True only when all inputs have dates <= day0 close (always True by construction; the unit test asserts it by truncation, section 6).

## 4. Data requirements & availability in THIS repo
Present: IBES clean table with `anntims`, consensus mean/stdev/numest/staleness, `sue_analyst`, `sue_seasonal`, `seasonal_diff_stdev` (verified columns); CRSP daily panel `data/normalized_equity/equity_*.parquet` (columns `permno,date,ticker,prc,prc_is_bidask_avg,ret,retx,shrout,vol,cfacpr,cfacshr`); raw CRSP `data/raw_wrds/crsp/dsf_YYYY.parquet` HAS `openprc, bid, ask, bidlo, askhi, numtrd` which the normalized panel DROPS.
Missing: opens in the normalized panel and in the cached `equity_panel.npz`; a daily-bar (not intraday) volume proxy only; no press-release timestamp (IBES `anndats/anntims` is the best available); no analyst revision history (roadmap item 5).
Proxy: RVOL on daily volume (documented as proxy for EA1904 "high-frequency volume expansion").

## 5. Framework mapping
New prep stage: `pead/prep/equity/event_features.py`, registered in `pead/prep/stages.py` as `Stage("event_features", "equity/event_features.py", _o("event_features/sue_flavors.parquet"), ["vol_features"], doc=...)` (same form as the `vol_features` stage verified at stages.py line 66). Output `data/event_features/sue_flavors.parquet` (git-ignored like other big derived data; small QA CSV tracked).
Loader: add `load_event_features()` to `pead/core/data.py` and an opt-in merge in `_load_events()` (see 5b) so `EquityStrategy.weights(events)` sees the columns; strategies declare them in `required_columns` (attribute exists in `pead/strategies/base.py::EquityStrategy.required_columns`).
Params dict: `dict(sue_k=8, sue_min_prior=4, rvol_window=20, rvol_min_obs=15, unknown_policy="amc")`.
No strategy class here (infrastructure spec); a QA script `pead/prep/equity/event_features_qa.py` prints coverage and flavor rank correlations.

## 5b. Build vs. change vs. restructure
(1) NEW: `pead/prep/equity/event_features.py` (stage); `Stage("event_features", ...)` entry; `pead/core/data.py::load_event_features()`; `data/event_features/sue_flavors.parquet`; `tests/test_event_features.py`; `REF-dsf-reader` panel arrays (shared with EQ-03).
(2) CHANGE: `pead/core/data.py::_load_events` (add optional left-merge on `event_id`, assert unchanged length; regression risk none if the file is absent or columns are only added); `pead/core/data.py::_load_panel` + `_panel_sources` cache key (add `openprc` array, bumps cache once; risk: the npz cache key includes source sizes, so old caches rebuild; existing strategies read only `ret_*`, verify final NAVs in `tests/test_framework.py::test_default_vintage_pins_corrected_final_nav` unchanged).
(3) RESTRUCTURE: REF-event-feature-store (this spec; one derived table, one loader, instead of each strategy re-reading IBES); REF-dsf-reader (extend `pead/prep/` normalized equity build to carry `openprc` or read it from `dsf_*.parquet`, `EquityUniverse` gets `panel_open`).
(4) IMPROVE: coverage of consensus fields is 84% (`consensus_meanest` notna 0.842) and `anntims_unknown` 12.6%; add QA table by year; document `anntims` timezone; add a test that the table is invariant to deleting all later rows (REF-pit-test-kit).

## 6. Acceptance tests
- Unit, synthetic: 12 quarters for one permno with known surprises -> hand-computed `sue_hist` equals output; sigma excludes the current surprise; fewer than 4 priors -> NaN; sigma below floor -> NaN.
- No-look-ahead (truncation invariance): build the table on the full data and on data with all rows dated after a cutoff removed; features of events with day0 <= cutoff are identical (assert_allclose with equal_nan). Also rewrite all post-cutoff `eps_actual` to garbage and assert earlier rows unchanged.
- Timing fixtures from library `02_EVENT_TIME_PIT.md`: Friday 16:20 release with Monday holiday -> day0 = Tuesday; 15:59 release classed INTRADAY, 16:00 AMC; half-day (13:00 close) release at 13:15 is AMC for that session. Check against the `day0_date` logic that produced `equity_events_1996_2013.parquet` and report any mismatch (do not silently re-derive day0).
- Determinism: two builds byte-identical (parquet hash) with fixed ordering.
- Row-count and key uniqueness: `event_id` unique, join length equals `len(equity_vol_panel)`.
- RVOL: a stock with constant volume has rvol 1.0.

## 7. Experiment plan
No P&L experiment. QA outputs: coverage by year and flavor; Spearman between `sue_hist`, `sue_analyst_raw`, `sue_seasonal_raw` (library reports 0.20 for consensus vs demeaned seasonal in the leeyawnnn data; ours is an independent measurement, do not assume it); mean RVOL by year; share of events by `timing_class` (library, SEC-based: 55% pre-market, 39% after close, ~5% intraday; we compare, differences are a finding about IBES `anntims`).

## 8. Expected outcome / hypotheses and risks
Expected: ~84% coverage for consensus-based features, 100% for seasonal where >= 8 prior diffs exist. Risks: IBES `anntims` can be the time the analyst record was entered, not the press release (some BMO releases look like intraday); unknown timestamps default to AMC; NASDAQ volume convention; splits in EPS basis. Data ends 2013, so nothing here says anything about post-2013 microstructure.

## 9. Dependencies and implementation checklist
Depends on: none (root). Needed by EQ-01, 03, 04, 05, 06, 07, 08.
- [ ] REF-dsf-reader: read `openprc` from `dsf_*.parquet`, align to `(permno, date)` panel order, store in npz cache
- [ ] event_features stage + QA
- [ ] timing_class + timezone check
- [ ] sue_hist with exclusion reasons
- [ ] rvol_d
- [ ] reaction decomposition columns
- [ ] loader merge + `required_columns` wiring
- [ ] tests (synthetic, truncation invariance, determinism)
