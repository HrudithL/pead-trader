# Where each strategy's return comes from

Generated tables: `results/situations_all.csv` (every situation, every strategy), `situations_contrasts.csv` (is situation A really better than B?),
`situations_drivers.csv` (what matters once everything is held fixed at once). Code: `pead/reporting/situations.py`; rebuild with
`python scripts/situations_report.py && python scripts/situations_facts.py && python scripts/build_situations_doc.py`. Sample 1996-2013, 71 announcement quarters.
Every number in the prose of this document is filled in from those CSVs (and `results/situations_facts.json`, `results/profiles_benchmark_compare.csv`) by `scripts/build_situations_doc.py`; the text itself lives in `scripts/situations_intro.md`.

## How to read the tables

Every position the strategy opened is labelled with the situation it was in: what kind of surprise it was, how big the company is, how easily it trades,
how jumpy it is, how long it was held, what the market did meanwhile, and so on. For each situation:

* **Share of money traded** -- how often the strategy is in this situation (by dollars, so a big bet counts more than a small one).
* **Share of alpha** -- how much of the strategy's earnings come from it. Compare with the share of money: a situation with 10% of the money and 40% of the alpha is doing four times its share.
* **Alpha per $ traded (bp)** -- the return the earnings signal produced per dollar put to work, after trading costs, in hundredths of a percent. "Alpha" here is the return
  beyond the average company that announced that quarter, i.e. the part the surprise ranking is supposed to predict.
* **Sure? (t)** -- how confident we can be that the real figure is above zero, computed across the 71 quarters (positions in the same quarter share the same market weather, so they are not counted as separate
  evidence). Roughly: 2 means "probably real", 3+ means "very likely real", under 2 means "could be luck".
* **Verdict** -- ENGINE (t >= 2.5, positive in at least 2 of 3 time periods, and earns more per dollar than the strategy's average), WORKS (t >= 2, positive in at least 2 of 3 periods),
  UNPROVEN (could be luck), LEAKS (reliably loses money). About 60 situations are tested per strategy, so a few will look good or bad by pure chance; the three-period rule is the guard against that.

## What every equity strategy has in common (numbers are Balanced Leverage; the other strategies match in direction unless a card says otherwise)

These figures come from the corrected data and use the average-announcer benchmark; see "Benchmark sensitivity" at the end for what changes under a size-matched benchmark.

| Finding | The numbers | Confidence |
|---|---|---|
| **The money is in the less-traded names, and it is trading volume more than size.** | The most heavily traded companies (liquidity groups 4-5) are 49% of the money but 28% of the alpha. The thinnest-trading group is 7% of the money and 15% of the alpha (133 bp per $ against 24 bp for the most traded). By company size the pattern is NOT steady: the smallest companies are 9% of the money and 4% of the alpha (28 bp), the second-smallest group is 19% of the money and 38% of the alpha (125 bp), and the largest group earns 29 bp. | Thin vs heavily traded differs by 129 bp per $ (t = 2.9); small vs large by only 22 bp (t = 0.3, not distinguishable from zero). Company size and trading volume rank almost the same way (correlation 0.83), so the two are hard to separate. |
| **Long holds do the work.** | Holds over 90 days are 8% of the money and 23% of the alpha (185 bp per $); holds up to 40 days are 34% of the money and 32% of the alpha (59 bp). | Real in the data (t = 2.2), **but partly flattered**: since the fixes each cell's hold length is estimated on the first 70% of the quarters (see FIXES.md), which removes part of the look-ahead, but the back-off thresholds between cells still see the full sample. |
| **Low-beta stocks pay, high-beta stocks do not.** | Low-beta stocks: 32% of the money, 62% of the alpha (121 bp). High-beta stocks: 33% of the money, 3% of the alpha (5 bp, t = -0.4, not distinguishable from zero). By period (1996-01 / 2002-07 / 2008-13): low beta 183 / 125 / 33 bp, high beta -90 / 30 / 9 bp. | With everything else held fixed, one standard deviation more beta costs about 47 bp per $ (t = -2.8). |
| **Big beats are the best single situation; bad news on the short side is spread out.** | Long D10 (biggest beats): 20% of the money, 35% of the alpha (109 bp, t = 4.3). Long D09 earns 72 bp. Shorts D01-D03 earn 56, 57 and 48 bp. The weakest long groups, D07 and D08, take 14% of the money and earn 4% of the alpha (D07: -17 bp, t = -0.8). | Strong for D10. D10 beats the biggest misses (short D01) by 76 bp per $ but only at t = 1.7. Long D07/D08 are not distinguishable from zero. |
| **Earnings season matters.** | Companies reporting in Apr-Jun and Jul-Sep: 49% of the money, 78% of the alpha (119 and 84 bp). Oct-Dec reports: 26% of the money, 17% of the alpha (40 bp). Jan-Mar reports: 25% of the money, 5% of the alpha (12 bp), but much of that is one quarter: 2009Q1 alone lost $2.84M; without 2009 the Jan-Mar figure is 55 bp. | Oct-Dec is positive in all three periods (71 / 53 / 14 bp); Jan-Mar is negative in 2008-13 (89 / 99 / -82 bp). But the t-stats of the two weak seasons (0.8 and 1.6) do not prove them worse than Apr-Sep. |
| **The edge has faded.** | 1996-2001: 120 bp per $; 2002-2007: 82 bp; 2008-2013: 19 bp. The latest period carries 44% of the money and only 13% of the alpha. | Real (early vs late differ by 104 bp, t = 2.6); the latest period on its own has t = 0.9. |
| **The market's direction does not clearly matter.** | Pooled by dollars, alpha per $ is 23 bp when the market rose over 5% during the hold and 97 bp when it fell over 5%. Quarter by quarter the difference (rose minus fell) is -34 bp with t = -0.17. Quarterly alpha per $ has correlation -0.11 with the market's return over the holding windows. | This is the market-neutral design working: no statistically distinguishable dependence. |
| **Steady, except for a few bad quarters.** | 79% of quarters make money. The best 10 of 71 quarters supply 56% of the alpha; the worst quarter (2009Q1) lost $2.84M, more than the next five worst combined ($2.53M). | Descriptive. (Dollar figures here use the average-announcer alpha; `docs/STRATEGY_PROFILES.md` quotes the size-matched figure for the same quarter.) |

## Why do these situations exist?

What the data supports, and what it only suggests:

* **Hard-to-trade names: a plausible cause, not tested here.** The usual explanation is that thinly traded companies have fewer analysts and fewer professional investors watching, and trading costs deter the people who would otherwise
  correct the mispricing, so the slow reaction to earnings lasts longer. The data matches this for trading volume (alpha per dollar falls steadily from the thinnest to the most heavily traded group: 133 / 81 / 80 / 47 / 24 bp) but not for company size alone (by size group: 28 / 125 / 74 / 49 / 29 bp), and it cannot test the cause.
* **Long holds: partly a measurement artifact.** In the cells where holds are long, the drift keeps building for months. But the hold length of each cell is still estimated from history that overlaps the test period; the fixes moved the estimate to the first 70% of quarters, which helps but does not remove the problem.
* **Calm stocks vs jumpy stocks: the data does not cleanly say.** Calm stocks earn 83 bp per $ against 29 bp for jumpy ones when pooled by dollars (95 vs 30 bp quarter by quarter, t = 2.1, borderline). But once beta, size, liquidity and hold length are held fixed, jumpiness has no independent effect (+4 bp, t = 0.2).
  The calm-stock advantage may really be a beta advantage: 56% of the jumpiest third of stocks are high-beta, against 13% of the calmest third (correlation 0.47). `docs/STRATEGY_PROFILES.md` splits Balanced Leverage by both labels at once (`results/profiles_beta_vol_grid.csv`) and finds the same thing. This remains the most important open question for the Calm strategies.
* **High beta being worse: unexplained.** Nothing in the data says why. One candidate is that market-adjusted returns assume a beta of 1, so a high-beta stock's relative return is full of market noise that has nothing to do with the earnings news.
* **The seasons and the post-2008 fade: unexplained.** The fade fits the idea that more investors now trade this drift away, but the data does not test that.

## How to tell whether a strategy is good on one topic and bad on another

1. **Frequency and weight:** how much money is in the situation, and how often does it occur (positions per year). A great situation that is 1% of the money cannot move the total.
2. **Return per dollar and share of alpha:** compare the share of alpha with the share of money.
3. **Sureness:** the t-stat across quarters, not across positions. A situation that made money in most quarters is more believable than one carried by three huge quarters.
4. **Consistency:** the same sign in all three time periods. A situation that only worked in 1996-2001 is probably decayed.
5. **Is it really that situation?** Many situations are the same stocks seen from different angles (small, thin, jumpy, high-beta). The "what matters once everything is held fixed" line on each card is a regression that separates them. Where it disagrees with the simple table, trust the regression for which feature matters, and neither for causes.
6. **Is the measurement itself clean?** The hold-length finding only partly passes this test (see above); the cell hold lengths are estimated on the first 70% of quarters, not strictly out of sample.
7. **Is it one of many comparisons?** Each strategy is cut about 60 ways. Anything that only just clears t = 2 deserves suspicion.

## Benchmark sensitivity (read before relying on any by-side or size-based number)

The tables here measure alpha against the average of *all* companies that announced in the same quarter ("all-announcer"). Against that benchmark, big stocks look like losers on the long side and winners on the short side (they lag a small-cap-dominated average). A *size-matched* benchmark (average of same-size companies, same quarter) fixes this; `docs/STRATEGY_PROFILES.md` uses it. Both, for Balanced Leverage (`results/profiles_benchmark_compare.csv`):

| Cut | Share of money | Real P&L per $ (bp) | Size-matched alpha (bp) | All-announcer alpha (bp) |
|---|---|---|---|---|
| Whole book | 100% | 60 | 68 | 62 |
| Long leg | 49% | 233 | 103 | 72 |
| Short leg | 51% | -109 | 34 | 53 |
| Smallest companies (size 1) | 9% | 4 | 81 | 28 |
| Size 2 | 19% | 119 | 126 | 125 |
| Size 3 | 23% | 74 | 75 | 74 |
| Size 4 | 24% | 52 | 50 | 49 |
| Largest companies (size 5) | 24% | 32 | 31 | 29 |
| Thin trading | 27% | 79 | 110 | 94 |
| Low-beta stocks | 32% | 124 | 128 | 121 |

* Single-dimension findings on trading volume and beta survive the change of benchmark (thin 110 vs 94 bp; low-beta 128 vs 121 bp). **The size findings do not**: the smallest companies earn 81 bp size-matched but 28 bp against all announcers; the largest 31 vs 29 bp.
* By-side conclusions depend on the benchmark: against all announcers the long leg earns 72 bp per $ and the short leg 53 bp; size-matched, 103 vs 34 bp. Across the nine core books the size-matched long leg earns 1.8 to 3.0 times the short leg per dollar.
* Any argument about **capacity or the live S&P 500 universe** (where only large companies trade) therefore depends on the benchmark: use the largest-size line of the table (all-announcer 29 bp, size-matched 31 bp) and say which one is meant.

## Strategy by strategy

### Extreme Picks  (`extreme_picks`)

Puts half its money on each end of the ranking (biggest beats, biggest misses). Simple, so its fortunes are tied to how the very best and very worst surprises behave.

**Total:** $9.0M alpha over 71 quarters, 69 bp per dollar traded, ~2,135 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 54% | 69% | 89 | 3.6 | ENGINE |
| Biggest misses (short) | 46% | 31% | 45 | 2.4 | WORKS |
| Smallest companies | 9% | -10% | -78 | -0.2 | UNPROVEN |
| Largest companies | 22% | 14% | 42 | 1.3 | UNPROVEN |
| Thinnest trading | 6% | 18% | 211 | 3.5 | ENGINE |
| Most heavily traded | 23% | 12% | 37 | 1.0 | UNPROVEN |
| Calm stocks | 33% | 59% | 123 | 5.2 | ENGINE |
| Jumpy stocks | 34% | -4% | -8 | 0.6 | UNPROVEN |
| Low-beta stocks | 31% | 77% | 172 | 5.1 | ENGINE |
| High-beta stocks | 34% | -32% | -64 | -0.4 | UNPROVEN |
| Holds over 90 days | 5% | 29% | 380 | 4.3 | ENGINE |
| Holds up to 40 days | 20% | 43% | 146 | 3.6 | ENGINE |
| Reports in Apr-Jun | 17% | 45% | 180 | 6.8 | ENGINE |
| Reports in Oct-Dec | 25% | 3% | 7 | -0.0 | UNPROVEN |
| 1996-2001 | 24% | 35% | 100 | 2.3 | WORKS |
| 2008-2013 | 40% | 4% | 7 | 0.9 | UNPROVEN |

**Money with no proven edge:** 0% of dollars sit in surprise groups whose alpha is not reliably above zero (none); together they earn 0% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -98 (t -2.9)

### Graded Bets  (`graded_bets`)

Spreads money over every company, so a lot of it sits on mild surprises that barely pay.

**Total:** $6.1M alpha over 71 quarters, 47 bp per dollar traded, ~9,606 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 20% | 45% | 104 | 4.3 | ENGINE |
| Biggest misses (short) | 18% | 14% | 36 | 2.0 | UNPROVEN |
| Smallest companies | 10% | -3% | -13 | 0.5 | UNPROVEN |
| Largest companies | 25% | 13% | 24 | 1.4 | UNPROVEN |
| Thinnest trading | 9% | 22% | 117 | 3.7 | ENGINE |
| Most heavily traded | 25% | 5% | 9 | 0.7 | UNPROVEN |
| Calm stocks | 33% | 58% | 82 | 6.3 | ENGINE |
| Jumpy stocks | 33% | -5% | -8 | 0.4 | UNPROVEN |
| Low-beta stocks | 32% | 78% | 115 | 5.4 | ENGINE |
| High-beta stocks | 34% | -22% | -30 | -0.1 | UNPROVEN |
| Holds over 90 days | 8% | 36% | 214 | 5.0 | ENGINE |
| Holds up to 40 days | 34% | 27% | 37 | 2.4 | WORKS |
| Reports in Apr-Jun | 19% | 57% | 142 | 5.7 | ENGINE |
| Reports in Oct-Dec | 25% | 5% | 10 | 0.3 | UNPROVEN |
| 1996-2001 | 28% | 46% | 77 | 2.3 | WORKS |
| 2008-2013 | 37% | 0% | 0 | 0.5 | UNPROVEN |

**Money with no proven edge:** 45% of dollars sit in surprise groups whose alpha is not reliably above zero (long D06, long D07, long D08, short D01, short D03, short D05); together they earn 2% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -57 (t -3.1); More extreme surprise (|rank - median|): +40 (t +4.9)

### Size Balanced  (`size_balanced`)

Extreme Picks with an equal budget for each company size, so no size group can dominate.

**Total:** $11.3M alpha over 71 quarters, 81 bp per dollar traded, ~2,135 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 49% | 59% | 98 | 4.1 | ENGINE |
| Biggest misses (short) | 51% | 41% | 66 | 3.0 | WORKS |
| Smallest companies | 10% | 0% | 4 | -0.0 | UNPROVEN |
| Largest companies | 23% | 12% | 43 | 1.8 | UNPROVEN |
| Thinnest trading | 6% | 17% | 225 | 3.5 | ENGINE |
| Most heavily traded | 23% | 14% | 48 | 1.4 | UNPROVEN |
| Calm stocks | 33% | 51% | 126 | 5.9 | ENGINE |
| Jumpy stocks | 34% | 7% | 16 | 0.8 | UNPROVEN |
| Low-beta stocks | 31% | 63% | 164 | 5.5 | ENGINE |
| High-beta stocks | 34% | -18% | -45 | -0.5 | UNPROVEN |
| Holds over 90 days | 5% | 22% | 337 | 3.7 | ENGINE |
| Holds up to 40 days | 23% | 39% | 136 | 3.7 | ENGINE |
| Reports in Apr-Jun | 19% | 44% | 194 | 6.8 | ENGINE |
| Reports in Oct-Dec | 25% | 8% | 24 | 0.5 | UNPROVEN |
| 1996-2001 | 25% | 35% | 111 | 2.7 | ENGINE |
| 2008-2013 | 40% | 17% | 35 | 1.5 | UNPROVEN |

**Money with no proven edge:** 0% of dollars sit in surprise groups whose alpha is not reliably above zero (none); together they earn 0% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -91 (t -3.5)

### Conviction Tilt  (`conviction_tilt`)

Drops the unsure middle and leans on the strongest surprises, balanced by company size.

**Total:** $11.6M alpha over 71 quarters, 78 bp per dollar traded, ~4,983 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 24% | 38% | 125 | 4.9 | ENGINE |
| Biggest misses (short) | 25% | 18% | 57 | 2.1 | WORKS |
| Smallest companies | 16% | 15% | 75 | 1.3 | UNPROVEN |
| Largest companies | 20% | 7% | 26 | 1.4 | UNPROVEN |
| Thinnest trading | 11% | 23% | 165 | 3.0 | ENGINE |
| Most heavily traded | 20% | 5% | 21 | 1.1 | UNPROVEN |
| Calm stocks | 33% | 44% | 104 | 6.5 | ENGINE |
| Jumpy stocks | 33% | 14% | 33 | 1.3 | UNPROVEN |
| Low-beta stocks | 32% | 56% | 137 | 5.1 | ENGINE |
| High-beta stocks | 34% | 4% | 9 | 0.5 | UNPROVEN |
| Holds over 90 days | 10% | 32% | 261 | 4.1 | ENGINE |
| Holds up to 40 days | 31% | 34% | 86 | 3.8 | ENGINE |
| Reports in Apr-Jun | 21% | 52% | 197 | 6.6 | ENGINE |
| Reports in Oct-Dec | 26% | 13% | 38 | 1.0 | UNPROVEN |
| 1996-2001 | 26% | 40% | 121 | 2.9 | ENGINE |
| 2008-2013 | 40% | 22% | 43 | 1.8 | UNPROVEN |

**Money with no proven edge:** 18% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, short D03, short D04); together they earn 8% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Thinner trading: +40 (t +2.0); Higher beta: -54 (t -2.7); More extreme surprise (|rank - median|): +25 (t +2.4)

### Sector Balanced  (`sector_balanced`)

Conviction Tilt with an equal budget for each of 12 industry groups, so no industry can dominate.

**Total:** $8.5M alpha over 71 quarters, 72 bp per dollar traded, ~6,368 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 20% | 40% | 144 | 5.0 | ENGINE |
| Biggest misses (short) | 20% | 9% | 32 | 1.0 | UNPROVEN |
| Smallest companies | 13% | 17% | 98 | 1.7 | UNPROVEN |
| Largest companies | 23% | 10% | 31 | 2.3 | WORKS |
| Thinnest trading | 10% | 25% | 180 | 4.2 | ENGINE |
| Most heavily traded | 22% | 8% | 26 | 1.6 | UNPROVEN |
| Calm stocks | 35% | 45% | 94 | 5.9 | ENGINE |
| Jumpy stocks | 31% | 14% | 33 | 0.9 | UNPROVEN |
| Low-beta stocks | 35% | 65% | 135 | 5.9 | ENGINE |
| High-beta stocks | 32% | 5% | 11 | -0.4 | UNPROVEN |
| Holds over 90 days | 9% | 29% | 238 | 3.6 | ENGINE |
| Holds up to 40 days | 33% | 27% | 58 | 2.8 | WORKS |
| Reports in Apr-Jun | 25% | 48% | 140 | 5.5 | ENGINE |
| Reports in Oct-Dec | 26% | 15% | 41 | 1.5 | UNPROVEN |
| 1996-2001 | 26% | 44% | 123 | 4.1 | ENGINE |
| 2008-2013 | 41% | 19% | 33 | 1.1 | UNPROVEN |

**Money with no proven edge:** 47% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, short D01, short D03, short D04); together they earn 23% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -46 (t -2.7)

### Balanced Leverage  (`balanced_leverage`)

Sector Balanced allowed to hold more; same bets, bigger. The reference book.

**Total:** $18.4M alpha over 71 quarters, 62 bp per dollar traded, ~6,109 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 20% | 35% | 109 | 4.3 | ENGINE |
| Biggest misses (short) | 20% | 18% | 56 | 1.8 | UNPROVEN |
| Smallest companies | 9% | 4% | 28 | 0.8 | UNPROVEN |
| Largest companies | 24% | 11% | 29 | 2.4 | WORKS |
| Thinnest trading | 7% | 15% | 133 | 3.6 | ENGINE |
| Most heavily traded | 24% | 9% | 24 | 1.7 | UNPROVEN |
| Calm stocks | 34% | 45% | 83 | 6.0 | ENGINE |
| Jumpy stocks | 32% | 15% | 29 | 1.0 | UNPROVEN |
| Low-beta stocks | 32% | 62% | 121 | 5.7 | ENGINE |
| High-beta stocks | 33% | 3% | 5 | -0.4 | UNPROVEN |
| Holds over 90 days | 8% | 23% | 185 | 3.0 | ENGINE |
| Holds up to 40 days | 34% | 32% | 59 | 3.0 | WORKS |
| Reports in Apr-Jun | 23% | 44% | 119 | 4.9 | ENGINE |
| Reports in Oct-Dec | 26% | 17% | 40 | 1.6 | UNPROVEN |
| 1996-2001 | 22% | 43% | 120 | 4.2 | ENGINE |
| 2008-2013 | 44% | 13% | 19 | 0.9 | UNPROVEN |

**Money with no proven edge:** 47% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, short D01, short D03, short D04); together they earn 31% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -47 (t -2.8); More extreme surprise (|rank - median|): +26 (t +2.5)

### Calm Weighted  (`calm_weighted`)

Balanced Leverage with bets divided by how jumpy the stock is.

**Total:** $19.7M alpha over 71 quarters, 65 bp per dollar traded, ~6,147 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 20% | 35% | 115 | 4.1 | ENGINE |
| Biggest misses (short) | 20% | 19% | 61 | 2.0 | WORKS |
| Smallest companies | 9% | 5% | 33 | 1.0 | UNPROVEN |
| Largest companies | 25% | 13% | 33 | 3.0 | WORKS |
| Thinnest trading | 7% | 13% | 128 | 3.6 | ENGINE |
| Most heavily traded | 23% | 11% | 30 | 2.2 | WORKS |
| Calm stocks | 42% | 51% | 79 | 6.4 | ENGINE |
| Jumpy stocks | 24% | 12% | 32 | 1.2 | UNPROVEN |
| Low-beta stocks | 34% | 61% | 116 | 6.2 | ENGINE |
| High-beta stocks | 30% | 5% | 12 | -0.1 | UNPROVEN |
| Holds over 90 days | 8% | 21% | 175 | 3.4 | ENGINE |
| Holds up to 40 days | 34% | 35% | 67 | 3.4 | ENGINE |
| Reports in Apr-Jun | 23% | 44% | 124 | 5.4 | ENGINE |
| Reports in Oct-Dec | 26% | 18% | 44 | 2.0 | UNPROVEN |
| 1996-2001 | 21% | 43% | 131 | 5.0 | ENGINE |
| 2008-2013 | 45% | 16% | 24 | 1.1 | UNPROVEN |

**Money with no proven edge:** 16% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, short D04); together they earn 2% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -43 (t -2.7); More extreme surprise (|rank - median|): +33 (t +3.3)

### Calm Rank Tilt  (`calm_rank_tilt`)

Balanced Leverage with bets for the jumpiest stocks shrunk hard, so most of its money is in calm stocks.

**Total:** $21.5M alpha over 71 quarters, 70 bp per dollar traded, ~6,211 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 20% | 33% | 117 | 3.1 | ENGINE |
| Biggest misses (short) | 19% | 29% | 106 | 3.0 | ENGINE |
| Smallest companies | 8% | 8% | 73 | 2.0 | UNPROVEN |
| Largest companies | 26% | 13% | 34 | 3.2 | WORKS |
| Thinnest trading | 6% | 12% | 133 | 3.8 | ENGINE |
| Most heavily traded | 22% | 9% | 30 | 2.3 | WORKS |
| Calm stocks | 57% | 59% | 73 | 6.7 | ENGINE |
| Jumpy stocks | 8% | 9% | 74 | 1.8 | UNPROVEN |
| Low-beta stocks | 39% | 61% | 110 | 6.7 | ENGINE |
| High-beta stocks | 22% | 9% | 27 | -0.3 | UNPROVEN |
| Holds over 90 days | 7% | 15% | 143 | 3.0 | ENGINE |
| Holds up to 40 days | 35% | 36% | 72 | 3.3 | ENGINE |
| Reports in Apr-Jun | 24% | 45% | 132 | 5.4 | ENGINE |
| Reports in Oct-Dec | 26% | 17% | 46 | 2.3 | WORKS |
| 1996-2001 | 20% | 44% | 154 | 5.8 | ENGINE |
| 2008-2013 | 45% | 20% | 30 | 1.6 | UNPROVEN |

**Money with no proven edge:** 46% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, short D02, short D03, short D04); together they earn 23% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -42 (t -2.7); More extreme surprise (|rank - median|): +53 (t +5.1); Market return while held (known only afterwards): -49 (t -2.1)

### Market Exposed  (`market_exposed`)

Balanced Leverage's signal with raw returns and no forced long/short match, so market moves leak in. (Its alpha figure here is still measured against the average announcing company, so it leaves out the market's own contribution, which is why it is below this strategy's total P&L.)

**Total:** $19.4M alpha over 71 quarters, 55 bp per dollar traded, ~6,199 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 22% | 37% | 96 | 3.8 | ENGINE |
| Biggest misses (short) | 20% | 20% | 55 | 1.8 | UNPROVEN |
| Smallest companies | 9% | -3% | -21 | 0.0 | UNPROVEN |
| Largest companies | 25% | 16% | 36 | 2.2 | WORKS |
| Thinnest trading | 6% | 13% | 116 | 3.2 | ENGINE |
| Most heavily traded | 24% | 15% | 34 | 1.8 | UNPROVEN |
| Calm stocks | 34% | 52% | 85 | 5.1 | ENGINE |
| Jumpy stocks | 32% | 11% | 20 | 0.9 | UNPROVEN |
| Low-beta stocks | 31% | 70% | 123 | 5.2 | ENGINE |
| High-beta stocks | 33% | -4% | -7 | -0.3 | UNPROVEN |
| Holds over 90 days | 7% | 20% | 153 | 2.6 | ENGINE |
| Holds up to 40 days | 32% | 38% | 64 | 2.7 | ENGINE |
| Reports in Apr-Jun | 23% | 48% | 114 | 4.2 | ENGINE |
| Reports in Oct-Dec | 26% | 16% | 34 | 1.2 | UNPROVEN |
| 1996-2001 | 21% | 42% | 110 | 3.4 | ENGINE |
| 2008-2013 | 44% | 7% | 9 | 0.4 | UNPROVEN |

**Money with no proven edge:** 46% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, short D01, short D03, short D04); together they earn 29% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -50 (t -2.7); More extreme surprise (|rank - median|): +32 (t +3.3)

### Variants that share a book with the above

* **Market Boost** trades exactly Balanced Leverage's positions (same table); its extra return comes from a separate half-size market position, not from any situation.
* **Sector Balanced Max, Balanced Leverage Max and Market Boost Max** hold an identical book (same positions, same 28 bp per $); only the overlay differs.
* **Max versions of every strategy** keep the same situations but earn less alpha per dollar (28-60 bp vs 47-81 bp for the base books), consistent with the 5%-of-volume trading limit binding more often at that size (Balanced Leverage Max: 49% of positions liquidity-capped vs 15% for Balanced Leverage; `pct_liquidity_capped` in each `summary.json`). That is an association, not a tested cause.
* **Calm Weighted Filter, Volatility Filter, Calm Weighted Matched and Calm Rank Levered** look like their parents in every table; see `results/situations_all.csv`.
