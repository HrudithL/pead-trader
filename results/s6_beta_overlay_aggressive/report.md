# s6_beta_overlay_aggressive

_equity strategy | family: equity-aggressive | status: research | derived from `s6_neutral_levered_aggressive`_

## What this strategy does

Strategy 6 Aggressive alpha book + the same 0.5x market overlay

**Why it should work.** Market-neutral earnings drift with enough gross exposure to matter; the neutrality is the hedge.

**Design notes** (from the strategy's source file).

Aggressive-sizing variants of the hand-built equity strategies.

Same signal, trim, tilt and neutralization as each base strategy; only the two dials that set how big and how levered the book is change (base_unit_fraction, gross-leverage cap = 6x). The multiplier for each was picked by sweeping 1.5x..12x the original base unit and taking the highest return subject to Sharpe >= 0.6 and max drawdown >= -40% (a worse drawdown is a fund-ending event, not a risk preference). The untouched 5%-of-ADV liquidity cap remains the real capacity constraint, and bites roughly four times as often at this sizing.

These are generated from the base strategy classes, so a change to a base strategy's weights flows through automatically.

| parameter | value |
|---|---|
| base_unit_fraction | 0.012 |
| max_gross_leverage | 6.0 |
| liquidity_cap_frac | 0.05 |
| return_basis | mktadj |
| cap_mode | priority |
| beta_overlay | 0.5 |

## Performance

| metric | s6_beta_overlay_aggressive | market (CRSP VW) |
|---|---|---|
| Annualized return | 15.01% | 9.94% |
| Annualized volatility | 13.70% | 19.35% |
| Sharpe (NAV-pct) | 1.09 | 0.59 |
| Sortino | 1.60 |  |
| Max drawdown | -37.0% | -55.5% |
| Calmar | 0.41 |  |
| Final NAV ($10M start) | $152,255,386 |  |
| Worst day | -5.91% |  |
| % positive days | 55.1% |  |
| Sharpe 1996-2001 | 1.39 |  |
| Sharpe 2002-2007 | 1.73 |  |
| Sharpe 2008-2013 | 0.21 |  |

Correlation of daily returns with the market: **0.68**.

- best Sharpe among equity: `s10_vol_rank_tilt` (1.81); this one: 1.09
- best return among equity: this strategy; this one: 0.15
- best Calmar among equity: `s10_vol_rank_tilt` (0.614); this one: 0.406

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
| P&L ($M, before costs) | 185.98 | -67.62 |
| P&L after costs ($M) | 168.87 | -85.60 |
| of which common (average-event) component ($M) | 174.40 | -171.82 |
| of which excess / PEAD component ($M) | 11.58 | 104.20 |
| excess minus costs ($M) | -5.54 | 86.22 |
| gross return per $ traded (bp) | 166.77 | -59.88 |
| positions in the money (hit rate) | 0.56 | 0.49 |
| stand-alone Sharpe (leg only) | 1.06 | -0.25 |

Daily P&L correlation between the two legs: **-0.77**.

- **The short leg loses money in absolute terms** (-67.6M before costs) while the long leg earns +186.0M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 174.4M / 171.8M. Stripping that common component, the surprise signal itself earned +11.6M on the long side and +104.2M on the short side.
- The two legs' daily P&L are -0.77 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 1.06 long / -0.25 short vs 1.09 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 7,718 | 1,052.78 | 11.28 | -4.89 | 1.67 | -6.56 | -8.1% | -62.28 |
| long | 8 | 16,225 | 2,721.85 | 42.43 | 2.92 | 4.23 | -1.31 | -1.6% | -4.80 |
| long | 9 | 17,480 | 3,520.62 | 43.56 | -3.12 | 5.32 | -8.44 | -10.5% | -23.97 |
| long | 10 | 16,939 | 3,857.22 | 71.60 | 16.66 | 5.90 | 10.76 | 13.3% | 27.91 |
| short | 1 | 16,755 | 3,662.36 | -27.10 | 28.88 | 5.92 | 22.95 | 28.5% | 62.68 |
| short | 2 | 16,916 | 3,529.55 | -24.03 | 38.62 | 5.54 | 33.09 | 41.0% | 93.74 |
| short | 3 | 15,579 | 2,858.54 | -23.05 | 26.72 | 4.57 | 22.15 | 27.4% | 77.48 |
| short | 4 | 9,015 | 1,241.23 | -11.41 | 9.98 | 1.95 | 8.04 | 10.0% | 64.74 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 12,943 | 580.43 | 4.74 | 8.64 | 1.91 | 6.73 | 8.3% | 115.97 |
| 2 | 22,307 | 2,425.20 | 15.93 | 23.56 | 5.94 | 17.62 | 21.8% | 72.64 |
| 3 | 25,252 | 4,605.79 | 25.85 | 33.11 | 8.66 | 24.46 | 30.3% | 53.10 |
| 4 | 27,425 | 6,685.72 | 17.20 | 24.57 | 9.76 | 14.81 | 18.4% | 22.15 |
| 5 | 28,700 | 8,147.02 | 19.55 | 25.91 | 8.84 | 17.07 | 21.2% | 20.95 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 11,934 | 272.18 | 5.63 | 7.59 | 1.36 | 6.23 | 7.7% | 228.86 |
| 2 | 22,802 | 1,937.75 | 12.15 | 20.54 | 5.81 | 14.73 | 18.3% | 76.01 |
| 3 | 25,740 | 4,782.62 | 26.45 | 37.71 | 9.56 | 28.14 | 34.9% | 58.84 |
| 4 | 27,494 | 7,253.58 | 21.14 | 28.42 | 10.15 | 18.26 | 22.6% | 25.18 |
| 5 | 28,652 | 8,196.33 | 17.81 | 21.40 | 8.20 | 13.21 | 16.4% | 16.11 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,165 | 9,600.44 | -14.15 | 34.77 | 14.06 | 20.71 | 25.7% | 21.57 |
| 41-60d | 25,072 | 4,938.28 | 27.47 | 18.98 | 7.84 | 11.14 | 13.8% | 22.57 |
| 61-90d | 34,592 | 6,224.84 | 23.02 | 26.13 | 10.15 | 15.98 | 19.8% | 25.66 |
| >90d | 11,798 | 1,680.60 | 46.93 | 35.90 | 3.04 | 32.86 | 40.7% | 195.50 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 32,056 | 3,008.84 | 26.65 | 30.54 | 4.72 | 25.82 | 32.0% | 85.81 |
| 2002-07 | 39,668 | 7,889.54 | 61.23 | 69.57 | 12.19 | 57.38 | 71.1% | 72.73 |
| 2008-13 | 44,903 | 11,545.77 | -4.60 | 15.67 | 18.18 | -2.51 | -3.1% | -2.18 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 21,429 | 2,583.01 | 6.03 | 8.80 | 4.33 | 4.47 | 5.5% | 17.32 |
| Chems | 2,571 | 1,235.93 | 7.33 | 9.13 | 1.67 | 7.47 | 9.3% | 60.42 |
| Durbl | 2,800 | 1,290.38 | 2.69 | 4.60 | 1.78 | 2.82 | 3.5% | 21.88 |
| Enrgy | 6,106 | 1,767.37 | 16.94 | 20.24 | 2.50 | 17.74 | 22.0% | 100.38 |
| Hlth | 11,413 | 2,280.39 | 12.88 | 14.74 | 3.68 | 11.06 | 13.7% | 48.52 |
| Manuf | 11,825 | 2,139.00 | 5.43 | 8.82 | 3.40 | 5.42 | 6.7% | 25.33 |
| Money | 17,141 | 2,149.44 | 11.87 | 16.47 | 3.75 | 12.72 | 15.8% | 59.20 |
| NoDur | 5,000 | 1,595.65 | 5.49 | 6.52 | 2.31 | 4.21 | 5.2% | 26.40 |
| Other | 18,022 | 2,447.24 | 8.16 | 12.96 | 4.30 | 8.66 | 10.7% | 35.37 |
| Shops | 11,767 | 2,235.81 | 13.10 | 15.47 | 3.56 | 11.91 | 14.8% | 53.27 |
| Telcm | 3,932 | 1,386.74 | -7.51 | -4.96 | 1.87 | -6.83 | -8.5% | -49.22 |
| Utils | 4,621 | 1,333.20 | 0.86 | 2.98 | 1.96 | 1.02 | 1.3% | 7.64 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 38,814 | 8,085.79 | 2.77 | 19.12 | 11.77 | 7.36 | 9.1% | 9.10 |
| low vol | 39,075 | 6,848.95 | 38.30 | 46.85 | 11.52 | 35.33 | 43.8% | 51.58 |
| mid vol | 37,173 | 7,280.81 | 41.91 | 49.60 | 11.41 | 38.19 | 47.3% | 52.46 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 15.01% | 0.00% |
| 0.5% | 10.7 | 14.57% | 0.43% |
| 1.0% | 21.5 | 14.11% | 0.89% |
| 2.0% | 43.0 | 13.06% | 1.94% |
| 5.0% | 107.4 | 8.01% | 6.99% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
