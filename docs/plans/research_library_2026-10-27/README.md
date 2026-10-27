# PEAD Research Library: implementation plans (2026-10-27)

Specs derived from `PEAD_Research_Library_Expanded_2026-10-27` (24 public strategy/source dossiers, 6 audits, reproduction kit), mapped onto this repo's framework. **Documentation only: no code, tests or results were changed, and nothing was backtested.** Every performance number in the library is an unverified author claim and is not used as a target.

Start with [`MASTER_ROADMAP.md`](MASTER_ROADMAP.md) (sequencing, gates, decisions needed), then [`ENABLING_CHANGES_MASTER.md`](ENABLING_CHANGES_MASTER.md) (what must be restructured/improved before things can be built).

## Layout

| Folder | Prefix | What | Per-folder files |
|---|---|---|---|
| `methodology/` | MT-01..14 | Validation, PIT/event-time, metrics, costs, evaluation harness, data-gap register | `_README.md`, `_ENABLING_CHANGES.md` (**program-wide REF register, authoritative**) |
| `equity/` | EQ-01..08, EQ-21..30 | Equity signals, entry/exit, features, evaluation | `_README_A/B.md`, `_ENABLING_CHANGES_A/B.md` |
| `options/` | OPT-01..06 | Options infrastructure and strategies | `_README.md`, `_ENABLING_CHANGES.md` |
| `overlays_adjacent/` | OV-01..07 | Gates, overlays, factor combos, validation | `_README.md`, `_ENABLING_CHANGES.md` |

Every spec sheet has the same sections: 1 thesis (take / don't take), 2 evidence grade and source defects, 3 exact rules, 4 data availability here, 5 framework mapping, **5b NEW / CHANGE / RESTRUCTURE / IMPROVE**, 6 acceptance tests, 7 experiment plan, 8 expected outcome and risks, 9 dependencies and checklist. Shared refactors have stable `REF-<slug>` IDs used consistently across areas.

## Spec index and priority

**P0 = do first. P1 = next. P2 = optional / low prior.** Files are in the folder named by the prefix.

### Methodology (`methodology/`)
| ID | Topic | Pri |
|---|---|---|
| MT-01 | Look-ahead / failure-mode regression suite | P0 |
| MT-02 | Event-time / PIT audit and shared layer | P0 |
| MT-03 | Metrics v2 (active window, PSR, DSR, bootstrap, HAC alpha) | P0 |
| MT-04 | Evaluation protocol and harness (search/holdout, gates, leaderboard v2) | P0 |
| MT-05 | Delisting returns and survivorship | P0 |
| MT-06 | Cost models (spread, borrow, impact) | P1 |
| MT-07 | SUE flavours and IBES provenance | P1 |
| MT-08 | Concentration / leave-one-out stress | P1 |
| MT-09 | Paired-factor and risk ablation harness | P1 |
| MT-10 | Cross-validation against library `pead_reference.py` | P1 |
| MT-11 | Red-team and placebo experiments | P1 |
| MT-12 | Run manifest, evidence grade, results schema v2 | P1 |
| MT-14 | Data-gap register | P1 |
| MT-13 | Next-open entry and gap decomposition | P2 |

### Equity (`equity/`)
| ID | Topic | Pri |
|---|---|---|
| EQ-08 | Look-ahead invariance test kit | P0 |
| EQ-02 | Event feature table | P0 |
| EQ-21 | Alternative SUE definitions | P0 |
| EQ-22 | Reaction/gap/volume feature build | P0 |
| EQ-30 | Signal evaluation harness (IC, clustered inference, placebo) | P0 |
| EQ-01 | EA1904-style SUE x volume x stop | P1 |
| EQ-03 | Reaction decomposition and entry timing | P1 |
| EQ-04 | Path exits / stops / trailing | P1 |
| EQ-05 | Bouddat asymmetric threshold and hold (long leg P1, short leg P2) | P1/P2 |
| EQ-06 | Seasonal SUE variants | P1 |
| EQ-23 | Steady Drift with reaction confirmation + entry-lag test | P1 |
| EQ-24 | Overnight-gap surprise (ORJ) | P1 |
| EQ-26 | Spread-based costs and entry bounce | P1 |
| EQ-28 | Era interaction and decay-aware exposure | P1 |
| EQ-07 | Peer spillover (lite) | P2 |
| EQ-25 | Attention proxies | P2 |
| EQ-27 | Next-session gap continuation (needs intraday engine) | P2 |
| EQ-29 | Walk-forward response curve | P2 |

### Options (`options/`)
| ID | Topic | Pri |
|---|---|---|
| OPT-01 | Shared option infrastructure (multi-leg, fills, spread report, event clock) | P0 |
| OPT-02 | Ask-aware post-reaction calls on top-decile SUE | P0 |
| OPT-03 | Deep-ITM call as stock substitute in `steady_drift_plus` | P1 |
| OPT-04 | Pre-earnings straddle gated by implied vs historical move | P1 |
| OPT-05 | Cash-secured risk reversal | P2 |
| OPT-06 | Index put-spread overlay on `steady_drift_plus` | P2 |

### Overlays and adjacent (`overlays_adjacent/`)
| ID | Topic | Pri |
|---|---|---|
| OV-01 | Concentration / leave-one-out validation | P0 |
| OV-02 | SUE x momentum paired factor | P1 |
| OV-03 | Market trend and earnings-breadth gate | P1 |
| OV-04 | Volatility-scaled position stop (close-to-close proxy; no ATR data) | P2 |
| OV-07 | Multifactor composite rank | P2 |
| OV-05 | Earnings-quality overlay (BLOCKED: needs a Compustat cash-flow pull) | P2 |
| OV-06 | Momentum sleeve with earnings-window avoidance | P2 |

## Key findings that shape everything (verified by the agents in the repo)

1. **Our own pipeline has gaps the library's audits warn about**: outcome columns (`hold_ret`, `exit_date`, ...) are visible to every `weights()` call; CRSP delisting returns are unused; headline Sharpe/CAGR include 316 flat warm-up days; the builders for `day0_date`, `ibes_clean` and size/sector features are not in the repo; there is no holdout and the leaderboard is a raw argmax (MT-01/02/03/04/05).
2. **Much of the library is already in the repo.** `sue_analyst` already is mcravi8's dispersion-scaled SUE; day-0 confirmation and size/sector-matched ranks were already tested in `meta/debate2` and were within noise (EQ-21/22). Expect few new winners over `steady_drift_plus`; "no improvement" is a valid, useful result.
3. **Options likely will not survive realistic fills** without evidence otherwise: current `options_cap20` is 0.28% (Sharpe 0.16), `options_worst_case` is negative; Kelly picks have a median day-0 relative spread of 11.9%. Each OPT spec has a go/no-go gate and the first deliverable is a spread report (OPT-01).
4. **Data limits:** data ends 2013 (no clean holdout; 2008-13 is the crisis/post-crisis, not independent); no ETF/VIX, no high/low (true ATR blocked), no intraday, no borrow data, no Compustat operating cash flow, 2011 options data missing. Blocked items are marked with the exact missing input in each spec (see MT-14).
5. **Library claims are not reproducible targets**: EA1904's 77-82% rests on a look-ahead-prone engine; FinLab's headline is a QQQ trend gate plus 3x ETFs, with no gate-only ablation; several "alpha" figures are raw mean returns.

## Rules for implementers

- Every change must keep the committed NAV pins (`tests/test_framework.py`; the four `steady_drift*` results; `options_cap20/30`) bit-identical unless the spec says a restatement is intended, in which case use a separate vintage first and re-pin once.
- No new strategy is promoted past `research` without passing the MT-04 gates; constants are chosen on the search window only.
- Respect `AGENTS.md` and `docs/REPO_MAP.md`: Python 3.12 / pandas 3, real directories only (no symlinks), raw data master on `D:`.
- Any download (risk-free series, FF factors, EDGAR, WRDS pulls) needs the owner's approval first.
