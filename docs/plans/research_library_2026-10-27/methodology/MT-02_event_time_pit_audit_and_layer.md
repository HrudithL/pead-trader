# MT-02: Event-time / point-in-time audit of our pipeline and a canonical event-time layer
- Status: planned | Priority: P0 | Effort: M | Source(s): library `foundations/02_EVENT_TIME_PIT.md`, `foundations/06_DATA_SCHEMAS_WRDS_OPTIONMETRICS.md` (join invariants), `reproduction/01_REPRODUCTION_PLAYBOOK.md` section 2 (state machine), `audits/03_BACKTEST_FAILURE_MODES.md` section 1; repo `pead/prep/**`, `pead/core/data.py`, `pead/engines/equity.py`
- Measurements below were made 2026-10-27 on the local `data/` (corrected vintage) with throwaway scripts; no repo code was changed.

## 1. Purpose & what we take from the library
Adopt the library's event record (`fiscal_period_end`, release timestamp, consensus snapshot time, decision time, fill time, first reaction session) and its join invariants (`announcement >= every input forecast`, `feature_end <= decision`, `fill >= decision`), and apply them to our own pipeline. Deliver (a) the audit checklist (section 3A), (b) a small shared module `pead/core/event_time.py` that encodes the rules once so the equity builder, the options builder, the live engine and the tests agree (REF-event-time-layer).

## 2. Gap analysis vs the repo today
The repo's convention is sound where it can be verified: after-close reporters are shifted a session, unknown times are shifted, entry is the day0 *close*, P&L accrues from day0+1, trailing features end at day0-1. The gaps are (i) the code that builds the event table and the SUE is **not in the repo**, (ii) several data-hygiene and bucket-consistency discrepancies, (iii) delisting returns are absent, (iv) the strategy-facing table carries outcome columns. Details are the checklist.

## 3. Exact specification
### 3A. Audit checklist (status: OK / DISCREPANCY / UNVERIFIABLE; every item becomes a test in MT-01 LA-15/16 or a ticket)

| # | Rule (library) | What the repo does (evidence) | Status | Action |
|---|---|---|---|---|
| E1 | Announcement timestamp -> first executable session; after-close goes to next session | `data/events/equity_events_1996_2013.parquet` has `anntims`, `anntims_unknown`, `day0_lag_days`. Empirical rule recovered: `anntims >= 16:00` => shifted (0 of ~100k violations; all rows at 16:00-16:05 have shift share 1.0, 15:55-15:59 have shift share 0.0-0.057). | OK (empirical) | LA-15(a) |
| E2 | The code that makes `day0_date` must be in the repo | `build_deciles.py:117-123` only *reads* the events file; no stage in `pead/prep/stages.py` produces `equity_events_1996_2013.parquet`, `ibes_clean_1995_2014.parquet` or `event_classification_features.parquet` (read at `build_deciles.py:28`). A scratch builder lives in git-ignored `earnings_build.tmp/data.py` and `meta/build_events.py` consumes, not creates. | DISCREPANCY (provenance) | REF-ibes-builder; MT-07 |
| E3 | Unknown release time -> next session + uncertainty flag | `anntims_unknown` is True for 12.6% of day0-ok events and 100% of them have `day0_lag_days>=1` (conservative). Hour-0 sentinel count (33,339) slightly exceeds the flag count (~32.8k): ~500 genuine 00:xx stamps or flag gaps. | OK, minor | Stress +1/+5/+30 min and "unknown=same day" variants in MT-11 |
| E4 | Same-close tradability of releases inside the session | Releases 09:30-15:59 enter at day0 close (`docs/STEADY_DRIFT.md:28`; engine accrues from `entry_row_idx+1`, `equity.py` `flat_row`). Releases stamped 15:55-15:59 (about 290 events) get a 16:00 close fill that is not realistically attainable. | DISCREPANCY (small) | `event_time.fill_session(ts, cutoff='15:50')` option; MT-11 sensitivity |
| E5 | Pre-market releases: library says open is tradable; we enter at close | ~86.8k of 261.5k day0-ok events are stamped 06:00-09:59 (hours 6-9; hour 9 includes post-open 09:30-09:59 stamps) and enter at the day0 *close*, forfeiting the whole day0 reaction. That is conservative and is the reason the repo's drift numbers are not comparable to a next-open study (leeyawnnn 4.1% next-open vs 1.6% next-close, library `strategies/05`). | OK, conservative; NOT comparable to next-open numbers | MT-13 (next-open variant using CRSP `openprc`) |
| E6 | Report date vs period end; non-negative reporting lag | 61 analyst-sourced events have `anndats < fpedats`; `anndats - fpedats` has max 1,707 days (median 29, p75 37). `build_deciles.py` applies no filter. | DISCREPANCY (data hygiene) | Flag `report_lag_days` in event layer; exclude `<0`; sensitivity for `>150` |
| E7 | IBES date vs independent date | Compustat `rdq` exists in `data/raw_wrds/comp/fundq.parquet` (columns incl. `rdq`) but is never used (`grep rdq pead` empty). | GAP | Cross-check module: flag events with `|anndats - rdq| > 1 trading day`; measure effect on top-decile membership |
| E8 | Consensus snapshot strictly before release | `consensus_statpers < anndats` holds for all 194,316 analyst rows (0 violations of `>=`). `n_lookahead_excluded` column shows the builder excluded late snapshots (mean 0.038/row). | OK | LA-15 add |
| E9 | Staleness cap on consensus | `consensus_staleness_days` median 14, p75 25, max 1,880; no cap applied at `build_deciles.py:123` (`sue_source=='analyst'`). | DISCREPANCY | Staleness-capped variants (30/60/90d per playbook 1B) as SUE flavors (MT-07) |
| E10 | Estimates built from analyst-level detail with per-analyst timestamps | Uses summary table (`statsumu_epsus`) via `consensus_*` columns; IBES detail `detu_epsus.parquet` (14.6M rows) is local but unused. | GAP | MT-07 (dispersion-from-detail, revisions) |
| E11 | EPS basis consistent between actual and estimate | `eps_basis_status = anndats_basis_assumed` for 281,002 of 287,844 rows (97.6%), `anndats_basis_confirmed` for only 689. | DISCREPANCY (assumption) | Report as data-quality tier in manifest; test split-adjusted subset (`share_basis_status=='adjusted'`: 89,923) separately in MT-09 |
| E12 | Ranking pool strictly earlier | `pit_rank.rank_pct(mode='prior_quarters')` (verified `pead/common/pit_rank.py`); pool = all events in strictly earlier `ann_quarter`. | OK | LA-08 |
| E13 | Same bucket definition in the ranker and the engine | `ann_quarter` is built from `anndats` (`build_deciles.py:127`); the engine buckets by `day0_date` quarter (`equity.py:166 entry_q`). 208 of 182,712 analyst events differ. No look-ahead, but quarter-sizing and ranking use different calendars. | DISCREPANCY (minor) | `event_time.quarter_of(day0)` used by both; rebuild and re-pin NAVs |
| E14 | Eligibility filters must not depend on the future | `day0_status=='ok'` (`build_deciles.py:120`) drops 2,275 `no_trading_day_at_or_after_anndats`, 2,605 `anndats_before_permno_coverage_start`, 62 `permno_not_in_equity_panel`, 10 `after_market_shift_past_end_of_panel` (of 266,489 raw events; 261,537 survive). The first is an outcome-conditioned filter (delisted right after the report). `data.py:83` `dropna(subset=[entry_row_idx, exit_row_idx])` is a second. | DISCREPANCY (survivorship) | Count and keep a `dropped_reason` column instead of dropping (library: "persist eligibility flags") |
| E15 | Delisting return | No use of `dsedelist` anywhere in `pead/`; on the 3,890 delistings dated 1996-2013 the last normalized `ret` equals `dlret` for only 3.0% (rows on `dlstdt` exist for all 3,890 so the ordinary last return is present but the delisting return is not); `data.py:58-59` `fillna(0.0)` turns missing returns into 0. 156 of 172,923 funded positions in `equity_vol_panel.parquet` contain a delisting date in their window (9 in decile 10). | DISCREPANCY (real, small) | MT-05 |
| E16 | Trailing features end before the decision | `volatility.py::trailing_window` offsets `-window..-1` from `entry_row_idx`; `avg_dollar_volume_21d` verified equal to the mean of `|prc|*vol` over the 21 sessions ending `entry_row_idx-1` for 3,000/3,000 sampled events. | OK | LA-09, LA-16 |
| E17 | Cross-sectional features use only earlier information | `vol_features.py::prior_quarter_ranks` ranks vs the *previous* quarter, per size quintile, requires >=30 reference names. | OK | LA-09 |
| E18 | Classification features PIT (`size_quintile`, `ff12_sector`, `bm_tercile`, `liquidity_quintile`) | Producer absent from repo (E2). `ff12_sector` presumably static SIC, `size_quintile` timing unknown. | UNVERIFIABLE | Rebuild from CRSP `stocknames` + month-end market cap in the event layer; compare to stored |
| E19 | Fitted quantities use a search window | Decay days fit on the first `SEARCH_FRAC=0.7` of announcement quarters (`daily_decay.py:52,84`); back-off thresholds `MIN_QUARTERS/MIN_EVENTS` and leave-one-out benchmark still full-sample (FIXES.md). | PARTIAL | MT-04 holdout protocol; document in manifest |
| E20 | Outcomes must not be visible to signal code | `hold_ret`, `ret_fwd_60d_mktadj`, `exit_date`, `holding_days` ship in the strategy-facing table (`vol_features.py:115-126`, `data.py:75-86`). | DISCREPANCY (interface) | MT-01 LA-01, REF-outcome-firewall |
| E21 | Options entry consistent with equity entry | Options use day0 horizon-0 bid/ask from OptionMetrics EOD quotes (`forward_prices_optimal.py`), i.e. the same close. OptionMetrics EOD snapshot time is not a trade time; the library warns EOD surfaces "do not suffice" for execution. | OK, labelled | Options area |
| E22 | Cash rate | `core/rates.py` annual T-bill averages typed from memory (stated in the file). | DISCREPANCY (known) | MT-14 REF-tbill-real |
| E23 | Calendar | Trading calendar = `market_benchmark` dates (`data.py::_load_panel`), series ends 2014-06-30 while events end 2013 and last non-zero return is 2014-06-20 for `balanced_leverage`. | OK | Active window (MT-03) |
| E24 | Sizing normalisers that include later reporters | `weights.neutralized_tilt` / `weights_size_sector_neutral` normalise by quarter-wide group totals (FIXES.md residual). | DISCREPANCY | MT-01 LA-11 |
| E25 | Unique event key | 19 duplicate `(permno, day0_date)` among analyst events, 105 in `positions_rankweighted_v2`; liquidity cap not aggregated across overlapping positions (FIXES.md). | DISCREPANCY | LA-18; dedupe priority rule in the layer |

### 3B. `pead/core/event_time.py` (REF-event-time-layer)
```python
CUTOFF_CLOSE = time(16, 0)                      # NYSE regular close, America/New_York
@dataclass(frozen=True)
class EventTimeRule:
    after_close_cutoff: time = time(16, 0)      # release >= cutoff -> next session
    unknown_time_policy: str = "next_session"   # | "same_session"  (stress only)
    fill: str = "close"                         # | "next_open"  (needs openprc, MT-13)
    latency_minutes: int = 0
    intraday_buffer_minutes: int = 0            # 15 -> releases stamped >= 15:45 treated as after-close
def first_executable_session(anndats, anntims, unknown, calendar, rule) -> pd.Series   # day0_date
def report_lag_days(anndats, fpedats) -> pd.Series
def quarter_of(day0_date) -> pd.PeriodIndex      # the ONE bucket used by ranker, vol percentiles, engine
def assert_event_invariants(ev) -> list[str]     # returns violations: day0>=anndats, lag>=0, consensus_statpers<anndats, key unique
def timing_class(anntims, unknown) -> Series[{'premarket','intraday','after_close','unknown'}]
```
Calendar comes from the market-benchmark dates (REF-calendar); weekends/holidays handled by `searchsorted`. Re-derive `day0_date` for every event under the default rule and assert equality with the stored column (bit-exact except documented E4 cases); this *is* the verification that replaces the missing builder.

## 4. Data requirements
Local and sufficient: events parquet, `ibes_clean_1995_2014.parquet`, `comp/fundq.parquet` (`rdq`), CRSP `dsf` (bid/ask/openprc present, columns `['permno','date','ret','prc','shrout','cfacpr','cfacshr','retx','vol','bid','ask','bidlo','askhi','openprc','numtrd']`), `dsedelist`. Not available: 8-K acceptance timestamps, press-release wire times (BLOCKED, see MT-14).

## 5. Framework mapping
`pead/core/event_time.py`; a prep stage `events_audit` (`python -m pead prep events_audit`) writing `data/audit/event_time_audit.json` and `.md`; a CLI `python -m pead audit events`; manifest field `timestamp_policy` (MT-12). `build_deciles.py`, `link_events_secid.py`, `return_distributions.py` import `quarter_of` (E13).

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-event-time-layer = the function library behind EQ's REF-anntims-audit / IMP-anntims and OPT's REF-event-clock (`event_clock.parquet` is the materialised output of `event_time.first_executable_session`); REF-event-key (EQ) is E25 here.
- **NEW:** `pead/core/event_time.py`; `pead/prep/events_audit.py` stage; `data/audit/event_time_audit.{json,md}`; tests LA-15/16 (MT-01); `pead/prep/earnings/` package for the missing builders (REF-ibes-builder: `build_ibes_clean.py`, `build_events.py`, `build_event_classification.py`) re-created from `earnings_build.tmp/data.py` logic and the WRDS raw tables.
- **CHANGE:** `pead/prep/equity/build_deciles.py:127` (`ann_quarter` from `quarter_of(day0_date)`; regression risk: HIGH for NAV pins -- 208 events change bucket, deciles shift slightly; re-pin the five corrected NAVs in `tests/test_framework.py::test_default_vintage_pins_corrected_final_nav` and update FIXES.md); `build_deciles.py:120-123` (keep dropped events with `dropped_reason`, exclude `report_lag_days<0`; risk: MEDIUM, changes event counts); `pead/prep/options/link_events_secid.py` and `return_distributions.py` (must use the same ranking/bucket, per `pit_rank.py` docstring "MUST be the same").
- **RESTRUCTURE:** move event/IBES construction under `pead/prep/earnings/` and register stages in `pead/prep/stages.py` so `python -m pead prep --list` shows the full chain from WRDS raw to `decile_events`; give events one canonical schema document (`docs/EVENT_SCHEMA.md`).
- **IMPROVE:** (a) provenance: write `source_hash` of each input into the output parquet metadata; (b) replace silent `day0_status` drops with flags; (c) record the 97.6% "assumed EPS basis" share in every report footer; (d) pin the data defect counts (E6, E13, E25).

## 6. Acceptance tests
- Re-derived `day0_date` equals stored for >= 99.9% of 261,537 day0-ok events; every mismatch listed in the audit file with its cause.
- `assert_event_invariants` returns exactly the known violations (61 negative lags; 19 duplicate keys) and nothing else.
- After E13 fix: ranker and engine bucket identical (`quarter_of`), 0 mismatches; regenerated NAV pins documented with before/after table in FIXES.md.
- Audit JSON reproducible: two consecutive runs byte-identical.

## 7. Rollout plan
Phase 1 (read-only): `event_time.py` + audit stage, no pipeline change; publish the checklist results. Phase 2: recreate builders (E2) and verify bit-exact against stored parquet. Phase 3: apply E13/E14/E6 changes in a separate vintage directory (`PEAD_DATA_DIR=data/_vintage_pit2`) and compare the leaderboard before committing (same technique as FIXES.md ladder). Phase 4: promote and re-pin.

## 8. Risks
Changing buckets or filters moves every headline number slightly; treat as a methodology fix with a ladder table, not a silent patch. Rebuilding the missing builders may not reproduce the stored files exactly -- then keep the stored files, mark builder `status=approximate`, and do not claim provenance.

## 9. Dependencies and implementation checklist
Depends on: REF-calendar, REF-ibes-builder. Blocks: MT-07, MT-13, MT-11, options/live areas that need `first_executable_session`.
- [ ] `event_time.py` + unit tests (holiday, Friday after-close, half-day, unknown time -- library fixtures)
- [ ] `events_audit` stage and audit report
- [ ] verify re-derivation vs stored `day0_date`
- [ ] E7 `rdq` cross-check numbers
- [ ] recreate IBES/events/classification builders (or mark UNVERIFIABLE permanently)
- [ ] E13/E14/E6 vintage experiment + ladder
- [ ] pin defect counts in `tests/test_pit_data.py`
