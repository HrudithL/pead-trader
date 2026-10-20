# options_cap20

_options strategy | family: options-kelly | status: validated_

## What this strategy does

Kelly picks, 20% premium cap, bought at the ask / sold at the bid, dead quotes at half-ask

**Why it should work.** Buy the contract whose payoff profile best matches the SUE-conditioned return distribution, sized by Kelly, spread across hundreds of independent events.

**Design notes** (from the strategy's source file).

Options strategies -- Kelly-optimal contract selection around earnings.

WHAT IT DOES. For every earnings announcement, look at the whole option chain (every strike, calls AND puts, ~13 contracts/event on average, up to 60) on the announcement day and buy the single contract with the highest expected LOG growth under an empirical, walk-forward distribution of the stock's 60-day return conditional on its SUE decile. The direction is never hard-coded: the math picks puts for bad surprises and calls for good ones on its own (in practice, on the corrected run, about 97% of funded trades are calls).

WHY KELLY AND NOT EXPECTED VALUE. A naive "maximize E[return]" selector loves deep-OTM lottery tickets (huge payoff x tiny probability = big mean) that lose money on almost every draw. Scoring each contract by max over f of E[log(1 + f*R)] penalizes ruin: a lottery ticket is still scored, it just earns a tiny optimal fraction f* and negligible growth. The selector therefore prefers contracts whose payoff is large exactly in the scenarios the SUE decile makes likely.

HOW IT IS SIZED. Only events whose best contract has positive expected log growth are traded. Each such event's size is HALF its own Kelly fraction (full Kelly over-bets when the distribution is itself an estimate), then the quarter's candidates share a premium budget (`max_premium_frac` of trailing NAV minus premium still outstanding from earlier quarters) in proportion to those fractions.

WHERE THE RETURN COMES FROM, AND HOW MUCH THERE IS. Filled at the quoted ask and sold at the quoted bid (the default since the 2026-10 fixes), the 20%-cap book earns about 0.3% a year at Sharpe 0.16: no measurable edge (see docs/FIXES.md and the generated report for the breakdown by moneyness, volatility and year). The pre-fix picture -- OTM calls on low-volatility, high-SUE stocks, ~1,200 single-stock bets per quarter, Sharpe above 3 -- came from the mid-fill and dead-quote conventions below, not from a real premium.

THE BACKTEST CONVENTIONS THAT WERE WRONG, AND THE FIXES. The original backtest valued a position at each checkpoint (+1, 5, 10, 20, 40, 60 trading days) from the contract's mid quote and, when the quote was missing, CARRIED THE PREVIOUS QUOTE FORWARD. 22% of picks have no quote at 60 days, and 93% of those (a one-year pre-fix check, not repeated) are not missing at all: the bid is ZERO (best_offer ~ $0.10) -- the option has died and nobody will buy it. Carrying its last live price forward never marks a dying option down. The strategies below value a zero-bid checkpoint at half the ask (or at zero, for the conservative bound) using the quotes now kept by the forward-price prep stage. Effect on the 20%-premium-cap book: 8.58% / Sharpe 3.78 as originally reported -> 5.87% / 2.50 with the corrected mark -> 4.12% / 1.77 once each contract also pays at least one $0.05 tick round trip -> 2.34% / 0.99 if dead options are worth nothing (all four figures pre-fix). Buying at the real ask and selling at the real bid then takes the book to 0.28% / 0.16 (corrected run).

REMAINING CAVEATS: no market impact; the early walk-forward distributions rest on only 8 quarters of history; ~4,000 trades a year would meet real capacity limits; what little profit there is comes from 2003 and 2013.

| parameter | value |
|---|---|
| hold_horizon | 60 |
| half_kelly | 0.5 |
| max_premium_frac | 0.2 |
| roundtrip_cost_pct | 0.0 |
| dead_quote_mark | zero_bid_half_ask |
| fill_mode | bid_ask |
| min_roundtrip_cost_per_share | 0.0 |
| min_premium | 0.0 |

## Performance

| metric | options_cap20 | market (CRSP VW) |
|---|---|---|
| Annualized return | 0.28% | 8.48% |
| Annualized volatility | 1.92% | 19.86% |
| Sharpe (NAV-pct) | 0.16 | 0.51 |
| Sortino | 0.23 |  |
| Max drawdown | -18.9% | -55.5% |
| Calmar | 0.01 |  |
| Final NAV ($10M start) | $10,481,798 |  |
| Worst day | -0.79% |  |
| % positive days | 43.0% |  |
| Sharpe 1996-2001 | 0.07 |  |
| Sharpe 2002-2007 | 0.31 |  |
| Sharpe 2008-2013 | 0.00 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: this strategy; this one: 0.156
- best return among options: this strategy; this one: 0.00282
- best Calmar among options: this strategy; this one: 0.015

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

- **97.1% of funded trades are calls** and they produce **59.8%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 30.5% and the median trade returns -50.0% on premium, yet the mean is 1.0%: a classic convex pay-off. The best 1% of trades produce **1151%** of gross P&L and the best 10% produce 3138%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 57,460 | 45.84 | 0.29 | 0.00 | 0.29 | 59.8% | 0.6% | 30.9% |
| put | 1,693 | 1.07 | 0.19 | 0.00 | 0.19 | 40.2% | 18.1% | 19.7% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,951 | 2.60 | -0.23 | 0.00 | -0.23 | -47.8% | -8.9% | 21.3% |
| 2 | 4,602 | 2.34 | -0.09 | 0.00 | -0.09 | -17.8% | -3.7% | 22.5% |
| 3 | 4,445 | 2.30 | 0.06 | 0.00 | 0.06 | 13.3% | 2.8% | 26.5% |
| 4 | 6,187 | 3.44 | -0.24 | 0.00 | -0.24 | -48.9% | -6.8% | 25.2% |
| 5 | 3,063 | 1.92 | 0.09 | 0.00 | 0.09 | 18.1% | 4.5% | 29.9% |
| 6 | 3,589 | 2.33 | 0.29 | 0.00 | 0.29 | 61.0% | 12.6% | 31.6% |
| 7 | 6,375 | 4.15 | -0.33 | 0.00 | -0.33 | -68.7% | -8.0% | 27.9% |
| 8 | 7,454 | 6.77 | -0.01 | 0.00 | -0.01 | -2.2% | -0.2% | 34.3% |
| 9 | 8,227 | 8.21 | 0.25 | 0.00 | 0.25 | 51.8% | 3.0% | 34.9% |
| 10 | 10,260 | 12.84 | 0.68 | 0.00 | 0.68 | 141.3% | 5.3% | 38.8% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 15,832 | 19.37 | -0.58 | 0.00 | -0.58 | -120.7% | -3.0% | 44.3% |
| call | ITM 0.8-0.9 | 4,273 | 3.88 | -0.06 | 0.00 | -0.06 | -11.8% | -1.5% | 42.0% |
| call | ITM 0.9-0.97 | 1,873 | 1.67 | -0.01 | 0.00 | -0.01 | -2.7% | -0.8% | 38.9% |
| call | ATM 0.97-1.03 | 2,417 | 2.45 | 0.09 | 0.00 | 0.09 | 19.3% | 3.8% | 35.9% |
| call | OTM 1.03-1.1 | 6,015 | 5.57 | 0.61 | 0.00 | 0.61 | 126.1% | 10.9% | 31.7% |
| call | OTM 1.1-1.2 | 11,646 | 7.50 | 0.63 | 0.00 | 0.63 | 131.6% | 8.4% | 25.1% |
| call | far OTM >1.2 | 15,404 | 5.40 | -0.40 | 0.00 | -0.40 | -82.0% | -7.3% | 16.1% |
| put | deep ITM <0.8 | 473 | 0.17 | 0.08 | 0.00 | 0.08 | 16.5% | 47.4% | 15.2% |
| put | ITM 0.8-0.9 | 672 | 0.37 | 0.14 | 0.00 | 0.14 | 29.9% | 39.4% | 21.0% |
| put | ITM 0.9-0.97 | 283 | 0.23 | 0.02 | 0.00 | 0.02 | 4.1% | 8.6% | 24.4% |
| put | ATM 0.97-1.03 | 149 | 0.21 | -0.04 | 0.00 | -0.04 | -8.6% | -19.6% | 12.8% |
| put | OTM 1.03-1.1 | 70 | 0.06 | -0.01 | 0.00 | -0.01 | -2.2% | -16.6% | 22.9% |
| put | OTM 1.1-1.2 | 26 | 0.01 | 0.01 | 0.00 | 0.01 | 1.0% | 31.3% | 42.3% |
| put | far OTM >1.2 | 20 | 0.01 | -0.00 | 0.00 | -0.00 | -0.6% | -29.5% | 25.0% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 19,712 | 14.15 | 1.25 | 0.00 | 1.25 | 258.4% | 8.8% | 25.9% |
| mid vol | 19,712 | 12.36 | 0.04 | 0.00 | 0.04 | 8.9% | 0.3% | 29.2% |
| high vol | 19,712 | 20.38 | -0.81 | 0.00 | -0.81 | -167.6% | -4.0% | 36.5% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 357 | 0.32 | -0.01 | 0.00 | -0.01 | -4.3% | -3.9% | 42.6% |
| low vol | ITM 0.8-0.9 | 537 | 0.49 | -0.05 | 0.00 | -0.05 | -17.1% | -10.1% | 35.0% |
| low vol | ITM 0.9-0.97 | 640 | 0.61 | -0.03 | 0.00 | -0.03 | -9.7% | -4.6% | 37.7% |
| low vol | ATM 0.97-1.03 | 1,463 | 1.57 | 0.11 | 0.00 | 0.11 | 37.1% | 6.8% | 38.0% |
| low vol | OTM 1.03-1.1 | 4,098 | 3.97 | 0.59 | 0.00 | 0.59 | 206.4% | 15.0% | 31.6% |
| low vol | OTM 1.1-1.2 | 7,154 | 4.95 | 0.54 | 0.00 | 0.54 | 188.4% | 11.0% | 24.2% |
| low vol | far OTM >1.2 | 4,760 | 1.79 | 0.04 | 0.00 | 0.04 | 12.5% | 2.0% | 17.1% |
| mid vol | deep ITM <0.8 | 3,449 | 3.50 | -0.12 | 0.00 | -0.12 | -41.0% | -3.4% | 46.0% |
| mid vol | ITM 0.8-0.9 | 1,827 | 1.71 | -0.04 | 0.00 | -0.04 | -12.1% | -2.0% | 43.8% |
| mid vol | ITM 0.9-0.97 | 779 | 0.69 | 0.03 | 0.00 | 0.03 | 11.9% | 4.9% | 40.9% |
| mid vol | ATM 0.97-1.03 | 644 | 0.57 | 0.01 | 0.00 | 0.01 | 1.8% | 0.9% | 33.7% |
| mid vol | OTM 1.03-1.1 | 1,493 | 1.16 | 0.06 | 0.00 | 0.06 | 21.6% | 5.4% | 33.3% |
| mid vol | OTM 1.1-1.2 | 3,610 | 2.02 | 0.11 | 0.00 | 0.11 | 38.3% | 5.5% | 27.3% |
| mid vol | far OTM >1.2 | 7,308 | 2.39 | -0.17 | 0.00 | -0.17 | -59.9% | -7.2% | 16.6% |
| high vol | deep ITM <0.8 | 12,021 | 15.54 | -0.45 | 0.00 | -0.45 | -157.5% | -2.9% | 43.9% |
| high vol | ITM 0.8-0.9 | 1,908 | 1.68 | 0.03 | 0.00 | 0.03 | 9.9% | 1.7% | 42.2% |
| high vol | ITM 0.9-0.97 | 453 | 0.36 | -0.02 | 0.00 | -0.02 | -6.7% | -5.4% | 37.1% |
| high vol | ATM 0.97-1.03 | 309 | 0.31 | -0.02 | 0.00 | -0.02 | -6.8% | -6.3% | 30.1% |
| high vol | OTM 1.03-1.1 | 424 | 0.44 | -0.05 | 0.00 | -0.05 | -17.1% | -11.3% | 27.1% |
| high vol | OTM 1.1-1.2 | 877 | 0.53 | -0.02 | 0.00 | -0.02 | -6.9% | -3.7% | 23.8% |
| high vol | far OTM >1.2 | 3,332 | 1.22 | -0.26 | 0.00 | -0.26 | -89.4% | -21.0% | 13.9% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,378 | 6.85 | 0.61 | 0.00 | 0.61 | 125.7% | 8.8% | 16.5% |
| $0.25-0.5 | 9,456 | 5.50 | 0.17 | 0.00 | 0.17 | 35.3% | 3.1% | 22.4% |
| $0.5-1 | 7,933 | 5.37 | 0.21 | 0.00 | 0.21 | 43.8% | 3.9% | 27.8% |
| $1-2 | 5,641 | 4.04 | 0.14 | 0.00 | 0.14 | 29.6% | 3.5% | 35.3% |
| $2-5 | 9,599 | 7.79 | -0.11 | 0.00 | -0.11 | -22.9% | -1.4% | 41.5% |
| >$5 | 12,146 | 17.36 | -0.54 | 0.00 | -0.54 | -111.5% | -3.1% | 44.4% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 2,452 | 3.49 | 0.35 | 0.00 | 0.35 | 73.1% | 10.1% | 38.5% |
| 1999 | 2,487 | 3.28 | -0.15 | 0.00 | -0.15 | -31.1% | -4.6% | 31.9% |
| 2000 | 2,207 | 3.34 | 0.17 | 0.00 | 0.17 | 34.6% | 5.0% | 39.9% |
| 2001 | 2,673 | 3.41 | -0.42 | 0.00 | -0.42 | -87.3% | -12.3% | 29.1% |
| 2002 | 3,170 | 3.50 | -1.05 | 0.00 | -1.05 | -217.6% | -29.9% | 21.1% |
| 2003 | 4,062 | 3.26 | 1.48 | 0.00 | 1.48 | 307.9% | 45.5% | 45.9% |
| 2004 | 4,911 | 3.61 | -0.05 | 0.00 | -0.05 | -9.9% | -1.3% | 27.2% |
| 2005 | 5,263 | 3.63 | -0.00 | 0.00 | -0.00 | -0.2% | -0.0% | 30.4% |
| 2006 | 5,130 | 3.30 | -0.09 | 0.00 | -0.09 | -19.4% | -2.8% | 26.4% |
| 2007 | 5,401 | 3.25 | 0.13 | 0.00 | 0.13 | 26.6% | 3.9% | 24.5% |
| 2008 | 3,307 | 3.06 | -1.01 | 0.00 | -1.01 | -209.5% | -32.9% | 17.2% |
| 2009 | 3,388 | 2.52 | 0.15 | 0.00 | 0.15 | 30.9% | 5.9% | 40.4% |
| 2010 | 5,498 | 2.84 | 0.17 | 0.00 | 0.17 | 34.7% | 5.9% | 33.2% |
| 2012 | 5,746 | 2.88 | -0.31 | 0.00 | -0.31 | -64.3% | -10.7% | 23.4% |
| 2013 | 3,458 | 1.52 | 1.11 | 0.00 | 1.11 | 231.4% | 73.1% | 40.6% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 0.28% | 1.92% | 0.16 | -18.9% | 59,153 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 2.45% | 1.82% | 1.33 | -12.9% | 62,494 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 0.28% | 1.92% | 0.16 | -18.9% | 59,153 |
| dead quotes worth zero (pessimistic bound) | -1.04% | 1.99% | -0.52 | -32.3% | 57,102 |
| + $0.10/share minimum cost | -3.28% | 1.99% | -1.66 | -52.3% | 53,826 |
| skip contracts under $0.25 | 0.03% | 1.73% | 0.03 | -18.6% | 47,184 |
| skip contracts under $0.50 | -0.06% | 1.60% | -0.03 | -19.1% | 39,309 |
| flat 10% round-trip cost | -2.31% | 1.96% | -1.19 | -44.4% | 54,475 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
