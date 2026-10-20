# extreme_picks

_equity strategy | family: equity-baseline | status: validated_

## What this strategy does

Buy the top 10% of earnings surprises, short the bottom 10%, equal bets

**Why it should work.** Analysts and investors under-react to earnings news, so stocks with the most positive surprise keep drifting up and the most negative keep drifting down for weeks.

**Design notes** (from the strategy's source file).

Extreme Picks -- long the best earnings surprises, short the worst.

The textbook PEAD trade: long the best earnings-surprise decile (D10), short the worst (D1), every event equal-weighted, each held for the number of days its (decile x size x book-to-market) cell's alpha takes to decay (per-cell holding windows, not a fixed horizon).

| parameter | value |
|---|---|
| base_unit_fraction | 0.003 |
| max_gross_leverage | 1.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | proportional |
| exposure_cap | open_book |
| beta_overlay | 0.0 |

## Performance

| metric | extreme_picks | market (CRSP VW) |
|---|---|---|
| Annualized return | 3.31% | 9.94% |
| Annualized volatility | 5.24% | 19.35% |
| Sharpe (NAV-pct) | 0.65 | 0.59 |
| Sortino | 0.89 |  |
| Max drawdown | -25.2% | -55.5% |
| Calmar | 0.13 |  |
| Final NAV ($10M start) | $18,873,209 |  |
| Worst day | -4.19% |  |
| % positive days | 50.5% |  |
| Sharpe 1996-2001 | 0.86 |  |
| Sharpe 2002-2007 | 1.62 |  |
| Sharpe 2008-2013 | -0.11 |  |

Correlation of daily returns with the market: **-0.03**.

- best Sharpe among equity: `calm_rank_tilt` (1.81); this one: 0.648
- best return among equity: `market_boost_max` (0.15); this one: 0.0331
- best Calmar among equity: `calm_rank_tilt` (0.614); this one: 0.132

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
| P&L ($M, before costs) | 18.26 | -6.74 |
| P&L after costs ($M) | 16.94 | -8.07 |
| of which common (average-event) component ($M) | 10.68 | -10.83 |
| of which excess / PEAD component ($M) | 7.58 | 4.08 |
| excess minus costs ($M) | 6.26 | 2.76 |
| gross return per $ traded (bp) | 259.72 | -110.40 |
| positions in the money (hit rate) | 0.57 | 0.49 |
| stand-alone Sharpe (leg only) | 1.10 | -0.37 |

Daily P&L correlation between the two legs: **-0.59**.

- **The short leg loses money in absolute terms** (-6.7M before costs) while the long leg earns +18.3M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 10.7M / 10.8M. Stripping that common component, the surprise signal itself earned +7.6M on the long side and +4.1M on the short side.
- The two legs' daily P&L are -0.59 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.10 long / -0.37 short vs 0.65 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 10 | 20,136 | 703.05 | 16.94 | 7.58 | 1.32 | 6.26 | 69.4% | 89.02 |
| short | 1 | 18,299 | 610.77 | -8.07 | 4.08 | 1.33 | 2.76 | 30.6% | 45.17 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 5,287 | 112.82 | -1.62 | -0.46 | 0.42 | -0.88 | -9.8% | -78.22 |
| 2 | 7,872 | 269.28 | 2.27 | 3.68 | 0.74 | 2.94 | 32.6% | 109.10 |
| 3 | 8,605 | 313.94 | 4.23 | 4.72 | 0.65 | 4.08 | 45.2% | 129.86 |
| 4 | 8,744 | 324.40 | 2.06 | 2.15 | 0.50 | 1.64 | 18.2% | 50.63 |
| 5 | 7,927 | 293.37 | 1.94 | 1.57 | 0.33 | 1.24 | 13.8% | 42.36 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,793 | 78.48 | 1.37 | 2.05 | 0.39 | 1.66 | 18.4% | 210.88 |
| 2 | 7,905 | 279.44 | 0.15 | 1.84 | 0.84 | 1.00 | 11.1% | 35.78 |
| 3 | 8,745 | 325.65 | 4.06 | 4.94 | 0.65 | 4.29 | 47.5% | 131.65 |
| 4 | 8,837 | 328.31 | 1.26 | 1.45 | 0.46 | 0.99 | 10.9% | 30.02 |
| 5 | 8,153 | 301.85 | 2.05 | 1.41 | 0.30 | 1.11 | 12.3% | 36.63 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 7,482 | 265.13 | 0.72 | 4.39 | 0.52 | 3.87 | 42.9% | 145.92 |
| 41-60d | 11,345 | 365.13 | -0.39 | 1.07 | 0.81 | 0.26 | 2.9% | 7.14 |
| 61-90d | 17,258 | 615.36 | 5.01 | 3.42 | 1.12 | 2.29 | 25.5% | 37.30 |
| >90d | 2,350 | 68.20 | 3.54 | 2.78 | 0.18 | 2.59 | 28.7% | 380.10 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 10,157 | 319.91 | 3.42 | 3.81 | 0.61 | 3.20 | 35.5% | 99.96 |
| 2002-07 | 13,612 | 470.36 | 6.16 | 6.34 | 0.91 | 5.43 | 60.2% | 115.41 |
| 2008-13 | 14,666 | 523.55 | -0.70 | 1.51 | 1.12 | 0.39 | 4.3% | 7.47 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 7,541 | 254.13 | 1.04 | 0.43 | 0.50 | -0.07 | -0.8% | -2.84 |
| Chems | 776 | 27.31 | -0.08 | 0.01 | 0.05 | -0.04 | -0.5% | -16.63 |
| Durbl | 990 | 32.83 | -0.03 | 0.18 | 0.07 | 0.11 | 1.3% | 34.39 |
| Enrgy | 1,529 | 53.47 | 0.97 | 1.17 | 0.11 | 1.06 | 11.7% | 197.76 |
| Hlth | 3,443 | 113.09 | 0.82 | 0.63 | 0.24 | 0.39 | 4.3% | 34.48 |
| Manuf | 4,089 | 140.55 | 0.38 | 1.01 | 0.28 | 0.73 | 8.1% | 52.18 |
| Money | 5,981 | 209.53 | 1.21 | 2.58 | 0.42 | 2.16 | 24.0% | 103.19 |
| NoDur | 1,707 | 58.62 | 0.47 | 0.55 | 0.11 | 0.43 | 4.8% | 73.99 |
| Other | 5,674 | 192.78 | 2.12 | 2.57 | 0.42 | 2.15 | 23.9% | 111.59 |
| Shops | 3,935 | 133.71 | 1.53 | 1.81 | 0.26 | 1.54 | 17.1% | 115.32 |
| Telcm | 1,293 | 44.51 | 0.25 | 0.44 | 0.09 | 0.35 | 3.9% | 79.13 |
| Utils | 1,477 | 53.28 | 0.18 | 0.29 | 0.09 | 0.20 | 2.2% | 37.50 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 12,942 | 445.05 | -0.80 | 0.44 | 0.80 | -0.36 | -4.0% | -8.14 |
| low vol | 12,792 | 432.76 | 5.51 | 6.30 | 0.96 | 5.34 | 59.2% | 123.41 |
| mid vol | 12,052 | 414.88 | 3.88 | 4.67 | 0.83 | 3.83 | 42.5% | 92.44 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 3.31% | -0.00% |
| 0.5% | 0.6 | 3.13% | 0.18% |
| 1.0% | 1.3 | 2.94% | 0.37% |
| 2.0% | 2.5 | 2.55% | 0.77% |
| 5.0% | 6.4 | 1.15% | 2.16% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
