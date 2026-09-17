#!/usr/bin/env bash
# One-time setup on the Linux GPU box (the 5090, or any fresh Linux checkout): points this repo's
# path constants (scripts/common/paths.py) at the raw WRDS pull + OptionMetrics extract that live
# on the external drive, instead of copying ~1GB+31GB onto the box's own disk.
#
# This prints environment-variable exports rather than creating anything under data/ -- on
# purpose. An earlier version of this script symlinked data/{raw_wrds,...} to the drive, and one
# of those symlinks ended up `git add`ed and committed by mistake. On a machine with
# `core.symlinks=false` (the default on Windows without admin/developer mode), checking out a
# commit that tracks a symlink at a path where a REAL directory already exists silently deletes
# that real directory to place the symlink's text placeholder there -- which is exactly what
# destroyed this project's local raw WRDS pull once already. Since every one of these locations is
# already a plain environment-variable override in scripts/common/paths.py, there is no need for
# data/ to contain anything at all for this to work -- so this script no longer touches data/,
# and there is nothing left for `git add`/`git checkout` to ever catch, accidentally track, or
# destroy. See the "Never symlink data into this repo" section of GPU_SETUP.md.
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
#   2. Prints the PEAD_*_DIR and OPTIONMETRICS_DIR export lines for your shell profile -- it does
#      not edit .bashrc itself (one more thing you might not want silently modified) and it does
#      not create, symlink, or otherwise touch anything under this repo's data/ directory.
#
# This script does NOT install Python packages or touch the GPU driver -- see GPU_SETUP.md for
# those steps. It only prints the two data sources' locations.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <path-where-the-drive-is-mounted>" >&2
  echo "example: $0 /media/\$USER/OptionMetrics" >&2
  exit 1
fi

DRIVE="$1"
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
  echo "       See GPU_SETUP.md's 'What's on the drive' section (this is a one-time step done" >&2
  echo "       from the Windows machine that holds the original WRDS pull, not from here)." >&2
  exit 1
fi

echo "drive OK: $DRIVE"
echo

missing=0
for d in raw_wrds normalized_equity earnings events metadata results; do
  if [[ ! -d "$WRDS/$d" ]]; then
    echo "  WARNING: '$WRDS/$d' not present on the drive -- that stage's scripts will fail until it is." >&2
    missing=1
  fi
done
[[ "$missing" -eq 0 ]] && echo "all six raw-data subfolders found on the drive."

echo
echo "Add these to your shell profile (~/.bashrc) so they're set for every future session --"
echo "no symlinks, nothing written under this repo's data/ directory:"
echo
echo "  export PEAD_RAW_WRDS_DIR=\"$WRDS/raw_wrds\""
echo "  export PEAD_RAW_EQUITY_DIR=\"$WRDS/normalized_equity\""
echo "  export PEAD_EARNINGS_DIR=\"$WRDS/earnings\""
echo "  export PEAD_EVENTS_DIR=\"$WRDS/events\""
echo "  export PEAD_METADATA_DIR=\"$WRDS/metadata\""
echo "  export PEAD_RESULTS_DIR=\"$WRDS/results\""
echo "  export OPTIONMETRICS_DIR=\"$DRIVE/parquet\""
echo
echo "...or export them just for this shell (the same lines above, without adding them to .bashrc)."
