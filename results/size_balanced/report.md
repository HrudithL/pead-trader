# size_balanced

_equity strategy | family: equity-baseline | status: validated | derived from `extreme_picks`_

## What this strategy does

Extreme Picks, with an equal budget for each of the five company sizes

**Why it should work.** PEAD is stronger in small caps; balancing by size keeps the edge but removes a size bet.

**Design notes** (from the strategy's source file).

Strategy 3 -- Size-balanced extreme decile.

Strategy 1's trade, but each leg's budget is spread evenly over the size quintiles present that quarter, so micro-caps (where most extreme surprises happen) cannot dominate the book.

| parameter | value |
|---|---|
| base_unit_fraction | 0.003 |
| max_gross_leverage | 1.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | proportional |
| beta_overlay | 0.0 |

## Performance

| metric | size_balanced | market (CRSP VW) |
|---|---|---|
| Annualized return | 4.74% | 9.94% |
| Annualized volatility | 5.26% | 19.35% |
| Sharpe (NAV-pct) | 0.91 | 0.59 |
| Sortino | 1.26 |  |
| Max drawdown | -17.5% | -55.5% |
| Calmar | 0.27 |  |
| Final NAV ($10M start) | $24,622,256 |  |
| Worst day | -4.30% |  |
| % positive days | 52.2% |  |
| Sharpe 1996-2001 | 1.32 |  |
| Sharpe 2002-2007 | 1.28 |  |
| Sharpe 2008-2013 | 0.34 |  |

Correlation of daily returns with the market: **0.00**.

- best Sharpe among equity: `calm_rank_tilt` (1.81); this one: 0.906
- best return among equity: `market_boost_max` (0.15); this one: 0.0474
- best Calmar among equity: `calm_rank_tilt` (0.614); this one: 0.27

![NAV (log scale)](figures/nav.png)

*NAV (log scale)*

![Drawdown from running peak](figures/drawdown.png)

*Drawdown from running peak*

![Calendar-year returns](figures/yearly.png)

*Calendar-year returns*

![Is the edge stable, or concentrated in a few years?](figures/rolling_sharpe.png)

*Is the edge stable, or concentrated in a few years?*

## Where the return comes from

![Long leg vs. short leg vs. total](figures/legs.png)

*Long leg vs. short leg vs. total*

|  | long leg | short leg |
|---|---|---|
| P&L ($M, before costs) | 23.53 | -5.75 |
| P&L after costs ($M) | 22.05 | -7.43 |
| of which common (average-event) component ($M) | 12.07 | -12.69 |
| of which excess / PEAD component ($M) | 11.46 | 6.95 |
| excess minus costs ($M) | 9.98 | 5.26 |
| gross return per $ traded (bp) | 309.93 | -70.95 |
| positions in the money (hit rate) | 0.57 | 0.49 |
| stand-alone Sharpe (leg only) | 1.16 | -0.23 |

Daily P&L correlation between the two legs: **-0.64**.

- **The short leg loses money in absolute terms** (-5.7M before costs) while the long leg earns +23.5M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 12.1M / 12.7M. Stripping that common component, the surprise signal itself earned +11.5M on the long side and +6.9M on the short side.
- The two legs' daily P&L are -0.64 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.16 long / -0.23 short vs 0.91 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 10 | 17,174 | 759.10 | 22.05 | 11.46 | 1.48 | 9.98 | 65.5% | 131.48 |
| short | 1 | 17,169 | 809.89 | -7.43 | 6.95 | 1.68 | 5.26 | 34.5% | 64.99 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,832 | 149.12 | 2.16 | 3.31 | 0.54 | 2.77 | 18.2% | 185.60 |
| 2 | 7,159 | 327.44 | 4.51 | 5.49 | 0.90 | 4.59 | 30.1% | 140.12 |
| 3 | 7,735 | 358.82 | 5.24 | 5.93 | 0.74 | 5.19 | 34.0% | 144.64 |
| 4 | 7,731 | 365.93 | 1.70 | 2.26 | 0.57 | 1.69 | 11.1% | 46.23 |
| 5 | 6,886 | 367.69 | 1.01 | 1.42 | 0.41 | 1.01 | 6.6% | 27.39 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,357 | 93.37 | 2.65 | 3.33 | 0.47 | 2.86 | 18.8% | 306.58 |
| 2 | 7,223 | 337.61 | 3.47 | 5.06 | 1.01 | 4.05 | 26.6% | 119.93 |
| 3 | 7,834 | 386.64 | 5.77 | 6.76 | 0.77 | 5.99 | 39.3% | 154.91 |
| 4 | 7,850 | 385.18 | 1.56 | 2.11 | 0.54 | 1.57 | 10.3% | 40.68 |
| 5 | 7,077 | 366.08 | 1.20 | 1.17 | 0.37 | 0.80 | 5.2% | 21.83 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 6,338 | 335.10 | -0.26 | 3.39 | 0.51 | 2.88 | 18.9% | 85.99 |
| 41-60d | 11,202 | 486.79 | 1.49 | 2.35 | 0.99 | 1.36 | 8.9% | 27.93 |
| 61-90d | 12,665 | 583.62 | 5.83 | 5.09 | 1.17 | 3.92 | 25.7% | 67.14 |
| >90d | 4,138 | 163.48 | 7.56 | 7.57 | 0.48 | 7.08 | 46.5% | 433.38 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 9,920 | 371.88 | 5.68 | 6.62 | 0.71 | 5.92 | 38.8% | 159.05 |
| 2002-07 | 11,506 | 527.09 | 5.64 | 6.77 | 1.04 | 5.73 | 37.6% | 108.64 |
| 2008-13 | 12,917 | 670.02 | 3.30 | 5.01 | 1.41 | 3.60 | 23.6% | 53.78 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 6,718 | 298.31 | 2.57 | 1.93 | 0.59 | 1.33 | 8.7% | 44.67 |
| Chems | 687 | 31.76 | -0.19 | -0.14 | 0.06 | -0.20 | -1.3% | -61.42 |
| Durbl | 869 | 38.28 | 0.26 | 0.51 | 0.08 | 0.43 | 2.8% | 111.43 |
| Enrgy | 1,375 | 66.03 | 1.65 | 1.81 | 0.13 | 1.68 | 11.0% | 254.65 |
| Hlth | 3,037 | 133.90 | 1.35 | 1.18 | 0.29 | 0.89 | 5.8% | 66.46 |
| Manuf | 3,627 | 163.12 | 0.76 | 1.50 | 0.32 | 1.18 | 7.8% | 72.50 |
| Money | 5,366 | 255.16 | 1.65 | 3.64 | 0.50 | 3.14 | 20.6% | 123.09 |
| NoDur | 1,540 | 71.33 | 0.94 | 1.05 | 0.14 | 0.91 | 6.0% | 127.65 |
| Other | 5,074 | 228.66 | 2.19 | 2.65 | 0.51 | 2.14 | 14.1% | 93.69 |
| Shops | 3,547 | 160.84 | 2.53 | 2.91 | 0.32 | 2.59 | 17.0% | 160.85 |
| Telcm | 1,164 | 55.57 | 0.74 | 1.00 | 0.11 | 0.89 | 5.8% | 160.29 |
| Utils | 1,339 | 66.02 | 0.17 | 0.36 | 0.11 | 0.26 | 1.7% | 38.57 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 11,517 | 531.47 | 2.60 | 4.04 | 0.96 | 3.08 | 20.2% | 57.99 |
| low vol | 11,554 | 516.11 | 6.39 | 7.84 | 1.15 | 6.70 | 44.0% | 129.83 |
| mid vol | 10,730 | 492.43 | 5.30 | 6.13 | 0.99 | 5.14 | 33.7% | 104.34 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 4.74% | 0.00% |
| 0.5% | 0.9 | 4.53% | 0.20% |
| 1.0% | 1.8 | 4.32% | 0.41% |
| 2.0% | 3.6 | 3.88% | 0.85% |
| 5.0% | 9.1 | 2.29% | 2.44% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
