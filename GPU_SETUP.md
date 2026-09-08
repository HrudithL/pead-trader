# GPU box setup: from a bare Linux machine to a running backtest

This is the complete, step-by-step path from a fresh Linux checkout (the 5090 box) to
`scripts/run_all_strategies.py` actually running every strategy in this project. Two things have
to arrive on that machine: **the code** (via `git`, normal) and **the data** (via the physical
OptionMetrics drive, *not* `git` -- see "Why the data isn't in git" below).

Everything here was written after actually validating the pieces that could be validated without
the 5090 itself: the raw WRDS data was copied and verified (file-count-matched) onto the drive,
the symlink step below was run for real against that copy in a real Linux environment (WSL2) and
confirmed readable, and the GPU-tiered scripts this whole setup exists to run were already
confirmed working end-to-end on a real CUDA GPU (a Colab T4 -- see `PEAD_Strategies_Overview.pdf`
and the conversation history for that run). The one thing *not* directly tested is this exact
script on the exact 5090 hardware -- do the dry run in Step 6 before trusting a long unattended run.

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

## Step 3: Python environment

```bash
python3 -m venv .venv && source .venv/bin/activate     # or your preferred env manager
pip install -r requirements.txt -r requirements-gpu.txt
```

`requirements-gpu.txt` installs `torch`, `cupy` (a CUDA build -- if the default wheel doesn't match
this box's CUDA version, see that file's own comment for the versioned alternative), and `optuna`.

## Step 4: confirm the GPU is actually visible

```bash
nvidia-smi
python3 -c "import cupy as cp, torch; print(cp.cuda.runtime.getDeviceCount(), torch.cuda.is_available())"
```

Both should report the real GPU. If `cupy` fails to import, its error message says exactly what's
missing (see `scripts/common/gpu.py`'s `get_backend()`) -- it's designed to fail loudly here rather
than silently falling back to CPU.

## Step 5: preview before committing to a long run

```bash
python scripts/run_all_strategies.py --dry-run
```

This prints every stage across both equity and options -- what would run, what's already done,
what's blocked -- with zero side effects. Confirm the stage list and `--jobs` auto-sizing (shown at
the top of the output, sized off this box's real CPU count and RAM) look sane before the real run.

## Step 6: the real run

```bash
tmux new -s pead                                    # survives a dropped SSH session
python scripts/run_all_strategies.py --device cuda
```

This is the single command: it builds every equity strategy (1-8) and every options tier (1-4)
from the raw data now linked in via Step 2, one stage at a time, each one given the whole
machine (see `scripts/run_all_strategies.py`'s own docstring for exactly why it's sequential
rather than concurrent). Detach from tmux (`Ctrl-b d`) and walk away; `tmux attach -t pead` to
check back in.

Useful variants:
```bash
python scripts/run_all_strategies.py --device cuda --include-reports   # also build the PDF reports
python scripts/run_all_strategies.py --only eq_35_finalize_strategy8   # just this stage + its deps
python scripts/run_all_strategies.py --device cuda --force             # rebuild everything, ignore checkpoints
```

## While it's running / after a crash

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
