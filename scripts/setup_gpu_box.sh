#!/usr/bin/env bash
# One-time setup on the Linux GPU box (the 5090, or any fresh Linux checkout): links this repo's
# data/ directory at the raw WRDS pull + OptionMetrics extract that live on the external drive,
# instead of copying ~1GB+31GB onto the box's own disk. Mirrors the NTFS junctions the main
# Windows checkout already uses for the same reason (see README's "Data" section) -- symlinks are
# the Linux equivalent, same idea: one real copy of the data, on the portable drive, referenced
# from wherever it's plugged in.
#
# Run from the repo root, after physically attaching the drive and noting where it mounted:
#   ./scripts/setup_gpu_box.sh /media/you/OptionMetrics
#
# What it expects to find on that drive (see GPU_SETUP.md for how this folder layout got there):
#   <drive>/parquet/                 the OptionMetrics IvyDB extract (unchanged, already present)
#   <drive>/pead_wrds_data/raw_wrds/           )
#   <drive>/pead_wrds_data/normalized_equity/  )
#   <drive>/pead_wrds_data/earnings/           )  the raw WRDS/IBES/CRSP/Compustat pull, staged
#   <drive>/pead_wrds_data/events/             )  onto this drive instead of committed to git --
#   <drive>/pead_wrds_data/metadata/           )  see .gitignore's "not ours to redistribute" note
#   <drive>/pead_wrds_data/results/            )
#
# What this script does:
#   1. Validates the drive actually has both pieces (fails loudly, not silently, if the drive
#      path is wrong or the data isn't staged there yet).
#   2. Symlinks data/{raw_wrds,normalized_equity,earnings,events,metadata,results} -> the drive's
#      copies. Refuses to overwrite a real (non-symlink) directory already there, unless --force.
#   3. Prints the OPTIONMETRICS_DIR export line to add to your shell profile (this script does not
#      edit .bashrc itself -- one more thing you might not want silently modified).
#
# This script does NOT install Python packages or touch the GPU driver -- see GPU_SETUP.md for
# those steps. It only wires up the two data sources.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <path-where-the-drive-is-mounted> [--force]" >&2
  echo "example: $0 /media/\$USER/OptionMetrics" >&2
  exit 1
fi

DRIVE="$1"
FORCE="${2:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRDS="$DRIVE/pead_wrds_data"

if [[ ! -d "$DRIVE" ]]; then
  echo "ERROR: '$DRIVE' is not a directory -- is the drive actually mounted there? Check 'df -h' / 'lsblk'." >&2
  exit 1
fi
if [[ ! -d "$DRIVE/parquet" ]]; then
  echo "ERROR: '$DRIVE/parquet' not found -- this doesn't look like the OptionMetrics drive." >&2
  exit 1
fi
if [[ ! -d "$WRDS" ]]; then
  echo "ERROR: '$WRDS' not found -- the raw WRDS pull hasn't been staged onto this drive yet." >&2
  echo "       See GPU_SETUP.md's 'Staging the raw data' section (this is a one-time step done" >&2
  echo "       from the Windows machine that holds the original WRDS pull, not from here)." >&2
  exit 1
fi

echo "drive OK: $DRIVE"
mkdir -p "$REPO_ROOT/data"

link_one() {
  local name="$1"
  local target="$WRDS/$name"
  local link="$REPO_ROOT/data/$name"

  if [[ ! -d "$target" ]]; then
    echo "  SKIP $name: '$target' not present on the drive"
    return
  fi
  if [[ -L "$link" ]]; then
    rm "$link"
  elif [[ -e "$link" ]]; then
    if [[ "$FORCE" != "--force" ]]; then
      echo "  SKIP $name: '$link' already exists and is not a symlink (pass --force to replace it)"
      return
    fi
    rm -rf "$link"
  fi
  ln -s "$target" "$link"
  echo "  linked data/$name -> $target"
}

echo "linking data/ subfolders to the drive's staged WRDS pull..."
for d in raw_wrds normalized_equity earnings events metadata results; do
  link_one "$d"
done

echo
echo "done. Add this to your shell profile (~/.bashrc) so it's set for every future session:"
echo
echo "  export OPTIONMETRICS_DIR=\"$DRIVE/parquet\""
echo
echo "...or export it just for this shell:"
echo "  export OPTIONMETRICS_DIR=\"$DRIVE/parquet\""
