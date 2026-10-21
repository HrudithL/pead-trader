# Repo map: everything in one place

Written 2026-10-20 at the end of the repo consolidation. If this file disagrees with the code, the code wins; fix this file.

## 1. What this repo is

Post-earnings-announcement drift (PEAD) research: stocks keep moving in the direction of an earnings surprise for weeks. The repo backtests equity strategies on that drift
(1996-2013, WRDS IBES/CRSP/Compustat), an options version (OptionMetrics), a combined portfolio, and a live paper-trading engine. Everything runs through one command:
`python -m pead list | run | report | compare | portfolio | prep | live | paper`.

**Read this first:** `docs/FIXES.md`; then `docs/STEADY_DRIFT.md` for the current best equity strategy and `docs/ROADMAP.md` for what is still missing. Four methodology defects were found and fixed in this consolidation; the previously published headline numbers were overstated
(for example the reference book `balanced_leverage` went 6.49% -> 5.38%, Sharpe 1.30 -> 1.09, and the options books lost essentially their whole edge once filled at real bid/ask).
Current numbers are in `results/leaderboard.md`. Old numbers are kept as `*_legacy` / `*_prefix_fix`.

## 2. How the consolidation was merged (all done, 2026-10-20)

| PR | What | Status |
|---|---|---|
| #7 | The restructured framework (`pead/`), plain strategy names, analysis docs. Contains the former live-paper-trading and volatility branches. | merged |
| #9 | The four methodology fixes + the corrected results + `docs/FIXES.md` + refreshed docs and reports. | merged |
| #10 | One research paper (`docs/paper/`), rebuilt from the corrected results; replaces #5. | merged |
| #8 | Archive of the 2026-08 optimization line + this map. | merged |
| #11 | `options_cap20/30` and `hedged_blend` relabelled `research`; what `validated` means made explicit. | merged |
| #12 | Paper rebuilt on corrected deciles: SUE reversal, volatility sweeps, capacity sweep. | merged |
| #14 | One complete `data/`: raw master (hash-verified identical to the `D:` master) + the corrected derived vintage; the pre-fix vintage kept at `data/_prefix_vintage/`; legacy tests run against it. | merged |
| #15 | Paper: option-spread and coverage tables rebuilt on corrected deciles from the OptionMetrics files. Nothing in the paper is pre-correction any more except the kept `*_legacy` comparison runs. | merged |
| #17 | Steady Drift: the long-only, never-borrowing, risk-throttled strategy family (`steady_drift`, `_calm`, `_core`, `_plus`), the engine additions behind it, its research (`meta/`, `docs/META_STRATEGY.md`, `docs/STEADY_DRIFT.md`) and `docs/ROADMAP.md`. | merged |
| #5 | Old plain-language paper PR. Superseded by #10; closed, tag `archive/pr5-strategies-explained` keeps its commit. | closed |

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

## 4. Strategy catalog (59 registered; numbers live in `results/leaderboard.md`, not here)

`status`: `validated` = the engine reproduces the original script exactly (NOT "has an edge"); `research` = chosen/tuned in-sample or exploratory; `reference` = kept for comparison.

**Equity (22 strategies, each with a `<name>_legacy` twin = 22 more).** Plain description of each is in `docs/STRATEGY_PROFILES.md`.
- Baselines: `extreme_picks`, `graded_bets`, `size_balanced`
- Neutralized: `conviction_tilt` -> `sector_balanced` -> `balanced_leverage` (the reference book)
- Overlay / directional: `market_boost` (adds a half-size market position), `market_exposed` (cautionary: carries market exposure)
- Volatility: `calm_weighted`, `calm_weighted_matched`, `calm_rank_tilt` (research), `calm_rank_levered` (research); negative results kept: `volatility_filter`, `calm_weighted_filter`
- Max-size variants (6x cap, research; checked against a -40% drawdown screen several now breach): `*_max` for balanced_leverage (identical book to `sector_balanced_max`), conviction_tilt, extreme_picks, graded_bets, size_balanced, market_exposed, market_boost
- `<name>_legacy` twins reproduce the *published* runs to the dollar (the original, wrong leverage cap on the original data).

**Long-only (4, no legacy twin):** `steady_drift` (top surprise decile, 60-session holds, calm stocks sized up, idle money in T-bills, exposure throttled to ~10% volatility), `steady_drift_calm` (8% target), `steady_drift_core` (no throttle), `steady_drift_plus` (smaller, more numerous positions, 80% cap, 12% target; **the best equity strategy that obeys no-borrowing and no look-ahead**, status research). Evidence and limits: `docs/STEADY_DRIFT.md`; open items: `docs/ROADMAP.md`.

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
- **`data/` is the CORRECTED point-in-time vintage** (raw master, byte-identical to the `D:` master, plus the corrected derived files), so `python -m pead run <name>` reproduces the committed corrected results. The complete PRE-fix derived vintage (537 files) is kept, git-ignored, in `data/_prefix_vintage/`; select it with the `PEAD_*_DIR` recipe in `docs/FIXES.md` ("Where the old numbers are kept") to reproduce the published `*_legacy` numbers. To rebuild the derived files see `docs/FIXES.md` (`python -m pead prep ... --force` with the options chain, selector on CPU takes about 90 s).
- Switches back to the old behavior: `<name>_legacy`, `PEAD_DECILE_RANKING=within_quarter`, `PEAD_DECAY_SEARCH_FRAC=1.0`.
- Backups (outside the repo): `D:\PEAD_repo_backup_2026-10-18\` holds a byte-for-byte copy of every old worktree and a bundle of all branches. `D:` is mounted again (it was missing earlier on 2026-10-20), so that backup and the raw-data masters are reachable. The OptionMetrics extract (131 files, 32.9 GB at `D:\OptionMetrics\parquet`) is still read from there (`OPTIONMETRICS_DIR`), not copied into the repo. A second bundle of every branch and tag is on `C:` at `C:\Users\hrudi\Documents\BEI\_pead_backups\all_refs_2026-10-20.bundle`, and the old worktree folders themselves are on `C:` at `C:\Users\hrudi\Documents\BEI\_pead_archived_worktrees\`.
- The corrected data vintage (git-ignored parquet) lives outside the repo at `C:\Users\hrudi\Documents\BEI\_pead_consolidation\vintage_fixed\` (plus `vintage_old`, `vintage_fix2only`, `vintage_fix3only`, `vintage_paper`, used for the attribution ladder and the paper). The repo's own `data/` now holds the corrected vintage (the pre-fix one is `data/_prefix_vintage/`).
- The live paper-trading workflow (`.github/workflows/live_paper_trading.yml`) has its schedule commented out: no `ALPACA_*` / `FINNHUB_API_KEY` repository secrets exist. To go live add the three secrets and uncomment the two cron lines.
- The pre-commit hook blocks any staged symlink (single git call; the old per-file version deadlocked on big commits).

## 7. Where every old branch and worktree went

**State at the end of 2026-10-20.** Worktrees: `main`, `strategy-analysis-backtest-481b86` (now on a short-lived branch; kept because its git-ignored `results/*/positions.parquet` are the corrected position files the paper builder reads), and `strategy-evaluation-roi-74c7dd` (the Steady Drift work; merged in #17 and then moved aside, see the table).
Ten former worktrees (the eight old ones plus the two temporary `consolidation-paper` / `consolidation-archive` checkouts) were **moved, not deleted**, to `C:\Users\hrudi\Documents\BEI\_pead_archived_worktrees\` and git's registrations were pruned; their git-ignored derived data is still in those folders.
Eighteen merged local branch labels were deleted with `git branch -d`. Still present locally: `rescue/*` (4, same commits as the `archive/*` tags), `claude/options-strategy` and `claude/trading-strategies-research-a9f3ee` (unmerged, tagged), the short-lived working branch, and the ROI branch. **No remote branch has been deleted** (that needs the owner's approval).

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
| worktree `strategy-evaluation-roi-74c7dd` (branch of the same name) | Steady Drift research and port | merged in #17. Folder **moved, not deleted**, to `C:\Users\hrudi\Documents\BEI\_pead_archived_worktrees\strategy-evaluation-roi-74c7dd` (its git-ignored `meta/out` and `meta/debate2` caches regenerate with `meta/build_events.py`); registration pruned; branch label deleted with `git branch -d` |

**Rule for removing a worktree:** never `git worktree remove` (git treats git-ignored data folders as disposable and `--force` deletes them recursively). Verify the backup, move the folder aside (a rename deletes nothing), then `git worktree prune`. Delete a branch only with `git branch -d`, which refuses unmerged work.

## 8. Open decisions

1. **Re-select the strategy constants and blend weights on a search window** (pick the dials on early history, test on later history). Expected to lower the numbers further.
2. **Copy the OptionMetrics extract (131 files, 32.9 GB) onto `C:`?** The options stages read it from the external drive `D:\OptionMetrics\parquet`, and that drive was unplugged twice on 2026-10-20. Earlier policy was "never copied"; this is the owner's call.
3. **Remote branches:** all remote branches are deleted (2026-10-21). The two that were unmerged, `claude/options-strategy` and `claude/trading-strategies-research-a9f3ee`, survive as pushed tags `archive/options-strategy-risk-reversal` and `archive/pr5-strategies-explained`.
4. Port the vol-targeted overlay / dispersion filter as registered strategies after re-testing on the corrected data.
5. Turn the live workflow back on only after adding the three secrets (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `FINNHUB_API_KEY`).
6. Delete `C:\Users\hrudi\Documents\BEI\_pead_archived_worktrees\` only when sure nothing in it is needed.
