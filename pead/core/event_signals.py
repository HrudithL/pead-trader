"""Extra event signals joined onto the equity events (EQ-03/05/06): the raw analyst SUE, the seasonal SUE and its PIT rank.

Source: data/events/equity_events_1996_2013.parquet (the table the vol panel was built from), joined on (permno, day0_date) with
the first row kept when the key is duplicated (19 analyst duplicates, MT-01 LA-15). Read-only.
"""
import numpy as np
import pandas as pd

from pead.common.paths import DATA_DIR
from pead.common.pit_rank import rank_pct


def add_sue_signals(events: pd.DataFrame) -> pd.DataFrame:
    src = pd.read_parquet(DATA_DIR / "events" / "equity_events_1996_2013.parquet",
                          columns=["permno", "day0_date", "sue_analyst", "sue_seasonal", "sue_source"])
    src = src.dropna(subset=["permno", "day0_date"]).drop_duplicates(["permno", "day0_date"])
    src["permno"] = src["permno"].astype("int64")
    src["day0_date"] = pd.to_datetime(src["day0_date"])
    ev = events.copy()
    m = ev[["permno", "day0_date"]].astype({"permno": "int64"}).merge(src, on=["permno", "day0_date"], how="left")
    ev["sue_analyst_raw"] = m["sue_analyst"].to_numpy()
    ev["sue_seasonal_raw"] = m["sue_seasonal"].to_numpy()
    q = ev["ann_quarter"].astype(str)
    v = pd.Series(ev["sue_seasonal_raw"].to_numpy(), index=ev.index)
    ok = v.notna()                                   # rank among events that HAVE the signal (NaNs would deflate every rank)
    rk = pd.Series(np.nan, index=ev.index)
    rk[ok] = rank_pct(v[ok], q[ok], mode="prior_quarters")
    ev["sue_seas_rank"] = rk
    ev["sue_seas_rank"] = ev["sue_seas_rank"].where(ev["sue_seasonal_raw"] >= 0)
    return ev
