# Strategy profiles: where each equity strategy wins, where it loses, and why

Generated from `results/profiles_recipes.csv`, `profiles_trades.csv`, `profiles_timing.csv` (code: `scripts/strategy_profiles.py`).
Sample 1996-2013, 72 announcement quarters. Read `docs/SITUATIONS.md` first for how to read the terms.

## Method (and one correction to earlier numbers)

A **recipe** is a specific kind of trade: side (long / short) x how easily the stock trades (thin / mid / liquid) x beta (low / mid / high) x hold (short <= 60 days / long > 60 days) -- 36 recipes per strategy.

**Alpha here is measured against the average announcer of the same size group in the same quarter** (a "size-matched" benchmark), not against the average of all announcers. The older benchmark makes large stocks look like losers on the long side and winners on the short side
(big stocks lag the average announcer, which is dominated by small caps), so it gives wrong answers whenever side and size are mixed in one cut. In real dollars the long leg of liquid stocks *makes* +133 bp per $ while the old benchmark said -38 bp. Single-dimension findings in SITUATIONS.md hold up under the size-matched benchmark
(thin: 153 vs 136 bp; low-beta 137 vs 129; smallest companies 260 vs 203). The by-leg conclusion does not, see below.

**Correction:** earlier docs said the surprise signal earns the same on the long and short legs. Against a size-matched benchmark the long leg earns about **twice** the short leg in every strategy (e.g. Balanced Leverage: long 108 bp per $, $18.2M; short 50 bp, $8.6M). Within every size group the long leg is ahead (smallest: 339 vs 205 bp; largest: 62 vs -6 bp). The short leg also carries unmodelled borrow fees.

## What every strategy has in common

* **The same single best trade.** *Long, thin-trading, low-beta, held over 60 days.* It is 2-3% of the money, earns 10-21% of the alpha at 406-648 bp per $, is positive in 71-78% of quarters, and is positive in all three time periods in 6 of the 9 strategies (negative in one period in Calm Weighted, Calm Rank Tilt and Market Exposed).
  Real dollars: $2.8M to $8.0M per strategy. (Table below.)
* **The same win rate.** 51-53% of trades make money. Median trade earns 39 to 117 bp per $. These are close to coin-flips with a small positive tilt.
* **Both tails are huge and look alike.** The best 1% of trades earn 1.0x to 2.0x the strategy's total alpha, and the worst 1% lose 1.1x to 1.9x of it. The winners and losers are the same kind of trade: for Balanced Leverage the top 1% average beta 1.26, volatility rank 0.65, 81-day holds; the bottom 1% beta 1.31, volatility rank 0.68, 78-day holds, against 1.11 / 0.50 / 58 days for the average trade.
  So the profit is the small net difference between large winners and large losers drawn from the same pool of jumpy, high-beta, long-hold trades. (Calm Rank Tilt's extreme trades are calmer: beta 1.02 and 1.07, volatility rank 0.36 for both.)
* **The same worst quarter and the same bad year.** 2009Q1 is the worst quarter for 8 of 9 strategies (Graded Bets: 2000Q4). 2009 is the only losing calendar year, for all nine.
* **The same consistent loser: high-beta shorts.** Shorting high-beta stocks is 11-18% of every strategy's money, earns **no alpha** (between -41 and +47 bp per $, t about 0 in all nine), and **loses real dollars in all three time periods in 8 of the 9 strategies** (2 of 3 in Calm Rank Tilt): -$3.3M to -$8.3M in the unlevered and levered books, -$21.6M in Market Exposed.
  Shorting low- and mid-beta stocks, by contrast, earns 71-108 bp per $ (t 2.2 to 3.4) in every strategy. Same side, same signal, opposite verdicts: beta is the dividing line. (Table: `results/profiles_beta_legs.csv`.)
* **All shorts lose real dollars, even the good ones.** The short leg loses money in absolute terms in every strategy (-54 to -241 bp per $ even for low- and mid-beta shorts), because the average announcing stock beats the market. The longs more than make it up. The short leg's job is to hedge, and it is worth keeping only while its alpha (70-108 bp) beats that drag and the borrow fees that are not modelled.
* **Longs in high-beta stocks are unproven too** (alpha 23-72 bp, t 0.5 to 1.3) but profitable in real dollars (+$3.7M to +$23M), so the problem is concentrated on the short side.
* **Costs take 16-22% of the signal.** Graded Bets is the highest (22%), because it trades the most positions.

## Strategy cards

Figures: **Alpha/Share** = recipe's share of the strategy's total alpha. **bp** = alpha per $ traded. **q+** = share of quarters the recipe made money. **Real $** = actual profit and loss of the trades in the recipe, including the market-relative drift on the short side.

### Extreme Picks
4.9% return, 5.3% vol, Sharpe 0.93, worst drop -18.5%. Sharpe by period 1.28 / 1.58 / 0.28. Alpha vs market 5.2% (t 4.0, beta 0.0). 34,343 positions (~1,900 a year). Win rate 53%, median trade +117 bp.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.2% of the money, 10.8% of the alpha, 490 bp, t 4.8, 71% of quarters, real $2.84M.
2. Long, thin, mid-beta, held >60d: 2.4%, 10.3%, 422 bp, t 2.0, real $2.86M.
3. Long, mid-liquidity, low-beta, held >60d: 1.9%, 8.8%, 456 bp, t 3.2, real $2.07M.
Longs earn 140 bp per $ against 54 bp for shorts.

**How it loses, consistently**
* Short, mid-liquidity, high-beta, short hold: 2.5% of the money, -110 bp, t -2.2, positive in only 43% of quarters, **losing in all three periods**, real -$1.02M.
* Short, liquid, high-beta, short hold: 5.1% of the money, -69 bp, losing in 2 periods, real -$1.20M.
* 8 of 36 recipes lose alpha, using 24% of the money (-$2.31M); 5 of them lose in 2+ periods (13% of money, -$1.62M).

**Advantages:** simplest to explain; joint-highest median trade; strongest per-trade conviction (every trade is a top or bottom decile); zero market beta.
**Disadvantages:** half the money is on the short side, which is the weak side; no protection against high-beta shorts; concentrated (top 20 stocks = 19% of the alpha; best 10 quarters = 47%); lost money in 2009; the weakest recent period of the group (2008-13 Sharpe 0.28); worst drop -18.5%.

### Graded Bets
3.9% return, 3.9% vol, Sharpe 1.01, worst drop -14.0%. Sharpe by period 1.39 / 1.55 / 0.32. Alpha 4.1% (t 4.1). 173,404 positions (~9,600 a year). Win rate 51%, median trade +39 bp.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.2% of the money, 15.0% of the alpha, 522 bp, t 6.5, 76% of quarters, real $3.22M.
2. Long, thin, mid-beta, held >60d: 2.0%, 9.2%, 347 bp, t 3.5, real $2.45M.
3. Long, mid-liquidity, low-beta, held >60d: 1.4%, 8.0%, 438 bp, t 4.1, real $1.48M.

**How it loses, consistently**
* Short, mid-liquidity, high-beta, short hold: 2.3% of the money, -29 bp, real -$0.69M (-173 bp), losing real dollars in all three periods.
* Long, liquid, high-beta, short hold: 5.9% of the money, -45 bp alpha (but positive in real dollars, +$0.37M).
* Its bigger problem is dilution: 42% of its money sits in surprise groups with no proven edge (long D06, D07, D09, short D01, D05).
* 5 recipes lose, using 18% of the money (-$0.97M).

**Advantages:** most diversified (top 20 stocks only 11% of the alpha); 79% of quarters profitable; lowest volatility of the first five books; fewest losing recipes of them (5).
**Disadvantages:** lowest alpha per dollar (60 bp); lowest median trade (39 bp); highest cost drag (22% of the signal) from trading the most positions; hardest hit by the fade (2008-13 Sharpe 0.32); its mild-surprise bets are mostly dead money.

### Size Balanced
4.7% return, 5.3% vol, Sharpe 0.91, worst drop -17.5%. Sharpe by period 1.32 / 1.28 / 0.34. Alpha 5.0% (t 3.9). 34,343 positions. Win rate 53%, median +117 bp.

**Where it profits most**
1. Long, thin, mid-beta, held >60d: 2.5% of the money, 11.9% of the alpha, 500 bp, t 2.1, real $3.04M.
2. Long, thin, low-beta, held >60d: 2.4%, 11.9%, 529 bp, t 4.9, real $2.98M.
3. Long, mid-liquidity, low-beta, held >60d: 2.0%, 11.5%, 622 bp (the highest per dollar of any third-ranked recipe), t 3.1, real $2.45M.
Highest alpha per dollar of the group (97 bp) and the strongest long leg (165 bp).

**How it loses, consistently**
* Short, mid-liquidity, high-beta, short hold: 2.3% of the money, -138 bp, t -2.1, **losing in all three periods**, real -$0.99M (-280 bp).
* Short, liquid, high-beta, short hold: 6.1% of the money, -90 bp, real -$1.54M, losing real dollars in all three periods.
* 7 recipes lose, using 21% of the money (-$2.49M); 6 of them in 2+ periods.

**Advantages:** best alpha per dollar; the strongest long leg; in the earlier all-announcer cut it was the only one of the first five books whose short D01 group was individually proven (t 2.5).
**Disadvantages:** Sharpe is the lowest (0.91); same exposure to high-beta shorts as Extreme Picks; 2008-13 Sharpe 0.34.

### Conviction Tilt
5.2% return, 4.4% vol, Sharpe 1.18, worst drop -13.1%. Sharpe by period 1.74 / 1.31 / 0.60. Alpha 5.4% (t 4.7). Win rate 52%, median +67 bp.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.9% of the money, 17.0% of the alpha, 539 bp, t 5.9, 76% of quarters, real $5.07M.
2. Long, thin, mid-beta, held >60d: 2.9%, 13.1%, 413 bp, t 3.2, real $4.43M.
3. Long, mid-liquidity, low-beta, held >60d: 1.5%, 8.6%, 542 bp, t 3.8, real $2.21M.

**How it loses, consistently**
* Short, mid-liquidity, high-beta, short hold: 2.3% of the money, -36 bp (t -0.7), but real -$0.89M (-187 bp), losing real dollars in all three periods.
* Long, liquid, high-beta, short hold: 4.5% of the money, -23 bp, losing in 2 periods but profitable in real dollars (+$0.62M).
* 7 recipes lose, using 20% of the money (-$0.75M, the smallest loss pool of the unlevered books).
* 23% of the money is in surprise groups with no proven edge (long D07, short D01).

**Advantages:** dropping the unsure middle works: 79% of quarters profitable; best of the first five books in 2008-13 (Sharpe 0.60).
**Disadvantages:** still has high-beta shorts that lose real dollars in every period; returns are modest (5.2%); size-neutral only, so industry concentration (up to 29% in one sector) is uncontrolled.

### Sector Balanced
4.1% return, 3.0% vol, Sharpe 1.33, worst drop -10.8%. Sharpe by period 2.19 / 1.34 / 0.66. Alpha 4.2% (t 5.5). Win rate 52%, median +67 bp.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 3.1% of the money, **20.7% of the alpha**, 648 bp, t 6.6, 78% of quarters, real $3.94M.
2. Long, thin, mid-beta, held >60d: 2.5%, 8.3%, 316 bp, t 2.1, real $2.19M.
3. Short, thin, low-beta, short hold: 3.5%, 6.4%, 177 bp, t 2.3 (but real -$0.43M, because the average announcer beats the market).

**How it loses, consistently**
* Short, thin, high-beta, held >60d: 2.6% of the money, -49 bp, real -$1.15M (-330 bp), losing real dollars in 2 periods.
* Long, liquid, high-beta, short hold: 4.3% of the money, -49 bp, negative alpha in 2 periods.
* 6 recipes lose, using 14% of the money (-$1.23M).
* Its shorts are the weakest in the group: short D01 earns only 16 bp per $. 43% of its money is in surprise groups with no proven edge.

**Advantages:** lowest volatility (3.0%) and shallowest drop (-10.8%); the highest Sharpe of the unlevered books; strongest dependence on one very reliable recipe (a fifth of the alpha from 3% of the money).
**Disadvantages:** lowest return of the viable books (4.1%) because of the 1.5x borrowing limit; weakest short leg; dependence on a single recipe is a concentration risk if that edge fades; 2009 was the only losing year.

### Balanced Leverage (the reference book)
6.5% return, 4.9% vol, Sharpe 1.30, worst drop -16.6%. Sharpe by period 2.07 / 1.52 / 0.47. Alpha 6.7% (t 5.3). Win rate 52%, median +67 bp. ~6,600 positions a year.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.1% of the money, 14.1% of the alpha, 530 bp, t 5.8, 76% of quarters, real $5.76M (807 bp in real dollars).
2. Long, liquid, mid-beta, held >60d: 3.5% of the money, 7.9%, 178 bp, t 2.0, real $3.00M. (A liquid-stock recipe makes the top three here and in the Calm books, which hold more in liquid names.)
3. Long, thin, mid-beta, held >60d: 2.1%, 7.9%, 290 bp, t 2.4, real $4.23M.
The long leg earns 108 bp per $ ($18.2M); the short leg 50 bp ($8.6M).

**How it loses, consistently**
* Short, liquid, high-beta, held >60d: 2.4% of the money, -67 bp, real -$1.36M (-169 bp).
* Long, liquid, high-beta, short hold: 4.8% of the money, -40 bp (t -0.8), negative alpha in 2 periods (still +$0.54M in real dollars).
* Only 4 recipes lose alpha, using 10% of the money; 3 of them lose in 2+ periods (8% of money, -$1.71M). Worst quarter: 2009Q1, -$2.39M.
* Shorts in the largest companies earn -6 bp per $ before borrow fees.

**Advantages:** the best combination of size and robustness among the proven books: $24.9M of alpha, t = 5.3, beta zero; few losing recipes; 9 recipes proven at t >= 2.
**Disadvantages:** the short leg is half as good as the long leg and has unmodelled borrow fees; 2009Q1 lost $2.4M; the fade (2008-13 Sharpe 0.47); a third of the money in high-beta stocks that earn about 28 bp per $.

### Calm Weighted
6.7% return, 4.5% vol, Sharpe 1.47, worst drop -15.6%. Sharpe by period 2.40 / 1.63 / 0.59. Alpha 6.9% (t 6.1). Win rate 52%.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.1% of the money, 13.0% of the alpha, 489 bp, t 5.4, 75% of quarters, real $5.69M.
2. Long, thin, mid-beta, held >60d: 2.2%, 7.2%, 268 bp, t 2.2, real $4.20M.
3. Long, liquid, mid-beta, held >60d: 3.4%, 7.0%, 166 bp, t 2.1, real $2.79M.
Long 108 bp, short 55 bp.

**How it loses, consistently**
* Long, liquid, high-beta, short hold: 4.1% of the money, -30 bp, losing in 2 periods (profitable in real dollars, +$0.60M).
* Short, liquid, mid-beta, short hold: 6.5% of the money, alpha +22 bp but **real -$0.81M (-36 bp)**, losing real dollars in 2 periods.
* 5 recipes lose, using 11% of the money (-$1.63M). Worst quarter 2009Q1, -$1.97M.

**Advantages:** no fitted parameters; improves on Balanced Leverage in all three periods; Sharpe 1.47; alpha t = 6.1; cheap to explain.
**Disadvantages:** the gain is mostly lower risk (alpha per dollar 76 vs 73 bp); may really be a beta effect in disguise (see SITUATIONS.md); same 2009Q1 vulnerability.

### Calm Rank Tilt (research)
7.1% return, 3.8% vol, Sharpe 1.81, worst drop -11.6%. Sharpe by period 2.94 / 1.89 / 0.78. Alpha 7.3% (t 7.6). 85% of quarters profitable. Win rate 52%.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 2.1% of the money, 10.1% of the alpha, 406 bp, t 4.1, 71% of quarters, real $5.00M.
2. Long, liquid, low-beta, held >60d: 3.4%, 7.5%, 190 bp, t 2.2, real $3.55M.
3. Long, thin, mid-beta, held >60d: 2.2%, 6.8%, 270 bp, t 1.6, real $4.09M.
The most balanced of all: long 105 bp, short 65.5 bp per $ (in the earlier all-announcer cut it is also the only book where short D01 is a proven engine, t 3.4).

**How it loses, consistently**
* It barely does. Only 3 recipes lose alpha, using 5% of the money (-$0.58M), and the worst are about break-even (+/-10 bp).
* Short, liquid, mid-beta, held >60d: 2.4% of the money, +10 bp but real -$0.45M, losing real dollars in all three periods.
* Worst quarter 2009Q1, -$1.01M (against -$2.39M for Balanced Leverage). Only losing year: 2009.

**Advantages:** best Sharpe, best drawdown, best in every period; the most consistent (85% of quarters); the most balanced between longs and shorts; fewest losing recipes.
**Disadvantages:** research status (the tilt strength was chosen after seeing the results); 57% of the money is in calm stocks, so it is concentrated and its capacity is limited; the jumpy stocks it still holds earn *more* per dollar (120 vs 77 bp), a hint that it may have shrunk them too far; return is only 7.1%.

### Market Exposed (cautionary)
7.3% return, 6.6% vol, Sharpe 1.11, worst drop -15.5%. Sharpe by period 1.79 / 1.48 / 0.32. Alpha 5.8% (t 4.0), but beta 0.18 and 0.52 correlation with the market. Win rate 52%.

**Where it profits most**
1. Long, thin, low-beta, held >60d: 1.9% of the money, 13.1% of the alpha, 527 bp, t 5.7, 76% of quarters, **real $8.03M (1,165 bp)**.
2. Long, mid-liquidity, low-beta, held >60d: 1.4%, 7.5%, 390 bp, t 3.9, real $4.59M.
3. Long, thin, mid-beta, held >60d: 1.9%, 7.1%, 282 bp, t 2.3, real $6.49M.

**How it loses, consistently**
* Short, liquid, high-beta, held >60d: 2.2% of the money, **real -$3.18M (-395 bp)**, losing real dollars in all three periods.
* Short, mid-liquidity, high-beta, short hold: 2.6% of the money, **real -$3.22M (-332 bp)**, losing in all three periods.
* Long, liquid, high-beta, short hold: 5.2% of the money, -42 bp alpha.
* 6 recipes lose, using 20% of the money. Worst quarter 2009Q1, -$2.76M (the deepest of the group); longest losing streak 2 quarters.

**Advantages:** highest raw return (7.3%); the long-thin-low-beta recipe makes $8M.
**Disadvantages:** the extra return comes mostly from market exposure, not skill; much higher volatility (6.6%); weak in 2008-13 (Sharpe 0.32); the largest real-dollar losses on high-beta shorts, because raw returns leave market moves in.

## Reading the cards together

* The strategies differ much less in *where* they profit than in *how much of their money goes to dead or losing trades*. Between 72% and 86% of every strategy's money sits in recipes that are not individually proven (t < 2); those recipes still earn 40% to 54% of the alpha, so they are not worthless, just unproven one by one.
* The progression Extreme Picks -> Conviction Tilt -> Sector Balanced -> Balanced Leverage -> Calm Weighted -> Calm Rank Tilt cuts the number of losing recipes from 8 (24% of the money) to 3 (5% of the money).
* High-beta shorts are the one pocket with no alpha and real-dollar losses in all three periods (8 of 9 strategies), which is why a beta rule (for example, no shorts in the highest-beta third) is the highest-value untested idea. It is untested: removing those positions also changes the hedge and the book's market exposure.
