"""Append-only trial registry (REF-trial-registry): every strategy variant / parameter set we evaluate.

Deflated Sharpe needs an honest count of what was tried. `log_trial` appends one JSON line per evaluation to
`results/trials.jsonl` (override with PEAD_TRIALS_FILE); nothing is ever rewritten or removed. `n_trials` and
`trial_sharpe_variance` feed `pead.core.stats.dsr`.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pead.common.paths import RUNS_DIR


def trials_path() -> Path:
    return Path(os.environ.get("PEAD_TRIALS_FILE", RUNS_DIR / "trials.jsonl"))


def log_trial(strategy: str, family: str, sharpe_daily: float, params: dict = None, window: str = "search",
              note: str = "", path=None) -> dict:
    """Append one trial. `sharpe_daily` is the PER-PERIOD (daily) Sharpe so it can feed stats.dsr directly."""
    rec = dict(ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), strategy=strategy, family=family,
               sharpe_daily=float(sharpe_daily), params=params or {}, window=window, note=note)
    p = Path(path) if path else trials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def load_trials(family: str = None, path=None) -> list:
    p = Path(path) if path else trials_path()
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if family is None or r["family"] == family]


def n_trials(family: str = None, path=None) -> int:
    return len(load_trials(family, path))


def trial_sharpe_variance(family: str = None, path=None) -> float:
    s = [r["sharpe_daily"] for r in load_trials(family, path)]
    return float(np.var(s, ddof=1)) if len(s) > 1 else float("nan")
