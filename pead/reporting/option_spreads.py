"""Option liquidity / spread diagnostics (OPT-01, REF-spread-diag): the gate for OPT-02..06.

`pick_contracts` selects, for each (secid, date) chain, the contract nearest a target |delta| inside a DTE window
and with a live bid; `spread_report` summarises their relative spreads and the round-trip cost expressed as a
fraction of the underlying move needed to break even. Pure functions; `scripts/option_spread_report.py` feeds
them from the OptionMetrics extract on D:.
"""
import numpy as np
import pandas as pd

# name -> (cp_flag, target |delta|, dte_lo, dte_hi)
BUCKETS = {
    "call_atm_30_60d": ("C", 0.50, 30, 60),
    "call_atm_7_30d": ("C", 0.50, 7, 30),
    "call_itm80_30_90d": ("C", 0.80, 30, 90),
    "call_itm90_30_90d": ("C", 0.90, 30, 90),
    "call_otm30_30_60d": ("C", 0.30, 30, 60),
    "put_atm_30_60d": ("P", 0.50, 30, 60),
}


def rel_spread(bid, ask):
    return (ask - bid) / ((ask + bid) / 2.0)


def pick_contracts(chain: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    """chain: secid, date, exdate, cp_flag, best_bid, best_offer, delta, open_interest, optionid.
    spot: secid, date, underlying_price. Returns one row per (secid, date, bucket) with the chosen contract."""
    c = chain.merge(spot, on=["secid", "date"], how="inner")
    c = c[(c["best_bid"] > 0) & (c["best_offer"] >= c["best_bid"]) & c["delta"].notna()].copy()
    c["dte"] = (pd.to_datetime(c["exdate"]) - pd.to_datetime(c["date"])).dt.days
    c["mid"] = (c["best_bid"] + c["best_offer"]) / 2
    c["rel_spread"] = rel_spread(c["best_bid"], c["best_offer"])
    c["abs_delta"] = c["delta"].abs()
    out = []
    for name, (cp, tgt, lo, hi) in BUCKETS.items():
        x = c[(c["cp_flag"] == cp) & c["dte"].between(lo, hi)].copy()
        if x.empty:
            continue
        x["dist"] = (x["abs_delta"] - tgt).abs()
        x = x[x["dist"] <= 0.12]
        x = x.sort_values(["secid", "date", "dist", "open_interest", "optionid"], ascending=[True, True, True, False, True])
        best = x.groupby(["secid", "date"], sort=False).head(1).copy()
        best["bucket"] = name
        out.append(best[["secid", "date", "bucket", "best_bid", "best_offer", "mid", "rel_spread", "abs_delta", "dte",
                         "open_interest", "underlying_price"]])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def spread_report(picks: pd.DataFrame, n_events: int = None, max_rel_spread=(0.05, 0.10, 0.20)) -> pd.DataFrame:
    """Per bucket: coverage (share of events with a live-bid contract), relative-spread quantiles, share passing each
    spread filter, and the median breakeven move = round-trip crossing cost (ask-bid) / (|delta| x spot)."""
    p = picks.copy()
    p["breakeven_move"] = (p["best_offer"] - p["best_bid"]) / (p["abs_delta"] * p["underlying_price"])
    rows = []
    for b, g in p.groupby("bucket"):
        row = dict(bucket=b, n=len(g), coverage=len(g) / n_events if n_events else np.nan,
                   rel_spread_p25=g["rel_spread"].quantile(.25), rel_spread_median=g["rel_spread"].median(),
                   rel_spread_p75=g["rel_spread"].quantile(.75), rel_spread_p90=g["rel_spread"].quantile(.90),
                   breakeven_move_median=g["breakeven_move"].median(), breakeven_move_p75=g["breakeven_move"].quantile(.75),
                   median_open_interest=g["open_interest"].median())
        for m in max_rel_spread:
            row[f"share_spread_le_{int(m * 100)}pct"] = float((g["rel_spread"] <= m).mean())
        rows.append(row)
    return pd.DataFrame(rows)
