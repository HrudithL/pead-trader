# options_cap30

_options strategy | family: options-kelly | status: validated | derived from `options_cap20`_

## What this strategy does

Kelly picks, 30% premium cap, bought at the ask / sold at the bid, dead quotes at half-ask

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
| roundtrip_cost_pct | 0.0 |
| dead_quote_mark | zero_bid_half_ask |
| fill_mode | bid_ask |
| min_roundtrip_cost_per_share | 0.0 |
| min_premium | 0.0 |

## Performance

| metric | options_cap30 | market (CRSP VW) |
|---|---|---|
| Annualized return | -0.10% | 8.48% |
| Annualized volatility | 3.01% | 19.86% |
| Sharpe (NAV-pct) | -0.02 | 0.51 |
| Sortino | -0.02 |  |
| Max drawdown | -29.0% | -55.5% |
| Calmar | -0.00 |  |
| Final NAV ($10M start) | $9,841,407 |  |
| Worst day | -1.56% |  |
| % positive days | 42.6% |  |
| Sharpe 1996-2001 | -0.48 |  |
| Sharpe 2002-2007 | 0.28 |  |
| Sharpe 2008-2013 | -0.10 |  |

Correlation of daily returns with the market: **0.19**.

- best Sharpe among options: `options_cap20` (1.77); this one: -0.0168
- best return among options: this strategy; this one: -0.000958
- best Calmar among options: `options_cap20` (0.298); this one: -0.00331

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

- **97.3% of funded trades are calls** and they produce **296.1%** of gross P&L -- even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call has more convexity per dollar than a put once the bad news is in the price).
- Hit rate is only 31.1% and the median trade returns -46.7% on premium, yet the mean is -0.2%: a classic convex pay-off. The best 1% of trades produce **-5208%** of gross P&L and the best 10% produce -13815%.
- Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model.

### By contract side

| leg | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| call | 61,357 | 69.81 | -0.47 | 0.00 | -0.47 | 296.1% | -0.7% | 31.4% |
| put | 1,712 | 1.49 | 0.31 | 0.00 | 0.31 | -196.1% | 20.9% | 19.7% |

### By SUE decile

| decile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1 | 5,235 | 3.78 | -0.36 | 0.00 | -0.36 | 225.7% | -9.5% | 22.3% |
| 2 | 4,882 | 3.38 | -0.13 | 0.00 | -0.13 | 80.3% | -3.8% | 23.7% |
| 3 | 4,782 | 3.44 | 0.08 | 0.00 | 0.08 | -48.6% | 2.2% | 27.5% |
| 4 | 6,701 | 5.24 | -0.40 | 0.00 | -0.40 | 253.1% | -7.7% | 26.1% |
| 5 | 3,394 | 3.03 | 0.14 | 0.00 | 0.14 | -88.1% | 4.6% | 31.6% |
| 6 | 3,943 | 3.66 | 0.36 | 0.00 | 0.36 | -225.4% | 9.8% | 32.3% |
| 7 | 7,135 | 6.75 | -0.59 | 0.00 | -0.59 | 370.1% | -8.7% | 29.6% |
| 8 | 7,838 | 10.34 | -0.15 | 0.00 | -0.15 | 93.9% | -1.4% | 34.5% |
| 9 | 8,581 | 12.53 | 0.19 | 0.00 | 0.19 | -120.4% | 1.5% | 35.2% |
| 10 | 10,578 | 19.15 | 0.70 | 0.00 | 0.70 | -440.6% | 3.6% | 38.5% |

### By side and moneyness (strike / spot)

| leg | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| call | deep ITM <0.8 | 19,085 | 32.34 | -1.44 | 0.00 | -1.44 | 906.2% | -4.4% | 43.6% |
| call | ITM 0.8-0.9 | 4,753 | 6.02 | -0.14 | 0.00 | -0.14 | 85.4% | -2.2% | 41.9% |
| call | ITM 0.9-0.97 | 1,973 | 2.50 | -0.06 | 0.00 | -0.06 | 40.6% | -2.6% | 38.3% |
| call | ATM 0.97-1.03 | 2,449 | 3.43 | 0.13 | 0.00 | 0.13 | -82.7% | 3.8% | 35.7% |
| call | OTM 1.03-1.1 | 6,040 | 7.70 | 0.82 | 0.00 | 0.82 | -519.4% | 10.7% | 31.7% |
| call | OTM 1.1-1.2 | 11,648 | 10.30 | 0.85 | 0.00 | 0.85 | -534.0% | 8.2% | 25.1% |
| call | far OTM >1.2 | 15,409 | 7.50 | -0.63 | 0.00 | -0.63 | 400.0% | -8.5% | 16.2% |
| put | deep ITM <0.8 | 473 | 0.24 | 0.13 | 0.00 | 0.13 | -79.9% | 53.2% | 15.2% |
| put | ITM 0.8-0.9 | 673 | 0.52 | 0.23 | 0.00 | 0.23 | -146.8% | 45.1% | 21.2% |
| put | ITM 0.9-0.97 | 284 | 0.32 | 0.04 | 0.00 | 0.04 | -25.4% | 12.5% | 23.6% |
| put | ATM 0.97-1.03 | 150 | 0.28 | -0.08 | 0.00 | -0.08 | 49.3% | -28.2% | 12.0% |
| put | OTM 1.03-1.1 | 73 | 0.09 | -0.01 | 0.00 | -0.01 | 7.8% | -13.6% | 24.7% |
| put | OTM 1.1-1.2 | 29 | 0.02 | 0.01 | 0.00 | 0.01 | -4.5% | 30.5% | 41.4% |
| put | far OTM >1.2 | 30 | 0.02 | -0.01 | 0.00 | -0.01 | 3.3% | -26.9% | 26.7% |

### By the stock's pre-event volatility tercile

| vol_tercile | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| low vol | 21,018 | 20.68 | 1.51 | 0.00 | 1.51 | -952.7% | 7.3% | 25.9% |
| mid vol | 21,017 | 19.43 | -0.08 | 0.00 | -0.08 | 48.2% | -0.4% | 30.3% |
| high vol | 21,018 | 31.15 | -1.59 | 0.00 | -1.59 | 1005.4% | -5.1% | 37.2% |

### Calls only: volatility x moneyness

| vol_tercile | moneyness_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|---|
| low vol | deep ITM <0.8 | 518 | 0.67 | -0.04 | 0.00 | -0.04 | 9.4% | -6.6% | 41.1% |
| low vol | ITM 0.8-0.9 | 660 | 0.82 | -0.12 | 0.00 | -0.12 | 25.0% | -14.2% | 34.4% |
| low vol | ITM 0.9-0.97 | 708 | 0.94 | -0.07 | 0.00 | -0.07 | 14.9% | -7.4% | 36.9% |
| low vol | ATM 0.97-1.03 | 1,538 | 2.25 | 0.13 | 0.00 | 0.13 | -27.9% | 5.8% | 37.6% |
| low vol | OTM 1.03-1.1 | 4,220 | 5.62 | 0.79 | 0.00 | 0.79 | -167.6% | 14.0% | 31.7% |
| low vol | OTM 1.1-1.2 | 7,459 | 7.07 | 0.67 | 0.00 | 0.67 | -142.7% | 9.5% | 24.3% |
| low vol | far OTM >1.2 | 5,177 | 2.66 | 0.06 | 0.00 | 0.06 | -12.0% | 2.1% | 17.2% |
| mid vol | deep ITM <0.8 | 4,561 | 6.65 | -0.29 | 0.00 | -0.29 | 62.1% | -4.4% | 45.6% |
| mid vol | ITM 0.8-0.9 | 2,152 | 2.85 | -0.02 | 0.00 | -0.02 | 4.2% | -0.7% | 44.7% |
| mid vol | ITM 0.9-0.97 | 831 | 1.07 | 0.03 | 0.00 | 0.03 | -7.0% | 3.1% | 39.6% |
| mid vol | ATM 0.97-1.03 | 633 | 0.79 | 0.02 | 0.00 | 0.02 | -3.5% | 2.1% | 33.6% |
| mid vol | OTM 1.03-1.1 | 1,447 | 1.59 | 0.10 | 0.00 | 0.10 | -21.5% | 6.4% | 33.3% |
| mid vol | OTM 1.1-1.2 | 3,446 | 2.64 | 0.21 | 0.00 | 0.21 | -45.7% | 8.1% | 27.3% |
| mid vol | far OTM >1.2 | 7,336 | 3.40 | -0.39 | 0.00 | -0.39 | 82.3% | -11.4% | 16.6% |
| high vol | deep ITM <0.8 | 14,002 | 25.02 | -1.11 | 0.00 | -1.11 | 235.5% | -4.4% | 43.1% |
| high vol | ITM 0.8-0.9 | 1,940 | 2.34 | 0.00 | 0.00 | 0.00 | -0.8% | 0.2% | 41.4% |
| high vol | ITM 0.9-0.97 | 433 | 0.48 | -0.03 | 0.00 | -0.03 | 5.9% | -5.7% | 37.9% |
| high vol | ATM 0.97-1.03 | 277 | 0.40 | -0.02 | 0.00 | -0.02 | 3.6% | -4.3% | 29.6% |
| high vol | OTM 1.03-1.1 | 373 | 0.49 | -0.06 | 0.00 | -0.06 | 13.7% | -13.0% | 26.0% |
| high vol | OTM 1.1-1.2 | 738 | 0.59 | -0.04 | 0.00 | -0.04 | 8.0% | -6.3% | 23.6% |
| high vol | far OTM >1.2 | 2,892 | 1.44 | -0.30 | 0.00 | -0.30 | 64.6% | -21.0% | 13.7% |

### By premium per share

| premium_bucket | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| <$0.25 | 14,378 | 9.36 | 0.68 | 0.00 | 0.68 | -429.7% | 7.3% | 16.5% |
| $0.25-0.5 | 9,456 | 7.56 | 0.26 | 0.00 | 0.26 | -164.4% | 3.4% | 22.4% |
| $0.5-1 | 7,937 | 7.41 | 0.33 | 0.00 | 0.33 | -204.7% | 4.4% | 27.7% |
| $1-2 | 5,653 | 5.67 | 0.19 | 0.00 | 0.19 | -121.0% | 3.4% | 35.4% |
| $2-5 | 10,318 | 11.48 | -0.30 | 0.00 | -0.30 | 189.2% | -2.6% | 40.9% |
| >$5 | 15,327 | 29.80 | -1.32 | 0.00 | -1.32 | 830.6% | -4.4% | 43.8% |

### By year

| year | trades | premium_M | gross_M | cost_M | net_M | share_of_net | roi_on_premium | hit_rate |
|---|---|---|---|---|---|---|---|---|
| 1998 | 2,724 | 5.62 | 0.47 | 0.00 | 0.47 | -297.6% | 8.4% | 37.6% |
| 1999 | 2,651 | 5.36 | -0.34 | 0.00 | -0.34 | 211.1% | -6.3% | 31.8% |
| 2000 | 2,567 | 5.36 | -0.02 | 0.00 | -0.02 | 9.9% | -0.3% | 38.8% |
| 2001 | 2,976 | 5.12 | -0.78 | 0.00 | -0.78 | 491.5% | -15.2% | 29.5% |
| 2002 | 3,390 | 5.23 | -1.04 | 0.00 | -1.04 | 654.0% | -19.8% | 22.7% |
| 2003 | 4,223 | 4.76 | 1.73 | 0.00 | 1.73 | -1091.0% | 36.4% | 46.7% |
| 2004 | 5,106 | 5.39 | -0.04 | 0.00 | -0.04 | 25.3% | -0.7% | 27.7% |
| 2005 | 5,420 | 5.38 | -0.59 | 0.00 | -0.59 | 372.3% | -11.0% | 29.9% |
| 2006 | 5,363 | 4.80 | 0.13 | 0.00 | 0.13 | -81.8% | 2.7% | 27.8% |
| 2007 | 5,664 | 5.03 | 0.36 | 0.00 | 0.36 | -229.5% | 7.2% | 25.9% |
| 2008 | 3,642 | 4.78 | -1.61 | 0.00 | -1.61 | 1017.3% | -33.8% | 18.4% |
| 2009 | 3,640 | 3.79 | 0.26 | 0.00 | 0.26 | -163.8% | 6.8% | 41.5% |
| 2010 | 5,841 | 4.25 | 0.41 | 0.00 | 0.41 | -255.3% | 9.5% | 34.5% |
| 2012 | 6,173 | 4.25 | -0.67 | 0.00 | -0.67 | 420.8% | -15.7% | 23.6% |
| 2013 | 3,689 | 2.18 | 1.56 | 0.00 | 1.56 | -983.0% | 71.5% | 40.7% |

### Execution sensitivity (the backtest assumes a flat 3% round-trip cost)

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | -0.10% | 3.01% | -0.02 | -29.0% | 63,069 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 3.14% | 2.89% | 1.08 | -20.6% | 66,490 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | -0.10% | 3.01% | -0.02 | -29.0% | 63,069 |
| dead quotes worth zero (pessimistic bound) | -2.06% | 3.07% | -0.66 | -48.7% | 60,566 |
| + $0.10/share minimum cost | -5.38% | 3.08% | -1.78 | -69.8% | 55,589 |
| skip contracts under $0.25 | -0.38% | 2.77% | -0.12 | -28.8% | 50,908 |
| skip contracts under $0.50 | -0.56% | 2.57% | -0.20 | -30.2% | 42,202 |
| flat 10% round-trip cost | -4.13% | 3.01% | -1.39 | -63.0% | 56,438 |

Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.
