# graded_bets_legacy

_equity strategy | family: equity-baseline | status: reference | derived from `graded_bets`_

## What this strategy does

Bet on every stock; the more extreme the surprise, the bigger the bet -- ORIGINAL leverage cap (new positions only, open book ignored)

**Why it should work.** Same drift as Extreme Picks but harvested across the whole cross-section for lower variance.

**Design notes** (from the strategy's source file).

Graded Bets -- every stock gets a bet, bigger the further its surprise is from average.

Trades every decile with weight proportional to SUE-rank distance from the median: broader diversification than the extreme-decile book, at the price of ~4x more positions (which saturates the liquidity and leverage caps and dilutes the high-conviction names).

| parameter | value |
|---|---|
| base_unit_fraction | 0.002 |
| max_gross_leverage | 1.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | proportional |
| exposure_cap | entry_cohort |
| beta_overlay | 0.0 |

## Performance

| metric | graded_bets_legacy | market (CRSP VW) |
|---|---|---|
| Annualized return | 3.88% | 9.94% |
| Annualized volatility | 3.86% | 19.35% |
| Sharpe (NAV-pct) | 1.01 | 0.59 |
| Sortino | 1.42 |  |
| Max drawdown | -14.0% | -55.5% |
| Calmar | 0.28 |  |
| Final NAV ($10M start) | $20,996,808 |  |
| Worst day | -2.34% |  |
| % positive days | 52.4% |  |
| Sharpe 1996-2001 | 1.39 |  |
| Sharpe 2002-2007 | 1.55 |  |
| Sharpe 2008-2013 | 0.32 |  |

Correlation of daily returns with the market: **0.01**.

- best Sharpe among equity: `calm_rank_tilt` (1.47); this one: 1.01
- best return among equity: `market_boost_max` (0.116); this one: 0.0388
- best Calmar among equity: `calm_rank_tilt` (0.442); this one: 0.278

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
| P&L ($M, before costs) | 21.70 | -7.13 |
| P&L after costs ($M) | 20.00 | -9.00 |
| of which common (average-event) component ($M) | 14.23 | -13.67 |
| of which excess / PEAD component ($M) | 7.27 | 6.71 |
| excess minus costs ($M) | 5.58 | 4.83 |
| gross return per $ traded (bp) | 248.49 | -83.56 |
| positions in the money (hit rate) | 0.55 | 0.48 |
| stand-alone Sharpe (leg only) | 1.19 | -0.33 |

Daily P&L correlation between the two legs: **-0.79**.

- **The short leg loses money in absolute terms** (-7.1M before costs) while the long leg earns +21.7M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 14.2M / 13.7M. Stripping that common component, the surprise signal itself earned +7.3M on the long side and +6.7M on the short side.
- The two legs' daily P&L are -0.79 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.19 long / -0.33 short vs 1.01 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 6 | 18,256 | 41.69 | 0.34 | -0.14 | 0.09 | -0.22 | -2.2% | -53.75 |
| long | 7 | 17,431 | 104.34 | 1.62 | -0.10 | 0.20 | -0.30 | -2.9% | -28.97 |
| long | 8 | 16,588 | 169.97 | 3.99 | 1.32 | 0.33 | 0.98 | 9.4% | 57.88 |
| long | 9 | 17,747 | 248.03 | 4.65 | 1.39 | 0.47 | 0.92 | 8.8% | 37.08 |
| long | 10 | 17,174 | 309.23 | 9.39 | 4.80 | 0.60 | 4.20 | 40.3% | 135.80 |
| short | 1 | 17,169 | 295.94 | -3.37 | 1.95 | 0.68 | 1.27 | 12.2% | 42.89 |
| short | 2 | 17,281 | 238.00 | -2.19 | 2.25 | 0.52 | 1.73 | 16.6% | 72.66 |
| short | 3 | 15,970 | 163.81 | -1.95 | 1.12 | 0.36 | 0.76 | 7.3% | 46.34 |
| short | 4 | 21,469 | 125.93 | -1.12 | 1.14 | 0.26 | 0.88 | 8.4% | 69.86 |
| short | 5 | 14,319 | 29.09 | -0.37 | 0.25 | 0.06 | 0.19 | 1.8% | 65.99 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 19,578 | 167.94 | 1.68 | 3.16 | 0.68 | 2.48 | 23.8% | 147.53 |
| 2 | 32,212 | 338.94 | 3.21 | 4.51 | 0.98 | 3.53 | 33.9% | 104.14 |
| 3 | 36,915 | 385.67 | 3.11 | 3.61 | 0.81 | 2.80 | 26.9% | 72.66 |
| 4 | 40,327 | 411.63 | 1.71 | 1.75 | 0.64 | 1.11 | 10.7% | 27.04 |
| 5 | 44,372 | 421.85 | 1.28 | 0.95 | 0.47 | 0.48 | 4.6% | 11.46 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 19,171 | 149.55 | 2.93 | 4.06 | 0.75 | 3.32 | 31.8% | 221.69 |
| 2 | 32,362 | 347.80 | 2.17 | 3.96 | 1.04 | 2.92 | 28.0% | 83.97 |
| 3 | 37,415 | 391.66 | 2.82 | 3.69 | 0.78 | 2.90 | 27.9% | 74.10 |
| 4 | 40,575 | 413.50 | 1.82 | 1.81 | 0.58 | 1.24 | 11.9% | 29.89 |
| 5 | 43,876 | 423.44 | 1.27 | 0.46 | 0.42 | 0.04 | 0.3% | 0.83 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 77,748 | 665.87 | -0.91 | 1.80 | 1.22 | 0.58 | 5.6% | 8.69 |
| 41-60d | 33,200 | 382.82 | 2.00 | 2.13 | 0.81 | 1.32 | 12.7% | 34.47 |
| 61-90d | 48,100 | 512.60 | 3.48 | 4.24 | 1.11 | 3.12 | 30.0% | 60.97 |
| >90d | 14,356 | 164.75 | 6.44 | 5.82 | 0.44 | 5.38 | 51.7% | 326.71 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 50,364 | 426.09 | 4.45 | 5.12 | 0.85 | 4.26 | 40.9% | 100.08 |
| 2002-07 | 58,164 | 591.79 | 4.61 | 5.74 | 1.19 | 4.55 | 43.7% | 76.96 |
| 2008-13 | 64,876 | 708.16 | 1.94 | 3.13 | 1.54 | 1.59 | 15.2% | 22.42 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 32,133 | 320.84 | 2.65 | 1.96 | 0.66 | 1.30 | 12.5% | 40.49 |
| Chems | 3,926 | 37.70 | 0.07 | 0.17 | 0.07 | 0.10 | 1.0% | 26.72 |
| Durbl | 4,066 | 40.94 | 0.35 | 0.53 | 0.09 | 0.44 | 4.2% | 107.21 |
| Enrgy | 9,095 | 85.98 | 0.87 | 1.08 | 0.16 | 0.92 | 8.8% | 106.99 |
| Hlth | 17,555 | 165.49 | 1.56 | 1.49 | 0.38 | 1.11 | 10.7% | 67.38 |
| Manuf | 17,051 | 173.75 | 0.65 | 1.25 | 0.35 | 0.90 | 8.7% | 51.90 |
| Money | 25,815 | 263.22 | 1.47 | 2.75 | 0.54 | 2.21 | 21.2% | 83.97 |
| NoDur | 7,510 | 73.82 | 0.38 | 0.43 | 0.14 | 0.28 | 2.7% | 38.53 |
| Other | 26,581 | 265.30 | 1.49 | 2.19 | 0.60 | 1.59 | 15.3% | 60.07 |
| Shops | 17,607 | 175.59 | 1.48 | 1.72 | 0.36 | 1.36 | 13.1% | 77.52 |
| Telcm | 5,624 | 56.27 | -0.05 | 0.19 | 0.11 | 0.08 | 0.8% | 14.21 |
| Utils | 6,441 | 67.13 | 0.08 | 0.22 | 0.12 | 0.10 | 1.0% | 15.03 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 57,238 | 573.05 | 1.82 | 2.59 | 1.06 | 1.52 | 14.6% | 26.57 |
| low vol | 58,337 | 579.75 | 4.55 | 5.97 | 1.32 | 4.64 | 44.6% | 80.05 |
| mid vol | 55,592 | 550.31 | 4.42 | 5.25 | 1.14 | 4.11 | 39.5% | 74.75 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 3.88% | -0.00% |
| 0.5% | 0.9 | 3.65% | 0.23% |
| 1.0% | 1.8 | 3.41% | 0.47% |
| 2.0% | 3.5 | 2.90% | 0.98% |
| 5.0% | 8.8 | 1.01% | 2.87% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
