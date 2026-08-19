#!/usr/bin/env python3
"""safe_rm — move paths to a local, restorable trash instead of deleting.

Instead of `rm`, use:
    python scripts/safe_rm.py PATH [PATH ...] [--reason "why"]

Every invocation creates one batch directory under `.trash/<UTC-timestamp>/`
that mirrors each target's repo-relative path, and appends one JSON line per
file to `.trash/manifest.jsonl` recording:
    ts, batch, original_path, trash_path, kind, size_bytes, sha256, reason

Restore with `python scripts/restore_from_trash.py` (see --help).

Design notes:
- Refuses to operate outside the repo root.
- Refuses to trash the `.trash/`, `.git/`, or `.venv/` directories themselves.
- Preserves symlinks as symlinks (does not follow).
- Never overwrites an existing entry in the trash batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_MARKERS = (".git",)
TRASH_DIR_NAME = ".trash"
MANIFEST_NAME = "manifest.jsonl"
PROTECTED = {".git", ".trash", ".venv"}


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if any((candidate / m).exists() for m in REPO_MARKERS):
            return candidate
    raise SystemExit(f"safe_rm: could not locate repo root from {start}")


def sha256_of(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel_to_repo(path: Path, repo: Path) -> Path:
    resolved = path.resolve() if path.exists() else path.absolute()
    try:
        return resolved.relative_to(repo)
    except ValueError as exc:
        raise SystemExit(f"safe_rm: refusing to touch path outside repo: {path}") from exc


def guard_protected(rel: Path) -> None:
    top = rel.parts[0] if rel.parts else ""
    if top in PROTECTED:
        raise SystemExit(f"safe_rm: refusing to trash protected path: {rel}")


def move_to_trash(target: Path, batch_dir: Path, repo: Path) -> dict:
    rel = rel_to_repo(target, repo)
    guard_protected(rel)
    if not target.exists() and not target.is_symlink():
        raise SystemExit(f"safe_rm: no such path: {target}")

    dest = batch_dir / rel
    if dest.exists() or dest.is_symlink():
        raise SystemExit(f"safe_rm: trash slot already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    if target.is_symlink():
        kind = "symlink"
        size = 0
        digest = None
        link_target = os.readlink(target)
        os.symlink(link_target, dest)
        target.unlink()
    elif target.is_dir():
        kind = "dir"
        size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
        digest = None
        shutil.move(str(target), str(dest))
    else:
        kind = "file"
        size = target.stat().st_size
        digest = sha256_of(target)
        shutil.move(str(target), str(dest))

    return {
        "original_path": str(rel),
        "trash_path": str(dest.relative_to(repo)),
        "kind": kind,
        "size_bytes": size,
        "sha256": digest,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Move paths to a local restorable trash.")
    ap.add_argument("paths", nargs="+", help="Files or directories to trash.")
    ap.add_argument("--reason", default="", help="Why this deletion is happening.")
    args = ap.parse_args(argv)

    repo = find_repo_root(Path.cwd())
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trash_root = repo / TRASH_DIR_NAME
    batch_dir = trash_root / ts
    batch_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = trash_root / MANIFEST_NAME

    entries = []
    for raw in args.paths:
        target = Path(raw)
        entry = move_to_trash(target, batch_dir, repo)
        entry.update(
            ts=ts,
            batch=ts,
            reason=args.reason,
        )
        entries.append(entry)
        print(f"trashed: {entry['original_path']} -> {entry['trash_path']}")

    with manifest_path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    print(f"batch: {ts}  ({len(entries)} entr{'y' if len(entries)==1 else 'ies'})")
    print(f"restore with: python scripts/restore_from_trash.py --batch {ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
