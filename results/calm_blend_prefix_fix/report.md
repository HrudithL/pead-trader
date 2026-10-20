# calm_blend

_portfolio strategy | family: portfolio | status: research | derived from `hedged_blend`_

## What this strategy does

The same blend with the stronger research equity sleeve (calm_rank_tilt: Sharpe 1.8)

**Why it should work.** As hedged_blend, with the volatility-rank equity book as the alpha sleeve.

**Design notes** (from the strategy's source file).

PortfolioStrategy: a fixed-weight, daily-rebalanced blend of other registered strategies.

A sleeve weight is a multiple of fund NAV allocated to that strategy's own sizing: weight 1.0 runs the strategy exactly as backtested on the whole NAV, 0.8 runs it 20% smaller. Weights need not sum to one -- the sleeves are low-correlation books that each use only part of their capital (the options sleeve spends at most its premium budget, the equity sleeve is margin-financed long/short), so they can share one NAV. Returns compound daily: r_p = sum(w_i r_i), NAV_t = NAV_{t-1} (1 + r_p).

Sleeve results come from the strategy registry itself (re-run in-process, ~seconds), so the blend always reflects the current sleeve definitions. Days with no sleeve activity (options in 2011, where the source file is corrupt) contribute zero for that sleeve.

| parameter | value |
|---|---|
| w_calm_rank_tilt | 0.9 |
| w_options_cap20 | 1.6 |

## Performance

| metric | calm_blend | market (CRSP VW) |
|---|---|---|
| Annualized return | 12.43% | 9.94% |
| Annualized volatility | 4.73% | 19.35% |
| Sharpe (NAV-pct) | 2.50 | 0.59 |
| Sortino | 4.08 |  |
| Max drawdown | -15.5% | -55.5% |
| Calmar | 0.80 |  |
| Final NAV ($10M start) | $97,913,566 |  |
| Worst day | -2.04% |  |
| % positive days | 54.2% |  |
| Sharpe 1996-2001 | 2.92 |  |
| Sharpe 2002-2007 | 2.84 |  |
| Sharpe 2008-2013 | 2.10 |  |

Correlation of daily returns with the market: **0.12**.

- best Sharpe among portfolio: this strategy; this one: 2.5
- best return among portfolio: this strategy; this one: 0.124
- best Calmar among portfolio: this strategy; this one: 0.802

![NAV (log scale)](figures/nav.png)

*NAV (log scale)*

![Drawdown from running peak](figures/drawdown.png)

*Drawdown from running peak*

![Calendar-year returns](figures/yearly.png)

*Calendar-year returns*

![Is the edge stable, or concentrated in a few years?](figures/rolling_sharpe.png)

*Is the edge stable, or concentrated in a few years?*

## Where the return comes from

![Each sleeve's contribution to the blend's cumulative P&L](figures/sleeves.png)

*Each sleeve's contribution to the blend's cumulative P&L*

| sleeve | weight | pnl_M | share_of_pnl |
|---|---|---|---|
| calm_rank_tilt | 0.90 | 33.83 | 38.5% |
| options_cap20 | 1.60 | 54.09 | 61.5% |

- Correlation between the sleeves' daily returns: **-0.05**.
- Blend market beta 0.030, correlation to the market 0.12.

Return, volatility and Sharpe of the blend are in the Performance table above; the cross-strategy report (`python -m pead compare`) shows the full weight frontier, the walk-forward (out-of-sample) weights and the volatility-target table.
