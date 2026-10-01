# size_balanced_aggressive

_equity strategy | family: equity-aggressive | status: research | derived from `size_balanced`_

## What this strategy does

size_balanced re-sized for return: base unit 0.009, 6x leverage cap

**Why it should work.** PEAD is stronger in small caps; balancing by size keeps the edge but removes a size bet.

**Design notes** (from the strategy's source file).

Aggressive-sizing variants of the hand-built equity strategies.

Same signal, trim, tilt and neutralization as each base strategy; only the two dials that set how big and how levered the book is change (base_unit_fraction, gross-leverage cap = 6x). The multiplier for each was picked by sweeping 1.5x..12x the original base unit and taking the highest return subject to Sharpe >= 0.6 and max drawdown >= -40% (a worse drawdown is a fund-ending event, not a risk preference). The untouched 5%-of-ADV liquidity cap remains the real capacity constraint, and bites roughly four times as often at this sizing.

These are generated from the base strategy classes, so a change to a base strategy's weights flows through automatically.

| parameter | value |
|---|---|
| base_unit_fraction | 0.009 |
| max_gross_leverage | 6.0 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | proportional |
| beta_overlay | 0.0 |

## Performance

| metric | size_balanced_aggressive | market (CRSP VW) |
|---|---|---|
| Annualized return | 8.45% | 9.94% |
| Annualized volatility | 12.26% | 19.35% |
| Sharpe (NAV-pct) | 0.72 | 0.59 |
| Sortino | 1.01 |  |
| Max drawdown | -37.5% | -55.5% |
| Calmar | 0.23 |  |
| Final NAV ($10M start) | $48,527,312 |  |
| Worst day | -7.65% |  |
| % positive days | 51.2% |  |
| Sharpe 1996-2001 | 1.00 |  |
| Sharpe 2002-2007 | 1.52 |  |
| Sharpe 2008-2013 | 0.03 |  |

Correlation of daily returns with the market: **0.00**.

- best Sharpe among equity: `s10_vol_rank_tilt` (1.81); this one: 0.723
- best return among equity: `s6_beta_overlay_aggressive` (0.15); this one: 0.0845
- best Calmar among equity: `s10_vol_rank_tilt` (0.614); this one: 0.226

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
| P&L ($M, before costs) | 67.08 | -18.11 |
| P&L after costs ($M) | 62.06 | -23.53 |
| of which common (average-event) component ($M) | 49.75 | -48.04 |
| of which excess / PEAD component ($M) | 17.33 | 29.92 |
| excess minus costs ($M) | 12.31 | 24.50 |
| gross return per $ traded (bp) | 215.17 | -56.29 |
| positions in the money (hit rate) | 0.57 | 0.49 |
| stand-alone Sharpe (leg only) | 0.95 | -0.15 |

Daily P&L correlation between the two legs: **-0.54**.

- **The short leg loses money in absolute terms** (-18.1M before costs) while the long leg earns +67.1M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 49.7M / 48.0M. Stripping that common component, the surprise signal itself earned +17.3M on the long side and +29.9M on the short side.
- The two legs' daily P&L are -0.54 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 0.95 long / -0.15 short vs 0.72 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 10 | 17,174 | 3,117.40 | 62.06 | 17.33 | 5.02 | 12.31 | 33.4% | 39.50 |
| short | 1 | 17,169 | 3,218.32 | -23.53 | 29.92 | 5.42 | 24.50 | 66.6% | 76.14 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,832 | 217.96 | 1.03 | 2.64 | 0.70 | 1.94 | 5.3% | 88.94 |
| 2 | 7,159 | 851.52 | 9.00 | 11.35 | 2.04 | 9.31 | 25.3% | 109.33 |
| 3 | 7,735 | 1,508.31 | 19.31 | 20.58 | 2.85 | 17.73 | 48.2% | 117.55 |
| 4 | 7,731 | 1,825.97 | 4.75 | 6.36 | 2.73 | 3.64 | 9.9% | 19.93 |
| 5 | 6,886 | 1,931.95 | 4.43 | 6.33 | 2.12 | 4.20 | 11.4% | 21.74 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 4,357 | 95.80 | 2.67 | 3.37 | 0.48 | 2.89 | 7.8% | 301.44 |
| 2 | 7,223 | 626.01 | 4.82 | 7.36 | 1.88 | 5.48 | 14.9% | 87.50 |
| 3 | 7,834 | 1,653.81 | 21.64 | 24.29 | 3.31 | 20.98 | 57.0% | 126.88 |
| 4 | 7,850 | 2,025.08 | 3.79 | 6.53 | 2.83 | 3.69 | 10.0% | 18.24 |
| 5 | 7,077 | 1,934.54 | 5.72 | 5.82 | 1.94 | 3.88 | 10.5% | 20.06 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 6,338 | 1,638.57 | -1.43 | 14.75 | 2.33 | 12.42 | 33.7% | 75.82 |
| 41-60d | 11,202 | 1,959.72 | 6.23 | 6.77 | 3.22 | 3.55 | 9.6% | 18.11 |
| 61-90d | 12,665 | 2,326.18 | 15.79 | 8.40 | 3.90 | 4.50 | 12.2% | 19.36 |
| >90d | 4,138 | 411.25 | 17.94 | 17.33 | 0.98 | 16.34 | 44.4% | 397.41 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 9,920 | 1,114.42 | 12.42 | 13.61 | 1.76 | 11.84 | 32.2% | 106.27 |
| 2002-07 | 11,506 | 2,156.30 | 24.73 | 26.98 | 3.52 | 23.46 | 63.7% | 108.78 |
| 2008-13 | 12,917 | 3,064.99 | 1.38 | 6.67 | 5.15 | 1.52 | 4.1% | 4.95 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 6,718 | 1,160.09 | 9.46 | 5.26 | 1.88 | 3.38 | 9.2% | 29.16 |
| Chems | 687 | 139.18 | 0.43 | 0.52 | 0.22 | 0.30 | 0.8% | 21.51 |
| Durbl | 869 | 140.56 | -0.42 | 0.34 | 0.25 | 0.09 | 0.2% | 6.50 |
| Enrgy | 1,375 | 271.72 | 5.57 | 6.25 | 0.44 | 5.81 | 15.8% | 213.78 |
| Hlth | 3,037 | 511.47 | 3.67 | 2.12 | 0.87 | 1.25 | 3.4% | 24.48 |
| Manuf | 3,627 | 669.11 | 1.81 | 3.82 | 1.08 | 2.74 | 7.4% | 40.96 |
| Money | 5,366 | 1,096.02 | 1.04 | 9.48 | 1.78 | 7.70 | 20.9% | 70.27 |
| NoDur | 1,540 | 295.77 | 2.35 | 2.53 | 0.48 | 2.05 | 5.6% | 69.33 |
| Other | 5,074 | 881.82 | 6.37 | 6.76 | 1.57 | 5.19 | 14.1% | 58.89 |
| Shops | 3,547 | 641.11 | 6.64 | 7.00 | 1.05 | 5.95 | 16.2% | 92.77 |
| Telcm | 1,164 | 224.12 | 1.16 | 2.09 | 0.35 | 1.74 | 4.7% | 77.79 |
| Utils | 1,339 | 304.75 | 0.44 | 1.07 | 0.47 | 0.61 | 1.6% | 19.93 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 11,517 | 2,311.71 | 3.82 | 9.35 | 3.51 | 5.83 | 15.8% | 25.23 |
| low vol | 11,554 | 1,942.27 | 17.45 | 19.94 | 3.47 | 16.47 | 44.7% | 84.81 |
| mid vol | 10,730 | 1,995.54 | 16.79 | 17.54 | 3.30 | 14.24 | 38.7% | 71.37 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 8.45% | 0.00% |
| 0.5% | 3.4 | 8.05% | 0.40% |
| 1.0% | 6.8 | 7.62% | 0.83% |
| 2.0% | 13.5 | 6.64% | 1.80% |
| 5.0% | 33.8 | 2.00% | 6.44% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
