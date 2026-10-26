# PEAD options final run manifest (2026-10-22)

This manifest binds the final research claims to the source-cleaned run outputs and states which adjacent directories are superseded. It accompanies the detailed [results record](PEAD_OPTIONS_BACKTEST_RESULTS_2026-10-22.md), [strategy specification](PEAD_OPTIONS_STRATEGY_SPEC_2026-10-22.md), and [research reconstruction](PEAD_OPTIONS_RECONSTRUCTION_2026-10-22.md).

## Final run identity

- Research branch: `codex/pead-options-optimization`.
- Local event and factor data live under ignored `data/`. The licensed OptionMetrics archive remained external and was streamed read-only; no contract-level option file or quote table was copied into the repository.
- Study dates: 1998-01-02 through 2013-12-31. CRSP includes all market sessions; 2011 has no usable option-year file and is no-trade cash exposure. The 2013 OptionMetrics source ends early.
- Shared holding calendar: CRSP ∩ OptionMetrics, 3,689 dates. There are 8 OptionMetrics-only and 336 CRSP-only dates. Daily funded outputs report zero position-days on non-option sessions.
- Final event collision rule: exclude every source row in repeated `(secid, day0_date)` clusters before choosing extreme SUE deciles. Source rows: 147,142 in, 147,115 retained; 13 clusters / 27 rows excluded (14 repeated extras); eligible rows: 65,527 before to 65,512 after. One cluster has both eligible call and put directions.
- Grid: 54 cells, 240,526 candidate trade rows, 97,245 unique contract paths, 4,792,785 contract-days. Unique daily quote keys: 4,780,942 required, 4,767,300 matched. Selected contract quotes were in-memory only (`contract_level_quotes_persisted=false`).

## Output directories

### `results/options_expiry_grid_full_source_collision_cleaned/`

Final settlement and daily 54-cell grid. Key files: `daily_grid_summary.csv`, `daily_grid_yearly.csv`, `daily_grid_paths.csv`, `daily_grid_audit.json`, `event_collision_audit.json`, settlement `grid_summary.csv` / `portfolio_summary.csv`, paired summaries, and 10,000-draw `settlement_grid_stability.csv` / `.json`.

### `results/options_frozen_call_daily_full_source_collision_cleaned/`

Strict 120-calendar-DTE / 0.50-delta / 60-session top-SUE call book and sensitivities. Key files: `daily_portfolio.csv`, `riskfree_daily_portfolio.csv`, `daily_sensitivity.csv`, `paired_hold_profile.csv`, `riskfree_yearly.csv`, `daily_audit.json`, and settlement summaries. Its daily result agrees with the corresponding full-grid cell on 6,023 funded contracts, cumulative premium, option proceeds, cash interest, and ending NAV.

### `results/options_candidate_c75_d70_h40_full_source_collision_cleaned/`

Strict lower-drawdown C/75/.70/40 challenger, with its daily path, cost/capacity/flag/timing sensitivities, same-cohort hold comparisons, yearly summary and audit.

## Decision figures from the final full-grid leader

C/120/.50/60: 6,023 funded contracts; $85.265m cumulative premium; $90.173m exit proceeds; $4.434m credited French-RF cash accrual; $19.342m ending NAV. Its ending wealth is $4.865m above the all-cash French daily-RF proxy. CAGR 4.2108%, excess-RF Sharpe 0.2146, maximum drawdown −33.1901%, four-factor alpha −1.7772% annualized (HAC t −0.8858; 95% CI [−5.7097%, +2.1553%]). Trade P&L reconciles, there are no unclosed positions, and no position-days fall outside the common option calendar.

This is the highest historical wealth point estimate among 54 inspected rules, not proof of an optimum. The most favorable Sharpe belongs to sparse C/75/.30/60: Sharpe 0.5929 on 246 funded contracts, with 88 zero-bid and one missing exit. Its familywise maximum-Sharpe year-block bootstrap p-value across 54 cells is 0.1304 (5,000 draws). Across 54 individual four-factor alpha intervals, 19 are entirely negative and zero entirely positive; those cellwise intervals are not search-adjusted.

## Cash benchmark warning

The French daily factor library reports RF percent rounded to two decimal places. Its daily RF is exactly zero on all 252 dates in 2003 and on every date in 2009–2013; 2004 and 2008 also contain many zero observations. Consequently, the $14.477m all-cash NAV and $4.865m strategy-relative wealth are measured against that published rounded series, not against a reconstructed investable bill account. This may understate low-rate cash carry. The factor ZIP SHA-256 hashes and the code inputs are recorded in each `daily_audit.json` / `daily_grid_audit.json`.

## Superseded output

`results/options_expiry_grid_event_cleaned/` is **not a final run**. It reflects the superseded cleaner that deduplicated only after restricting to extreme SUE deciles. Its daily grid was stopped at 13/54 cells and it has no complete daily portfolio result. A warning is saved as `results/options_expiry_grid_event_cleaned/RUN_INCOMPLETE.md`. Do not cite these settlement files as the final cleaned sample.

## Reproduction

From the worktree root, with the existing virtual environment and local data paths configured in `PEAD_DATA_DIR` and `OPTIONMETRICS_DIR`:

```powershell
.venv\Scripts\python.exe -m pead.research.options_expiry_grid --years 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2012 2013 --data-dir $env:PEAD_DATA_DIR --optionmetrics-dir $env:OPTIONMETRICS_DIR --output-dir results/options_expiry_grid_full_source_collision_cleaned --portfolio --daily-grid
```

Final verification: `tests/test_options_expiry_grid.py`, `tests/test_options_daily_portfolio.py`, and `tests/test_options_robustness.py` passed, 26 tests total. The runtime streamed local data sources; cloud compute was not needed.

**Preservation:** `data/` is Git-ignored and may contain unique local information. Back it up outside the worktree before removing or archiving a managed worktree. Keep the licensed OptionMetrics archive outside GitHub.
