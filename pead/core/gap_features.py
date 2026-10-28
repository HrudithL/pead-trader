"""Overnight-gap features from CRSP daily opens (EQ-22/EQ-24), computed on the fly from the raw dsf files (read-only).

gap_t = open_t / close_{t-1} - 1 on split-adjusted prices (price / cfacpr), for the stock's consecutive panel rows.
gap_z_t = gap_t / std(gap over the previous 60 sessions, shifted so day t is excluded; needs >= 30).
For an event, day0 is the first session whose close contains the news, so gap at day0 is known before the day0-close entry.
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from pead.common.paths import DATA_DIR, RUNS_DIR

CRSP_DIR = DATA_DIR / "raw_wrds" / "crsp"


@lru_cache(maxsize=1)
def _gap_panel() -> pd.DataFrame:
    cache = RUNS_DIR / "_cache" / "gap_panel_v2.parquet"          # derived, git-ignored; delete to rebuild
    if cache.exists():
        return pd.read_parquet(cache)
    frames = []
    for y in range(1995, 2015):
        d = pd.read_parquet(CRSP_DIR / f"dsf_{y}.parquet", columns=["permno", "date", "openprc", "prc", "cfacpr", "vol"])
        frames.append(d)
    d = pd.concat(frames, ignore_index=True).sort_values(["permno", "date"]).reset_index(drop=True)
    cf = d["cfacpr"].where(d["cfacpr"] > 0)
    d["adj_open"] = d["openprc"].abs() / cf
    d["adj_close"] = d["prc"].abs() / cf
    same = d["permno"].eq(d["permno"].shift(1))
    d["gap"] = np.where(same, d["adj_open"] / d["adj_close"].shift(1) - 1, np.nan)
    d.loc[~np.isfinite(d["gap"]) | (d["gap"].abs() > 1.0), "gap"] = np.nan
    d["intraday"] = d["adj_close"] / d["adj_open"] - 1
    d.loc[~np.isfinite(d["intraday"]) | (d["intraday"].abs() > 1.0), "intraday"] = np.nan
    v = d["vol"].where(d["vol"] > 0)
    d["rvol_d"] = v / v.groupby(d["permno"]).transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    sd = d.groupby("permno")["gap"].transform(lambda s: s.shift(1).rolling(60, min_periods=30).std())
    d["gap_z"] = d["gap"] / sd.where(sd > 0)
    d["date"] = pd.to_datetime(d["date"])
    out = d[["permno", "date", "gap", "gap_z", "intraday", "rvol_d"]]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(cache)
    except OSError:
        pass
    return out


def add_gap_features(events: pd.DataFrame, min_day_events: int = 20) -> pd.DataFrame:
    """events needs permno and day0_date. Adds gap, gap_adj (market proxy: same-day cross-section median, week median if the
    day has < min_day_events events) and gap_z (volatility scaled)."""
    g = _gap_panel()
    e = events[["permno", "day0_date"]].reset_index().merge(
        g.rename(columns={"date": "day0_date"}), on=["permno", "day0_date"], how="left").set_index("index").sort_index()
    out = events.copy()
    out["gap"], out["gap_z"] = e["gap"].to_numpy(), e["gap_z"].to_numpy()
    out["intraday"], out["rvol_d"] = e["intraday"].to_numpy(), e["rvol_d"].to_numpy()
    d = out["day0_date"]
    day_med = out.groupby(d)["gap"].transform("median")
    day_n = out.groupby(d)["gap"].transform("count")
    week = d.dt.isocalendar()
    wk = week["year"].astype(str) + "-" + week["week"].astype(str)
    wk_med = out.groupby(wk)["gap"].transform("median")
    out["gap_adj"] = out["gap"] - np.where(day_n >= min_day_events, day_med, wk_med)
    out["gap_z_adj"] = out["gap_z"] - np.where(day_n >= min_day_events, day_med, wk_med) / np.where(out["gap_z"].abs() > 0, out["gap"] / out["gap_z"], np.nan)
    return out
