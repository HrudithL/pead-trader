# options_cap20_midfill

_options strategy | family: options-kelly | status: reference | derived from `options_cap20`_

## What this strategy does

options_cap20 with the previous convention: mid fills, flat 3% + >= $0.05/share cost

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
| dead_quote_mark | zero_bid_half_ask |
| fill_mode | mid |
| min_roundtrip_cost_per_share | 0.05 |
| min_premium | 0.0 |

## Performance

| metric | options_cap20_midfill | market (CRSP VW) |
|---|---|---|
| Annualized return | 4.43% | 8.48% |
| Annualized volatility | 2.36% | 19.86% |
| Sharpe (NAV-pct) | 1.85 | 0.51 |
| Sortino | 3.18 |  |
| Max drawdown | -14.5% | -55.5% |
| Calmar | 0.31 |  |
| Final NAV ($10M start) | $20,609,239 |  |
| Worst day | -0.80% |  |
| % positive days | 47.1% |  |
| Sharpe 1996-2001 | 1.19 |  |
| Sharpe 2002-2007 | 2.01 |  |
| Sharpe 2008-2013 | 2.37 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: `options_cap20` (1.77); this one: 1.85
- best return among options: `options_cap30` (0.0569); this one: 0.0443
- best Calmar among options: `options_cap20` (0.298); this one: 0.307

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

- **97.3% of funded trades are calls** and they produce **94.8%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 39.3% and the median trade returns -24.4% on premium, yet the mean is 25.6%: a classic convex pay-off. The best 1% of trades produce **64%** of gross P&L and the best 10% produce 168%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 63,261 | 64.16 | 15.86 | 5.89 | 9.96 | 93.9% | 24.7% | 39.6% |
| put | 1,727 | 1.32 | 0.88 | 0.23 | 0.65 | 6.1% | 66.6% | 27.0% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 5,368 | 3.65 | 1.14 | 0.56 | 0.58 | 5.4% | 31.2% | 30.2% |
| 2 | 5,041 | 3.31 | 1.21 | 0.53 | 0.68 | 6.4% | 36.7% | 32.8% |
| 3 | 5,009 | 3.49 | 1.49 | 0.49 | 0.99 | 9.3% | 42.6% | 37.2% |
| 4 | 6,785 | 4.77 | 1.23 | 0.70 | 0.52 | 4.9% | 25.7% | 34.2% |
| 5 | 3,606 | 2.99 | 1.01 | 0.32 | 0.70 | 6.6% | 34.0% | 40.7% |
| 6 | 4,059 | 3.40 | 1.57 | 0.36 | 1.20 | 11.3% | 46.1% | 41.6% |
| 7 | 7,209 | 5.85 | 0.93 | 0.64 | 0.29 | 2.8% | 15.9% | 36.6% |
| 8 | 8,060 | 9.39 | 1.85 | 0.70 | 1.15 | 10.8% | 19.7% | 42.5% |
| 9 | 8,948 | 11.26 | 2.48 | 0.77 | 1.71 | 16.1% | 22.1% | 42.8% |
| 10 | 10,903 | 17.38 | 3.82 | 1.04 | 2.78 | 26.2% | 22.0% | 45.9% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 20,517 | 29.10 | 1.39 | 0.87 | 0.52 | 4.9% | 4.8% | 51.6% |
| call | ITM 0.8-0.9 | 4,963 | 5.21 | 0.70 | 0.16 | 0.54 | 5.1% | 13.5% | 51.3% |
| call | ITM 0.9-0.97 | 2,057 | 2.27 | 0.54 | 0.08 | 0.46 | 4.3% | 23.7% | 52.1% |
| call | ATM 0.97-1.03 | 2,488 | 3.32 | 1.17 | 0.21 | 0.96 | 9.0% | 35.2% | 46.5% |
| call | OTM 1.03-1.1 | 6,094 | 7.32 | 3.66 | 0.72 | 2.94 | 27.7% | 50.0% | 38.9% |
| call | OTM 1.1-1.2 | 11,716 | 9.80 | 5.48 | 1.74 | 3.74 | 35.2% | 55.9% | 32.1% |
| call | far OTM >1.2 | 15,426 | 7.14 | 2.92 | 2.10 | 0.81 | 7.7% | 40.9% | 23.0% |
| put | deep ITM <0.8 | 474 | 0.20 | 0.21 | 0.05 | 0.16 | 1.5% | 106.0% | 22.4% |
| put | ITM 0.8-0.9 | 674 | 0.43 | 0.43 | 0.08 | 0.35 | 3.3% | 99.5% | 28.2% |
| put | ITM 0.9-0.97 | 285 | 0.29 | 0.19 | 0.05 | 0.14 | 1.3% | 64.4% | 30.9% |
| put | ATM 0.97-1.03 | 156 | 0.28 | 0.03 | 0.04 | -0.01 | -0.1% | 11.4% | 19.2% |
| put | OTM 1.03-1.1 | 75 | 0.08 | 0.01 | 0.00 | 0.01 | 0.1% | 14.9% | 36.0% |
| put | OTM 1.1-1.2 | 36 | 0.02 | 0.01 | 0.00 | 0.01 | 0.1% | 38.8% | 47.2% |
| put | far OTM >1.2 | 27 | 0.02 | -0.00 | 0.00 | -0.00 | -0.0% | -19.4% | 33.3% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 21,657 | 20.44 | 10.65 | 3.15 | 7.50 | 70.7% | 52.1% | 35.0% |
| mid vol | 21,656 | 17.86 | 4.11 | 1.65 | 2.46 | 23.2% | 23.0% | 38.3% |
| high vol | 21,656 | 27.16 | 1.97 | 1.33 | 0.64 | 6.1% | 7.3% | 44.4% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 695 | 0.83 | 0.09 | 0.03 | 0.06 | 0.6% | 10.2% | 60.7% |
| low vol | ITM 0.8-0.9 | 766 | 0.84 | 0.10 | 0.03 | 0.08 | 0.8% | 12.4% | 54.7% |
| low vol | ITM 0.9-0.97 | 769 | 0.98 | 0.26 | 0.04 | 0.23 | 2.3% | 26.7% | 56.6% |
| low vol | ATM 0.97-1.03 | 1,574 | 2.23 | 0.86 | 0.12 | 0.74 | 7.5% | 38.8% | 49.3% |
| low vol | OTM 1.03-1.1 | 4,280 | 5.42 | 3.09 | 0.55 | 2.54 | 25.5% | 57.0% | 39.0% |
| low vol | OTM 1.1-1.2 | 7,561 | 6.88 | 4.32 | 1.40 | 2.92 | 29.3% | 62.8% | 31.7% |
| low vol | far OTM >1.2 | 5,255 | 2.65 | 1.54 | 0.89 | 0.65 | 6.6% | 58.4% | 24.1% |
| mid vol | deep ITM <0.8 | 5,207 | 6.54 | 0.37 | 0.20 | 0.18 | 1.8% | 5.7% | 54.0% |
| mid vol | ITM 0.8-0.9 | 2,229 | 2.43 | 0.35 | 0.07 | 0.27 | 2.8% | 14.3% | 52.6% |
| mid vol | ITM 0.9-0.97 | 856 | 0.88 | 0.21 | 0.03 | 0.18 | 1.8% | 23.4% | 50.6% |
| mid vol | ATM 0.97-1.03 | 628 | 0.70 | 0.20 | 0.05 | 0.15 | 1.5% | 28.3% | 42.0% |
| mid vol | OTM 1.03-1.1 | 1,437 | 1.43 | 0.48 | 0.11 | 0.37 | 3.7% | 33.8% | 40.6% |
| mid vol | OTM 1.1-1.2 | 3,409 | 2.37 | 1.01 | 0.28 | 0.73 | 7.3% | 42.4% | 33.6% |
| mid vol | far OTM >1.2 | 7,284 | 3.13 | 1.11 | 0.85 | 0.26 | 2.6% | 35.5% | 23.5% |
| high vol | deep ITM <0.8 | 14,608 | 21.71 | 0.93 | 0.65 | 0.27 | 2.8% | 4.3% | 50.3% |
| high vol | ITM 0.8-0.9 | 1,967 | 1.94 | 0.25 | 0.06 | 0.19 | 1.9% | 13.0% | 48.5% |
| high vol | ITM 0.9-0.97 | 431 | 0.41 | 0.07 | 0.02 | 0.06 | 0.6% | 17.2% | 47.1% |
| high vol | ATM 0.97-1.03 | 285 | 0.38 | 0.10 | 0.04 | 0.06 | 0.6% | 26.9% | 40.7% |
| high vol | OTM 1.03-1.1 | 377 | 0.47 | 0.09 | 0.06 | 0.02 | 0.2% | 18.2% | 31.8% |
| high vol | OTM 1.1-1.2 | 741 | 0.55 | 0.16 | 0.07 | 0.09 | 0.9% | 28.3% | 28.9% |
| high vol | far OTM >1.2 | 2,883 | 1.36 | 0.27 | 0.36 | -0.10 | -1.0% | 19.6% | 19.8% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,378 | 9.23 | 6.65 | 3.33 | 3.32 | 31.3% | 72.0% | 24.3% |
| $0.25-0.5 | 9,456 | 7.15 | 3.49 | 0.99 | 2.50 | 23.5% | 48.8% | 29.6% |
| $0.5-1 | 7,952 | 6.99 | 2.59 | 0.50 | 2.09 | 19.7% | 37.0% | 34.8% |
| $1-2 | 5,809 | 5.33 | 1.46 | 0.20 | 1.27 | 11.9% | 27.4% | 42.8% |
| $2-5 | 11,092 | 10.89 | 1.58 | 0.33 | 1.26 | 11.9% | 14.6% | 51.3% |
| >$5 | 16,301 | 25.88 | 0.96 | 0.78 | 0.18 | 1.7% | 3.7% | 50.8% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 2,504 | 3.54 | 0.90 | 0.15 | 0.74 | 7.0% | 25.4% | 41.5% |
| 1999 | 2,567 | 3.46 | 0.15 | 0.15 | 0.00 | 0.0% | 4.4% | 35.9% |
| 2000 | 2,272 | 3.58 | 0.60 | 0.15 | 0.45 | 4.2% | 16.9% | 43.6% |
| 2001 | 2,781 | 3.77 | 0.04 | 0.24 | -0.20 | -1.9% | 1.2% | 33.1% |
| 2002 | 3,270 | 4.00 | -0.53 | 0.36 | -0.89 | -8.4% | -13.2% | 25.1% |
| 2003 | 4,205 | 3.89 | 3.12 | 0.41 | 2.71 | 25.5% | 80.2% | 53.1% |
| 2004 | 5,137 | 4.61 | 1.10 | 0.52 | 0.59 | 5.6% | 24.0% | 33.4% |
| 2005 | 5,537 | 4.97 | 1.28 | 0.60 | 0.68 | 6.4% | 25.8% | 38.2% |
| 2006 | 5,557 | 4.83 | 1.10 | 0.58 | 0.52 | 4.9% | 22.8% | 33.9% |
| 2007 | 5,923 | 5.16 | 1.57 | 0.60 | 0.98 | 9.2% | 30.5% | 31.8% |
| 2008 | 3,881 | 5.12 | -1.08 | 0.34 | -1.43 | -13.4% | -21.1% | 23.9% |
| 2009 | 4,067 | 4.49 | 1.08 | 0.32 | 0.77 | 7.2% | 24.2% | 53.4% |
| 2010 | 6,417 | 5.22 | 2.04 | 0.57 | 1.47 | 13.8% | 39.0% | 47.2% |
| 2012 | 6,858 | 5.68 | 1.21 | 0.71 | 0.51 | 4.8% | 21.4% | 39.1% |
| 2013 | 4,012 | 3.15 | 4.13 | 0.42 | 3.71 | 35.0% | 130.9% | 54.3% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 4.43% | 2.36% | 1.85 | -14.5% | 64,988 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 9.17% | 2.24% | 3.93 | -10.3% | 69,933 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 6.29% | 2.36% | 2.60 | -13.0% | 66,971 |
| dead quotes worth zero (pessimistic bound) | 2.52% | 2.43% | 1.04 | -17.9% | 62,297 |
| + $0.10/share minimum cost | 2.14% | 2.39% | 0.90 | -17.3% | 61,894 |
| skip contracts under $0.25 | 3.82% | 2.00% | 1.89 | -13.5% | 52,036 |
| skip contracts under $0.50 | 2.93% | 1.78% | 1.64 | -13.7% | 43,044 |
| flat 10% round-trip cost | 3.04% | 2.37% | 1.27 | -16.5% | 62,760 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
