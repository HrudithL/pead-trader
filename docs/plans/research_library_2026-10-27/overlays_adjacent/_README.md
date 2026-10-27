# Overlays and adjacent ideas for the long-only, no-borrowing SUE book (spec index)

Date: 2026-10-27. Scope: documentation only; no code under `pead/` or `tests/` was edited. Source library (read-only): `C:\Users\hrudi\Downloads\PEAD_Research_Library_Expanded_2026-10-27\PEAD_Research_Library_2026\`. Baseline to beat: `steady_drift_plus` (results/leaderboard.md: 12.07% / vol 9.97% / Sharpe 1.19 / max DD -22.07%; STEADY_DRIFT: placebo skill about +4.3 pts return, so about half the excess return is the event universe).

## Data reality check (read first)

- Repo data: CRSP / IBES / Compustat stocks, 1996-2013 (panel 1995-2014), plus OptionMetrics. Daily panel columns: `permno, date, ticker, prc, ret, retx, shrout, vol, cfacpr, cfacshr`; market columns: `vwretd, sprtrn, vwretx, ewretd, ewretx`. There is NO daily high/low (true ATR is BLOCKED; close-to-close proxy only), NO ETF data (QQQ, TQQQ, TECL, IEF, GLD, SHY do not exist here), NO VIX, and Compustat `fundq` has no operating cash flow (`oancfy` missing, so cash-flow quality / accruals are BLOCKED).
- Almost every library "winner" (FinLab 32.0% CAGR, 51.9% reversal, 41.0% rotation) is a 2016-2026 leveraged-ETF regime allocator whose headline is the QQQ trend + 3x ETF engine, not earnings skill. None of those numbers transfers. Library evidence is author-reported, in-sample or on hand-picked survivors; it supplies hypotheses and protocols, not effect sizes.
- There is no clean holdout in this repo (design chosen after seeing 1996-2013; 2008-13 is only the nearest thing). Every spec therefore requires: pre-registered variant lists, a random/placebo control, and the OV-01 stress test.

## Specs, ranked

| Rank | ID | Idea | Priority | Effort | Why this rank |
|---|---|---|---|---|---|
| 1 | OV-01 | Concentration / leave-one-out stress as a standard validation | P0 | M | Zero research risk; gates every other item; endorsed by three library documents |
| 2 | OV-02 | SUE x 12-1 momentum paired factor (V0 diagnostic, filter, tilt, controls) | P1 | M | Cheap first diagnostic on existing columns; directly tests whether SUE is partly momentum |
| 3 | OV-03 | CRSP-index trend gate and earnings-breadth gate with gate-only ablation | P1 | M | Targets the book's real weakness (drawdown) but two-episode evidence; breadth probably redundant |
| 4 | OV-07 | SUE-anchored composite + walk-forward factor-IC harness | P2 | M | The IC harness is reusable; the composite only after OV-02/OV-05 pass |
| 5 | OV-04 | Vol-scaled close-triggered position stop (no ATR; proxy) | P2 | M | Source's own sweep shows stops hurt; build to close the question |
| 6 | OV-05 | Earnings-quality overlay (ROA proxy now; cash-flow BLOCKED) | P2 | L | Needs a WRDS pull; source t = 1.13; hypothesis only |
| 7 | OV-06 | Momentum sleeve for idle cash with earnings-window avoidance | P2 | L | Needs shared-cash ledger and full-panel eligibility; no consumer of the avoidance rule in current repo |

Suggested implementation order: OV-01, then OV-02 V0 and OV-03 diagnostics in parallel, then the rest only on a "go" from the diagnostics. See `_ENABLING_CHANGES.md` for the refactors.

## Already covered in the repo (do NOT duplicate)

Volatility targeting: `apply_cash_and_risk_target` (`pead/engines/equity.py`, 60-day lookback, `min(1, target/vol)`, 10 bp turnover cost) and the registered 8/10/12% targets. Calm tilt / low-vol weighting: `calm_rank_tilt`, `calm_weighted`, `strategies/vol.py::low_vol_rank_scale`. Idle cash on T-bills: `pead/core/rates.py` (typed from memory; ROADMAP item 2). Inverse-variance sizing with name/ADV caps: `weights.causal_inverse_variance`, `Sizing.name_cap_frac`, `liquidity_cap_frac`. Decay-aware throttle is ROADMAP item 6 (not in this folder). Sector-aware admission is ROADMAP item 8.

## Rejected or parked (with reasons)

| Idea | Source | Reason |
|---|---|---|
| FinLab ETF regime allocator (TQQQ/TECL/IEF/GLD/SHY, 8% touched stop, monthly) | strategies 02, 16, 22 | Needs ETF data and 3x leverage (violates no-borrowing); headline is QQQ trend + leverage; library's own missing ablation (gate-only) was never run. Only the gate logic survives, as OV-03 |
| Short-term ETF reversal (51.9% CAGR), 3x ETF rotation (41.0%), 2x rotation, low-vol regime, breakout | strategy 16 | ETF-only, author t about 1.25 for reversal, leveraged, no stock-level content |
| Momentum / quality / FCF "breadth" ETF systems as return sources | 16, 18, 22 | Same ETF engine; momentum breadth threshold never bound |
| ATR-based position sizing (2.5 x ATR risk, 1-2% tiers) | strategy 14 | Duplicate of inverse idiosyncratic variance sizing; ATR needs OHLC (absent); source's sizing/stop mismatch |
| ATR-expansion and ATR-normalised-momentum entry filters | strategy 14 | Needs OHLC; no PEAD evidence; author's 0.95 threshold not the best tested value; gold/FX/index results |
| +4 ATR profit target, 10-bar max hold | strategy 14 | Repo found 40 and 90 session holds worse than 60 (8.7%/7.9%); target caps drift |
| Volatility-target with 2x leverage (6% monthly) | strategy 15 | Borrowing; units confusion (6% monthly is about 20.8% annual); existing throttle never exceeds 1 |
| Long/short 12-1 momentum | strategy 15 | Shorts need borrow (unmodelled); source's own long/short table is Sharpe 0.28, DD -66.7% |
| VIX panic entries | strategy 21 | No VIX data; about 16 events over 11 years |
| Crypto-linked momentum | strategy 21 | Irrelevant to stock PEAD |
| RSI mean-reversion signal itself | strategy 21 | Not PEAD; only the earnings-avoidance rule is kept (OV-06) |
| Hand-weighted multifactor score (15/35/20/15/10/5) | strategy 19 | In-sample, fixed 100 names; only the IC pipeline is kept (OV-07) |
| Stand-alone price-high "value" proxy | strategy 20 | Not value; correlated with momentum |
| FCF and value+quality as separate stock books | strategies 16, 18 | Evidence is the ETF engine or t = 1.13 |
| Graph / network models | EA1904 (outside scope) | Out of scope for this folder |

## Cross-cutting rules every spec follows

1. No new idea is accepted without: pre-registered variant list, random-drop / matched-exposure / shuffled-SUE controls, OV-01 stress, three-era table (1996-2001, 2002-07, 2008-13), and the repo's own acceptance rule (Sharpe >= baseline, alpha t >= 5.5, 2008-13 Sharpe >= 0.75, marked holdings < 95% NAV).
2. All features are causal: only rows with index <= `entry_row_idx` (day0 close) and events with strictly earlier `day0_date`.
3. Report the number of variants tried; the repo has already run 66 variants (35 + 31) in STEADY_DRIFT.
4. Live engine parity is explicitly NOT claimed: all new strategies stay `live_ready = False`.
