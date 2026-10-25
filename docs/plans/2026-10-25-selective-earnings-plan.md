# Selective Earnings Options Implementation Plan

**Goal:** Make RESEARCH.md executable and auditable as its own strategy research system.
**Architecture:** Independent multi-leg engine, explicit input contracts, EOD data adapter, separate research CLI.
**Tech stack:** Python 3.12, pandas 3, numpy, pyarrow, pytest.

1. Preserve RESEARCH.md and AGENTS.md in dedicated worktree and branch. Create isolated environment. Audit raw and derived schemas, timestamps, quotes, benchmark coverage.
2. Write failing synthetic tests for spread selection, cash/collateral and no look-ahead. Specify deterministic selector and delegate mechanical draft to qwen-delegate sequentially. Review and apply.
3. Implement strict canonical schemas, signals, directional/condor/reversal selection, quote-side fills, integer lots, daily risk limits, missing-mark handling, ledger and trade audit. Run tests after each module.
4. Build raw EOD adapter with bounded parquet batches, prior-session liquidity, delayed entry, prior-only signal ranks and historical RV. Cache only within new worktree. Preserve source data read-only.
5. Implement purged chronological folds, parameter freezing, same-date benchmark evaluation, block bootstrap and explicit pass/fail acceptance gates. Test boundary cases.
6. Run focused tests, baseline suite and end-to-end synthetic fixture; execute available-data audit and real-data research run where valid. Document unresolved timestamp, assignment, benchmark and era limitations precisely.
7. Review changes, inspect git diff, commit only new research files and small reports. Leave data and worktree intact.
