# sector_balanced_legacy

_equity strategy | family: equity-neutralized | status: reference | derived from `sector_balanced`_

## What this strategy does

Conviction Tilt, also balanced across 12 industry groups; conservative 1.5x borrowing limit -- ORIGINAL leverage cap (new positions only, open book ignored)

**Why it should work.** Same drift, with sector and size risk diversified away so only the earnings signal is left.

**Design notes** (from the strategy's source file).

Sector Balanced -- Conviction Tilt plus an equal budget for each industry group.

A diagnostic found Conviction Tilt's book putting up to 29% of gross weight in one FF12 sector in some quarters (8.3% if spread evenly): an unpriced bet unrelated to earnings drift. Neutralizing each (side, size, sector) cell to an equal budget removes it. Best Sharpe / shallowest drawdown of the hand-built strategies at its conservative 1.5x leverage cap.

| parameter | value |
|---|---|
| base_unit_fraction | 0.0015 |
| max_gross_leverage | 1.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| exposure_cap | entry_cohort |
| beta_overlay | 0.0 |

## Performance

| metric | sector_balanced_legacy | market (CRSP VW) |
|---|---|---|
| Annualized return | 4.06% | 9.94% |
| Annualized volatility | 3.02% | 19.35% |
| Sharpe (NAV-pct) | 1.33 | 0.59 |
| Sortino | 1.88 |  |
| Max drawdown | -10.8% | -55.5% |
| Calmar | 0.37 |  |
| Final NAV ($10M start) | $21,709,852 |  |
| Worst day | -3.95% |  |
| % positive days | 52.3% |  |
| Sharpe 1996-2001 | 2.19 |  |
| Sharpe 2002-2007 | 1.34 |  |
| Sharpe 2008-2013 | 0.66 |  |

Correlation of daily returns with the market: **0.03**.

- best Sharpe among equity: `calm_rank_tilt` (1.47); this one: 1.33
- best return among equity: `market_boost_max` (0.116); this one: 0.0406
- best Calmar among equity: `calm_rank_tilt` (0.442); this one: 0.375

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
| P&L ($M, before costs) | 19.96 | -5.40 |
| P&L after costs ($M) | 18.59 | -6.88 |
| of which common (average-event) component ($M) | 10.04 | -10.32 |
| of which excess / PEAD component ($M) | 9.63 | 5.12 |
| excess minus costs ($M) | 8.26 | 3.66 |
| gross return per $ traded (bp) | 304.72 | -80.72 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 1.32 | -0.31 |

Daily P&L correlation between the two legs: **-0.79**.

- **The short leg loses money in absolute terms** (-5.4M before costs) while the long leg earns +20.0M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 10.0M / 10.3M. Stripping that common component, the surprise signal itself earned +9.6M on the long side and +5.1M on the short side.
- The two legs' daily P&L are -0.79 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.32 long / -0.31 short vs 1.33 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,029 | 56.48 | 1.39 | 0.44 | 0.12 | 0.32 | 2.7% | 56.28 |
| long | 8 | 16,588 | 151.86 | 4.08 | 1.88 | 0.32 | 1.56 | 13.1% | 102.46 |
| long | 9 | 17,747 | 202.60 | 3.85 | 1.43 | 0.42 | 1.01 | 8.4% | 49.71 |
| long | 10 | 17,174 | 244.17 | 9.27 | 5.89 | 0.52 | 5.37 | 45.1% | 220.00 |
| short | 1 | 17,169 | 247.53 | -3.33 | 0.92 | 0.57 | 0.34 | 2.9% | 13.84 |
| short | 2 | 17,281 | 208.23 | -1.82 | 2.07 | 0.45 | 1.62 | 13.6% | 77.86 |
| short | 3 | 15,970 | 152.89 | -1.24 | 1.52 | 0.33 | 1.19 | 10.0% | 78.08 |
| short | 4 | 9,171 | 60.77 | -0.48 | 0.61 | 0.12 | 0.49 | 4.1% | 81.16 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,213 | 164.15 | 4.46 | 5.49 | 0.66 | 4.83 | 40.5% | 294.31 |
| 2 | 22,885 | 262.98 | 2.30 | 3.09 | 0.76 | 2.33 | 19.6% | 88.77 |
| 3 | 25,501 | 294.17 | 2.65 | 3.17 | 0.63 | 2.54 | 21.3% | 86.32 |
| 4 | 27,583 | 301.86 | 1.36 | 1.77 | 0.47 | 1.29 | 10.9% | 42.89 |
| 5 | 28,947 | 301.38 | 0.94 | 1.24 | 0.33 | 0.90 | 7.6% | 29.99 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,650 | 130.20 | 3.85 | 4.72 | 0.65 | 4.07 | 34.2% | 312.87 |
| 2 | 23,084 | 287.52 | 2.88 | 4.04 | 0.86 | 3.18 | 26.6% | 110.46 |
| 3 | 25,851 | 308.05 | 2.25 | 2.93 | 0.62 | 2.31 | 19.4% | 75.04 |
| 4 | 27,665 | 300.81 | 1.87 | 2.09 | 0.42 | 1.67 | 14.0% | 55.35 |
| 5 | 28,874 | 297.88 | 0.86 | 0.97 | 0.30 | 0.68 | 5.7% | 22.67 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,855 | 481.23 | 0.05 | 2.76 | 0.88 | 1.88 | 15.8% | 39.09 |
| 41-60d | 25,652 | 300.47 | 1.88 | 2.15 | 0.66 | 1.49 | 12.5% | 49.67 |
| 61-90d | 35,324 | 395.08 | 2.86 | 3.49 | 0.90 | 2.59 | 21.7% | 65.61 |
| >90d | 12,298 | 147.76 | 6.92 | 6.35 | 0.41 | 5.94 | 49.8% | 401.85 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 328.62 | 4.94 | 5.65 | 0.69 | 4.96 | 41.6% | 150.79 |
| 2002-07 | 39,956 | 438.83 | 4.14 | 5.11 | 0.92 | 4.19 | 35.2% | 95.51 |
| 2008-13 | 45,278 | 557.10 | 2.64 | 3.99 | 1.24 | 2.76 | 23.1% | 49.48 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,094 | 124.11 | 1.01 | 1.30 | 0.27 | 1.03 | 8.6% | 82.85 |
| Chems | 2,590 | 92.50 | 0.28 | 0.47 | 0.18 | 0.29 | 2.4% | 31.24 |
| Durbl | 2,843 | 101.51 | 1.37 | 1.59 | 0.21 | 1.38 | 11.6% | 136.12 |
| Enrgy | 6,194 | 109.07 | 1.62 | 1.87 | 0.22 | 1.64 | 13.8% | 150.72 |
| Hlth | 11,696 | 121.82 | 1.26 | 1.53 | 0.27 | 1.26 | 10.6% | 103.83 |
| Manuf | 12,034 | 116.86 | 0.99 | 1.25 | 0.26 | 0.99 | 8.3% | 84.50 |
| Money | 17,583 | 114.16 | 1.27 | 1.61 | 0.27 | 1.33 | 11.2% | 116.81 |
| NoDur | 5,081 | 108.74 | 0.87 | 1.08 | 0.23 | 0.85 | 7.2% | 78.44 |
| Other | 18,377 | 121.67 | 1.17 | 1.50 | 0.28 | 1.22 | 10.2% | 100.16 |
| Shops | 11,984 | 120.09 | 1.49 | 1.72 | 0.26 | 1.46 | 12.3% | 121.65 |
| Telcm | 3,981 | 108.87 | 0.26 | 0.48 | 0.22 | 0.27 | 2.2% | 24.32 |
| Utils | 4,672 | 85.15 | 0.11 | 0.35 | 0.17 | 0.18 | 1.5% | 20.85 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,523 | 411.62 | 2.13 | 3.38 | 0.80 | 2.59 | 21.7% | 62.85 |
| low vol | 40,091 | 462.76 | 4.81 | 5.97 | 1.10 | 4.87 | 40.9% | 105.24 |
| mid vol | 37,903 | 425.63 | 4.61 | 5.29 | 0.90 | 4.38 | 36.8% | 103.00 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 4.06% | -0.00% |
| 0.5% | 0.7 | 3.89% | 0.17% |
| 1.0% | 1.4 | 3.71% | 0.35% |
| 2.0% | 2.8 | 3.34% | 0.72% |
| 5.0% | 6.9 | 2.04% | 2.02% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
