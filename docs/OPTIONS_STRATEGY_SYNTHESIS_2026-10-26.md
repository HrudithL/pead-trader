# Options and equity strategy evidence audit — 2026-10-26

## Status and objective

This is a new independent audit of the open PRs, source code, preserved worktrees and saved daily ledgers. It is not a new backtest or a claim that a superior strategy has been built. The proposed implementation is awaiting the design approval required by `superpowers/brainstorming/SKILL.md`. No original worktree, frozen source, dataset or published result has been changed.

User objective: find an options-only strategy with materially better growth, drawdown and Sharpe, then investigate whether the strongest equity strategy benefits from options hedging or funded leverage. Quality takes priority over producing a predetermined number of strategies. A result that fails the research gates must remain a rejected experiment.

Research targets proposed for approval: NAV CAGR at least 12%, excess-risk-free Sharpe at least 1, maximum drawdown no worse than 20%. These are targets, not measured outcomes or promises. For equity modifications, also require evidence of improvement relative to the existing equity parent; meeting an absolute return hurdle is insufficient.

## PR and worktree reconciliation

GitHub was queried directly on 2026-10-26. All three open PRs are drafts; none was merged or altered.

| PR / local branch | Actual contribution | Interpretation |
|---|---|---|
| [#19](https://github.com/HrudithL/pead-trader/pull/19), `codex/selective-earnings-research` | Independent cash/collateral-funded options engine; many failed trials; earnings-response SPX put-credit ladder; actual SET settlement; staged liquidity-constrained exits; consumed 2012–2013 holdout | The high-return study. It is principally conditional market/short-volatility exposure, not the same trade as buying calls on individual SUE announcements. |
| [#20](https://github.com/HrudithL/pead-trader/pull/20), `codex/pead-options-optimization` | Preserves 54 aggregate artifacts from the corrected options study whose implementation already merged in #18 | Artifact completion, not independent new alpha. |
| [#21](https://github.com/HrudithL/pead-trader/pull/21), `codex/pead-options-next-study` | Next-session entries, duplicate-event exclusions, matched stock/call/debit-spread controls, low-volatility conditioning and IBES feasibility | The safer, low-return comparison. GitHub reports merge conflicts; integrate deliberately rather than automatically merging overlapping #18 code. |
| #18, merged at `cd18c188` | Corrected 54-cell call/put grid and quote-level sizing stresses | The root checkout is still at `1dc8795` (#17); its `origin/main` already records #18. Do not infer that root code is current GitHub main. |
| #17 / `claude/strategy-evaluation-roi-74c7dd` | Steady Drift family, equity engine port and prior research | Already merged. No new strategy PR is missing for this worktree. |
| `docs/repomap-final-2` / strategy-analysis worktree | Older documentation and corrected position artifacts | Historical work already represented by merged PRs. Preserve its ignored data. |
| `claude/agents-md-project-notes-b44691` | Same commit as root; clean tracked status | No unique tracked strategy changes requiring another strategy PR. |

Both external Codex options worktrees and all other registered worktrees had clean tracked status. Root has pre-existing untracked `AGENTS.md`, `CLAUDE.md` and `RESEARCH.md`; preserve them. AGENTS requires sequential local-worker calls and planner review. No worktree was removed, pruned or cleaned.

## Economic differences and existing results

### SPX short-volatility ladder (#19)

The existing high-return policy sells roughly 35-delta puts and buys 15-delta puts, targeting 45 DTE within 35–75 DTE. Weekly entries require positive lagged market trend and earnings-response breadth, with lagged realized volatility at most 30%. It can admit 30% of NAV maximum loss per ticket/cohort and 80% aggregate. Cash secures full contractual spread width; this avoids account borrowing but does not eliminate economic leverage. Several different strike tickets on the same index and expiry are highly related exposures.

Remaining positions settle to actual native SET. Risk exits are staged at executable leg quotes and constrained by shared prior-volume/open-interest capacity and free cash. A stop is therefore an instruction to attempt liquidation, not guaranteed protection at its trigger.

Development 1999–2010: $100K CAGR 16.11%, drawdown −46.94%, excess-RF Sharpe 0.526; $1M CAGR 13.38%, drawdown −51.17%, Sharpe 0.478. The $1M moderate policy already reduces risk: CAGR 8.93%, drawdown −23.16%, Sharpe 0.422. That is direct evidence of a return/risk tradeoff, not evidence that changing size creates a better signal.

The frozen 2012–2013 test earned 38.24% / 34.76% CAGR, but drawdowns were −45.73% / −47.06%. SPY returned 19.61% with −9.69% drawdown and 1.520 Sharpe; candidate Sharpe was 0.86 / 0.81 and annual estimated market alpha was −11.86% / −15.12%. This holdout is consumed. It must not be relabeled untouched after another design pass.

### Directional single-name calls (#18/#20/#21)

The balanced historical call rule targets 75 DTE and 0.70 delta, holds 40 sessions, and buys after positive SUE events. The same-day version returned 3.97% with −25.58% drawdown and 0.204 excess-RF Sharpe. Conservative next-session entry returned 3.29%, −27.25%, 0.141. It is more credible for information timing but less profitable.

On the matched common cohort, long calls returned 3.27%, drawdown −20.36%, Sharpe 0.153; debit call spreads returned 1.43%, −18.65%, Sharpe −0.123. The matched stock control at 10bp per side returned 2.95%, −6.39%, Sharpe 0.278. Thus a defined-risk spread did not automatically improve economic quality.

The lowest trailing-realized-volatility call subset returned 3.43%, −7.49%, Sharpe 0.280 over its documented full window. Four-factor annual alpha was +0.04%, with HAC interval [−1.30%, +1.38%]. Bucket cutoffs use prior-quarter information; choosing to favor that bucket after observing results remains selection bias. Calls are long delta and therefore do not constitute a downside hedge for bullish SPX carry or long equities.

The 120-DTE/0.50-delta/60-session call cell returned 4.21% at a 20% premium cap; allowing 100% premium raised CAGR only to 6.51% and deepened drawdown to −52.40%. It does not support levering the same signal toward an asserted 15–20% return.

### Equity parent

`steady_drift_plus`: causal top-decile SUE, inverse-idiosyncratic-variance sizing, 60-session holds, smaller/more numerous positions, cash collateral, 80% cost-basis admission cap and a 12% volatility throttle. Published full-window CAGR 12.07%, drawdown −22.07%, zero-RF Sharpe 1.193. The full window includes pre-trading cash months, and cash accrual uses annual rates typed from memory. Both must be disclosed or corrected before comparing a new funded strategy.

Earlier placebo work attributes only about four annual percentage points to surprise skill; much of the return is the announcing-stock universe. An index hedge can reduce market beta but cannot guarantee retention of the small/midcap, sector, liquidity and low-volatility premia. A short broad index is not a perfect hedge for this stock basket.

## Independently recomputed common-period metrics

Source: saved daily NAV/return files, joined by exact dates. Common period 1999-01-04 through 2010-12-31, 3,019 sessions. CAGR uses 252 sessions/year; Sharpe subtracts the same French daily RF series carried in the #19 ledger. Equity cash accrual itself is not retroactively changed by this normalization. SPY comparator is CRSP PERMNO 84398 total return. Capital/capacity differs: SPX account $1M, stock and call accounts $10M.

| Saved sleeve or diagnostic | CAGR | Annual vol | Excess-RF Sharpe | Max drawdown |
|---|---:|---:|---:|---:|
| SPX deployed, $1M | 13.382% | 31.435% | 0.478 | −51.167% |
| SPX moderate, $1M | 8.926% | 17.542% | 0.422 | −23.156% |
| Steady Drift Plus | 12.321% | 10.968% | 0.869 | −22.066% |
| Low-volatility matched call subset | 3.958% | 4.434% | 0.292 | −7.492% |
| SPY | 1.892% | 21.729% | 0.071 | −55.201% |
| 50% deployed SPX / 50% low-vol calls, diagnostic | 9.943% | 16.404% | 0.497 | −27.590% |
| 80% equity / 20% deployed SPX, diagnostic | 13.364% | 12.430% | 0.855 | −24.174% |
| 80% equity / 20% low-vol calls, diagnostic | 10.670% | 9.315% | 0.847 | −18.864% |

The three blends are arithmetic daily return-series experiments, not executable account simulations. They assume daily capital reallocation and omit the additional transfers, turnover, lot rounding, changed trade selection and shared-cash constraints. Do not register them as new strategies or quote their returns as implementable.

Daily correlation: SPX/calls 0.246; SPX/equity 0.344; equity/calls 0.579. On the worst 5% SPY sessions, mean daily returns were SPX −1.245%, equity −1.212%, calls −0.230%, SPY −3.156%. Lower correlation is not evidence that one sleeve pays when the others lose.

Paired circular three-month bootstrap, 1,000 repetitions, fixed seed 20261026, comparing the 80/20 equity/SPX diagnostic with equity alone:

| Difference (blend minus equity) | 2.5th percentile | Median | 97.5th percentile |
|---|---:|---:|---:|
| CAGR | −1.470 percentage points | +1.072 points | +3.436 points |
| Sharpe | −0.238 | −0.016 | +0.186 |
| Signed max drawdown | −6.765 percentage points | +1.474 points | +5.900 points |

A positive drawdown difference means a shallower decline. These intervals condition on the already selected sleeves and do not adjust for all previous research searches. They do not establish simultaneous improvement in return, Sharpe and drawdown.

On identical 418 sessions in January 2012–August 30, 2013: equity CAGR 14.75%, Sharpe 1.554, drawdown −6.46%; low-volatility calls 2.27%, 0.546, −5.42%; SPX $1M 34.76%, 0.810, −47.06%; SPY 19.61%, 1.520, −9.69%. The higher SPX return does not dominate the equity parent on risk.

## Drawdown and accounting diagnosis

The $1M deployed book's development peak was 2006-06-30; trough 2007-03-05. It lost 34.66% on 2007-02-27 and 33.25% on 2007-03-13. The $100K book lost 32.37% on February 27. Admission-based original max loss/current NAV peaked at 156.45% ($1M) and 144.46% ($100K) on March 5. This is the original risk measure divided by current NAV, not an estimate of remaining loss; it illustrates why an admission fraction cannot be advertised as a continuous current-risk cap.

A read-only reconstruction of February 27 used the $1M completed trade records to reconstruct the affected spread quantities and the exact date's cached quotes. There were 51 trade records, 1,048 spread lots, 37 unique quoted legs, no missing quotes, and no quoted closing liability beyond contractual terminal width. Thus this day's loss cannot be dismissed as missing-quote writeoffs or an above-width marking artifact. This is a focused diagnostic, not a full independent audit of every valuation.

Free cash remained positive in the saved development and holdout accounts. Development cash interest was $80,861 at $100K and $686,643 at $1M. Holdout cash interest was zero under its rounded/lagged proxy and haircut; the holdout return should not be described as significantly aided by Treasury carry.

## Candidate research design proposed for approval

1. **Risk-budgeted SPX carry:** retain the existing lagged signal and 35/15 delta contract economics, reduce ticket/weekly cohort admission maximum loss to 5% NAV and aggregate to 20%, add an expiry concentration constraint, and test a lagged volatility throttle. Measure both initial loss budget and remaining stress loss/current NAV. Capacity-constrained risk reduction may breach a requested target temporarily; report breaches and deferred orders rather than asserting an impossible hard continuous cap. Compare admission-only and actual staged reduction as registered ablations.

2. **SPX carry with funded downside protection:** use candidate 1's smaller risk budget plus a separate long 25-delta / short 10-delta SPX put spread, 60–120 DTE, rolled monthly, selected with prior-session Greeks and paid at actual ask/bid. Start with a predeclared 1% NAV annual premium budget and report whole-lot feasibility at $100K and $1M. Carry admission requires its specified hedge to be fundable; inability to buy protection must reduce/stop new carry rather than assume a hedge. A debit put spread has capped payout and can cease providing incremental protection below its short strike. Test against lower carry exposure with cash at comparable risk, not just against the aggressive parent.

3. **Steady Drift with a beta-sized index hedge:** retain stock signal/holding rules, estimate beta solely from trailing returns, and size the paid index put-spread hedge against that beta, with explicit budget/capacity constraints. Compare against unchanged stocks and reduced stock exposure plus cash. Separately run a modest 1.2x equity financing control with disclosed funding spread, risk limits and liquidity costs. Neither hedge nor leverage is presumed to improve Sharpe or alpha. Index basis risk and option premium drag can make both fail.

Exact risk/hedge parameters must be frozen in the approved implementation protocol before new candidate runs. They are risk preferences and hypotheses, not fitted winners. The local worker's exploratory output is scratch material: its second response incorrectly imported the old 30/80% allocation and a bullish-call sleeve, and those suggestions were rejected by the planner. The third response also confused portfolio beta with an unweighted sum of stock betas, mixed bid/ask and capacity units, and proposed tests that assumed the desired result. Those suggestions were rejected. Hedge beta must come from a properly weighted portfolio or its trailing return regression; financing and hedge carry must be measured, not assumed favorable. No worker code or claimed result has been applied.

Whole-lot feasibility is an early stopping gate: a 1% annual budget is only $1,000 at $100K NAV, or about $83/month if allocated evenly. An SPX hedge may be unaffordable at that size. Freeze budget timing and required hedge coverage before running the candidate; do not quietly increase the budget, substitute instruments lacking historical data, or promote a no-trade/cash-only account as a successful options strategy.

## Input fingerprints for the independent metric check

Audit runtime: the existing selective-earnings Python 3.12 environment, pandas 3.1.0. Saved equity summaries were originally produced under pandas 3.0.5; this audit reads saved returns rather than rebuilding event ranks. Baseline SPX CAGR, Sharpe and drawdown reproduced the saved summaries.

SHA-256 of the principal saved return inputs:

| Input | SHA-256 |
|---|---|
| Root `results/steady_drift_plus/daily.csv.gz` | `7AB7AD880D7DEC076A74FF819BAF66F4E29F8A8174565C0ED8414C35F5CF5031` |
| #21 `results/options_structure_comparison_2026-10-24/daily_nav_long_call_common_low_tvol.csv` | `3595C4FE289F84D524E804DCF900EFF8D2D0E65B8272B02E875BA0A9477FE8F9` |
| #19 `results/selective_earnings_index_settlement_risk_exit/deployed_1000k/daily.csv.gz` | `29BC0A4E23D8233F866F8684BC236251BDEE9A0F7C1BE96E36A0B8468BABD950` |
| #19 `results/selective_earnings_index_settlement_risk_exit/moderate_1000k/daily.csv.gz` | `6D53F5E07F222914D829B05AF1419B54024691393704523FF2AFC0629C9576AA` |

## Validation and integration requirements

- Preserve #19's frozen hashes and holdout outputs; use a new branch and output namespace. Base work on current remote main with explicitly reviewed dependencies, rather than silently overwriting older root code or merging draft PRs remotely.
- Unify NAV calendar, cash rate, excess-RF Sharpe and comparable benchmark dates. Label 2011's absent option history and partial 2013; do not infer trades across a source gap. Re-run equity cash accounting from the ledger when replacing its cash series, not by subtracting a constant from reported CAGR.
- Simulate actual contracts, executable bid/ask, fees, entry/exit shared capacity, integer lots, collateral and cash funding. No scaling a saved NAV series into a claimed executed strategy. No contemporaneous/future Greeks, liquidity, earnings observations or immature training labels.
- Add focused adversarial tests for unchanged historical decisions when future data changes, overlapping cohort/expiry limits, hedge cash reservation, failed/partial hedge fills, staged reductions, shared option-leg usage, SET, missing quotes, source gaps and reconciliation. Reconcile independent cash flows to NAV and zero holdings at terminal resolution.
- Predeclare the small candidate set and record every failed variant. Use chronological purged folds for diagnostic stability and paired dependence-aware intervals for changes against each parent. Account for factor, nonlinear option and small-cap/universe exposures; market-only alpha is insufficient for short volatility.
- Run doubled fees, no one-lot capacity floor, additional bid/ask slippage, matched-risk stock/cash alternatives and adverse-regime checks. Report hedge premium, cash interest and option/stock P&L separately; include turnover, participation and account-size sensitivity.
- All currently available history has informed design. New runs are development evidence, including any revised 2012–2013 run. Positive bootstrap results on this archive cannot become a fresh holdout. A strong claim needs new, untouched data and ultimately timestamped forward execution evidence.
- Retain a candidate only if it has a credible economic source, improves the relevant parent after costs and passes the stated risk/uncertainty gates. If none qualifies, report that result and preserve the useful infrastructure without promoting a strategy.

External rationale checked against primary sources: [Cboe's put/collar comparison](https://www.cboe.com/insights/posts/benchmark-indices-series-hedging-downside-exposure-with-pput-cll-and-cllz-indices) documents the cost/upside tradeoff of option protection; [Israelov's protective-put research](https://www.aqr.com/Insights/Research/White-Papers/Pathetic-Protection-The-Elusive-Benefits-of-Protective-Puts) motivates comparison against simply reducing equity exposure. Neither source establishes that these proposed repo-specific candidates work.

## Work checklist

- [x] Read shared AGENTS instructions, REPO_MAP and FIXES.
- [x] Reconcile open PRs and registered worktrees.
- [x] Read strategy rules, accounting, validation code and existing results.
- [x] Independently recompute common-period risk metrics and diagnose a major loss day.
- [x] Reject unsubstantiated simple blending and leverage claims.
- [x] Explore distinct designs with sequential local-worker calls; review and reject incorrect draft suggestions.
- [ ] Obtain the required design approval.
- [ ] Freeze the implementation protocol in an isolated branch/worktree.
- [ ] Implement, test and run actual funded candidates and stresses.
- [ ] Review surviving evidence, document failures and create a reviewable PR.
