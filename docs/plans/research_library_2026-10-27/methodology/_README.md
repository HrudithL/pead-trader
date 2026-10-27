# Methodology and validation infrastructure: spec sheets from the PEAD Research Library (2026-10-27)
Documentation only; no code under `pead/` or `tests/` was changed. Library read (all of `00_START_HERE`, release notes, foundations 01-07, audits 01-06, comparisons 01-03, reproduction 01-05 + `pead_reference.py` + tests, references 01-02, `MANIFEST.json`) against the repo (`AGENTS.md`, REPO_MAP, FIXES, ROADMAP, STEADY_DRIFT, ARCHITECTURE, `pead/core|common|engines|reporting|prep|strategies/base.py`, tests, leaderboard). All repo numbers below were measured on 2026-10-27; nothing is estimated.

## Listing and ranking (recommended build order = ranking)
| Rank | ID | Title | Pri | Effort | Why this rank |
|---|---|---|---|---|---|
| 1 | MT-01 | Look-ahead and accounting failure-mode suite | P0 | M | Cheap, no data, gates everything else; closes the "outcome columns visible to `weights()`" hole |
| 2 | MT-03 | Metrics and inference v2 (active window, PSR/DSR, bootstrap, HAC) | P0 | M | Every number in the repo is affected; additive first step needs no re-run |
| 3 | MT-05 | Delisting returns and survivorship | P0 | S | Local data unused (`dsedelist`); closes FIXES "unknown" item |
| 4 | MT-02 | Event-time / PIT audit and event-time layer | P0 | M | Checklist of 25 items; missing builders; shared layer for equity, options, live |
| 5 | MT-04 | Evaluation protocol and harness (search/holdout, trial registry, gates, leaderboard v2) | P0 | L | The promotion gate for all areas; depends on 1, 2, 3 |
| 6 | MT-10 | Cross-validation port of `pead_reference.py` | P1 | S | Independent oracle; 8/8 library tests pass on our interpreter |
| 7 | MT-12 | Run manifest, evidence grade, results schema v2 | P1 | M | Reproducibility metadata; separates "validated" from "has an edge" |
| 8 | MT-14 | Data-gap register | P1 | S | Tells every area what is local, proxy or blocked |
| 9 | MT-06 | Cost models (spread, borrow, impact, options friction) and cost stress | P1 | M | Edge is cost-sensitive; short legs currently free of borrow |
| 10 | MT-11 | Red-team and placebo experiments | P1 | S | Time inversion, delay curve, 200-seed placebo p-values |
| 11 | MT-08 | Concentration and leave-one-out stress | P1 | S | Library's NVDA-style test; engine supports exact reruns |
| 12 | MT-09 | Paired factor/risk ablation harness | P1 | M | Replaces `meta/` scripts with a single paired, matched-risk tool |
| 13 | MT-07 | SUE flavour library and IBES provenance | P1 | M | Needed before any non-default signal; rebuilds missing builders |
| 14 | MT-13 | Next-open entry and gap decomposition | P2 | M | Makes repo numbers comparable with next-open studies; uses local `openprc` |

## Key findings in our own pipeline (details and evidence in the specs)
1. **Outcome leakage surface:** `hold_ret`, `ret_fwd_60d_mktadj`, `exit_date`, `holding_days` are columns of the table every `weights()` receives (`vol_features.py:115-126`, `data.py::_load_events`). No current strategy was shown to misuse them, but nothing prevents it (MT-01 LA-01).
2. **Delisting returns are not in the return panel:** last-row `ret` equals `dlret` for only 3.0% of 3,890 delistings (1996-2013); 156 of 172,923 funded positions contain a delisting (9 in decile 10); missing returns are zero-filled (`data.py:58-59`) (MT-05).
3. **Headline Sharpe/CAGR include flat warm-up:** `balanced_leverage` 316 flat days; reported Sharpe 1.092 / CAGR 5.38% vs 1.129 / 5.76% on the active window; the alpha code and the paper already use the active window, the leaderboard does not (MT-03).
4. **No untouched holdout and no trial registry; leaderboard is raw argmax** (`store.py:106-118`). Search window (first 49 of 71 announcement quarters) ends 2008Q2, so the 22-quarter holdout starts at the financial crisis and is already burned (MT-04).
5. **The code that builds `day0_date`, SUE inputs and classification features is not in the repo** (only consumed by `build_deciles.py:28,117-123`). The day0 rule was recovered empirically: `anntims >= 16:00` shifts to the next session (0 violations), unknown times (12.6%) always shift. Data hygiene: 61 events with `anndats < fpedats`, 19 duplicate keys, 208 events whose ranking quarter differs from the engine quarter (MT-02).
6. **PIT items that check out:** consensus snapshot strictly before release for all 194,316 analyst rows; trailing features end at day0-1 and `avg_dollar_volume_21d` matches that rule exactly on 3,000 sampled events; PIT rank uses strictly earlier quarters. The EPS basis is only *assumed* for 97.6% of rows.
7. **Unused local data that the library says matters:** `openprc`, closing `bid/ask`, `rdq`, `dsedelist`, IBES detail (`detu_epsus`, 14.6M rows); risk-free series is typed from memory.
8. **Known non-causal knobs:** `cap_mode='priority'|'proportional'` and `neutralized_tilt` quarter-wide normalisers; MT-01 turns them into registered xfails with a measurement experiment.

## Overlaps with other areas (resolve once; see `_ENABLING_CHANGES.md`)
EQ-08 (`tests/pit_kit.py`) = MT-01 fixtures; EQ-30 / OPT `REF-search-holdout` / MT-04 windows must be ONE definition (decision needed: 70%-of-quarters vs `day0 <= 2005-12-31`); OV-01 (`pead/validation/`) implements MT-08 and the placebo/bootstrap parts of MT-09/11; OPT's `REF-fill-model`/`REF-leg-engine` = multi-leg options model with the library's 47.4 oracle; EQ `REF-cost-model|source|borrow-fee` = MT-06's single `CostModel` hook; OV `REF-tbill-real` = MT-14 D12.

## Rejected or deferred ideas (with reasons)
- **Runs-test of sign randomness as validation** (EA1904): rejection of randomness is not alpha (library audits/01).
- **Full CPCV / PBO machinery now:** with ~66 saved runs, a 1996-2013 sample and 200-session holdings, purged walk-forward plus DSR and paired bootstrap is proportionate. Revisit if a sweep family exceeds a few hundred trials. Deferred.
- **White Reality Check / SPA:** deferred behind BH-FDR across ablation pairs and DSR with an honest trial count.
- **Replacing the fixed-notional quarterly engine with an order-level broker simulator** (library typed-record engine): rejected; reconciliation tests plus the reference oracle give the assurance at far lower risk. Revisit only for multi-leg options.
- **Adopting library "proposed experimental constants"** (1% issuer risk, 10% name, 30% sector, 5/15/30 bps) as defaults: used as sweep grids only; they are not source parameters.
- **Minute-level latency stresses (+1/+5/+30 min) and intraday fills:** no intraday data; degraded to session-level policies (MT-11 R7, MT-14 D9).
- **UTC-converting all timestamps:** IBES `anntims` are exchange-local times at daily granularity; a DST-safe session rule inside `event_time.py` is enough.
- **Calendar-day (365.25) CAGR basis:** keep 252-trading-day convention on the active window; document it.
- **Reproducing the 15 external strategies clean-room:** belongs to the equity/options areas; here only the reference port.
- **Sortino rewrite:** the repo already uses semi-deviation over all observations (the library's definition up to n-1).

## Files
`MT-01_lookahead_failure_mode_suite.md`, `MT-02_event_time_pit_audit_and_layer.md`, `MT-03_metrics_and_inference_v2.md`, `MT-04_evaluation_protocol_and_harness.md`, `MT-05_delisting_returns_and_survivorship.md`, `MT-06_cost_models_spread_borrow_impact.md`, `MT-07_sue_flavors_and_ibes_provenance.md`, `MT-08_concentration_and_leave_one_out_stress.md`, `MT-09_paired_factor_and_risk_ablation_harness.md`, `MT-10_reference_cross_validation_port.md`, `MT-11_red_team_and_placebo_experiments.md`, `MT-12_run_manifest_evidence_grade_result_schema.md`, `MT-13_next_open_entry_and_gap_decomposition.md`, `MT-14_data_gap_register.md`, `_ENABLING_CHANGES.md`.
