"""
Hardware-aware defaults for the options-strategy pipeline: how many worker processes to use for
CPU-bound and disk-scan-bound work, and what GPU is available -- detected once here and shared by
every script in this directory plus run_pipeline.py's scheduler.

No new dependency (psutil isn't in requirements.txt): CPU count comes from os.cpu_count(), RAM
from a small stdlib-only cross-platform probe (ctypes on Windows, os.sysconf on POSIX), and GPU
presence/VRAM from cupy if installed (optional -- see requirements-gpu.txt). Every probe here is
advisory/best-effort: it only ever picks a DEFAULT for a --jobs/--device flag a script already
exposes, so a probe failing (unsupported platform, no GPU, cupy not installed) just falls back to
a conservative default rather than breaking anything.
"""
import argparse
import os


def cpu_count() -> int:
    return os.cpu_count() or 1


def total_ram_gb():
    """Best-effort total system RAM in GB, or None if it can't be determined on this platform."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
        page_size = os.sysconf("SC_PAGE_SIZE")
        n_pages = os.sysconf("SC_PHYS_PAGES")
        return (page_size * n_pages) / (1024 ** 3)
    except Exception:
        return None


def gpu_summary() -> dict:
    """{'available', 'name', 'vram_gb', 'count'} via cupy -- the same backend
    lib.gpu.get_backend('cuda') hands every GPU script here, so this reports exactly what those
    scripts would actually get, not just "is a GPU physically present in this machine"."""
    try:
        import cupy as cp
        count = cp.cuda.runtime.getDeviceCount()
        if count == 0:
            return {"available": False, "name": None, "vram_gb": None, "count": 0}
        props = cp.cuda.runtime.getDeviceProperties(0)
        _free, total = cp.cuda.runtime.memGetInfo()
        name = props["name"]
        if isinstance(name, bytes):
            name = name.decode(errors="replace")
        return {"available": True, "name": name, "vram_gb": total / (1024 ** 3), "count": count}
    except Exception:
        return {"available": False, "name": None, "vram_gb": None, "count": 0}


def recommended_scan_jobs(default_cap: int = 4) -> int:
    """Default parallelism for the per-year OptionMetrics-scan scripts (35/36/41/47/47b/49).
    These are bounded by the OM drive's real I/O -- documented in lib/options.py as a single
    physical external drive on this dev machine -- so this deliberately does NOT default to full
    cpu_count() the way a pure-CPU task would. It caps at `default_cap` regardless of core count,
    and --jobs is always there to override once the actual box (SSD/NVMe/RAID on the 5090 vs. an
    external HDD here) is known to tolerate more concurrent readers."""
    env_override = os.environ.get("PEAD_SCAN_JOBS")
    if env_override:
        return max(1, int(env_override))
    return max(1, min(default_cap, cpu_count()))


def recommended_cpu_jobs() -> int:
    """Default parallelism for pure CPU-bound work with no shared disk bottleneck (e.g. Optuna
    trials run on CPU) -- safe to use every core."""
    env_override = os.environ.get("PEAD_CPU_JOBS")
    if env_override:
        return max(1, int(env_override))
    return cpu_count()


def add_jobs_arg(parser: argparse.ArgumentParser, default: int = None, help_suffix: str = ""):
    """Adds a --jobs flag for a per-year scan script. Default is recommended_scan_jobs() unless
    the caller overrides it (e.g. a script with a different concurrency profile)."""
    if default is None:
        default = recommended_scan_jobs()
    parser.add_argument("--jobs", type=int, default=default,
                         help=f"parallel worker PROCESSES for the per-year scan loop, one year "
                              f"per worker (this machine: {cpu_count()} CPU cores, "
                              f"{recommended_scan_jobs()} recommended given the OM drive is a "
                              f"single physical volume -- raise this on a machine with faster/"
                              f"striped storage). --jobs 1 disables parallelism. "
                              f"Default: %(default)s. {help_suffix}")
    return parser


def add_force_arg(parser: argparse.ArgumentParser, help_suffix: str = ""):
    """Adds a --force flag for a per-year-checkpointed scan script: reprocess every year even if
    its per-year checkpoint parquet already exists, instead of silently reusing a stale one.
    run_pipeline.py's own --force only bypasses ITS top-level "final output already exists" skip;
    without a matching flag on the child script, a forced stage would still silently reuse stale
    per-year checkpoints underneath it, so this must be threaded down separately."""
    parser.add_argument("--force", action="store_true",
                         help=f"reprocess every year even if its per-year checkpoint file already "
                              f"exists (instead of silently reusing it). {help_suffix}")
    return parser


def hardware_report() -> str:
    """One-line human-readable summary of what this machine offers -- printed by run_pipeline.py
    at startup so an unattended multi-day run's log records what hardware it actually ran on."""
    ram = total_ram_gb()
    ram_s = f"{ram:.0f}GB RAM" if ram else "RAM unknown"
    gpu = gpu_summary()
    gpu_s = (f"GPU: {gpu['name']} ({gpu['vram_gb']:.1f}GB VRAM)" if gpu["available"]
             else "GPU: none (cupy not installed or no CUDA device)")
    return f"{cpu_count()} CPU cores, {ram_s}, {gpu_s}"
