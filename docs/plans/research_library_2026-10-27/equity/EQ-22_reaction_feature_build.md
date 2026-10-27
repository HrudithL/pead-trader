# EQ-22: Reaction-session feature build (day-0 excess return, gap vs intraday split, abnormal volume, 2-day window) and the event feature store
- Status: planned | Priority: P0 | Effort: M | Source(s): library `strategies/17_ALBERT_PEREZ_POST_EARNINGS_INTRADAY.md` (https://github.com/AlbertPerez7/earnings-stock-analyzer), `23_LAN_ORJ_INVESTOR_ATTENTION.md` (Lan et al., IRFA 95, 103460, 2024), `08_AARSH_DAYAL.md`, `12_DYLAN_JEON.md`, `foundations/01_SIGNAL_DEFINITIONS.md` sections 7-9, `foundations/02_EVENT_TIME_PIT.md`

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Library theme: separate the information revealed before you can trade (the overnight gap, the reaction session) from what remains after the first tradable price, and let those pieces be separate features. EQ-23, EQ-24, EQ-25, EQ-26, EQ-27 and EQ-28 all need the same per-event numbers, so they are built once here as a side table plus the loader that attaches it to the events the strategies see. Take: gap = open(day0)/close(day0-1) - 1; intraday = close(day0)/open(day0) - 1; excess day-0 return; 2-day return robust to +-1 session mis-dating; abnormal volume ratio computed only from prior sessions. Do NOT take: Perez's one-time top-25 ticker selection, anything that sums gap and intraday into a "PEAD return", Lan's unrecoverable ORJ transform (formula not accessible; EQ-24 uses a proxy), Dylan's whole-sample 1/99 percentile winsorisation.

## 2. Evidence grade & known defects in the source
Perez = library grade B/C: README/pipeline checked, no numbers established, train/OOS boundary stated inconsistently (2017 vs 2018), no costs. Lan = D/E: abstract only, Chinese A-shares. Repo-side defect that matters most: `anntims_unknown` in `data/events/equity_events_1996_2013.parquet` is 95% in 1996, 85% in 1997, 27% in 1998, then 0-4% from 1999 (verified by year); the events builder that assigns `day0_date`/`day0_lag_days` is not in `pead/prep` (stages start from the events parquet), so for early years day0 may be the announcement day when the release was after the close, which makes "day-0 return" pre-news. Every feature below must carry a `timing_known` flag and acceptance tests must be repeated on `timing_known` only (see REF-anntims-audit).

## 3. Exact rules
Notation: event e has permno p, `entry_row_idx` r (row of day0 in the (permno,date)-sorted panel; the engine enters at the day0 close and earns returns from r+1), `day0_date`. All raw prices from CRSP daily files `data/raw_wrds/crsp/dsf_YYYY.parquet` (columns incl. prc, openprc, bid, ask, bidlo, askhi, vol, numtrd; `data/normalized_equity` has NO openprc/bid/ask, verified).
Features (all stored as float32, NaN when an input is missing, never imputed to 0):
- `d0_ret` = ret_raw[r] (CRSP `ret`, includes the overnight gap and the intraday move of the reaction session).
- `d0_excess` = d0_ret - vwretd on day0 date (same quantity as `d0ex` in `meta/debate2/D_lib.py`: `ev["d0ret"] - vw[...]`).
- `gap` = |openprc[r]| / |prc[r-1]| - 1 using price series made comparable through `cfacpr`: p_adj = |price| / cfacpr; `intraday` = |prc[r]|/|openprc[r]| - 1 (same adjustment). If `openprc` is NaN or 0, or prc[r-1] is a bid/ask average (`prc<0`), gap is NaN (flag `gap_ok=False`). `(1+gap)*(1+intraday) - 1` must equal `d0_ret` within 2% relative on dividend-free, split-free days; otherwise set gap_ok False.
- `er2` = close(r)/close(r-2) - 1 total-return based (product of 1+ret over rows r-1, r), minus the same for vwretd. Robust to day0 being one session late.
- `gap_z` = gap / std(overnight gaps of the same permno over its previous 60 sessions that end at r-1, needs >= 30 valid, floor 0.004). Overnight gap history series = openprc[t]/prc[t-1]-1 per day.
- `abn_vol` = vol[r] / median(vol[r-60 : r-1]) (needs >= 30 valid; vol in CRSP shares; NASDAQ volume is inflated before 2004 by dealer double counting, so the ratio is only comparable within a firm and flagged `vol_era=pre2004`).
- `timing_known` = ~anntims_unknown (from the events file, joined via ibes_ticker/fpedats/anndats; see REF-event-key).
- `n_prior_gaps` count used for gap_z.
No feature may read any row beyond r (assert max index used == r). Output: `data/event_features/reaction.parquet`, key `(permno, day0_date, dup_rank)`, where `dup_rank` disambiguates the 105 duplicate (permno, day0) rows (REF-event-key).

## 4. Data requirements & availability in THIS repo
Available: dsf files 1995-2014 (openprc non-null 98.1% of rows in 2005, 98.2% in 1997 as sampled), events with anntims flag, panel row index via `pead/prep/equity/lib/panel.py::load_mktadj_panel`. Missing: intraday bars/NBBO (BLOCKED for anything minute-level: needs TAQ or equivalent from 1996); index overnight gap (no index open): proxy `gap` is stock-only, demeaned later by cross-sectional median of same-day events when >= 20 events (documented in EQ-24). CRSP openprc quality before 1992 for NYSE is poor, but the sample starts 1996, so acceptable; still audit missing-rate by year and exchange code (`exchcd` is not in dsf; use stocknames.parquet).

## 5. Framework mapping
- New prep stage `reaction_features`: file `pead/prep/equity/reaction_features.py`, registered in `pead/prep/stages.py::STAGES` after `vol_features` (outputs `event_features/reaction.parquet`, deps `["vol_features"]`, kind `io`). Reuse `pead/common/volatility.py::trailing_window` (verified signature `trailing_window(values, eq_permno, entry_row_idx, event_permno, window)`) for the 60-session windows; reuse `pead/prep/equity/lib/panel.py::load_positions`.
- Loader: `pead/core/data.py::_load_events` left-joins every parquet in `DATA_DIR/event_features/` onto the events (REF-event-feature-store). `EquityStrategy.required_columns` (in `pead/strategies/base.py`) lists the new columns for any strategy that uses them, so the live engine knows what it must supply.
- Engine change: none.
- Params: `GAP_STD_WINDOW=60`, `GAP_STD_MIN=30`, `GAP_STD_FLOOR=0.004`, `VOL_WINDOW=60`, `VOL_MIN=30`.

## 5b. Build vs. change vs. restructure
1. NEW: `pead/prep/equity/reaction_features.py`; `pead/prep/equity/lib/dsf.py` (REF-dsf-reader: load selected dsf columns per year, align to the panel's (permno,date) sort so row indices match `entry_row_idx`); `data/event_features/` directory convention (git-ignored like other derived data, document in `docs/REPO_MAP.md` section 6); `tests/test_reaction_features.py`.
2. CHANGE: `pead/core/data.py::_load_events` (add left-join of side tables; assert `len(out)==len(base)` and unchanged row order and index so every existing strategy and the five pinned NAVs in `tests/test_fixes.py::test_default_vintage_pins_corrected_final_nav` are untouched; regression risk low but the pin test must be run). `pead/prep/stages.py::STAGES` add one `Stage`. `_panel_sources`/cache key in `_load_panel` unchanged.
3. RESTRUCTURE: REF-event-feature-store (side tables keyed by event key, loaded generically instead of widening `equity_vol_panel.parquet` each time); REF-event-key (positions/vol panel lack `fpedats`/`ibes_ticker`, and 105 duplicate `event_id`s exist; add `dup_rank` and `fpedats` to `pead/prep/equity/positions.py::KEEP_COLS` OR join side tables on (permno, day0_date, dup_rank) computed identically in both places; changing `positions.py` forces a full rebuild of the vintage and a re-pin, so prefer the second option).
4. IMPROVE: REF-anntims-audit: produce a table of day0_lag_days vs anntims by year and a count of events where `anntims_unknown` and the stock moved > 5% on day0-1 (signature of an after-close release dated one session early); REF-dsf-reader should memoise per-year parquet reads (12 s cold load for the panel today per `pead/core/data.py::_load_panel`).

## 6. Acceptance tests
- Synthetic panel (use `tests/test_steady_drift.py::_panel` style): one stock, known open/close path -> exact gap, intraday, d0_ret reconstruction `(1+gap)(1+intraday)-1`.
- No look-ahead: rewrite all dsf rows after row r (prices, volume) and assert every feature for that event is bit-identical; rewrite row r-1 and assert gap_z changes (sanity that inputs are really used).
- Determinism: two builds produce identical parquet hashes.
- Row-count: after loader join, `len(events)` and `events.index` identical to before; NaN rate of each new column printed per year and asserted < 5% for gap/abn_vol in 1999-2013.
- Duplicate-key: the 105 duplicate (permno, day0) rows each get distinct dup_rank and distinct fpedats-linked features only if the inputs differ (they do not; assert equal features, which proves the dedupe is harmless).
- Pinned NAV test and `tests/test_framework.py` unchanged.

## 7. Experiment plan
Descriptive only (no strategy): by year and `timing_known`, tabulate mean d0_excess, gap, intraday for D10 and D1 using EQ-30. Expected from the library: more than half of the beat-minus-miss spread sits in the first-session move (leeyawnnn, unverified); here check that mean `gap` accounts for most of mean `d0_ret` for D10 in `timing_known` years. Baseline reference rows: `steady_drift_plus` 12.07% / 1.19 / -22.07%. Search window 1996-2005, holdout 2006-2013 (REF-period-split).

## 8. Expected outcome / hypotheses and risks
Hypothesis: gap carries most of the day-0 information; `intraday` and next-day return after a positive gap are small and noisy. Risks: openprc semantics (Nasdaq opening quote vs trade) shift the gap by a few bp; dividends and splits on day r (use `cfacpr` and `retx`); overlapping events (28% of positions overlap the next event of the same stock per docs/FIXES.md); early-year timestamp contamination described above. Output is infrastructure; no return claim.

## 9. Dependencies and implementation checklist
- [ ] REF-dsf-reader (`pead/prep/equity/lib/dsf.py`)
- [ ] REF-event-key decision and `dup_rank`
- [ ] `reaction_features` stage + registry entry
- [ ] REF-event-feature-store loader join + pin test green
- [ ] REF-anntims-audit table
- [ ] Unit tests above
- [ ] `docs/REPO_MAP.md` data section updated
