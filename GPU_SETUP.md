# GPU box setup: from a bare Linux machine to a running backtest

**If you are an AI agent (e.g. Claude Code) reading this file to set the project up on a new
Linux GPU machine: this document is complete and self-contained.** Follow the numbered steps in
order, run every command shown, and check the stated success condition before moving to the next
step. Don't skip Step 6 (validation before the real run) even under time pressure -- it's what
catches a broken environment in seconds instead of hours into an unattended run. If a step's
success condition isn't met, stop and diagnose there rather than continuing -- see "If something
goes wrong" at the end for what each kind of failure usually means, and don't guess past a genuine
blocker (e.g. no GPU drivers, no physical access to the drive) -- report back what's blocking
instead.

This is the complete, step-by-step path from a fresh Linux checkout (the 5090 box, or any
CUDA-capable Linux machine) to `pead` (the data build and every backtest) running in
this project. Two things have to arrive on that machine: **the code** (via `git`, normal) and
**the data** (via the physical OptionMetrics drive, *not* `git` -- see "Why the data isn't in git"
below).

**Quick reference** (the full command sequence, if you just want to see it end to end before
reading the detail below):
```bash
git clone <this repo's remote> PEAD_Trading && cd PEAD_Trading
lsblk                                                  # find where the drive mounted
./scripts/setup_gpu_box.sh /media/<you>/OptionMetrics  # prints export lines, touches nothing in data/
# copy/paste its printed PEAD_*_DIR and OPTIONMETRICS_DIR export lines into ~/.bashrc, then:
source ~/.bashrc
python pead/prep/verify_raw_data.py --check              # confirms the drive's copy matches canonical
git config core.hooksPath scripts/git-hooks             # blocks ever committing a symlink
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-gpu.txt
nvidia-smi && python3 -c "import cupy as cp, torch; print(cp.cuda.runtime.getDeviceCount(), torch.cuda.is_available())"
python -m pead prep --list
python pead/prep/options/optimal_contract_selector.py --device cuda --mock-data --smoke-test
tmux new -s pead
python -m pead prep --all --device cuda && python -m pead run all
```

Everything here was written after actually validating the pieces that could be validated without
the 5090 itself: the raw WRDS data was copied and verified (file-count-matched) onto the drive,
the environment-variable step below was run for real against that copy in a real Linux
environment (WSL2) and confirmed readable, and the GPU-tiered scripts this whole setup exists to run were already
confirmed working end-to-end on a real CUDA GPU (a Colab T4 -- see `PEAD_Strategies_Overview.pdf`
and the conversation history for that run). **What has NOT been directly tested: this exact
orchestrator script on the actual 5090 hardware, and three options-pipeline stages against the
real, full-scale OptionMetrics data** -- `opt_41_daily_option_paths` (the real per-year daily-quote
scan, the heaviest OM read in the whole pipeline), `opt_42_gpu_exit_optimizer` (which consumes
41's output), and `opt_44_ml_contract_selector`. All three have only ever been run on mock or
small-scale data by anyone, on any machine. If one of these three fails on first contact with real
data, that's expected new territory, not necessarily something wrong with your setup -- see "If
something goes wrong" below for how to tell the difference and what to do about it.

## Why the data isn't in git

`.gitignore` already excludes the raw WRDS pull with the comment "large, and not ours to
redistribute" -- this project's WRDS/IBES/CRSP/Compustat access is licensed for your own research
use, and putting it in a git repo (especially if that repo is ever pushed somewhere with broader
access) crosses from "using it" into "redistributing it." The OptionMetrics IvyDB extract has the
same restriction and, per the README, was never even meant to be copied off its own physical
drive. Moving both onto that same drive and carrying it between machines you control for your own
use is a different thing entirely from redistribution -- but it's still your license to manage, so
this setup keeps the data on the drive, not in git, by design.

## What's on the drive

```
<drive>/
  parquet/                          the OptionMetrics IvyDB extract (already there, unchanged)
  pead_wrds_data/
    raw_wrds/                       raw WRDS pull (CRSP dsf_*, Compustat funda/fundq, etc.)
    normalized_equity/              equity_1995.parquet .. equity_2014.parquet, market_benchmark.parquet
    earnings/                       ibes_clean_1995_2014.parquet
    events/                         equity_events_1996_2013.parquet
    metadata/                       OM trading calendar, universe links, etc.
    results/                        event_classification_features.parquet
```

`pead_wrds_data/` is a straight copy of this repo's own `data/{raw_wrds,normalized_equity,
earnings,events,metadata,results}/` folders, staged here once from the machine that holds the
original WRDS pull. If you ever refresh the underlying WRDS pull, redo that copy -- there's no
sync mechanism, this is a one-time (or refresh-when-you-refresh-the-data) staging step. Verify
the staged copy against the git-tracked `data/raw_data_manifest.json` before every real run (see
"Step 2b" below) rather than trusting it by assumption.

## Never symlink data into this repo

An earlier version of this setup created symlinks at `data/{raw_wrds,normalized_equity,earnings,
events,metadata,results}` pointing at the drive. One of those symlinks was accidentally
`git add`ed and committed. On a machine with `core.symlinks=false` (the default for Git for
Windows without admin/developer mode), checking out a commit that tracks a symlink at a path
where a **real directory already exists** silently **deletes that real directory** to place the
symlink's plain-text placeholder there -- no prompt, no error. That destroyed this project's local
raw WRDS pull once already.

The fix is structural, not just "be careful": every one of the six raw-data locations is a plain
environment-variable override in `pead/common/paths.py` (`PEAD_RAW_WRDS_DIR`,
`PEAD_RAW_EQUITY_DIR`, `PEAD_EARNINGS_DIR`, `PEAD_EVENTS_DIR`, `PEAD_METADATA_DIR`,
`PEAD_RESULTS_DIR`), so **`data/` never needs to contain anything for these six folders on the GPU
box at all**. `scripts/setup_gpu_box.sh` only prints the export lines below now -- it does not
create, symlink, or touch anything under `data/`. Do not reintroduce a symlink there. As a second
line of defense, `.gitignore`'s entries for these paths have no trailing slash (so they also match
a symlink or stray file of the same name, not just a real directory), and
`scripts/git-hooks/pre-commit` refuses to commit any symlink anywhere in the repo -- see "Enable
the pre-commit safety hook" below. Run that setup step on every machine, including this one.

## Enable the pre-commit safety hook

One command, once per clone (git hooks are local to a checkout, not carried by `git clone` itself,
so this has to be run again on every machine including this one):

```bash
git config core.hooksPath scripts/git-hooks
```

**Success condition:** `git config --get core.hooksPath` prints `scripts/git-hooks`. From then on,
`git commit` refuses any commit that stages a symlink, anywhere in the repo, with an explanation
of why -- see `scripts/git-hooks/pre-commit`'s own comment for the full incident this defends
against.

## Step 1: get the code

```bash
git clone <this repo's remote> PEAD_Trading   # or: cd PEAD_Trading && git pull
cd PEAD_Trading
```
**Success condition:** `ls` shows `pead/`, `data/`, `README.md`, `GPU_SETUP.md`.

## Step 2: attach the drive and point the code at it (env vars, no symlinks)

Plug in the drive. Find where it mounted:

```bash
lsblk            # or: df -h
```

It'll show up somewhere like `/media/<you>/OptionMetrics` (auto-mount) or wherever you mounted it
manually. Then, from the repo root:

```bash
./scripts/setup_gpu_box.sh /media/<you>/OptionMetrics
```

This validates the drive has both the OptionMetrics extract and the staged WRDS pull, then prints
seven `export` lines (`PEAD_RAW_WRDS_DIR`, `PEAD_RAW_EQUITY_DIR`, `PEAD_EARNINGS_DIR`,
`PEAD_EVENTS_DIR`, `PEAD_METADATA_DIR`, `PEAD_RESULTS_DIR`, `OPTIONMETRICS_DIR`) pointing directly
at the drive's copies. It does **not** create, symlink, or touch anything under this repo's
`data/` directory -- see "Never symlink data into this repo" above for why that matters. Copy its
printed lines into `~/.bashrc` so they survive new shells:

```bash
./scripts/setup_gpu_box.sh /media/<you>/OptionMetrics >> ~/.bashrc   # appends the export lines
source ~/.bashrc
```
(Review what actually got appended -- the script also prints its validation messages to stdout,
so skim `~/.bashrc`'s new lines and drop anything that isn't an `export ...` line before sourcing.)

**Success condition:** the script printed "all six raw-data subfolders found on the drive" (not a
`WARNING` for any of them). Then confirm a file is actually readable through the env var:
`ls -la "$PEAD_RAW_EQUITY_DIR/equity_1995.parquet"` should show a real file.

## Step 2b: verify the staged copy actually matches canonical

This is the check the earlier symlink incident was missing -- the staged copy on the drive can
look complete (every folder present, files readable) while still being a different vintage of the
data than whatever the currently-published results were built from, with nothing in the pipeline
itself able to tell the difference. `data/raw_data_manifest.json` (committed to git, written once
from the canonical machine via `python pead/prep/verify_raw_data.py --write`) is the source of
truth. Check the drive's copy against it before trusting any real run:

```bash
python pead/prep/verify_raw_data.py --check
```

**Success condition:** it prints `OK <folder>: N files match` for all six folders and exits 0. If
it instead prints `MISMATCH` for any folder, **stop** -- do not run the pipeline for real against
this data, and do not commit any of its output. Report back which folder(s) mismatched and how
(`missing`/`extra`/`changed` files) rather than proceeding; that's exactly the signal that the
drive needs re-staging from the canonical machine (`python pead/prep/verify_raw_data.py --write`
there first, to refresh the manifest, if the canonical data itself has legitimately changed).

## Step 3: Python environment

```bash
python3 -m venv .venv && source .venv/bin/activate     # or your preferred env manager
pip install -r requirements.txt -r requirements-gpu.txt
```
**Success condition:** both install commands exit 0. `requirements-gpu.txt` installs `torch`,
`cupy` (a CUDA build -- if the default wheel doesn't match this box's CUDA version, see that
file's own comment for the versioned alternative), and `optuna`.

## Step 4: confirm the GPU is actually visible

```bash
nvidia-smi
python3 -c "import cupy as cp, torch; print(cp.cuda.runtime.getDeviceCount(), torch.cuda.is_available())"
```
**Success condition:** `nvidia-smi` prints a real GPU (name, driver version, CUDA version), and
the Python line prints something like `1 True`. If `cupy` fails to import, its error message says
exactly what's missing (see `pead/common/gpu.py`'s `get_backend()`) -- it's designed to fail
loudly here rather than silently falling back to CPU. **If `nvidia-smi` itself fails or isn't
found**, that means the NVIDIA driver isn't installed on this box -- that's an OS-level
prerequisite outside this repo's scope (not something a `pip install` fixes). Stop here and flag
it rather than trying to work around it; installing/fixing GPU drivers is a system-administration
step someone with physical/root access to this specific box needs to handle.

## Step 5: preview before committing to a long run

```bash
python -m pead prep --list
```
**Success condition:** it prints every data-build stage with its dependencies and whether its outputs already
exist (`built yes/no`), and exits without a traceback. It runs nothing. The stages are the dependency graph in
`pead/prep/stages.py`: eight equity stages (events -> deciles -> decay cells -> positions -> volatility features)
and ten options stages (calendar -> link -> scan chains -> return distributions -> Kelly selection -> forward prices).

## Step 6: validate the GPU path with mock data before the real run

```bash
python pead/prep/options/optimal_contract_selector.py --device cuda --mock-data --smoke-test
python -m pytest tests -q
```
**Success condition:** the selector scores a synthetic chain on the GPU in about a second and writes
`data/event_options/optimal_contracts_smoke.parquet` (a non-canonical file the real pipeline ignores); the
tests pass (the reproduction tests are skipped if the real inputs are not linked in yet). A failure here is a
Python-environment or GPU problem (Steps 3-4), independent of any data issue.

## Step 7: the real run

```bash
tmux new -s pead                                    # survives a dropped SSH session
python -m pead prep --all --device cuda --jobs 8    # build every missing input
python -m pead run all                              # backtest every strategy -> results/
python -m pead compare                              # comprehensive report -> results/comprehensive/
```

`prep --all` runs independent stages concurrently, serializes the GPU stage (the Kelly selector) to one at a time
and caps the OptionMetrics disk scans with `--io-parallel` (they contend for one drive). It skips any stage whose
declared outputs exist, so re-running the same command resumes after a crash or reboot; the per-year scan stages
checkpoint one file per year. The backtests (`pead run`) are NumPy code that take seconds each and do not use the GPU.

**Success condition:** `prep` exits 0 and `logs/prep_status.json` shows every stage `done` or `skipped`;
`python -m pead run all` prints one result line per strategy.

Useful variants:
```bash
python -m pead prep options/forward_prices_optimal --force          # redo one stage's outputs (name as in --list)
python -m pead prep forward_prices_optimal --force --with-deps      # ...and whatever it needs first
python -m pead prep --all --force                                   # rebuild everything, ignore checkpoints
```

## If something goes wrong

- **Live status**: `cat logs/prep_status.json` -- each stage's state, rewritten as stages start and finish.
- **Why did stage X fail?**: `cat logs/prep/<stage>.log` -- that stage's complete output. Most real failures are
  out-of-memory (reduce `--jobs`), a full disk, or a transient OptionMetrics read error (the scan helper retries
  those automatically; a *consistent* failure on one year is worth a closer look -- `opprcd2011` is known to be corrupt
  and is excluded).
- **Resuming**: run the same command again. Finished stages are skipped; only what did not finish is retried.
- **A stage fails immediately with `unrecognized arguments`**: `pead/prep/stages.py` passes `--device`, `--jobs` and
  `--force` only to scripts whose `flags` declare them; compare the stage's `flags` with the script's own `--help`.

## When it's done

Small results land in `results/` (tracked) and `data/*.csv` / `data/*.json`; pull them back with:

```bash
git add results/ data/*.csv data/*.json pead/
git commit -m "Real GPU-box run: <what changed>"
git push
```

The large intermediates (`data/*.parquet`, `data/event_options/`, `results/*/positions.parquet`) are gitignored on purpose:
they are fully regenerable from the raw data, so there is nothing to sync back for those.
