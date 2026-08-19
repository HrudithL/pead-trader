#!/usr/bin/env python3
"""restore_from_trash — restore files previously moved by safe_rm.

Usage:
    # list all batches (newest last)
    python scripts/restore_from_trash.py --list

    # show the contents of one batch
    python scripts/restore_from_trash.py --batch 20260813T142530Z --list

    # restore an entire batch back to original locations
    python scripts/restore_from_trash.py --batch 20260813T142530Z

    # restore a single original path (searches manifest, newest match wins)
    python scripts/restore_from_trash.py --path data/foo/bar.csv

    # by default, refuses to overwrite existing files; pass --force to replace
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_MARKERS = (".git",)
TRASH_DIR_NAME = ".trash"
MANIFEST_NAME = "manifest.jsonl"


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if any((candidate / m).exists() for m in REPO_MARKERS):
            return candidate
    raise SystemExit(f"restore_from_trash: could not locate repo root from {start}")


def load_manifest(repo: Path) -> list[dict]:
    path = repo / TRASH_DIR_NAME / MANIFEST_NAME
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def restore_entry(entry: dict, repo: Path, force: bool) -> None:
    src = repo / entry["trash_path"]
    dst = repo / entry["original_path"]
    if not src.exists() and not src.is_symlink():
        print(f"skip (missing in trash): {entry['trash_path']}")
        return
    if (dst.exists() or dst.is_symlink()) and not force:
        print(f"skip (exists, use --force to overwrite): {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if (dst.exists() or dst.is_symlink()) and force:
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))
    print(f"restored: {entry['original_path']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Restore files from the local trash.")
    ap.add_argument("--batch", help="Batch id (UTC timestamp) to operate on.")
    ap.add_argument("--path", help="Original repo-relative path to restore.")
    ap.add_argument("--list", action="store_true", help="List batches or batch contents.")
    ap.add_argument("--force", action="store_true", help="Overwrite existing destinations.")
    args = ap.parse_args(argv)

    repo = find_repo_root(Path.cwd())
    entries = load_manifest(repo)
    if not entries:
        print("trash is empty (no manifest entries)")
        return 0

    if args.list and not args.batch and not args.path:
        batches: dict[str, int] = {}
        for e in entries:
            batches[e["batch"]] = batches.get(e["batch"], 0) + 1
        for batch in sorted(batches):
            print(f"{batch}  ({batches[batch]} entr{'y' if batches[batch]==1 else 'ies'})")
        return 0

    if args.batch:
        matching = [e for e in entries if e["batch"] == args.batch]
        if not matching:
            print(f"no entries for batch {args.batch}")
            return 1
        if args.list:
            for e in matching:
                print(f"{e['kind']:<7} {e['size_bytes']:>12}  {e['original_path']}")
            return 0
        for e in matching:
            restore_entry(e, repo, args.force)
        return 0

    if args.path:
        matches = [e for e in entries if e["original_path"] == args.path]
        if not matches:
            print(f"no manifest entry for path: {args.path}")
            return 1
        entry = matches[-1]  # newest
        restore_entry(entry, repo, args.force)
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
