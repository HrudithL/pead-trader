# options_cap40_legacy

_options strategy | family: options-kelly | status: reference | derived from `options_cap20_legacy`_

## What this strategy does

Kelly picks, 40% cap, ORIGINAL convention: dead quotes carried forward, flat 3% cost

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
| max_premium_frac | 0.4 |
| roundtrip_cost_pct | 0.03 |
| dead_quote_mark | carry_last |
| min_roundtrip_cost_per_share | 0.0 |
| min_premium | 0.0 |

## Performance

| metric | options_cap40_legacy | market (CRSP VW) |
|---|---|---|
| Annualized return | 15.79% | 8.48% |
| Annualized volatility | 4.50% | 19.86% |
| Sharpe (NAV-pct) | 3.28 | 0.51 |
| Sortino | 6.08 |  |
| Max drawdown | -23.9% | -55.5% |
| Calmar | 0.66 |  |
| Final NAV ($10M start) | $115,414,400 |  |
| Worst day | -2.08% |  |
| % positive days | 52.1% |  |
| Sharpe 1996-2001 | 0.54 |  |
| Sharpe 2002-2007 | 4.58 |  |
| Sharpe 2008-2013 | 3.93 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: `options_cap20` (1.77); this one: 3.28
- best return among options: `options_cap30` (0.0569); this one: 0.158
- best Calmar among options: `options_cap20` (0.298); this one: 0.662

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

- **98.4% of funded trades are calls** and they produce **97.6%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 44.8% and the median trade returns -3.0% on premium, yet the mean is 33.7%: a classic convex pay-off. The best 1% of trades produce **53%** of gross P&L and the best 10% produce 125%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 75,609 | 339.72 | 112.96 | 10.19 | 102.77 | 97.5% | 33.3% | 45.0% |
| put | 1,216 | 3.81 | 2.76 | 0.11 | 2.64 | 2.5% | 72.4% | 35.6% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 5,392 | 15.24 | 8.07 | 0.46 | 7.62 | 7.2% | 53.0% | 38.9% |
| 2 | 5,801 | 17.27 | 7.87 | 0.52 | 7.35 | 7.0% | 45.6% | 42.1% |
| 3 | 6,293 | 21.87 | 10.07 | 0.66 | 9.41 | 8.9% | 46.0% | 44.3% |
| 4 | 8,100 | 24.35 | 9.20 | 0.73 | 8.47 | 8.0% | 37.8% | 42.9% |
| 5 | 6,877 | 29.01 | 11.53 | 0.87 | 10.66 | 10.1% | 39.7% | 45.8% |
| 6 | 8,480 | 30.66 | 10.46 | 0.92 | 9.55 | 9.1% | 34.1% | 44.3% |
| 7 | 7,746 | 31.23 | 11.89 | 0.94 | 10.95 | 10.4% | 38.1% | 44.8% |
| 8 | 8,929 | 48.72 | 12.73 | 1.46 | 11.27 | 10.7% | 26.1% | 45.9% |
| 9 | 9,755 | 57.61 | 15.34 | 1.73 | 13.61 | 12.9% | 26.6% | 46.5% |
| 10 | 9,452 | 67.58 | 18.55 | 2.03 | 16.53 | 15.7% | 27.5% | 48.8% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 31,190 | 184.12 | 9.44 | 5.52 | 3.92 | 3.7% | 5.1% | 50.1% |
| call | ITM 0.8-0.9 | 6,437 | 25.56 | 4.22 | 0.77 | 3.45 | 3.3% | 16.5% | 50.1% |
| call | ITM 0.9-0.97 | 2,380 | 11.38 | 3.46 | 0.34 | 3.12 | 3.0% | 30.4% | 50.2% |
| call | ATM 0.97-1.03 | 2,597 | 16.04 | 7.61 | 0.48 | 7.13 | 6.8% | 47.5% | 47.7% |
| call | OTM 1.03-1.1 | 6,170 | 31.60 | 24.14 | 0.95 | 23.19 | 22.0% | 76.4% | 41.8% |
| call | OTM 1.1-1.2 | 11,661 | 40.96 | 38.78 | 1.23 | 37.55 | 35.6% | 94.7% | 39.1% |
| call | far OTM >1.2 | 15,174 | 30.05 | 25.32 | 0.90 | 24.41 | 23.2% | 84.2% | 36.8% |
| put | deep ITM <0.8 | 298 | 0.37 | 0.49 | 0.01 | 0.48 | 0.5% | 134.4% | 41.3% |
| put | ITM 0.8-0.9 | 418 | 0.89 | 0.70 | 0.03 | 0.67 | 0.6% | 78.8% | 35.9% |
| put | ITM 0.9-0.97 | 213 | 1.01 | 0.92 | 0.03 | 0.89 | 0.8% | 91.0% | 32.9% |
| put | ATM 0.97-1.03 | 136 | 1.07 | 0.58 | 0.03 | 0.55 | 0.5% | 54.1% | 27.9% |
| put | OTM 1.03-1.1 | 72 | 0.31 | 0.08 | 0.01 | 0.07 | 0.1% | 26.5% | 37.5% |
| put | OTM 1.1-1.2 | 43 | 0.09 | -0.01 | 0.00 | -0.01 | -0.0% | -6.9% | 32.6% |
| put | far OTM >1.2 | 36 | 0.06 | -0.01 | 0.00 | -0.01 | -0.0% | -20.6% | 30.6% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 25,601 | 111.18 | 80.22 | 3.33 | 76.88 | 72.9% | 72.1% | 42.0% |
| mid vol | 25,600 | 102.45 | 24.55 | 3.07 | 21.47 | 20.4% | 24.0% | 45.4% |
| high vol | 25,601 | 129.78 | 10.94 | 3.89 | 7.04 | 6.7% | 8.4% | 47.0% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 1,613 | 11.11 | 1.34 | 0.33 | 1.00 | 1.0% | 12.0% | 57.3% |
| low vol | ITM 0.8-0.9 | 1,192 | 6.36 | 1.23 | 0.19 | 1.03 | 1.0% | 19.3% | 53.0% |
| low vol | ITM 0.9-0.97 | 1,009 | 6.46 | 2.09 | 0.19 | 1.90 | 1.8% | 32.4% | 52.9% |
| low vol | ATM 0.97-1.03 | 1,780 | 12.11 | 5.92 | 0.36 | 5.56 | 5.4% | 48.9% | 50.1% |
| low vol | OTM 1.03-1.1 | 4,663 | 25.73 | 21.35 | 0.77 | 20.57 | 20.0% | 83.0% | 41.9% |
| low vol | OTM 1.1-1.2 | 8,318 | 32.72 | 33.42 | 0.98 | 32.44 | 31.6% | 102.2% | 39.4% |
| low vol | far OTM >1.2 | 6,401 | 14.62 | 13.67 | 0.44 | 13.23 | 12.9% | 93.5% | 36.2% |
| mid vol | deep ITM <0.8 | 9,643 | 59.54 | 3.62 | 1.79 | 1.84 | 1.8% | 6.1% | 51.8% |
| mid vol | ITM 0.8-0.9 | 3,199 | 12.60 | 2.12 | 0.38 | 1.74 | 1.7% | 16.8% | 50.8% |
| mid vol | ITM 0.9-0.97 | 963 | 3.51 | 1.14 | 0.10 | 1.03 | 1.0% | 32.5% | 49.5% |
| mid vol | ATM 0.97-1.03 | 568 | 2.41 | 0.94 | 0.07 | 0.87 | 0.8% | 38.9% | 42.1% |
| mid vol | OTM 1.03-1.1 | 1,237 | 4.58 | 2.00 | 0.14 | 1.86 | 1.8% | 43.6% | 41.8% |
| mid vol | OTM 1.1-1.2 | 2,819 | 6.93 | 4.56 | 0.21 | 4.35 | 4.2% | 65.8% | 38.8% |
| mid vol | far OTM >1.2 | 6,800 | 12.08 | 9.49 | 0.36 | 9.13 | 8.9% | 78.6% | 37.3% |
| high vol | deep ITM <0.8 | 19,923 | 113.40 | 4.46 | 3.40 | 1.05 | 1.0% | 3.9% | 48.6% |
| high vol | ITM 0.8-0.9 | 2,044 | 6.60 | 0.88 | 0.20 | 0.68 | 0.7% | 13.3% | 47.3% |
| high vol | ITM 0.9-0.97 | 407 | 1.41 | 0.23 | 0.04 | 0.18 | 0.2% | 16.0% | 44.7% |
| high vol | ATM 0.97-1.03 | 248 | 1.51 | 0.75 | 0.04 | 0.71 | 0.7% | 50.0% | 43.5% |
| high vol | OTM 1.03-1.1 | 270 | 1.29 | 0.79 | 0.04 | 0.75 | 0.7% | 61.2% | 40.4% |
| high vol | OTM 1.1-1.2 | 518 | 1.29 | 0.79 | 0.04 | 0.75 | 0.7% | 61.2% | 36.1% |
| high vol | far OTM >1.2 | 1,971 | 3.35 | 2.16 | 0.10 | 2.06 | 2.0% | 64.4% | 37.0% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,108 | 42.42 | 52.35 | 1.27 | 51.08 | 48.5% | 123.4% | 40.6% |
| $0.25-0.5 | 9,217 | 29.42 | 21.98 | 0.88 | 21.10 | 20.0% | 74.7% | 37.0% |
| $0.5-1 | 7,754 | 29.09 | 15.98 | 0.87 | 15.10 | 14.3% | 54.9% | 37.7% |
| $1-2 | 5,808 | 23.65 | 8.91 | 0.71 | 8.21 | 7.8% | 37.7% | 43.6% |
| $2-5 | 13,114 | 56.03 | 9.45 | 1.68 | 7.77 | 7.4% | 16.9% | 50.5% |
| >$5 | 26,824 | 162.91 | 7.05 | 4.89 | 2.16 | 2.1% | 4.3% | 49.3% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 3,366 | 7.67 | 0.89 | 0.23 | 0.66 | 0.6% | 11.6% | 41.1% |
| 1999 | 2,792 | 7.58 | 0.11 | 0.23 | -0.12 | -0.1% | 1.4% | 37.8% |
| 2000 | 2,879 | 7.97 | 0.73 | 0.24 | 0.49 | 0.5% | 9.2% | 45.4% |
| 2001 | 3,288 | 7.69 | 0.14 | 0.23 | -0.09 | -0.1% | 1.8% | 38.9% |
| 2002 | 3,590 | 9.51 | 1.52 | 0.28 | 1.23 | 1.2% | 16.0% | 33.6% |
| 2003 | 4,600 | 11.10 | 7.82 | 0.33 | 7.48 | 7.1% | 70.4% | 58.4% |
| 2004 | 5,567 | 16.08 | 5.59 | 0.48 | 5.11 | 4.8% | 34.8% | 40.3% |
| 2005 | 6,081 | 20.94 | 5.33 | 0.63 | 4.71 | 4.5% | 25.5% | 44.4% |
| 2006 | 6,598 | 24.07 | 7.74 | 0.72 | 7.02 | 6.7% | 32.1% | 39.7% |
| 2007 | 7,161 | 32.86 | 11.57 | 0.99 | 10.58 | 10.0% | 35.2% | 37.5% |
| 2008 | 5,272 | 36.11 | -6.14 | 1.08 | -7.23 | -6.9% | -17.0% | 29.4% |
| 2009 | 5,562 | 32.60 | 8.76 | 0.98 | 7.78 | 7.4% | 26.9% | 60.6% |
| 2010 | 7,624 | 42.64 | 17.25 | 1.28 | 15.97 | 15.1% | 40.4% | 51.9% |
| 2012 | 8,085 | 53.19 | 14.79 | 1.60 | 13.20 | 12.5% | 27.8% | 46.0% |
| 2013 | 4,360 | 33.52 | 39.63 | 1.00 | 38.62 | 36.6% | 118.2% | 61.1% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 15.79% | 4.50% | 3.28 | -23.9% | 76,825 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 15.79% | 4.50% | 3.28 | -23.9% | 76,825 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 15.79% | 4.50% | 3.28 | -23.9% | 76,825 |
| dead quotes worth zero (pessimistic bound) | 6.90% | 5.12% | 1.33 | -31.7% | 70,209 |
| + $0.10/share minimum cost | 8.29% | 4.99% | 1.62 | -28.6% | 68,708 |
| skip contracts under $0.25 | 9.93% | 4.11% | 2.32 | -25.9% | 61,357 |
| skip contracts under $0.50 | 6.38% | 3.78% | 1.66 | -26.1% | 50,486 |
| flat 10% round-trip cost | 11.08% | 4.73% | 2.24 | -29.4% | 72,823 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
