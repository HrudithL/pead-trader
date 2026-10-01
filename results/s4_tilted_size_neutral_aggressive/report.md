# s4_tilted_size_neutral_aggressive

_equity strategy | family: equity-aggressive | status: research | derived from `s4_tilted_size_neutral`_

## What this strategy does

s4_tilted_size_neutral re-sized for return: base unit 0.007, 6x leverage cap

**Why it should work.** Concentrate capital where the surprise is largest; neutralize size so it is not a hidden bet.

**Design notes** (from the strategy's source file).

Aggressive-sizing variants of the hand-built equity strategies.

Same signal, trim, tilt and neutralization as each base strategy; only the two dials that set how big and how levered the book is change (base_unit_fraction, gross-leverage cap = 6x). The multiplier for each was picked by sweeping 1.5x..12x the original base unit and taking the highest return subject to Sharpe >= 0.6 and max drawdown >= -40% (a worse drawdown is a fund-ending event, not a risk preference). The untouched 5%-of-ADV liquidity cap remains the real capacity constraint, and bites roughly four times as often at this sizing.

These are generated from the base strategy classes, so a change to a base strategy's weights flows through automatically.

| parameter | value |
|---|---|
| base_unit_fraction | 0.007 |
| max_gross_leverage | 6.0 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| beta_overlay | 0.0 |

## Performance

| metric | s4_tilted_size_neutral_aggressive | market (CRSP VW) |
|---|---|---|
| Annualized return | 10.70% | 9.94% |
| Annualized volatility | 12.25% | 19.35% |
| Sharpe (NAV-pct) | 0.89 | 0.59 |
| Sortino | 1.27 |  |
| Max drawdown | -40.0% | -55.5% |
| Calmar | 0.27 |  |
| Final NAV ($10M start) | $72,371,948 |  |
| Worst day | -6.54% |  |
| % positive days | 51.9% |  |
| Sharpe 1996-2001 | 1.30 |  |
| Sharpe 2002-2007 | 1.54 |  |
| Sharpe 2008-2013 | 0.07 |  |

Correlation of daily returns with the market: **0.03**.

- best Sharpe among equity: `s10_vol_rank_tilt` (1.81); this one: 0.891
- best return among equity: `s6_beta_overlay_aggressive` (0.15); this one: 0.107
- best Calmar among equity: `s10_vol_rank_tilt` (0.614); this one: 0.267

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
| P&L ($M, before costs) | 124.48 | -38.59 |
| P&L after costs ($M) | 113.18 | -50.81 |
| of which common (average-event) component ($M) | 105.32 | -105.36 |
| of which excess / PEAD component ($M) | 19.16 | 66.77 |
| excess minus costs ($M) | 7.87 | 54.55 |
| gross return per $ traded (bp) | 189.32 | -56.80 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 0.95 | -0.15 |

Daily P&L correlation between the two legs: **-0.72**.

- **The short leg loses money in absolute terms** (-38.6M before costs) while the long leg earns +124.5M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 105.3M / 105.4M. Stripping that common component, the surprise signal itself earned +19.2M on the long side and +66.8M on the short side.
- The two legs' daily P&L are -0.72 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 0.95 long / -0.15 short vs 0.89 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,029 | 531.28 | 8.56 | 0.29 | 0.97 | -0.68 | -1.1% | -12.76 |
| long | 8 | 16,588 | 1,498.23 | 26.40 | 3.80 | 2.65 | 1.15 | 1.8% | 7.65 |
| long | 9 | 17,747 | 2,072.79 | 26.15 | -1.23 | 3.50 | -4.73 | -7.6% | -22.83 |
| long | 10 | 17,174 | 2,472.81 | 52.07 | 16.30 | 4.17 | 12.13 | 19.4% | 49.05 |
| short | 1 | 17,169 | 2,422.99 | -18.52 | 21.39 | 4.38 | 17.01 | 27.2% | 70.19 |
| short | 2 | 17,281 | 2,130.37 | -14.14 | 24.11 | 3.77 | 20.34 | 32.6% | 95.46 |
| short | 3 | 15,970 | 1,602.41 | -13.84 | 14.65 | 2.92 | 11.73 | 18.8% | 73.20 |
| short | 4 | 9,171 | 638.34 | -4.31 | 6.63 | 1.15 | 5.48 | 8.8% | 85.79 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,213 | 602.96 | 4.85 | 8.74 | 2.02 | 6.72 | 10.8% | 111.47 |
| 2 | 22,885 | 2,160.87 | 17.58 | 23.79 | 5.48 | 18.31 | 29.3% | 84.73 |
| 3 | 25,501 | 3,273.89 | 21.32 | 26.08 | 6.45 | 19.62 | 31.4% | 59.94 |
| 4 | 27,583 | 3,629.42 | 11.28 | 16.02 | 5.50 | 10.52 | 16.9% | 28.98 |
| 5 | 28,947 | 3,702.08 | 7.34 | 11.31 | 4.06 | 7.25 | 11.6% | 19.57 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,650 | 301.82 | 5.97 | 8.05 | 1.51 | 6.54 | 10.5% | 216.62 |
| 2 | 23,084 | 1,899.67 | 12.57 | 20.69 | 5.70 | 14.99 | 24.0% | 78.91 |
| 3 | 25,851 | 3,614.33 | 23.26 | 31.93 | 7.23 | 24.70 | 39.6% | 68.33 |
| 4 | 27,665 | 3,809.07 | 10.53 | 15.12 | 5.33 | 9.78 | 15.7% | 25.68 |
| 5 | 28,874 | 3,743.30 | 10.05 | 10.13 | 3.74 | 6.39 | 10.2% | 17.07 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,855 | 5,376.99 | -6.32 | 24.09 | 8.71 | 15.38 | 24.6% | 28.61 |
| 41-60d | 25,652 | 2,972.62 | 15.99 | 14.30 | 5.33 | 8.97 | 14.4% | 30.19 |
| 61-90d | 35,324 | 3,914.33 | 19.92 | 19.96 | 7.17 | 12.79 | 20.5% | 32.68 |
| >90d | 12,298 | 1,105.28 | 32.79 | 27.57 | 2.30 | 25.27 | 40.5% | 228.63 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 2,125.65 | 21.61 | 24.64 | 3.67 | 20.97 | 33.6% | 98.66 |
| 2002-07 | 39,956 | 4,622.80 | 35.24 | 41.74 | 8.03 | 33.71 | 54.0% | 72.91 |
| 2008-13 | 45,278 | 6,620.77 | 5.52 | 19.55 | 11.81 | 7.74 | 12.4% | 11.69 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,094 | 2,356.09 | 15.94 | 9.07 | 4.12 | 4.95 | 7.9% | 21.00 |
| Chems | 2,590 | 305.15 | 1.28 | 2.20 | 0.51 | 1.69 | 2.7% | 55.42 |
| Durbl | 2,843 | 306.15 | 0.81 | 2.26 | 0.55 | 1.71 | 2.7% | 55.79 |
| Enrgy | 6,194 | 728.42 | 8.18 | 10.62 | 1.19 | 9.43 | 15.1% | 129.48 |
| Hlth | 11,696 | 1,213.37 | 10.05 | 8.32 | 2.27 | 6.05 | 9.7% | 49.90 |
| Manuf | 12,034 | 1,336.21 | 2.73 | 7.14 | 2.32 | 4.82 | 7.7% | 36.09 |
| Money | 17,583 | 2,118.09 | 7.17 | 19.32 | 3.69 | 15.63 | 25.0% | 73.80 |
| NoDur | 5,081 | 574.32 | 2.52 | 2.98 | 0.98 | 2.00 | 3.2% | 34.88 |
| Other | 18,377 | 2,048.37 | 7.91 | 12.23 | 3.86 | 8.38 | 13.4% | 40.90 |
| Shops | 11,984 | 1,335.59 | 7.65 | 8.84 | 2.35 | 6.49 | 10.4% | 48.61 |
| Telcm | 3,981 | 448.63 | -1.34 | 1.22 | 0.73 | 0.49 | 0.8% | 10.84 |
| Utils | 4,672 | 598.84 | -0.53 | 1.72 | 0.95 | 0.77 | 1.2% | 12.85 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,523 | 4,710.15 | 10.53 | 19.83 | 7.61 | 12.22 | 19.6% | 25.94 |
| low vol | 40,091 | 4,230.51 | 24.63 | 32.68 | 8.07 | 24.61 | 39.4% | 58.16 |
| mid vol | 37,903 | 4,264.38 | 26.18 | 32.44 | 7.52 | 24.92 | 39.9% | 58.45 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 10.70% | 0.00% |
| 0.5% | 6.7 | 10.15% | 0.55% |
| 1.0% | 13.3 | 9.55% | 1.15% |
| 2.0% | 26.6 | 8.12% | 2.58% |
| 5.0% | 66.6 | -2.80% | 13.50% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
