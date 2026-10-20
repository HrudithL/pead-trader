# Where each strategy's return comes from

Generated tables: `results/situations_all.csv` (every situation, every strategy), `situations_contrasts.csv` (is situation A really better than B?),
`situations_drivers.csv` (what matters once everything is held fixed at once). Code: `pead/reporting/situations.py`; rebuild with
`python scripts/situations_report.py && python scripts/situations_facts.py && python scripts/build_situations_doc.py`. Sample 1996-2013, {{nq}} announcement quarters.
Every number in the prose of this document is filled in from those CSVs (and `results/situations_facts.json`, `results/profiles_benchmark_compare.csv`) by `scripts/build_situations_doc.py`; the text itself lives in `scripts/situations_intro.md`.

## How to read the tables

Every position the strategy opened is labelled with the situation it was in: what kind of surprise it was, how big the company is, how easily it trades,
how jumpy it is, how long it was held, what the market did meanwhile, and so on. For each situation:

* **Share of money traded** -- how often the strategy is in this situation (by dollars, so a big bet counts more than a small one).
* **Share of alpha** -- how much of the strategy's earnings come from it. Compare with the share of money: a situation with 10% of the money and 40% of the alpha is doing four times its share.
* **Alpha per $ traded (bp)** -- the return the earnings signal produced per dollar put to work, after trading costs, in hundredths of a percent. "Alpha" here is the return
  beyond the average company that announced that quarter, i.e. the part the surprise ranking is supposed to predict.
* **Sure? (t)** -- how confident we can be that the real figure is above zero, computed across the {{nq}} quarters (positions in the same quarter share the same market weather, so they are not counted as separate
  evidence). Roughly: 2 means "probably real", 3+ means "very likely real", under 2 means "could be luck".
* **Verdict** -- ENGINE (t >= 2.5, positive in at least 2 of 3 time periods, and earns more per dollar than the strategy's average), WORKS (t >= 2, positive in at least 2 of 3 periods),
  UNPROVEN (could be luck), LEAKS (reliably loses money). About 60 situations are tested per strategy, so a few will look good or bad by pure chance; the three-period rule is the guard against that.

## What every equity strategy has in common (numbers are Balanced Leverage; the other strategies match in direction unless a card says otherwise)

These figures come from the corrected data and use the average-announcer benchmark; see "Benchmark sensitivity" at the end for what changes under a size-matched benchmark.

| Finding | The numbers | Confidence |
|---|---|---|
| **The money is in the less-traded names, and it is trading volume more than size.** | The most heavily traded companies (liquidity groups 4-5) are {{liq45_money}} of the money but {{liq45_alpha}} of the alpha. The thinnest-trading group is {{liq1_money}} of the money and {{liq1_alpha}} of the alpha ({{liq1_bp}} bp per $ against {{liq5_bp}} bp for the most traded). By company size the pattern is NOT steady: the smallest companies are {{size1_money}} of the money and {{size1_alpha}} of the alpha ({{size1_bp}} bp), the second-smallest group is {{size2_money}} of the money and {{size2_alpha}} of the alpha ({{size2_bp}} bp), and the largest group earns {{size5_bp}} bp. | Thin vs heavily traded differs by {{thin_diff}} bp per $ (t = {{thin_t}}); small vs large by only {{small_diff}} bp (t = {{small_t}}, not distinguishable from zero). Company size and trading volume rank almost the same way (correlation {{corr_size_liq}}), so the two are hard to separate. |
| **Long holds do the work.** | Holds over 90 days are {{hold90_money}} of the money and {{hold90_alpha}} of the alpha ({{hold90_bp}} bp per $); holds up to 40 days are {{hold40_money}} of the money and {{hold40_alpha}} of the alpha ({{hold40_bp}} bp). | Real in the data (t = {{hold_t}}), **but partly flattered**: since the fixes each cell's hold length is estimated on the first 70% of the quarters (see FIXES.md), which removes part of the look-ahead, but the back-off thresholds between cells still see the full sample. |
| **Low-beta stocks pay, high-beta stocks do not.** | Low-beta stocks: {{lowbeta_money}} of the money, {{lowbeta_alpha}} of the alpha ({{lowbeta_bp}} bp). High-beta stocks: {{highbeta_money}} of the money, {{highbeta_alpha}} of the alpha ({{highbeta_bp}} bp, t = {{highbeta_t}}, not distinguishable from zero). By period (1996-01 / 2002-07 / 2008-13): low beta {{lowbeta_eras}} bp, high beta {{highbeta_eras}} bp. | With everything else held fixed, one standard deviation more beta costs about {{beta_driver_bp}} bp per $ (t = {{beta_driver_t}}). |
| **Big beats are the best single situation; bad news on the short side is spread out.** | Long D10 (biggest beats): {{d10_money}} of the money, {{d10_alpha}} of the alpha ({{d10_bp}} bp, t = {{d10_t}}). Long D09 earns {{d09_bp}} bp. Shorts D01-D03 earn {{s1_bp}}, {{s2_bp}} and {{s3_bp}} bp. The weakest long groups, D07 and D08, take {{d78_money}} of the money and earn {{d78_alpha}} of the alpha (D07: {{d07_bp}} bp, t = {{d07_t}}). | Strong for D10. D10 beats the biggest misses (short D01) by {{d10_vs_s1}} bp per $ but only at t = {{d10_vs_s1_t}}. Long D07/D08 are not distinguishable from zero. |
| **Earnings season matters.** | Companies reporting in Apr-Jun and Jul-Sep: {{amj_jas_money}} of the money, {{amj_jas_alpha}} of the alpha ({{amj_bp}} and {{jas_bp}} bp). Oct-Dec reports: {{q4_money}} of the money, {{q4_alpha}} of the alpha ({{q4_bp}} bp). Jan-Mar reports: {{q1_money}} of the money, {{q1_alpha}} of the alpha ({{q1_bp}} bp), but much of that is one quarter: {{worst_q}} alone lost ${{worst_q_M}}M; without 2009 the Jan-Mar figure is {{q1_bp_ex}} bp. | Oct-Dec is positive in all three periods ({{q4_eras}} bp); Jan-Mar is negative in 2008-13 ({{q1_eras}} bp). But the t-stats of the two weak seasons ({{q1_t}} and {{q4_t}}) do not prove them worse than Apr-Sep. |
| **The edge has faded.** | 1996-2001: {{era1_bp}} bp per $; 2002-2007: {{era2_bp}} bp; 2008-2013: {{era3_bp}} bp. The latest period carries {{era3_money}} of the money and only {{era3_alpha}} of the alpha. | Real (early vs late differ by {{era_diff}} bp, t = {{era_t}}); the latest period on its own has t = {{era3_t}}. |
| **The market's direction does not clearly matter.** | Pooled by dollars, alpha per $ is {{mrise_bp}} bp when the market rose over 5% during the hold and {{mfell_bp}} bp when it fell over 5%. Quarter by quarter the difference (rose minus fell) is {{mdiff}} bp with t = {{mdiff_t}}. Quarterly alpha per $ has correlation {{corr_q_mkt}} with the market's return over the holding windows. | This is the market-neutral design working: no statistically distinguishable dependence. |
| **Steady, except for a few bad quarters.** | {{pct_q_pos}} of quarters make money. The best 10 of {{nq}} quarters supply {{best10}} of the alpha; the worst quarter ({{worst_q}}) lost ${{worst_q_M}}M, more than the next five worst combined (${{next5_M}}M). | Descriptive. (Dollar figures here use the average-announcer alpha; `docs/STRATEGY_PROFILES.md` quotes the size-matched figure for the same quarter.) |

## Why do these situations exist?

What the data supports, and what it only suggests:

* **Hard-to-trade names: a plausible cause, not tested here.** The usual explanation is that thinly traded companies have fewer analysts and fewer professional investors watching, and trading costs deter the people who would otherwise
  correct the mispricing, so the slow reaction to earnings lasts longer. The data matches this for trading volume (alpha per dollar falls steadily from the thinnest to the most heavily traded group: {{liq_bps}} bp) but not for company size alone (by size group: {{size_bps}} bp), and it cannot test the cause.
* **Long holds: partly a measurement artifact.** In the cells where holds are long, the drift keeps building for months. But the hold length of each cell is still estimated from history that overlaps the test period; the fixes moved the estimate to the first 70% of quarters, which helps but does not remove the problem.
* **Calm stocks vs jumpy stocks: the data does not cleanly say.** Calm stocks earn {{calm_bp}} bp per $ against {{jumpy_bp}} bp for jumpy ones when pooled by dollars ({{calm_q_bp}} vs {{jumpy_q_bp}} bp quarter by quarter, t = {{calm_t}}, borderline). But once beta, size, liquidity and hold length are held fixed, jumpiness has no independent effect ({{jumpy_driver_bp}} bp, t = {{jumpy_driver_t}}).
  The calm-stock advantage may really be a beta advantage: {{pct_jumpy_hb}} of the jumpiest third of stocks are high-beta, against {{pct_calm_hb}} of the calmest third (correlation {{corr_vol_beta}}). `docs/STRATEGY_PROFILES.md` splits Balanced Leverage by both labels at once (`results/profiles_beta_vol_grid.csv`) and finds the same thing. This remains the most important open question for the Calm strategies.
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

{{bench_table}}

* Single-dimension findings on trading volume and beta survive the change of benchmark (thin {{b_thin_sm}} vs {{b_thin_all}} bp; low-beta {{b_low_sm}} vs {{b_low_all}} bp). **The size findings do not**: the smallest companies earn {{b_s1_sm}} bp size-matched but {{b_s1_all}} bp against all announcers; the largest {{b_s5_sm}} vs {{b_s5_all}} bp.
* By-side conclusions depend on the benchmark: against all announcers the long leg earns {{b_long_all}} bp per $ and the short leg {{b_short_all}} bp; size-matched, {{b_long_sm}} vs {{b_short_sm}} bp. Across the nine core books the size-matched long leg earns {{b_ratio_min}} to {{b_ratio_max}} times the short leg per dollar.
* Any argument about **capacity or the live S&P 500 universe** (where only large companies trade) therefore depends on the benchmark: use the largest-size line of the table (all-announcer {{b_s5_all}} bp, size-matched {{b_s5_sm}} bp) and say which one is meant.
