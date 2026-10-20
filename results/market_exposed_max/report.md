# market_exposed_max

_equity strategy | family: equity-aggressive | status: research | derived from `market_exposed`_

## What this strategy does

market_exposed with bigger bets for more return: base unit 0.006, 6x leverage cap

**Why it should work.** Let net exposure float with the signal; empirically this smuggles in uncontrolled market beta.

**Design notes** (from the strategy's source file).

Aggressive-sizing variants of the hand-built equity strategies.

Same signal, trim, tilt and neutralization as each base strategy; only the two dials that set how big and how levered the book is change (base_unit_fraction, gross-leverage cap = 6x). The multiplier for each was picked by sweeping 1.5x..12x the original base unit and taking the highest return subject to Sharpe >= 0.6 and max drawdown >= -40% (a worse drawdown is a fund-ending event, not a risk preference). The untouched 5%-of-ADV liquidity cap remains the real capacity constraint, and bites roughly four times as often at this sizing.

These are generated from the base strategy classes, so a change to a base strategy's weights flows through automatically.

| parameter | value |
|---|---|
| base_unit_fraction | 0.006 |
| max_gross_leverage | 6.0 |
| liquidity_cap_frac | 0.05 |
| return_basis | raw |
| cap_mode | priority |
| beta_overlay | 0.0 |

## Performance

| metric | market_exposed_max | market (CRSP VW) |
|---|---|---|
| Annualized return | 11.78% | 9.94% |
| Annualized volatility | 11.85% | 19.35% |
| Sharpe (NAV-pct) | 1.00 | 0.59 |
| Sortino | 1.45 |  |
| Max drawdown | -33.0% | -55.5% |
| Calmar | 0.36 |  |
| Final NAV ($10M start) | $87,451,313 |  |
| Worst day | -7.17% |  |
| % positive days | 51.9% |  |
| Sharpe 1996-2001 | 1.64 |  |
| Sharpe 2002-2007 | 1.65 |  |
| Sharpe 2008-2013 | 0.13 |  |

Correlation of daily returns with the market: **0.66**.

- best Sharpe among equity: `calm_rank_tilt` (1.81); this one: 0.999
- best return among equity: `market_boost_max` (0.15); this one: 0.118
- best Calmar among equity: `calm_rank_tilt` (0.614); this one: 0.357

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
| P&L ($M, before costs) | 222.73 | -124.99 |
| P&L after costs ($M) | 212.73 | -135.28 |
| of which common (average-event) component ($M) | 88.48 | -80.08 |
| of which excess / PEAD component ($M) | 20.72 | 48.19 |
| excess minus costs ($M) | 10.72 | 37.90 |
| gross return per $ traded (bp) | 370.11 | -222.37 |
| positions in the money (hit rate) | 0.63 | 0.42 |
| stand-alone Sharpe (leg only) | 0.73 | -0.46 |

Daily P&L correlation between the two legs: **-0.97**.

- **The short leg loses money in absolute terms** (-125.0M before costs) while the long leg earns +222.7M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by 88.5M / 80.1M. Stripping that common component, the surprise signal itself earned +20.7M on the long side and +48.2M on the short side.
- The two legs' daily P&L are -0.97 correlated: the short leg is the hedge that makes the long leg's variance disappear (stand-alone leg Sharpe 0.73 long / -0.46 short vs 1.00 combined).

### By decile and side (signal-driven part only)

| leg | decile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|---|
| long | 7 | 8,029 | 514.45 | 18.09 | -0.60 | 0.88 | -1.49 | -3.1% | -28.94 |
| long | 8 | 16,588 | 1,409.31 | 53.03 | 5.42 | 2.38 | 3.04 | 6.3% | 21.59 |
| long | 9 | 17,747 | 1,906.30 | 56.00 | 0.65 | 3.11 | -2.46 | -5.1% | -12.89 |
| long | 10 | 17,174 | 2,187.84 | 85.60 | 15.25 | 3.63 | 11.62 | 23.9% | 53.13 |
| short | 1 | 17,169 | 1,947.43 | -49.31 | 14.87 | 3.63 | 11.24 | 23.1% | 57.74 |
| short | 2 | 17,281 | 1,766.20 | -41.46 | 18.29 | 3.18 | 15.11 | 31.1% | 85.57 |
| short | 3 | 15,970 | 1,363.24 | -31.22 | 10.33 | 2.50 | 7.83 | 16.1% | 57.43 |
| short | 4 | 9,171 | 543.72 | -13.28 | 4.70 | 0.98 | 3.71 | 7.6% | 68.25 |

![D10 and the short deciles carry the alpha; the middle of each leg earns little](figures/decile.png)

*D10 and the short deciles carry the alpha; the middle of each leg earns little*

### By size quintile (1 = smallest)

| size_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 14,213 | 527.66 | 4.45 | 7.78 | 1.84 | 5.93 | 12.2% | 112.39 |
| 2 | 22,885 | 1,725.66 | 9.74 | 17.71 | 4.49 | 13.22 | 27.2% | 76.61 |
| 3 | 25,501 | 2,640.37 | 17.24 | 19.90 | 5.21 | 14.70 | 30.2% | 55.66 |
| 4 | 27,583 | 3,267.00 | 18.85 | 13.42 | 4.94 | 8.48 | 17.4% | 25.96 |
| 5 | 28,947 | 3,477.79 | 27.17 | 10.10 | 3.80 | 6.29 | 12.9% | 18.10 |

### By liquidity quintile (1 = least liquid)

| liquidity_quintile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1 | 13,650 | 300.19 | 6.42 | 8.06 | 1.50 | 6.56 | 13.5% | 218.65 |
| 2 | 23,084 | 1,613.35 | 9.98 | 17.20 | 4.84 | 12.36 | 25.4% | 76.61 |
| 3 | 25,851 | 2,851.47 | 15.67 | 24.23 | 5.70 | 18.53 | 38.1% | 64.97 |
| 4 | 27,665 | 3,422.28 | 16.70 | 10.57 | 4.79 | 5.78 | 11.9% | 16.90 |
| 5 | 28,874 | 3,450.23 | 28.63 | 8.78 | 3.45 | 5.33 | 11.0% | 15.43 |

### By holding-window length

| hold_bucket | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| <=40d | 45,855 | 4,666.77 | -12.92 | 16.61 | 7.52 | 9.09 | 18.7% | 19.48 |
| 41-60d | 25,652 | 2,632.78 | 22.99 | 11.89 | 4.64 | 7.25 | 14.9% | 27.52 |
| 61-90d | 35,324 | 3,344.89 | 17.09 | 18.76 | 6.09 | 12.67 | 26.1% | 37.88 |
| >90d | 12,298 | 994.04 | 50.29 | 21.65 | 2.04 | 19.61 | 40.3% | 197.29 |

### By era

| era | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| 1996-01 | 33,895 | 1,718.35 | 23.31 | 21.02 | 3.00 | 18.02 | 37.1% | 104.88 |
| 2002-07 | 39,956 | 4,116.20 | 43.81 | 38.63 | 7.05 | 31.59 | 65.0% | 76.74 |
| 2008-13 | 45,278 | 5,803.94 | 10.33 | 9.25 | 10.24 | -0.99 | -2.0% | -1.70 |

### By FF12 sector

| ff12_sector | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| BusEq | 22,094 | 1,238.86 | 20.07 | 5.99 | 2.33 | 3.67 | 7.5% | 29.58 |
| Chems | 2,590 | 701.65 | 6.49 | 6.27 | 1.05 | 5.22 | 10.7% | 74.43 |
| Durbl | 2,843 | 766.16 | -0.97 | 1.37 | 1.16 | 0.21 | 0.4% | 2.73 |
| Enrgy | 6,194 | 937.74 | 5.51 | 12.17 | 1.50 | 10.67 | 21.9% | 113.80 |
| Hlth | 11,696 | 1,162.56 | 14.23 | 7.15 | 2.13 | 5.02 | 10.3% | 43.16 |
| Manuf | 12,034 | 1,066.72 | 5.86 | 5.38 | 1.91 | 3.47 | 7.1% | 32.52 |
| Money | 17,583 | 1,062.83 | 6.91 | 9.58 | 2.08 | 7.50 | 15.4% | 70.58 |
| NoDur | 5,081 | 882.91 | 8.96 | 4.94 | 1.43 | 3.51 | 7.2% | 39.72 |
| Other | 18,377 | 1,189.85 | 8.11 | 8.34 | 2.35 | 6.00 | 12.3% | 50.40 |
| Shops | 11,984 | 1,123.54 | 9.69 | 7.24 | 2.00 | 5.24 | 10.8% | 46.62 |
| Telcm | 3,981 | 796.70 | -7.22 | -0.46 | 1.21 | -1.67 | -3.4% | -20.93 |
| Utils | 4,672 | 708.98 | -0.19 | 0.94 | 1.15 | -0.21 | -0.4% | -2.96 |

### By idiosyncratic-volatility tercile

| vol_tercile | positions | notional_M | net_pnl_M | excess_M | cost_M | alpha_net_M | alpha_share | alpha_bp_per_$ |
|---|---|---|---|---|---|---|---|---|
| high vol | 39,523 | 3,964.97 | 9.35 | 14.03 | 6.36 | 7.67 | 15.8% | 19.35 |
| low vol | 40,091 | 3,751.90 | 35.21 | 29.62 | 7.09 | 22.52 | 46.3% | 60.04 |
| mid vol | 37,903 | 3,786.09 | 32.34 | 25.14 | 6.57 | 18.57 | 38.2% | 49.05 |

### Short-borrow sensitivity (not modeled in the backtest)

| borrow_rate | fee_M | annualized_return | drag |
|---|---|---|---|
| 0.0% | 0.0 | 11.78% | -0.00% |
| 0.5% | 5.5 | 11.40% | 0.38% |
| 1.0% | 11.1 | 11.00% | 0.78% |
| 2.0% | 22.2 | 10.11% | 1.67% |
| 5.0% | 55.4 | 6.16% | 5.62% |

The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on the cost of running the short leg.
