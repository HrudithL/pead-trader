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
python meta/build_events.py && python meta/steady.py          # frontier (meta/out/steady_frontier.csv)
python meta/steady_validate.py                                # eras, costs, placebo, years
python meta/steady_universe.py                                # size-group robustness
```
