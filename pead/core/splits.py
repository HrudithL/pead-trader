"""Search / holdout split shared by every evaluation (REF-search-holdout, plan decision D1).

Search = the first `fraction` (default 70%) of announcement quarters, the same rule the decay holding periods are fitted
on (`pead/prep/equity/daily_decay.py`). Holdout = the remaining quarters. With data ending in 2013 the holdout starts at
the financial crisis and has been inspected during development, so it is labelled 'burned', never 'pristine'.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROTOCOL_DIR = Path(__file__).resolve().parents[1] / "eval" / "protocols"


def load_protocol(pid: str = "v1") -> dict:
    return json.loads((PROTOCOL_DIR / f"{pid}.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Windows:
    search_quarters: tuple
    holdout_quarters: tuple
    search_end: pd.Timestamp            # last calendar day of the final search quarter
    holdout_start: pd.Timestamp
    kind: str = "burned"


def split_quarters(ann_quarters, fraction: float = 0.70) -> tuple:
    qs = sorted(pd.PeriodIndex(pd.Series(ann_quarters).dropna().unique(), freq="Q"))
    n = max(1, int(len(qs) * fraction))
    return tuple(qs[:n]), tuple(qs[n:])


def windows(ann_quarters, protocol: dict = None) -> Windows:
    protocol = protocol or load_protocol()
    s, h = split_quarters(ann_quarters, protocol["search"]["fraction"])
    end = s[-1].end_time.normalize()
    return Windows(s, h, end, end + pd.Timedelta(days=1), protocol["holdout"]["kind"])


def search_mask(quarters, fraction: float = 0.70):
    """Boolean array: True where the event's announcement quarter is in the search window."""
    s, _ = split_quarters(quarters, fraction)
    return pd.PeriodIndex(pd.Series(quarters).astype(str), freq="Q").isin(list(s))
