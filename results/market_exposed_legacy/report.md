# market_exposed_legacy

_equity strategy | family: equity-directional | status: reference | derived from `market_exposed`_

## What this strategy does

Balanced Leverage signal, longs and shorts not forced to match, market moves left in -- ORIGINAL leverage cap (new positions only, open book ignored)

**Why it should work.** Let net exposure float with the signal; empirically this smuggles in uncontrolled market beta.

**Design notes** (from the strategy's source file).

Market Exposed -- like Balanced Leverage, but longs and shorts need not match, so it rides the market (cautionary result).

Balanced Leverage's signal, but the long and short legs are no longer forced to equal dollar totals, and returns are RAW so real market beta is carried. Net dollar exposure is only ~10% of gross (as in every other book) yet the book is 0.52-correlated with the market, because raw returns carry each stock's own beta and equal dollars long and short are not equal BETA: high-SUE and low-SUE firms differ in beta. Kept as a documented counter-example to the explicit beta overlay.

| parameter | value |
|---|---|
| base_unit_fraction | 0.003 |
| max_gross_leverage | 2.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | raw |
| cap_mode | priority |
| exposure_cap | entry_cohort |
| beta_overlay | 0.0 |

## Performance

| metric | market_exposed_legacy | market (CRSP VW) |
|---|---|---|
| Annualized return | 7.30% | 9.94% |
| Annualized volatility | 6.56% | 19.35% |
| Sharpe (NAV-pct) | 1.11 | 0.59 |
| Sortino | 1.61 |  |
| Max drawdown | -15.5% | -55.5% |
| Calmar | 0.47 |  |
| Final NAV ($10M start) | $39,420,102 |  |
| Worst day | -3.54% |  |
| % positive days | 51.7% |  |
| Sharpe 1996-2001 | 1.79 |  |
| Sharpe 2002-2007 | 1.48 |  |
| Sharpe 2008-2013 | 0.32 |  |

Correlation of daily returns with the market: **0.52**.

- best Sharpe among equity: `calm_rank_tilt` (1.47); this one: 1.11
- best return among equity: `market_boost_max` (0.116); this one: 0.073
- best Calmar among equity: `calm_rank_tilt` (0.442); this one: 0.47

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
| P&L ($M, before costs) | 83.80 | -46.97 |
| P&L after costs ($M) | 80.33 | -50.91 |
| of which common (average-event) component ($M) | 28.93 | -28.01 |
| of which excess / PEAD component ($M) | 14.01 | 16.42 |
| excess minus costs ($M) | 10.56 | 12.49 |
| gross return per $ traded (bp) | 453.74 | -252.30 |
| positions in the money (hit rate) | 0.63 | 0.42 |
| stand-alone Sharpe (leg only) | 0.80 | -0.48 |

Daily P&L correlation between the two legs: **-0.97**.

- **The short leg loses money in absolute terms** (-47.0M before costs) while the long leg earns +83.8M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 28.9M / 28.0M. Stripping that common component, the surprise signal itself earned +14.0M on the long side and +16.4M on the short side.
- The two legs' daily P&L are -0.97 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 0.80 long / -0.48 short vs 1.11 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,029 | 151.48 | 6.63 | 0.31 | 0.29 | 0.02 | 0.1% | 1.52 |
| long | 8 | 16,588 | 419.95 | 18.91 | 2.98 | 0.80 | 2.19 | 9.5% | 52.10 |
| long | 9 | 17,747 | 579.02 | 19.40 | 1.30 | 1.07 | 0.23 | 1.0% | 4.06 |
| long | 10 | 17,174 | 696.43 | 35.39 | 9.41 | 1.32 | 8.10 | 35.1% | 116.27 |
| short | 1 | 17,169 | 687.39 | -20.20 | 5.06 | 1.50 | 3.56 | 15.4% | 51.76 |
| short | 2 | 17,281 | 581.80 | -15.87 | 5.84 | 1.21 | 4.62 | 20.1% | 79.49 |
| short | 3 | 15,970 | 427.74 | -10.65 | 3.74 | 0.90 | 2.84 | 12.3% | 66.46 |
| short | 4 | 9,171 | 164.64 | -4.20 | 1.78 | 0.33 | 1.45 | 6.3% | 87.95 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,213 | 331.20 | 4.24 | 6.87 | 1.26 | 5.61 | 24.3% | 169.38 |
| 2 | 22,885 | 698.98 | 5.38 | 9.11 | 1.95 | 7.15 | 31.0% | 102.34 |
| 3 | 25,501 | 855.45 | 6.19 | 7.67 | 1.78 | 5.89 | 25.6% | 68.91 |
| 4 | 27,583 | 908.68 | 5.87 | 4.17 | 1.42 | 2.75 | 11.9% | 30.29 |
| 5 | 28,947 | 914.14 | 7.73 | 2.61 | 1.00 | 1.61 | 7.0% | 17.57 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,650 | 237.22 | 5.30 | 6.82 | 1.19 | 5.63 | 24.4% | 237.28 |
| 2 | 23,084 | 737.56 | 4.92 | 8.94 | 2.21 | 6.73 | 29.2% | 91.20 |
| 3 | 25,851 | 913.89 | 5.24 | 8.86 | 1.83 | 7.03 | 30.5% | 76.92 |
| 4 | 27,665 | 916.54 | 5.74 | 3.53 | 1.28 | 2.25 | 9.8% | 24.55 |
| 5 | 28,874 | 902.97 | 8.23 | 2.27 | 0.90 | 1.37 | 5.9% | 15.18 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,855 | 1,393.00 | -4.21 | 6.39 | 2.48 | 3.91 | 17.0% | 28.09 |
| 41-60d | 25,652 | 849.98 | 5.63 | 4.06 | 1.72 | 2.34 | 10.1% | 27.50 |
| 61-90d | 35,324 | 1,098.83 | 6.54 | 8.72 | 2.31 | 6.42 | 27.8% | 58.40 |
| >90d | 12,298 | 366.64 | 21.45 | 11.26 | 0.91 | 10.35 | 44.9% | 282.27 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 747.21 | 10.90 | 10.52 | 1.46 | 9.06 | 39.3% | 121.23 |
| 2002-07 | 39,956 | 1,240.62 | 12.89 | 13.31 | 2.43 | 10.88 | 47.2% | 87.72 |
| 2008-13 | 45,278 | 1,720.62 | 5.63 | 6.60 | 3.52 | 3.08 | 13.3% | 17.88 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,094 | 361.52 | 6.68 | 2.59 | 0.76 | 1.83 | 7.9% | 50.55 |
| Chems | 2,590 | 247.25 | 1.86 | 2.09 | 0.43 | 1.66 | 7.2% | 67.09 |
| Durbl | 2,843 | 271.20 | 0.82 | 1.76 | 0.49 | 1.27 | 5.5% | 46.95 |
| Enrgy | 6,194 | 306.46 | 2.68 | 5.00 | 0.58 | 4.42 | 19.2% | 144.27 |
| Hlth | 11,696 | 351.27 | 4.78 | 2.81 | 0.74 | 2.07 | 9.0% | 58.92 |
| Manuf | 12,034 | 328.49 | 2.00 | 2.19 | 0.68 | 1.51 | 6.6% | 45.98 |
| Money | 17,583 | 324.19 | 2.90 | 3.99 | 0.73 | 3.26 | 14.2% | 100.66 |
| NoDur | 5,081 | 297.45 | 3.35 | 2.27 | 0.56 | 1.71 | 7.4% | 57.47 |
| Other | 18,377 | 351.79 | 2.42 | 2.88 | 0.78 | 2.10 | 9.1% | 59.76 |
| Shops | 11,984 | 342.63 | 3.79 | 3.25 | 0.70 | 2.55 | 11.1% | 74.40 |
| Telcm | 3,981 | 294.61 | -1.67 | 0.91 | 0.54 | 0.37 | 1.6% | 12.55 |
| Utils | 4,672 | 231.59 | -0.19 | 0.69 | 0.42 | 0.26 | 1.1% | 11.32 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,523 | 1,190.47 | 5.04 | 7.46 | 2.16 | 5.30 | 23.0% | 44.53 |
| low vol | 40,091 | 1,262.78 | 13.29 | 12.94 | 2.76 | 10.18 | 44.1% | 80.57 |
| mid vol | 37,903 | 1,199.44 | 10.75 | 9.88 | 2.38 | 7.50 | 32.6% | 62.54 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 7.30% | 0.00% |
| 0.5% | 1.9 | 7.02% | 0.27% |
| 1.0% | 3.8 | 6.74% | 0.56% |
| 2.0% | 7.6 | 6.12% | 1.18% |
| 5.0% | 19.1 | 3.72% | 3.58% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
