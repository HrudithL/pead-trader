# options_cap20_legacy

_options strategy | family: options-kelly | status: reference_

## What this strategy does

Kelly picks, 20% cap, ORIGINAL convention: dead quotes carried forward, flat 3% cost

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
| dead_quote_mark | carry_last |
| min_roundtrip_cost_per_share | 0.0 |
| min_premium | 0.0 |

## Performance

| metric | options_cap20_legacy | market (CRSP VW) |
|---|---|---|
| Annualized return | 8.58% | 8.48% |
| Annualized volatility | 2.18% | 19.86% |
| Sharpe (NAV-pct) | 3.78 | 0.51 |
| Sortino | 7.77 |  |
| Max drawdown | -10.3% | -55.5% |
| Calmar | 0.83 |  |
| Final NAV ($10M start) | $39,486,311 |  |
| Worst day | -0.77% |  |
| % positive days | 52.3% |  |
| Sharpe 1996-2001 | 1.64 |  |
| Sharpe 2002-2007 | 4.80 |  |
| Sharpe 2008-2013 | 4.39 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: `options_cap20` (1.77); this one: 3.78
- best return among options: `options_cap30` (0.0569); this one: 0.0858
- best Calmar among options: `options_cap20` (0.298); this one: 0.834

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

- **98.3% of funded trades are calls** and they produce **97.1%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 44.9% and the median trade returns -2.2% on premium, yet the mean is 34.5%: a classic convex pay-off. The best 1% of trades produce **48%** of gross P&L and the best 10% produce 121%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 68,979 | 92.27 | 31.34 | 2.77 | 28.57 | 96.9% | 34.0% | 45.1% |
| put | 1,188 | 1.23 | 0.95 | 0.04 | 0.91 | 3.1% | 77.3% | 35.9% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,850 | 4.17 | 2.28 | 0.12 | 2.15 | 7.3% | 54.6% | 38.3% |
| 2 | 5,139 | 4.48 | 2.27 | 0.13 | 2.13 | 7.2% | 50.6% | 41.4% |
| 3 | 5,690 | 5.69 | 2.77 | 0.17 | 2.60 | 8.8% | 48.8% | 44.3% |
| 4 | 6,956 | 6.38 | 2.64 | 0.19 | 2.45 | 8.3% | 41.5% | 42.0% |
| 5 | 6,377 | 7.68 | 3.10 | 0.23 | 2.88 | 9.7% | 40.4% | 45.9% |
| 6 | 7,400 | 7.98 | 2.74 | 0.24 | 2.50 | 8.5% | 34.3% | 44.1% |
| 7 | 6,887 | 8.08 | 3.17 | 0.24 | 2.93 | 9.9% | 39.2% | 45.3% |
| 8 | 8,386 | 13.18 | 3.48 | 0.40 | 3.08 | 10.5% | 26.4% | 46.3% |
| 9 | 9,314 | 16.28 | 4.30 | 0.49 | 3.81 | 12.9% | 26.4% | 46.8% |
| 10 | 9,168 | 19.58 | 5.53 | 0.59 | 4.95 | 16.8% | 28.3% | 49.4% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 25,540 | 45.76 | 2.48 | 1.37 | 1.10 | 3.7% | 5.4% | 51.3% |
| call | ITM 0.8-0.9 | 5,664 | 7.21 | 1.14 | 0.22 | 0.93 | 3.1% | 15.8% | 50.8% |
| call | ITM 0.9-0.97 | 2,219 | 3.13 | 0.85 | 0.09 | 0.76 | 2.6% | 27.1% | 51.4% |
| call | ATM 0.97-1.03 | 2,573 | 4.59 | 2.05 | 0.14 | 1.91 | 6.5% | 44.6% | 47.9% |
| call | OTM 1.03-1.1 | 6,154 | 9.68 | 6.64 | 0.29 | 6.35 | 21.5% | 68.6% | 41.7% |
| call | OTM 1.1-1.2 | 11,656 | 12.72 | 10.68 | 0.38 | 10.30 | 34.9% | 83.9% | 39.2% |
| call | far OTM >1.2 | 15,173 | 9.18 | 7.51 | 0.28 | 7.23 | 24.5% | 81.8% | 36.8% |
| put | deep ITM <0.8 | 298 | 0.14 | 0.22 | 0.00 | 0.22 | 0.7% | 162.4% | 40.6% |
| put | ITM 0.8-0.9 | 418 | 0.32 | 0.28 | 0.01 | 0.27 | 0.9% | 88.2% | 36.4% |
| put | ITM 0.9-0.97 | 213 | 0.31 | 0.27 | 0.01 | 0.26 | 0.9% | 87.2% | 32.9% |
| put | ATM 0.97-1.03 | 136 | 0.33 | 0.16 | 0.01 | 0.15 | 0.5% | 47.1% | 28.7% |
| put | OTM 1.03-1.1 | 68 | 0.09 | 0.02 | 0.00 | 0.02 | 0.1% | 21.2% | 36.8% |
| put | OTM 1.1-1.2 | 35 | 0.02 | 0.00 | 0.00 | -0.00 | -0.0% | 2.3% | 37.1% |
| put | far OTM >1.2 | 20 | 0.01 | -0.00 | 0.00 | -0.00 | -0.0% | -14.9% | 30.0% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 23,382 | 29.76 | 20.97 | 0.89 | 20.08 | 68.1% | 70.5% | 41.9% |
| mid vol | 23,381 | 26.33 | 7.57 | 0.79 | 6.78 | 23.0% | 28.7% | 45.1% |
| high vol | 23,381 | 37.38 | 3.74 | 1.12 | 2.62 | 8.9% | 10.0% | 47.7% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 1,088 | 1.86 | 0.22 | 0.06 | 0.17 | 0.6% | 12.0% | 58.5% |
| low vol | ITM 0.8-0.9 | 967 | 1.44 | 0.25 | 0.04 | 0.20 | 0.7% | 17.2% | 54.4% |
| low vol | ITM 0.9-0.97 | 883 | 1.56 | 0.47 | 0.05 | 0.43 | 1.5% | 30.4% | 55.3% |
| low vol | ATM 0.97-1.03 | 1,693 | 3.28 | 1.51 | 0.10 | 1.41 | 4.9% | 46.0% | 50.2% |
| low vol | OTM 1.03-1.1 | 4,477 | 7.52 | 5.64 | 0.23 | 5.41 | 18.9% | 75.0% | 41.8% |
| low vol | OTM 1.1-1.2 | 7,910 | 9.54 | 8.81 | 0.29 | 8.53 | 29.8% | 92.4% | 39.5% |
| low vol | far OTM >1.2 | 5,784 | 3.95 | 3.67 | 0.12 | 3.55 | 12.4% | 93.0% | 36.2% |
| mid vol | deep ITM <0.8 | 7,299 | 12.45 | 0.78 | 0.37 | 0.40 | 1.4% | 6.2% | 53.0% |
| mid vol | ITM 0.8-0.9 | 2,706 | 3.48 | 0.55 | 0.10 | 0.45 | 1.6% | 15.8% | 51.6% |
| mid vol | ITM 0.9-0.97 | 907 | 1.08 | 0.27 | 0.03 | 0.24 | 0.8% | 25.0% | 49.6% |
| mid vol | ATM 0.97-1.03 | 606 | 0.84 | 0.33 | 0.03 | 0.30 | 1.1% | 38.9% | 43.6% |
| mid vol | OTM 1.03-1.1 | 1,355 | 1.66 | 0.75 | 0.05 | 0.70 | 2.4% | 45.0% | 42.1% |
| mid vol | OTM 1.1-1.2 | 3,134 | 2.65 | 1.57 | 0.08 | 1.49 | 5.2% | 59.3% | 38.8% |
| mid vol | far OTM >1.2 | 7,002 | 3.87 | 3.03 | 0.12 | 2.92 | 10.2% | 78.3% | 37.5% |
| high vol | deep ITM <0.8 | 17,142 | 31.43 | 1.47 | 0.94 | 0.53 | 1.8% | 4.7% | 50.1% |
| high vol | ITM 0.8-0.9 | 1,989 | 2.29 | 0.34 | 0.07 | 0.28 | 1.0% | 15.1% | 48.0% |
| high vol | ITM 0.9-0.97 | 428 | 0.49 | 0.10 | 0.01 | 0.09 | 0.3% | 21.2% | 47.2% |
| high vol | ATM 0.97-1.03 | 273 | 0.47 | 0.21 | 0.01 | 0.20 | 0.7% | 45.1% | 43.6% |
| high vol | OTM 1.03-1.1 | 322 | 0.50 | 0.26 | 0.01 | 0.24 | 0.8% | 51.2% | 38.8% |
| high vol | OTM 1.1-1.2 | 606 | 0.53 | 0.29 | 0.02 | 0.28 | 1.0% | 55.6% | 36.5% |
| high vol | far OTM >1.2 | 2,385 | 1.36 | 0.80 | 0.04 | 0.76 | 2.7% | 59.2% | 36.4% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,108 | 12.43 | 14.47 | 0.37 | 14.10 | 47.8% | 116.4% | 40.6% |
| $0.25-0.5 | 9,217 | 9.11 | 6.49 | 0.27 | 6.22 | 21.1% | 71.3% | 37.1% |
| $0.5-1 | 7,753 | 8.96 | 4.53 | 0.27 | 4.26 | 14.5% | 50.6% | 37.7% |
| $1-2 | 5,815 | 7.12 | 2.46 | 0.21 | 2.25 | 7.6% | 34.6% | 43.5% |
| $2-5 | 12,372 | 15.74 | 2.51 | 0.47 | 2.04 | 6.9% | 15.9% | 50.8% |
| >$5 | 20,902 | 40.14 | 1.82 | 1.20 | 0.61 | 2.1% | 4.5% | 50.8% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 2,769 | 3.33 | 0.50 | 0.10 | 0.40 | 1.4% | 15.0% | 42.8% |
| 1999 | 2,601 | 3.26 | 0.20 | 0.10 | 0.10 | 0.3% | 6.0% | 38.1% |
| 2000 | 2,285 | 3.47 | 0.72 | 0.10 | 0.61 | 2.1% | 20.6% | 47.2% |
| 2001 | 2,879 | 3.67 | 0.32 | 0.11 | 0.20 | 0.7% | 8.6% | 39.6% |
| 2002 | 3,315 | 4.24 | 0.17 | 0.13 | 0.05 | 0.2% | 4.1% | 33.4% |
| 2003 | 4,345 | 4.55 | 3.87 | 0.14 | 3.73 | 12.7% | 85.0% | 57.9% |
| 2004 | 5,280 | 5.74 | 2.10 | 0.17 | 1.93 | 6.5% | 36.6% | 40.4% |
| 2005 | 5,771 | 6.67 | 2.37 | 0.20 | 2.17 | 7.3% | 35.5% | 43.7% |
| 2006 | 5,988 | 7.06 | 2.31 | 0.21 | 2.10 | 7.1% | 32.7% | 39.5% |
| 2007 | 6,485 | 8.15 | 3.19 | 0.24 | 2.94 | 10.0% | 39.1% | 37.4% |
| 2008 | 4,591 | 8.53 | -1.25 | 0.26 | -1.51 | -5.1% | -14.7% | 29.8% |
| 2009 | 4,854 | 8.08 | 2.29 | 0.24 | 2.05 | 7.0% | 28.4% | 59.5% |
| 2010 | 7,105 | 9.56 | 4.08 | 0.29 | 3.79 | 12.8% | 42.6% | 52.1% |
| 2012 | 7,638 | 10.92 | 3.33 | 0.33 | 3.00 | 10.2% | 30.5% | 45.7% |
| 2013 | 4,261 | 6.27 | 8.12 | 0.19 | 7.93 | 26.9% | 129.4% | 60.9% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 8.58% | 2.18% | 3.78 | -10.3% | 70,167 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 8.58% | 2.18% | 3.78 | -10.3% | 70,167 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 8.58% | 2.18% | 3.78 | -10.3% | 70,167 |
| dead quotes worth zero (pessimistic bound) | 4.11% | 2.36% | 1.72 | -14.5% | 64,734 |
| + $0.10/share minimum cost | 4.78% | 2.22% | 2.12 | -12.0% | 65,810 |
| skip contracts under $0.25 | 5.49% | 1.88% | 2.85 | -11.3% | 54,871 |
| skip contracts under $0.50 | 3.71% | 1.69% | 2.16 | -11.1% | 45,034 |
| flat 10% round-trip cost | 6.43% | 2.19% | 2.86 | -12.6% | 67,625 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
