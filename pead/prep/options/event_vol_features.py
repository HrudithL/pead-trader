"""
Per-event volatility features for the options side: what the day0 option chain says the stock's
volatility is (implied), what it has actually been (realized), and the ratio between them.

For every (secid, day0_date) event in data/event_options/full_chain_with_underlying.parquet:
  - tvol_60d     daily std of the stock's raw returns over the 60 trading days before day0
                 (common.volatility -- the same leak-free measure the equity side and
                 46 --vol-conditioned use), and rv_ann = tvol_60d * sqrt(252)
  - atm_iv       average OptionMetrics implied vol of the call with delta closest to +0.50 and the
                 put with delta closest to -0.50 at the chain's selected expiration (either one
                 alone if only one side is quoted)
  - iv_rv        atm_iv / rv_ann: how expensive the day0 options are relative to the volatility the
                 stock has actually been delivering. Options are typically priced above realized vol
                 (the variance risk premium); a high ratio means an option buyer is paying for more
                 movement than the stock has recently shown. 50_options_backtest_optimal.py's
                 --iv-rv-filter uses this.
  - ann_quarter  the event's announcement quarter, for prior-quarter breakpoints downstream

Output: data/event_options/event_vol_features.parquet (one row per event)
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pead.common.paths import DATA_DIR, RAW_EQUITY_DIR
from pead.common.volatility import event_total_vol

OUT_DIR = DATA_DIR / "event_options"


def main():
    t0 = time.time()
    chain = pd.read_parquet(OUT_DIR / "full_chain_with_underlying.parquet",
                             columns=["secid", "day0_date", "permno", "cp_flag", "delta", "iv"])
    chain["day0_date"] = pd.to_datetime(chain["day0_date"])
    c = chain[chain["iv"].notna() & (chain["iv"] > 0) & chain["delta"].notna()].copy()
    c["target"] = np.where(c["cp_flag"] == "C", 0.5, -0.5)
    c["dist"] = (c["delta"] - c["target"]).abs()
    atm = (c.sort_values(["secid", "day0_date", "cp_flag", "dist"], kind="mergesort")
            .groupby(["secid", "day0_date", "cp_flag"], as_index=False).head(1)
            .groupby(["secid", "day0_date"], as_index=False)["iv"].mean()
            .rename(columns={"iv": "atm_iv"}))

    ev = chain.drop_duplicates(subset=["secid", "day0_date"])[["secid", "day0_date", "permno"]]
    ev = ev.merge(atm, on=["secid", "day0_date"], how="left")
    ev["tvol_60d"] = event_total_vol(ev["permno"], ev["day0_date"], RAW_EQUITY_DIR)
    ev["rv_ann"] = ev["tvol_60d"] * np.sqrt(252)
    ev["iv_rv"] = ev["atm_iv"] / ev["rv_ann"]

    anndats = pd.read_parquet(OUT_DIR / "decile_events_secid_all.parquet",
                               columns=["secid", "day0_date", "anndats"]).dropna(subset=["secid"])
    anndats["secid"] = anndats["secid"].astype("int64")
    anndats["day0_date"] = pd.to_datetime(anndats["day0_date"])
    anndats["ann_quarter"] = pd.to_datetime(anndats["anndats"]).dt.to_period("Q").astype(str)
    anndats = anndats.drop_duplicates(subset=["secid", "day0_date"])[["secid", "day0_date", "ann_quarter"]]
    ev["secid"] = ev["secid"].astype("int64")
    n = len(ev)
    ev = ev.merge(anndats, on=["secid", "day0_date"], how="left")
    assert len(ev) == n

    out = OUT_DIR / "event_vol_features.parquet"
    ev.to_parquet(out, index=False)
    print(f"{len(ev):,} events: atm_iv coverage {ev['atm_iv'].notna().mean():.1%}, "
          f"tvol coverage {ev['tvol_60d'].notna().mean():.1%}  [{time.time()-t0:.1f}s]")
    print(ev[["atm_iv", "rv_ann", "iv_rv"]].describe(percentiles=[.1, .25, .5, .75, .9]).round(3).to_string())
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
