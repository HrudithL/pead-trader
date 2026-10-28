import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core import gap_features as gf  # noqa: E402


def test_add_gap_features_market_adjusts_with_same_day_median(monkeypatch):
    d = pd.Timestamp("2005-03-01")
    n = 25
    panel = pd.DataFrame(dict(permno=range(n), date=d, gap=np.r_[0.10, np.full(n - 1, 0.02)], gap_z=np.r_[2.0, np.full(n - 1, 1.0)]))
    monkeypatch.setattr(gf, "_gap_panel", lambda: panel)
    ev = pd.DataFrame(dict(permno=range(n), day0_date=d))
    out = gf.add_gap_features(ev)
    assert out["gap"].iloc[0] == pytest.approx(0.10)
    assert out["gap_adj"].iloc[0] == pytest.approx(0.08)            # median of the day is 0.02
    assert out["gap_adj"].iloc[1] == pytest.approx(0.0)


def test_small_day_falls_back_to_week_median(monkeypatch):
    days = pd.to_datetime(["2005-03-01", "2005-03-02"])              # same ISO week, 3 events each: below min_day_events
    panel = pd.DataFrame(dict(permno=range(6), date=np.repeat(days, 3), gap=[0.0, 0.0, 0.0, 0.04, 0.04, 0.04], gap_z=1.0))
    monkeypatch.setattr(gf, "_gap_panel", lambda: panel)
    ev = pd.DataFrame(dict(permno=range(6), day0_date=np.repeat(days, 3)))
    out = gf.add_gap_features(ev, min_day_events=20)
    assert out["gap_adj"].tolist() == pytest.approx([-0.02] * 3 + [0.02] * 3)
