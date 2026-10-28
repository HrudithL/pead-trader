"""EQ-26: how big is the day-0 closing bid-ask spread of the stocks the long-only book actually buys, vs the engine's cost table?

    python scripts/spread_costs.py [strategy]        (default steady_drift_plus)

Relative spread = (ask - bid) / midpoint from the CRSP daily file's closing quotes on day 0; half of it is the one-way crossing cost.
Compared by liquidity quintile with the engine's one-way bps (5/7/10/15/25 for quintiles 5..1; round trip is twice that).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import RUNS_DIR  # noqa: E402
from pead.core.gap_features import CRSP_DIR  # noqa: E402
from pead.engines.equity import COST_BPS  # noqa: E402


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "steady_drift_plus"
    pos = pd.read_parquet(RUNS_DIR / name / "positions.parquet", columns=["permno", "day0_date", "liquidity_quintile", "notional"])
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    frames = []
    for y in sorted(pos["day0_date"].dt.year.unique()):
        d = pd.read_parquet(CRSP_DIR / f"dsf_{y}.parquet", columns=["permno", "date", "bid", "ask"])
        d["date"] = pd.to_datetime(d["date"])
        frames.append(d)
    q = pd.concat(frames)
    m = pos.merge(q, left_on=["permno", "day0_date"], right_on=["permno", "date"], how="left")
    ok = (m["bid"] > 0) & (m["ask"] >= m["bid"])
    m["rel_spread"] = np.where(ok, (m["ask"] - m["bid"]) / ((m["ask"] + m["bid"]) / 2), np.nan)
    m["half_bps"] = m["rel_spread"] * 1e4 / 2
    m["engine_one_way_bps"] = m["liquidity_quintile"].map(COST_BPS)
    m["w"] = m["notional"].abs()
    g = m.groupby("liquidity_quintile")
    out = pd.DataFrame({
        "n": g.size(), "quote_share": g["rel_spread"].apply(lambda s: s.notna().mean()),
        "median_half_spread_bps": g["half_bps"].median(),
        "notional_wtd_half_spread_bps": g.apply(lambda x: np.average(x["half_bps"].dropna(), weights=x.loc[x["half_bps"].notna(), "w"])),
        "engine_one_way_bps": g["engine_one_way_bps"].first()}).round(2)
    tot_w = np.average(m["half_bps"].dropna(), weights=m.loc[m["half_bps"].notna(), "w"])
    tot_e = np.average(m["engine_one_way_bps"], weights=m["w"])
    p = RUNS_DIR / "spread_costs"
    p.mkdir(parents=True, exist_ok=True)
    out.to_csv(p / f"{name}.csv")
    print(out.to_string()); print(f"notional-weighted: half-spread {tot_w:.1f} bps vs engine one-way {tot_e:.1f} bps")


if __name__ == "__main__":
    main()
