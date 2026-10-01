# opt_kelly_cap20_conservative

_options strategy | family: options-kelly | status: research | derived from `opt_kelly_cap20`_

## What this strategy does

opt_kelly_cap20 with dead quotes worth zero -- the pessimistic bound

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
| max_premium_frac | 0.2 |
| roundtrip_cost_pct | 0.03 |
| dead_quote_mark | zero_bid_zero |
| min_roundtrip_cost_per_share | 0.05 |
| min_premium | 0.0 |

## Performance

| metric | opt_kelly_cap20_conservative | market (CRSP VW) |
|---|---|---|
| Annualized return | 2.34% | 8.48% |
| Annualized volatility | 2.37% | 19.86% |
| Sharpe (NAV-pct) | 0.99 | 0.51 |
| Sortino | 1.57 |  |
| Max drawdown | -17.1% | -55.5% |
| Calmar | 0.14 |  |
| Final NAV ($10M start) | $14,699,816 |  |
| Worst day | -0.84% |  |
| % positive days | 44.5% |  |
| Sharpe 1996-2001 | 0.32 |  |
| Sharpe 2002-2007 | 0.99 |  |
| Sharpe 2008-2013 | 1.60 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: `opt_kelly_cap20` (1.77); this one: 0.987
- best return among options: `opt_kelly_cap30` (0.0569); this one: 0.0234
- best Calmar among options: `opt_kelly_cap20` (0.298); this one: 0.137

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

- **98.1% of funded trades are calls** and they produce **97.3%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 38.4% and the median trade returns -29.0% on premium, yet the mean is 18.2%: a classic convex pay-off. The best 1% of trades produce **87%** of gross P&L and the best 10% produce 230%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 61,414 | 51.76 | 9.29 | 4.70 | 4.59 | 97.6% | 17.9% | 38.7% |
| put | 1,167 | 0.79 | 0.26 | 0.15 | 0.11 | 2.4% | 32.7% | 23.2% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,425 | 2.39 | 0.40 | 0.38 | 0.02 | 0.5% | 16.9% | 28.1% |
| 2 | 4,549 | 2.41 | 0.47 | 0.39 | 0.08 | 1.7% | 19.5% | 31.0% |
| 3 | 4,945 | 2.99 | 0.77 | 0.39 | 0.39 | 8.2% | 25.9% | 36.5% |
| 4 | 6,082 | 3.56 | 0.44 | 0.51 | -0.07 | -1.5% | 12.3% | 33.1% |
| 5 | 5,597 | 4.17 | 1.04 | 0.42 | 0.62 | 13.2% | 24.9% | 39.6% |
| 6 | 6,295 | 4.38 | 0.61 | 0.46 | 0.14 | 3.1% | 13.8% | 36.4% |
| 7 | 5,995 | 4.33 | 0.90 | 0.45 | 0.46 | 9.7% | 20.8% | 38.4% |
| 8 | 7,557 | 7.31 | 1.09 | 0.54 | 0.55 | 11.7% | 14.9% | 41.8% |
| 9 | 8,523 | 9.43 | 1.54 | 0.62 | 0.92 | 19.5% | 16.3% | 42.7% |
| 10 | 8,613 | 11.58 | 2.29 | 0.69 | 1.60 | 34.0% | 19.8% | 45.7% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 18,991 | 23.36 | 1.00 | 0.70 | 0.30 | 6.4% | 4.3% | 51.4% |
| call | ITM 0.8-0.9 | 4,918 | 4.38 | 0.55 | 0.13 | 0.41 | 8.7% | 12.4% | 50.1% |
| call | ITM 0.9-0.97 | 2,084 | 1.86 | 0.33 | 0.07 | 0.26 | 5.6% | 17.6% | 50.6% |
| call | ATM 0.97-1.03 | 2,535 | 2.70 | 0.80 | 0.17 | 0.63 | 13.4% | 29.5% | 45.5% |
| call | OTM 1.03-1.1 | 6,111 | 5.97 | 2.39 | 0.58 | 1.81 | 38.5% | 40.0% | 38.1% |
| call | OTM 1.1-1.2 | 11,617 | 7.89 | 3.10 | 1.40 | 1.71 | 36.3% | 39.3% | 31.0% |
| call | far OTM >1.2 | 15,158 | 5.60 | 1.13 | 1.66 | -0.53 | -11.3% | 20.1% | 22.3% |
| put | deep ITM <0.8 | 298 | 0.10 | 0.12 | 0.03 | 0.09 | 1.9% | 113.8% | 23.5% |
| put | ITM 0.8-0.9 | 417 | 0.23 | 0.11 | 0.04 | 0.06 | 1.3% | 46.7% | 24.0% |
| put | ITM 0.9-0.97 | 213 | 0.19 | 0.06 | 0.04 | 0.03 | 0.5% | 33.6% | 21.1% |
| put | ATM 0.97-1.03 | 134 | 0.20 | -0.03 | 0.03 | -0.07 | -1.4% | -17.2% | 13.4% |
| put | OTM 1.03-1.1 | 65 | 0.06 | 0.01 | 0.00 | 0.01 | 0.1% | 13.4% | 36.9% |
| put | OTM 1.1-1.2 | 25 | 0.01 | 0.00 | 0.00 | 0.00 | 0.0% | 13.1% | 40.0% |
| put | far OTM >1.2 | 15 | 0.01 | -0.00 | 0.00 | -0.00 | -0.0% | -12.8% | 26.7% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 20,855 | 15.99 | 6.05 | 2.47 | 3.58 | 76.2% | 37.8% | 33.9% |
| mid vol | 20,855 | 14.18 | 2.33 | 1.31 | 1.02 | 21.7% | 16.5% | 37.1% |
| high vol | 20,855 | 22.37 | 1.16 | 1.07 | 0.10 | 2.1% | 5.2% | 44.2% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 554 | 0.53 | 0.06 | 0.02 | 0.04 | 1.0% | 11.3% | 61.4% |
| low vol | ITM 0.8-0.9 | 684 | 0.63 | 0.07 | 0.02 | 0.05 | 1.1% | 11.1% | 53.9% |
| low vol | ITM 0.9-0.97 | 741 | 0.75 | 0.15 | 0.03 | 0.12 | 2.6% | 19.8% | 54.9% |
| low vol | ATM 0.97-1.03 | 1,589 | 1.80 | 0.60 | 0.10 | 0.50 | 11.0% | 33.3% | 48.3% |
| low vol | OTM 1.03-1.1 | 4,267 | 4.39 | 2.04 | 0.44 | 1.60 | 34.8% | 46.4% | 38.1% |
| low vol | OTM 1.1-1.2 | 7,404 | 5.46 | 2.41 | 1.11 | 1.31 | 28.5% | 44.2% | 30.6% |
| low vol | far OTM >1.2 | 5,084 | 2.06 | 0.68 | 0.70 | -0.02 | -0.5% | 32.8% | 23.1% |
| mid vol | deep ITM <0.8 | 4,539 | 4.91 | 0.26 | 0.15 | 0.11 | 2.4% | 5.3% | 53.6% |
| mid vol | ITM 0.8-0.9 | 2,227 | 2.04 | 0.23 | 0.06 | 0.17 | 3.6% | 11.2% | 50.7% |
| mid vol | ITM 0.9-0.97 | 873 | 0.74 | 0.14 | 0.03 | 0.11 | 2.4% | 18.2% | 49.8% |
| mid vol | ATM 0.97-1.03 | 645 | 0.59 | 0.14 | 0.04 | 0.10 | 2.3% | 24.0% | 42.2% |
| mid vol | OTM 1.03-1.1 | 1,474 | 1.20 | 0.35 | 0.09 | 0.26 | 5.6% | 29.0% | 39.5% |
| mid vol | OTM 1.1-1.2 | 3,467 | 1.98 | 0.60 | 0.23 | 0.37 | 8.0% | 30.3% | 32.1% |
| mid vol | far OTM >1.2 | 7,249 | 2.50 | 0.47 | 0.68 | -0.21 | -4.7% | 18.6% | 23.0% |
| high vol | deep ITM <0.8 | 13,893 | 17.91 | 0.68 | 0.54 | 0.14 | 3.1% | 3.8% | 50.3% |
| high vol | ITM 0.8-0.9 | 2,006 | 1.71 | 0.25 | 0.05 | 0.19 | 4.2% | 14.4% | 48.2% |
| high vol | ITM 0.9-0.97 | 469 | 0.37 | 0.04 | 0.01 | 0.03 | 0.7% | 11.9% | 45.0% |
| high vol | ATM 0.97-1.03 | 300 | 0.31 | 0.06 | 0.03 | 0.02 | 0.5% | 17.9% | 38.0% |
| high vol | OTM 1.03-1.1 | 370 | 0.38 | 0.01 | 0.05 | -0.05 | -1.0% | 1.4% | 33.5% |
| high vol | OTM 1.1-1.2 | 740 | 0.44 | 0.09 | 0.06 | 0.03 | 0.7% | 20.0% | 30.1% |
| high vol | far OTM >1.2 | 2,823 | 1.03 | -0.01 | 0.28 | -0.29 | -6.4% | -1.4% | 19.0% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,108 | 7.26 | 3.18 | 2.62 | 0.57 | 12.1% | 43.8% | 23.2% |
| $0.25-0.5 | 9,217 | 5.65 | 1.95 | 0.78 | 1.17 | 24.8% | 34.5% | 28.9% |
| $0.5-1 | 7,753 | 5.56 | 1.59 | 0.40 | 1.19 | 25.4% | 28.6% | 33.9% |
| $1-2 | 5,718 | 4.36 | 1.02 | 0.16 | 0.86 | 18.3% | 23.5% | 42.1% |
| $2-5 | 10,939 | 9.10 | 1.11 | 0.27 | 0.84 | 17.9% | 12.2% | 50.7% |
| >$5 | 14,846 | 20.63 | 0.69 | 0.62 | 0.07 | 1.5% | 3.3% | 50.5% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 2,753 | 3.31 | 0.38 | 0.13 | 0.25 | 5.3% | 11.4% | 41.4% |
| 1999 | 2,580 | 3.18 | 0.00 | 0.13 | -0.13 | -2.7% | 0.1% | 35.5% |
| 2000 | 2,256 | 3.29 | 0.53 | 0.14 | 0.39 | 8.3% | 16.0% | 44.8% |
| 2001 | 2,823 | 3.40 | -0.12 | 0.20 | -0.32 | -6.8% | -3.6% | 34.6% |
| 2002 | 3,228 | 3.61 | -0.72 | 0.31 | -1.03 | -22.0% | -20.0% | 25.1% |
| 2003 | 4,149 | 3.42 | 2.44 | 0.36 | 2.08 | 44.3% | 71.3% | 53.0% |
| 2004 | 4,986 | 3.95 | 0.65 | 0.44 | 0.20 | 4.3% | 16.4% | 33.7% |
| 2005 | 5,363 | 4.05 | 0.42 | 0.49 | -0.07 | -1.6% | 10.3% | 36.4% |
| 2006 | 5,287 | 3.73 | 0.62 | 0.46 | 0.17 | 3.6% | 16.7% | 33.6% |
| 2007 | 5,609 | 3.86 | 1.01 | 0.45 | 0.56 | 11.9% | 26.2% | 31.2% |
| 2008 | 3,628 | 3.69 | -0.98 | 0.25 | -1.24 | -26.3% | -26.6% | 22.9% |
| 2009 | 3,739 | 3.19 | 0.73 | 0.24 | 0.49 | 10.5% | 22.9% | 52.1% |
| 2010 | 5,966 | 3.74 | 1.26 | 0.44 | 0.82 | 17.6% | 33.8% | 45.0% |
| 2012 | 6,428 | 3.98 | 0.53 | 0.52 | 0.02 | 0.4% | 13.4% | 36.5% |
| 2013 | 3,786 | 2.16 | 2.80 | 0.30 | 2.50 | 53.3% | 130.0% | 52.7% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 2.34% | 2.37% | 0.99 | -17.1% | 62,581 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 8.58% | 2.18% | 3.78 | -10.3% | 70,167 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 4.11% | 2.36% | 1.72 | -14.5% | 64,734 |
| dead quotes worth zero (pessimistic bound) | 2.34% | 2.37% | 0.99 | -17.1% | 62,581 |
| + $0.10/share minimum cost | 0.13% | 2.40% | 0.07 | -23.8% | 59,819 |
| skip contracts under $0.25 | 2.73% | 1.98% | 1.37 | -15.1% | 51,686 |
| skip contracts under $0.50 | 2.37% | 1.75% | 1.35 | -14.9% | 43,529 |
| flat 10% round-trip cost | 0.98% | 2.37% | 0.42 | -24.2% | 60,498 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
