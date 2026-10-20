# options_cap30_midfill

_options strategy | family: options-kelly | status: reference | derived from `options_cap20_midfill`_

## What this strategy does

options_cap30 with the previous convention: mid fills, flat 3% + >= $0.05/share cost

**Why it should work.** Buy the contract whose payoff profile best matches the SUE-conditioned return distribution, sized by Kelly, spread across hundreds of independent events.

**Design notes** (from the strategy's source file).

Options strategies -- Kelly-optimal contract selection around earnings.

WHAT IT DOES. For every earnings announcement, look at the whole option chain (every strike, calls AND puts, ~13 contracts/event on average, up to 60) on the announcement day and buy the single contract with the highest expected LOG growth under an empirical, walk-forward distribution of the stock's 60-day return conditional on its SUE decile. The direction is never hard-coded: the math picks puts for bad surprises and calls for good ones on its own (32% puts in D1 vs 5% in D10).

WHY KELLY AND NOT EXPECTED VALUE. A naive "maximize E[return]" selector loves deep-OTM lottery tickets (huge payoff x tiny probability = big mean) that lose money on almost every draw. Scoring each contract by max over f of E[log(1 + f*R)] penalizes ruin: a lottery ticket is still scored, it just earns a tiny optimal fraction f* and negligible growth. The selector therefore prefers contracts whose payoff is large exactly in the scenarios the SUE decile makes likely.

HOW IT IS SIZED. Only events whose best contract has positive expected log growth are traded. Each such event's size is HALF its own Kelly fraction (full Kelly over-bets when the distribution is itself an estimate), then the quarter's candidates share a premium budget (`max_premium_frac` of trailing NAV minus premium still outstanding from earlier quarters) in proportion to those fractions.

WHERE THE RETURN COMES FROM (see the generated report): OTM calls on low-volatility, high-SUE stocks -- the post-announcement upside tail is under-priced relative to the drift -- diversified across ~1,200 mostly independent single-stock bets per quarter (Grinold's breadth), which is what turns a modest per-trade edge into a Sharpe of 3+.

THE BACKTEST CONVENTION THAT WAS WRONG, AND THE FIX. The original backtest valued a position at each checkpoint (+1, 5, 10, 20, 40, 60 trading days) from the contract's mid quote and, when the quote was missing, CARRIED THE PREVIOUS QUOTE FORWARD. 22% of picks have no quote at 60 days, and 93% of those are not missing at all: the bid is ZERO (best_offer ~ $0.10) -- the option has died and nobody will buy it. Carrying its last live price forward never marks a dying option down. The strategies below value a zero-bid checkpoint at half the ask (or at zero, for the conservative bound) using the quotes now kept by the forward-price prep stage. Effect on the 20%-premium-cap book: 8.58% / Sharpe 3.78 as originally reported -> 5.87% / 2.50 with the corrected mark -> 4.12% / 1.77 once each contract also pays at least one $0.05 tick round trip -> 2.34% / 0.99 if dead options are worth nothing.

REMAINING CAVEATS: no market impact; the early walk-forward distributions rest on only 8 quarters of history; ~4,600 trades a year would meet real capacity limits; most of the profit comes from 2003 and 2013.

| parameter | value |
|---|---|
| hold_horizon | 60 |
| half_kelly | 0.5 |
| max_premium_frac | 0.3 |
| roundtrip_cost_pct | 0.03 |
| dead_quote_mark | zero_bid_half_ask |
| fill_mode | mid |
| min_roundtrip_cost_per_share | 0.05 |
| min_premium | 0.0 |

## Performance

| metric | options_cap30_midfill | market (CRSP VW) |
|---|---|---|
| Annualized return | 6.10% | 8.48% |
| Annualized volatility | 3.70% | 19.86% |
| Sharpe (NAV-pct) | 1.62 | 0.51 |
| Sortino | 2.66 |  |
| Max drawdown | -22.5% | -55.5% |
| Calmar | 0.27 |  |
| Final NAV ($10M start) | $26,857,603 |  |
| Worst day | -1.59% |  |
| % positive days | 46.7% |  |
| Sharpe 1996-2001 | 0.57 |  |
| Sharpe 2002-2007 | 1.91 |  |
| Sharpe 2008-2013 | 2.16 |  |

Correlation of daily returns with the market: **0.18**.

- best Sharpe among options: `options_cap20` (1.77); this one: 1.62
- best return among options: `options_cap30` (0.0569); this one: 0.061
- best Calmar among options: `options_cap20` (0.298); this one: 0.271

![NAV (log scale)](figures/nav.png)

*NAV (log scale)*

![Drawdown from running peak](figures/drawdown.png)

*Drawdown from running peak*

![Calendar-year returns](figures/yearly.png)

*Calendar-year returns*

![Is the edge stable, or concentrated in a few years?](figures/rolling_sharpe.png)

*Is the edge stable, or concentrated in a few years?*

## Where the return comes from

![Call leg vs put leg](figures/legs.png)

*Call leg vs put leg*

- **97.4% of funded trades are calls** and they produce **95.1%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 39.7% and the median trade returns -22.6% on premium, yet the mean is 23.9%: a classic convex pay-off. The best 1% of trades produce **70%** of gross P&L and the best 10% produce 175%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 66,684 | 110.63 | 25.55 | 9.67 | 15.88 | 94.2% | 23.1% | 40.0% |
| put | 1,750 | 2.00 | 1.33 | 0.35 | 0.98 | 5.8% | 66.5% | 27.3% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 5,635 | 6.10 | 1.77 | 0.90 | 0.87 | 5.2% | 28.9% | 31.0% |
| 2 | 5,368 | 5.61 | 1.94 | 0.85 | 1.09 | 6.5% | 34.6% | 34.0% |
| 3 | 5,333 | 6.13 | 2.50 | 0.81 | 1.68 | 10.0% | 40.8% | 37.8% |
| 4 | 7,295 | 8.26 | 1.92 | 1.14 | 0.78 | 4.6% | 23.3% | 35.2% |
| 5 | 3,887 | 5.45 | 1.68 | 0.53 | 1.15 | 6.8% | 30.8% | 41.5% |
| 6 | 4,354 | 6.05 | 2.65 | 0.61 | 2.04 | 12.1% | 43.8% | 42.5% |
| 7 | 7,892 | 10.63 | 1.46 | 1.06 | 0.40 | 2.4% | 13.7% | 37.6% |
| 8 | 8,372 | 16.06 | 3.02 | 1.16 | 1.86 | 11.0% | 18.8% | 42.3% |
| 9 | 9,199 | 19.17 | 4.00 | 1.26 | 2.74 | 16.3% | 20.9% | 42.9% |
| 10 | 11,099 | 29.18 | 5.95 | 1.71 | 4.24 | 25.1% | 20.4% | 45.7% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 23,389 | 54.33 | 2.11 | 1.63 | 0.48 | 2.9% | 3.9% | 51.1% |
| call | ITM 0.8-0.9 | 5,396 | 8.93 | 1.13 | 0.27 | 0.85 | 5.1% | 12.6% | 51.1% |
| call | ITM 0.9-0.97 | 2,131 | 3.82 | 0.88 | 0.14 | 0.74 | 4.4% | 23.0% | 51.9% |
| call | ATM 0.97-1.03 | 2,514 | 5.35 | 1.89 | 0.34 | 1.55 | 9.2% | 35.3% | 46.3% |
| call | OTM 1.03-1.1 | 6,102 | 11.54 | 6.00 | 1.15 | 4.85 | 28.8% | 52.0% | 39.0% |
| call | OTM 1.1-1.2 | 11,721 | 15.33 | 8.90 | 2.76 | 6.14 | 36.4% | 58.1% | 32.1% |
| call | far OTM >1.2 | 15,431 | 11.34 | 4.64 | 3.38 | 1.26 | 7.5% | 41.0% | 23.0% |
| put | deep ITM <0.8 | 474 | 0.30 | 0.33 | 0.07 | 0.25 | 1.5% | 107.8% | 22.4% |
| put | ITM 0.8-0.9 | 675 | 0.65 | 0.67 | 0.12 | 0.56 | 3.3% | 104.5% | 28.1% |
| put | ITM 0.9-0.97 | 286 | 0.45 | 0.30 | 0.08 | 0.22 | 1.3% | 67.8% | 31.1% |
| put | ATM 0.97-1.03 | 156 | 0.41 | -0.00 | 0.07 | -0.07 | -0.4% | -0.9% | 19.2% |
| put | OTM 1.03-1.1 | 80 | 0.13 | 0.02 | 0.01 | 0.02 | 0.1% | 17.2% | 38.8% |
| put | OTM 1.1-1.2 | 44 | 0.04 | 0.01 | 0.00 | 0.01 | 0.1% | 30.7% | 40.9% |
| put | far OTM >1.2 | 35 | 0.03 | -0.01 | 0.00 | -0.01 | -0.0% | -16.1% | 37.1% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 22,805 | 34.05 | 17.79 | 5.15 | 12.64 | 75.0% | 52.2% | 35.2% |
| mid vol | 22,804 | 31.66 | 6.50 | 2.70 | 3.79 | 22.5% | 20.5% | 39.2% |
| high vol | 22,805 | 46.89 | 2.59 | 2.17 | 0.42 | 2.5% | 5.5% | 44.6% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 871 | 1.78 | 0.18 | 0.05 | 0.13 | 0.8% | 10.1% | 59.9% |
| low vol | ITM 0.8-0.9 | 905 | 1.59 | 0.17 | 0.05 | 0.12 | 0.8% | 10.8% | 54.9% |
| low vol | ITM 0.9-0.97 | 842 | 1.73 | 0.46 | 0.06 | 0.40 | 2.5% | 26.8% | 55.9% |
| low vol | ATM 0.97-1.03 | 1,619 | 3.67 | 1.38 | 0.19 | 1.18 | 7.4% | 37.4% | 49.0% |
| low vol | OTM 1.03-1.1 | 4,365 | 8.73 | 5.12 | 0.89 | 4.24 | 26.7% | 58.7% | 39.2% |
| low vol | OTM 1.1-1.2 | 7,785 | 11.13 | 7.27 | 2.25 | 5.02 | 31.6% | 65.3% | 31.7% |
| low vol | far OTM >1.2 | 5,625 | 4.47 | 2.59 | 1.48 | 1.11 | 7.0% | 58.0% | 24.1% |
| mid vol | deep ITM <0.8 | 6,356 | 13.64 | 0.76 | 0.41 | 0.35 | 2.2% | 5.6% | 53.6% |
| mid vol | ITM 0.8-0.9 | 2,499 | 4.31 | 0.58 | 0.13 | 0.45 | 2.8% | 13.4% | 51.9% |
| mid vol | ITM 0.9-0.97 | 876 | 1.46 | 0.31 | 0.05 | 0.26 | 1.6% | 21.3% | 50.0% |
| mid vol | ATM 0.97-1.03 | 622 | 1.10 | 0.32 | 0.08 | 0.24 | 1.5% | 29.2% | 41.8% |
| mid vol | OTM 1.03-1.1 | 1,388 | 2.17 | 0.76 | 0.17 | 0.59 | 3.7% | 35.2% | 40.1% |
| mid vol | OTM 1.1-1.2 | 3,271 | 3.46 | 1.41 | 0.40 | 1.00 | 6.3% | 40.7% | 33.9% |
| mid vol | far OTM >1.2 | 7,180 | 4.96 | 1.79 | 1.36 | 0.43 | 2.7% | 36.0% | 23.5% |
| high vol | deep ITM <0.8 | 16,154 | 38.89 | 1.17 | 1.17 | -0.00 | -0.0% | 3.0% | 49.6% |
| high vol | ITM 0.8-0.9 | 1,991 | 3.03 | 0.38 | 0.09 | 0.29 | 1.8% | 12.6% | 48.2% |
| high vol | ITM 0.9-0.97 | 412 | 0.63 | 0.10 | 0.03 | 0.08 | 0.5% | 16.3% | 47.8% |
| high vol | ATM 0.97-1.03 | 272 | 0.58 | 0.19 | 0.07 | 0.13 | 0.8% | 33.6% | 40.4% |
| high vol | OTM 1.03-1.1 | 349 | 0.64 | 0.12 | 0.09 | 0.03 | 0.2% | 18.3% | 32.1% |
| high vol | OTM 1.1-1.2 | 660 | 0.73 | 0.22 | 0.10 | 0.12 | 0.8% | 29.9% | 28.8% |
| high vol | far OTM >1.2 | 2,622 | 1.91 | 0.26 | 0.54 | -0.28 | -1.7% | 13.8% | 19.3% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,378 | 14.67 | 10.69 | 5.35 | 5.34 | 31.7% | 72.9% | 24.4% |
| $0.25-0.5 | 9,456 | 11.20 | 5.62 | 1.55 | 4.07 | 24.1% | 50.2% | 29.6% |
| $0.5-1 | 7,955 | 10.97 | 4.23 | 0.78 | 3.44 | 20.4% | 38.5% | 34.8% |
| $1-2 | 5,830 | 8.47 | 2.35 | 0.31 | 2.03 | 12.1% | 27.7% | 42.9% |
| $2-5 | 11,481 | 18.12 | 2.50 | 0.54 | 1.95 | 11.6% | 13.8% | 51.0% |
| >$5 | 19,334 | 49.21 | 1.50 | 1.48 | 0.02 | 0.1% | 3.0% | 50.3% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 2,795 | 5.75 | 1.33 | 0.24 | 1.08 | 6.4% | 23.1% | 41.0% |
| 1999 | 2,726 | 5.75 | 0.10 | 0.24 | -0.13 | -0.8% | 1.8% | 35.3% |
| 2000 | 2,665 | 5.94 | 0.56 | 0.24 | 0.32 | 1.9% | 9.5% | 42.4% |
| 2001 | 3,055 | 5.83 | -0.19 | 0.36 | -0.55 | -3.3% | -3.3% | 33.2% |
| 2002 | 3,463 | 6.28 | -0.09 | 0.56 | -0.65 | -3.9% | -1.4% | 26.2% |
| 2003 | 4,337 | 6.09 | 4.06 | 0.62 | 3.44 | 20.4% | 66.6% | 53.6% |
| 2004 | 5,292 | 7.58 | 1.78 | 0.81 | 0.97 | 5.8% | 23.5% | 33.7% |
| 2005 | 5,697 | 8.17 | 0.95 | 0.94 | 0.02 | 0.1% | 11.6% | 38.0% |
| 2006 | 5,806 | 7.94 | 2.18 | 0.89 | 1.29 | 7.7% | 27.4% | 35.0% |
| 2007 | 6,159 | 9.22 | 3.12 | 1.00 | 2.12 | 12.5% | 33.8% | 33.1% |
| 2008 | 4,112 | 9.48 | -2.15 | 0.61 | -2.76 | -16.4% | -22.7% | 24.0% |
| 2009 | 4,167 | 8.05 | 1.89 | 0.54 | 1.35 | 8.0% | 23.4% | 53.6% |
| 2010 | 6,689 | 9.66 | 4.07 | 0.99 | 3.08 | 18.2% | 42.1% | 47.6% |
| 2012 | 7,254 | 10.83 | 1.63 | 1.23 | 0.40 | 2.4% | 15.1% | 40.2% |
| 2013 | 4,217 | 6.05 | 7.65 | 0.76 | 6.89 | 40.9% | 126.4% | 55.1% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 6.10% | 3.70% | 1.62 | -22.5% | 68,434 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 13.09% | 3.38% | 3.66 | -17.1% | 74,764 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 8.79% | 3.61% | 2.35 | -21.1% | 71,787 |
| dead quotes worth zero (pessimistic bound) | 3.28% | 3.83% | 0.86 | -25.8% | 65,515 |
| + $0.10/share minimum cost | 2.72% | 3.80% | 0.73 | -25.3% | 64,630 |
| skip contracts under $0.25 | 5.31% | 3.19% | 1.64 | -21.4% | 55,861 |
| skip contracts under $0.50 | 3.95% | 2.84% | 1.38 | -23.0% | 46,112 |
| flat 10% round-trip cost | 3.73% | 3.76% | 0.99 | -26.8% | 65,261 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
