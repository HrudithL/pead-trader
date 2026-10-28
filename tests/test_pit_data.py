"""Data-level point-in-time checks on the real events (MT-01 LA-15/LA-16). Skipped when the data are absent."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import DATA_DIR  # noqa: E402

EV = DATA_DIR / "events" / "equity_events_1996_2013.parquet"
needs_data = pytest.mark.skipif(not EV.exists(), reason="events parquet not available")


@pytest.fixture(scope="module")
def events():
    return pd.read_parquet(EV)


@needs_data
def test_la15a_after_close_announcements_trade_next_session(events):
    e = events[(events["anntims"] >= dt.time(16, 0)) & ~events["anntims_unknown"] & events["day0_date"].notna()]
    assert len(e) > 1000
    assert (e["day0_date"] > e["anndats"]).all()


@needs_data
def test_la15b_unknown_time_events_are_delayed_at_least_a_day(events):
    e = events[events["anntims_unknown"] & events["day0_date"].notna()]
    assert len(e) > 1000 and (e["day0_lag_days"].astype(float) >= 1).all()


@needs_data
def test_la15c_day0_never_before_announcement(events):
    e = events[events["day0_date"].notna()]
    assert (e["day0_date"] >= e["anndats"]).all()


@needs_data
def test_la15d_known_defect_counts_are_pinned(events):
    """Pinned 2026-10-27/28 so a rebuild that changes them is noticed (docs/plans MT-01 LA-15d)."""
    analyst = events[events["sue_source"] == "analyst"].dropna(subset=["day0_date"])
    assert int(analyst.duplicated(["permno", "day0_date"]).sum()) == 19
    assert int((analyst["anndats"].dt.to_period("Q") != analyst["day0_date"].dt.to_period("Q")).sum()) == 208
    assert int((analyst["anndats"] < analyst["fpedats"]).sum()) == 61


@needs_data
def test_la16_adv21_matches_prior_21_sessions():
    """avg_dollar_volume_21d is built from the 21 sessions BEFORE entry (no same-day volume)."""
    panel_dir = DATA_DIR / "normalized_equity"
    vp = DATA_DIR / "equity_vol_panel.parquet"
    if not vp.exists() or not (panel_dir / "equity_2005.parquet").exists():
        pytest.skip("panel not available")
    ev = pd.read_parquet(vp).sample(60, random_state=0)
    cols = pd.read_parquet(panel_dir / "equity_2005.parquet").columns
    if not {"prc", "vol"} <= set(cols):
        pytest.skip("prc/vol not in normalized panel")
    ok = checked = 0
    for _, r in ev.iterrows():
        y = r["day0_date"].year
        f = panel_dir / f"equity_{y}.parquet"
        if not f.exists():
            continue
        px = pd.read_parquet(panel_dir / f"equity_{y - 1}.parquet") if (panel_dir / f"equity_{y - 1}.parquet").exists() else None
        d = pd.concat([x for x in (px, pd.read_parquet(f)) if x is not None])
        d = d[d["permno"] == r["permno"]].sort_values("date")
        prior = d[d["date"] < r["day0_date"]].tail(21)
        if len(prior) < 21:
            continue
        adv = (prior["prc"].abs() * prior["vol"]).mean()
        checked += 1
        ok += int(np.isclose(adv, r["avg_dollar_volume_21d"], rtol=1e-3) or np.isclose(adv / 1000, r["avg_dollar_volume_21d"], rtol=1e-3))
    if checked < 20:
        pytest.skip("too few checkable events")
    assert ok >= 0.9 * checked
