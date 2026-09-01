"""Shared path constants for every script in the pipeline.

Every location is resolved relative to the repo root by default, so the pipeline runs unmodified
after a plain `git clone` on any machine (this replaced a set of dead, machine-specific absolute
paths -- e.g. `/root/pead_report/data`, `/mnt/user-data/uploads/...`, `D:/OptionMetrics/parquet`
-- that earlier versions of these scripts hardcoded from the cloud sandbox they were first
developed in). Each location can still be overridden with an environment variable, e.g. to point
`OPTIONMETRICS_DIR` at a Google Drive mount on a Colab GPU runtime.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("PEAD_DATA_DIR", REPO_ROOT / "data"))
REPORTS_DIR = Path(os.environ.get("PEAD_REPORTS_DIR", REPO_ROOT / "reports"))
FIGURES_DIR = Path(os.environ.get("PEAD_FIGURES_DIR", REPORTS_DIR / "figures"))

RAW_EQUITY_DIR = Path(os.environ.get("PEAD_RAW_EQUITY_DIR", DATA_DIR / "normalized_equity"))
EVENTS_DIR = Path(os.environ.get("PEAD_EVENTS_DIR", DATA_DIR / "events"))
EARNINGS_DIR = Path(os.environ.get("PEAD_EARNINGS_DIR", DATA_DIR / "earnings"))
RESULTS_DIR = Path(os.environ.get("PEAD_RESULTS_DIR", DATA_DIR / "results"))
METADATA_DIR = Path(os.environ.get("PEAD_METADATA_DIR", DATA_DIR / "metadata"))

# OptionMetrics IvyDB extract (31GB in this specific parquet/ subfolder; the full external drive
# is larger but this pipeline only ever reads this subfolder), not tracked in this repo, never
# copied off its physical drive -- see README "Data" section and "Data portability".
OPTIONMETRICS_DIR = Path(os.environ.get("OPTIONMETRICS_DIR", "D:/OptionMetrics/parquet"))

# Operational logs (pipeline_status.json, pipeline_run.log) for the options-strategy GPU pipeline's
# unattended multi-day runs -- not research output, so not under DATA_DIR.
LOGS_DIR = Path(os.environ.get("PEAD_LOGS_DIR", REPO_ROOT / "logs"))

# Backtest engines (scripts 12, 17-27) all size positions against this starting NAV.
INITIAL_CAPITAL = 10_000_000.0
