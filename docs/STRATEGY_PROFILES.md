# Strategy profiles: where each equity strategy wins, where it loses, and why

Every number below comes from the corrected results (after the four methodology fixes in [FIXES.md](FIXES.md)): `results/<name>/summary.json`, `results/alpha_benchmark.csv`, and the tables written by `scripts/strategy_profiles.py` (`results/profiles_recipes.csv`, `profiles_trades.csv`, `profiles_timing.csv`, `profiles_beta_legs.csv`), `scripts/benchmark_compare.py` (`profiles_benchmark_compare.csv`) and `scripts/beta_vol_grid.py` (`profiles_beta_vol_grid.csv`). The earlier version of this document was written before the fixes; several of its findings did not survive, and this version says where.
Sample 1996-2013, 71 announcement quarters (the first quarter is lost to the point-in-time ranking). Read `docs/SITUATIONS.md` first for how to read the terms.

## Method (and one correction to earlier numbers)

A **recipe** is a specific kind of trade: side (long / short) x how easily the stock trades (**thin** = the two least-traded fifths of stocks by daily dollar volume, **mid-liquidity** = the middle fifth, **liquid** = the two most-traded fifths) x **beta** (how strongly the stock follows the market: **low / mid / high** = thirds of the strategy's own positions; a high-beta stock typically moves 1.7x the market, a low-beta one 0.5x) x hold (short <= 60 days / long > 60 days) -- 36 recipes per strategy.

**Alpha vs real dollars.** *Real dollars* are the actual profit and loss of the trades, including the fact that the average announcing stock moves differently from the market. *Alpha* strips that out: it is what a trade earned beyond a comparable stock, so it measures what the surprise signal itself delivered. The market-neutral books are built to earn alpha, which is why a recipe can show positive alpha and negative real dollars (typical for shorts) at the same time. **bp** = hundredths of a percent earned per dollar traded (100 bp = 1%). **t** is a confidence measure computed across announcement quarters (|t| above about 2 means unlikely to be luck).

**Alpha here is measured against the average announcer of the same size group in the same quarter** (a "size-matched" benchmark), not against the average of all announcers. The older benchmark makes large stocks look like losers on the long side and winners on the short side (big stocks lag the average announcer, which is dominated by small caps), so it gives wrong answers whenever side and size are mixed in one cut. For Balanced Leverage, the long leg of liquid stocks makes +153 bp per $ in real dollars; the all-announcer benchmark says -20 bp and the size-matched one +75 bp. Single-dimension findings in SITUATIONS.md mostly hold up under the size-matched benchmark (thin stocks: 110 vs 94 bp; low-beta: 128 vs 121 bp), with one exception: the smallest fifth of companies earns 81 bp size-matched but only 28 bp against all announcers (Balanced Leverage; `profiles_benchmark_compare.csv`).

**Long leg vs short leg.** Earlier docs said the surprise signal earns the same on both legs, and a first correction said the long leg earns about twice the short leg. On the corrected data and the size-matched benchmark the long leg earns **1.8 to 3.0 times** the short leg per dollar in every strategy (Balanced Leverage: long 103 bp per $, $15.0M; short 34 bp, $5.1M). By company size the long leg is ahead in the four middle-to-large fifths in every strategy, but **not in the smallest fifth**: there the long leg leads in only 5 of the 9 books (Balanced Leverage: long 73 bp, short 88 bp; largest fifth: long 71, short -10). The short leg also carries unmodelled borrow fees.

The 3 x 3 volatility-by-beta table at the end of the common section and the card figures all use this size-matched alpha. To reproduce the profile tables, point `PEAD_DATA_DIR` / `PEAD_RAW_EQUITY_DIR` at the corrected data vintage (the committed `data/` is pre-fix; see FIXES.md).

## What every strategy has in common

* **No single best trade any more, but one family.** The old "same single best recipe" no longer holds: *long, thin-trading, low-beta, held over 60 days* is still strong (below) but is the top recipe in only 4 of the 9 books (Graded Bets, Conviction Tilt, Sector Balanced, Calm Weighted) and ranks 1st to 5th in all of them. What is shared is the family: in 7 of 9 books (all but Graded Bets and Sector Balanced, whose third-best is a thin low-beta short) the three best recipes are all **long positions held over 60 days in low- or mid-beta stocks**. Together those long, long-hold, low-or-mid-beta recipes are 18-24% of the money and 43-57% of the alpha.

  | Long, thin, low-beta, held >60d | rank by alpha | % of money | % of alpha | bp per $ | t | quarters + | real $ |
  |---|---|---|---|---|---|---|---|
  | Extreme Picks | 5th | 2.2% | 8.3% | 330 | 2.4 | 59% | $1.51M |
  | Graded Bets | 1st | 2.1% | 14.5% | 411 | 4.1 | 72% | $1.79M |
  | Size Balanced | 5th | 2.2% | 8.0% | 331 | 2.3 | 58% | $1.63M |
  | Conviction Tilt | 1st | 2.9% | 13.2% | 373 | 3.2 | 65% | $2.52M |
  | Sector Balanced | 1st | 2.8% | 15.4% | 433 | 3.7 | 66% | $2.20M |
  | Balanced Leverage | 2nd | 2.0% | 10.4% | 364 | 3.3 | 63% | $3.37M |
  | Calm Weighted | 1st | 2.0% | 10.0% | 351 | 3.2 | 62% | $3.46M |
  | Calm Rank Tilt | 3rd | 2.0% | 8.9% | 325 | 2.8 | 62% | $3.33M |
  | Market Exposed | 4th | 1.7% | 9.9% | 360 | 3.0 | 62% | $5.50M |

  It uses 1.7-2.9% of the money and earns 8-15% of the alpha at 325-433 bp per $. It is positive in 58-72% of quarters (it was 71-78% before the fixes), **negative in exactly one of the three periods in every strategy** (before: in all three periods in 6 of 9), and **positive in real dollars in all three periods in every strategy** ($1.5M to $5.5M).
* **The same win rate.** 51-53% of trades make money. The median trade earns 34 to 114 bp per $ (Graded Bets lowest at 34; Extreme Picks and Size Balanced highest at 114). These are close to coin-flips with a small positive tilt.
* **Both tails are huge and look alike.** The best 1% of trades earn 1.2x to 2.3x the strategy's total alpha, and the worst 1% lose 1.2x to 2.2x of it. The winners and losers are the same kind of trade: for Balanced Leverage the top 1% average beta 1.27, volatility rank 0.65, 69-day holds; the bottom 1% beta 1.31, volatility rank 0.67, 70-day holds, against 1.11 / 0.50 / 57 days for the average trade.
  So the profit is the small net difference between large winners and large losers drawn from the same pool of jumpy, high-beta, long-hold trades. (Calm Rank Tilt's extreme trades are calmer: beta 1.03 and 1.05, volatility rank 0.35 and 0.36.)
* **The same worst quarter and the same bad year.** 2009Q1 is the worst quarter for 8 of 9 strategies (Conviction Tilt: 2000Q4). 2009 is a losing calendar year for all nine; three books also lose a second year (Graded Bets 2001; Sector Balanced and Balanced Leverage 2003).
* **The most consistent loser: high-beta shorts.** Shorting high-beta stocks is 11-18% of every strategy's money and earns **no reliable alpha**: between -83 and +11 bp per $, t between -0.9 and -0.1 (negative alpha in 7 of the 9 books; Conviction Tilt +10 bp and Calm Rank Tilt +11 bp are the exceptions). It **loses real dollars in all three time periods in 8 of the 9 strategies** (2 of 3 in Calm Rank Tilt): -$3.5M to -$8.8M in the unlevered and levered books, -$22.3M in Market Exposed. These facts are the same as before the fixes; the alpha range moved from -41...+47 bp to -83...+11 bp.
  Shorting low- and mid-beta stocks, by contrast, earns 60-113 bp per $ in every strategy (t 1.5 to 3.5); before the fixes it was 71-108 bp with t of 2.2 to 3.4 in every strategy, so this side is weaker than it looked: t is above 2 in only four of the nine books (Extreme Picks, Graded Bets, Size Balanced, Conviction Tilt); Balanced Leverage, Sector Balanced and Market Exposed sit at 1.99-2.00, and Calm Weighted and Calm Rank Tilt are below (1.8, 1.6). Same side, same signal, different verdicts: beta is still the dividing line, but the low-beta short leg is now moderate evidence, not strong. (Table: `results/profiles_beta_legs.csv`.)
* **All shorts lose real dollars, even the good ones.** The short leg as a whole loses money in absolute terms in every strategy (-82 to -294 bp per $; -$6.6M to -$16.0M, -$49.2M in Market Exposed), and the low- and mid-beta shorts alone lose real dollars (-27 to -248 bp per $), in all three periods in 7 of the 9 books. The average announcing stock moves up relative to the market, so a short loses in real dollars even when it correctly picks the worse of two announcers. The longs more than make it up. The short leg's job is to hedge, and it is worth keeping only while its alpha (60-113 bp on low/mid-beta shorts) beats that drag and the borrow fees that are not modelled.
* **Longs in high-beta stocks are unproven too** (alpha -16 to +64 bp, t 0.5 to 1.1; negative alpha in Extreme Picks, Graded Bets and Size Balanced) but profitable in real dollars in all nine books (+$2.6M to +$20.1M), so the problem is concentrated on the short side.
* **Costs take 18-26% of the signal.** Graded Bets is again the highest (26%), because it trades the most positions; Size Balanced is the lowest (18%).

### Is the calm-stock advantage really a beta advantage? (`scripts/beta_vol_grid.py`, Balanced Leverage)

The Calm strategies size by stock-specific volatility. SITUATIONS.md raised the question whether "calm stocks earn more" is really "low-beta stocks earn more", because 56% of the jumpiest third of stocks are also in the highest-beta third (against 13% of the calmest third; confirmed here with the corrected data). The table splits every Balanced Leverage position by both labels at once: alpha in bp per $ (size-matched), with the share of the book's money and the t-statistic in brackets.

| Volatility (rows) / beta (columns) | low beta | mid beta | high beta | all betas |
|---|---|---|---|---|
| calm | 125 (17.9% of money, t 6.9) | 43 (12.0%, t 2.0) | 46 (4.7%, t -0.6) | 86 (34.5%, t 6.4) |
| average | 123 (9.9%, t 4.3) | 54 (12.5%, t 2.5) | 63 (10.5%, t 1.0) | 77 (32.8%, t 5.0) |
| jumpy | 149 (4.5%, t 2.8) | 96 (9.8%, t 1.2) | **-23** (18.3%, t -0.8) | 37 (32.6%, t 1.4) |
| all volatilities | 128 (32.2%, t 6.5) | 62 (34.3%, t 3.6) | 13 (33.5%, t -0.1) | 68 |

Paired by quarter (bp per $, t): calm minus jumpy **holding beta fixed** is -18 (t -0.4) for low-beta, +45 (t 0.6) for mid-beta and -32 (t -0.3) for high-beta -- no consistent sign and nothing near significance -- while calm minus jumpy ignoring beta is +59 (t 1.9, borderline). Low-beta minus high-beta **holding volatility fixed** is +170 (t 1.8) among calm stocks, +24 (t 0.3) among average ones and +167 (t 2.6) among jumpy ones; ignoring volatility it is +121 (t 2.8).

**Conclusion supported by the corrected data:** the calm-stock edge is carried by beta, not by calmness. Once beta is held fixed the calm-versus-jumpy difference changes sign and is nowhere distinguishable from zero, while the beta effect stays large in two of three volatility rows. The only losing cell is jumpy *and* high-beta (18% of the money, -23 bp). I do not have a pre-fix version of this grid, so I cannot say whether the numbers moved; the direction matches the sentence already in SITUATIONS.md that jumpiness has no independent effect once beta is controlled. Caveats: one book, terciles of both labels, and the quarter-clustered t-statistics are weighted by quarter, so a small cell can have a positive dollar-weighted alpha and a negative t (calm / high-beta). The practical reading, a hypothesis not a result: the Calm strategies may work mainly by shrinking the high-beta names, and a plain beta rule would be the cleaner way to test it.

## Strategy cards

Figures: **Alpha/Share** = recipe's share of the strategy's total alpha. **bp** = alpha per $ traded. **q+** = share of quarters the recipe made money. **Real $** = actual profit and loss of the trades in the recipe, including the market-relative drift on the short side. "Periods" are 1996-2001, 2002-2007 and 2008-2013. "Losing recipes" counts recipes (of 36) with negative total alpha.

### Extreme Picks
3.3% return, 5.2% vol, Sharpe 0.65, worst drop -25.2%. Sharpe by period 0.86 / 1.62 / -0.11. Alpha vs market 3.7% (t 2.9, beta -0.01). 38,435 positions (~2,100 a year). Win rate 53%, median trade +114 bp. 80% of quarters profitable.

**Where it profits most**
1. Long, liquid, low-beta, held >60d: 6.2% of the money, 12.6% of the alpha, 174 bp, t 2.5, 58% of quarters, real $2.16M.
2. Long, mid-liquidity, low-beta, held >60d: 2.6%, 12.5%, 413 bp, t 3.1, 64% of quarters, real $1.90M.
3. Long, liquid, mid-beta, held >60d: 8.4%, 10.3%, 107 bp, t 2.3, real $1.80M.
(Before the fixes the thin, low-beta recipe led this book at 490 bp; it is now 5th at 330 bp.) Longs earn 123 bp per $ ($8.6M) against 44 bp for shorts ($2.7M).

**How it loses, consistently**
* Short, mid-liquidity, high-beta, short hold: 2.5% of the money, -199 bp, t -1.9, positive in only 43% of quarters, **negative alpha in all three periods and negative real dollars in all three**, real -$1.27M.
* Short, liquid, high-beta, short hold: 4.9% of the money, -106 bp, losing alpha in 2 periods, real -$1.19M.
* Long, liquid, high-beta, held >60d: 8.0% of the money (the biggest weak pocket), -74 bp, t 0.6, negative in 2 periods, real dollars about break-even (-$0.05M).
* 9 of 36 recipes lose alpha, using 26% of the money (alpha -$3.16M, real -$4.42M); 8 of them lose in 2+ periods (24% of money, -$3.07M).

**Advantages:** simplest to explain; joint-highest median trade; strongest per-trade conviction (every trade is a top or bottom decile); zero market beta; its two surprise groups are both proven (long D10 t 3.6, short D01 t 2.4).
**Disadvantages:** 46% of the money is on the short side, which is the weak side (44 bp against 123 bp); no protection against high-beta shorts; concentrated (top 20 stocks = 18% of the alpha; best 10 quarters = 48%); lost money in 2009; the weakest recent period of the group (2008-13 Sharpe -0.11, alpha 0.0% a year); worst drop -25.2%, the deepest of the market-neutral books.

### Graded Bets
2.4% return, 3.9% vol, Sharpe 0.62, worst drop -19.5%. Sharpe by period 0.96 / 1.52 / -0.23. Alpha 2.6% (t 2.5). 172,914 positions (~9,600 a year). Win rate 51%, median trade +34 bp. 76% of quarters profitable.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.1% of the money, 14.5% of the alpha, 411 bp, t 4.1, 72% of quarters, real $1.79M.
2. Long, mid-liquidity, low-beta, held >60d: 1.6%, 11.1%, 406 bp, t 4.2, real $1.15M.
3. Short, thin, low-beta, short hold: 3.8%, 10.4%, 161 bp, t 2.2, but real **-$0.49M** (-100 bp), negative in real dollars in all three periods.
Longs earn 75 bp per $ ($5.1M), shorts 42 bp ($2.6M).

**How it loses, consistently**
* Short, thin, high-beta, held >60d: 2.0% of the money, -115 bp, real **-$1.22M (-459 bp)**, losing real dollars in all three periods.
* Long, liquid, high-beta, short hold: 3.7% of the money, -102 bp, t -0.9, negative in 2 periods (real dollars about break-even, -$0.05M).
* Short, thin, high-beta, short hold: 3.4% of the money, alpha +75 bp but real -$1.08M, losing real dollars in all three periods.
* 12 of 36 recipes lose alpha (the most of the nine), using 34% of the money (alpha -$1.99M, real -$1.74M); 7 of them in 2+ periods (22%, -$1.45M).
* Its bigger problem is dilution: 45% of its money sits in surprise groups with no proven edge (long D06, D07, D08; short D01, D03, D05), and those groups earn only 2% of the alpha. Long D06 and D07 reliably *lose* (-50 and -77 bp, t -3.6 and -2.5).

**Advantages:** most diversified (top 20 stocks only 13% of the alpha); the lowest volatility of the nine apart from Sector Balanced (3.9%); its D09/D10 longs and D02/D04 shorts are proven.
**Disadvantages:** lowest return (2.4%), lowest alpha per dollar (59 bp) and lowest median trade (34 bp) of the nine; highest cost drag (26% of the signal) from trading the most positions; negative 2008-13 Sharpe (-0.23); more losing recipes (12) than any other book; two losing years (2001 and 2009); its mild-surprise bets are mostly dead money or worse.

### Size Balanced
3.8% return, 4.8% vol, Sharpe 0.81, worst drop -18.1%. Sharpe by period 0.93 / 1.44 / 0.24. Alpha 4.1% (t 3.4). 38,435 positions. Win rate 53%, median +114 bp. 85% of quarters profitable (the most of the nine).

**Where it profits most**
1. Long, mid-liquidity, low-beta, held >60d: 2.3% of the money, 10.8% of the alpha, 420 bp, t 2.8, 62% of quarters, real $1.85M.
2. Long, liquid, mid-beta, held >60d: 7.0%, 10.2%, 134 bp, t 2.1, real $1.87M.
3. Long, mid-liquidity, mid-beta, held >60d: 2.4%, 8.8%, 332 bp, t 4.0, 72% of quarters, real $1.45M.
Highest alpha per dollar of the nine (91 bp) and the strongest long leg (129 bp, $8.7M; shorts 56 bp, $4.0M).

**How it loses, consistently**
* Short, liquid, high-beta, short hold: 6.0% of the money, -114 bp, t -1.5, real -$1.51M (-181 bp), losing real dollars in all three periods.
* Short, mid-liquidity, high-beta, short hold: 2.5% of the money, -207 bp, t -2.0, **negative alpha in all three periods and negative real dollars in all three**, real -$1.27M (-368 bp).
* Long, liquid, high-beta, held >60d: 6.5% of the money, -65 bp, negative alpha in all three periods (real dollars about zero, +$0.07M).
* 8 recipes lose, using 24% of the money (alpha -$2.77M, real -$3.70M); 7 of them in 2+ periods (22%).

**Advantages:** best alpha per dollar; the strongest long leg; the most consistent (85% of quarters profitable); short D01 is proven here (t 3.0, 66 bp).
**Disadvantages:** Sharpe is low (0.81); same exposure to high-beta shorts as Extreme Picks; 2008-13 Sharpe 0.24.

### Conviction Tilt
3.8% return, 4.0% vol, Sharpe 0.97, worst drop -14.5%. Sharpe by period 1.24 / 1.35 / 0.41. Alpha 4.0% (t 3.9). 89,686 positions (~5,000 a year). Win rate 52%, median +69 bp. 76% of quarters profitable.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.9% of the money, 13.2% of the alpha, 373 bp, t 3.2, 65% of quarters, real $2.52M.
2. Long, mid-liquidity, low-beta, held >60d: 1.7%, 9.3%, 435 bp, t 3.6, 73% of quarters, real $1.55M.
3. Long, thin, mid-beta, held >60d: 2.7%, 7.5%, 226 bp, t 2.0, real $2.11M.
Longs earn 111 bp per $ ($7.8M), shorts 54 bp ($4.3M).

**How it loses, consistently**
* Short, mid-liquidity, mid-beta, held >60d: 1.1% of the money, -198 bp, t -1.4, negative alpha and negative real dollars in all three periods, real -$0.57M.
* Short, mid-liquidity, high-beta, short hold: 2.6% of the money, -56 bp, t -1.1, losing alpha and real dollars in all three periods, real -$0.77M.
* Short, liquid, high-beta, short hold: 6.3% of the money, -21 bp, real -$0.86M, losing in 2 periods.
* 7 recipes lose alpha, using 17% of the money (alpha -$1.16M, real -$1.21M); 6 of them in 2+ periods (16%).
* 18% of the money is in surprise groups with no proven edge (long D07, D08; short D03, D04), which earn 8% of the alpha.

**Advantages:** dropping the unsure middle keeps the unproven share of money low (18%, against 45-47% for Graded Bets, Sector Balanced, Balanced Leverage and Calm Rank Tilt; only Calm Weighted, at 16%, is lower); alpha per dollar 81 bp; its longs D09 and D10 and shorts D01 and D02 are all proven.
**Disadvantages:** high-beta shorts still lose real dollars in every period (-$4.3M, 18% of its money, at +10 bp alpha); modest return (3.8%) and 2008-13 Sharpe only 0.41; size-neutral only, so industry concentration is uncontrolled (Sector Balanced exists to fix this).

### Sector Balanced
3.1% return, 2.9% vol, Sharpe 1.08, worst drop -11.4%. Sharpe by period 1.72 / 1.25 / 0.41. Alpha 3.4% (t 4.5). 114,623 positions (~6,400 a year). Win rate 52%, median +59 bp. 82% of quarters profitable.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.8% of the money, **15.4% of the alpha**, 433 bp, t 3.7, 66% of quarters, real $2.20M.
2. Long, liquid, mid-beta, held >60d: 5.4%, 10.1%, 145 bp, t 2.9, 68% of quarters, real $1.32M.
3. Short, thin, low-beta, short hold: 4.3%, 8.9%, 163 bp, t 2.5, 70% of quarters (but real -$0.33M, because the average announcer beats the market).
Longs earn 116 bp per $ ($6.7M), shorts 41 bp ($2.4M).

**How it loses, consistently**
* Short, thin, high-beta, held >60d: 2.1% of the money, -183 bp (t -0.2), real **-$1.16M (-470 bp)**, losing alpha and real dollars in 2 periods.
* Short, mid-liquidity, mid-beta, held >60d: 1.0% of the money, -227 bp, t -1.0, losing alpha and real dollars in all three periods, real -$0.45M.
* Short, mid-liquidity, high-beta, short hold: 2.5% of the money, -76 bp, real -$0.66M.
* 7 recipes lose alpha, using 18% of the money (alpha -$1.55M, real -$2.13M); 4 of them in 2+ periods (8% of money, -$1.10M).
* Its short D01 earns only 32 bp per $ (t 1.0, unproven); 47% of its money is in surprise groups with no proven edge (long D07, D08; short D01, D03, D04), earning 23% of the alpha.

**Advantages:** lowest volatility (2.9%) and shallowest drop (-11.4%) of the nine; the highest Sharpe of the unlevered books (1.08); the best sector spread (no sector is more than 12% of any quarter's new dollars, against 28% for Conviction Tilt and 33-34% for Extreme Picks and Size Balanced; `max_sector_share_of_quarter` in `profiles_timing.csv`).
**Disadvantages:** lowest return of the viable books (3.1%) because of the 1.5x borrowing limit; weak short leg (41 bp); depends on one recipe for 15% of the alpha from under 3% of the money; two losing years (2003 and 2009); 2008-13 Sharpe 0.41.

### Balanced Leverage (the reference book)
5.4% return, 4.9% vol, Sharpe 1.09, worst drop -18.6%. Sharpe by period 1.76 / 1.45 / 0.19. Alpha 5.8% (t 4.5). 109,970 positions (~6,100 a year). Win rate 52%, median +60 bp. 80% of quarters profitable.

**Where it profits most**
1. Long, liquid, mid-beta, held >60d: 5.9% of the money, 11.3% of the alpha, 131 bp, t 2.7, 65% of quarters, real $3.47M.
2. Long, thin, low-beta, held >60d: 2.0%, 10.4%, 364 bp, t 3.3, 63% of quarters, real $3.37M (585 bp in real dollars).
3. Long, mid-liquidity, low-beta, held >60d: 1.8%, 8.8%, 340 bp, t 3.5, real $2.62M.
(A liquid-stock recipe leads this book and the Calm books, which hold more in liquid names.) The long leg earns 103 bp per $ ($15.0M); the short leg 34 bp ($5.1M).

**How it loses, consistently**
* Short, liquid, high-beta, short hold: 6.3% of the money, -36 bp (t -0.9), real **-$1.99M (-106 bp)**, losing real dollars in 2 periods.
* Short, thin, high-beta, held >60d: 1.9% of the money, -113 bp, real -$2.13M (-377 bp).
* Short, mid-liquidity, mid-beta, held >60d: 1.1% of the money, -171 bp, t -1.0, losing alpha and real dollars in all three periods, real -$1.04M.
* 7 recipes lose alpha, using 19% of the money (alpha -$2.80M, real -$5.57M); 4 of them in 2+ periods (9% of money, -$1.44M). Worst quarter: 2009Q1, -$2.18M.
* Shorts in the largest companies earn -10 bp per $ against the size-matched benchmark.

**Advantages:** the best combination of size and robustness among the plain-sized books: $20.1M of size-matched alpha, 5.8% a year against the market (t 4.5), beta zero; 9 recipes proven at t >= 2.
**Disadvantages:** the short leg earns a third of the long leg and has unmodelled borrow fees (5% a year would take the return from 5.4% to 1.3%, per its report); 2009Q1 lost $2.2M; the fade (2008-13 Sharpe 0.19); a third of the money (33%) in high-beta stocks that earn only 14 bp per $ combined; two losing years (2003 and 2009).

### Calm Weighted
5.6% return, 4.5% vol, Sharpe 1.24, worst drop -17.5%. Sharpe by period 2.03 / 1.54 / 0.29. Alpha 6.1% (t 5.2). 110,649 positions. Win rate 52%, median +60 bp. 82% of quarters profitable.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.0% of the money, 10.0% of the alpha, 351 bp, t 3.2, 62% of quarters, real $3.46M.
2. Long, liquid, mid-beta, held >60d: 5.9%, 9.9%, 118 bp, t 2.9, real $3.35M.
3. Long, mid-liquidity, low-beta, held >60d: 1.9%, 8.8%, 326 bp, t 3.7, 66% of quarters, real $2.88M.
Long 103 bp ($15.4M), short 39 bp ($6.0M).

**How it loses, consistently**
* Short, thin, high-beta, held >60d: 1.8% of the money, -130 bp (t 0.0), real **-$2.13M (-389 bp)**, losing real dollars in 2 periods.
* Short, mid-liquidity, mid-beta, held >60d: 1.1% of the money, -189 bp, t -1.2, losing alpha and real dollars in all three periods, real -$1.16M.
* Short, thin, high-beta, short hold: 3.0% of the money, alpha +46 bp but real -$1.92M (-214 bp), losing real dollars in all three periods.
* 8 recipes lose alpha, using 19% of the money (alpha -$2.28M, real -$5.06M); 6 of them in 2+ periods (11%, -$1.38M). Worst quarter 2009Q1, -$1.81M.

**Advantages:** no fitted parameters; improves on Balanced Leverage in all three periods (Sharpe 2.03 / 1.54 / 0.29 vs 1.76 / 1.45 / 0.19); Sharpe 1.24; alpha t = 5.2; cheap to explain; only 16% of its money is in unproven surprise groups.
**Disadvantages:** the gain is mostly lower risk (alpha per dollar 71 bp vs 68 bp for Balanced Leverage) and may partly be a beta effect (see the volatility-by-beta table); same 2009Q1 vulnerability; 2008-13 Sharpe only 0.29.

### Calm Rank Tilt (research)
6.0% return, 4.0% vol, Sharpe 1.47, worst drop -13.6%. Sharpe by period 2.31 / 1.63 / 0.51. Alpha 6.5% (t 6.4). 111,802 positions. 80% of quarters profitable. Win rate 52%, median +59 bp.

**Where it profits most**
1. Long, liquid, low-beta, held >60d: 6.1% of the money, 11.7% of the alpha, 142 bp, t 2.2, 61% of quarters, real $4.45M.
2. Long, mid-liquidity, low-beta, held >60d: 2.2%, 9.4%, 320 bp, t 3.4, 65% of quarters, real $3.33M.
3. Long, thin, low-beta, held >60d: 2.0%, 8.9%, 325 bp, t 2.8, real $3.33M.
One of the more balanced books on the long/short split: long 101 bp ($15.3M), short 49 bp ($7.5M), a ratio of 2.1 (only Graded Bets, at 1.8, is closer); its short D01 is a proven engine (105 bp, t 3.0; t 3.4 before the fixes) and is the only book where short D01 clearly beats D02-D04.

**How it loses, consistently**
* It loses less than the others, but not "barely": 6 recipes lose alpha, using 8% of the money (alpha -$1.88M, real -$4.06M); 4 of them in 2+ periods (5%, -$1.62M).
* Short, mid-liquidity, mid-beta, held >60d: 1.2% of the money, -279 bp, t -1.7, losing alpha and real dollars in all three periods, real -$1.53M.
* Short, thin, high-beta, held >60d: 1.5% of the money, -55 bp, real -$1.34M.
* Worst quarter 2009Q1, -$0.51M (against -$2.18M for Balanced Leverage). Only losing year: 2009. Its longest losing streak, **4 quarters, is the longest of the nine** (2-3 for the others).

**Advantages:** best Sharpe (1.47), best alpha t (6.4), best in every period; the smallest worst quarter; the smallest share of money in losing recipes (8%); only 22% of its money in high-beta names (30-34% in the other eight books).
**Disadvantages:** research status (the tilt strength was chosen after seeing the results, and the corrected book was not re-selected); not the shallowest drop (-13.6% vs -11.4% for Sector Balanced); 57% of the money is in calm stocks, so it is concentrated and its capacity is limited; the jumpy stocks it still holds (8% of money) earn about the same per dollar as the calm ones (74 vs 73 bp, all-announcer benchmark), so there is no sign any more that it shrank them too far; return is only 6.0%.

### Market Exposed (cautionary)
6.6% return, 9.5% vol, Sharpe 0.72, worst drop -34.1%. Sharpe by period 1.42 / 1.10 / 0.04. Alpha 5.2% (t 2.5), with beta 0.21. Win rate 52%, median +60 bp. 80% of quarters profitable.

**Where it profits most**
1. Long, liquid, mid-beta, held >60d: 7.0% of the money, 11.6% of the alpha, 105 bp, t 2.3, 66% of quarters, **real $10.43M (425 bp)**.
2. Long, liquid, low-beta, held >60d: 5.6%, 11.2%, 127 bp, t 2.1, 52% of quarters, real $7.76M.
3. Long, mid-liquidity, low-beta, held >60d: 1.8%, 10.4%, 366 bp, t 3.8, real $4.89M (773 bp).
Longs earn 92 bp per $ ($16.6M), shorts 33 bp ($5.6M).

**How it loses, consistently**
* Short, thin, high-beta, short hold: 3.3% of the money, alpha +41 bp, but real **-$4.78M (-410 bp)**, losing real dollars in all three periods.
* Short, thin, high-beta, held >60d: 2.1% of the money, -86 bp, real **-$4.72M (-647 bp)**, losing real dollars in all three periods.
* Short, mid-liquidity, high-beta, short hold: 2.9% of the money, -82 bp, real -$4.11M (-411 bp), all three periods.
* 9 recipes lose alpha, using 23% of the money (alpha -$3.54M, real -$9.44M); 5 of them in 2+ periods (12%, -$2.46M). **15 of 36 recipes lose real dollars in all three periods, using 38% of the money.** Worst quarter 2009Q1, -$2.93M (the deepest of the group); longest losing streak 2 quarters.

**Advantages:** highest raw return of the nine (6.6%); the liquid mid-beta long recipe makes $10.4M.
**Disadvantages:** its 2008-13 Sharpe is 0.04 and alpha there is -0.2% a year; the extra return comes mostly from market exposure (beta 0.21), not skill; much higher volatility (9.5%) and a -34.1% drop; the largest real-dollar losses on high-beta shorts (-$22.3M), because raw returns leave market moves in.

## Reading the cards together

* The strategies differ much less in *where* they profit than in *how much of their money goes to dead or losing trades*. Between 66% and 80% of every strategy's money sits in recipes that are not individually proven (t < 2); those recipes still earn 19% to 47% of the alpha, so they are not worthless, just unproven one by one.
* The earlier claim that the progression Extreme Picks -> Conviction Tilt -> Sector Balanced -> Balanced Leverage -> Calm Weighted -> Calm Rank Tilt steadily cuts the number of losing recipes (8 to 3) does not survive the fixes. Losing recipes now go 9 (26% of the money), 7 (17%), 7 (18%), 7 (19%), 8 (19%), 6 (8%): the share of money in losing recipes falls only at the last step, Calm Rank Tilt.
* High-beta shorts are the one pocket with no reliable alpha and real-dollar losses in all three periods (8 of 9 strategies), which is why a beta rule (for example, no shorts in the highest-beta third) is the highest-value untested idea. It is untested: removing those positions also changes the hedge and the book's market exposure.
* The volatility-by-beta table above says the second-highest-value idea is to test the Calm strategies' advantage against a plain beta rule.
