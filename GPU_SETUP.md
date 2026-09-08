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
CUDA-capable Linux machine) to `scripts/run_all_strategies.py` actually running every strategy in
this project. Two things have to arrive on that machine: **the code** (via `git`, normal) and
**the data** (via the physical OptionMetrics drive, *not* `git` -- see "Why the data isn't in git"
below).

**Quick reference** (the full command sequence, if you just want to see it end to end before
reading the detail below):
```bash
git clone <this repo's remote> PEAD_Trading && cd PEAD_Trading
lsblk                                                  # find where the drive mounted
./scripts/setup_gpu_box.sh /media/<you>/OptionMetrics
echo 'export OPTIONMETRICS_DIR="/media/<you>/OptionMetrics/parquet"' >> ~/.bashrc && source ~/.bashrc
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-gpu.txt
nvidia-smi && python3 -c "import cupy as cp, torch; print(cp.cuda.runtime.getDeviceCount(), torch.cuda.is_available())"
python scripts/run_all_strategies.py --dry-run
python scripts/run_all_strategies.py --device cuda --mock-data --smoke-test
tmux new -s pead
python scripts/run_all_strategies.py --device cuda
```

Everything here was written after actually validating the pieces that could be validated without
the 5090 itself: the raw WRDS data was copied and verified (file-count-matched) onto the drive,
the symlink step below was run for real against that copy in a real Linux environment (WSL2) and
confirmed readable, and the GPU-tiered scripts this whole setup exists to run were already
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
sync mechanism, this is a one-time (or refresh-when-you-refresh-the-data) staging step.

## Step 1: get the code

```bash
git clone <this repo's remote> PEAD_Trading   # or: cd PEAD_Trading && git pull
cd PEAD_Trading
```
**Success condition:** `ls` shows `scripts/`, `data/`, `README.md`, `GPU_SETUP.md`.

## Step 2: attach the drive and link the data in

Plug in the drive. Find where it mounted:

```bash
lsblk            # or: df -h
```

It'll show up somewhere like `/media/<you>/OptionMetrics` (auto-mount) or wherever you mounted it
manually. Then, from the repo root:

```bash
./scripts/setup_gpu_box.sh /media/<you>/OptionMetrics
```

This symlinks `data/{raw_wrds,normalized_equity,earnings,events,metadata,results}` to their copies
on the drive (the same idea as the NTFS junctions the main Windows checkout uses, just the Linux
equivalent), and prints the `OPTIONMETRICS_DIR` line to export. It refuses to overwrite a real
(non-symlink) `data/` subfolder that's already there -- pass `--force` if you actually want to
replace one. Add the printed export to `~/.bashrc` so it survives new shells:

```bash
echo 'export OPTIONMETRICS_DIR="/media/<you>/OptionMetrics/parquet"' >> ~/.bashrc
source ~/.bashrc
```
**Success condition:** `./scripts/setup_gpu_box.sh` printed `linked data/<name> -> ...` for all
six of `raw_wrds`, `normalized_equity`, `earnings`, `events`, `metadata`, `results` (not `SKIP` --
if it skipped, either the drive path or the target folder name is wrong, or a real
`data/<name>` already exists there and needs `--force` or manual removal first). Then confirm a
file is actually readable through the link: `ls -la data/normalized_equity/equity_1995.parquet`
should show a real file, not a broken-link error.

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
exactly what's missing (see `scripts/common/gpu.py`'s `get_backend()`) -- it's designed to fail
loudly here rather than silently falling back to CPU. **If `nvidia-smi` itself fails or isn't
found**, that means the NVIDIA driver isn't installed on this box -- that's an OS-level
prerequisite outside this repo's scope (not something a `pip install` fixes). Stop here and flag
it rather than trying to work around it; installing/fixing GPU drivers is a system-administration
step someone with physical/root access to this specific box needs to handle.

## Step 5: preview before committing to a long run

```bash
python scripts/run_all_strategies.py --dry-run
```
**Success condition:** it prints all 41 stages with a plan for each (`WOULD RUN`, `output already
exists -- skipping`, etc.) and exits without a traceback. This has zero side effects -- it runs
nothing. Confirm the stage list and `--jobs` auto-sizing (shown at the top of the output, sized
off this box's real CPU count and RAM) look sane before going further.

## Step 6: validate the real path with mock data before the real run

```bash
python scripts/run_all_strategies.py --device cuda --mock-data --smoke-test
```
**Success condition:** this actually EXECUTES a shrunk, synthetic-data version of every
mock-capable stage (Strategy 8's GPU tiers, the options GPU tiers) end to end in well under a
minute, exercising the real subprocess/GPU/checkpoint machinery with no dependency on the raw data
being linked in correctly yet. Check the final summary block it prints: every mock-capable stage
should say `done`, and every stage that needs real (non-mock) data should say
`skipped_mock_unsupported` -- not `failed`. **A `failed` stage here means something is wrong with
the Python environment or GPU setup itself (Steps 3-4), independent of any data issue** -- worth
fixing before spending hours on the real run in Step 7. Check
`logs/run_all/<stage>.failure.json` for specifics (see "If something goes wrong" below).

## Step 7: the real run

```bash
tmux new -s pead                                    # survives a dropped SSH session
python scripts/run_all_strategies.py --device cuda
```

This is the single command: it builds every equity strategy (1-8) and every options tier (1-4)
from the raw data now linked in via Step 2, one stage at a time, each one given the whole
machine (see `scripts/run_all_strategies.py`'s own docstring for exactly why it's sequential
rather than concurrent). Detach from tmux (`Ctrl-b d`) and walk away; `tmux attach -t pead` to
check back in. **This can genuinely take hours to days** (the options Tier 3/4 GPU sweeps are the
largest tensor searches in the project) -- that is expected, not a hang; use the heartbeat file
below to tell the difference between "still working" and "actually stuck."

**Success condition:** the final line printed is `finished. per-stage status: logs/run_all_status.json`
(exit code 0), with no stage left in `logs/run_all_status.json` showing `"state": "failed"`. It's
normal and expected for some stages to show `skipped_no_script` or `skipped_manual` depending on
flags -- only `failed` needs attention (see below).

Useful variants:
```bash
python scripts/run_all_strategies.py --device cuda --include-reports   # also build the PDF reports
python scripts/run_all_strategies.py --only eq_35_finalize_strategy8   # just this stage + its deps
python scripts/run_all_strategies.py --device cuda --force             # rebuild everything, ignore checkpoints
```

## If something goes wrong

- **Live status**: `cat logs/run_all_status.json` -- every stage's state (running/done/failed),
  updated immediately after each one finishes, not batched at the end.
- **Is it actually alive or hung?**: `cat logs/run_all_heartbeat.json` -- updated every 20s while a
  stage runs. If that timestamp is stale but the process is still in `ps`, it's genuinely stuck.
- **Why did stage X fail?**: `cat logs/run_all/<stage>.failure.json` -- exit code, last 60 lines of
  its own output, and a hardware snapshot (RAM available, disk free, GPU status via `nvidia-smi`)
  captured at the moment of failure. This is usually enough to tell OOM / disk-full / a real bug
  apart without re-running anything.
- **Resuming**: just run the exact same command again. Every finished stage is skipped (its output
  file already exists); only what didn't finish gets retried.
- **`opt_41_daily_option_paths`, `opt_42_gpu_exit_optimizer`, or `opt_44_ml_contract_selector`
  specifically failing on real data**: these three are the ones flagged at the top of this document
  as never having been run at real, full scale by anyone before -- a failure here is plausibly a
  genuine bug hitting real data for the first time, not a setup mistake. Read the failure.json
  first: if it's an OOM (check `hardware_at_failure.ram_available_gb`), a raw OptionMetrics
  read error (`opt_41` retries a transient one automatically -- see its own code -- but a
  *consistent* read failure on one year is worth a closer look), or a shape/dtype error deep in a
  GPU kernel, that's a real finding worth fixing in the script itself, not retrying blindly. Every
  other stage in the catalog has already been run successfully on either real data or a real GPU
  (see the top of this document and `PEAD_Strategies_Overview.pdf` for exactly which), so a failure
  elsewhere is more likely an environment/setup issue from Steps 1-6 above.
- **A stage fails immediately with an `argparse`/`unrecognized arguments` error**: this is a bug in
  `scripts/run_all_strategies.py`'s own stage catalog (passing a flag a script doesn't actually
  accept) -- exactly this class of bug was already caught and fixed once during testing (see the
  script's git history). Check the failing script's own `--help` output, compare against what
  `run_all_strategies.py`'s `build_command()` passes it, and fix the mismatch in the catalog.

## When it's done

Results land in `data/*.csv` / `data/*.json` (small, git-trackable) and `reports/*.pdf` (if you
passed `--include-reports`). Pull those back to wherever you want them tracked:

```bash
git add data/*.csv data/*.json reports/*.pdf scripts/
git commit -m "Real GPU-box run: <what changed>"
git push
```

The large intermediates (`data/*.parquet` -- position files, feature panels, the daily option
price paths, etc.) are gitignored on purpose: they're fully regeneratable from the raw data by
re-running the pipeline, so there's nothing to sync back for those.
