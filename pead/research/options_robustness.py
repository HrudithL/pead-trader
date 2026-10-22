"""Conservative robustness test of the existing single-expiry Kelly picks.

This reads the canonical derived data through PEAD_DATA_DIR and writes only aggregate
research results to the current worktree. It does not rescan or copy OptionMetrics.
The resulting NAV recognizes P&L on settlement dates, not daily option marks; its
drawdown and volatility must not be compared to a daily marked portfolio.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

from pead.common.paths import DATA_DIR, REPO_ROOT


HORIZONS = (20, 40, 60)


def prepare_selected_trades(panel: pd.DataFrame, chain: pd.DataFrame,
                            calendar: pd.DatetimeIndex, hold: int):
    """Join contract metadata and require a real planned exit and executable quotes."""
    if hold not in (1, 5, 10, 20, 40, 60):
        raise ValueError("hold must have a fetched checkpoint")
    p = panel.copy()
    p["day0_date"] = pd.to_datetime(p["day0_date"])
    extra = [x for x in ("entry_oi", "entry_volume") if x in chain.columns]
    c = chain[["secid", "day0_date", "optionid", "exdate", "strike",
               "underlying_price", *extra]].drop_duplicates(["secid", "day0_date", "optionid"]).copy()
    c["day0_date"] = pd.to_datetime(c["day0_date"])
    c["optionid"] = c["optionid"].astype("int64")
    c["exdate"] = pd.to_datetime(c["exdate"])
    p = p.merge(c, left_on=["secid", "day0_date", "kelly_optionid"],
                right_on=["secid", "day0_date", "optionid"], how="left", validate="many_to_one")
    idx = calendar.get_indexer(p["day0_date"])
    valid_calendar = (idx >= 0) & (idx + hold < len(calendar))
    exit_dates = np.full(len(p), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    exit_dates[valid_calendar] = calendar.to_numpy()[idx[valid_calendar] + hold]
    p["entry_date"] = p["day0_date"]
    p["exit_date"] = pd.to_datetime(exit_dates)
    gap = np.zeros(len(p), dtype=bool)
    gaps = np.r_[False, np.diff(calendar.to_numpy()).astype("timedelta64[D]").astype(int) > 10]
    prefix = np.cumsum(gaps)
    gap[valid_calendar] = (prefix[idx[valid_calendar] + hold] - prefix[idx[valid_calendar]]) > 0
    ask = p["kelly_fwd_ask_0d"].to_numpy(dtype=float)
    bid = p[f"kelly_fwd_bid_{hold}d"].to_numpy(dtype=float)
    invalid_ask = ~np.isfinite(ask) | (ask <= 0)
    missing_bid = ~np.isfinite(bid) | (bid < 0)
    expiry = p["exdate"].isna() | (p["exdate"] <= p["exit_date"])
    metadata = p["underlying_price"].isna() | (p["underlying_price"] <= 0)
    audit = {
        "input": int(len(p)),
        "invalid_calendar": int((~valid_calendar).sum()),
        "coverage_gap": int(gap.sum()),
        "missing_entry_ask": int(invalid_ask.sum()),
        "missing_exit_bid": int(missing_bid.sum()),
        "expiry_before_exit": int(expiry.sum()),
        "missing_contract_metadata": int(metadata.sum()),
        "zero_exit_bid": int((bid == 0).sum()),
    }
    # Future quote availability is unknown at entry. Keep missing exits and
    # assign zero proceeds as the conservative, nonfillable scenario.
    keep = valid_calendar & ~gap & ~invalid_ask & ~expiry & ~metadata
    out = p.loc[keep].copy()
    out["entry_ask"] = ask[keep]
    out["exit_bid_missing"] = missing_bid[keep]
    out["exit_bid"] = np.where(missing_bid[keep], 0.0, bid[keep])
    out["moneyness"] = out["strike"] / out["underlying_price"]
    audit["eligible"] = int(len(out))
    return out.reset_index(drop=True), audit


def simulate_trades(trades: pd.DataFrame, calendar: pd.DatetimeIndex,
                    initial_capital: float = 10_000_000.0, cap: float = 0.20,
                    max_trade_frac: float = 0.0025, hold: int = 60,
                    max_oi_frac: float | None = None):
    """Size entries sequentially using only prior events and already settled P&L.

    Outstanding premium is capped at ``cap * realized NAV``. Settlement-date NAV is
    a cash accounting diagnostic, not a mark-to-market NAV or a Sharpe estimator.
    """
    t = trades.sort_values(["entry_date", "secid", "optionid"], kind="stable").reset_index(drop=True).copy()
    t["contracts"] = 0
    t["premium_paid"] = 0.0
    t["net_pnl"] = 0.0
    if t.empty:
        return t, pd.DataFrame({"date": calendar, "realized_pnl": 0.0,
                                "settled_nav": initial_capital})
    entry_groups = t.groupby("entry_date", sort=False).indices
    open_trades: dict[int, tuple[pd.Timestamp, float, float]] = {}
    prior_entries: deque[pd.Timestamp] = deque()
    nav = float(initial_capital)
    rows = []
    for date in calendar:
        while prior_entries and (date - prior_entries[0]).days > 365:
            prior_entries.popleft()
        for i in entry_groups.get(date, []):
            ask = float(t.at[i, "entry_ask"])
            bid = float(t.at[i, "exit_bid"])
            outstanding = sum(x[1] for x in open_trades.values())
            trailing_n = max(1, len(prior_entries))
            target_frac = min(max_trade_frac, cap * 252 / (trailing_n * hold))
            budget = min(target_frac * nav, max(0.0, cap * nav - outstanding))
            lots = max(0, int(np.floor(budget / (100 * ask))))
            if max_oi_frac is not None:
                oi = t.at[i, "entry_oi"]
                lots = min(lots, max(0, int(np.floor(max_oi_frac * oi))) if pd.notna(oi) else 0)
            premium = lots * 100 * ask
            pnl = lots * 100 * (bid - ask)
            t.at[i, "contracts"] = lots
            t.at[i, "premium_paid"] = premium
            t.at[i, "net_pnl"] = pnl
            if lots:
                open_trades[i] = (t.at[i, "exit_date"], premium, pnl)
            prior_entries.append(date)
        # Entries and exits use closing quotes. Proceeds from today's exits
        # become available for sizing only on the next trading session.
        realized = 0.0
        for i, (end, premium, pnl) in list(open_trades.items()):
            if end == date:
                realized += pnl
                del open_trades[i]
        nav += realized
        rows.append((date, realized, nav))
    daily = pd.DataFrame(rows, columns=["date", "realized_pnl", "settled_nav"])
    return t, daily


def prior_quarter_low_vol_cutoffs(features: pd.DataFrame) -> pd.Series:
    """One-third quantile from strictly earlier quarters, with an eight-quarter warmup."""
    f = features.dropna(subset=["ann_quarter", "tvol_60d"]).copy()
    quarters = sorted(f["ann_quarter"].unique())
    cutoff = {}
    for j, q in enumerate(quarters):
        if j >= 8:
            cutoff[q] = float(f.loc[f["ann_quarter"] < q, "tvol_60d"].quantile(1 / 3))
    return pd.Series(cutoff, name="prior_low_vol_cutoff")


def summarize_settled(positions: pd.DataFrame, daily: pd.DataFrame) -> dict:
    funded = positions[positions.contracts > 0]
    years = (max((funded.exit_date.max() - funded.entry_date.min()).days / 365.25,
                 1 / 365.25) if len(funded) else 1.0)
    total = float(funded.net_pnl.sum())
    start = float(daily.settled_nav.iloc[0] - daily.realized_pnl.iloc[0])
    missing = (funded.exit_bid_missing.astype(bool) if "exit_bid_missing" in funded
               else pd.Series(False, index=funded.index))
    return {
        "funded_trades": int(len(funded)),
        "premium_paid": float(funded.premium_paid.sum()),
        "net_pnl": total,
        "roi_on_premium": total / funded.premium_paid.sum() if funded.premium_paid.sum() else None,
        "calendar_cagr_from_settlements": float((1 + total / start) ** (1 / years) - 1)
        if 1 + total / start > 0 else -1.0,
        "quoted_zero_bid_funded": int(((funded.exit_bid == 0) & ~missing).sum()),
        "missing_bid_funded": int(missing.sum()),
    }


def run(data_dir: Path, output_dir: Path):
    base = data_dir / "event_options"
    panel = pd.read_parquet(base / "optimal_forward_prices.parquet")
    chain = pd.read_parquet(base / "full_chain_with_underlying.parquet",
                            columns=["secid", "day0_date", "optionid", "exdate", "strike",
                                     "underlying_price", "entry_oi", "entry_volume"])
    feats = pd.read_parquet(base / "event_vol_features.parquet",
                            columns=["secid", "day0_date", "tvol_60d", "ann_quarter"])
    feats["day0_date"] = pd.to_datetime(feats["day0_date"])
    panel = panel.merge(feats, on=["secid", "day0_date"], how="left", validate="many_to_one")
    panel["prior_low_vol_cutoff"] = panel["ann_quarter"].map(prior_quarter_low_vol_cutoffs(feats))
    calendar = pd.DatetimeIndex(pd.read_parquet(data_dir / "metadata" / "om_trading_calendar.parquet")["date"])
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, audits, yearly = [], {}, []
    for hold in HORIZONS:
        prepared, audit = prepare_selected_trades(panel, chain, calendar, hold)
        audits[str(hold)] = audit
        rules = {
            "all_selected": prepared.kelly_growth > 0,
            "moderate_otm_low_vol_call": (
                (prepared.kelly_growth > 0) & (prepared.kelly_side == "C")
                & prepared.decile.ge(9) & prepared.moneyness.between(1.03, 1.20)
                & prepared.tvol_60d.lt(prepared.prior_low_vol_cutoff)
            ),
        }
        for name, mask in rules.items():
            for capacity, oi_frac in (("unrestricted", None), ("oi10", 0.10)):
                positions, daily = simulate_trades(prepared.loc[mask], calendar,
                                                    hold=hold, max_oi_frac=oi_frac)
                summary = summarize_settled(positions, daily)
                rows.append({"rule": name, "hold": hold, "capacity": capacity, **summary})
                funded = positions[positions.contracts > 0].copy()
                funded["exit_year"] = funded.exit_date.dt.year
                for year, group in funded.groupby("exit_year"):
                    yearly.append({"rule": name, "hold": hold, "capacity": capacity, "year": int(year),
                                   "trades_settled": len(group),
                                   "entry_premium_of_settled": float(group.premium_paid.sum()),
                                   "net_pnl_realized": float(group.net_pnl.sum())})
    pd.DataFrame(rows).to_csv(output_dir / "summary.csv", index=False)
    pd.DataFrame(yearly).to_csv(output_dir / "yearly.csv", index=False)
    (output_dir / "audit.json").write_text(json.dumps(audits, indent=2), encoding="utf-8")
    return pd.DataFrame(rows), audits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "results" / "options_robustness")
    args = parser.parse_args()
    summary, _ = run(args.data_dir, args.output_dir)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
