# opt_kelly_cap30_legacy

_options strategy | family: options-kelly | status: reference | derived from `opt_kelly_cap20_legacy`_

## What this strategy does

Kelly picks, 30% cap, ORIGINAL convention: dead quotes carried forward, flat 3% cost

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
| dead_quote_mark | carry_last |
| min_roundtrip_cost_per_share | 0.0 |
| min_premium | 0.0 |

## Performance

| metric | opt_kelly_cap30_legacy | market (CRSP VW) |
|---|---|---|
| Annualized return | 12.26% | 8.48% |
| Annualized volatility | 3.33% | 19.86% |
| Sharpe (NAV-pct) | 3.49 | 0.51 |
| Sortino | 6.71 |  |
| Max drawdown | -17.4% | -55.5% |
| Calmar | 0.70 |  |
| Final NAV ($10M start) | $68,824,425 |  |
| Worst day | -1.44% |  |
| % positive days | 52.1% |  |
| Sharpe 1996-2001 | 0.98 |  |
| Sharpe 2002-2007 | 4.67 |  |
| Sharpe 2008-2013 | 4.10 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: `opt_kelly_cap20` (1.77); this one: 3.49
- best return among options: `opt_kelly_cap30` (0.0569); this one: 0.123
- best Calmar among options: `opt_kelly_cap20` (0.298); this one: 0.705

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

- **98.4% of funded trades are calls** and they produce **97.4%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 44.9% and the median trade returns -2.6% on premium, yet the mean is 33.5%: a classic convex pay-off. The best 1% of trades produce **51%** of gross P&L and the best 10% produce 124%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 73,716 | 190.66 | 62.93 | 5.72 | 57.21 | 97.3% | 33.0% | 45.1% |
| put | 1,210 | 2.27 | 1.68 | 0.07 | 1.61 | 2.7% | 74.1% | 35.7% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 5,221 | 8.51 | 4.52 | 0.26 | 4.26 | 7.3% | 53.1% | 38.8% |
| 2 | 5,567 | 9.46 | 4.46 | 0.28 | 4.18 | 7.1% | 47.2% | 41.9% |
| 3 | 6,110 | 12.02 | 5.59 | 0.36 | 5.24 | 8.9% | 46.5% | 44.5% |
| 4 | 7,750 | 13.56 | 5.21 | 0.41 | 4.81 | 8.2% | 38.4% | 42.6% |
| 5 | 6,748 | 16.11 | 6.33 | 0.48 | 5.84 | 9.9% | 39.3% | 45.8% |
| 6 | 8,196 | 17.05 | 5.65 | 0.51 | 5.14 | 8.7% | 33.1% | 44.2% |
| 7 | 7,517 | 17.28 | 6.52 | 0.52 | 6.00 | 10.2% | 37.7% | 45.2% |
| 8 | 8,809 | 27.35 | 7.09 | 0.82 | 6.27 | 10.7% | 25.9% | 46.0% |
| 9 | 9,623 | 32.77 | 8.63 | 0.98 | 7.64 | 13.0% | 26.3% | 46.9% |
| 10 | 9,385 | 38.83 | 10.61 | 1.17 | 9.44 | 16.1% | 27.3% | 49.1% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 29,612 | 100.72 | 5.19 | 3.02 | 2.17 | 3.7% | 5.1% | 50.5% |
| call | ITM 0.8-0.9 | 6,183 | 14.53 | 2.33 | 0.44 | 1.89 | 3.2% | 16.0% | 50.5% |
| call | ITM 0.9-0.97 | 2,329 | 6.37 | 1.80 | 0.19 | 1.61 | 2.7% | 28.3% | 50.6% |
| call | ATM 0.97-1.03 | 2,594 | 9.08 | 4.17 | 0.27 | 3.90 | 6.6% | 45.9% | 47.6% |
| call | OTM 1.03-1.1 | 6,165 | 18.42 | 13.43 | 0.55 | 12.87 | 21.9% | 72.9% | 41.8% |
| call | OTM 1.1-1.2 | 11,660 | 24.01 | 21.48 | 0.72 | 20.76 | 35.3% | 89.5% | 39.2% |
| call | far OTM >1.2 | 15,173 | 17.53 | 14.54 | 0.53 | 14.01 | 23.8% | 82.9% | 36.8% |
| put | deep ITM <0.8 | 298 | 0.24 | 0.34 | 0.01 | 0.34 | 0.6% | 146.6% | 40.3% |
| put | ITM 0.8-0.9 | 418 | 0.56 | 0.47 | 0.02 | 0.45 | 0.8% | 83.2% | 36.8% |
| put | ITM 0.9-0.97 | 213 | 0.59 | 0.54 | 0.02 | 0.52 | 0.9% | 91.1% | 32.9% |
| put | ATM 0.97-1.03 | 136 | 0.62 | 0.30 | 0.02 | 0.28 | 0.5% | 48.3% | 28.7% |
| put | OTM 1.03-1.1 | 72 | 0.18 | 0.04 | 0.01 | 0.04 | 0.1% | 24.2% | 36.1% |
| put | OTM 1.1-1.2 | 42 | 0.05 | -0.00 | 0.00 | -0.00 | -0.0% | -4.6% | 31.0% |
| put | far OTM >1.2 | 31 | 0.04 | -0.01 | 0.00 | -0.01 | -0.0% | -16.6% | 32.3% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 24,968 | 61.62 | 43.62 | 1.85 | 41.78 | 71.0% | 70.8% | 41.9% |
| mid vol | 24,967 | 56.42 | 14.43 | 1.69 | 12.74 | 21.7% | 25.6% | 45.5% |
| high vol | 24,968 | 74.83 | 6.54 | 2.25 | 4.30 | 7.3% | 8.7% | 47.4% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 1,465 | 5.27 | 0.58 | 0.16 | 0.42 | 0.7% | 11.0% | 56.5% |
| low vol | ITM 0.8-0.9 | 1,121 | 3.31 | 0.58 | 0.10 | 0.48 | 0.8% | 17.6% | 53.5% |
| low vol | ITM 0.9-0.97 | 967 | 3.43 | 1.05 | 0.10 | 0.94 | 1.7% | 30.5% | 53.7% |
| low vol | ATM 0.97-1.03 | 1,756 | 6.73 | 3.23 | 0.20 | 3.03 | 5.3% | 48.0% | 50.1% |
| low vol | OTM 1.03-1.1 | 4,606 | 14.74 | 11.69 | 0.44 | 11.25 | 19.7% | 79.3% | 41.9% |
| low vol | OTM 1.1-1.2 | 8,200 | 18.75 | 18.20 | 0.56 | 17.63 | 30.8% | 97.0% | 39.4% |
| low vol | far OTM >1.2 | 6,237 | 8.19 | 7.58 | 0.25 | 7.33 | 12.8% | 92.6% | 36.1% |
| mid vol | deep ITM <0.8 | 8,980 | 30.70 | 1.92 | 0.92 | 1.00 | 1.7% | 6.3% | 52.2% |
| mid vol | ITM 0.8-0.9 | 3,039 | 7.18 | 1.20 | 0.22 | 0.98 | 1.7% | 16.7% | 51.4% |
| mid vol | ITM 0.9-0.97 | 951 | 2.08 | 0.58 | 0.06 | 0.52 | 0.9% | 27.8% | 49.7% |
| mid vol | ATM 0.97-1.03 | 585 | 1.47 | 0.52 | 0.04 | 0.47 | 0.8% | 34.9% | 42.1% |
| mid vol | OTM 1.03-1.1 | 1,280 | 2.88 | 1.27 | 0.09 | 1.18 | 2.1% | 44.0% | 42.0% |
| mid vol | OTM 1.1-1.2 | 2,911 | 4.41 | 2.81 | 0.13 | 2.67 | 4.7% | 63.6% | 38.8% |
| mid vol | far OTM >1.2 | 6,853 | 7.20 | 5.67 | 0.22 | 5.45 | 9.5% | 78.7% | 37.6% |
| high vol | deep ITM <0.8 | 19,156 | 64.71 | 2.67 | 1.94 | 0.73 | 1.3% | 4.1% | 49.2% |
| high vol | ITM 0.8-0.9 | 2,021 | 4.03 | 0.55 | 0.12 | 0.43 | 0.8% | 13.7% | 47.5% |
| high vol | ITM 0.9-0.97 | 410 | 0.86 | 0.17 | 0.03 | 0.15 | 0.3% | 20.1% | 45.4% |
| high vol | ATM 0.97-1.03 | 252 | 0.87 | 0.43 | 0.03 | 0.40 | 0.7% | 49.2% | 43.3% |
| high vol | OTM 1.03-1.1 | 279 | 0.81 | 0.47 | 0.02 | 0.44 | 0.8% | 58.1% | 39.4% |
| high vol | OTM 1.1-1.2 | 543 | 0.83 | 0.47 | 0.03 | 0.45 | 0.8% | 56.6% | 36.8% |
| high vol | far OTM >1.2 | 2,081 | 2.15 | 1.30 | 0.06 | 1.23 | 2.2% | 60.3% | 36.4% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,108 | 24.22 | 29.05 | 0.73 | 28.33 | 48.2% | 120.0% | 40.6% |
| $0.25-0.5 | 9,217 | 17.21 | 12.60 | 0.52 | 12.09 | 20.5% | 73.2% | 37.1% |
| $0.5-1 | 7,753 | 16.98 | 9.01 | 0.51 | 8.50 | 14.4% | 53.1% | 37.8% |
| $1-2 | 5,810 | 13.68 | 4.93 | 0.41 | 4.52 | 7.7% | 36.1% | 43.5% |
| $2-5 | 13,045 | 31.74 | 5.14 | 0.95 | 4.19 | 7.1% | 16.2% | 50.5% |
| >$5 | 24,993 | 89.10 | 3.87 | 2.67 | 1.20 | 2.0% | 4.3% | 49.9% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 3,074 | 5.46 | 0.70 | 0.16 | 0.54 | 0.9% | 12.9% | 42.1% |
| 1999 | 2,789 | 5.40 | 0.17 | 0.16 | 0.00 | 0.0% | 3.1% | 38.1% |
| 2000 | 2,677 | 5.69 | 0.77 | 0.17 | 0.60 | 1.0% | 13.6% | 46.1% |
| 2001 | 3,206 | 5.77 | 0.27 | 0.17 | 0.10 | 0.2% | 4.7% | 39.3% |
| 2002 | 3,530 | 6.84 | 0.79 | 0.20 | 0.59 | 1.0% | 11.6% | 33.9% |
| 2003 | 4,525 | 7.65 | 5.74 | 0.23 | 5.51 | 9.4% | 75.0% | 58.3% |
| 2004 | 5,494 | 10.31 | 3.63 | 0.31 | 3.32 | 5.6% | 35.2% | 40.5% |
| 2005 | 5,992 | 12.71 | 3.51 | 0.38 | 3.13 | 5.3% | 27.6% | 44.1% |
| 2006 | 6,420 | 14.05 | 4.62 | 0.42 | 4.20 | 7.1% | 32.9% | 39.6% |
| 2007 | 6,970 | 17.74 | 6.67 | 0.53 | 6.13 | 10.4% | 37.6% | 37.7% |
| 2008 | 5,065 | 19.18 | -3.11 | 0.58 | -3.68 | -6.3% | -16.2% | 29.7% |
| 2009 | 5,321 | 17.82 | 4.86 | 0.54 | 4.33 | 7.4% | 27.3% | 60.1% |
| 2010 | 7,515 | 22.10 | 9.27 | 0.66 | 8.60 | 14.6% | 41.9% | 52.0% |
| 2012 | 8,000 | 26.37 | 7.33 | 0.79 | 6.54 | 11.1% | 27.8% | 45.9% |
| 2013 | 4,348 | 15.83 | 19.41 | 0.47 | 18.93 | 32.2% | 122.6% | 61.1% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 12.26% | 3.33% | 3.49 | -17.4% | 74,926 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 12.26% | 3.33% | 3.49 | -17.4% | 74,926 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 12.26% | 3.33% | 3.49 | -17.4% | 74,926 |
| dead quotes worth zero (pessimistic bound) | 5.64% | 3.69% | 1.50 | -23.1% | 68,954 |
| + $0.10/share minimum cost | 6.64% | 3.55% | 1.83 | -20.1% | 68,747 |
| skip contracts under $0.25 | 7.79% | 2.97% | 2.54 | -18.9% | 59,390 |
| skip contracts under $0.50 | 5.07% | 2.71% | 1.84 | -18.7% | 48,698 |
| flat 10% round-trip cost | 8.83% | 3.41% | 2.50 | -21.1% | 71,693 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
