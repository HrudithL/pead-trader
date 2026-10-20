"""Build the causal event table the meta-strategy trades from.  python meta/build_events.py

Read-only inputs come from the analysis worktree (SRC).  Everything written goes to meta/out/.

Per event we produce:
  * pit_sue_pct   SUE percentile against ONLY events whose day0 is strictly earlier and within the
                  trailing 365 calendar days (no same-quarter future announcers, no full-sample pool)
  * entry-date features already in the vol panel (beta_252d, ivol_252d, 21d dollar volume, size/liquidity)
  * fixed-horizon outcome path: raw daily returns for the H=60 sessions after day0 (entry at day0 close,
    exactly as the existing engine does), truncated at the stock's last available row (delisting stays IN)
No holding window is fitted: every position is held 60 sessions.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(os.environ.get("PEAD_SRC", "C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"))
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
H = 60

ev = pd.read_parquet(SRC / "data/events/equity_events_1996_2013.parquet",
                     columns=["permno", "day0_date", "day0_status", "sue_primary", "sue_source", "anndats"])
ev = ev[(ev.day0_status == "ok") & ev.sue_primary.notna()].drop_duplicates(["permno", "day0_date"]).copy()
ev["day0_date"] = pd.to_datetime(ev["day0_date"])
ev = ev.sort_values("day0_date").reset_index(drop=True)

# ---- point-in-time SUE percentile: trailing 365d pool of strictly earlier day0s ----------------------
d0 = ev["day0_date"].to_numpy("datetime64[D]")
sue = ev["sue_primary"].to_numpy(float)
pct = np.full(len(ev), np.nan)
days = np.unique(d0)
lo = np.searchsorted(d0, days - np.timedelta64(365, "D"), "left")
hi = np.searchsorted(d0, days, "left")                     # strictly earlier than this day
for k, day in enumerate(days):
    a, b = lo[k], hi[k]
    m0, m1 = np.searchsorted(d0, day, "left"), np.searchsorted(d0, day, "right")
    if b - a < 500:
        continue
    pool = np.sort(sue[a:b])
    v = sue[m0:m1]
    pct[m0:m1] = (np.searchsorted(pool, v, "left") + np.searchsorted(pool, v, "right")) / 2.0 / len(pool)
ev["pit_sue_pct"] = pct

# ---- features from the vol panel (all are trailing / pre-day0 by construction; see audit notes) -------
vp = pd.read_parquet(SRC / "data/equity_vol_panel.parquet",
                     columns=["permno", "day0_date", "size_quintile", "liquidity_quintile", "avg_dollar_volume_21d",
                              "ivol_252d", "beta_252d", "ivol_252d_pct", "ff12_sector", "sue_rank_pct", "decile",
                              "holding_days", "ret_fwd_60d_mktadj", "hold_ret"])
vp["day0_date"] = pd.to_datetime(vp["day0_date"])
vp = vp.drop_duplicates(["permno", "day0_date"])
ev = ev.merge(vp, on=["permno", "day0_date"], how="inner")
ev = ev.rename(columns={"sue_rank_pct": "legacy_sue_rank_pct", "decile": "legacy_decile", "holding_days": "legacy_hold",
                        "hold_ret": "legacy_hold_ret_mktadj"})
ev["avg_dollar_volume_21d"] = ev["avg_dollar_volume_21d"].astype(float)

# ---- daily panel: raw stock returns and market ------------------------------------------------------
frames = [pd.read_parquet(SRC / f"data/normalized_equity/equity_{y}.parquet", columns=["permno", "date", "ret"])
          for y in range(1995, 2015)]
eq = pd.concat(frames, ignore_index=True).sort_values(["permno", "date"]).drop_duplicates(["permno", "date"]).reset_index(drop=True)
mk = pd.read_parquet(SRC / "data/normalized_equity/market_benchmark.parquet", columns=["date", "vwretd"]).drop_duplicates("date").sort_values("date")
cal = pd.DatetimeIndex(mk["date"].to_numpy())
eq["ret"] = eq["ret"].fillna(0.0)
perm = eq["permno"].to_numpy()
last_row = pd.Series(np.arange(len(eq))).groupby(perm).max()
key = eq[["permno", "date"]].reset_index().rename(columns={"index": "row"})
ev = ev.merge(key.rename(columns={"date": "day0_date"}), on=["permno", "day0_date"], how="inner")
row0 = ev["row"].to_numpy()
last = last_row.reindex(ev["permno"]).to_numpy()
n_fwd = np.minimum(H, last - row0)
ev["n_fwd"] = n_fwd
ev = ev[ev.n_fwd >= 1].reset_index(drop=True)
row0, n_fwd = ev["row"].to_numpy(), ev["n_fwd"].to_numpy()

rets = eq["ret"].to_numpy()
dates = eq["date"].to_numpy()
cal_idx = cal.searchsorted(dates)                           # calendar index of every panel row
rep = np.repeat(np.arange(len(ev)), n_fwd)
off = np.arange(n_fwd.sum()) - np.repeat(np.cumsum(n_fwd) - n_fwd, n_fwd)
flat_row = np.repeat(row0 + 1, n_fwd) + off
path = pd.DataFrame({"ev": rep.astype(np.int32), "cal": cal_idx[flat_row].astype(np.int32), "ret": rets[flat_row].astype(np.float32)})
path.to_parquet(OUT / "event_paths.parquet")

mret = mk["vwretd"].fillna(0.0).to_numpy()
cum_stock = np.bincount(rep, weights=np.log1p(np.clip(path["ret"].to_numpy(float), -0.99, None)), minlength=len(ev))
cum_mkt = np.bincount(rep, weights=np.log1p(mret[path["cal"].to_numpy()]), minlength=len(ev))
ev["ret_raw_hold"] = np.expm1(cum_stock)
ev["ret_mkt_hold"] = np.expm1(cum_mkt)
ev["excess_hold"] = ev["ret_raw_hold"] - ev["ret_mkt_hold"]
ev["entry_cal"] = cal.searchsorted(ev["day0_date"].to_numpy())
ev["exit_cal"] = path.groupby("ev")["cal"].max().reindex(range(len(ev))).to_numpy()
ev["ann_quarter"] = ev["day0_date"].dt.to_period("Q").astype(str)
ev = ev.drop(columns=["row"])
ev.to_parquet(OUT / "events_pit.parquet")
pd.DataFrame({"date": cal, "vwretd": mret}).to_parquet(OUT / "market.parquet")
print(len(ev), "events;", ev.pit_sue_pct.notna().mean().round(3), "with PIT sue pct;", len(path), "path rows")
print(ev[["pit_sue_pct", "legacy_sue_rank_pct"]].corr(method="spearman"))
print("mean 60d excess by PIT SUE decile:")
print(ev.groupby(pd.cut(ev.pit_sue_pct, np.linspace(0, 1, 11)), observed=True)["excess_hold"].agg(["mean", "count"]).round(4))
