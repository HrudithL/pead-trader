# s9b_inverse_vol_matched

_equity strategy | family: equity-volatility | status: validated | derived from `s9b_inverse_vol`_

## What this strategy does

9b with base unit solved so annualized vol equals Strategy 6's (4.94%)

**Why it should work.** PEAD is a stronger, cleaner signal in low-vol stocks; size positions by inverse risk.

**Design notes** (from the strategy's source file).

Strategy 9b -- Strategy 6 sized by inverse idiosyncratic volatility.

Within every (quarter, side, sector, size) cell, each event's weight is scaled by 1/ivol, so calm stocks get more capital than wild ones. Rationale: the earnings drift is worth more per unit of risk in low-vol names (the SUE x vol interaction is t = -2.4 to -2.7 in Fama-MacBeth), and the engine's cost/liquidity model charges the same bps regardless of vol. Raises Sharpe 1.30 -> 1.47 with no fitted parameter, in all three sub-eras.

`InverseVolVolMatched` re-sizes it to Strategy 6's exact volatility to answer "at the SAME risk, how much more return": 7.31% vs 6.49%.

| parameter | value |
|---|---|
| base_unit_fraction | 0.003468808347716693 |
| max_gross_leverage | 2.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| beta_overlay | 0.0 |

## Performance

| metric | s9b_inverse_vol_matched | market (CRSP VW) |
|---|---|---|
| Annualized return | 7.31% | 9.94% |
| Annualized volatility | 4.94% | 19.35% |
| Sharpe (NAV-pct) | 1.45 | 0.59 |
| Sortino | 2.15 |  |
| Max drawdown | -17.0% | -55.5% |
| Calmar | 0.43 |  |
| Final NAV ($10M start) | $39,500,315 |  |
| Worst day | -3.35% |  |
| % positive days | 52.4% |  |
| Sharpe 1996-2001 | 2.39 |  |
| Sharpe 2002-2007 | 1.66 |  |
| Sharpe 2008-2013 | 0.52 |  |

Correlation of daily returns with the market: **-0.00**.

- best Sharpe among equity: `s10_vol_rank_tilt` (1.81); this one: 1.45
- best return among equity: `s6_beta_overlay_aggressive` (0.15); this one: 0.0731
- best Calmar among equity: `s10_vol_rank_tilt` (0.614); this one: 0.43

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
| P&L ($M, before costs) | 51.01 | -13.08 |
| P&L after costs ($M) | 46.96 | -17.46 |
| of which common (average-event) component ($M) | 33.69 | -34.49 |
| of which excess / PEAD component ($M) | 17.32 | 21.41 |
| excess minus costs ($M) | 13.27 | 17.03 |
| gross return per $ traded (bp) | 242.65 | -60.53 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 1.32 | -0.23 |

Daily P&L correlation between the two legs: **-0.81**.

- **The short leg loses money in absolute terms** (-13.1M before costs) while the long leg earns +51.0M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 33.7M / 34.5M. Stripping that common component, the surprise signal itself earned +17.3M on the long side and +21.4M on the short side.
- The two legs' daily P&L are -0.81 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.32 long / -0.23 short vs 1.45 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,017 | 187.53 | 3.24 | 0.08 | 0.36 | -0.28 | -0.9% | -15.12 |
| long | 8 | 16,581 | 496.58 | 10.87 | 3.63 | 0.96 | 2.67 | 8.8% | 53.76 |
| long | 9 | 17,745 | 651.92 | 10.47 | 2.12 | 1.24 | 0.88 | 2.9% | 13.48 |
| long | 10 | 17,173 | 766.04 | 22.39 | 11.49 | 1.48 | 10.01 | 33.0% | 130.65 |
| short | 1 | 17,166 | 766.10 | -6.38 | 6.51 | 1.61 | 4.90 | 16.2% | 63.93 |
| short | 2 | 17,276 | 669.92 | -5.27 | 7.41 | 1.34 | 6.07 | 20.0% | 90.63 |
| short | 3 | 15,967 | 514.75 | -4.22 | 5.23 | 1.02 | 4.21 | 13.9% | 81.69 |
| short | 4 | 9,161 | 209.99 | -1.58 | 2.25 | 0.40 | 1.85 | 6.1% | 88.25 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,194 | 334.35 | 5.62 | 7.77 | 1.28 | 6.49 | 21.4% | 194.22 |
| 2 | 22,880 | 766.10 | 6.70 | 9.33 | 2.16 | 7.18 | 23.7% | 93.68 |
| 3 | 25,498 | 978.87 | 7.90 | 9.75 | 2.06 | 7.68 | 25.4% | 78.51 |
| 4 | 27,576 | 1,082.62 | 5.42 | 6.98 | 1.71 | 5.26 | 17.4% | 48.63 |
| 5 | 28,938 | 1,100.90 | 3.87 | 4.89 | 1.21 | 3.68 | 12.1% | 33.44 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,631 | 247.89 | 5.24 | 6.96 | 1.24 | 5.72 | 18.9% | 230.70 |
| 2 | 23,084 | 835.03 | 6.57 | 10.23 | 2.50 | 7.72 | 25.5% | 92.48 |
| 3 | 25,848 | 1,073.98 | 8.16 | 10.90 | 2.15 | 8.75 | 28.9% | 81.51 |
| 4 | 27,653 | 1,066.69 | 5.75 | 6.35 | 1.49 | 4.86 | 16.0% | 45.55 |
| 5 | 28,865 | 1,038.89 | 3.74 | 4.24 | 1.04 | 3.20 | 10.6% | 30.79 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,837 | 1,657.83 | 0.68 | 9.89 | 2.91 | 6.98 | 23.0% | 42.10 |
| 41-60d | 25,647 | 973.48 | 6.02 | 6.04 | 1.96 | 4.09 | 13.5% | 41.97 |
| 61-90d | 35,313 | 1,222.87 | 8.62 | 10.36 | 2.55 | 7.81 | 25.8% | 63.84 |
| >90d | 12,289 | 408.66 | 14.18 | 12.44 | 1.01 | 11.43 | 37.7% | 279.64 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 809.38 | 11.18 | 13.03 | 1.57 | 11.46 | 37.8% | 141.54 |
| 2002-07 | 39,913 | 1,427.49 | 12.27 | 14.91 | 2.77 | 12.15 | 40.1% | 85.09 |
| 2008-13 | 45,278 | 2,025.98 | 6.06 | 10.79 | 4.09 | 6.70 | 22.1% | 33.06 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,085 | 427.07 | 2.45 | 3.33 | 0.90 | 2.43 | 8.0% | 56.95 |
| Chems | 2,588 | 279.38 | 1.84 | 2.28 | 0.48 | 1.80 | 5.9% | 64.39 |
| Durbl | 2,843 | 300.63 | 2.59 | 3.17 | 0.53 | 2.64 | 8.7% | 87.71 |
| Enrgy | 6,194 | 342.20 | 4.51 | 5.29 | 0.63 | 4.67 | 15.4% | 136.36 |
| Hlth | 11,685 | 411.81 | 3.00 | 3.85 | 0.86 | 3.00 | 9.9% | 72.72 |
| Manuf | 12,033 | 383.08 | 2.04 | 3.02 | 0.78 | 2.24 | 7.4% | 58.46 |
| Money | 17,569 | 375.06 | 3.08 | 4.03 | 0.83 | 3.20 | 10.6% | 85.34 |
| NoDur | 5,080 | 341.14 | 1.72 | 2.34 | 0.63 | 1.70 | 5.6% | 49.90 |
| Other | 18,372 | 410.74 | 3.06 | 4.07 | 0.91 | 3.16 | 10.4% | 76.96 |
| Shops | 11,984 | 398.66 | 3.75 | 4.58 | 0.81 | 3.77 | 12.4% | 94.55 |
| Telcm | 3,981 | 327.17 | 0.81 | 1.44 | 0.58 | 0.86 | 2.8% | 26.17 |
| Utils | 4,672 | 265.90 | 0.66 | 1.32 | 0.48 | 0.84 | 2.8% | 31.68 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,494 | 1,057.22 | 4.66 | 7.73 | 1.92 | 5.82 | 19.2% | 55.02 |
| low vol | 40,087 | 1,759.86 | 13.57 | 17.56 | 3.70 | 13.86 | 45.7% | 78.77 |
| mid vol | 37,893 | 1,388.11 | 11.06 | 13.09 | 2.69 | 10.40 | 34.3% | 74.93 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 7.31% | 0.00% |
| 0.5% | 2.2 | 7.00% | 0.31% |
| 1.0% | 4.3 | 6.67% | 0.64% |
| 2.0% | 8.7 | 5.95% | 1.36% |
| 5.0% | 21.7 | 3.01% | 4.30% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
