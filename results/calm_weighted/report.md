# calm_weighted

_equity strategy | family: equity-volatility | status: validated | derived from `balanced_leverage`_

## What this strategy does

Balanced Leverage, with each bet divided by how jumpy the stock is

**Why it should work.** PEAD is a stronger, cleaner signal in low-vol stocks; size positions by inverse risk.

**Design notes** (from the strategy's source file).

Calm Weighted -- Balanced Leverage, with bigger bets on calmer stocks (sized by inverse idiosyncratic volatility).

Within every (quarter, side, sector, size) cell, each event's weight is scaled by 1/ivol, so calm stocks get more capital than wild ones. Rationale: the earnings drift is worth more per unit of risk in low-vol names (the SUE x vol interaction is t = -2.4 to -2.7 in Fama-MacBeth), and the engine's cost/liquidity model charges the same bps regardless of vol. Raises Sharpe 1.30 -> 1.47 with no fitted parameter, in all three sub-eras.

`CalmWeightedMatched` re-sizes it to Balanced Leverage's exact volatility to answer "at the SAME risk, how much more return": 7.31% vs 6.49%.

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

| metric | calm_weighted | market (CRSP VW) |
|---|---|---|
| Annualized return | 5.64% | 9.94% |
| Annualized volatility | 4.51% | 19.35% |
| Sharpe (NAV-pct) | 1.24 | 0.59 |
| Sortino | 1.79 |  |
| Max drawdown | -17.5% | -55.5% |
| Calmar | 0.32 |  |
| Final NAV ($10M start) | $29,109,274 |  |
| Worst day | -3.69% |  |
| % positive days | 50.6% |  |
| Sharpe 1996-2001 | 2.03 |  |
| Sharpe 2002-2007 | 1.54 |  |
| Sharpe 2008-2013 | 0.29 |  |

Correlation of daily returns with the market: **-0.05**.

- best Sharpe among equity: `calm_rank_tilt` (1.81); this one: 1.24
- best return among equity: `market_boost_max` (0.15); this one: 0.0564
- best Calmar among equity: `calm_rank_tilt` (0.614); this one: 0.322

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
| P&L ($M, before costs) | 37.55 | -12.31 |
| P&L after costs ($M) | 34.57 | -15.46 |
| of which common (average-event) component ($M) | 23.96 | -24.53 |
| of which excess / PEAD component ($M) | 13.59 | 12.22 |
| excess minus costs ($M) | 10.61 | 9.07 |
| gross return per $ traded (bp) | 252.23 | -81.07 |
| positions in the money (hit rate) | 0.56 | 0.48 |
| stand-alone Sharpe (leg only) | 1.30 | -0.32 |

Daily P&L correlation between the two legs: **-0.79**.

- **The short leg loses money in absolute terms** (-12.3M before costs) while the long leg earns +37.5M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 24.0M / 24.5M. Stripping that common component, the surprise signal itself earned +13.6M on the long side and +12.2M on the short side.
- The two legs' daily P&L are -0.79 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.30 long / -0.32 short vs 1.24 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 5,819 | 97.95 | 1.49 | 0.01 | 0.20 | -0.19 | -1.0% | -19.30 |
| long | 8 | 15,492 | 326.71 | 6.47 | 1.26 | 0.66 | 0.60 | 3.0% | 18.36 |
| long | 9 | 17,756 | 457.99 | 9.69 | 4.16 | 0.91 | 3.25 | 16.5% | 70.91 |
| long | 10 | 19,540 | 605.95 | 16.92 | 8.17 | 1.21 | 6.95 | 35.3% | 114.73 |
| short | 1 | 17,672 | 600.75 | -6.48 | 4.95 | 1.29 | 3.66 | 18.6% | 60.97 |
| short | 2 | 18,038 | 522.42 | -5.09 | 4.51 | 1.07 | 3.44 | 17.5% | 65.93 |
| short | 3 | 14,145 | 352.94 | -3.29 | 2.69 | 0.71 | 1.98 | 10.1% | 56.08 |
| short | 4 | 2,187 | 42.32 | -0.60 | 0.07 | 0.08 | -0.01 | -0.1% | -3.10 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,151 | 273.10 | 0.24 | 1.95 | 1.06 | 0.89 | 4.5% | 32.69 |
| 2 | 21,121 | 562.95 | 6.30 | 8.24 | 1.60 | 6.64 | 33.7% | 118.00 |
| 3 | 23,790 | 687.37 | 5.68 | 7.14 | 1.47 | 5.67 | 28.8% | 82.55 |
| 4 | 25,693 | 736.76 | 4.21 | 5.16 | 1.18 | 3.98 | 20.2% | 54.08 |
| 5 | 26,894 | 746.82 | 2.68 | 3.31 | 0.83 | 2.49 | 12.6% | 33.33 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 12,462 | 206.58 | 2.24 | 3.68 | 1.03 | 2.64 | 13.4% | 127.96 |
| 2 | 21,542 | 626.74 | 4.13 | 6.91 | 1.88 | 5.03 | 25.5% | 80.21 |
| 3 | 24,096 | 750.06 | 6.10 | 7.81 | 1.50 | 6.31 | 32.1% | 84.11 |
| 4 | 25,666 | 722.72 | 4.06 | 4.59 | 1.01 | 3.57 | 18.2% | 49.45 |
| 5 | 26,879 | 700.65 | 2.52 | 2.77 | 0.70 | 2.07 | 10.5% | 29.50 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 39,177 | 1,036.37 | 0.99 | 8.91 | 1.99 | 6.92 | 35.2% | 66.78 |
| 41-60d | 26,318 | 759.45 | 3.00 | 6.24 | 1.68 | 4.56 | 23.2% | 60.04 |
| 61-90d | 36,129 | 977.98 | 9.56 | 5.93 | 1.81 | 4.12 | 20.9% | 42.12 |
| >90d | 9,025 | 233.22 | 5.55 | 4.73 | 0.65 | 4.08 | 20.7% | 175.07 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 32,145 | 639.04 | 8.17 | 9.64 | 1.27 | 8.37 | 42.5% | 130.97 |
| 2002-07 | 36,722 | 1,027.78 | 8.29 | 10.20 | 2.05 | 8.15 | 41.4% | 79.27 |
| 2008-13 | 41,782 | 1,340.18 | 2.65 | 5.97 | 2.80 | 3.17 | 16.1% | 23.63 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 19,826 | 287.79 | 1.29 | 1.86 | 0.62 | 1.25 | 6.3% | 43.24 |
| Chems | 2,543 | 203.74 | 0.84 | 1.21 | 0.36 | 0.85 | 4.3% | 41.63 |
| Durbl | 2,811 | 220.57 | 1.64 | 2.18 | 0.41 | 1.77 | 9.0% | 80.14 |
| Enrgy | 6,234 | 247.93 | 2.85 | 3.35 | 0.47 | 2.88 | 14.6% | 115.99 |
| Hlth | 10,915 | 287.33 | 1.18 | 1.80 | 0.61 | 1.19 | 6.1% | 41.45 |
| Manuf | 11,462 | 268.82 | 1.44 | 2.10 | 0.57 | 1.53 | 7.8% | 57.08 |
| Money | 15,495 | 249.53 | 2.01 | 2.66 | 0.58 | 2.08 | 10.6% | 83.40 |
| NoDur | 4,886 | 247.29 | 1.11 | 1.54 | 0.48 | 1.06 | 5.4% | 43.08 |
| Other | 16,535 | 274.79 | 2.20 | 2.90 | 0.62 | 2.27 | 11.5% | 82.73 |
| Shops | 11,323 | 280.69 | 2.57 | 3.16 | 0.59 | 2.57 | 13.1% | 91.61 |
| Telcm | 3,941 | 245.21 | 1.83 | 2.33 | 0.46 | 1.88 | 9.5% | 76.50 |
| Utils | 4,678 | 193.31 | 0.14 | 0.71 | 0.36 | 0.35 | 1.8% | 18.34 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 36,115 | 727.84 | 1.59 | 3.70 | 1.35 | 2.35 | 12.0% | 32.36 |
| low vol | 37,603 | 1,259.70 | 9.68 | 12.76 | 2.74 | 10.01 | 50.9% | 79.50 |
| mid vol | 35,206 | 972.34 | 7.05 | 8.47 | 1.93 | 6.54 | 33.2% | 67.24 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 5.64% | -0.00% |
| 0.5% | 1.5 | 5.35% | 0.29% |
| 1.0% | 3.1 | 5.04% | 0.60% |
| 2.0% | 6.1 | 4.37% | 1.27% |
| 5.0% | 15.3 | 1.69% | 3.95% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
