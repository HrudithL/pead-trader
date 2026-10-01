# s9c_inverse_vol_filtered

_equity strategy | family: equity-volatility | status: research | derived from `s6_neutral_levered`_

## What this strategy does

Inverse-vol sizing combined with the walk-forward cell filter

**Why it should work.** Skip the cells that history says do not pay -- but the data shows a gradient, not a bad cell.

**Design notes** (from the strategy's source file).

Strategy 9a / 9c -- walk-forward volatility-cell filter (alone and combined with inverse-vol).

Drops a (side, idio-vol tercile) cell from new entries when its own history says it has not earned its keep, judged ONLY on quarters whose positions had fully exited. Result: filtering does not help on its own (Sharpe 1.23 vs 1.30 baseline-equivalent run; the effect is a smooth gradient, not one broken cell) and adds nothing once inverse-vol sizing is on (9c 1.41 < 9b 1.47). Kept as a documented negative result: size by vol, do not filter by it.

| parameter | value |
|---|---|
| base_unit_fraction | 0.003 |
| max_gross_leverage | 2.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| beta_overlay | 0.0 |

## Performance

| metric | s9c_inverse_vol_filtered | market (CRSP VW) |
|---|---|---|
| Annualized return | 6.54% | 9.94% |
| Annualized volatility | 4.55% | 19.35% |
| Sharpe (NAV-pct) | 1.41 | 0.59 |
| Sortino | 2.07 |  |
| Max drawdown | -15.7% | -55.5% |
| Calmar | 0.42 |  |
| Final NAV ($10M start) | $34,340,070 |  |
| Worst day | -3.42% |  |
| % positive days | 52.3% |  |
| Sharpe 1996-2001 | 2.26 |  |
| Sharpe 2002-2007 | 1.57 |  |
| Sharpe 2008-2013 | 0.60 |  |

Correlation of daily returns with the market: **0.02**.

- best Sharpe among equity: `s10_vol_rank_tilt` (1.81); this one: 1.41
- best return among equity: `s6_beta_overlay_aggressive` (0.15); this one: 0.0654
- best Calmar among equity: `s10_vol_rank_tilt` (0.614); this one: 0.418

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
| P&L ($M, before costs) | 42.03 | -10.89 |
| P&L after costs ($M) | 38.76 | -14.42 |
| of which common (average-event) component ($M) | 26.55 | -27.19 |
| of which excess / PEAD component ($M) | 15.48 | 16.30 |
| excess minus costs ($M) | 12.21 | 12.77 |
| gross return per $ traded (bp) | 253.44 | -64.00 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 1.33 | -0.25 |

Daily P&L correlation between the two legs: **-0.81**.

- **The short leg loses money in absolute terms** (-10.9M before costs) while the long leg earns +42.0M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 26.5M / 27.2M. Stripping that common component, the surprise signal itself earned +15.5M on the long side and +16.3M on the short side.
- The two legs' daily P&L are -0.81 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.33 long / -0.25 short vs 1.41 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,009 | 146.90 | 2.71 | 0.24 | 0.29 | -0.05 | -0.2% | -3.48 |
| long | 8 | 16,508 | 390.71 | 8.78 | 3.09 | 0.78 | 2.31 | 9.3% | 59.15 |
| long | 9 | 17,676 | 513.54 | 8.55 | 2.04 | 1.00 | 1.04 | 4.2% | 20.23 |
| long | 10 | 17,111 | 607.20 | 18.71 | 10.12 | 1.20 | 8.91 | 35.7% | 146.75 |
| short | 1 | 17,093 | 608.49 | -5.44 | 4.89 | 1.31 | 3.58 | 14.3% | 58.83 |
| short | 2 | 17,204 | 526.88 | -4.28 | 5.68 | 1.08 | 4.60 | 18.4% | 87.37 |
| short | 3 | 15,897 | 402.46 | -3.39 | 4.04 | 0.82 | 3.22 | 12.9% | 80.05 |
| short | 4 | 9,154 | 163.49 | -1.32 | 1.68 | 0.31 | 1.37 | 5.5% | 83.62 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,166 | 294.58 | 5.45 | 7.32 | 1.14 | 6.18 | 24.7% | 209.69 |
| 2 | 22,785 | 622.38 | 5.57 | 7.68 | 1.77 | 5.91 | 23.7% | 94.96 |
| 3 | 25,396 | 769.16 | 6.24 | 7.74 | 1.64 | 6.10 | 24.4% | 79.32 |
| 4 | 27,473 | 832.21 | 4.17 | 5.35 | 1.32 | 4.02 | 16.1% | 48.36 |
| 5 | 28,832 | 841.35 | 2.90 | 3.70 | 0.93 | 2.77 | 11.1% | 32.91 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,618 | 225.32 | 5.01 | 6.56 | 1.13 | 5.43 | 21.7% | 241.08 |
| 2 | 23,010 | 687.46 | 5.62 | 8.59 | 2.06 | 6.53 | 26.1% | 94.95 |
| 3 | 25,773 | 838.97 | 6.51 | 8.59 | 1.68 | 6.91 | 27.7% | 82.40 |
| 4 | 27,539 | 815.59 | 4.56 | 4.99 | 1.14 | 3.85 | 15.4% | 47.23 |
| 5 | 28,707 | 792.06 | 2.61 | 3.01 | 0.79 | 2.22 | 8.9% | 28.05 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,693 | 1,290.11 | 0.52 | 7.71 | 2.30 | 5.41 | 21.7% | 41.96 |
| 41-60d | 25,536 | 768.41 | 4.74 | 4.86 | 1.58 | 3.28 | 13.1% | 42.65 |
| 61-90d | 35,163 | 967.77 | 7.01 | 8.50 | 2.07 | 6.43 | 25.8% | 66.48 |
| >90d | 12,260 | 333.38 | 12.07 | 10.71 | 0.85 | 9.86 | 39.5% | 295.65 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,652 | 683.38 | 9.27 | 10.83 | 1.35 | 9.47 | 37.9% | 138.65 |
| 2002-07 | 39,722 | 1,115.51 | 9.49 | 11.69 | 2.21 | 9.47 | 37.9% | 84.93 |
| 2008-13 | 45,278 | 1,560.78 | 5.58 | 9.27 | 3.24 | 6.03 | 24.1% | 38.65 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 21,907 | 331.60 | 1.91 | 2.60 | 0.71 | 1.89 | 7.6% | 57.02 |
| Chems | 2,587 | 224.04 | 1.36 | 1.74 | 0.40 | 1.35 | 5.4% | 60.14 |
| Durbl | 2,836 | 241.66 | 2.31 | 2.81 | 0.44 | 2.37 | 9.5% | 98.02 |
| Enrgy | 6,186 | 271.01 | 3.68 | 4.29 | 0.51 | 3.78 | 15.1% | 139.41 |
| Hlth | 11,622 | 321.02 | 2.54 | 3.22 | 0.68 | 2.54 | 10.1% | 78.96 |
| Manuf | 12,007 | 300.44 | 1.79 | 2.59 | 0.63 | 1.96 | 7.9% | 65.30 |
| Money | 17,569 | 293.98 | 2.56 | 3.33 | 0.67 | 2.66 | 10.6% | 90.40 |
| NoDur | 5,074 | 270.67 | 1.34 | 1.84 | 0.52 | 1.32 | 5.3% | 48.90 |
| Other | 18,309 | 319.76 | 2.56 | 3.37 | 0.72 | 2.65 | 10.6% | 82.71 |
| Shops | 11,943 | 311.85 | 3.09 | 3.75 | 0.65 | 3.10 | 12.4% | 99.33 |
| Telcm | 3,942 | 263.04 | 0.64 | 1.13 | 0.48 | 0.65 | 2.6% | 24.59 |
| Utils | 4,670 | 210.60 | 0.56 | 1.12 | 0.39 | 0.73 | 2.9% | 34.63 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,046 | 814.48 | 3.66 | 6.05 | 1.51 | 4.54 | 18.2% | 55.81 |
| low vol | 40,091 | 1,402.43 | 11.45 | 14.69 | 3.03 | 11.65 | 46.7% | 83.11 |
| mid vol | 37,903 | 1,094.45 | 9.05 | 10.73 | 2.16 | 8.56 | 34.3% | 78.26 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 6.54% | 0.00% |
| 0.5% | 1.7 | 6.26% | 0.28% |
| 1.0% | 3.4 | 5.96% | 0.57% |
| 2.0% | 6.9 | 5.33% | 1.21% |
| 5.0% | 17.2 | 2.81% | 3.73% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
