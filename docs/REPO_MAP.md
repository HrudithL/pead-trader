# Repo map: everything in one place

Written 2026-10-20 at the end of the repo consolidation. If this file disagrees with the code, the code wins; fix this file.

## 1. What this repo is

Post-earnings-announcement drift (PEAD) research: stocks keep moving in the direction of an earnings surprise for weeks. The repo backtests equity strategies on that drift
(1996-2013, WRDS IBES/CRSP/Compustat), an options version (OptionMetrics), a combined portfolio, and a live paper-trading engine. Everything runs through one command:
`python -m pead list | run | report | compare | portfolio | prep | live | paper`.

**Read this first:** `docs/FIXES.md`. Four methodology defects were found and fixed in this consolidation; the previously published headline numbers were overstated
(for example the reference book `balanced_leverage` went 6.49% -> 5.38%, Sharpe 1.30 -> 1.09, and the options books lost essentially their whole edge once filled at real bid/ask).
Current numbers are in `results/leaderboard.md`. Old numbers are kept as `*_legacy` / `*_prefix_fix`.

## 2. How the consolidation was merged (pull requests, in order)

| Order | PR | What | Status when this was written |
|---|---|---|---|
| 1 | #7 | The restructured framework (`pead/`), plain strategy names, analysis docs. Contains the former live-paper-trading and volatility branches. | open, mergeable, awaiting the owner's merge |
| 2 | #9 | The four methodology fixes + the corrected results + `docs/FIXES.md` + refreshed docs. Stacked on #7. | open |
| 3 | #10 | One research paper (`docs/paper/`, 46 pages), rebuilt from the corrected results; replaces #5. Stacked on #9. | open |
| 4 | #8 | Archive of the 2026-08 optimization line + this map. Stacked on #7. | open |
| - | #5 | Old plain-language paper PR. Superseded by #10; closed (reopenable), tag `archive/pr5-strategies-explained` keeps its commit. | closed |

Stacked PRs are based on the branch below them, so each shows only its own changes. After #7 merges, GitHub retargets the next one to `main`; merge in the order above.

## 3. Layout

```
pead/                    the package (one strategy = one file)
  strategies/{equity,options,portfolio}/   strategy definitions; legacy_exposure.py generates the <name>_legacy twins
  engines/               equity.py, options.py  (backtest engines)
  prep/{equity,options}/ the data-build stages (`python -m pead prep --list`)
  core/ common/          data loading, metrics, alpha, Kelly, point-in-time ranking, paths, GPU/CPU backend
  reporting/             per-run reports, comparisons, attribution, situations, the paper builder
  live/                  daily paper-trading engine (Alpaca paper + simulated option fills)
  research/              ARCHIVE: the old numbered scripts and recovered side-projects (nothing imports from here)
results/                 one folder per run (summary.json, daily.csv.gz, report.md ...), leaderboard.*, comprehensive/
data/                    small tracked inputs/outputs (CSV/JSON); big derived data is git-ignored
docs/                    FIXES, STRATEGIES, STRATEGY_PROFILES, SITUATIONS, ARCHITECTURE, LIVE_TRADING, HISTORY, REPO_MAP, paper/
scripts/                 alpha_benchmark.py, situations_report.py, strategy_profiles.py, build_situations_doc.py, git-hooks/, setup_gpu_box.sh
reports/ reference/      legacy PDFs (pre-framework, pre-correction numbers) and the reference option code
tests/                   test_framework.py (engine reproduces the original scripts), test_fixes.py (the four fixes)
```

## 4. Strategy catalog (55 registered; numbers live in `results/leaderboard.md`, not here)

`status`: `validated` = the engine reproduces the original script exactly (NOT "has an edge"); `research` = chosen/tuned in-sample or exploratory; `reference` = kept for comparison.

**Equity (22 strategies, each with a `<name>_legacy` twin = 22 more).** Plain description of each is in `docs/STRATEGY_PROFILES.md`.
- Baselines: `extreme_picks`, `graded_bets`, `size_balanced`
- Neutralized: `conviction_tilt` -> `sector_balanced` -> `balanced_leverage` (the reference book)
- Overlay / directional: `market_boost` (adds a half-size market position), `market_exposed` (cautionary: carries market exposure)
- Volatility: `calm_weighted`, `calm_weighted_matched`, `calm_rank_tilt` (research), `calm_rank_levered` (research); negative results kept: `volatility_filter`, `calm_weighted_filter`
- Max-size variants (6x cap, research; checked against a -40% drawdown screen several now breach): `*_max` for balanced_leverage (identical book to `sector_balanced_max`), conviction_tilt, extreme_picks, graded_bets, size_balanced, market_exposed, market_boost
- `<name>_legacy` twins reproduce the *published* runs to the dollar (the original, wrong leverage cap on the original data).

**Options (8):** `options_cap20`, `options_cap30`, `options_worst_case` (corrected: ask in / bid out); `options_cap20_midfill`, `options_cap30_midfill` (the previous mid-fill convention); `options_cap20_legacy`, `options_cap30_legacy`, `options_cap40_legacy` (the originally reported, overstated numbers).

**Portfolio (3):** `hedged_blend` (0.8x equity sleeve + 1.6x options sleeve on one NAV), `calm_blend`, `legacy_blend`. Blend weights were chosen when the options sleeve looked good and were not re-optimised.

## 5. Ideas that exist but are NOT registered strategies

| Idea | Where | Why not ported |
|---|---|---|
| Volatility-targeted market overlay; fully vol-managed book | `pead/research/optimization_line_2026_08/` | developed before the fixes; port after re-testing |
| Analyst-dispersion filter (its own "Strategy 8", not the ML one) | same folder | a risk filter, not alpha (its own note); blend weight picked in-sample |
| Revision / revenue-surprise / seasonal-SUE diagnostics | same folder | printed to console only; conclusions not saved except dispersion |
| ML "Strategy 8" (walk-forward MLP + GPU sweep) | `pead/research/equity_strategy_history/` | below the reference book, status unverified |
| Options Tiers 2-4 (exit optimizer, ML selector, joint optimizer) | `pead/research/options_strategy_history/` | tuned on the wrong quote convention |
| Risk-reversal options design | git tag `archive/options-strategy-risk-reversal` | superseded by the Kelly selector; script numbers collide with `main` |
| Re-select constants (trim/tilt/units/blend weights) on a search window | open task, `docs/FIXES.md` | not done; would most likely lower the numbers further |
| "Tier B": score option contracts at the ask, not the mid | open task | needs a selector re-run |

## 6. Data and reproducibility

- Raw data master: `D:\OptionMetrics\pead_wrds_data` (WRDS equity side) and `D:\OptionMetrics\parquet` (OptionMetrics, never copied). Real directories only; never symlinks or junctions (a tracked symlink once deleted real data).
- Git-ignored working data lives under `data/` (`raw_wrds`, `normalized_equity`, `earnings`, `events`, `event_options`, `cache`, `*.parquet`). Path overrides: the `PEAD_*_DIR` environment variables in `pead/common/paths.py`.
- Use the default Python 3.12 / pandas 3. pandas 2 silently produces different deciles.
- **The committed `data/` is the PRE-fix vintage.** The corrected data vintage was built in an isolated folder; to rebuild see `docs/FIXES.md` (`python -m pead prep ... --force` with the options chain, selector on CPU takes about 90 s).
- Switches back to the old behavior: `<name>_legacy`, `PEAD_DECILE_RANKING=within_quarter`, `PEAD_DECAY_SEARCH_FRAC=1.0`.
- A full byte-for-byte backup of every old worktree plus a git bundle of all branches exists at `D:\PEAD_repo_backup_2026-10-18\` (outside the repo).
- The live paper-trading workflow (`.github/workflows/live_paper_trading.yml`) has its schedule commented out: no `ALPACA_*` / `FINNHUB_API_KEY` repository secrets exist. To go live add the three secrets and uncomment the two cron lines.
- The pre-commit hook blocks any staged symlink (single git call; the old per-file version deadlocked on big commits).

## 7. Where every old branch and worktree went

| Old branch / worktree | Content | Fate |
|---|---|---|
| `claude/strategy-analysis-backtest-481b86` (worktree of the same name) | the restructure + live trading + volatility work | PR #7; its folder also hosted the fixes work (PR #9) |
| `claude/live-paper-trading-earnings-acf39f`, `claude/volatility-subsampling-strategies-7f0318` | commits already inside #7 | delete after #7 merges |
| worktree `fund-leverage-backtest-assumptions-3d4dc8` | four fixes in the old layout (never run) | ported into #9; kept as tag `archive/methodology-fixes-2026-09-24`; its mock-run outputs stay in the folder and the D: backup |
| worktree `pead-strategy-optimization-634f65` | vol-targeting, dispersion filter, diagnostics | archived in #8; tag `archive/optimization-line-2026-08` |
| worktrees `gpu-equity-trading-strategy-a87ac6` and `paper-strategy-mechanics-29026a` | two research-paper drafts (2026-09-24 and 2026-10-01) | merged into the one paper (#10); tags `archive/paper-quant-2026-09-24`, `archive/paper-mechanics-2026-10-01` |
| `claude/trading-strategies-research-a9f3ee` (PR #5) | plain-language paper | superseded, closed; tag `archive/pr5-strategies-explained` |
| `claude/options-strategy` | risk-reversal options design (1 old commit) | tag `archive/options-strategy-risk-reversal`, then delete |
| `origin-check/main` (stale remote-tracking ref) | an old line of main | tag `archive/origin-check-main`, then delete |
| worktrees `equity-strategies-overview-b81011`, `gpu-options-master-script-888a09`, `live-paper-trading-earnings-acf39f`, `scripts-reorganize-refactor-4ab9db` | nothing unique except git-ignored derived data (kept in the folders and the backup) | remove by moving the folder aside, never by `git worktree remove` |
| `claude/aggressive-equities-variants-c8d42a`, `claude/gpu-equity-trading-strategy-a87ac6`, `claude/gpu-options-master-script-888a09`, `claude/scripts-reorganize-refactor-4ab9db`, `claude/options-strategy-gpu-compute-131625`, `claude/pead-options-market-aec53d`, `claude/pead-strategy-optimization-634f65`, `claude/kelly-options-equity-research-070ed7`, `claude/equity-strategies-overview-b81011`, `claude/paper-strategy-mechanics-29026a`, `claude/fund-leverage-backtest-assumptions-3d4dc8`, remote `origin/options-risk-cap-and-results` | each verified as an ancestor of `main` (nothing unmerged) | delete the branch labels after the PRs merge |
| worktree `strategy-evaluation-roi-74c7dd` | **another agent is building new equity strategies here** | **untouched on purpose. Do not prune, move, or delete it.** |

**Rule for removing a worktree:** never `git worktree remove` (git treats git-ignored data folders as disposable and `--force` deletes them recursively). Verify the backup, move the folder aside (a rename deletes nothing), then `git worktree prune`. Delete a branch only with `git branch -d`, which refuses unmerged work.

## 8. Open decisions

1. Merge #7, then #9, #10 and #8 (the owner's call; the automation's permission check refused to merge without review).
2. Re-select strategy constants and blend weights on a search window (expected to lower numbers).
3. Paper: confirm the author line, the date line and the audience (see the paper PR).
4. Port the vol-targeted overlay / dispersion filter as registered strategies after re-testing on the corrected data.
5. Turn the live workflow back on only after adding the three secrets.
