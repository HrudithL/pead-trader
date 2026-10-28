# Parallel work: many sessions, many worktrees, no overlap

Written 2026-10-27. How to run several Claude/Codex sessions at once against the plans in `docs/plans/research_library_2026-10-27/` without them colliding. If this disagrees with the code, the code wins.

## 1. The model: one trunk lane, many leaf lanes

Almost every spec adds a new file (a strategy, a signal, a test), which is safe to do in parallel. The collisions come from a small set of shared files that many specs also want to change. So work is split in two:

- **Trunk lane (serial, one session at a time).** Owns every shared-infrastructure change (the `REF-*` items). Small PRs, merged to `main` one by one, each leaving all NAV pins bit-identical. Nobody else edits these files.
- **Leaf lanes (parallel).** One worktree + one branch + one PR per spec. A leaf only adds files, and only uses a shared piece after the trunk PR providing it has merged.

### Trunk-owned files (leaf sessions must not edit these)

`pead/engines/equity.py`, `pead/engines/options.py`, `pead/strategies/base.py`, `pead/strategies/registry.py`, `pead/core/{data,metrics,alpha,store,rates}.py`, `pead/cli.py`, `pead/runner.py`, `pead/prep/stages.py`, `pead/common/paths.py`, `tests/test_framework.py`, `tests/test_fixes.py`, `scripts/git-hooks/`. If a leaf spec's section 5b lists any of these under CHANGE, that part is a trunk task; the leaf waits or the trunk lane does it first.

### Generated / shared outputs (nobody commits these from a leaf branch)

`results/leaderboard.md`, `results/leaderboard.json`, `docs/REPO_MAP.md`, `docs/ROADMAP.md`. A leaf commits only its own `results/<its_strategy>/` folders and its spec's status line. After merges, one maintainer session regenerates the leaderboard on `main` and updates the map/roadmap.

## 2. Lanes and what can run at the same time

Spec IDs refer to `docs/plans/research_library_2026-10-27/`. REF names are defined in `ENABLING_CHANGES_MASTER.md`.

| Lane | Kind | Owns (new files / dirs) | Start when |
|---|---|---|---|
| **T0** Guard rails | trunk | `tests/pit_kit.py`, `tests/reference/`, `pead/core/stats.py`, `pead/eval/registry.py`, data-gap register (MT-01 tests, MT-10, MT-03 additive, MT-14, trial registry) | now |
| **T1** Gates and interfaces | trunk | strategy interface, outcome firewall, search/holdout, eval harness skeleton, event-time layer (read-only audit) | after T0 |
| **T2** Data and engine hooks | trunk | delisting returns, `panel_quotes`, cost hooks, manifest, metrics switch, quarter-bucket fix | after T1, each in its own data vintage |
| **A** Measurement | leaf | `pead/signals/`, `pead/validation/`, event feature tables (EQ-30, EQ-22, EQ-02, EQ-21, MT-07, MT-08/OV-01, MT-11) | after T0 pit-kit + metrics; EQ-30/22/21 can start in parallel with T1 if they only read data |
| **B** Equity candidates | leaf, one per spec | one file each in `pead/strategies/equity/` (EQ-23, EQ-24, EQ-28, EQ-06, EQ-04, EQ-05, EQ-01, OV-02, OV-03, ...) | after T1 gates exist (so results are comparable); signal-only specs can start after lane A |
| **C** Options | leaf group | `pead/engines/fills.py`, `options_structures.py`, `pead/strategies/options/*` (OPT-01 first and alone) | OPT-01 now (it owns the options-engine refactor and is its own trunk-like lane); OPT-02..06 only after its spread report passes the go/no-go gate |
| **D** Docs/maintenance | leaf | leaderboard regeneration, REPO_MAP/ROADMAP updates | after merges |

Hard rules: only **one** session may be in the options-engine refactor (OPT-01) and only **one** in the equity-engine hooks (T2) at any time. Lanes A, B, C otherwise run concurrently as long as their file sets are disjoint. When in doubt compare the "NEW" and "CHANGE" lists in the two specs' section 5b.

## 3. Branch, worktree and PR conventions

- One spec per session. Branch name starts with the spec ID, e.g. `eq-21-sue-variants`, `opt-01-option-infrastructure`, `t0-pit-test-kit`.
- Create a worktree per session. The desktop app's default location is `.claude/worktrees/<name>` (git-ignored). The convention in `AGENTS.md` (sibling `../<repo>-worktrees/<slug>`) also works; pick one and stay with it.
- Start the session with a brief that says: the spec file path; "touch only files in the spec's NEW list plus its tests"; "do not edit trunk-owned files"; "do not commit leaderboard/REPO_MAP"; "run `python -m pytest tests -q` and show all pins unchanged".
- Name the strategy registration after the spec (`eq21_...`) so `results/<strategy>/` folders never collide between sessions.
- Merge order: trunk PRs first. A leaf rebases/merges `origin/main` before opening its PR. Merge leaf PRs one at a time; regenerate the leaderboard on `main` afterward.
- Never let two sessions edit the same spec file; a spec's status line (`Status: planned -> in progress -> done/null result`) is changed only by the session that owns it, in its own PR.

## 4. Data in worktrees (read this before running anything)

`data/` is git-ignored, so a fresh worktree has none of it. The repo has been burned by symlinks and junctions into data (`docs/HISTORY.md`, the pre-commit hook blocks tracked symlinks), so:

- Do not symlink or junction `data/`. Do not copy 30+ GB per worktree.
- Point a worktree at the canonical data with the existing path overrides in `pead/common/paths.py` (`PEAD_DATA_DIR`, `OPTIONMETRICS_DIR`, ...). Example for the worktree's shell (PowerShell):

  ```powershell
  $env:PEAD_DATA_DIR = "C:\Users\hrudi\Documents\BEI\PEAD_Trading\data"
  $env:PEAD_RUNS_DIR = "$PWD\results"        # results stay in the worktree
  ```
  `OPTIONMETRICS_DIR` already defaults to `D:/OptionMetrics/parquet`.
- **Treat canonical `data/` as read-only for leaf sessions.** Backtest runs read it and write only to the worktree's `results/`. Anything that rebuilds derived files (`python -m pead prep ...`, anything with `--force`) must not run against the canonical directory; it runs in its own vintage directory (copy the small derived inputs to a new folder next to `_pead_consolidation\vintage_*`, point `PEAD_DATA_DIR` there, record the vintage name in the PR). Data-changing trunk work (T2) always uses this route and re-pins once.
- Results reproduce only under Python 3.12 / pandas 3. A worktree's own `.venv` must match.
- Keep `D:` attached while sessions run; two sessions reading the 32 GB OptionMetrics extract at once will thrash the disk, so at most one heavy options scan at a time.

## 5. Machine limits

16 GB RAM. At most two heavy backtests at once, and none while the local Qwen worker is loaded (`AGENTS.md` concurrency rules). Delegation to `qwen-delegate` stays sequential across all sessions combined.

## 6. Retiring a worktree without losing `data/`

Never `git worktree remove` (it treats ignored data as disposable) and never delete the folder. Use `scripts/retire_worktree.sh`, which follows the repo's own rule ("move it aside, then prune"):

```bash
bash scripts/retire_worktree.sh <worktree-path>            # dry run: prints every check and what it would do
bash scripts/retire_worktree.sh <worktree-path> --apply    # performs it
```

It refuses unless: tracked and untracked (non-ignored) files are clean; the branch is merged into `origin/main` or fully pushed; and it never touches the main checkout. It then reports the size and file count of ignored content, **moves** the whole folder (ignored data included) to the archive root (`C:\Users\hrudi\Documents\BEI\_pead_archived_worktrees\` by default, override with `PEAD_ARCHIVE_DIR`), runs `git worktree prune`, and prints the `git branch -d` command (safe delete, refuses unmerged work) rather than running it. Nothing is deleted; delete the archive folder yourself when you are sure.

## 7. Currently registered worktrees (2026-10-27, for reference)

All have clean tracked status. Those not merged hold unique work; do not retire them without reading `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md`.

| Worktree | Branch | Commits ahead of main |
|---|---|---|
| `.claude/worktrees/agents-md-project-notes-b44691` | `claude/agents-md-project-notes-b44691` | 0 (merged) |
| `.claude/worktrees/strategy-evaluation-roi-74c7dd` | `claude/strategy-evaluation-roi-74c7dd` | 0 (merged; REPO_MAP says its data is worth keeping) |
| `.claude/worktrees/strategy-analysis-backtest-481b86` | `docs/repomap-final-2` | 0 (merged; hosts corrected position files the paper builder reads, keep) |
| `.claude/worktrees/options-next-study` | `codex/pead-options-next-study` | 3 |
| `.claude/worktrees/options-risk-synthesis` | `codex/options-risk-synthesis` | 20 (local only) |
| `.claude/worktrees/options-risk-synthesis-publication` | `codex/options-risk-synthesis-review` | 20 |
| `.claude/worktrees/return-discovery` | `codex/return-discovery-20` | 26 (local only) |
| `~/.codex/worktrees/pead-options-optimization/PEAD_Trading` | `codex/pead-options-optimization` | 1 |
| `~/.codex/worktrees/selective-earnings-research/PEAD_Trading` | `codex/selective-earnings-research` | 9 |
