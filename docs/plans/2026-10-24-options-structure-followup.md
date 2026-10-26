# PEAD Options Structure Follow-up Implementation Plan

> **For the executing agent:** Keep all edits and outputs in `codex/pead-options-next-study`. Do not remove either worktree or edit raw data.

**Goal:** Produce a causal, matched-event comparison of the frozen next-session long-call strategy, same-expiry debit call spreads, and stock exposure, and determine whether timestamp-safe IBES revisions can be tested.

**Architecture:** Add a standalone options research module under `pead/research/` that reuses the event collision filter and streams only selected OptionMetrics leg IDs through next-session and exit dates. It reconstructs stock paths from CRSP daily returns and writes aggregate summaries/audits only. IBES revision feasibility and stock PEAD/reversal diagnostics are separate modules. The IBES stage emits no strategy result unless timestamp, forecast-horizon, and share-basis checks pass.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow, existing PEAD research modules and the licensed local OptionMetrics archive.

---

### Task 1: Audit source columns, calendar, and file coverage

**Files:**
- Read: `pead/research/options_expiry_grid.py`
- Read: `pead/research/options_daily_portfolio.py`
- Read: `data/event_options/decile_events_secid.parquet`
- Read: `data/raw_wrds/ibes/detu_epsus.parquet`
- Read: `data/normalized_equity/equity_*.parquet`

Record fields required for event IDs, selection quote fields, daily bid/ask/OI, CRSP close-to-close returns, and IBES forecast timestamps. Confirm option years from the existing chain and OptionMetrics archive before running.

### Task 2: Implement matched long-call and vertical event selection

**Files:**
- Create: `pead/research/options_structure_comparison.py`

Reuse `prepare_strategy_events` for collision-safe filtering. Implement a deterministic matched contract selector for top-SUE C/75/0.70 long calls and same-expiry higher-strike short calls nearest 0.30 delta using day-0 chain fields; schedule next-session entry and the 40-session exit with the expiry buffer. Exclude repeated security/date clusters before the SUE filter and record reasons for spread ineligibility.

### Task 3: Stream entry and exit quotes for selected legs

**Files:**
- Modify: `pead/research/options_structure_comparison.py`

Load next-session asks/bids/OI and scheduled-exit bid/ask/OI for only the selected option IDs. Long-call cash flow is ask at entry, bid at exit. Spread debit is long ask minus short bid; liquidation credit is long bid minus short ask, floored at zero. Missing entry leg excludes the spread; missing exit leg sets spread proceeds to zero. Cap spread lots by both legs' open interest and never persist contract-level quotes.

### Task 4: Build common event returns and portfolio summaries

**Files:**
- Modify: `pead/research/options_structure_comparison.py`
- Create outputs: `results/options_structure_comparison_2026-10-24/`

Use the same dates and event IDs for stock, long call, and spread. Calculate CRSP stock total returns between the same close dates. Produce event-level P&L normalized by initial delta dollars and funded portfolio curves under common NAV, cap, and per-entry capital budgets. Add baseline and low-volatility tercile summaries, quote/coverage/capacity audits, yearly summaries, bid/ask friction stresses, and factor-risk metrics from daily NAV.

### Task 5: Audit IBES revisions feasibility and, only if valid, score it

**Files:**
- Create: `pead/research/ibes_revision_feasibility.py`
- Read: `data/raw_wrds/ibes/detu_epsus.parquet`
- Create outputs: `results/options_structure_comparison_2026-10-24/ibes_revision_feasibility.json` and `ibes_revision_feasibility_summary.csv`

Stream only needed IBES columns and candidate tickers. Require the same analyst and forecast fiscal-period end before/after the announcement; establish the announcement-relative revision timestamp and a next-session cutoff; audit ticker joins, analyst coverage, missing clocks, and fiscal-horizon mapping. Stop at a feasibility report if timestamp semantics or share basis are not defensible. Never use post-entry revisions as predictors.

### Task 6: Reconcile the research report

**Files:**
- Create: `docs/PEAD_OPTIONS_STRUCTURE_FOLLOWUP_2026-10-24.md`
- Update: `docs/PEAD_OPTIONS_DECISION_AND_IMPROVEMENT_PLAN_2026-10-24.md`

Report the new comparison, methods, attrition, actual eligible history, limits, and ranked methods to improve PEAD options. Correct the prior IBES availability claim based on the audit. Keep the 12% CAGR hurdle distinct from a claim that it can be achieved.

### Task 7: Verify outputs and preserve source data

Run the analysis module in the dedicated study worktree; reconcile matched sample counts across instruments, ensure output files contain aggregates rather than quote rows, inspect the study diff, and confirm the original data-bearing worktree remains registered. Do not remove or archive any worktree.

### Completion notes (2026-10-24)

- Tasks 1–4 completed. The next-session all-call book reconciles to the prior C/75/0.70/40 run at 5,195 funded trades, 3.29% CAGR, 0.141 excess-RF Sharpe, and −27.25% drawdown. Common cohort counts are 3,169 candidate events, 3,099 funded calls, 3,150 funded spreads, and 3,069 funded stock positions.
- Task 5 completed as a coverage audit only. The detail file maps a plausible next-quarter horizon for 33,048 of 33,108 positive-SUE events; changed-analyst coverage is summarized at 1/5/10-session feature freezes. No revision-return backtest was produced because timestamp semantics/time zone, unadjusted split basis, and the inferred horizon require further validation.
- Task 6 completed in `docs/PEAD_OPTIONS_STRUCTURE_FOLLOWUP_2026-10-24.md`; the prior improvement memo was corrected to reflect the local raw IBES extract.
- Task 7 completed: Python syntax compilation, full aggregate backtest, drift/reversal audit, IBES coverage audit, and `git diff --check` succeeded. `git worktree list` confirms the original `C:/Users/hrudi/.codex/worktrees/pead-options-optimization/PEAD_Trading` remains registered and its `data` directory remains present. No raw OptionMetrics quotes or analyst-level forecast rows were written, and no worktree was removed or archived.
