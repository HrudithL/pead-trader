"""Write or check a manifest (relative path, size, sha256) of the six raw-data folders.

Why this exists: this project's raw WRDS pull is staged onto an external drive so a GPU box can
read it without a git-tracked copy (see GPU_SETUP.md). That staging is a manual, one-time copy
with no sync mechanism -- and once, that copy silently drifted from the source machine's `data/`
without anyone noticing until a strategy's own hyperparameter search picked a different answer
(see README's "Never symlink these paths" note and the Strategy 8 caveat in the results section).
This script is the check that incident was missing: a small, git-trackable manifest of exactly
what the canonical raw data looks like, and a `--check` mode that any machine (this one, a GPU
box, Colab) can run against whatever its PEAD_*_DIR env vars currently resolve to, before trusting
a real run's results.

    python scripts/verify_raw_data.py --write   # run ONCE on the machine holding canonical data,
                                                 # commit the resulting data/raw_data_manifest.json
    python scripts/verify_raw_data.py --check   # run on any machine before a real pipeline run;
                                                 # exits non-zero and lists every mismatch if the
                                                 # data it can see doesn't match the manifest

This only reads the six raw folders (not the OptionMetrics extract, which is separately never
copied off its own drive) and only ever writes the small manifest file, never the data itself.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import (
    DATA_DIR, RAW_WRDS_DIR, RAW_EQUITY_DIR, EARNINGS_DIR, EVENTS_DIR, METADATA_DIR, RESULTS_DIR,
)

FOLDERS = {
    "raw_wrds": RAW_WRDS_DIR,
    "normalized_equity": RAW_EQUITY_DIR,
    "earnings": EARNINGS_DIR,
    "events": EVENTS_DIR,
    "metadata": METADATA_DIR,
    "results": RESULTS_DIR,
}

MANIFEST_PATH = DATA_DIR / "raw_data_manifest.json"


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest() -> dict:
    manifest = {}
    for name, folder in FOLDERS.items():
        if not folder.is_dir():
            manifest[name] = {"present": False}
            continue
        files = {}
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                rel = str(path.relative_to(folder)).replace("\\", "/")
                files[rel] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
        manifest[name] = {"present": True, "files": files}
    return manifest


def write_manifest():
    manifest = build_manifest()
    n_files = sum(len(v.get("files", {})) for v in manifest.values())
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print(f"wrote {MANIFEST_PATH} ({n_files} files across {len(FOLDERS)} folders)")
    for name, v in manifest.items():
        if not v["present"]:
            print(f"  WARNING: {name} not found at {FOLDERS[name]} -- not included in the manifest")


def check_manifest() -> bool:
    if not MANIFEST_PATH.exists():
        print(f"ERROR: no manifest at {MANIFEST_PATH} -- run --write on the canonical machine first", file=sys.stderr)
        return False

    with open(MANIFEST_PATH) as f:
        expected = json.load(f)
    actual = build_manifest()

    ok = True
    for name in FOLDERS:
        exp, act = expected.get(name, {}), actual.get(name, {})
        if not exp.get("present", False):
            continue
        if not act.get("present", False):
            print(f"MISMATCH {name}: expected present, but {FOLDERS[name]} not found")
            ok = False
            continue
        exp_files, act_files = exp.get("files", {}), act.get("files", {})
        missing = sorted(set(exp_files) - set(act_files))
        extra = sorted(set(act_files) - set(exp_files))
        changed = sorted(
            f for f in (set(exp_files) & set(act_files))
            if exp_files[f]["sha256"] != act_files[f]["sha256"]
        )
        if missing or extra or changed:
            ok = False
            print(f"MISMATCH {name}:")
            for f in missing:
                print(f"  missing: {f}")
            for f in extra:
                print(f"  extra:   {f}")
            for f in changed:
                print(f"  changed: {f}")
        else:
            print(f"OK {name}: {len(exp_files)} files match")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="build and write the manifest from current data")
    mode.add_argument("--check", action="store_true", help="verify current data against the committed manifest")
    args = parser.parse_args()

    if args.write:
        write_manifest()
    else:
        ok = check_manifest()
        if not ok:
            print("\nraw data does NOT match the committed manifest -- do not trust a real run "
                  "against this data until reconciled.", file=sys.stderr)
            sys.exit(1)
        print("\nraw data matches the committed manifest.")


if __name__ == "__main__":
    main()
