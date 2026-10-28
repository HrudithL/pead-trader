import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.eval import leaderboard  # noqa: E402


def _write(base, name, mu, status="research", seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("1996-04-01", "2013-12-31")
    r = rng.normal(mu, 0.008, len(dates))
    nav = 1e7 * np.cumprod(1 + r)
    d = base / name
    d.mkdir()
    pd.DataFrame({"date": dates, "daily_pnl": np.diff(nav, prepend=1e7), "nav": nav}).to_csv(d / "daily.csv.gz", index=False)
    (d / "summary.json").write_text('{"asset_class": "equity", "status": "%s"}' % status)


def test_build_ranks_by_lower_bound_and_marks_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("PEAD_TRIALS_FILE", str(tmp_path / "t.jsonl"))
    _write(tmp_path, "good", 0.0008, seed=1)
    _write(tmp_path, "weak", 0.0001, seed=2)
    _write(tmp_path, "old_legacy", 0.0008, status="reference", seed=3)
    df = leaderboard.build(pd.Series(pd.period_range("1996Q2", "2013Q4", freq="Q").astype(str)), n_boot=60, results_dir=tmp_path)
    cur = df[df.V == "V1"]
    assert list(cur.name) == ["good", "weak"] and df.set_index("name").loc["old_legacy", "V"] == "ref"
    g = df.set_index("name").loc["good"]
    assert g.sharpe_lo95 < g.sharpe < g.sharpe_hi95 and np.isnan(g.dsr)
    leaderboard.write(df, tmp_path)
    assert (tmp_path / "leaderboard_v2.md").read_text().startswith("# Leaderboard v2")
