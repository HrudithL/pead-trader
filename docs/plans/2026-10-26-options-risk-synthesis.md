# Options risk synthesis implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test the user-approved carry, protected carry and hedged equity hypotheses with explicit funded positions; investigate failures without concealing the search.

**Architecture:** Add an independent `pead/risk_research` package, reusing #19's actual native SPX quotes, signals, structures, SET and metrics. Preserve the original engine and frozen holdout hashes. A single daily cash ledger owns stock market values, option liabilities, collateral, fees, shared leg capacity, financing and trade records. No saved NAV series becomes an executable sleeve.

**Tech stack:** Existing Python 3.12 / pandas 3 / NumPy / PyArrow / pytest; sequential qwen worker for fully specified helpers; planner owns accounting and final review.

Approved by user in chat 2026-10-26, including diagnosis and economically justified revisions. All 1999–2013 history is development; absent 2011 option quotes are an explicit boundary, not filled or invented.

## Frozen initial policies

- Carry: same lagged trend/breadth/RV signal and 35/15-delta SPX monthly put-credit menu; ticket and weekly cohort 5% original maximum loss/NAV, aggregate 20%, each expiry 10%. Whole lots, full width collateral, $1.50/leg/side, 5% prior volume and 2% prior OI, no lot floor in governing run. Risk-off exits persist and share capacity with entries. Retain the earlier one-lot-floor convention as a stress/comparator only.
- Protected carry: identical carry policy plus 25/10-delta bear-put spreads, 60–120 DTE nearest 90, monthly rolls, 1% initial-year-NAV gross annual debit budget (calendar-year reset, available at year start). Require funded hedge coverage before admitting carry. Report affordability, capped payoff, underhedging and cash/no-trade outcomes. Hedge closes/rolls must succeed before replacing it; no assumed fill.
- Equity: existing causal SUE/volatility weights and 60-session hold objective, next-session entry, 1% issuer cap, 5% stock ADV, 80% marked stock admission cap, 12% lagged risk target. Rebuild real positions with total-return marks and per-side existing liquidity-quintile costs; explicitly label this a new funded implementation rather than pretend exact reproduction of the old fixed-notional wrapper. Compare unhedged, 80% of that exposure, beta-sized funded put-spread hedge, and 1.2x financing control. Financing uses lagged RF plus 2%/year with a 4% stress. Report stock/cash/hedge attribution and basis risk.
- Beta: prior 126 sessions, minimum 60, covariance/variance, clipped [0,1], decisions shifted one session. Desired hedge initial delta offsets 50% of estimated equity beta exposure, constrained by annual debit budget and whole lots; report shortfall. Carry requires hedge delta at least 25% of carry's prior/entry delta, otherwise skip new carry.
- First failure revisions may change ONE identified cause at a time, with new protocol labels. The initial policies are never overwritten. Targets: CAGR >=12%, excess-RF Sharpe >=1, drawdown >=-20%; also require relevant-parent comparisons and robustness. None is assumed attainable.

## Task 1 — baseline and evidence

1. Create isolated `codex/options-risk-synthesis` from #19, preserve originals.
2. Run `python -m pytest tests -q`; observed baseline 107 passed, 19 data-dependent skips.
3. Preserve the independent synthesis and source fingerprints in the new branch.
4. Commit the approved design/plan before implementation.

## Task 2 — causal helpers and selectors

Files: `pead/risk_research/policy.py`, `tests/test_risk_research.py`.

1. Write failing tests for lagged beta/future mutation, same-expiry put debit selection, executable debit/capacity, and annual gross premium budget.
2. Run focused pytest and observe missing functionality.
3. Delegate the unambiguous beta helper to the local worker; review and apply only correct output.
4. Implement deterministic 25/10 prior-delta selector with positive debit below width, DTE/expiry/liquidity checks, ordered ties and no invented quote rows.
5. Re-run tests, including zero capacity and unaffordable whole lots.

## Task 3 — shared funded ledger

Files: `pead/risk_research/ledger.py`, `tests/test_risk_research.py`.

1. Write failing tests for spread roundtrip cash/fees, SET payoff, collateral isolation, expiry/cohort limits, missing quotes, persistent partial exits, shared hedge/carry leg usage and hedge prerequisites.
2. Implement daily ledger. Interest accrues once on beginning free cash. Actual stocks spend cash and mark on total returns; option credits reserve full width. NAV equals cash + stock values + executable option values, including exit fee reserves. Missing option marks use disclosed terminal worst-value reserves, never executable claimed fills.
3. Stage exits before entries, capacity constrained. Existing unavailable exits cannot finance new entries. Terminal missing stock values cannot be silently forward-filled; record exclusions/reserves. Stop new option entries 120 calendar days before known segment end and resolve all terminal positions.
4. Verify independent terminal cash = initial NAV + stock P&L + option P&L + cash carry - fees - financing. Test future-data invariance.

## Task 4 — inputs and full experiments

Files: `pead/risk_research/study.py`, `pead/risk_research/report.py`.

1. Load source caches read-only via explicit CLI paths; never symlink or move data.
2. Prepare causal stock targets and filtered normalized CRSP rows; include known event timing/duplicate exclusions and whole initial shares. Keep licensed rows in ignored local cache only.
3. Freeze policy/source hashes and write a trial entry BEFORE each run. Save every attempted trial and failures.
4. Run 1999–2010 and separately 2012–2013 at $100K/$1M options capitals; equity at $1M/$10M if capacity permits. Do not compound through the 2011 source gap.
5. Run doubled fees, additional quoted slippage, no-floor capacity, premium-budget/financing stresses and one-variable diagnostic ablations warranted by results.
6. Summarize excess-RF Sharpe, CAGR, drawdown, expected shortfall, worst session/year, yearly returns, market/factor exposure, funded counts, risk breaches, premium cost and capacity. Paired 3-month bootstrap against parent, plus prior chronological era results; no new holdout claim.

## Task 5 — diagnose and review

1. Trace weak performance to financing, hedge carry/affordability, premature exit, concentration, price response, timing or signal weakness using ledger attribution.
2. Reproduce bugs with failing tests before fixing; rerun affected experiments with new trial IDs. Revise only an identified mechanism, never fabricate target performance.
3. Run focused and full tests and cash-flow reconciliation. Review code against invariants and original requirements.
4. Save `docs/OPTIONS_RISK_SYNTHESIS_RESULTS_2026-10-26.md` and aggregate-only artifacts. Clearly distinguish useful rejected experiments, surviving research candidates, and deployment acceptance.
5. Create and attach a draft PR, stacked on #19 if needed; do not merge existing draft PRs, alter their results, or enable live trading.
