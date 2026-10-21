# Steady Drift: the post-fix, no-borrowing, risk-targeted PEAD book

Date: 2026-10-20. Follows `docs/META_STRATEGY.md` (Drift Core) after merging the consolidation (`docs/FIXES.md`). Code: `meta/steady.py`,
`meta/steady_validate.py`, `meta/steady_universe.py`, engine `meta/simulate.py`. Perspective reports and scripts: `meta/debate2/` (prefixes A_ B_ C_ D_).
Rules throughout: no borrowed money (holdings never exceeded NAV; measured peak 84%), no look-ahead, long-only.

## 1. The landscape after the fixes

* Equity market-neutral books lost 1-4 points a year (`balanced_leverage` 6.5% -> 5.4%, `calm_rank_tilt` 7.1% -> 6.0%); `_max` books now breach their own drawdown screens.
* Options lost essentially all edge at real bid/ask (`options_cap20` 0.28%/yr, t = 0.18 on premium); `hedged_blend` 11.3% -> 4.9%.
* With no borrowing, every market-neutral book shrinks to 1-3%: they tie up (long + 1.5 x short) = 1.2x to 5.6x NAV (calm_weighted averages 2.2x), pay borrow on small-cap shorts, and the T-bill alone pays 2.7%. After realistic costs and graded borrow fees the 22 books return 1.1-3.1%.

## 2. Where the returns come from (four independent perspectives)

| Perspective | Main finding | Evidence |
|---|---|---|
| Return sources (A) | The surprise signal is real: cell-matched D9-10 minus D1-2 spread 364 bp (1996-01) -> 249 -> 142 bp (2008-13); shuffling SUE gives -1 bp. But it fades (balanced_leverage surprise-specific return 11.0 -> 8.6 -> 3.4 pts, t 1.9 in 2008-13). Long leg: 9.3 pts net, of which 5.2 is the event-universe premium; short leg nets -4.4 pts (its surprise return +2.5 is outweighed by shorting the universe). Calm weighting adds no return (-0.3 to +0.2 pts), it is a risk tool. | `A_decomp_by_book_era.csv`, `A_side_components.csv`, `A_event_decile_spread_by_era.csv` |
| Consistency (B) | No book is consistent across eras: 3-year rolling Sharpe slopes -0.06 to -0.15 per year. Best predictors of consistency: sector neutrality, inverse-vol sizing, low gross leverage; number of positions is irrelevant. Drawdown depth is mostly leverage. Drift Core earns the most but is the index with an edge (corr 0.98, DD -53%). | `B_scorecard.csv`, `B_design_corr.csv` |
| Economics (C) | Long-only is the only structure that survives no-borrow + costs. Capacity halves near $26-34M (market-neutral) and the best thin recipe supports only ~$2M. Options: drop (median entry spread 27%). Live trader fills options at mid and trades shorts that earn nothing. Top-500 universe alpha is 0.5% in 2009-13 vs 4.3% full CRSP. | `C_roi_summary.csv`, `C_capacity_strategy.csv`, `C_options_*.csv`, `C_livefit_signal.csv` |
| Improvements (D) | 35 variants (pool length, thresholds, hold length, floors, sector/size matching, day-0 confirmation) none beat the top-decile rule by more than noise; equal-weight sizing is worse. What changed results were exposure choices (cash vs index fill, trend gate), not skill. Passive equal-weight index returned 16.4%/yr over 2000-13 vs 4.3% cap-weighted: most "alpha vs cap-weighted index" is the small-cap premium. | `D_results_A.csv`, `D_results_B.csv` |

What a good strategy therefore needs: (1) only the leg that is paid (long, top surprise), (2) no shorts and no leverage, (3) risk sized by volatility and spread across sectors, (4) risk that is held constant through time (the thing no strategy had), (5) no options until bid/ask fills are fixed, (6) honest accounting of the universe premium.

## 3. The strategy

1. **Signal.** Top decile of IBES SUE, ranked against only the previous 365 days of earlier announcements.
2. **Universe.** 21-day dollar volume >= $2M (full CRSP-type universe otherwise).
3. **Entry/hold.** Buy at the close of the announcement session (after-close reporters shifted a session); hold 60 sessions.
4. **Size.** Inverse idiosyncratic variance; max 2% of NAV per name; max 5% of ADV; no FF12 sector above 20% of a quarter's budget; stock cost basis <= 70% of NAV.
5. **Idle money.** Cash earning the T-bill (annual averages typed from memory, +-5 bp: an assumption).
6. **Risk target.** Exposure e = min(1, 10% / trailing-60-day volatility of the book), from data through the previous day; the rest in T-bills; 10 bp cost on each change. Never above 1, so never levered.

Choices that were NOT tuned: top decile, 60 days, $2M floor and 20% sector cap are round numbers fixed before the runs; sweeps of each were flat (`D_results_A.csv`, `meta/ablate2.py`). Variants tried in this step: 12 (4 base books x vol targets 8/10/12/15%, plus index/T-bill references). The 10% target is a risk preference taken from a smooth frontier, not a maximum.

## 4. Results (1997-07 to 2013-12, long-only, no borrowing)

| | Return | Vol | Sharpe | Max DD | Beta | Alpha vs index (t) |
|---|---|---|---|---|---|---|
| Market index (cap-weighted) | 7.0% | 20.6% | 0.43 | -55.5% | 1.00 | |
| Drift Core (index fill, `META_STRATEGY.md`) | 13.1% | 20.1% | 0.71 | -53.6% | 0.96 | 5.8 (6.0) |
| Cash-filled, no risk target | 11.7% | 12.7% | 0.94 | -36.2% | 0.58 | 6.7 (6.5) |
| **Steady Drift (10% risk target)** | **10.8%** | 9.3% | **1.15** | **-20.5%** | 0.40 | 7.2 (6.7) |
| Steady Drift, 8% target (conservative) | 9.8% | 8.1% | 1.19 | -16.2% | 0.34 | 6.7 (6.6) |
| Best published post-fix book, `calm_rank_tilt` (levered ~1.5-2.7x gross) | 6.0% | 4.0% | 1.47 | -13.6% | 0 | 6.5 |
| ... same scaled to no-borrow (C) | ~2-3% | | | | | |

(Sharpe uses zero risk-free for every row, as the repo does; the T-bill averaged 2.4% over this window.)

By period: 1997-2001 13.4% / Sharpe 1.37 / DD -11.9%; 2002-07 12.8% / 1.45 / -12.2%; 2008-13 7.0% / 0.74 / -18.1% (index 6.1%, DD -52%); 2010-13 10.7% (index 15.2%, it lags strong bull markets by design).
Calendar years: 78% positive, 75% of quarters positive, worst quarter -9.8%, worst 12 months -16.6%; 2008 was -13.8% vs index -38.2%; its only down years were 2002 (-0.1%), 2008, and 2011 (-1.8%).
Costs: 2x costs 10.5% (Sharpe 1.12), 3x costs 10.1% (1.09).
Universe: announcers in the two largest size quintiles 10.1% / 1.10; the largest quintile alone 9.1% / 1.03; the three smaller quintiles 10.8% / 1.15 (2008-13: 5.8%). Not a micro-cap artifact.

### The placebo (read this before believing the table)

Shuffling SUE ranks within each quarter and re-running the identical machine gives 6.4-6.8% / Sharpe 0.73-0.77 / alpha ~3.3% (t ~3.2). So about **3.5-4 points of the 7.2-point alpha is surprise skill; the rest is the event universe and low-vol weighting** (small and mid-cap announcers that beat the cap-weighted index in this sample). The skill part is also the part that fades: alpha vs index was 9.4% in 1997-01, 7.9% in 2002-07, 4.1% (t 2.1) in 2008-13 and 2.9% (t 1.7) in 2010-13. **Planning number going forward: about 0.4 x the market's return, plus the T-bill on the rest, plus 2-3 points of alpha. In a 7% market that is roughly 6-7% a year with about a third of the market's drawdown, not 10.8%.**

## 5. Is it "the best, most profitable, most consistent so far"?

* Best of the strategies that obey the rules (no borrowing, no look-ahead, no shorts that need unmodelled borrow): yes on every measure I computed. Every market-neutral book collapses to 1-3% under the same rules; Drift Core earns more but with the index's -54% drawdown.
* Not the highest raw return anywhere in the repo (Drift Core 13.1%; unconstrained levered books 8-12% after the fixes with -45% drawdowns).
* "Consistent" is relative: it has the lowest drawdown of any positive-alpha book that earns above 9%, but its edge still decays and 2008-13 is the honest guide to the future.

## 6. Limits that remain

* Design chosen after seeing 1996-2013; 2008-13 is the nearest thing to a holdout and it shows lower returns. No data after 2013.
* T-bill path typed from memory; costs and borrow assumptions are assumptions (`meta/debate2/C_common.py`).
* The benchmark is the cap-weighted index; the passive equal-weight index returned far more (it holds micro-caps and rebalances free). An investable small-cap benchmark is missing, so "alpha vs index" overstates skill; the placebo is the better yardstick.
* Not validated on the live signal (yfinance EPS surprise) or an S&P universe: top-500 events earned ~0.5%/yr alpha in 2009-13 in the full-CRSP analysis (C). Realistic accounts: $1M+ (a $100k account holds ~260 positions of ~$300).
* Size/liquidity quintile breakpoints are cross-sectional within the announcement quarter (small look-ahead flagged by the audit).
* Options excluded; the live paper engine still fills options at mid and trades shorts: do not use it for this strategy as is.
* Not run through the `pead` framework engine (it has no cash-fill or risk-target support); a port would be the next step.

## 7. Reproduce

```
python meta/build_events.py && python meta/steady.py          # prototype frontier (meta/out/steady_frontier.csv); the framework versions are `python -m pead run steady_drift*`
python meta/steady_validate.py                                # eras, costs, placebo, years
python meta/steady_universe.py                                # size-group robustness
```

---

# Part 2: the strategy inside the `pead` framework, and how to get more return without paying for it in Sharpe or alpha

## 8. What was ported (and what changed in the engine)

Steady Drift is now an ordinary framework strategy: `pead/strategies/equity/steady_drift.py` (four registered variants), run with
`python -m pead run steady_drift` like any other, with the usual per-strategy report, leaderboard row and tests. It needed five
small, general additions to the shared machinery (none changes an existing strategy's numbers; the old tests still pass):

| Addition | Where | What it does |
|---|---|---|
| Fixed holding window | `with_fixed_hold`, `EquityStrategy.hold_sessions` | Holds exactly N sessions, cut at the stock's last row so delistings stay in. Needs the panel's permno array, now stored in the panel cache (`EquityUniverse.panel_permno`; old caches rebuild once). |
| Per-name cap | `Sizing.name_cap_frac` | No position above a fraction of NAV. |
| First-come-first-served cap | `cap_mode="chronological"` | When the cash/gross budget binds, positions are admitted in announcement order. The older "priority" mode keeps the biggest bets of the quarter, which can only be known once the quarter is over. |
| Causal weights | `weights.causal_inverse_variance` | Inverse-variance weight relative to the median of the previous 365 days' events; nothing depends on later announcements. |
| Cash yield + risk throttle | `apply_cash_and_risk_target`, `pead/core/rates.py` | Idle NAV earns the T-bill rate; exposure = min(1, target vol / trailing 60-day vol of the book, data through t-1); never above 100%. The T-bill series is an assumption (typed from memory). |

`legacy_twin = False` on the new strategies (they have no pre-fix numbers to reproduce, so `*_legacy` twins are not generated).
Tests: `tests/test_steady_drift.py` (a fixed hold never crosses into the next stock; causal weights do not change when the future is rewritten; the
throttle's exposure through day t does not change when later returns change; cash yield; per-name cap; chronological vs priority). Full suite: 29 passed, 14 skipped (they need data).

Two honest corrections came out of the port:
1. The `meta/` prototype normalised each quarter's weights by the whole quarter's total and admitted the largest bets first. Both use events that
   had not happened yet. The framework version removes both; the effect is small (10.81% -> 10.57%, Sharpe 1.15 -> 1.16 over the full window).
2. The sector cap was dropped. It made no difference in the prototype (13.15% vs 12.93%) and a causal version needs sector-aware admission in the engine (roadmap item 8).

Reconciliation, framework vs prototype (same signal, 1995-2013): `steady_drift` 10.59% / vol 9.0% / Sharpe 1.16 / max DD -20.0% against 10.81% / 9.3% / 1.15 / -20.5%;
`steady_drift_core` (no throttle) 11.99% / 13.1% / 0.93 / -40.5% against 11.70% / 12.7% / 0.94 / -36.2%.
To reproduce: with the complete corrected `data/` of `docs/REPO_MAP.md` section 6 in place, `python -m pead run steady_drift steady_drift_calm steady_drift_core steady_drift_plus`. (The figures in this document were first produced with `PEAD_DATA_DIR` pointing at a copy of the corrected `equity_vol_panel.parquet` and `PEAD_RAW_EQUITY_DIR` at the raw `normalized_equity` folder, the same files.)

| Registered | What differs | Return | Vol | Sharpe | Max DD |
|---|---|---|---|---|---|
| `steady_drift` | the base design (10% target) | 10.6% | 9.0% | 1.16 | -20.0% |
| `steady_drift_calm` | 8% target | 9.6% | 7.8% | 1.21 | -15.9% |
| `steady_drift_core` | no throttle | 12.0% | 13.1% | 0.93 | -40.5% |
| `steady_drift_plus` | breadth + 80% cap + 12% target (section 9) | 12.1% | 10.0% | 1.19 | -22.1% |

(Framework summaries, full window 1995-2013, which includes the flat pre-trading months; section 9 measures from 1997-07.)

## 9. Getting more return without giving up Sharpe or alpha

Rule fixed before looking at the second grid: accept a change only if Sharpe >= baseline, alpha t >= 5.5, 2008-13 Sharpe >= 0.75 and the marked value of the holdings stays under 95% of NAV (the no-borrowing check, computed from the actual positions rather than cost basis). 31 variants were run (`meta/steady_grid.py`, `meta/steady_grid2.py`; tables in `meta/out/steady_grid*.csv`). Baseline from 1997-07: 10.6% / Sharpe 1.09 / DD -20.0% / alpha 6.9% (t 5.9) / 2008-13 Sharpe 0.75.

| Lever | Result | Verdict |
|---|---|---|
| Smaller positions, more names (base size 0.4% -> 0.3% -> 0.25% of NAV, name cap 1%) | positions 8.5k -> 12-14k; share hitting the 5%-of-volume cap 56% -> 20-40%; Sharpe 1.09 -> 1.17-1.20; 2000-06 Sharpe 1.20 -> 1.36-1.43; 2007-13 Sharpe 0.77 -> 0.78-0.81 | **Adopt.** The only change that raised Sharpe. Almost all of it is in the early period; 2007-13 barely moves. |
| Raise the invested cap 70% -> 80% | +0.4 to +0.6 pt return, Sharpe flat | Adopt, 80% at most. 85% and 95% **fail the no-borrow check** in 7 of 8 configurations (marked holdings reach 1.01-1.66x NAV after drawdowns; the one pass is 0.92), so they are rejected despite 11.5-13.2% returns. |
| Risk target 10% -> 12% | +0.7 pt return, -0.03 to -0.06 Sharpe, deeper drawdown (-19% -> -22%) | Adopt as `steady_drift_plus`: Sharpe is still >= baseline once combined with breadth. The 10% sibling (`bu0.003_cap0.8_vol0.1` in the grid) gets 11.7% / Sharpe 1.19 / DD -19.1%. |
| Top two deciles instead of top decile | return -1.5 to -2 pts, Sharpe -0.2 | Reject: dilutes the signal and hits the volume cap more. |
| Hold 40 or 90 sessions | 8.7% / 7.9% | Reject: 60 stays. |
| Dollar-volume floor $0.5M / $5M | 10.9% / 10.3% | No effect; $2M stays. |

`steady_drift_plus` (base size 0.3%, cap 80%, name cap 1%, target 12%): 12.4% / Sharpe 1.14 / DD -22.1% / alpha 8.2% (t 6.4); 2008-13 return 9.0%, Sharpe 0.79; marked holdings <= 75% of NAV.

**The honest part.** I re-ran the shuffled-surprise placebo (SUE ranks permuted within each quarter, everything else identical, 3 seeds, `meta/steady_placebo.py`) on both:
`steady_drift` real 10.6% vs placebo 5.6-7.3% (surprise skill = +4.3 pts return, +3.9 pts alpha, +0.40 Sharpe); `steady_drift_plus` real 12.4% vs placebo 7.3-8.8%
(skill = +4.3 pts, +3.9 pts alpha, +0.36 Sharpe). So the extra return of `plus` is more exposure to the event universe (small/mid-cap, low-vol), **not more surprise skill**: its skill-adjusted alpha is identical. Taking a higher risk target at an unchanged Sharpe is a legitimate way to earn more, but the new return is as exposed to a regime change in that universe as the old.
Nothing in 31 variants raised the surprise-specific part, consistent with the earlier 35-variant search.

## 10. What it would take to raise the skill itself (not tried, ranked)

1. **New information, not new tuning.** Analyst revisions, forecast dispersion and the earnings-day return are the usual add-ons to SUE; none are in the event table (IBES detail would have to be pulled from WRDS). Only a signal that carries different information can lift the 3.9-point skill alpha.
2. **A decay-aware throttle.** Skill alpha fell 9.4 -> 7.9 -> 4.1 points across the three eras. A strictly-past rolling estimate of the current D10 spread could scale the whole book: more exposure when the trailing spread is healthy, less when it fades. Needs its own placebo.
3. **Real T-bill series** instead of the typed-from-memory path.
4. **Out-of-sample year or the live signal.** Everything here is 1997-2013 and in-sample for the design; the live trader's yfinance surprise signal has not been run through this engine.
5. **Capacity.** 56% of the base design's positions hit the 5% volume cap at $10M; breadth cuts that to ~39%. The return-vs-NAV curve for the new strategies has not been re-measured.

## 11. Open item: investable small-cap benchmark (TO BUILD LATER)

Not built now, by agreement. Why it matters: Steady Drift's "alpha vs the cap-weighted index" mixes surprise skill with the small/mid-cap premium this universe earned in 1997-2013. The shuffled-surprise placebo is a proxy. A proper benchmark would be a transparent, investable stand-in built from the same data: the same announcing stocks (or CRSP small-cap deciles) with the same dollar-volume floor, value-weighted, rebalanced monthly, with the same cost model. Run `steady_drift*` against it and report alpha versus that. Tracked as item 1 in `docs/ROADMAP.md`.
