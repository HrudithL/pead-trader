# s6_neutral_levered

_equity strategy | family: equity-neutralized | status: validated | derived from `s5_sector_neutral`_

## What this strategy does

Strategy 5 weights, 2.5x gross leverage cap, base unit 0.003

**Why it should work.** Market-neutral earnings drift with enough gross exposure to matter; the neutrality is the hedge.

**Design notes** (from the strategy's source file).

Strategy 6 -- Sector/size-neutral book with the leverage cap raised to 2.5x.

Strategy 5's weights, but with the 1.5x cap lifted to 2.5x (the point where it stops binding) and sizing retuned. Because sector+size neutralization already prevents concentration, the cap was protecting nothing: return rises 4.1% -> 6.5% with the Sharpe holding at ~1.3. The pure-alpha reference strategy for the repo.

| parameter | value |
|---|---|
| base_unit_fraction | 0.003 |
| max_gross_leverage | 2.5 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| beta_overlay | 0.0 |

## Performance

| metric | s6_neutral_levered | market (CRSP VW) |
|---|---|---|
| Annualized return | 6.49% | 9.94% |
| Annualized volatility | 4.94% | 19.35% |
| Sharpe (NAV-pct) | 1.30 | 0.59 |
| Sortino | 1.90 |  |
| Max drawdown | -16.6% | -55.5% |
| Calmar | 0.39 |  |
| Final NAV ($10M start) | $34,048,037 |  |
| Worst day | -3.57% |  |
| % positive days | 52.0% |  |
| Sharpe 1996-2001 | 2.07 |  |
| Sharpe 2002-2007 | 1.52 |  |
| Sharpe 2008-2013 | 0.47 |  |

Correlation of daily returns with the market: **0.02**.

- best Sharpe among equity: `s10_vol_rank_tilt` (1.81); this one: 1.3
- best return among equity: `s6_beta_overlay_aggressive` (0.15); this one: 0.0649
- best Calmar among equity: `s10_vol_rank_tilt` (0.614); this one: 0.391

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
| P&L ($M, before costs) | 42.74 | -11.85 |
| P&L after costs ($M) | 39.45 | -15.40 |
| of which common (average-event) component ($M) | 26.88 | -27.72 |
| of which excess / PEAD component ($M) | 15.86 | 15.87 |
| excess minus costs ($M) | 12.57 | 12.32 |
| gross return per $ traded (bp) | 253.36 | -68.44 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 1.22 | -0.24 |

Daily P&L correlation between the two legs: **-0.81**.

- **The short leg loses money in absolute terms** (-11.8M before costs) while the long leg earns +42.7M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 26.9M / 27.7M. Stripping that common component, the surprise signal itself earned +15.9M on the long side and +15.9M on the short side.
- The two legs' daily P&L are -0.81 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.22 long / -0.24 short vs 1.30 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,029 | 148.61 | 3.03 | 0.56 | 0.29 | 0.27 | 1.1% | 18.00 |
| long | 8 | 16,588 | 394.76 | 9.19 | 3.47 | 0.78 | 2.69 | 10.8% | 68.11 |
| long | 9 | 17,747 | 522.21 | 8.37 | 1.77 | 1.01 | 0.76 | 3.1% | 14.60 |
| long | 10 | 17,174 | 621.45 | 18.85 | 10.07 | 1.22 | 8.85 | 35.6% | 142.39 |
| short | 1 | 17,169 | 624.20 | -6.03 | 4.58 | 1.33 | 3.25 | 13.1% | 52.06 |
| short | 2 | 17,281 | 539.87 | -4.65 | 5.58 | 1.09 | 4.49 | 18.0% | 83.16 |
| short | 3 | 15,970 | 404.59 | -3.39 | 4.05 | 0.81 | 3.24 | 13.0% | 79.97 |
| short | 4 | 9,171 | 162.59 | -1.33 | 1.66 | 0.31 | 1.35 | 5.4% | 82.78 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,213 | 306.96 | 5.45 | 7.41 | 1.18 | 6.24 | 25.1% | 203.15 |
| 2 | 22,885 | 641.27 | 6.15 | 8.35 | 1.80 | 6.55 | 26.3% | 102.10 |
| 3 | 25,501 | 785.23 | 6.22 | 7.75 | 1.64 | 6.11 | 24.6% | 77.86 |
| 4 | 27,583 | 839.10 | 3.71 | 4.93 | 1.31 | 3.62 | 14.5% | 43.15 |
| 5 | 28,947 | 845.72 | 2.51 | 3.30 | 0.93 | 2.37 | 9.5% | 28.03 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,650 | 225.40 | 5.20 | 6.74 | 1.13 | 5.61 | 22.6% | 249.07 |
| 2 | 23,084 | 676.60 | 5.79 | 8.72 | 2.03 | 6.69 | 26.9% | 98.85 |
| 3 | 25,851 | 834.81 | 6.30 | 8.50 | 1.67 | 6.83 | 27.5% | 81.85 |
| 4 | 27,665 | 843.84 | 4.54 | 5.11 | 1.18 | 3.93 | 15.8% | 46.53 |
| 5 | 28,874 | 837.44 | 2.21 | 2.66 | 0.84 | 1.82 | 7.3% | 21.71 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,855 | 1,296.80 | -0.36 | 7.02 | 2.28 | 4.74 | 19.1% | 36.57 |
| 41-60d | 25,652 | 773.42 | 4.67 | 4.86 | 1.58 | 3.28 | 13.2% | 42.46 |
| 61-90d | 35,324 | 1,006.66 | 7.13 | 8.63 | 2.12 | 6.51 | 26.2% | 64.70 |
| >90d | 12,298 | 341.40 | 12.61 | 11.22 | 0.87 | 10.35 | 41.6% | 303.13 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 702.89 | 9.20 | 10.91 | 1.38 | 9.54 | 38.3% | 135.71 |
| 2002-07 | 39,956 | 1,131.98 | 10.13 | 12.35 | 2.22 | 10.13 | 40.7% | 89.47 |
| 2008-13 | 45,278 | 1,583.40 | 4.72 | 8.47 | 3.25 | 5.22 | 21.0% | 32.97 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,094 | 335.42 | 1.77 | 2.48 | 0.71 | 1.77 | 7.1% | 52.89 |
| Chems | 2,590 | 227.57 | 1.44 | 1.85 | 0.40 | 1.45 | 5.8% | 63.75 |
| Durbl | 2,843 | 245.81 | 2.39 | 2.91 | 0.45 | 2.46 | 9.9% | 100.13 |
| Enrgy | 6,194 | 278.68 | 3.95 | 4.57 | 0.52 | 4.05 | 16.3% | 145.38 |
| Hlth | 11,696 | 325.36 | 2.67 | 3.38 | 0.68 | 2.69 | 10.8% | 82.79 |
| Manuf | 12,034 | 304.84 | 1.76 | 2.55 | 0.63 | 1.92 | 7.7% | 62.91 |
| Money | 17,583 | 299.07 | 2.59 | 3.36 | 0.67 | 2.69 | 10.8% | 89.95 |
| NoDur | 5,081 | 274.80 | 1.38 | 1.87 | 0.52 | 1.35 | 5.4% | 49.05 |
| Other | 18,377 | 325.08 | 2.31 | 3.13 | 0.72 | 2.41 | 9.7% | 74.15 |
| Shops | 11,984 | 317.62 | 3.13 | 3.82 | 0.65 | 3.17 | 12.7% | 99.77 |
| Telcm | 3,981 | 270.57 | 0.18 | 0.75 | 0.49 | 0.26 | 1.0% | 9.46 |
| Utils | 4,672 | 213.45 | 0.48 | 1.06 | 0.39 | 0.67 | 2.7% | 31.19 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,523 | 1,094.31 | 4.11 | 7.37 | 1.98 | 5.39 | 21.7% | 49.25 |
| low vol | 40,091 | 1,165.68 | 10.19 | 12.82 | 2.56 | 10.26 | 41.2% | 87.97 |
| mid vol | 37,903 | 1,105.61 | 9.59 | 11.28 | 2.19 | 9.09 | 36.5% | 82.20 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 6.49% | 0.00% |
| 0.5% | 1.8 | 6.20% | 0.29% |
| 1.0% | 3.5 | 5.90% | 0.59% |
| 2.0% | 7.0 | 5.24% | 1.25% |
| 5.0% | 17.5 | 2.61% | 3.88% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
