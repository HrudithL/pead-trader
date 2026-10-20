# calm_rank_levered

_equity strategy | family: equity-volatility | status: research | derived from `calm_rank_tilt`_

## What this strategy does

Calm Rank Tilt with its lower risk spent on larger bets (about 1.7x) and a 4x borrowing limit

**Why it should work.** PEAD alpha per dollar is highest in calm stocks; rank-weight toward them without depending on vol levels.

**Design notes** (from the strategy's source file).

Strategy 10 -- Strategy 6 tilted toward calm stocks by volatility RANK (research).

Strategy 9b sized each event by 1/idiosyncratic-vol (Sharpe 1.30 -> 1.47). The attribution work showed that PEAD alpha per dollar is concentrated in low-volatility names and almost absent in the wildest tercile, and that the 1/vol weight, being linear in the vol LEVEL, only mildly demotes the high-vol tail. Weighting by (1.02 - vol percentile)^2 -- where the percentile is measured against the previous quarter's cross-section within the stock's size quintile -- demotes it hard while staying smooth:

Sharpe 1.30 (S6) -> 1.47 (9b) -> 1.81; max drawdown -16.6% -> -11.6%; every era improves split-sample check: exponent picked on 1996-2004 data alone (best k ~ 3) lifts 2005-2013 Sharpe 0.93 -> 1.10, the same as the full-sample best, so the gain is not an artifact of one period.

Lower volatility buys room: `calm_rank_levered` spends it on 1.7x the base unit and a 4x gross cap for ~9% a year at ~5% vol (a market-neutral book clearing the S&P 500's return in this sample). More sizing than that stops paying: the 5%-of-ADV liquidity cap starts to bind and 2008-13 Sharpe decays.

STATUS research: the exponent and sizing were chosen after seeing 9b's full-sample results (the split-sample check above is the only out-of-sample evidence), and the levered variant leans on small-cap capacity.

| parameter | value |
|---|---|
| base_unit_fraction | 0.005 |
| max_gross_leverage | 4.0 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| beta_overlay | 0.0 |

## Performance

| metric | calm_rank_levered | market (CRSP VW) |
|---|---|---|
| Annualized return | 9.23% | 9.94% |
| Annualized volatility | 5.17% | 19.35% |
| Sharpe (NAV-pct) | 1.73 | 0.59 |
| Sortino | 2.64 |  |
| Max drawdown | -15.9% | -55.5% |
| Calmar | 0.58 |  |
| Final NAV ($10M start) | $55,789,745 |  |
| Worst day | -3.60% |  |
| % positive days | 52.5% |  |
| Sharpe 1996-2001 | 2.86 |  |
| Sharpe 2002-2007 | 1.96 |  |
| Sharpe 2008-2013 | 0.50 |  |

Correlation of daily returns with the market: **-0.03**.

- best Sharpe among equity: `calm_rank_tilt` (1.81); this one: 1.73
- best return among equity: `market_boost_max` (0.15); this one: 0.0923
- best Calmar among equity: `calm_rank_tilt` (0.614); this one: 0.579

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
| P&L ($M, before costs) | 72.46 | -13.81 |
| P&L after costs ($M) | 66.24 | -20.45 |
| of which common (average-event) component ($M) | 55.30 | -54.77 |
| of which excess / PEAD component ($M) | 17.16 | 40.96 |
| excess minus costs ($M) | 10.94 | 34.32 |
| gross return per $ traded (bp) | 208.91 | -39.14 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 1.45 | -0.16 |

Daily P&L correlation between the two legs: **-0.77**.

- **The short leg loses money in absolute terms** (-13.8M before costs) while the long leg earns +72.5M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 55.3M / 54.8M. Stripping that common component, the surprise signal itself earned +17.2M on the long side and +41.0M on the short side.
- The two legs' daily P&L are -0.77 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.45 long / -0.16 short vs 1.73 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,029 | 326.55 | 3.79 | -1.68 | 0.59 | -2.27 | -5.0% | -69.55 |
| long | 8 | 16,588 | 843.87 | 14.71 | 2.34 | 1.52 | 0.81 | 1.8% | 9.64 |
| long | 9 | 17,747 | 1,080.59 | 17.10 | 3.02 | 1.91 | 1.11 | 2.5% | 10.29 |
| long | 10 | 17,174 | 1,217.60 | 30.64 | 13.48 | 2.20 | 11.29 | 24.9% | 92.70 |
| short | 1 | 17,169 | 1,171.25 | -4.34 | 14.08 | 2.29 | 11.79 | 26.0% | 100.64 |
| short | 2 | 17,281 | 1,084.11 | -6.66 | 13.88 | 2.02 | 11.85 | 26.2% | 109.34 |
| short | 3 | 15,970 | 888.83 | -6.83 | 8.96 | 1.64 | 7.32 | 16.2% | 82.30 |
| short | 4 | 9,171 | 384.55 | -2.61 | 4.04 | 0.68 | 3.36 | 7.4% | 87.37 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,213 | 343.64 | 5.44 | 7.65 | 1.29 | 6.36 | 14.0% | 184.96 |
| 2 | 22,885 | 1,016.63 | 8.45 | 11.48 | 2.83 | 8.65 | 19.1% | 85.09 |
| 3 | 25,501 | 1,544.72 | 12.48 | 14.91 | 3.26 | 11.65 | 25.7% | 75.44 |
| 4 | 27,583 | 1,953.20 | 11.43 | 14.10 | 3.12 | 10.98 | 24.3% | 56.24 |
| 5 | 28,947 | 2,139.15 | 7.99 | 9.98 | 2.37 | 7.62 | 16.8% | 35.60 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,650 | 251.16 | 5.02 | 6.70 | 1.26 | 5.45 | 12.0% | 216.90 |
| 2 | 23,084 | 1,138.52 | 8.26 | 13.18 | 3.42 | 9.77 | 21.6% | 85.78 |
| 3 | 25,851 | 1,809.17 | 14.81 | 18.74 | 3.62 | 15.12 | 33.4% | 83.59 |
| 4 | 27,665 | 1,938.19 | 9.63 | 10.53 | 2.71 | 7.82 | 17.3% | 40.34 |
| 5 | 28,874 | 1,860.06 | 8.04 | 8.93 | 1.86 | 7.07 | 15.6% | 37.98 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,855 | 2,914.53 | 2.37 | 17.60 | 4.88 | 12.71 | 28.1% | 43.62 |
| 41-60d | 25,652 | 1,600.35 | 11.07 | 9.58 | 2.99 | 6.59 | 14.6% | 41.18 |
| 61-90d | 35,324 | 1,883.42 | 13.71 | 15.21 | 3.65 | 11.56 | 25.5% | 61.38 |
| >90d | 12,298 | 599.04 | 18.64 | 15.73 | 1.34 | 14.39 | 31.8% | 240.28 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 1,122.39 | 17.52 | 19.70 | 2.06 | 17.64 | 39.0% | 157.16 |
| 2002-07 | 39,956 | 2,449.43 | 20.47 | 24.04 | 4.43 | 19.61 | 43.3% | 80.07 |
| 2008-13 | 45,278 | 3,425.53 | 7.80 | 14.37 | 6.37 | 8.01 | 17.7% | 23.37 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,094 | 729.85 | 5.33 | 6.37 | 1.46 | 4.91 | 10.9% | 67.28 |
| Chems | 2,590 | 441.60 | 3.43 | 4.07 | 0.69 | 3.38 | 7.5% | 76.53 |
| Durbl | 2,843 | 460.22 | 2.24 | 2.98 | 0.74 | 2.23 | 4.9% | 48.49 |
| Enrgy | 6,194 | 534.68 | 5.92 | 7.08 | 0.90 | 6.18 | 13.7% | 115.58 |
| Hlth | 11,696 | 673.29 | 3.17 | 4.07 | 1.28 | 2.79 | 6.2% | 41.47 |
| Manuf | 12,034 | 655.21 | 3.13 | 4.54 | 1.24 | 3.30 | 7.3% | 50.41 |
| Money | 17,583 | 657.72 | 5.56 | 7.08 | 1.36 | 5.72 | 12.6% | 86.93 |
| NoDur | 5,081 | 542.02 | 2.69 | 3.39 | 0.92 | 2.47 | 5.4% | 45.49 |
| Other | 18,377 | 706.61 | 5.30 | 6.85 | 1.47 | 5.38 | 11.9% | 76.12 |
| Shops | 11,984 | 672.08 | 4.85 | 5.88 | 1.28 | 4.61 | 10.2% | 68.53 |
| Telcm | 3,981 | 466.01 | 2.81 | 3.57 | 0.75 | 2.83 | 6.2% | 60.67 |
| Utils | 4,672 | 458.03 | 1.34 | 2.24 | 0.78 | 1.47 | 3.2% | 32.00 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,523 | 625.15 | 5.22 | 6.78 | 1.11 | 5.66 | 12.5% | 90.58 |
| low vol | 40,091 | 3,855.29 | 24.00 | 31.48 | 7.20 | 24.28 | 53.6% | 62.98 |
| mid vol | 37,903 | 2,416.18 | 15.81 | 19.07 | 4.36 | 14.71 | 32.5% | 60.90 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 9.23% | 0.00% |
| 0.5% | 3.4 | 8.87% | 0.36% |
| 1.0% | 6.9 | 8.49% | 0.74% |
| 2.0% | 13.8 | 7.65% | 1.58% |
| 5.0% | 34.4 | 3.97% | 5.25% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
