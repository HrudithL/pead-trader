# MT-07: SUE flavour library, edge-case policy, and reproducible IBES/event builders
- Status: planned | Priority: P1 | Effort: M | Source(s): library `foundations/01_SIGNAL_DEFINITIONS.md` (sections 2-7, 10), `reproduction/05_IMPLEMENTATION_AND_PSEUDOCODE.md` B, `reproduction/02_EXPERIMENTS.md` E1, `reproduction/pead_reference.py::{pct_surprise,lagged_historical_sue}`, `strategies/03_QUANTCONNECT_SUE.md`, `strategies/05_LEEYAWNNN_EVENT_STUDY.md`, `foundations/06` (analyst_forecast, IBES detail construction); repo `data/earnings/ibes_clean_1995_2014.parquet`, `pead/prep/equity/build_deciles.py`

## 1. Purpose & what we take from the library
The library insists that the four SUE denominators are *different signals* (analyst dispersion, historical-surprise sd, seasonal difference, percentage surprise) and that edge cases (zero/near-zero denominators, stale snapshots, minimum observations, basis mismatch) have a preregistered policy and a visible flag. We adopt: (a) a flavour registry with one PIT-safe implementation each, validated against the reference code, (b) the edge-case policy table, (c) experiment E1 (compare flavours in one executor), (d) the "no silent substitution" rule, (e) bring the IBES/event builders into the repo.

## 2. Gap analysis vs the repo today (measured on `ibes_clean_1995_2014.parquet`, 287,844 rows)
- **Only one flavour feeds the book**: `build_deciles.py:123` keeps `sue_source=='analyst'` (184,091 events in the events file) and ranks `sue_analyst = (eps_actual - consensus_meanest) / consensus_stdev` (verified 2026-10-27: recomputing with the unadjusted `eps_actual` matches the stored column with mean absolute difference 0.031 and median 0, residuals up to 133; recomputing with `eps_actual_adj` does NOT match (mean absolute difference about 1,700), so the stored score is built on the unadjusted actual and the split-basis question sits on the consensus side). Seasonal SUE (`sue_seasonal`, 220,946 rows, `seasonal_n_prior_diffs` median 8) exists and is "kept only as an appendix robustness cut" (build_deciles docstring). Spearman between the two = 0.451 on 162,131 rows where both exist (library's historical-consensus vs seasonal comparison gives 0.20, a different pair; neither is interchangeable).
- No percentage-surprise flavour, no historical-surprise-sd flavour, no revision flavour, no reaction-conditioned cells; IBES detail (`data/raw_wrds/ibes/detu_epsus.parquet`, 14.6M rows with `analys, revdats, revtims`) is local but unused.
- Edge cases today: `consensus_stdev` has no value <= 0.005 and 70,426 rows (36%) at <= 0.01 (cent-quantised, a de-facto floor); `consensus_numest >= 2` (min 2; 21,306 rows exactly 2); `consensus_meanest == 0` for 1,536 rows (percent surprise would be undefined); `|sue_analyst| > 20` for 1,968 rows, extremes to about +/-1.8e3 (rank-based use makes outliers inert but any magnitude-based use would not); staleness unbounded (median 14d, max 1,880d, >60d: 1,460; >90d: 170). `eps_basis_status = anndats_basis_assumed` 97.6%.
- No unit tests of any SUE construction in `tests/` (the builder is not in the repo -- MT-02 E2).

## 3. Exact specification
Registry `pead/signals/sue.py` (REF-signal-flavors); all functions take the event frame produced by the event-time layer (MT-02) and return `(score: Series, flags: DataFrame)`; flags never dropped silently, recorded as columns `sue_<flavor>_flag` bitmask: 1 floor_active, 2 insufficient_obs, 3 stale_snapshot, 4 basis_unverified, 5 denominator_zero.

| id | formula | PIT rule | edge-case policy (defaults, pre-registered, grid-tested) |
|---|---|---|---|
| `pct` | `(A - E) / max(|E|, floor)`; floor=0.01 (as reference) and also the stricter "discard if |E| < min_abs" variant (KPH) | E = consensus snapshot strictly before release | `E==0` -> floor + flag, never inf; winsor [-5, 5] on the *score used for magnitude*, ranks unaffected |
| `analyst` (current) | `(A - mean_est) / std_est` | `consensus_statpers < anndats` (holds today) ; staleness cap variants {none, 90, 60, 30} days (playbook 1B) | `numest < 2` -> NaN + flag; `std_est < std_floor` (default 0.01 -> already) -> floor + flag; ties rounded 8 decimals before rank (as `build_deciles.py:131`) |
| `hist_sd` | `(A - E) / sd(prior K surprises)` K=8, min 4 (reference `lagged_historical_sue`) | prior surprises are strictly earlier releases of the same permno (`shift(1)`), test: editing the future 8th EPS leaves earlier scores unchanged (reference test) | `sd < 1e-12` -> NaN; first 4 events NaN (never filled) |
| `seasonal` (current `sue_seasonal`) | `(EPS_q - EPS_{q-4}) / sd(prior 8 seasonal diffs)` | EPS values = **first-reported** (IBES actuals as of that date, not restated); `eps_year_ago` must be the value known at its own release | fewer than 4 prior diffs -> NaN (today `seasonal_n_prior_diffs` min 0: audit what the stored score does at 0-3) |
| `seasonal_demeaned` (leeyawnnn) | seasonal difference minus firm's trailing mean seasonal change, over rolling sd | as above | documented warm-up; reported as separate flavour with its own name |
| `revision` | change in mean consensus over 30/60/90 days before release (from `detu_epsus`) | `revdats/revtims` < release time minus buffer | needs analyst-level construction (latest per analyst, dedupe stale) |
| `reaction_cell` | 2x2: sign(surprise) x sign(day0 return) | reaction known only at the day0 close -> entry must be >= day0+1 close (`EventTimeRule.entry_lag=1`); same-close entry is a look-ahead of the fill (library: "a reaction feature can only be traded at a subsequent quote/bar") | zero-return treated as its own cell |

Ranking: all flavours go through `pit_rank.rank_pct` (prior-quarter pool, MT-02 E12/E13) so comparability is guaranteed. `EquityUniverse.events` gets one `sue_rank_pct_<flavor>` column per enabled flavour; `weights()` selects via `required_columns`. Experiment E1 harness: `python -m pead experiment sue_flavors` runs the same top-decile long-only book (`steady_drift`-style, chronological cap, fixed 60-session hold) for each flavour with identical costs/universe, producing IC by quarter, decile spreads, and the paired differences (MT-09). A flavour may be used in a promoted strategy only if its registry row says `validated_against_reference=true` (see MT-10).

IBES provenance (REF-ibes-builder): recreate `pead/prep/earnings/build_ibes_clean.py` (actuals `actu_epsus` + summary `statsumu_epsus` + `idsum`/`ibcrsphist` link -> `ibes_clean`), `build_events.py` (day0 via `event_time`), `build_event_classification.py`. Verification contract = bit-for-bit (or documented tolerance) agreement with the stored parquet; until that holds, builder status is `approximate` and every downstream manifest says `ibes_clean: stored, builder unverified`.

## 4. Data requirements
Present locally: IBES actuals, summary, detail; Compustat `fundq` (`epspxq`, `rdq`) as an independent EPS and date source for seasonal flavour cross-checks; CRSP for reaction. Missing: first-reported (vintage) EPS history -- IBES actuals file is the closest; revisions of actuals not tracked (library: "IBES summary actuals may include revisions") -> **degraded proxy**: use IBES `actu_epsus` rows with `anndats <= release` and label `vintage=ibes_actuals_asof_release_assumed`. Nothing else blocked.

## 5. Framework mapping
`pead/signals/{__init__,sue.py,flags.py}`; prep stages under `pead/prep/earnings/`; `pead/prep/equity/build_deciles.py` reads the selected flavour via env/arg `PEAD_SUE_FLAVOR` (default `analyst`, so nothing changes); CLI `python -m pead signals list|check|ic`; results `results/signals/ic_by_quarter_<flavor>.csv`.

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** EQ-06 / EQ-21 / EQ-02 define seasonal and historical-surprise flavours as features; `pead/signals/sue.py` here is their single implementation and oracle-tested home (REF-signal-flavors); REF-event-feature-store (EQ) is where flavour columns are joined.
- **NEW:** `pead/signals/sue.py` flavours + `flags`; `pead/prep/earnings/*` builders; `tests/test_signals.py` including a verbatim copy of the reference tests for `pct` and `hist_sd` (MT-10); `docs/SIGNALS.md` (definitions table above); experiment `sue_flavors`.
- **CHANGE:** `pead/prep/equity/build_deciles.py:120-133` (flavour parameter; keep default path byte-identical; regression risk: none by default); `pead/strategies/base.py::EquityStrategy.required_columns` (accept flavour column names); event schema (additional rank columns; `equity_vol_panel.parquet` gets them through `positions.py`/`vol_features.py` -- both carry the superset of columns, check column-order dependence in `prep/equity/positions.py`).
- **RESTRUCTURE:** one place defines "what a surprise is" -- `live/features.py` (live engine scores yfinance EPS surprises, ROADMAP #3) and the research path must call the same flavour functions; move `rank_pct` consumers (`build_deciles`, `link_events_secid`, `return_distributions`) to read the shared event table rather than each re-ranking.
- **IMPROVE:** (a) 97.6% of rows have an *assumed* EPS basis; add a basis-confirmed subset run to quantify (MT-09 paired comparison, 689 confirmed vs 281,002 assumed is too small to power; use `share_basis_status=='no_change'` 192,726 vs `adjusted` 89,923 as the contrast); (b) flags must reach reports as sample-attrition tables (library: "persist eligibility flags instead of dropping events"); (c) cap on staleness as an explicit parameter instead of implicit.

## 6. Acceptance tests (`tests/test_signals.py`)
- `pct`: `(1.1, 1.0) -> 0.1`; `(x, 0.0)` returns finite with flag 1; NaN in -> NaN out (reference test 1).
- `hist_sd`: editing EPS of event 8 leaves scores 1-7 identical (reference test 2); denominators 0 -> NaN; first `min_periods` NaN.
- `analyst`: recompute from raw `actu_epsus` + `statsumu_epsus` for 1,000 random events and match the stored `sue_analyst` within the basis tolerance; any residual > tolerance is listed (this is the first real test of the stored column).
- `seasonal`: perturb a future quarter's EPS -> earlier scores unchanged; events with < 4 prior diffs are NaN.
- All flavours: perturbing consensus snapshots dated at/after `anndats` changes nothing.
- E1 experiment reproducible (fixed seed) and its table written to `results/signals/`.

## 7. Rollout plan
1. Implement `pct`, `hist_sd` from the reference (copy + adapt) with tests. 2. Recompute `analyst` from raw and diff vs stored. 3. Builders under `prep/earnings/` (approximate until verified). 4. Seasonal/demeaned/revision. 5. E1 run on the Steady-Drift-style book; record IC and Sharpe per flavour with DSR counting each as a trial (MT-04). 6. Reaction cell only after `entry_lag` exists in the engine/event layer.

## 8. Risks
- Flavour proliferation is itself multiple testing: every flavour counts as a trial in the registry.
- Rebuilt IBES may not match the stored parquet (older WRDS vintage, mapping choices); then keep stored data authoritative.
- `reaction_cell` with an entry lag changes the economics (misses day-1 return); that is the point of the library's warning.

## 9. Dependencies and implementation checklist
Depends on: MT-02 (event-time layer, builders), MT-10 (reference oracle), REF-signal-flavors, REF-ibes-builder. Blocks: any equity strategy that wants a non-default SUE; ROADMAP #5 (extra signal information).
- [ ] `pct`, `hist_sd` + reference tests
- [ ] recompute-from-raw check of stored `sue_analyst`
- [ ] `seasonal` PIT audit incl. `<4` diffs behaviour
- [ ] flags bitmask + sample-attrition table in reports
- [ ] `revision` from `detu_epsus`
- [ ] E1 experiment + trial logging
- [ ] builders under `pead/prep/earnings/`
