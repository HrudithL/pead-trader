# Evidence audit for the 20% return objective

Date: 2026-10-26 (America/Chicago). This is a research diagnostic, not a new strategy backtest. No strategy, frozen experiment, original result or PR has been changed. The requested 20%+ net annualized return has not been achieved.

## Objective and comparison basis

The user wants materially higher growth without restoring the old strategy's large drawdowns and inconsistency, and prefers simple economic improvements before additional information or complexity. Interpret 20% as compounded annual growth of total account NAV, after execution and financing costs, before taxes; not return on option premium or margin alone. Pending clarification, the proposed risk criteria retain PR #22's excess-RF Sharpe >=1 and maximum drawdown <=20%. These risk limits are proposed, not newly confirmed user instructions.

## What the open PRs actually establish

GitHub was queried directly. The latest open PR is [#22](https://github.com/HrudithL/pead-trader/pull/22), head `98dcb8ba9943ba1b7ee5f65388e9cbcfe439ff52`, stacked on [#19](https://github.com/HrudithL/pead-trader/pull/19), not on current main. PRs [#20](https://github.com/HrudithL/pead-trader/pull/20) and [#21](https://github.com/HrudithL/pead-trader/pull/21) preserve the directional-options study and next-session comparison. They do not establish another high-return investment-ready strategy.

PR #22 contains funded carry, paid-protection and equity-hedge experiments. Its stated 196 governing account/scenario observations share historical data. The saved comparison file contains 202 observations including six earlier completed implementation observations. There are **zero rows with CAGR >=20%**, even before imposing the Sharpe and drawdown criteria. This is evidence about this tested candidate set, not an impossibility theorem about future strategies.

Matched development results, $1M, 1999-01-04 through 2010-12-31:

| Account | CAGR | Excess-RF Sharpe | Maximum drawdown |
|---|---:|---:|---:|
| Funded equity parent | 10.37% | 0.674 | -28.67% |
| Equity with direct SPY beta hedge | 7.29% | 0.897 | -6.93% |
| Risk20 SPX credit carry | 5.68% | 0.444 | -10.47% |
| Carry with paid protection | 1.40% | -0.678 | -3.92% |

The older #19 deployed options book returned 13.38% at $1M, with a 51.17% drawdown. At $100K it returned 16.11%, with a 46.94% drawdown. Those are not equivalent-risk alternatives to the newer constrained accounts. Its short 2012-2013 segment exceeded 20% CAGR but retained approximately 46-47% drawdowns; that is already consumed evidence and does not satisfy the consistency objective.

The funded equity parent is deliberately different from canonical `steady_drift_plus`: next-session timing, issuer limits, explicit holdings/cash and conservative missing-price reserves. The older canonical 12.07% result uses a different implementation, dates and cash convention. It cannot be compared as if those changes were free.

## Why the return gap is not just insufficient size

I independently read the saved daily accounts using Python 3.12.10 / pandas 3.1.0. As an intentionally optimistic diagnostic, for each day define `scaled_return = RF + exposure * (saved_return - RF)`. Search exposure from 0.05 through 10.00 in steps of 0.05 and report the first value reaching 20% CAGR. CAGR uses 252 sessions/year; drawdown includes initial wealth of one; Sharpe uses daily excess returns divided by total-return volatility.

| Saved parent | First exposure reaching 20% | Diagnostic CAGR | Diagnostic drawdown | Excess-RF Sharpe |
|---|---:|---:|---:|---:|
| Funded equity | 2.55x | 20.07% | -63.01% | 0.674 |
| Beta-hedged equity | 3.90x | 20.01% | -28.20% | 0.897 |
| Risk20 carry | None through 10x | — | — | — |

**These are not executable portfolios or new measured strategy returns.** The exposure is selected after seeing the complete return path. The calculation assumes daily resizing, RF financing, unchanged existing cost behavior and unchanged underlying trade selection. It omits incremental financing spreads, fees, collateral, capacity, integer lots and liquidation effects. It demonstrates why simple scaling is not presently supported; it must never be registered or promoted as a 20% strategy. Lack of a crossing for this particular carry path through 10x is not a universal upper bound for different policies.

## Consistency is a separate problem

The following are subperiod diagnostics from the same running $1M accounts, not independently cash-started simulations. Subperiod returns do not erase holdings or selection history from earlier years.

| Account | Period | CAGR | Maximum drawdown within period | Excess-RF Sharpe |
|---|---|---:|---:|---:|
| Funded equity | 1999-2004 | 13.89% | -18.17% | 1.007 |
| Funded equity | 2005-2007 | 10.62% | -8.57% | 0.628 |
| Funded equity | 2008-2010 | 3.43% | -25.92% | 0.260 |
| Beta-hedged equity | 1999-2004 | 11.17% | -6.93% | 1.436 |
| Beta-hedged equity | 2005-2007 | 2.50% | -3.90% | -0.373 |
| Beta-hedged equity | 2008-2010 | 4.61% | -3.98% | 0.819 |
| Risk20 carry | 1999-2004 | 6.97% | -8.79% | 0.588 |
| Risk20 carry | 2005-2007 | 7.49% | -10.47% | 0.396 |
| Risk20 carry | 2008-2010 | 1.45% | -6.91% | 0.187 |

A hedge reduces certain exposures; it does not automatically supply additional expected return. Paid options impose premium and execution costs. [Cboe's benchmark explanation](https://www.cboe.com/insights/posts/benchmark-indices-series-hedging-downside-exposure-with-pput-cll-and-cllz-indices) documents the protection-cost and collar-upside tradeoffs. That external rationale does not validate any repo-specific candidate.

## Feasibility findings before another experiment

- The prior-IV/RV screen already failed in #22. A proposed expected-payoff screen would need a different mechanism: compare actual quoted credit with forecast contractual loss and costs, rather than assuming expensive implied volatility is sufficient.
- The cached native SPX spot history begins in 1998. Direct read-only checks of the licensed annual source confirm SPX closes also exist in 1996 and 1997. Those earlier observations could improve warmup; no source rows were copied or changed.
- Daily overlapping 45-session outcomes are not independent trials. The existing 1998-start cache has only about 22 disjoint 45-session windows by January 2002, 39 by January 2005, and 67 by January 2010. These are illustrative calendar counts, not effective sample-size estimates. Earlier native years help but do not supply many independent crashes.
- A forecast of terminal SET loss must respect opening settlement rather than silently use close-to-close returns. Queries of 1996/1997 annual source files found SPX rows but no SET SECID 118963 rows; these particular queries do not establish whether another local source contains earlier SET history. Any close-based approximation needs explicit labeling and a separate settlement stress.
- Available annual native files extend through 2013. The CRSP benchmark cache extends through June 2014; a benchmark extension alone does not provide a new complete stock/options strategy holdout. The already studied 2012-2013 period cannot become untouched again.
- Three years of warmup is a proposed minimum, not evidence of statistically adequate tail-loss estimation. Uncertain or unavailable estimates should exclude an order rather than manufacture a positive edge.

## Reconciliation and source fingerprints

Read-only sources are the preserved full-ledger checkout under `.claude/worktrees/options-risk-synthesis/` and the published checkout under `.claude/worktrees/options-risk-synthesis-publication/`. The aggregate comparison hash matches the published manifest. The local worker was invoked sequentially under the 16k context budget; its first exploratory draft incorrectly imported 126/60-session beta-estimation windows into a terminal-loss forecasting problem. That suggestion was rejected. A second draft explored unchanged equity and carry inside one shared-cash ledger, but confused risk-budget fractions with a carry rate and supplied no meaningful capital analysis. Those suggestions were also rejected. No worker code was applied.

Planner assessment of that second alternative: investing idle equity cash in carry may improve capital use, but stock admission and contractual option-width collateral compete for the same dollars. Both sleeves can lose together. Adding standalone reported CAGRs or blending their saved NAVs cannot establish the return of the combined funded account. There is currently no evidence that this simpler allocation change supplies the missing return at acceptable risk, so it is not the recommended first experiment.

SHA-256, relative to the preserved full-ledger `results/options_risk_synthesis/`:

| Input | SHA-256 |
|---|---|
| `comparison.csv` | `2b58d94b4608f5ed19d4f439e5c6abf28fa49a0be87ac0f6a6d98e6c09fd64d5` |
| `linear_v2_development_equity_suite/equity_funded_1000000/daily.csv.gz` | `d90a61ee4df1b36471f553d18badf1371c7fb60fba8574e0f05dd765dd1c747e` |
| `linear_v2_development_equity_suite/equity_beta_neutral_1000000/daily.csv.gz` | `866c4320c6a3f78fb9d03983802e97281b1555c226fa27d41e5d3e95238071dd` |
| `pricing_v1_development_options_suite/carry_risk20_1000000/daily.csv.gz` | `c968b159796b5a44e4d1979ed595faf24439570839c2c68c2c87404022660d41` |

No new implementation has been started. Approval of the specific experiment and clarification of risk/borrowing preferences are pending in this chat. No candidate has been marked investment-accepted or live-ready.
