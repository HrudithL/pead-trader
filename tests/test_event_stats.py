import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.core import event_stats as es  # noqa: E402


def _events(signal_strength, seed=0, nq=20, n=200):
    rng = np.random.default_rng(seed)
    sig = rng.normal(size=nq * n)
    out = signal_strength * sig + rng.normal(size=nq * n)
    return pd.DataFrame(dict(sig=sig, out=out, ann_quarter=np.repeat(np.arange(nq), n)))


def test_ic_detects_signal_and_not_noise():
    good = es.ic_summary(es.quarterly_ic(_events(0.3), "sig", "out"))
    noise = es.ic_summary(es.quarterly_ic(_events(0.0, seed=1), "sig", "out"))
    assert good["mean_ic"] > 0.2 and good["ic_t"] > 10 and good["pct_positive"] == 1.0
    assert abs(noise["ic_t"]) < 3


def test_decile_spread_sign():
    s = es.decile_spread(_events(0.5), "sig", "out")
    assert s["spread"] > 0.5 and s["spread_t"] > 5 and s["top"] > s["bottom"]
