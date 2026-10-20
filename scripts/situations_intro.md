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
