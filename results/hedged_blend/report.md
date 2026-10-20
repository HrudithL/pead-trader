# hedged_blend

_portfolio strategy | family: portfolio | status: validated | derived from `calm_weighted`_

## What this strategy does

Market-neutral inverse-vol equity book (0.8x) + corrected-quote Kelly options book (1.6x), on one NAV

**Why it should work.** The sleeves are nearly uncorrelated with each other and with the market, so a blend can diversify. After the fixes the options sleeve earns about nothing, so the blend is essentially its equity sleeve (see docs/STRATEGIES.md section 5). Weights (set before the fixes, not re-optimised) gave each sleeve equal stand-alone risk at ~5% portfolio volatility.

**Design notes** (from the strategy's source file).

PortfolioStrategy: a fixed-weight, daily-rebalanced blend of other registered strategies.

A sleeve weight is a multiple of fund NAV allocated to that strategy's own sizing: weight 1.0 runs the strategy exactly as backtested on the whole NAV, 0.8 runs it 20% smaller. Weights need not sum to one -- the sleeves are low-correlation books that each use only part of their capital (the options sleeve spends at most its premium budget, the equity sleeve is margin-financed long/short), so they can share one NAV. Returns compound daily: r_p = sum(w_i r_i), NAV_t = NAV_{t-1} (1 + r_p).

Sleeve results come from the strategy registry itself (re-run in-process, ~seconds), so the blend always reflects the current sleeve definitions. Days with no sleeve activity (options in 2011, where the source file is corrupt) contribute zero for that sleeve.

| parameter | value |
|---|---|
| w_calm_weighted | 0.8 |
| w_options_cap20 | 1.6 |

## Performance

| metric | hedged_blend | market (CRSP VW) |
|---|---|---|
| Annualized return | 4.90% | 9.94% |
| Annualized volatility | 4.46% | 19.35% |
| Sharpe (NAV-pct) | 1.09 | 0.59 |
| Sortino | 1.61 |  |
| Max drawdown | -21.8% | -55.5% |
| Calmar | 0.22 |  |
| Final NAV ($10M start) | $25,382,788 |  |
| Worst day | -2.93% |  |
| % positive days | 50.8% |  |
| Sharpe 1996-2001 | 1.82 |  |
| Sharpe 2002-2007 | 1.31 |  |
| Sharpe 2008-2013 | 0.25 |  |

Correlation of daily returns with the market: **0.08**.

- best Sharpe among portfolio: `calm_blend` (1.28); this one: 1.09
- best return among portfolio: `calm_blend` (0.058); this one: 0.049
- best Calmar among portfolio: `calm_blend` (0.288); this one: 0.225

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
| calm_weighted | 0.80 | 14.28 | 92.8% |
| options_cap20 | 1.60 | 1.10 | 7.2% |

- Correlation between the sleeves' daily returns: **-0.06**.
- Blend market beta 0.018, correlation to the market 0.08.

Return, volatility and Sharpe of the blend are in the Performance table above; the cross-strategy report (`python -m pead compare`) shows the full weight frontier, the walk-forward (out-of-sample) weights and the volatility-target table.
