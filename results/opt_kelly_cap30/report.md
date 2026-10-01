# opt_kelly_cap30

_options strategy | family: options-kelly | status: validated | derived from `opt_kelly_cap20`_

## What this strategy does

Kelly picks, 30% premium cap, dead quotes marked at half-ask, >= $0.05/share cost

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
| min_roundtrip_cost_per_share | 0.05 |
| min_premium | 0.0 |

## Performance

| metric | opt_kelly_cap30 | market (CRSP VW) |
|---|---|---|
| Annualized return | 5.69% | 8.48% |
| Annualized volatility | 3.61% | 19.86% |
| Sharpe (NAV-pct) | 1.55 | 0.51 |
| Sortino | 2.51 |  |
| Max drawdown | -22.3% | -55.5% |
| Calmar | 0.26 |  |
| Final NAV ($10M start) | $25,172,895 |  |
| Worst day | -1.72% |  |
| % positive days | 46.5% |  |
| Sharpe 1996-2001 | 0.12 |  |
| Sharpe 2002-2007 | 1.95 |  |
| Sharpe 2008-2013 | 2.17 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: `opt_kelly_cap20` (1.77); this one: 1.55
- best return among options: this strategy; this one: 0.0569
- best Calmar among options: `opt_kelly_cap20` (0.298); this one: 0.255

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

- **98.3% of funded trades are calls** and they produce **96.7%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 39.9% and the median trade returns -22.0% on premium, yet the mean is 23.2%: a classic convex pay-off. The best 1% of trades produce **70%** of gross P&L and the best 10% produce 175%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 67,672 | 103.18 | 23.50 | 8.87 | 14.63 | 96.5% | 22.8% | 40.2% |
| put | 1,189 | 1.35 | 0.79 | 0.25 | 0.54 | 3.5% | 58.3% | 26.2% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,830 | 4.57 | 1.53 | 0.69 | 0.84 | 5.5% | 33.5% | 30.8% |
| 2 | 5,074 | 4.87 | 1.53 | 0.73 | 0.80 | 5.3% | 31.5% | 34.2% |
| 3 | 5,540 | 6.14 | 2.08 | 0.74 | 1.34 | 8.8% | 33.8% | 38.5% |
| 4 | 6,964 | 7.34 | 1.69 | 0.96 | 0.73 | 4.8% | 23.0% | 35.9% |
| 5 | 6,146 | 8.45 | 2.51 | 0.79 | 1.72 | 11.3% | 29.7% | 41.3% |
| 6 | 7,238 | 9.21 | 1.82 | 0.88 | 0.94 | 6.2% | 19.7% | 38.6% |
| 7 | 6,794 | 9.06 | 2.32 | 0.87 | 1.45 | 9.6% | 25.6% | 40.2% |
| 8 | 8,208 | 14.73 | 2.76 | 1.02 | 1.74 | 11.5% | 18.7% | 42.8% |
| 9 | 9,081 | 18.28 | 3.48 | 1.17 | 2.32 | 15.3% | 19.1% | 43.4% |
| 10 | 8,986 | 21.89 | 4.58 | 1.28 | 3.30 | 21.7% | 20.9% | 45.8% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 24,227 | 51.75 | 1.93 | 1.55 | 0.37 | 2.5% | 3.7% | 50.9% |
| call | ITM 0.8-0.9 | 5,653 | 8.44 | 1.06 | 0.26 | 0.80 | 5.3% | 12.5% | 50.7% |
| call | ITM 0.9-0.97 | 2,255 | 3.56 | 0.77 | 0.13 | 0.64 | 4.2% | 21.6% | 50.6% |
| call | ATM 0.97-1.03 | 2,572 | 4.95 | 1.76 | 0.31 | 1.44 | 9.5% | 35.5% | 46.3% |
| call | OTM 1.03-1.1 | 6,151 | 10.59 | 5.58 | 1.06 | 4.52 | 29.8% | 52.6% | 39.1% |
| call | OTM 1.1-1.2 | 11,643 | 13.87 | 8.07 | 2.52 | 5.56 | 36.6% | 58.2% | 32.1% |
| call | far OTM >1.2 | 15,171 | 10.04 | 4.34 | 3.04 | 1.30 | 8.6% | 43.3% | 23.1% |
| put | deep ITM <0.8 | 298 | 0.17 | 0.22 | 0.04 | 0.17 | 1.2% | 131.8% | 25.8% |
| put | ITM 0.8-0.9 | 418 | 0.37 | 0.27 | 0.07 | 0.19 | 1.3% | 71.3% | 26.6% |
| put | ITM 0.9-0.97 | 213 | 0.33 | 0.29 | 0.07 | 0.22 | 1.4% | 85.6% | 26.3% |
| put | ATM 0.97-1.03 | 135 | 0.34 | 0.01 | 0.06 | -0.05 | -0.4% | 1.6% | 16.3% |
| put | OTM 1.03-1.1 | 68 | 0.10 | 0.02 | 0.01 | 0.01 | 0.1% | 16.1% | 35.3% |
| put | OTM 1.1-1.2 | 32 | 0.02 | 0.00 | 0.00 | 0.00 | 0.0% | 5.5% | 37.5% |
| put | far OTM >1.2 | 25 | 0.02 | -0.00 | 0.00 | -0.00 | -0.0% | -10.3% | 36.0% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 22,947 | 31.40 | 16.09 | 4.73 | 11.36 | 74.9% | 51.3% | 35.4% |
| mid vol | 22,947 | 29.49 | 5.76 | 2.45 | 3.31 | 21.8% | 19.5% | 39.3% |
| high vol | 22,947 | 43.62 | 2.44 | 1.94 | 0.49 | 3.2% | 5.6% | 45.1% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 933 | 1.76 | 0.17 | 0.05 | 0.12 | 0.8% | 9.7% | 59.5% |
| low vol | ITM 0.8-0.9 | 921 | 1.49 | 0.15 | 0.04 | 0.11 | 0.7% | 10.4% | 53.3% |
| low vol | ITM 0.9-0.97 | 883 | 1.63 | 0.40 | 0.06 | 0.35 | 2.4% | 24.8% | 54.2% |
| low vol | ATM 0.97-1.03 | 1,675 | 3.43 | 1.28 | 0.18 | 1.10 | 7.5% | 37.3% | 48.9% |
| low vol | OTM 1.03-1.1 | 4,439 | 8.09 | 4.78 | 0.82 | 3.96 | 27.1% | 59.1% | 39.4% |
| low vol | OTM 1.1-1.2 | 7,840 | 10.21 | 6.70 | 2.08 | 4.62 | 31.6% | 65.5% | 31.8% |
| low vol | far OTM >1.2 | 5,683 | 4.11 | 2.31 | 1.38 | 0.93 | 6.3% | 56.1% | 24.2% |
| mid vol | deep ITM <0.8 | 6,662 | 13.25 | 0.64 | 0.40 | 0.25 | 1.7% | 4.9% | 52.9% |
| mid vol | ITM 0.8-0.9 | 2,678 | 4.16 | 0.55 | 0.13 | 0.43 | 2.9% | 13.3% | 51.4% |
| mid vol | ITM 0.9-0.97 | 936 | 1.34 | 0.26 | 0.05 | 0.22 | 1.5% | 19.7% | 49.0% |
| mid vol | ATM 0.97-1.03 | 625 | 1.00 | 0.31 | 0.07 | 0.23 | 1.6% | 30.9% | 42.1% |
| mid vol | OTM 1.03-1.1 | 1,389 | 1.95 | 0.69 | 0.15 | 0.54 | 3.7% | 35.5% | 39.7% |
| mid vol | OTM 1.1-1.2 | 3,189 | 3.05 | 1.20 | 0.36 | 0.84 | 5.8% | 39.4% | 33.2% |
| mid vol | far OTM >1.2 | 7,090 | 4.41 | 1.78 | 1.23 | 0.55 | 3.8% | 40.4% | 23.7% |
| high vol | deep ITM <0.8 | 16,623 | 36.73 | 1.11 | 1.10 | 0.00 | 0.0% | 3.0% | 49.6% |
| high vol | ITM 0.8-0.9 | 2,053 | 2.79 | 0.35 | 0.09 | 0.26 | 1.8% | 12.5% | 48.5% |
| high vol | ITM 0.9-0.97 | 435 | 0.58 | 0.10 | 0.02 | 0.08 | 0.5% | 17.2% | 46.2% |
| high vol | ATM 0.97-1.03 | 271 | 0.51 | 0.17 | 0.06 | 0.11 | 0.7% | 32.6% | 40.2% |
| high vol | OTM 1.03-1.1 | 323 | 0.55 | 0.10 | 0.08 | 0.02 | 0.1% | 18.7% | 33.7% |
| high vol | OTM 1.1-1.2 | 608 | 0.59 | 0.18 | 0.08 | 0.10 | 0.7% | 30.2% | 29.8% |
| high vol | far OTM >1.2 | 2,396 | 1.51 | 0.26 | 0.44 | -0.18 | -1.2% | 16.9% | 19.0% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,108 | 13.18 | 9.68 | 4.84 | 4.84 | 31.9% | 73.4% | 24.5% |
| $0.25-0.5 | 9,217 | 9.93 | 5.05 | 1.38 | 3.68 | 24.2% | 50.9% | 29.9% |
| $0.5-1 | 7,753 | 9.80 | 3.80 | 0.70 | 3.10 | 20.4% | 38.8% | 34.8% |
| $1-2 | 5,784 | 7.79 | 2.17 | 0.29 | 1.89 | 12.4% | 27.9% | 43.1% |
| $2-5 | 12,068 | 17.35 | 2.19 | 0.52 | 1.67 | 11.0% | 12.6% | 50.5% |
| >$5 | 19,931 | 46.48 | 1.39 | 1.39 | -0.01 | -0.0% | 3.0% | 50.1% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 3,060 | 5.42 | 0.58 | 0.21 | 0.37 | 2.4% | 10.6% | 40.9% |
| 1999 | 2,749 | 5.27 | 0.02 | 0.20 | -0.18 | -1.2% | 0.4% | 35.4% |
| 2000 | 2,635 | 5.43 | 0.58 | 0.21 | 0.37 | 2.4% | 10.8% | 44.0% |
| 2001 | 3,146 | 5.34 | -0.17 | 0.30 | -0.47 | -3.1% | -3.1% | 34.7% |
| 2002 | 3,470 | 5.83 | -0.12 | 0.49 | -0.61 | -4.0% | -2.0% | 26.7% |
| 2003 | 4,365 | 5.71 | 3.74 | 0.57 | 3.17 | 20.9% | 65.6% | 53.8% |
| 2004 | 5,274 | 7.07 | 1.64 | 0.74 | 0.89 | 5.9% | 23.1% | 34.1% |
| 2005 | 5,687 | 7.62 | 0.86 | 0.86 | 0.00 | 0.0% | 11.3% | 38.1% |
| 2006 | 5,837 | 7.37 | 1.98 | 0.81 | 1.17 | 7.7% | 26.9% | 35.4% |
| 2007 | 6,162 | 8.48 | 2.92 | 0.90 | 2.02 | 13.3% | 34.4% | 33.4% |
| 2008 | 4,201 | 8.73 | -2.07 | 0.54 | -2.62 | -17.2% | -23.7% | 24.3% |
| 2009 | 4,227 | 7.50 | 1.86 | 0.49 | 1.37 | 9.0% | 24.8% | 54.2% |
| 2010 | 6,623 | 9.02 | 3.78 | 0.95 | 2.83 | 18.6% | 41.9% | 47.3% |
| 2012 | 7,229 | 10.10 | 1.54 | 1.13 | 0.40 | 2.7% | 15.2% | 39.7% |
| 2013 | 4,196 | 5.64 | 7.15 | 0.70 | 6.45 | 42.5% | 126.8% | 55.3% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 5.69% | 3.61% | 1.55 | -22.3% | 68,861 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 12.26% | 3.33% | 3.49 | -17.4% | 74,926 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 8.25% | 3.54% | 2.26 | -20.9% | 72,092 |
| dead quotes worth zero (pessimistic bound) | 3.01% | 3.74% | 0.81 | -26.9% | 65,987 |
| + $0.10/share minimum cost | 2.42% | 3.71% | 0.66 | -26.4% | 65,061 |
| skip contracts under $0.25 | 4.98% | 3.12% | 1.57 | -21.0% | 56,293 |
| skip contracts under $0.50 | 3.74% | 2.76% | 1.34 | -23.2% | 47,525 |
| flat 10% round-trip cost | 3.29% | 3.66% | 0.90 | -29.8% | 65,606 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
