# Research-library plan: status of every spec (2026-10-28)

Plans: `docs/plans/research_library_2026-10-27/`. "Null" = built and evaluated, no improvement over `steady_drift_plus` out of sample. Nothing was promoted past `research`.

| Spec | Status | Where / why not |
|---|---|---|
| MT-01 look-ahead suite | built (LA-03..08, 10, 12, 14, 15, 16, 20; firewall) | `tests/test_lookahead.py`, `test_pit_data.py`, `test_guardrails.py`. Not built: LA-09, LA-11 (neutralization leak measurement), LA-13, LA-18, mutation twins, `pead check`/`tests.json` |
| MT-02 event-time / PIT layer | not built | needs the event builder rebuilt in a separate data vintage and one re-pin; flagged in `docs/MT05_DELISTING.md` |
| MT-03 metrics v2 | built additive (`pead/core/stats.py`) | `metrics.summarize` still legacy; switch needs owner decision D3 |
| MT-04 eval protocol / harness | first cut | split, `pead eval`, trial registry, `leaderboard2`; no freeze, no V2+ levels, gates G4-G8 not automated; constants await D5 |
| MT-05 delisting | built, no effect | `docs/MT05_DELISTING.md` |
| MT-06 costs | grid + spread measurement | `docs/MT06_COST_GRID.md`, `docs/MT09_PAIRED_ABLATION.md`; borrow/impact not modelled (no data) |
| MT-07 SUE flavours / IBES provenance | partly (IC comparison) | `docs/SIGNAL_REPORT.md`; detail-level (`detu_epsus`) rebuild not done |
| MT-08 / OV-01 concentration, LOO | done | `docs/LOO_STEADY_DRIFT_PLUS.md` |
| MT-09 paired ablation | done | `scripts/paired_ablation.py` |
| MT-10 reference cross-validation | not built | library `pead_reference.py` not in the repo; fetch-on-demand decision D4 open |
| MT-11 red team / placebo | placebo + random-gate placebo done | other red-team items not built |
| MT-12 run manifest | done for new runs | `summary.json` `run` block gains python, git_dirty, data_fingerprint, params_hash |
| MT-13 next-open entry / gap decomposition | event-study half done | gap/intraday decomposition in `docs/EQ_LIBRARY_CANDIDATES.md`; open-entry trading modes need an engine first-leg |
| MT-14 data gaps | done | `docs/DATA_GAPS.md` |
| EQ-01 EA1904-style | null | `docs/EQ_LIBRARY_CANDIDATES.md` |
| EQ-02 event feature table | partial | gap/intraday/rvol/seasonal computed on the fly (`gap_features.py`, `event_signals.py`), no persisted table |
| EQ-03 reaction decomposition / entry | A and C done, B not | B needs engine first-leg entry |
| EQ-04 path exits | null | stops fail the holdout |
| EQ-05 Bouddat | long leg null; short leg not built | short collateral/borrow unmodelled; STEADY_DRIFT already shows the short side unprofitable |
| EQ-06 seasonal SUE | null | |
| EQ-07 peer spillover | not built | needs customer/supplier or peer graph (BLOCKED, DATA_GAPS D19) |
| EQ-08 look-ahead kit | built (merged into MT-01 kit) | |
| EQ-21 SUE variants | done, null | `docs/SIGNAL_REPORT.md` |
| EQ-22 reaction features | built on the fly | |
| EQ-23 reaction-confirmed + lag | done, null | `docs/EQ23_REACTION_CONFIRMED.md` |
| EQ-24 overnight gap | done, null | `docs/EQ24_OVERNIGHT_GAP.md` |
| EQ-25 attention proxies | not built | no attention/news data (BLOCKED, D22) |
| EQ-26 spread costs | measured | engine understates one-way cost by ~4 bps |
| EQ-27 next-session gap continuation | not built | needs intraday engine (BLOCKED, D9) |
| EQ-28 era interaction / decay | done, null | `docs/EQ28_ERA_DECAY.md` |
| EQ-29 walk-forward response curve | not built | P2; subsumed by the gate walk-forward in `docs/OV03_TREND_BREADTH_GATE.md` |
| EQ-30 signal harness | built | `pead/core/event_stats.py`, `scripts/signal_report.py` |
| OPT-01 option infrastructure | spread report + go/no-go | multi-leg engine / fills not built because the gate says no-go |
| OPT-02, 04, 05, 06 | not built, by decision | `docs/OPT01_GO_NO_GO.md` |
| OPT-03 deep-ITM substitute | not built, conditional | needs real T-bill series and an exit-quote study |
| OV-02 SUE x momentum | done, null | `docs/OV02_SUE_MOMENTUM.md` |
| OV-03 trend / breadth gate | done, best candidate | `docs/OV03_TREND_BREADTH_GATE.md`; research status, not promoted |
| OV-04 vol-scaled stop | done, null | close-to-close proxy only (no high/low) |
| OV-05 earnings quality | not built | needs a Compustat operating-cash-flow pull (BLOCKED) |
| OV-06 momentum sleeve | not built | separate sleeve; OV-02 shows momentum adds nothing to the SUE book |
| OV-07 multifactor composite | done, null | |

## Decisions still needed from the owner
D2 downloads (daily risk-free series, Fama-French factors, EDGAR times), D3 restating headline Sharpe/CAGR on the active window, D4 reference code policy, D5 gate constants in `protocols/v1.json`, D6 post-2013 data (the only route to a real holdout). Everything above that is marked built is limited by the 1996-2013 sample, the typed-from-memory T-bill series, and a burned holdout.
