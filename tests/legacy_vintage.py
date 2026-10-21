"""Helper for tests that pin the ORIGINAL (pre-fix) numbers.

data/ holds the corrected point-in-time vintage; the complete pre-fix derived vintage is kept (git-ignored)
in data/_prefix_vintage/. pead/common/paths.py reads its env vars at import time, so legacy strategies are run
in ONE subprocess (`python -m pead run ... --no-report`) pointed at that folder, writing into a temp runs
folder (repo results/ is never touched). The batch is cached for the whole pytest session.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
PREFIX = DATA / "_prefix_vintage"

# every strategy the legacy-pin tests need; run together in one invocation (parallel worker processes)
LEGACY_BATCH = ["balanced_leverage_legacy", "sector_balanced_legacy", "market_exposed_legacy", "extreme_picks_legacy",
                "graded_bets_legacy", "size_balanced_legacy", "conviction_tilt_legacy", "calm_weighted_legacy",
                "options_cap20_legacy", "options_cap30_legacy", "options_cap20_midfill"]

_cache = {}


def have_prefix():
    return (PREFIX / "metadata").is_dir() and (PREFIX / "decile_events.parquet").exists()


def legacy_env(runs_dir):
    return {**os.environ,
            "PEAD_DATA_DIR": str(PREFIX), "PEAD_METADATA_DIR": str(PREFIX / "metadata"),
            "PEAD_RAW_WRDS_DIR": str(DATA / "raw_wrds"), "PEAD_RAW_EQUITY_DIR": str(DATA / "normalized_equity"),
            "PEAD_EVENTS_DIR": str(DATA / "events"), "PEAD_EARNINGS_DIR": str(DATA / "earnings"),
            "PEAD_RESULTS_DIR": str(DATA / "results"), "PEAD_RUNS_DIR": str(runs_dir)}


def require_prefix():
    if not have_prefix():
        pytest.skip("data/_prefix_vintage (the pre-fix derived vintage) is absent")


def legacy_run(name):
    """Return (summary dict, daily DataFrame) of a legacy strategy run on the pre-fix vintage."""
    require_prefix()
    if "dir" not in _cache:
        _cache["dir"] = Path(tempfile.mkdtemp(prefix="pead_legacy_runs_"))
        out = subprocess.run([sys.executable, "-m", "pead", "run", *LEGACY_BATCH, "--no-report"], cwd=REPO,
                             env=legacy_env(_cache["dir"]), capture_output=True, text=True)
        _cache["rc"], _cache["log"] = out.returncode, (out.stdout + out.stderr)[-3000:]
    d = _cache["dir"] / name
    if not (d / "summary.json").exists():
        pytest.fail(f"legacy run of {name} produced no result (rc={_cache['rc']}):\n{_cache['log']}")
    summary = json.loads((d / "summary.json").read_text())["summary"]
    return summary, pd.read_csv(d / "daily.csv.gz")


def prefix_ref(filename):
    """Path of a pre-fix reference file (original per-strategy script output); the copies in data/ are identical."""
    require_prefix()
    return PREFIX / filename
