#!/usr/bin/env bash
# Retire a git worktree WITHOUT losing its git-ignored data/.
# Never uses `git worktree remove` (it treats ignored files as disposable) and never deletes anything:
# the folder is MOVED to an archive root, then git's registration is pruned.
# Usage: scripts/retire_worktree.sh <worktree-path> [--apply]      (default: dry run)
set -euo pipefail

wt="${1:-}"; apply="${2:-}"
[ -n "$wt" ] || { echo "usage: $0 <worktree-path> [--apply]"; exit 2; }
[ -d "$wt" ] || { echo "no such directory: $wt"; exit 2; }
wt="$(cd "$wt" && pwd)"
main="$(git -C "$wt" worktree list --porcelain | awk '/^worktree /{print substr($0,10); exit}')"
main="$(cd "$main" && pwd)"
[ "$wt" != "$main" ] || { echo "REFUSING: $wt is the main checkout"; exit 1; }

archive="${PEAD_ARCHIVE_DIR:-/c/Users/hrudi/Documents/BEI/_pead_archived_worktrees}"
branch="$(git -C "$wt" rev-parse --abbrev-ref HEAD)"
fail=0
say() { printf '%s\n' "$*"; }
bad() { say "FAIL: $*"; fail=1; }

git -C "$main" fetch -q origin || say "warn: fetch failed, using cached origin/main"

# 1. nothing uncommitted (ignored files are NOT counted; they are preserved by the move)
dirty="$(git -C "$wt" status --porcelain | wc -l)"
[ "$dirty" -eq 0 ] && say "ok: tracked/untracked-not-ignored files clean" || bad "$dirty uncommitted path(s); commit or stash first"

# 2. commits are safe: merged into origin/main, or present on a pushed remote branch
ahead="$(git -C "$wt" rev-list --count origin/main..HEAD)"
if [ "$ahead" -eq 0 ]; then
  say "ok: branch $branch is fully merged into origin/main"
elif git -C "$wt" branch -r --contains HEAD | grep -q .; then
  say "ok: $ahead commit(s) not in main but present on a remote branch"
else
  bad "$ahead commit(s) exist only locally (not in main, not on any remote); push or merge first"
fi

# 3. report what ignored content will travel with the folder
ign_files="$(git -C "$wt" ls-files --others --ignored --exclude-standard --directory | wc -l)"
say "info: $ign_files ignored top-level path(s) (all preserved by the move; set SHOW_SIZE=1 to also compute folder size, which is slow on big data)"
[ -z "${SHOW_SIZE:-}" ] || say "info: folder size $(du -sh "$wt" 2>/dev/null | cut -f1)"

dest="$archive/$(basename "$wt")"
[ ! -e "$dest" ] || bad "destination already exists: $dest"
say "plan: move $wt  ->  $dest ; then 'git worktree prune'"

[ "$fail" -eq 0 ] || { say "Not safe to retire. Nothing changed."; exit 1; }
[ "$apply" = "--apply" ] || { say "Dry run only. Re-run with --apply to perform."; exit 0; }

mkdir -p "$archive"
mv "$wt" "$dest"
git -C "$main" worktree prune
say "done: moved to $dest and pruned."
say "branch '$branch' still exists. When satisfied: git -C \"$main\" branch -d '$branch'   (refuses unmerged work)"
say "remote branch (if any, and merged): git push origin --delete '$branch'"
