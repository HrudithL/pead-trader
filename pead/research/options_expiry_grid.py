"""Stream the external OptionMetrics drive for a fixed strike/expiry/hold grid.

Only aggregate research tables are written. No raw or extracted option quotes are
copied from the drive. Grid returns are per-trade diagnostics, not portfolio NAV,
Sharpe, alpha, or evidence that the best historical cell will work in the future.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from pead.common.paths import DATA_DIR, OPTIONMETRICS_DIR, REPO_ROOT
from pead.prep.options.lib.options import OPPRCD_COLS, scan_year_for_keys
from pead.research.options_robustness import simulate_trades, summarize_settled


DTE_TARGETS = (75, 120, 180)
DELTA_TARGETS = (0.30, 0.50, 0.70)
HOLDS = (20, 40, 60)


def exclude_duplicate_event_dates(events: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Exclude all repeated security/session clusters and summarize collisions.

    Event rows can represent different fiscal quarters reported by one issuer on
    the same day. The daily option quote cannot be uniquely assigned to one row,
    and opposite SUE directions must not be resolved by parquet row order.
    """
    keys = ["secid", "day0_date"]
    valid = events.dropna(subset=keys)
    counts = valid.groupby(keys, dropna=False).size()
    duplicate_counts = counts[counts.gt(1)]
    duplicate_keys = duplicate_counts.index
    row_keys = pd.MultiIndex.from_frame(events[keys])
    duplicate_mask = row_keys.isin(duplicate_keys)
    duplicate_rows = events.loc[duplicate_mask].copy()
    if len(duplicate_rows):
        duplicate_deciles = pd.to_numeric(duplicate_rows.decile, errors="coerce")
        duplicate_rows["_direction"] = np.select(
            [duplicate_deciles.ge(9), duplicate_deciles.le(2)], ["C", "P"], default="")
        eligible_directions = duplicate_rows[duplicate_rows._direction.ne("")]
        direction_counts = eligible_directions.groupby(keys)._direction.nunique()
        decile_counts = duplicate_rows.groupby(keys).decile.nunique()
        opposing_groups = int(direction_counts.gt(1).sum())
        multiple_decile_groups = int(decile_counts.gt(1).sum())
    else:
        opposing_groups = multiple_decile_groups = 0
    clean = events.loc[~duplicate_mask].copy()
    audit = {
        "input_events": int(len(events)),
        "retained_events": int(len(clean)),
        "duplicate_event_date_groups": int(len(duplicate_counts)),
        "duplicate_extra_rows": int(duplicate_counts.sum() - len(duplicate_counts)),
        "duplicate_event_rows_excluded": int(duplicate_counts.sum()),
        "eligible_signal_rows_excluded_by_duplicates": int(
            duplicate_rows.decile.isin([1, 2, 9, 10]).sum()),
        "opposing_signal_groups": opposing_groups,
        "multiple_decile_groups": multiple_decile_groups,
        "policy": "exclude every row in a repeated secid/day0_date cluster",
    }
    return clean, audit


def prepare_strategy_events(events: pd.DataFrame, years: list[int]) -> tuple[pd.DataFrame, dict]:
    """Filter run years, remove source-date collisions, then select signal deciles.

    Duplicate detection must see every source decile. Filtering to extreme SUE
    rows first can hide a same-day report with a middle decile and leave an
    ambiguous event in the strategy sample.
    """
    events = events.dropna(subset=["secid", "day0_date"]).copy()
    events["secid"] = events.secid.astype("int64")
    events = events[events.day0_date.dt.year.isin(years)].copy()
    eligible_before = int(events.decile.isin([1, 2, 9, 10]).sum())
    clean, audit = exclude_duplicate_event_dates(events)
    selected = clean[clean.decile.isin([1, 2, 9, 10])].copy()
    audit["eligible_signal_rows_before_exclusion"] = eligible_before
    audit["eligible_signal_rows_after_exclusion"] = int(len(selected))
    return selected, audit


def select_entry_grid(raw: pd.DataFrame, events: pd.DataFrame,
                      dte_targets=DTE_TARGETS, delta_targets=DELTA_TARGETS,
                      max_dte_distance: int = 25, min_oi: int = 100,
                      max_spread_fraction: float = 0.30,
                      max_delta_distance: float = 0.10) -> pd.DataFrame:
    """Pick one quote per event/direction/DTE/delta cell using entry-known fields."""
    if raw.empty:
        return pd.DataFrame()
    r = raw.copy()
    r["date"] = pd.to_datetime(r["date"])
    r["exdate"] = pd.to_datetime(r["exdate"])
    e = events[["secid", "day0_date", "decile", "underlying_price"]].copy()
    if e.duplicated(["secid", "day0_date"]).any():
        raise ValueError("event dates must be unique; exclude duplicate security/date clusters first")
    e["day0_date"] = pd.to_datetime(e["day0_date"])
    e["direction"] = np.select([e.decile.ge(9), e.decile.le(2)], ["C", "P"], default="")
    e = e[e.direction.ne("")]
    r = r.merge(e, left_on=["secid", "date"], right_on=["secid", "day0_date"],
                how="inner", validate="many_to_one")
    r["dte"] = (r.exdate - r.date).dt.days
    r["spread_fraction"] = (r.best_offer - r.best_bid) / r.best_offer
    r = r[(r.cp_flag == r.direction) & r.delta.notna()
          & r.best_bid.gt(0) & r.best_offer.ge(r.best_bid)
          & r.best_offer.ge(0.10) & r.spread_fraction.le(max_spread_fraction)
          & r.open_interest.ge(min_oi) & r.dte.gt(0)].copy()
    if r.empty:
        return r
    out = []
    keys = ["secid", "day0_date", "dte_target", "delta_target"]
    for dte in dte_targets:
        q = r[(r.dte - dte).abs().le(max_dte_distance)].copy()
        q["dte_target"] = dte
        q["dte_error"] = (q.dte - dte).abs()
        for delta in delta_targets:
            z = q.copy()
            z["delta_target"] = delta
            z["delta_error"] = (z.delta.abs() - delta).abs()
            z = z[z.delta_error.le(max_delta_distance)]
            z = z.sort_values(keys + ["dte_error", "delta_error", "spread_fraction",
                                      "optionid"], kind="stable")
            out.append(z.drop_duplicates(keys))
    if not out:
        return pd.DataFrame()
    result = pd.concat(out, ignore_index=True)
    result["entry_ask"] = result.best_offer
    result["entry_bid"] = result.best_bid
    result["strike"] = result.strike_price / 1000.0
    result["moneyness"] = result.strike / result.underlying_price
    return result[["secid", "day0_date", "decile", "direction", "optionid", "cp_flag",
                   "exdate", "dte", "dte_target", "delta", "delta_target", "strike",
                   "moneyness", "underlying_price", "entry_ask", "entry_bid",
                   "open_interest", "volume", "spread_fraction"]]


def load_underlying_spot(year: int, events: pd.DataFrame,
                         optionmetrics_dir: Path) -> pd.DataFrame:
    """Read secprd for all linked events, including those lacking an old 120-DTE chain."""
    path = optionmetrics_dir / f"secprd{year}.parquet"
    spot = pq.read_table(path, columns=["secid", "date", "close"]).to_pandas()
    spot["date"] = pd.to_datetime(spot["date"])
    keys = events[["secid", "day0_date"]].drop_duplicates().rename(
        columns={"day0_date": "date"}).copy()
    matched = spot.merge(keys, on=["secid", "date"], how="inner")
    matched = matched.rename(columns={"date": "day0_date", "close": "underlying_price"})
    return matched[["secid", "day0_date", "underlying_price"]].drop_duplicates(
        ["secid", "day0_date"])


def attach_exit_dates(candidates: pd.DataFrame, calendar: pd.DatetimeIndex,
                      holds=HOLDS, expiry_buffer_days: int = 10) -> pd.DataFrame:
    """Expand hold cells; reject unpriced calendar gaps and near-expiry exits."""
    if candidates.empty:
        return candidates.copy()
    frames = []
    cal = pd.DatetimeIndex(calendar).sort_values()
    gap = np.r_[False, np.diff(cal.to_numpy()).astype("timedelta64[D]").astype(int) > 10]
    prefix = np.cumsum(gap)
    for hold in holds:
        t = candidates.copy()
        idx = cal.get_indexer(pd.to_datetime(t.day0_date))
        valid = (idx >= 0) & (idx + hold < len(cal))
        t = t.loc[valid].copy()
        ii = idx[valid]
        no_gap = (prefix[ii + hold] - prefix[ii]) == 0
        t = t.loc[no_gap].copy()
        ii = ii[no_gap]
        t["hold"] = hold
        t["exit_date"] = cal.to_numpy()[ii + hold]
        remaining = (t.exdate - t.exit_date).dt.days
        frames.append(t.loc[remaining.ge(expiry_buffer_days)])
    return pd.concat(frames, ignore_index=True) if frames else candidates.iloc[0:0].copy()


def scan_exit_quotes(year: int, lookup: pd.DataFrame, optionmetrics_dir: Path) -> pd.DataFrame:
    """Stream a year and retain only required (secid,date,optionid) bids in memory."""
    path = optionmetrics_dir / f"opprcd{year}.parquet"
    keys = lookup[["secid", "exit_date", "optionid"]].drop_duplicates().rename(
        columns={"exit_date": "date"}).copy()
    keys["date"] = pd.to_datetime(keys.date)
    keys["optionid"] = keys.optionid.astype("int64")
    matches = []
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(columns=["secid", "date", "optionid", "best_bid"],
                                 batch_size=1_000_000):
        b = batch.to_pandas(date_as_object=False)
        matched = b.merge(keys, on=["secid", "date", "optionid"], how="inner")
        if len(matched):
            matches.append(matched)
    if not matches:
        return pd.DataFrame(columns=["secid", "date", "optionid", "best_bid"])
    return pd.concat(matches, ignore_index=True).drop_duplicates(["secid", "date", "optionid"])


def aggregate_outcomes(contracts: pd.DataFrame, exit_quotes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Summarize ask-to-bid payoffs under a 10%-of-OI premium capacity proxy."""
    q = exit_quotes.rename(columns={"date": "exit_date", "best_bid": "exit_bid"})
    t = contracts.merge(q, on=["secid", "exit_date", "optionid"], how="left", validate="many_to_one")
    t["missing_bid"] = t.exit_bid.isna()
    t["quoted_zero_bid"] = t.exit_bid.eq(0)
    t["exit_bid"] = t.exit_bid.fillna(0).clip(lower=0)
    t["lots"] = np.floor(t.open_interest * 0.10).clip(lower=0)
    t["premium"] = t.lots * 100 * t.entry_ask
    t["net_pnl"] = t.lots * 100 * (t.exit_bid - t.entry_ask)
    t["year"] = t.day0_date.dt.year
    keys = ["direction", "dte_target", "delta_target", "hold"]

    def grouped(frame, group_keys):
        out = frame.groupby(group_keys, observed=True).agg(
            trades=("optionid", "size"), funded=("lots", lambda v: int((v > 0).sum())),
            premium=("premium", "sum"), net_pnl=("net_pnl", "sum"),
            zero_bids=("quoted_zero_bid", "sum"),
            missing_bids=("missing_bid", "sum"),
        ).reset_index()
        out["roi_on_premium"] = out.net_pnl / out.premium.replace(0, np.nan)
        return out

    return grouped(t, keys), grouped(t, ["year", *keys])


def paired_cell_comparisons(contracts: pd.DataFrame,
                            exit_quotes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Equal-premium ROI differences on the exact same event and direction."""
    q = exit_quotes.rename(columns={"date": "exit_date", "best_bid": "exit_bid"})
    t = contracts.merge(q, on=["secid", "exit_date", "optionid"], how="left", validate="many_to_one")
    t["roi"] = t.exit_bid.fillna(0).clip(lower=0) / t.entry_ask - 1
    comparisons = []
    specs = [
        ("delta_target", [(0.30, 0.50), (0.50, 0.70)], ["dte_target", "hold"]),
        ("dte_target", [(75, 120), (120, 180)], ["delta_target", "hold"]),
        ("hold", [(20, 40), (40, 60)], ["dte_target", "delta_target"]),
    ]
    for axis, pairs, fixed in specs:
        index = ["secid", "day0_date", "direction", *fixed]
        for first, second in pairs:
            z = t[t[axis].isin([first, second])]
            if z.empty:
                continue
            wide = z.pivot_table(index=index, columns=axis, values="roi", aggfunc="first")
            if first not in wide or second not in wide:
                continue
            paired = wide[[first, second]].dropna().reset_index()
            if paired.empty:
                continue
            paired["roi_difference"] = paired[first] - paired[second]
            paired["axis"] = axis
            paired["first_cell"] = first
            paired["second_cell"] = second
            paired["year"] = paired.day0_date.dt.year
            for other in ("dte_target", "delta_target", "hold"):
                if other == axis:
                    paired[other] = -1
            comparisons.append(paired[["year", "direction", "axis", "first_cell", "second_cell",
                                       "dte_target", "delta_target", "hold", "roi_difference"]])
    cols = ["year", "direction", "axis", "first_cell", "second_cell",
            "dte_target", "delta_target", "hold", "roi_difference"]
    if not comparisons:
        return pd.DataFrame(columns=cols), pd.DataFrame(columns=cols)
    all_pairs = pd.concat(comparisons, ignore_index=True)
    keys = ["direction", "axis", "first_cell", "second_cell",
            "dte_target", "delta_target", "hold"]

    def grouped(group_keys):
        return all_pairs.groupby(group_keys, observed=True).agg(
            paired_events=("roi_difference", "size"),
            mean_roi_difference=("roi_difference", "mean"),
            median_roi_difference=("roi_difference", "median"),
            fraction_first_better=("roi_difference", lambda v: float((v > 0).mean())),
        ).reset_index()

    return grouped(keys), grouped(["year", *keys])


def portfolio_outcomes(contracts: pd.DataFrame, exit_quotes: pd.DataFrame,
                       calendar: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Size each grid cell as a separate, sequential 20%-premium-cap portfolio."""
    q = exit_quotes.rename(columns={"date": "exit_date", "best_bid": "exit_bid"})
    t = contracts.merge(q, on=["secid", "exit_date", "optionid"], how="left",
                        validate="many_to_one").copy()
    t["exit_bid_missing"] = t.exit_bid.isna()
    t["exit_bid"] = t.exit_bid.fillna(0).clip(lower=0)
    t["entry_date"] = t.day0_date
    t["entry_oi"] = t.open_interest
    keys = ["direction", "dte_target", "delta_target", "hold"]
    summaries, yearly = [], []
    for cell, group in t.groupby(keys, observed=True):
        positions, daily = simulate_trades(group, calendar, hold=int(cell[3]),
                                           max_oi_frac=0.10)
        summaries.append(dict(zip(keys, cell)) | summarize_settled(positions, daily))
        funded = positions[positions.contracts > 0].copy()
        funded["exit_year"] = funded.exit_date.dt.year
        for year, settled in funded.groupby("exit_year"):
            yearly.append(dict(zip(keys, cell)) | {
                "year": int(year), "trades_settled": len(settled),
                "entry_premium_of_settled": float(settled.premium_paid.sum()),
                "net_pnl_realized": float(settled.net_pnl.sum()),
            })
    return pd.DataFrame(summaries), pd.DataFrame(yearly)


def run(years: list[int], data_dir: Path, optionmetrics_dir: Path,
        output_dir: Path, dte_targets=DTE_TARGETS, delta_targets=DELTA_TARGETS,
        holds=HOLDS, direction: str | None = None, portfolio: bool = False,
        daily_portfolio: bool = False, daily_grid: bool = False,
        sizing_multipliers: tuple[float, ...] | None = None):
    if sizing_multipliers and not daily_portfolio:
        raise ValueError("--sizing-multipliers requires --daily-portfolio")
    base = data_dir / "event_options"
    events = pd.read_parquet(base / "decile_events_secid.parquet",
                             columns=["secid", "day0_date", "decile"])
    events, event_collision_audit = prepare_strategy_events(events, years)
    print("event date collision audit: " + json.dumps(event_collision_audit, sort_keys=True), flush=True)
    spot = pd.concat([load_underlying_spot(y, events[events.day0_date.dt.year == y],
                                           optionmetrics_dir) for y in years], ignore_index=True)
    events = events.merge(spot, on=["secid", "day0_date"], how="inner", validate="many_to_one")
    entries = []
    for year in years:
        ev = events[events.day0_date.dt.year == year]
        if ev.empty:
            continue
        raw = scan_year_for_keys(year, ev.rename(columns={"day0_date": "date"}),
                                 columns=OPPRCD_COLS, optionmetrics_dir=optionmetrics_dir)
        chosen = select_entry_grid(raw, ev, dte_targets, delta_targets)
        if direction is not None and len(chosen):
            chosen = chosen[chosen.direction.eq(direction)].copy()
        print(f"entry {year}: {len(ev):,} events, {len(chosen):,} grid quotes", flush=True)
        if len(chosen):
            entries.append(chosen)
    if not entries:
        raise ValueError("No valid entry contracts for the requested years")
    option_calendar = pd.DatetimeIndex(
        pd.read_parquet(data_dir / "metadata" / "om_trading_calendar.parquet").date)
    market_calendar = pd.DatetimeIndex(
        pd.read_parquet(data_dir / "normalized_equity" / "market_benchmark.parquet",
                        columns=["date"]).date)
    excluded = option_calendar.difference(market_calendar)
    if len(excluded):
        print(f"excluding {len(excluded)} OptionMetrics dates absent from CRSP market calendar: "
              f"{[str(d.date()) for d in excluded]}", flush=True)
    calendar = option_calendar.intersection(market_calendar)
    candidates = attach_exit_dates(pd.concat(entries, ignore_index=True), calendar, holds)
    print(f"exit lookups: {len(candidates):,}", flush=True)
    exits = []
    for year, grp in candidates.groupby(candidates.exit_date.dt.year):
        quotes = scan_exit_quotes(int(year), grp, optionmetrics_dir)
        print(f"exit {year}: {len(grp):,} lookups, {len(quotes):,} matched quotes", flush=True)
        exits.append(quotes)
    quotes = pd.concat(exits, ignore_index=True)
    summary, yearly = aggregate_outcomes(candidates, quotes)
    paired, paired_yearly = paired_cell_comparisons(candidates, quotes)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "event_collision_audit.json").write_text(
        json.dumps(event_collision_audit, indent=2), encoding="utf-8")
    summary.to_csv(output_dir / "grid_summary.csv", index=False)
    yearly.to_csv(output_dir / "grid_yearly.csv", index=False)
    paired.to_csv(output_dir / "paired_summary.csv", index=False)
    paired_yearly.to_csv(output_dir / "paired_yearly.csv", index=False)
    if portfolio:
        portfolios, portfolio_yearly = portfolio_outcomes(candidates, quotes, calendar)
        portfolios.to_csv(output_dir / "portfolio_summary.csv", index=False)
        portfolio_yearly.to_csv(output_dir / "portfolio_yearly.csv", index=False)
        print(portfolios.to_string(index=False))
    if daily_portfolio:
        from pead.research.options_daily_portfolio import run_daily_portfolio
        cells = candidates[["direction", "dte_target", "delta_target", "hold"]].drop_duplicates()
        if len(cells) != 1:
            raise ValueError("--daily-portfolio requires exactly one direction/DTE/delta/hold cell")
        q = quotes.rename(columns={"date": "exit_date", "best_bid": "exit_bid"})
        detail = candidates.merge(q, on=["secid", "exit_date", "optionid"],
                                  how="left", validate="many_to_one").copy()
        detail["exit_bid_missing"] = detail.exit_bid.isna()
        detail["exit_bid"] = detail.exit_bid.fillna(0).clip(lower=0)
        detail["entry_date"] = detail.day0_date
        detail["entry_oi"] = detail.open_interest
        run_daily_portfolio(detail, calendar, optionmetrics_dir, data_dir, output_dir,
                            sizing_multipliers=sizing_multipliers)
    if daily_grid:
        from pead.research.options_daily_portfolio import run_daily_grid_portfolios
        detail = candidates.copy()
        detail["entry_date"] = detail.day0_date
        detail["entry_oi"] = detail.open_interest
        run_daily_grid_portfolios(detail, calendar, optionmetrics_dir, data_dir, output_dir)
    print(summary.sort_values("roi_on_premium", ascending=False).head(15).to_string(index=False))
    return summary, yearly


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--years", type=int, nargs="+", default=[y for y in range(1998, 2014) if y != 2011])
    p.add_argument("--data-dir", type=Path, default=DATA_DIR)
    p.add_argument("--optionmetrics-dir", type=Path, default=OPTIONMETRICS_DIR)
    p.add_argument("--output-dir", type=Path, default=REPO_ROOT / "results" / "options_expiry_grid")
    p.add_argument("--dte-targets", type=int, nargs="+", default=DTE_TARGETS)
    p.add_argument("--delta-targets", type=float, nargs="+", default=DELTA_TARGETS)
    p.add_argument("--holds", type=int, nargs="+", default=HOLDS)
    p.add_argument("--direction", choices=["C", "P"])
    p.add_argument("--portfolio", action="store_true")
    p.add_argument("--daily-portfolio", action="store_true",
                   help="scan selected daily bids in memory; save only aggregate portfolio NAV")
    p.add_argument("--sizing-multipliers", type=float, nargs="+",
                   help="rerun a single daily portfolio with scaled premium budgets (max 5x, no borrowing)")
    p.add_argument("--daily-grid", action="store_true",
                   help="run all requested cells under common daily funded portfolio accounting")
    a = p.parse_args()
    run(a.years, a.data_dir, a.optionmetrics_dir, a.output_dir,
        a.dte_targets, a.delta_targets, a.holds, a.direction, a.portfolio,
        a.daily_portfolio, a.daily_grid,
        tuple(a.sizing_multipliers) if a.sizing_multipliers else None)


if __name__ == "__main__":
    main()
