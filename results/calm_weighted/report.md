# calm_weighted

_equity strategy | family: equity-volatility | status: validated | derived from `balanced_leverage`_

## What this strategy does

Balanced Leverage, with each bet divided by how jumpy the stock is

**Why it should work.** PEAD is a stronger, cleaner signal in low-vol stocks; size positions by inverse risk.

**Design notes** (from the strategy's source file).

Strategy 9b -- Strategy 6 sized by inverse idiosyncratic volatility.

Within every (quarter, side, sector, size) cell, each event's weight is scaled by 1/ivol, so calm stocks get more capital than wild ones. Rationale: the earnings drift is worth more per unit of risk in low-vol names (the SUE x vol interaction is t = -2.4 to -2.7 in Fama-MacBeth), and the engine's cost/liquidity model charges the same bps regardless of vol. Raises Sharpe 1.30 -> 1.47 with no fitted parameter, in all three sub-eras.

`InverseVolVolMatched` re-sizes it to Strategy 6's exact volatility to answer "at the SAME risk, how much more return": 7.31% vs 6.49%.

| parameter | value |
|---|---|
| base_unit_fraction | 0.003 |
| max_gross_leverage | 2.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| beta_overlay | 0.0 |

## Performance

| metric | calm_weighted | market (CRSP VW) |
|---|---|---|
| Annualized return | 6.71% | 9.94% |
| Annualized volatility | 4.49% | 19.35% |
| Sharpe (NAV-pct) | 1.47 | 0.59 |
| Sortino | 2.17 |  |
| Max drawdown | -15.6% | -55.5% |
| Calmar | 0.43 |  |
| Final NAV ($10M start) | $35,421,134 |  |
| Worst day | -3.36% |  |
| % positive days | 52.4% |  |
| Sharpe 1996-2001 | 2.40 |  |
| Sharpe 2002-2007 | 1.63 |  |
| Sharpe 2008-2013 | 0.59 |  |

Correlation of daily returns with the market: **0.01**.

- best Sharpe among equity: `calm_rank_tilt` (1.81); this one: 1.47
- best return among equity: `market_boost_max` (0.15); this one: 0.0671
- best Calmar among equity: `calm_rank_tilt` (0.614); this one: 0.43

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
| P&L ($M, before costs) | 43.18 | -10.78 |
| P&L after costs ($M) | 39.82 | -14.40 |
| of which common (average-event) component ($M) | 27.28 | -27.98 |
| of which excess / PEAD component ($M) | 15.90 | 17.20 |
| excess minus costs ($M) | 12.54 | 13.57 |
| gross return per $ traded (bp) | 253.23 | -61.58 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 1.33 | -0.23 |

Daily P&L correlation between the two legs: **-0.81**.

- **The short leg loses money in absolute terms** (-10.8M before costs) while the long leg earns +43.2M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 27.3M / 28.0M. Stripping that common component, the surprise signal itself earned +15.9M on the long side and +17.2M on the short side.
- The two legs' daily P&L are -0.81 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.33 long / -0.23 short vs 1.47 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,029 | 151.20 | 2.79 | 0.25 | 0.30 | -0.05 | -0.2% | -3.44 |
| long | 8 | 16,588 | 401.72 | 9.02 | 3.19 | 0.80 | 2.39 | 9.1% | 59.48 |
| long | 9 | 17,747 | 528.00 | 8.81 | 2.11 | 1.02 | 1.08 | 4.2% | 20.55 |
| long | 10 | 17,174 | 624.27 | 19.20 | 10.36 | 1.24 | 9.12 | 34.9% | 146.09 |
| short | 1 | 17,169 | 625.49 | -5.50 | 5.11 | 1.35 | 3.76 | 14.4% | 60.15 |
| short | 2 | 17,281 | 542.85 | -4.32 | 5.97 | 1.11 | 4.86 | 18.6% | 89.51 |
| short | 3 | 15,970 | 414.21 | -3.31 | 4.31 | 0.84 | 3.47 | 13.3% | 83.83 |
| short | 4 | 9,171 | 168.10 | -1.27 | 1.80 | 0.32 | 1.48 | 5.7% | 88.07 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,213 | 299.80 | 5.47 | 7.38 | 1.16 | 6.22 | 23.8% | 207.43 |
| 2 | 22,885 | 638.69 | 5.81 | 7.99 | 1.82 | 6.17 | 23.6% | 96.63 |
| 3 | 25,501 | 791.87 | 6.65 | 8.18 | 1.68 | 6.50 | 24.9% | 82.09 |
| 4 | 27,583 | 858.14 | 4.41 | 5.65 | 1.36 | 4.28 | 16.4% | 49.90 |
| 5 | 28,947 | 867.35 | 3.09 | 3.90 | 0.96 | 2.94 | 11.3% | 33.93 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,650 | 228.45 | 5.04 | 6.62 | 1.14 | 5.47 | 21.0% | 239.60 |
| 2 | 23,084 | 704.48 | 5.79 | 8.86 | 2.11 | 6.75 | 25.8% | 95.75 |
| 3 | 25,851 | 863.36 | 6.82 | 8.95 | 1.73 | 7.22 | 27.7% | 83.66 |
| 4 | 27,665 | 841.45 | 4.75 | 5.25 | 1.18 | 4.07 | 15.6% | 48.33 |
| 5 | 28,874 | 817.81 | 3.00 | 3.39 | 0.82 | 2.57 | 9.8% | 31.43 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,855 | 1,327.77 | 0.88 | 8.30 | 2.36 | 5.94 | 22.7% | 44.73 |
| 41-60d | 25,652 | 790.84 | 4.93 | 5.08 | 1.62 | 3.46 | 13.2% | 43.73 |
| 61-90d | 35,324 | 995.63 | 7.27 | 8.77 | 2.12 | 6.64 | 25.4% | 66.73 |
| >90d | 12,298 | 341.59 | 12.34 | 10.95 | 0.87 | 10.07 | 38.6% | 294.91 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 689.80 | 9.68 | 11.33 | 1.37 | 9.96 | 38.1% | 144.39 |
| 2002-07 | 39,956 | 1,152.94 | 10.10 | 12.32 | 2.28 | 10.04 | 38.5% | 87.10 |
| 2008-13 | 45,278 | 1,613.11 | 5.64 | 9.45 | 3.33 | 6.11 | 23.4% | 37.90 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,094 | 341.69 | 2.19 | 2.92 | 0.73 | 2.19 | 8.4% | 63.98 |
| Chems | 2,590 | 229.95 | 1.46 | 1.85 | 0.40 | 1.45 | 5.5% | 62.94 |
| Durbl | 2,843 | 248.11 | 2.35 | 2.86 | 0.45 | 2.41 | 9.2% | 96.99 |
| Enrgy | 6,194 | 278.49 | 3.79 | 4.41 | 0.52 | 3.89 | 14.9% | 139.59 |
| Hlth | 11,696 | 330.66 | 2.61 | 3.32 | 0.70 | 2.62 | 10.0% | 79.16 |
| Manuf | 12,034 | 309.06 | 1.80 | 2.61 | 0.65 | 1.97 | 7.5% | 63.67 |
| Money | 17,583 | 302.38 | 2.62 | 3.41 | 0.69 | 2.72 | 10.4% | 90.01 |
| NoDur | 5,081 | 278.21 | 1.44 | 1.95 | 0.53 | 1.42 | 5.4% | 50.88 |
| Other | 18,377 | 329.38 | 2.59 | 3.42 | 0.74 | 2.68 | 10.2% | 81.25 |
| Shops | 11,984 | 320.79 | 3.15 | 3.83 | 0.67 | 3.16 | 12.1% | 98.56 |
| Telcm | 3,981 | 270.71 | 0.85 | 1.39 | 0.49 | 0.89 | 3.4% | 33.01 |
| Utils | 4,672 | 216.42 | 0.56 | 1.13 | 0.40 | 0.73 | 2.8% | 33.97 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,523 | 847.40 | 4.09 | 6.58 | 1.56 | 5.01 | 19.2% | 59.15 |
| low vol | 40,091 | 1,438.79 | 11.70 | 15.05 | 3.10 | 11.95 | 45.8% | 83.08 |
| mid vol | 37,903 | 1,120.54 | 9.45 | 11.15 | 2.21 | 8.94 | 34.2% | 79.76 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 6.71% | 0.00% |
| 0.5% | 1.8 | 6.43% | 0.28% |
| 1.0% | 3.5 | 6.13% | 0.57% |
| 2.0% | 7.1 | 5.50% | 1.21% |
| 5.0% | 17.7 | 2.99% | 3.72% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
