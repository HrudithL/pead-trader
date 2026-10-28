"""Backfill the trial registry from saved results (a LOWER BOUND on trials tried; sweeps are not reconstructed).

    python scripts/backfill_trials.py [--apply]

One trial per results/<name>/summary.json, family from the registered strategy (else 'unregistered'). Refuses to run
twice: it skips names already present with note 'backfill'. Dry run by default.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import RUNS_DIR  # noqa: E402
from pead.eval import registry  # noqa: E402
from pead.strategies import registry as strategies  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    known = {t["strategy"] for t in registry.load_trials() if t["note"] == "backfill"}
    reg = strategies.all_strategies()
    n = 0
    for d in sorted(RUNS_DIR.iterdir()):
        f = d / "summary.json"
        if not f.exists() or d.name in known:
            continue
        s = json.loads(f.read_text(encoding="utf-8"))
        sr = s.get("summary", {}).get("sharpe")
        fam_meta = s.get("family")
        if sr is None or not np.isfinite(sr):
            continue
        fam = fam_meta or (reg[d.name].family if d.name in reg else "unregistered")
        n += 1
        print(f"{d.name:40s} {fam:20s} sharpe={sr:.3f}")
        if a.apply:
            registry.log_trial(d.name, fam, sr / np.sqrt(252), window="full", note="backfill")
    print(f"{n} trials {'logged' if a.apply else 'would be logged (dry run)'}")


if __name__ == "__main__":
    main()
