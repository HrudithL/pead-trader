# Risk synthesis experiment and failure log

All historical periods are development evidence. User approved implementation, tests, root-cause diagnosis and justified revisions in this chat on 2026-10-26. No original PR was merged or old result overwritten.

## Execution state

- Isolated branch `codex/options-risk-synthesis`, based on PR #19 `bb2b738`.
- Baseline: 107 tests passed, 19 data-dependent skips.
- New focused suite currently 24 tests passed. Code review found and reproduced important defects before accepting performance.
- Current running governing experiment: `final_v6_development_all_suite`, source commit `b5ecfae`, 56 funded account/scenario combinations sharing read-only market inputs. Covers baseline, doubled fees, 1% additional per-leg option slippage, assumed one-lot participation floor, and 4% financing-spread stress. Default capacity has **no** one-lot floor.
- Remaining: finish terminal audits; 2012–2013 consumed-history comparison; targeted compensation-screen revision; complete matched-stress reporting, independent attribution, full tests and draft PR.

## Preserved vintages and diagnoses

| Label | Status | Finding / response |
|---|---|---|
| `initial_development_options_base_smoke` | Historical smoke, 180 sessions | Cash/collateral reconciled. Settlement quote counter later corrected; not a final result. |
| `initial_development_options_base` | Completed initial implementation | Risk20 $1M CAGR 5.68%, Sharpe 0.444, drawdown −10.47%. Protected vintage had flawed hedge sizing/roll policy; do not promote. |
| `reviewed_v2_development_equity_base_smoke` | Smoke only | Exercised real stock targets, cash and option funding. Superseded stock trim/unit behavior. |
| `reviewed_v3_development_all_base` | Intentionally stopped | Reviewer reproduced rounding of seven allowed shares into eight. Fixed executed integer shares and preserved an explicit superseded marker. |
| `reviewed_v4_development_all_base` | Incomplete terminal gate | Options accounts completed; stock residuals prevented trial completion. Later tests reproduced fractional-unit dust and introduced raw-price rebasing through stock splits/DRIP. No off-ledger terminal liquidation. |
| `final_v5_development_all_suite` | Failed; no accepted result | Tiny stock risk trim could queue a zero-share pending order. Regression reproduced it; zero requests now leave no pending exit. |
| `final_v6_development_all_suite` | Running | Governing corrected initial policies and costs. Each trial freezes policy/source hashes before execution. |

## Accounting defects corrected before accepting results

1. Native SET resolution no longer calls a missing-quote mark first; it uses actual SOQ and does not mislabel settlement as a quote failure.
2. Ledger enforces admission limits independently of the caller's requested lot count.
3. Options reserve closing commissions across the entire book. A close cannot spend another holding's collateral on its own fee.
4. Replacement hedge is purchased with independent free cash and the remaining gross annual budget before the old hedge can be sold. Unaffordable replacement preserves old protection.
5. Protected carry sizes the hedge in delta units from actual contemplated spread lots, not loss-budget dollars divided by index spot. Coverage shortfalls trigger staged carry reductions; no instant guaranteed fill is asserted.
6. Stock entries, scheduled exits and risk trims share each issuer's lagged ADV capacity. Partial risk trims retain only genuinely unfilled requested shares as pending, not the entire remaining holding.
7. Executed stock shares are capped as integers directly, without converting to a fraction and rounding upward a second time. DRIP/split units rebase to observed raw prices before rounding.
8. Fractional terminal DRIP quantities can fully resolve; negligible partial trims cannot create zero-share pending orders.
9. Borrowing uses the unhaircut lagged RF proxy plus a declared financing spread. Cash earns the separate haircut proxy. Relative scaling and an actual gross-borrowing request have distinct controls.
10. Original admission risk/current NAV is reported separately from remaining carry loss from current quotes to worst contractual settlement. The latter excludes hedge offsets and is not a portfolio stress guarantee.
11. Every final account must reconcile both cumulative cash flows and independent closed-trade P&L/carry/financing to final NAV, with zero terminal holdings. Failure preserves partial ledgers and is not marked complete.

## Economic diagnosis motivating one additional test

Completed options accounts in the reviewed-v4 vintage (stock half of trial incomplete) show the mechanism, not a final winner. At $1M:

- Unhedged risk20: option trade P&L +$500,472, cash interest +$438,773, terminal wealth $1,939,245; 5.68% CAGR, 0.444 excess-RF Sharpe, −10.47% drawdown. A substantial fraction of account growth is cash carry. Smaller risk budgets remove the old return because much of it was compensation for concentrated option risk.
- Protected carry: carry P&L −$102,633, hedge P&L −$69,811, cash interest +$353,364; 1.40% CAGR and negative excess-RF Sharpe. There were 107 coverage-shortfall observations, 80 deferred capacity exits and 141 failed carry-admission attempts. Both paid insurance and forced hedge-coverage adjustments reduced economics. These are iteration-specific diagnostics and must be reconciled to the governing final vintage before final reporting.
- A 10% market-volatility admission throttle lowered return and Sharpe rather than creating a stronger signal.

Increasing size to restore the original CAGR would restore the risk this experiment was intended to remove. Weakening protection and labeling the result "hedged" would also change the research question.

The justified extra challenger is therefore a **pricing compensation screen**, not a larger size: retain risk20's other rules, but require the short put's strictly previous-session IV to be at least **1.2 times** lagged realized market volatility. The threshold is frozen before that challenger is run, no threshold grid is planned, and missing/nonadjacent prior IV excludes the trade. Compare with an otherwise identical risk20 parent at both capitals, after the same costs. This is an adaptive test prompted by development failure, not an untouched validation. It may also fail and will not be promoted without evidence.

## Reproduction constraints

Use the existing Python 3.12/pandas 3 runtime. Explicit source checkout contains the preserved #19 native SPX quote/signal/SET caches; root `data/` supplies normalized stocks and event features. Licensed data remain local. `results/options_risk_synthesis/input_manifest.json` currently records SHA-256 for 51 consulted cache/input files. Fourteen yearly SPX caches were independently checked for unique date/option keys. Recent SPX spot uses native `D:/OptionMetrics/parquet/secprd2012/2013.parquet` rather than fabricated missing 2011 inputs.

The original #19 engine and eight frozen source hashes remain unchanged. Original studies and user-protected worktrees were neither cleaned nor removed.
