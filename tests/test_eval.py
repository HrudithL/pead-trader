"""Search/holdout split and the evaluation harness."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core import splits  # noqa: E402
from pead.eval.harness import evaluate  # noqa: E402


def test_split_is_first_70pct_of_quarters_and_burned():
    qs = pd.period_range("1996Q2", "2013Q4", freq="Q")          # 71 quarters
    w = splits.windows(pd.Series(qs.astype(str)))
    assert len(w.search_quarters) == 49 and str(w.search_quarters[-1]) == "2008Q2"
    assert w.search_end == pd.Timestamp("2008-06-30") and w.holdout_start == pd.Timestamp("2008-07-01")
    assert w.kind == "burned"


def test_search_mask_matches_split():
    q = pd.Series(np.repeat(pd.period_range("2000Q1", periods=10, freq="Q").astype(str), 3))
    m = splits.search_mask(q)
    assert m.sum() == 21 and m[:21].all() and not m[21:].any()


def test_evaluate_reports_degradation(tmp_path):
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("1996-04-01", "2013-12-31")
    r = np.where(dates <= "2008-06-30", 0.0008, 0.0002) + rng.normal(0, 0.008, len(dates))
    nav = 1e7 * np.cumprod(1 + r)
    d = tmp_path / "toy"
    d.mkdir()
    pd.DataFrame({"date": dates, "daily_pnl": np.diff(nav, prepend=1e7), "nav": nav}).to_csv(d / "daily.csv.gz", index=False)
    ev = evaluate("toy", pd.Series(pd.period_range("1996Q2", "2013Q4", freq="Q").astype(str)), results_dir=tmp_path)
    assert ev["search"]["sharpe"] > ev["holdout"]["sharpe"] and 0 < ev["degradation_sharpe_ratio"] < 1
