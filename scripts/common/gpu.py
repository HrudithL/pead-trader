"""
Device-agnostic array backend + unattended-run checkpointing, shared by every GPU-tiered pipeline
in this repo (options_strategy/40+ and equity_strategy/32+). Split out of
options_strategy/lib/gpu.py (which originated this pattern first) so the equity-strategy GPU
pipeline can reuse the identical numpy<->cupy backend selection and StageTimer/status-log
machinery instead of a second copy -- see each domain's own lib/gpu.py for its
mock-data generators, which stay domain-specific.

Three things every GPU-tiered script in this repo needs, provided here:

1. An array backend that is numpy on CPU and cupy on GPU, selected by a `--device {cpu,cuda}`
   flag -- so the exact same vectorized code path (batching, indexing, reductions) is exercised
   at every scale, and a script written/tested on this CPU-only dev machine needs zero code
   changes to run on a real GPU later.
2. A `--smoke-test` flag convention (the flag itself is added here; each script defines what
   "shrink every size/iteration knob" means for its own kernel).
3. A checkpoint/status log so a multi-day unattended run can be safely left alone: each stage
   records start/done/failed with a timestamp to logs/pipeline_status.json, and a stage that
   already produced its output file is skipped on re-run (crash/reboot safe).
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from common.paths import LOGS_DIR

STATUS_PATH = LOGS_DIR / "pipeline_status.json"


# ---------------------------------------------------------------------------
# Array backend (numpy <-> cupy), device-agnostic
# ---------------------------------------------------------------------------

def get_backend(device: str):
    """Returns (xp, device) where xp is the numpy-API-compatible array module to use.
    device='cpu' always works (numpy). device='cuda' requires cupy + an actual GPU; raises a
    clear error immediately (rather than a confusing failure deep in a kernel) if unavailable."""
    if device == "cpu":
        return np, "cpu"
    if device == "cuda":
        try:
            import cupy as cp
        except ImportError as e:
            raise RuntimeError(
                "device='cuda' requires cupy (pip install cupy-cudaXX matching this machine's "
                "CUDA version -- see requirements-gpu.txt). Not installed here."
            ) from e
        if cp.cuda.runtime.getDeviceCount() == 0:
            raise RuntimeError("device='cuda' requested but cupy sees no CUDA-capable GPU.")
        return cp, "cuda"
    raise ValueError(f"unknown device {device!r}, expected 'cpu' or 'cuda'")


def to_host(xp, arr):
    """Bring an array back to a plain numpy array on CPU, regardless of which backend made it."""
    if xp is np:
        return np.asarray(arr)
    return xp.asnumpy(arr)


def add_device_arg(parser: argparse.ArgumentParser, default="cpu"):
    parser.add_argument("--device", choices=["cpu", "cuda"], default=default,
                         help="array backend: 'cpu' (numpy, always available) or "
                              "'cuda' (cupy, needs a real GPU). Default: %(default)s")
    return parser


def add_smoke_test_arg(parser: argparse.ArgumentParser):
    parser.add_argument("--smoke-test", action="store_true",
                         help="shrink every size/iteration knob drastically (few events, few "
                              "sweep combos, few epochs) while exercising the exact same code "
                              "path -- for validating a script finishes cleanly on a small GPU "
                              "in minutes before committing to the real multi-hour/day run.")
    return parser


# ---------------------------------------------------------------------------
# Checkpoint / status logging for unattended multi-day runs
# ---------------------------------------------------------------------------

def _load_status() -> dict:
    if STATUS_PATH.exists():
        return json.loads(STATUS_PATH.read_text())
    return {}


def _save_status(status: dict):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2, default=str))


def stage_done(output_path) -> bool:
    """A stage is considered done if its declared output file already exists -- the resume
    check a driver script (run_pipeline.py) uses to skip completed stages after a crash/reboot."""
    return Path(output_path).exists()


class StageTimer:
    """Context manager: records start/done/failed + wall-clock duration for one pipeline stage
    into logs/pipeline_status.json, so a run left unattended for days leaves a readable trail of
    what finished, what's in progress, and what broke, without needing to grep raw stdout logs.

    Usage:
        with StageTimer("42_gpu_exit_optimizer", extra={"device": args.device}):
            ... do the stage's work ...
    """

    def __init__(self, name: str, extra: dict | None = None):
        self.name = name
        self.extra = extra or {}

    def __enter__(self):
        self.t0 = time.time()
        status = _load_status()
        status[self.name] = {"state": "running", "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              **self.extra}
        _save_status(status)
        print(f"[{self.name}] started", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        status = _load_status()
        elapsed = time.time() - self.t0
        if exc_type is None:
            status[self.name] = {**status.get(self.name, {}), "state": "done",
                                  "elapsed_sec": round(elapsed, 1)}
            print(f"[{self.name}] done in {elapsed:.1f}s", flush=True)
        else:
            status[self.name] = {**status.get(self.name, {}), "state": "failed",
                                  "elapsed_sec": round(elapsed, 1), "error": str(exc)}
            print(f"[{self.name}] FAILED after {elapsed:.1f}s: {exc}", flush=True)
        _save_status(status)
        return False  # never swallow the exception
