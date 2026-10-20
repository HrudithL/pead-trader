# Where each strategy's return comes from

Generated tables: `results/situations_all.csv` (every situation, every strategy), `situations_contrasts.csv` (is situation A really better than B?),
`situations_drivers.csv` (what matters once everything is held fixed at once). Code: `pead/reporting/situations.py`; rebuild with
`python scripts/situations_report.py && python scripts/build_situations_doc.py`. Sample 1996-2013, 72 announcement quarters.

## How to read the tables

Every position the strategy opened is labelled with the situation it was in: what kind of surprise it was, how big the company is, how easily it trades,
how jumpy it is, how long it was held, what the market did meanwhile, and so on. For each situation:

* **Share of money traded** -- how often the strategy is in this situation (by dollars, so a big bet counts more than a small one).
* **Share of alpha** -- how much of the strategy's earnings come from it. Compare with the share of money: a situation with 10% of the money and 40% of the alpha is doing four times its share.
* **Alpha per $ traded (bp)** -- the return the earnings signal produced per dollar put to work, after trading costs, in hundredths of a percent. "Alpha" here is the return
  beyond the average company that announced that quarter, i.e. the part the surprise ranking is supposed to predict.
* **Sure? (t)** -- how confident we can be that the real figure is above zero, computed across the 72 quarters (positions in the same quarter share the same market weather, so they are not counted as separate
  evidence). Roughly: 2 means "probably real", 3+ means "very likely real", under 2 means "could be luck".
* **Verdict** -- ENGINE (t >= 2.5, positive in at least 2 of 3 time periods, and earns more per dollar than the strategy's average), WORKS (t >= 2, positive in at least 2 of 3 periods),
  UNPROVEN (could be luck), LEAKS (reliably loses money). About 60 situations are tested per strategy, so a few will look good or bad by pure chance; the three-period rule is the guard against that.

## What every equity strategy has in common (numbers are Balanced Leverage; the other strategies match in direction, see the cards)

| Finding | The numbers | Confidence |
|---|---|---|
| **The money is in the hard-to-trade names.** | The largest and most heavily traded companies (size and liquidity groups 4-5) are 49% of the money but 24% and 23% of the alpha. The smallest companies are 9% of the money and 25% of the alpha (203 bp per $ against 28 bp for the largest). The thinnest-trading group is 7% of the money and 23% of the alpha (249 bp vs 22 bp). | Real: thin vs heavily traded differs by 238 bp per $ (t = 5.0), small vs large by 180 bp (t = 2.8). We cannot tell small from thin: company size and trading volume rank almost the same way (correlation 0.84). |
| **Long holds do the work.** | Holds over 90 days are 10% of the money and 42% of the alpha (303 bp per $); holds up to 40 days are 38% of the money and 19% of the alpha (37 bp). | Real in the data (t = 4.6), **but flattered**: each cell's hold length was chosen by looking at the same 1996-2013 history. Part of this is look-ahead. |
| **Low-beta stocks pay, high-beta stocks do not.** | Low-beta stocks: 31% of the money, 56% of the alpha (129 bp). High-beta stocks: 33% of the money, 9% of the alpha (20 bp, not distinguishable from zero). Same ordering in all three periods (low beta 192 / 142 / 40 bp, high beta -28 / 24 / 27 bp). | Strong: with everything else held fixed, one standard deviation more beta costs about 40 bp per $ (t = -3.0). |
| **Big beats are the best single situation; bad news on the short side is spread out.** | Long D10 (biggest beats): 18% of the money, 36% of the alpha (142 bp, t = 5.3). Shorts D02-D03 earn 80-83 bp each. The weakest long groups, D07 and D09, take 20% of the money and earn 4% of the alpha. | Strong for D10. Long D07/D09 not distinguishable from zero. |
| **Earnings season matters.** | Companies reporting in Apr-Jun and Jul-Sep: 50% of the money, 83% of the alpha (130 and 113 bp). Oct-Dec reports: 26% of the money, 14% of the alpha (40 bp). Jan-Mar reports: 25% of the money, 3% of the alpha (8 bp), but most of that is one quarter: 2009Q1 alone lost $2.85M; without 2009 the Jan-Mar figure is 64 bp. | Oct-Dec is weaker in all three periods (74 / 34 / 29 bp); Jan-Mar is weak mainly in 2008-13. But the t-stats (1.0-1.6) do not prove Oct-Mar is worse than Apr-Sep. |
| **The edge has faded.** | 1996-2001: 136 bp per $; 2002-2007: 89 bp; 2008-2013: 33 bp. The latest period carries 46% of the money and only 21% of the alpha. | Real (early vs late differ by 110 bp, t = 3.0). |
| **The market's direction does not matter.** | Quarter by quarter, alpha per $ is 61 bp when the market rose over 5% during the hold and 43 bp when it fell over 5% (difference t = 0.03). Quarterly alpha is uncorrelated with the market's quarterly return (-0.03). (Pooled by dollars the fall figure is higher, 130 vs 54 bp, but that rests on few quarters and is not statistically distinguishable.) | This is the market-neutral design working. |
| **Steady, except for a few bad quarters.** | 72% of quarters make money. The best 10 of 72 quarters supply 52% of the alpha; the worst quarter (2009Q1) lost $2.85M, more than the next five worst combined. | Descriptive. |

**A pattern worth knowing about, with only four examples:** in the four sharp rebound quarters after a crash (2001Q4, 2003Q2, 2009Q2, 2009Q3) the book made -28 bp against +94 bp in all other quarters.
The damage came from the long leg (-133 bp), not from short squeezes. Four episodes are too few to call it a rule.

## Why do these situations exist?

What the data supports, and what it only suggests:

* **Hard-to-trade names: a plausible cause, not tested here.** The usual explanation is that small, thinly traded companies have fewer analysts and fewer professional investors watching, and trading costs deter the people who would otherwise
  correct the mispricing, so the slow reaction to earnings lasts longer. The data matches this (alpha per dollar falls steadily from the smallest to the largest group) but cannot test the cause.
* **Long holds: partly a measurement artifact.** In the cells where holds are long, the drift keeps building for months. But the hold length of each cell comes from the same history we then test on.
* **Calm stocks vs jumpy stocks: the data does not cleanly say.** Calm stocks earn 88 bp per $ against 49 bp for jumpy ones when pooled by dollars (105 vs 46 bp quarter by quarter, t = 1.9, borderline). But once beta, size, liquidity and hold length are held fixed, jumpiness has no independent effect (+2 bp, t = 0.1).
  The calm-stock advantage may really be a beta advantage: 56% of the jumpiest third of stocks are high-beta, against 13% of the calmest third (correlation 0.47). This is the most important open question for the Calm strategies.
* **High beta being worse: unexplained.** Nothing in the data says why. One candidate is that market-adjusted returns assume a beta of 1, so a high-beta stock's relative return is full of market noise that has nothing to do with the earnings news.
* **The seasons and the post-2008 fade: unexplained.** The fade fits the idea that more investors now trade this drift away, but the data does not test that.

## How to tell whether a strategy is good on one topic and bad on another

1. **Frequency and weight:** how much money is in the situation, and how often does it occur (positions per year). A great situation that is 1% of the money cannot move the total.
2. **Return per dollar and share of alpha:** compare the share of alpha with the share of money.
3. **Sureness:** the t-stat across quarters, not across positions. A situation that made money in 55 of 72 quarters is more believable than one carried by three huge quarters.
4. **Consistency:** the same sign in all three time periods. A situation that only worked in 1996-2001 is probably decayed.
5. **Is it really that situation?** Many situations are the same stocks seen from different angles (small, thin, jumpy, high-beta). The "what matters once everything is held fixed" line on each card is a regression that separates them. Where it disagrees with the simple table, trust the regression for which feature matters, and neither for causes.
6. **Is the measurement itself clean?** The hold-length finding fails this test (look-ahead); fixing it needs the cell hold lengths re-estimated using only past data.
7. **Is it one of many comparisons?** Each strategy is cut about 60 ways. Anything that only just clears t = 2 deserves suspicion.

## Benchmark sensitivity (read before relying on any by-side number)

The tables here measure alpha against the average of *all* companies that announced in the same quarter. Against that benchmark, big stocks look like losers on the long side and winners on the short side (they lag a small-cap-dominated average). A *size-matched* benchmark (average of same-size companies, same quarter) fixes this; `docs/STRATEGY_PROFILES.md` uses it.
Single-dimension findings here survive it (thin 153 vs 136 bp, low-beta 137 vs 129, smallest companies 260 vs 203). By-side conclusions do not: against the size-matched benchmark the long leg earns about twice the short leg in every strategy, not the same.

## Strategy by strategy

### Extreme Picks  (`extreme_picks`)

Puts half its money on each end of the ranking (biggest beats, biggest misses). Simple, so its fortunes are tied to how the very best and very worst surprises behave.

**Total:** $13.5M alpha over 72 quarters, 78 bp per dollar traded, ~1,908 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 52% | 68% | 103 | 4.4 | ENGINE |
| Biggest misses (short) | 48% | 32% | 52 | 1.5 | UNPROVEN |
| Smallest companies | 8% | 13% | 122 | 1.1 | UNPROVEN |
| Largest companies | 22% | 4% | 15 | 0.4 | UNPROVEN |
| Thinnest trading | 5% | 21% | 301 | 4.7 | ENGINE |
| Most heavily traded | 22% | 2% | 7 | 0.2 | UNPROVEN |
| Calm stocks | 33% | 48% | 112 | 5.0 | ENGINE |
| Jumpy stocks | 34% | 14% | 33 | 1.0 | UNPROVEN |
| Low-beta stocks | 28% | 62% | 172 | 5.6 | ENGINE |
| High-beta stocks | 35% | -10% | -22 | -0.4 | UNPROVEN |
| Holds over 90 days | 10% | 51% | 399 | 4.8 | ENGINE |
| Holds up to 40 days | 20% | 19% | 75 | 3.0 | WORKS |
| Reports in Apr-Jun | 24% | 69% | 219 | 7.2 | ENGINE |
| Reports in Oct-Dec | 26% | -4% | -12 | -0.2 | UNPROVEN |
| 1996-2001 | 20% | 35% | 136 | 2.7 | ENGINE |
| 2008-2013 | 46% | 15% | 25 | 0.4 | UNPROVEN |

**Money with no proven edge:** 48% of dollars sit in surprise groups whose alpha is not reliably above zero (short D01); together they earn 32% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -82 (t -3.0); Longer hold window: +80 (t +2.9)

### Graded Bets  (`graded_bets`)

Spreads money over every company, so a lot of it sits on mild surprises that barely pay.

**Total:** $10.4M alpha over 72 quarters, 61 bp per dollar traded, ~9,634 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 18% | 40% | 136 | 5.3 | ENGINE |
| Biggest misses (short) | 17% | 12% | 43 | 1.2 | UNPROVEN |
| Smallest companies | 10% | 23% | 145 | 2.6 | ENGINE |
| Largest companies | 24% | 4% | 11 | 0.8 | UNPROVEN |
| Thinnest trading | 9% | 32% | 222 | 5.7 | ENGINE |
| Most heavily traded | 25% | 0% | 1 | 0.4 | UNPROVEN |
| Calm stocks | 34% | 44% | 80 | 6.0 | ENGINE |
| Jumpy stocks | 33% | 15% | 26 | 1.2 | UNPROVEN |
| Low-beta stocks | 31% | 58% | 114 | 5.1 | ENGINE |
| High-beta stocks | 35% | 5% | 9 | 0.3 | UNPROVEN |
| Holds over 90 days | 10% | 52% | 327 | 6.8 | ENGINE |
| Holds up to 40 days | 39% | 6% | 9 | 0.9 | UNPROVEN |
| Reports in Apr-Jun | 24% | 59% | 147 | 5.8 | ENGINE |
| Reports in Oct-Dec | 26% | 5% | 12 | 0.5 | UNPROVEN |
| 1996-2001 | 25% | 41% | 101 | 2.9 | ENGINE |
| 2008-2013 | 41% | 15% | 23 | 0.9 | UNPROVEN |

**Money with no proven edge:** 42% of dollars sit in surprise groups whose alpha is not reliably above zero (long D06, long D07, long D09, short D01, short D05); together they earn 18% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Thinner trading: +42 (t +2.1); Higher beta: -39 (t -2.5); Longer hold window: +64 (t +5.1)

### Size Balanced  (`size_balanced`)

Extreme Picks with an equal budget for each company size, so no size group can dominate.

**Total:** $15.2M alpha over 72 quarters, 97 bp per dollar traded, ~1,908 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 48% | 65% | 131 | 4.7 | ENGINE |
| Biggest misses (short) | 52% | 35% | 65 | 2.5 | WORKS |
| Smallest companies | 10% | 18% | 186 | 1.2 | UNPROVEN |
| Largest companies | 23% | 7% | 27 | 1.2 | UNPROVEN |
| Thinnest trading | 6% | 19% | 307 | 4.7 | ENGINE |
| Most heavily traded | 23% | 5% | 22 | 0.8 | UNPROVEN |
| Calm stocks | 33% | 44% | 130 | 5.9 | ENGINE |
| Jumpy stocks | 34% | 20% | 58 | 1.5 | UNPROVEN |
| Low-beta stocks | 30% | 58% | 192 | 6.2 | ENGINE |
| High-beta stocks | 35% | -7% | -19 | -0.3 | UNPROVEN |
| Holds over 90 days | 10% | 46% | 433 | 4.1 | ENGINE |
| Holds up to 40 days | 21% | 19% | 86 | 3.1 | WORKS |
| Reports in Apr-Jun | 25% | 61% | 242 | 7.4 | ENGINE |
| Reports in Oct-Dec | 26% | 5% | 18 | 0.4 | UNPROVEN |
| 1996-2001 | 24% | 39% | 159 | 3.4 | ENGINE |
| 2008-2013 | 43% | 24% | 54 | 1.3 | UNPROVEN |

**Money with no proven edge:** 0% of dollars sit in surprise groups whose alpha is not reliably above zero (none); together they earn 0% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -89 (t -3.5); Longer hold window: +82 (t +2.5)

### Conviction Tilt  (`conviction_tilt`)

Drops the unsure middle and leans on the strongest surprises, balanced by company size.

**Total:** $17.6M alpha over 72 quarters, 86 bp per dollar traded, ~6,242 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 20% | 38% | 167 | 5.7 | ENGINE |
| Biggest misses (short) | 20% | 10% | 44 | 1.2 | UNPROVEN |
| Smallest companies | 14% | 31% | 194 | 3.2 | ENGINE |
| Largest companies | 22% | 6% | 25 | 1.8 | UNPROVEN |
| Thinnest trading | 10% | 29% | 251 | 5.3 | ENGINE |
| Most heavily traded | 22% | 5% | 21 | 1.1 | UNPROVEN |
| Calm stocks | 33% | 40% | 103 | 7.2 | ENGINE |
| Jumpy stocks | 33% | 22% | 57 | 1.8 | UNPROVEN |
| Low-beta stocks | 30% | 52% | 148 | 6.2 | ENGINE |
| High-beta stocks | 35% | 8% | 19 | 0.6 | UNPROVEN |
| Holds over 90 days | 12% | 48% | 355 | 5.9 | ENGINE |
| Holds up to 40 days | 35% | 15% | 37 | 2.6 | WORKS |
| Reports in Apr-Jun | 25% | 51% | 179 | 6.9 | ENGINE |
| Reports in Oct-Dec | 26% | 12% | 41 | 1.1 | UNPROVEN |
| 1996-2001 | 24% | 41% | 151 | 4.0 | ENGINE |
| 2008-2013 | 43% | 27% | 54 | 2.3 | WORKS |

**Money with no proven edge:** 23% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, short D01); together they earn 14% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Thinner trading: +47 (t +2.1); Higher beta: -43 (t -2.6); Longer hold window: +63 (t +3.9)

### Sector Balanced  (`sector_balanced`)

Conviction Tilt with an equal budget for each of 12 industry groups, so no industry can dominate.

**Total:** $12.0M alpha over 72 quarters, 91 bp per dollar traded, ~6,618 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 18% | 45% | 220 | 6.8 | ENGINE |
| Biggest misses (short) | 19% | 3% | 16 | 0.2 | UNPROVEN |
| Smallest companies | 12% | 40% | 292 | 5.1 | ENGINE |
| Largest companies | 23% | 7% | 29 | 2.4 | WORKS |
| Thinnest trading | 10% | 34% | 312 | 6.9 | ENGINE |
| Most heavily traded | 22% | 6% | 24 | 1.5 | UNPROVEN |
| Calm stocks | 35% | 40% | 105 | 6.3 | ENGINE |
| Jumpy stocks | 31% | 22% | 63 | 1.8 | UNPROVEN |
| Low-beta stocks | 34% | 59% | 160 | 7.7 | ENGINE |
| High-beta stocks | 32% | 9% | 26 | 0.0 | UNPROVEN |
| Holds over 90 days | 11% | 49% | 400 | 6.2 | ENGINE |
| Holds up to 40 days | 36% | 16% | 40 | 2.7 | WORKS |
| Reports in Apr-Jun | 25% | 44% | 160 | 7.1 | ENGINE |
| Reports in Oct-Dec | 25% | 15% | 54 | 1.8 | UNPROVEN |
| 1996-2001 | 25% | 42% | 154 | 6.1 | ENGINE |
| 2008-2013 | 42% | 23% | 49 | 1.4 | UNPROVEN |

**Money with no proven edge:** 43% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D09, short D01, short D04); together they earn 18% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -44 (t -3.1); Longer hold window: +68 (t +4.1); Market return while held (known only afterwards): -62 (t -2.2)

### Balanced Leverage  (`balanced_leverage`)

Sector Balanced allowed to hold more; same bets, bigger. The reference book.

**Total:** $24.9M alpha over 72 quarters, 73 bp per dollar traded, ~6,618 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 18% | 36% | 142 | 5.3 | ENGINE |
| Biggest misses (short) | 18% | 13% | 52 | 1.4 | UNPROVEN |
| Smallest companies | 9% | 25% | 203 | 3.3 | ENGINE |
| Largest companies | 25% | 10% | 28 | 2.4 | WORKS |
| Thinnest trading | 7% | 23% | 249 | 5.9 | ENGINE |
| Most heavily traded | 24% | 7% | 22 | 1.5 | UNPROVEN |
| Calm stocks | 34% | 41% | 88 | 6.4 | ENGINE |
| Jumpy stocks | 32% | 22% | 49 | 1.6 | UNPROVEN |
| Low-beta stocks | 31% | 55% | 129 | 6.6 | ENGINE |
| High-beta stocks | 33% | 9% | 20 | -0.0 | UNPROVEN |
| Holds over 90 days | 10% | 42% | 303 | 5.0 | ENGINE |
| Holds up to 40 days | 38% | 19% | 37 | 2.5 | WORKS |
| Reports in Apr-Jun | 25% | 44% | 130 | 5.7 | ENGINE |
| Reports in Oct-Dec | 26% | 14% | 40 | 1.6 | UNPROVEN |
| 1996-2001 | 21% | 38% | 136 | 5.7 | ENGINE |
| 2008-2013 | 46% | 21% | 33 | 1.2 | UNPROVEN |

**Money with no proven edge:** 43% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D09, short D01, short D04); together they earn 23% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -39 (t -3.0); Longer hold window: +55 (t +3.5); Market return while held (known only afterwards): -59 (t -2.4)

### Calm Weighted  (`calm_weighted`)

Balanced Leverage with bets divided by how jumpy the stock is.

**Total:** $26.1M alpha over 72 quarters, 76 bp per dollar traded, ~6,618 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 18% | 35% | 146 | 5.1 | ENGINE |
| Biggest misses (short) | 18% | 14% | 60 | 1.8 | UNPROVEN |
| Smallest companies | 9% | 24% | 207 | 3.5 | ENGINE |
| Largest companies | 25% | 11% | 34 | 3.3 | WORKS |
| Thinnest trading | 7% | 21% | 240 | 5.8 | ENGINE |
| Most heavily traded | 24% | 10% | 31 | 2.3 | WORKS |
| Calm stocks | 42% | 46% | 83 | 7.0 | ENGINE |
| Jumpy stocks | 25% | 19% | 59 | 1.8 | UNPROVEN |
| Low-beta stocks | 34% | 54% | 121 | 6.9 | ENGINE |
| High-beta stocks | 30% | 11% | 29 | 0.3 | UNPROVEN |
| Holds over 90 days | 10% | 39% | 295 | 5.4 | ENGINE |
| Holds up to 40 days | 38% | 23% | 45 | 3.3 | WORKS |
| Reports in Apr-Jun | 25% | 43% | 131 | 6.2 | ENGINE |
| Reports in Oct-Dec | 26% | 16% | 47 | 1.9 | UNPROVEN |
| 1996-2001 | 20% | 38% | 144 | 6.5 | ENGINE |
| 2008-2013 | 47% | 23% | 38 | 1.5 | UNPROVEN |

**Money with no proven edge:** 54% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, long D09, short D01, short D04); together they earn 33% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -37 (t -2.9); Longer hold window: +51 (t +3.7); Market return while held (known only afterwards): -59 (t -2.7)

### Calm Rank Tilt  (`calm_rank_tilt`)

Balanced Leverage with bets for the jumpiest stocks shrunk hard, so most of its money is in calm stocks.

**Total:** $28.4M alpha over 72 quarters, 81 bp per dollar traded, ~6,618 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 18% | 31% | 143 | 4.0 | ENGINE |
| Biggest misses (short) | 17% | 22% | 102 | 3.4 | ENGINE |
| Smallest companies | 7% | 20% | 219 | 4.0 | ENGINE |
| Largest companies | 26% | 13% | 38 | 3.6 | WORKS |
| Thinnest trading | 6% | 16% | 223 | 6.4 | ENGINE |
| Most heavily traded | 23% | 12% | 41 | 3.0 | WORKS |
| Calm stocks | 57% | 54% | 77 | 7.9 | WORKS |
| Jumpy stocks | 8% | 12% | 120 | 2.8 | ENGINE |
| Low-beta stocks | 39% | 54% | 112 | 7.4 | ENGINE |
| High-beta stocks | 22% | 13% | 49 | 0.7 | UNPROVEN |
| Holds over 90 days | 10% | 34% | 288 | 5.6 | ENGINE |
| Holds up to 40 days | 40% | 26% | 53 | 3.7 | WORKS |
| Reports in Apr-Jun | 25% | 39% | 128 | 5.9 | ENGINE |
| Reports in Oct-Dec | 26% | 19% | 59 | 2.7 | WORKS |
| 1996-2001 | 19% | 40% | 170 | 8.2 | ENGINE |
| 2008-2013 | 47% | 23% | 40 | 2.1 | WORKS |

**Money with no proven edge:** 37% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, long D09, short D04); together they earn 15% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Jumpier stock (stock-specific vol): +32 (t +2.4); Higher beta: -38 (t -3.1); Longer hold window: +45 (t +3.9); More extreme surprise (|rank - median|): +24 (t +2.4); Market return while held (known only afterwards): -54 (t -3.2)

### Market Exposed  (`market_exposed`)

Balanced Leverage's signal with raw returns and no forced long/short match, so market moves leak in. (Its alpha figure here is still measured against the average announcing company, so it leaves out the market's own contribution, which is why it is below this strategy's total P&L.)

**Total:** $23.2M alpha over 72 quarters, 63 bp per dollar traded, ~6,618 positions a year.

| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |
|---|---|---|---|---|---|
| Biggest beats (long) | 19% | 35% | 117 | 4.6 | ENGINE |
| Biggest misses (short) | 19% | 15% | 51 | 1.1 | UNPROVEN |
| Smallest companies | 9% | 24% | 166 | 2.3 | WORKS |
| Largest companies | 25% | 7% | 17 | 1.3 | UNPROVEN |
| Thinnest trading | 6% | 24% | 237 | 5.6 | ENGINE |
| Most heavily traded | 24% | 6% | 16 | 1.1 | UNPROVEN |
| Calm stocks | 34% | 44% | 81 | 5.2 | ENGINE |
| Jumpy stocks | 32% | 23% | 44 | 1.6 | UNPROVEN |
| Low-beta stocks | 31% | 56% | 113 | 4.9 | ENGINE |
| High-beta stocks | 34% | 10% | 19 | 0.2 | UNPROVEN |
| Holds over 90 days | 10% | 44% | 281 | 4.8 | ENGINE |
| Holds up to 40 days | 38% | 17% | 28 | 1.8 | UNPROVEN |
| Reports in Apr-Jun | 25% | 50% | 126 | 4.4 | ENGINE |
| Reports in Oct-Dec | 26% | 11% | 28 | 1.0 | UNPROVEN |
| 1996-2001 | 20% | 40% | 124 | 4.3 | ENGINE |
| 2008-2013 | 46% | 13% | 18 | 0.5 | UNPROVEN |

**Money with no proven edge:** 54% of dollars sit in surprise groups whose alpha is not reliably above zero (long D07, long D08, long D09, short D01, short D04); together they earn 32% of the alpha.

**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): Higher beta: -33 (t -2.2); Longer hold window: +53 (t +3.3); Market return while held (known only afterwards): -64 (t -2.2)

### Variants that share a book with the above

* **Market Boost** trades exactly Balanced Leverage's positions (same table); its extra return comes from a separate half-size market position, not from any situation.
* **Sector Balanced Max, Balanced Leverage Max and Market Boost Max** hold an identical book (same positions, same 36 bp per $); only the overlay differs.
* **Max versions of every strategy** keep the same situations but earn roughly half the alpha per dollar (31-58 bp vs 60-97 bp), consistent with the 5%-of-volume trading limit binding about four times as often at that size and pushing money out of the thinnest stocks.
* **Calm Weighted Filter, Volatility Filter, Calm Weighted Matched and Calm Rank Levered** look like their parents in every table; see `results/situations_all.csv`.
