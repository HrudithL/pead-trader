# Selective Earnings Options: approved research design

Approved by the user on 2026-10-25. Scope is RESEARCH.md only; no existing strategy selection or optimization.

## Research hypothesis
Extreme analyst surprises can support directional debit verticals; residual post-event IV can support iron condors; prior extreme news can reverse at the next announcement. Option price must be part of eligibility. Evidence is a hypothesis, not a portfolio return claim.

## Architecture
Separate pead/earnings_options package with validated canonical events, daily contract quotes, benchmark and session calendar; deterministic same-expiry structures; self-financing daily liquidation-NAV ledger; chronological train/validation/test evaluation. A dedicated CLI audits inputs, prepares local data and runs research. No changes to existing engines or strategy parameters.

## Timing and provenance
Daily OptionMetrics EOD data supports delayed EOD execution only, not the intraday execution proposed in RESEARCH.md. Use previous-session volume and OI for capacity. No final-sample quantiles, no future realized volatility filters, no revised consensus. Strict research mode excludes unverified release/consensus availability and pre-event trades without a historically known calendar. Exploratory proxies must be labeled and cannot pass the investment gate.

## Execution and risk
Buy ask, sell bid; commissions per contract per side. Debit spreads reserve premium; condors reserve maximum width less credit plus fees. Integer lots. Maximum-loss budgets: directional 30bp, condor/reversal 20bp; issuer 1%, sector 5%, aggregate 25%, calendar-cohort 5% NAV. Unlevered. Exit before expiry and scheduled next earnings; stop and profit rules use observable liquidation values. Missing short-leg marks reserve contractual worst loss and invalidate execution validation. American assignment/corporate actions require explicit exclusions or authoritative handling; never silently assume European exercise.

## Inference and acceptance
All training outcomes must mature before validation; all validation outcomes before test. Frozen parameters and fingerprints. Walk-forward OOS plus a final untouched holdout. Same-date S&P 500/SPY total returns; a broad-market series is explicitly a proxy. Net CAGR must exceed max(12%, benchmark CAGR)+2 percentage points; Sharpe at least benchmark, positive alpha, dependence-aware lower confidence bound above zero, drawdown no worse than 1.25 times benchmark. Sufficient sample length and actual fills/provenance are required. Test failure is a valid research outcome; do not increase leverage to force acceptance.

## Deliverables
Input audit, reproducible commands, tested structure selection and accounting, local-data preparation, OOS evaluation with costs and exclusions, report of measured results and unresolved data gaps. Preserve every worktree and data source; no removal, prune, clean, symlink or junction operations.
