# volatility_filter

_equity strategy | family: equity-volatility | status: research | derived from `balanced_leverage`_

## What this strategy does

Balanced Leverage, skipping long/short x jumpiness groups that have not made money so far

**Why it should work.** Skip the cells that history says do not pay -- but the data shows a gradient, not a bad cell.

**Design notes** (from the strategy's source file).

Volatility Filter (and Calm Weighted Filter) -- skip stock groups that have not paid, judged only on past data.

Drops a (side, idio-vol tercile) cell from new entries when its own history says it has not earned its keep, judged ONLY on quarters whose positions had fully exited. Result: filtering does not help on its own (corrected run: Sharpe 0.89 vs 1.09 for Balanced Leverage; pre-fix 1.23 vs 1.30; the effect is a smooth gradient, not one broken cell) and adds nothing once inverse-vol sizing is on (corrected: Calm Weighted Filter 1.08 < Calm Weighted 1.24; pre-fix 1.41 < 1.47). Kept as a documented negative result: size by vol, do not filter by it.

| parameter | value |
|---|---|
| base_unit_fraction | 0.003 |
| max_gross_leverage | 2.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| exposure_cap | open_book |
| beta_overlay | 0.0 |

## Performance

| metric | volatility_filter | market (CRSP VW) |
|---|---|---|
| Annualized return | 4.60% | 9.94% |
| Annualized volatility | 5.20% | 19.35% |
| Sharpe (NAV-pct) | 0.89 | 0.59 |
| Sortino | 1.27 |  |
| Max drawdown | -18.8% | -55.5% |
| Calmar | 0.25 |  |
| Final NAV ($10M start) | $24,019,423 |  |
| Worst day | -4.29% |  |
| % positive days | 49.9% |  |
| Sharpe 1996-2001 | 1.43 |  |
| Sharpe 2002-2007 | 1.07 |  |
| Sharpe 2008-2013 | 0.19 |  |

Correlation of daily returns with the market: **0.03**.

- best Sharpe among equity: `calm_rank_tilt` (1.47); this one: 0.891
- best return among equity: `market_boost_max` (0.116); this one: 0.046
- best Calmar among equity: `calm_rank_tilt` (0.442); this one: 0.245

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
| P&L ($M, before costs) | 32.04 | -12.71 |
| P&L after costs ($M) | 29.45 | -15.43 |
| of which common (average-event) component ($M) | 20.72 | -21.05 |
| of which excess / PEAD component ($M) | 11.31 | 8.34 |
| excess minus costs ($M) | 8.72 | 5.62 |
| gross return per $ traded (bp) | 247.91 | -96.95 |
| positions in the money (hit rate) | 0.56 | 0.48 |
| stand-alone Sharpe (leg only) | 1.17 | -0.38 |

Daily P&L correlation between the two legs: **-0.78**.

- **The short leg loses money in absolute terms** (-12.7M before costs) while the long leg earns +32.0M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 20.7M / 21.1M. Stripping that common component, the surprise signal itself earned +11.3M on the long side and +8.3M on the short side.
- The two legs' daily P&L are -0.78 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.17 long / -0.38 short vs 0.89 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 5,837 | 84.34 | 1.35 | 0.08 | 0.17 | -0.10 | -0.7% | -11.29 |
| long | 8 | 15,020 | 282.62 | 5.69 | 1.08 | 0.57 | 0.51 | 3.6% | 18.14 |
| long | 9 | 17,362 | 397.36 | 8.31 | 3.63 | 0.79 | 2.84 | 19.8% | 71.48 |
| long | 10 | 19,198 | 527.91 | 14.10 | 6.53 | 1.06 | 5.46 | 38.1% | 103.53 |
| short | 1 | 17,107 | 522.20 | -6.32 | 3.59 | 1.12 | 2.47 | 17.2% | 47.22 |
| short | 2 | 17,394 | 451.29 | -5.08 | 3.17 | 0.92 | 2.25 | 15.7% | 49.92 |
| short | 3 | 13,787 | 300.06 | -3.45 | 1.60 | 0.60 | 0.99 | 6.9% | 33.15 |
| short | 4 | 2,168 | 37.34 | -0.58 | -0.02 | 0.07 | -0.09 | -0.6% | -23.82 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 12,911 | 253.77 | 0.22 | 1.74 | 0.98 | 0.76 | 5.3% | 30.11 |
| 2 | 20,633 | 496.97 | 5.33 | 6.97 | 1.40 | 5.57 | 38.8% | 112.09 |
| 3 | 23,152 | 592.28 | 3.80 | 5.00 | 1.25 | 3.75 | 26.1% | 63.31 |
| 4 | 25,014 | 626.51 | 2.96 | 3.73 | 0.98 | 2.74 | 19.1% | 43.78 |
| 5 | 26,163 | 633.59 | 1.70 | 2.22 | 0.70 | 1.52 | 10.6% | 23.99 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 12,343 | 188.03 | 2.26 | 3.54 | 0.94 | 2.60 | 18.1% | 138.26 |
| 2 | 21,074 | 533.40 | 3.17 | 5.47 | 1.60 | 3.87 | 27.0% | 72.63 |
| 3 | 23,523 | 634.05 | 4.44 | 5.86 | 1.27 | 4.60 | 32.0% | 72.49 |
| 4 | 24,959 | 628.11 | 2.99 | 3.40 | 0.88 | 2.52 | 17.6% | 40.12 |
| 5 | 25,970 | 619.37 | 1.15 | 1.36 | 0.62 | 0.74 | 5.2% | 11.99 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 38,259 | 889.88 | -0.30 | 6.33 | 1.71 | 4.63 | 32.3% | 52.00 |
| 41-60d | 25,572 | 650.73 | 1.94 | 4.69 | 1.44 | 3.25 | 22.7% | 50.02 |
| 61-90d | 35,161 | 855.70 | 7.46 | 4.41 | 1.58 | 2.83 | 19.7% | 33.01 |
| >90d | 8,881 | 206.81 | 4.93 | 4.22 | 0.58 | 3.64 | 25.4% | 175.99 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 31,383 | 620.57 | 6.69 | 7.87 | 1.23 | 6.64 | 46.3% | 107.05 |
| 2002-07 | 34,703 | 870.62 | 5.61 | 7.32 | 1.74 | 5.58 | 38.9% | 64.09 |
| 2008-13 | 41,787 | 1,111.94 | 1.71 | 4.47 | 2.34 | 2.12 | 14.8% | 19.11 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 18,723 | 246.30 | 0.19 | 0.65 | 0.52 | 0.12 | 0.9% | 5.08 |
| Chems | 2,528 | 177.05 | 0.67 | 1.01 | 0.32 | 0.69 | 4.8% | 39.15 |
| Durbl | 2,772 | 192.97 | 1.35 | 1.80 | 0.36 | 1.43 | 10.0% | 74.24 |
| Enrgy | 6,145 | 218.16 | 2.46 | 2.89 | 0.42 | 2.47 | 17.2% | 113.13 |
| Hlth | 10,525 | 246.44 | 0.98 | 1.50 | 0.52 | 0.97 | 6.8% | 39.58 |
| Manuf | 11,344 | 232.51 | 1.16 | 1.72 | 0.49 | 1.23 | 8.5% | 52.70 |
| Money | 15,356 | 215.07 | 1.88 | 2.45 | 0.50 | 1.95 | 13.6% | 90.62 |
| NoDur | 4,848 | 214.58 | 0.86 | 1.23 | 0.42 | 0.82 | 5.7% | 38.03 |
| Other | 16,089 | 235.74 | 1.53 | 2.10 | 0.53 | 1.57 | 11.0% | 66.72 |
| Shops | 11,147 | 243.84 | 2.49 | 3.00 | 0.51 | 2.49 | 17.3% | 102.04 |
| Telcm | 3,731 | 212.11 | 0.70 | 1.07 | 0.40 | 0.67 | 4.7% | 31.62 |
| Utils | 4,665 | 168.35 | -0.24 | 0.25 | 0.32 | -0.07 | -0.5% | -4.10 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 33,987 | 765.25 | -0.67 | 1.76 | 1.41 | 0.35 | 2.5% | 4.60 |
| low vol | 36,925 | 910.29 | 7.29 | 9.27 | 2.03 | 7.24 | 50.4% | 79.50 |
| mid vol | 35,223 | 879.88 | 6.62 | 7.78 | 1.76 | 6.02 | 42.0% | 68.45 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 4.60% | -0.00% |
| 0.5% | 1.3 | 4.30% | 0.30% |
| 1.0% | 2.6 | 3.98% | 0.62% |
| 2.0% | 5.3 | 3.28% | 1.33% |
| 5.0% | 13.2 | 0.40% | 4.21% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
