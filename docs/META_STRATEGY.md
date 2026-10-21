# Drift Core: a no-borrowing strategy built from what the PEAD strategies actually have going for them

Date: 2026-10-20. Branch `claude/strategy-evaluation-roi-74c7dd`. Code: `meta/` (run order below). Sub-agent evidence: `meta/debate/`.
Inputs are the results and docs of the sibling analysis worktree (`claude/strategy-analysis-backtest-481b86`), read-only.

## 1. The short answer

* **15% a year without borrowing is reachable only by carrying market risk.** The strategies that printed 12-15% did it with a 4-7x gross
  position (`market_boost_max` averages 4.1x, peaks 7.4x) plus a market overlay that supplies 41% of its profit. Scaled to a hard 1.0x ceiling the
  market-neutral books earn **2-4%**, not 6-7%: even the "unlevered" ones already run 1.0-1.8x gross on average (they short, and shorts need cash behind them).
* **What survives with no borrowing is the long side of the signal.** Buying only the best earnings surprises, holding 60 trading days,
  sizing by inverse volatility, and keeping everything else in the market index returned **10.8%/yr (2000-13, index 4.3%)** and **13.1%/yr
  (1997-13, index 7.0%)**, every position paid for in cash. About 6 points a year over the index, but it is market risk plus a
  decaying edge: max drawdown -53%, market beta 0.97, and the edge over the index fell from ~12%/yr (2000-03) to ~2% (2010-13).
* **Adding a 20% options satellite** (premium budget, never more) lifts it to **14.1% (2000-13) / 15.3% (1997-13)** on the corrected
  option marks, **11.8% / 13.2%** if dead options are worth zero. This is the only route to ~15% here, and it rests on the least
  trustworthy input in the repo (see section 4).
* **Planning number for live money: index return + 2-3 points**, not +6. The placebo test (section 5) says about half of the apparent edge is not
  earnings-surprise skill at all.

## 2. What each strategy has going for it and against it

| Strategy family | Genuinely working | Working against it | Verdict under "no borrowing" |
|---|---|---|---|
| Market-neutral equity books (`extreme_picks` ... `calm_rank_tilt`) | Signal is real (alpha t 4-8 in sample); `calm_*` books fix risk sizing; zero market exposure | Need 1.5-2.7x gross (shorts cost cash: 150% collateral); alpha per dollar fell ~4x from 1996-01 to 2008-13; short leg loses real dollars in every book; borrow of 3-5% erases most of 2008-13; capital-hungry | Scaled to 1.0x they earn 1.3%/yr per dollar of capital tied up. **Do not run unlevered**; ROI per dollar is the worst of all options |
| `market_boost`, `*_max`, `market_exposed` | Reach 10-15% | Return is leverage + index exposure, not skill; 6x caps breached (7.6x seen); -37% drawdowns | **Do not run** |
| Options (`options_cap20/30/40`) | Uncorrelated to equity (-0.05); wins in 2009 when equity lost; premium genuinely unlevered (avg 7.7% of NAV) | Original 8.6% was a quote-marking error (corrected 4.1%, worst case 2.3%); 62% of profit from 2003+2013; round-trip spread 25-30% of premium vs 3% assumed; 2011 missing | Small satellite only (<=20% of NAV), treat as unproven. **Do not run cap30/cap40** |
| `hedged_blend` | Best Sharpe (2.25), complementary eras | Combined gross ~2.4x; capped to 1.0x it earns 4.5% | Idea survives (equity + options), the sizing does not |
| High-beta shorts (any book) | nothing | no alpha (t~0), lose real dollars in all three eras in 8 of 9 books | **Never run** |
| Long thin low-beta, held >60 days | Best single recipe in all 9 books: 406-648 bp per $, t 4-7 | Only 2-3% of the money; capacity ~$1-3M a year; 2008-13 edge near zero in calm books | Keep, as the core of the long book, but it cannot carry a large account |

## 3. The new strategy: Drift Core

Every rule uses only information available on the trade date.

1. **Signal.** On each earnings announcement, rank the surprise (IBES SUE) against only the previous 365 days of earlier announcements.
   Act only on the top decile (percentile >= 0.9). No shorts, no middle, no lower deciles.
2. **Entry/exit.** Buy at the close of the announcement session (after-close reporters are already shifted to the next session); hold exactly
   60 trading days. No fitted holding windows.
3. **Size.** Weight by 1 / (stock's trailing idiosyncratic variance), capped at 2% of NAV per name and 5% of the stock's 21-day dollar
   volume. Sized each quarter from the NAV at the start of that quarter.
4. **No borrowing.** Cost basis of open positions <= 80% of NAV; the rest sits in the market index. Measured peak market value of holdings
   was 99.8% of NAV (never above 100%, so no margin loan was ever implied).
5. **Costs.** 5-25 bp per side by liquidity quintile at entry and at exit. Results hold at 2x and 3x those costs (10.4% and 10.0% vs 10.8%).
6. **Optional options satellite.** Up to 20% of NAV reserved for the existing Kelly option selection (`options_cap20`); stock sleeve is then 80%.

A model-based variant (ridge regression re-fit each quarter on only finished trades, buying events with predicted edge above costs) was built
first (`meta/simulate.py`, 9.8%, Sharpe 0.57). The one-line top-decile rule beat it (10.8%, Sharpe 0.60, alpha t 6.1 vs 4.1) and has fewer
moving parts, so the model was dropped. That is a choice made after seeing both full-sample results; treat the difference as noise and the
simple rule as the cleaner thing to trust.

### Results (long-only, unlevered)

| | 1997-13 | 2000-13 | 2008-13 |
|---|---|---|---|
| Market index (CRSP value-weighted) | 7.0% / DD -56% | 4.3% / DD -56% | 6.1% / DD -52% |
| **Drift Core**, events + index | **13.1%** / Sharpe 0.71 / DD -54% | **10.8%** / 0.60 / -54% | **9.7%** / 0.50 / -50% |
| Drift Core 80% + options 20% (corrected marks) | 15.3% / 0.94 / -53% | 14.1% / 0.85 / -53% | 13.1% / 0.71 / -49% |
| ... same, dead options = 0 | 13.2% / 0.83 | 11.8% / 0.74 | 11.5% / 0.64 |
| Drift Core, cash instead of index (beta 0.45) | 8.9% / 0.94 / -30% | 8.5% / 0.86 / -30% | 6.5% / 0.57 / -27% |
| Published `calm_rank_tilt` (levered ~1.5x gross) | 7.4% | 6.1% | 3.1% |
| Published `hedged_blend` (levered ~2.4x) | 12.6% | 12.5% | 9.9% |

Edge over the index (beta-adjusted, events only): 11.6%/yr in 2000-03, 3.7% in 2004-07, 3.4% in 2008-13, 2.2% (t 1.8) in 2010-13.
Even when limited to the largest stocks only (size quintile 5), alpha was 4.0%/yr (t 3.9), so it is not purely a small-cap artifact.
Year by year (events + index), the only losing years were 2002 (-10.7%) and 2008 (-34.9%), both with the index (-20.9%, -38.2%); 2001 and 2011 were flat.

## 4. Is the backtest method right? (auditor sub-agent, plus my checks)

Sound: entry timing (position starts earning at day0+1; day0's own reaction is not earned, after-close reporters shift a session); trailing
features (volume, beta, volatility use only data before day0); P&L compounding and quarterly resizing.

Not sound, and what I did about each:

| Problem in the published results | Size | In Drift Core |
|---|---|---|
| SUE decile ranked within the whole quarter (uses future announcers) | 26% of events change decile; D10-D1 spread -23% | Ranked vs trailing 365 days of earlier events |
| Holding days fit on the full sample | 370 bp spread pre-2008 vs 127 bp after | Fixed 60 sessions, nothing fitted |
| Parameters (tilt exponent, base unit) chosen after seeing full-sample results | ~26 variants tried | Only free choices: top decile, 60 days, 80% cap; sweeps are flat (SUE threshold 0.7-0.95: 10.0-10.9%) |
| Leverage cap ignored positions still open (7.6x seen vs 6x cap) | big | Cap on open cost basis, checked against market value |
| "Unlevered" books use 1.5-2.7x gross; no interest, borrow or margin cost | 3-5% borrow removes 60-100% of 2008-13 profit | Long-only, no shorts |
| Costs 5-25 bp, no impact | thin-stock round trip realistically higher | Re-run at 2x/3x; still 10.4%/10.0% |
| Delisted names | events with fewer than 60 sessions left are kept (0.14% of top-decile events); an extra -20%/-50% loss changes return by -0.1 to -0.2 pt | Kept in |
| Options still on mid fills / carry-forward; spreads 22-32% of premium; 2011 absent | the main reason 15% is not trustworthy | Satellite capped at 20% and shown with dead options = 0 |
| Full CRSP universe vs live S&P-style universe | top-450-by-cap events earn ~41 bp per $ vs 89 bp; ~15 bp since 2009 in the market-neutral books | Large-cap-only run earns 8.2% vs 10.8% (index 4.3%); still positive |

Backtest vs live mismatch to fix before real money: the live trader scores yfinance EPS surprises over the stock's own last 8 quarters;
this research uses IBES analyst SUE. They are different signals. The equity numbers above have never been validated on the live signal.

## 5. What the data say about why it earns, and the placebo

I shuffled each quarter's SUE ranks randomly across that quarter's events and re-ran the identical rule three times. It still "earned"
7.5-8.1%/yr and 3.2-3.7% alpha (t 3.1-3.8). So roughly **3.4 of the ~6.2 points of apparent alpha is the universe and sizing, not the surprise**: earnings announcers are
small/mid-cap and low-vol-weighted, a group that beat the cap-weighted index in 1997-2013. The surprise-specific part is ~2.8 points, and
that is also the part that has been decaying. This is the single most important caveat to the table above.

## 6. Where to improve (ranked)

1. **Rebuild the options data with bid/ask fills** (needs `forward_prices_optimal` re-run on the D: OptionMetrics data), then re-test the satellite. This decides whether 15% is real.
2. **Benchmark against a matched small-cap/low-vol index**, so the surprise edge is separated from the size premium; a small-cap index core might also be the better "fill" than the cap-weighted index.
3. **Backtest on the live signal and universe** (S&P-style, yfinance-style surprise) with a market-impact term.
4. A **trend/valuation gate on the index fill** would attack the -53% drawdown, but is market timing and was not tested.
5. Extend past 2013 (the data end there); the 2010-13 edge is thin, and recent years are what matter.

## 7. Reproduce

```
python meta/build_events.py   # causal event table, fixed-60d paths (reads sibling worktree data, read-only; ~30s)
python meta/run_meta.py       # benchmarks + ridge-model variant
python meta/ablate.py         # controls, halves, coefficients;   meta/ablate2.py thresholds/costs;  meta/ablate3.py universe/delisting/sub-periods
python meta/final.py          # the results table (meta/out/final_table.csv)
python meta/placebo.py        # shuffled-SUE control
python meta/combine.py        # sleeve correlations; walk-forward sleeve allocator (do not trust its options weights, see below)
```
`meta/combine.py` also contains a walk-forward sleeve allocator. It pushed the options sleeve to 33-54% of capital, beyond the 20% cap the
options results were ever tested at, so it is reported but not adopted. It also shows the unlevered long/short sleeve earns only 1.3% per
dollar of capital (Sharpe 1.1) against 10.8% for the long-event book, which is why the final strategy has no short sleeve.
Debate sub-agent scripts and outputs: `meta/debate/` (advocate, skeptic, audit).

> **Update (same day, after merging the consolidation):** the options satellite in this document is superseded. With real bid/ask fills `options_cap20` earns 0.28%/yr, so the 14-15% figures above do not stand. See `docs/STEADY_DRIFT.md` for the post-fix design (cash fill, sector cap, volatility target) and its placebo-adjusted numbers.
