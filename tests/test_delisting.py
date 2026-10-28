import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core.delisting import DelistPolicy, apply_delisting_returns  # noqa: E402


def _panel():
    dates = pd.to_datetime(["2000-01-03", "2000-01-04", "2000-01-05"] * 2)
    permno = np.array([1, 1, 1, 2, 2, 2])
    ret = np.array([0.0, 0.0, 0.10, 0.0, 0.0, 0.05])
    return ret, ret - 0.01, dates.to_numpy(), permno


def test_dlret_compounds_onto_last_row_and_leaves_others():
    ret, mk, d, p = _panel()
    dl = pd.DataFrame(dict(permno=[1], dlstdt=pd.to_datetime(["2000-01-06"]), dlret=[-0.5], dlstcd=[233], exchcd=[1]))
    r1, r2, info = apply_delisting_returns(ret, mk, d, p, dl)
    assert r1[2] == pytest.approx(1.10 * 0.5 - 1.0) and r2[2] == pytest.approx(mk[2] + (r1[2] - ret[2]))
    np.testing.assert_array_equal(np.delete(r1, 2), np.delete(ret, 2))
    assert info["n_applied"] == 1


def test_missing_performance_dlret_is_imputed_by_exchange_and_ordinary_codes_get_zero():
    ret, mk, d, p = _panel()
    dl = pd.DataFrame(dict(permno=[1, 2], dlstdt=pd.to_datetime(["2000-01-05", "2000-01-05"]), dlret=[np.nan, np.nan],
                           dlstcd=[500, 233], exchcd=[3, 1]))
    r1, _, info = apply_delisting_returns(ret, mk, d, p, dl)
    assert r1[2] == pytest.approx(1.10 * 0.45 - 1.0)                 # Nasdaq performance delisting: -55%
    assert r1[5] == pytest.approx(0.05)                              # merger code, missing dlret: no change
    assert info["n_imputed"] == 1
    r1b, _, _ = apply_delisting_returns(ret, mk, d, p, dl, DelistPolicy(impute_missing_performance=False))
    assert r1b[2] == pytest.approx(0.10)


def test_far_delisting_date_is_not_applied():
    ret, mk, d, p = _panel()
    dl = pd.DataFrame(dict(permno=[1], dlstdt=pd.to_datetime(["2001-01-01"]), dlret=[-0.9], dlstcd=[400], exchcd=[1]))
    r1, _, info = apply_delisting_returns(ret, mk, d, p, dl)
    np.testing.assert_array_equal(r1, ret) and info["n_applied"] == 0
