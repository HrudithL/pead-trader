"""Daily, funded bid-mark audits for PEAD option rules.

The OptionMetrics files are streamed in place. Contract-level quotes and selected
paths exist only in memory; the output contains daily *portfolio* totals and
aggregate diagnostics. This is a historical audit, not a live execution model.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from pead.common.paths import REPO_ROOT
from pead.research.french_factors import load_french_daily


def attach_prior_quarter_volatility(trades: pd.DataFrame, data_dir: Path) -> pd.DataFrame:
    """Attach event IV/RV and volatility buckets using prior-quarter cutoffs only."""
    path = data_dir / "event_options" / "event_vol_features.parquet"
    if not path.exists():
        return trades
    features = pd.read_parquet(path, columns=[
        "secid", "day0_date", "ann_quarter", "atm_iv", "tvol_60d", "rv_ann", "iv_rv"
    ]).copy()
    features["secid"] = features.secid.astype("int64")
    features["day0_date"] = pd.to_datetime(features.day0_date)
    features["quarter"] = pd.PeriodIndex(features.ann_quarter.astype(str), freq="Q")
    for col in ("iv_rv", "tvol_60d"):
        cuts = features.groupby("quarter", observed=True)[col].quantile([1 / 3, 2 / 3])
        cuts = cuts.unstack().rename(columns={1 / 3: "q33", 2 / 3: "q67"})
        for suffix in ("q33", "q67"):
            prior = cuts[suffix].shift(1)
            features[f"{col}_prior_{suffix}"] = features.quarter.map(prior)
        value = features[col]
        q33 = features[f"{col}_prior_q33"]
        q67 = features[f"{col}_prior_q67"]
        features[f"{col}_bucket"] = np.select(
            [value.notna() & q33.notna() & value.le(q33),
             value.notna() & q67.notna() & value.le(q67),
             value.notna() & q67.notna() & value.gt(q67)],
            ["low", "mid", "high"], default="missing")
        features[f"{col}_below_q67"] = value.notna() & q67.notna() & value.le(q67)
    keep = ["secid", "day0_date", "atm_iv", "tvol_60d", "rv_ann", "iv_rv",
            "iv_rv_bucket", "iv_rv_below_q67", "tvol_60d_bucket", "tvol_60d_below_q67"]
    t = trades.copy()
    t["secid"] = t.secid.astype("int64")
    t["day0_date"] = pd.to_datetime(t.day0_date)
    return t.merge(features[keep], on=["secid", "day0_date"], how="left",
                   validate="many_to_one")


def selected_daily_lookups(trades: pd.DataFrame, option_calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """Expand each eligible contract through its scheduled close, without crossing a gap."""
    cal = pd.DatetimeIndex(option_calendar).sort_values()
    rows = []
    for trade_id, t in trades.reset_index(drop=True).iterrows():
        a = cal.get_indexer([t.entry_date])[0]
        b = cal.get_indexer([t.exit_date])[0]
        if a < 0 or b <= a:
            raise ValueError(f"invalid option-calendar span for trade {trade_id}")
        rows.append(pd.DataFrame({"trade_id": trade_id, "secid": int(t.secid),
                                  "optionid": int(t.optionid), "date": cal[a:b + 1]}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["trade_id", "secid", "optionid", "date"])


def scan_selected_daily_quotes(lookup: pd.DataFrame, optionmetrics_dir: Path) -> pd.DataFrame:
    """Return in-memory daily bid/ask/OI for selected IDs; persist none of it."""
    found = []
    for year, group in lookup.groupby(lookup.date.dt.year):
        keys = group[["secid", "optionid", "date"]].drop_duplicates()
        selected_secids = keys.secid.unique()
        path = optionmetrics_dir / f"opprcd{int(year)}.parquet"
        matches = []
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(columns=["secid", "date", "optionid", "best_bid",
                                              "best_offer", "open_interest", "cfadj",
                                              "ss_flag"], batch_size=1_000_000):
            raw = batch.to_pandas(date_as_object=False)
            # Secid prefilter keeps the three-key join modest on the dense chain.
            raw = raw[raw.secid.isin(selected_secids)]
            if len(raw):
                hit = raw.merge(keys, on=["secid", "date", "optionid"], how="inner")
                if len(hit):
                    matches.append(hit)
        q = pd.concat(matches, ignore_index=True).drop_duplicates(
            ["secid", "date", "optionid"]) if matches else pd.DataFrame(
                columns=["secid", "date", "optionid", "best_bid", "best_offer",
                         "open_interest", "cfadj", "ss_flag"])
        print(f"daily quotes {year}: {len(keys):,} keys, {len(q):,} matched", flush=True)
        if len(q):
            found.append(group.merge(q, on=["secid", "date", "optionid"],
                                     how="inner", validate="many_to_one"))
    return pd.concat(found, ignore_index=True) if found else pd.DataFrame(
        columns=["trade_id", "date", "best_bid", "best_offer", "open_interest",
                 "cfadj", "ss_flag"])


def simulate_daily(trades: pd.DataFrame, quotes: pd.DataFrame,
                   calendar: pd.DatetimeIndex, *, initial_capital: float = 10_000_000.0,
                   premium_cap: float = 0.20, max_trade_fraction: float = 0.0025,
                   max_oi_fraction: float = 0.10, per_name_fraction: float = 1.0,
                   hold: int = 60, trailing_scaler: bool = True,
                   cost_fraction: float = 0.0, missing_policy: str = "zero",
                   cash_returns: pd.Series | None = None,
                   option_trading_dates: pd.DatetimeIndex | None = None
                   ) -> tuple[pd.DataFrame, dict]:
    """Buy a date's signals pro rata; mark open lots at that day's bid.

    Entries use prior-close NAV. Same-close exits cannot fund entries. On dates
    outside the option trading calendar, an open position carries its last bid;
    a missing quote on an option session follows missing_policy. A missing exit
    is always zero. Cost is an additional fraction of ask premium charged on
    entry, leaving the frozen ask/bid route otherwise unchanged.
    """
    if missing_policy not in {"zero", "carry"}:
        raise ValueError("missing_policy must be zero or carry")
    if cost_fraction < 0:
        raise ValueError("cost_fraction must be nonnegative")
    cal = pd.DatetimeIndex(calendar).sort_values().unique()
    t = trades.reset_index(drop=True).copy()
    t["entry_date"] = pd.to_datetime(t.entry_date)
    t["exit_date"] = pd.to_datetime(t.exit_date)
    option_days = None
    if option_trading_dates is not None:
        option_days = set(pd.DatetimeIndex(option_trading_dates).sort_values().unique())
        off_calendar = ~t.entry_date.isin(option_days) | ~t.exit_date.isin(option_days)
        if off_calendar.any():
            raise ValueError("trade entry and exit dates must be option trading sessions")
    if "entry_bid" not in t:
        raise ValueError("entry_bid is required for an immediate executable mark")
    entry_groups = t.groupby("entry_date", sort=False).indices
    q = quotes[["trade_id", "date", "best_bid"]].drop_duplicates(
        ["trade_id", "date"]).copy()
    q["date"] = pd.to_datetime(q.date)
    quote_by_day = {date: dict(zip(g.trade_id.astype(int), g.best_bid))
                    for date, g in q.groupby("date", sort=False)}
    open_pos: dict[int, dict] = {}
    last_bids: dict[int, float] = {}
    trailing: deque[pd.Timestamp] = deque()
    cash = float(initial_capital)
    prior_nav = float(initial_capital)
    premium_paid = exit_proceeds = added_cost = 0.0
    cash_interest = 0.0
    funded = missing_marks = zero_marks = missing_exits = zero_exits = 0
    capacity_binding_days = 0
    funded_records = []
    settled_records = []
    records = []
    non_option_session_position_days = 0
    cash_returns = pd.Series(dtype=float) if cash_returns is None else cash_returns
    for date in cal:
        interest = cash * float(cash_returns.get(date, 0.0))
        cash += interest
        cash_interest += interest
        while trailing and (date - trailing[0]).days > 365:
            trailing.popleft()
        entrants = list(entry_groups.get(date, []))
        if entrants:
            n_trailing = len(trailing) + len(entrants)
            frac = min(max_trade_fraction,
                       premium_cap * 252 / (max(1, n_trailing) * hold)) if trailing_scaler \
                else max_trade_fraction
            outstanding = sum(p["premium"] for p in open_pos.values())
            room = max(0.0, min(cash, premium_cap * prior_nav - outstanding))
            desired = []
            for i in entrants:
                row = t.iloc[i]
                ask = float(row.entry_ask)
                oi = float(row.entry_oi)
                if not (np.isfinite(ask) and ask > 0 and np.isfinite(oi) and oi > 0):
                    desired.append(0)
                    continue
                already_lots = sum(p["lots"] for p in open_pos.values()
                                   if p["optionid"] == int(row.optionid))
                option_limit = max(0, int(np.floor(max_oi_fraction * oi)) - already_lots)
                name_premium = sum(p["premium"] for p in open_pos.values()
                                   if p["secid"] == int(row.secid))
                name_limit = max(0.0, per_name_fraction * prior_nav - name_premium)
                budget = min(frac * prior_nav, name_limit)
                desired.append(max(0, min(option_limit, int(np.floor(budget / (100 * ask))))))
            desired_premium = sum(lots * 100 * float(t.iloc[i].entry_ask)
                                  for i, lots in zip(entrants, desired))
            scale = min(1.0, room / desired_premium) if desired_premium > 0 else 0.0
            if scale < 1 and desired_premium > 0:
                capacity_binding_days += 1
            # Floor after common scaling: the rule is invariant to secid order.
            allocated = [int(np.floor(lots * scale)) for lots in desired]
            for i, lots in zip(entrants, allocated):
                if lots == 0:
                    continue
                row = t.iloc[i]
                paid = lots * 100 * float(row.entry_ask)
                fee = paid * cost_fraction
                if cash + 1e-8 < paid + fee:
                    continue
                cash -= paid + fee
                premium_paid += paid
                added_cost += fee
                funded += 1
                funded_records.append((i, lots, paid, fee))
                open_pos[i] = {"lots": lots, "premium": paid,
                               "secid": int(row.secid), "optionid": int(row.optionid),
                               "exit_date": row.exit_date}
                last_bids[i] = max(0.0, float(row.entry_bid))
            trailing.extend([date] * len(entrants))
        day_bids = quote_by_day.get(date, {})
        is_option_session = option_days is None or date in option_days
        if not is_option_session:
            non_option_session_position_days += len(open_pos)
        market_value = 0.0
        for i, pos in list(open_pos.items()):
            if not is_option_session:
                if date == pos["exit_date"]:
                    raise ValueError("position cannot exit outside the option trading calendar")
                bid = last_bids[i]
            else:
                raw_bid = day_bids.get(i, np.nan)
                if date == t.at[i, "entry_date"]:
                    raw_bid = t.at[i, "entry_bid"]
                missing = not np.isfinite(raw_bid) or raw_bid < 0
                if missing:
                    if date == pos["exit_date"]:
                        missing_exits += 1
                    else:
                        missing_marks += 1
                    bid = last_bids[i] if missing_policy == "carry" and date != pos["exit_date"] else 0.0
                else:
                    bid = max(0.0, float(raw_bid))
                    last_bids[i] = bid
                    if bid == 0:
                        if date == pos["exit_date"]:
                            zero_exits += 1
                        else:
                            zero_marks += 1
            value = pos["lots"] * 100 * bid
            if date == pos["exit_date"]:
                cash += value
                exit_proceeds += value
                settled_records.append((i, value - pos["premium"]
                                        - pos["premium"] * cost_fraction))
                del open_pos[i]
                del last_bids[i]
            else:
                market_value += value
        nav = cash + market_value
        records.append((date, nav, cash, market_value, len(open_pos),
                        sum(p["premium"] for p in open_pos.values())))
        prior_nav = nav
    daily = pd.DataFrame(records, columns=["date", "nav", "cash", "option_bid_value",
                                           "open_positions", "outstanding_premium"])
    if daily.empty:
        raise ValueError("empty study calendar")
    ret = daily.nav.pct_change().fillna(daily.nav.iloc[0] / initial_capital - 1)
    daily["return"] = ret
    years = max((daily.date.iloc[-1] - daily.date.iloc[0]).days / 365.25, 1 / 365.25)
    wealth = daily.nav.iloc[-1] / initial_capital
    vol = float(ret.std(ddof=1) * np.sqrt(252)) if len(ret) > 1 else np.nan
    sharpe = float(ret.mean() * 252 / vol) if vol > 0 else np.nan
    summary = {
        "funded_trades": funded, "premium_paid": premium_paid,
        "exit_proceeds": exit_proceeds, "added_cost": added_cost,
        "cash_interest": cash_interest,
        "net_pnl": float(daily.nav.iloc[-1] - initial_capital),
        "cagr": float(wealth ** (1 / years) - 1) if wealth > 0 else -1.0,
        "annualized_volatility": vol, "daily_sharpe_zero_rf": sharpe,
        "max_drawdown": float((daily.nav / daily.nav.cummax() - 1).min()),
        "missing_open_marks": missing_marks, "zero_open_marks": zero_marks,
        "missing_exits": missing_exits, "zero_exits": zero_exits,
        "capacity_binding_days": capacity_binding_days,
        "study_start": str(daily.date.iloc[0].date()),
        "study_end": str(daily.date.iloc[-1].date()),
        "missing_policy": missing_policy, "cost_fraction": cost_fraction,
        "cash_model": "daily_rf" if len(cash_returns) else "zero_yield",
        "unclosed_positions": len(open_pos),
        "non_option_session_position_days": non_option_session_position_days,
    }
    if len(cash_returns):
        rf_aligned = cash_returns.reindex(daily.date).fillna(0.0).to_numpy(dtype=float)
        excess_ret = ret.to_numpy(dtype=float) - rf_aligned
        excess_vol = np.std(excess_ret, ddof=1) * np.sqrt(252)
        summary["daily_sharpe_excess_rf"] = float(
            np.mean(excess_ret) * 252 / excess_vol) if excess_vol > 0 else np.nan
    if funded_records:
        f = pd.DataFrame(funded_records, columns=["trade_id", "lots", "premium", "cost"])
        s = pd.DataFrame(settled_records, columns=["trade_id", "pnl"])
        f = f.merge(s, on="trade_id", how="left", validate="one_to_one")
        f["roi"] = f.pnl / f.premium
        f["exit_year"] = t.loc[f.trade_id, "exit_date"].dt.year.to_numpy()
        summary["profitable_trade_fraction"] = float((f.pnl > 0).mean())
        summary["median_trade_roi"] = float(f.roi.median())
        summary["top_10_trade_pnl"] = float(f.pnl.nlargest(10).sum())
        summary["worst_10_trade_pnl"] = float(f.pnl.nsmallest(10).sum())
        in_2013 = f[f.exit_year.eq(2013)]
        summary["trade_pnl_2013"] = float(in_2013.pnl.sum())
        summary["top_10_trade_pnl_2013"] = float(in_2013.pnl.nlargest(10).sum())
        summary["trade_pnl_2008_2012"] = float(
            f.loc[f.exit_year.isin([2008, 2009, 2010, 2012]), "pnl"].sum())
        for col in ("delta", "dte", "moneyness", "spread_fraction"):
            if col in t:
                values = t.loc[f.trade_id, col].to_numpy(dtype=float)
                summary[f"funded_{col}_median"] = float(np.nanmedian(values))
                summary[f"funded_{col}_p10"] = float(np.nanquantile(values, 0.1))
                summary[f"funded_{col}_p90"] = float(np.nanquantile(values, 0.9))
        summary["trade_pnl_reconciles"] = bool(
            np.isclose(f.pnl.sum() + cash_interest, summary["net_pnl"], atol=0.01))
        if "exit_ask" in t:
            meta = t.loc[f.trade_id, ["entry_ask", "entry_bid", "exit_bid", "exit_ask"]].reset_index(drop=True)
            valid = (meta.entry_bid.ge(0) & meta.exit_bid.ge(0)
                     & meta.exit_ask.ge(meta.exit_bid) & meta.exit_ask.notna())
            w = f.loc[valid.to_numpy(), "lots"].to_numpy(dtype=float) * 100
            m = meta.loc[valid].reset_index(drop=True)
            entry_mid = (m.entry_ask.to_numpy() + m.entry_bid.to_numpy()) / 2
            exit_mid = (m.exit_bid.to_numpy() + m.exit_ask.to_numpy()) / 2
            summary["attribution_covered_trades"] = int(valid.sum())
            summary["mid_to_mid_change_dollars"] = float(np.sum(w * (exit_mid - entry_mid)))
            summary["entry_half_spread_dollars"] = float(np.sum(w *
                (m.entry_ask.to_numpy() - entry_mid)))
            summary["exit_half_spread_dollars"] = float(np.sum(w *
                (exit_mid - m.exit_bid.to_numpy())))
    return daily, summary


def simulate_sizing_sensitivity(trades: pd.DataFrame, quotes: pd.DataFrame,
                                calendar: pd.DatetimeIndex, *,
                                multipliers: tuple[float, ...],
                                initial_capital: float = 10_000_000.0,
                                cash_returns: pd.Series | None = None,
                                option_trading_dates: pd.DatetimeIndex | None = None
                                ) -> list[tuple[float, pd.DataFrame, dict]]:
    """Rerun one fixed contract/event rule with larger, unborrowed premium budgets.

    Scale the baseline 20%-of-NAV outstanding-premium cap and 0.25%-of-NAV
    per-signal budget together. Keep the 10%-of-OI contract cap and 2%-of-NAV
    issuer cap fixed. A multiplier above five would permit premium risk above
    100% of NAV, which this cash-only research engine cannot support.
    """
    values = tuple(float(value) for value in multipliers)
    if not values or len(set(values)) != len(values):
        raise ValueError("sizing multipliers must be nonempty and unique")
    if any(not np.isfinite(value) or value <= 0 or value > 5 for value in values):
        raise ValueError("sizing multipliers must be positive and cannot exceed 5")
    t = trades.reset_index(drop=True).copy()
    if "hold" in t and len(t) and not t.hold.eq(t.hold.iloc[0]).all():
        raise ValueError("sizing sensitivity requires one scheduled hold per run")
    hold = int(t.hold.iloc[0]) if "hold" in t and len(t) else 60

    out = []
    for multiplier in values:
        daily, summary = simulate_daily(
            t, quotes, calendar, initial_capital=initial_capital,
            premium_cap=0.20 * multiplier,
            max_trade_fraction=0.0025 * multiplier,
            max_oi_fraction=0.10, per_name_fraction=0.02,
            hold=hold, trailing_scaler=True, cash_returns=cash_returns,
            option_trading_dates=option_trading_dates,
        )
        summary["sizing_multiplier"] = multiplier
        summary["premium_cap_fraction"] = 0.20 * multiplier
        summary["max_trade_fraction"] = 0.0025 * multiplier
        out.append((multiplier, daily, summary))
    return out


def build_grid_daily_path_lookup(trades: pd.DataFrame,
                                 option_calendar: pd.DatetimeIndex
                                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build deduplicated contract paths shared by all grid cells and holds.

    A contract selected for several hold horizons only needs to be scanned once,
    through its longest scheduled exit. The returned lookup contains no quote data.
    """
    t = trades.copy().reset_index(drop=True)
    t["entry_date"] = pd.to_datetime(t.entry_date)
    t["exit_date"] = pd.to_datetime(t.exit_date)
    cal = pd.DatetimeIndex(option_calendar).sort_values().unique()
    paths = t[["secid", "optionid", "entry_date"]].drop_duplicates().reset_index(drop=True)
    paths["path_id"] = np.arange(len(paths), dtype=np.int32)
    t = t.merge(paths, on=["secid", "optionid", "entry_date"], how="left",
                validate="many_to_one")
    max_exit = t.groupby("path_id", sort=False).exit_date.max()
    paths["max_exit_date"] = paths.path_id.map(max_exit)
    starts = cal.get_indexer(paths.entry_date)
    ends = cal.get_indexer(paths.max_exit_date)
    if np.any(starts < 0) or np.any(ends < starts):
        raise ValueError("daily grid path contains an invalid option-calendar span")
    lengths = ends - starts + 1
    offsets = np.arange(int(lengths.sum()), dtype=np.int64)
    group_starts = np.repeat(np.cumsum(lengths) - lengths, lengths)
    date_positions = np.repeat(starts, lengths) + offsets - group_starts
    lookup = pd.DataFrame({
        "path_id": np.repeat(paths.path_id.to_numpy(dtype=np.int32), lengths),
        "date": cal.to_numpy()[date_positions],
    }).merge(paths[["path_id", "secid", "optionid"]], on="path_id", how="left",
             validate="many_to_one")
    return t, lookup[["path_id", "secid", "optionid", "date"]]


def scan_unique_daily_bid_quotes(lookup: pd.DataFrame,
                                 optionmetrics_dir: Path) -> pd.DataFrame:
    """Stream unique selected contract-days from OptionMetrics; retain bids in RAM only."""
    keys = lookup[["secid", "optionid", "date"]].drop_duplicates().copy()
    keys["date"] = pd.to_datetime(keys.date)
    found = []
    for year, group in keys.groupby(keys.date.dt.year):
        path = optionmetrics_dir / f"opprcd{int(year)}.parquet"
        selected_secids = group.secid.unique()
        matches = []
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(columns=["secid", "date", "optionid", "best_bid"],
                                     batch_size=1_000_000):
            raw = batch.to_pandas(date_as_object=False)
            raw = raw[raw.secid.isin(selected_secids)]
            if len(raw):
                hit = raw.merge(group, on=["secid", "date", "optionid"], how="inner")
                if len(hit):
                    matches.append(hit)
        quotes = pd.concat(matches, ignore_index=True).drop_duplicates(
            ["secid", "optionid", "date"]) if matches else pd.DataFrame(
                columns=["secid", "optionid", "date", "best_bid"])
        print(f"daily grid quotes {year}: {len(group):,} unique keys, "
              f"{len(quotes):,} matched", flush=True)
        if len(quotes):
            found.append(quotes)
    if not found:
        return pd.DataFrame(columns=["secid", "optionid", "date", "best_bid"])
    return pd.concat(found, ignore_index=True).drop_duplicates(
        ["secid", "optionid", "date"])


def year_block_sharpe_diagnostics(excess_returns: np.ndarray,
                                  dates: pd.DatetimeIndex,
                                  cell_ids: list[str], *, n_boot: int = 5000,
                                  seed: int = 20261022) -> tuple[pd.DataFrame, dict]:
    """Estimate year-block Sharpe intervals and a grid-wide centered max test."""
    values = np.asarray(excess_returns, dtype=float)
    if values.ndim != 2 or values.shape[0] != len(dates):
        raise ValueError("excess return panel must be daily dates by cells")
    years = np.asarray(pd.DatetimeIndex(dates).year)
    unique_years = np.unique(years)
    blocks = [values[years == year] for year in unique_years]
    counts = np.asarray([len(b) for b in blocks], dtype=float)
    sums = np.vstack([b.sum(axis=0) for b in blocks])
    sums_sq = np.vstack([(b * b).sum(axis=0) for b in blocks])
    n = counts.sum()
    observed_mean = values.mean(axis=0)
    observed_sd = values.std(axis=0, ddof=1)
    observed = observed_mean / observed_sd * np.sqrt(252)
    rng = np.random.default_rng(seed)
    draw_idx = rng.integers(0, len(unique_years), size=(n_boot, len(unique_years)))
    draw_n = counts[draw_idx].sum(axis=1)[:, None]
    draw_sum = sums[draw_idx].sum(axis=1)
    draw_sq = sums_sq[draw_idx].sum(axis=1)
    draw_mean = draw_sum / draw_n
    draw_var = np.maximum(0.0, (draw_sq - draw_sum * draw_sum / draw_n) /
                          np.maximum(1.0, draw_n - 1.0))
    draw_sharpe = draw_mean / np.sqrt(draw_var) * np.sqrt(252)
    lower = np.nanquantile(draw_sharpe, 0.025, axis=0)
    upper = np.nanquantile(draw_sharpe, 0.975, axis=0)

    # Center each cell's daily excess return at zero under the joint null, then
    # resample whole years. This asks how often noise would produce the observed
    # best Sharpe after searching all cells.
    null_sums = sums - counts[:, None] * observed_mean[None, :]
    null_sq = (sums_sq - 2 * observed_mean[None, :] * sums
               + counts[:, None] * observed_mean[None, :] ** 2)
    null_sum = null_sums[draw_idx].sum(axis=1)
    null_total_sq = null_sq[draw_idx].sum(axis=1)
    null_mean = null_sum / draw_n
    null_var = np.maximum(0.0, (null_total_sq - null_sum * null_sum / draw_n) /
                          np.maximum(1.0, draw_n - 1.0))
    null_sharpe = null_mean / np.sqrt(null_var) * np.sqrt(252)
    maxima = np.nanmax(null_sharpe, axis=1)
    max_observed = float(np.nanmax(observed))
    result = pd.DataFrame({"cell_id": cell_ids,
                           "excess_rf_sharpe": observed,
                           "year_block_ci_low": lower,
                           "year_block_ci_high": upper})
    evidence = {
        "years": [int(y) for y in unique_years],
        "cells": len(cell_ids),
        "observed_max_excess_rf_sharpe": max_observed,
        "max_sharpe_familywise_p": float((1 + np.sum(maxima >= max_observed)) /
                                          (n_boot + 1)),
        "centered_null_max_sharpe_95pct": float(np.quantile(maxima, 0.95)),
        "resamples": int(n_boot), "seed": int(seed),
        "method": "centered whole-calendar-year block bootstrap across all cells",
    }
    return result, evidence


def run_daily_grid_portfolios(trades: pd.DataFrame, option_calendar: pd.DatetimeIndex,
                              optionmetrics_dir: Path, data_dir: Path, output_dir: Path,
                              factor_dir: Path | None = None,
                              initial_capital: float = 10_000_000.0) -> dict:
    """Run all requested cells with shared quote scans and identical daily funding."""
    from pead.research.options_deep_diagnostics import hac_regression

    cell_keys = ["direction", "dte_target", "delta_target", "hold"]
    t, path_lookup = build_grid_daily_path_lookup(trades, option_calendar)
    print(f"daily grid: {t[cell_keys].drop_duplicates().shape[0]} cells, "
          f"{len(t):,} candidate trades, {path_lookup.path_id.nunique():,} unique paths, "
          f"{len(path_lookup):,} path-days", flush=True)
    daily_quotes = scan_unique_daily_bid_quotes(path_lookup, optionmetrics_dir)
    benchmark = pd.read_parquet(data_dir / "normalized_equity" / "market_benchmark.parquet",
                                columns=["date", "vwretd"])
    benchmark["date"] = pd.to_datetime(benchmark.date)
    start = pd.Timestamp(year=int(t.entry_date.min().year), month=1, day=1)
    end = pd.Timestamp(year=int(t.entry_date.max().year), month=12, day=31)
    market_calendar = pd.DatetimeIndex(benchmark.loc[
        benchmark.date.between(start, end), "date"].drop_duplicates().sort_values())
    factor_dir = factor_dir or REPO_ROOT / "data" / "factor"
    factors, factor_provenance = load_french_daily(factor_dir)
    factor_panel = factors.set_index("date").reindex(market_calendar)
    if factor_panel.RF.isna().any():
        raise ValueError(f"official daily RF missing on {int(factor_panel.RF.isna().sum())} study dates")
    rf = factor_panel.RF
    all_cash_final = float(initial_capital * np.prod(1.0 + rf.to_numpy(dtype=float)))
    path_id_array = path_lookup.path_id.to_numpy(dtype=np.int32)
    path_info = path_lookup[["path_id", "secid", "optionid", "date"]]
    summary_rows, yearly_rows, daily_rows = [], [], []
    excess_columns, cell_ids = [], []
    groups = list(t.groupby(cell_keys, sort=True, observed=True))
    for cell_number, (cell, group) in enumerate(groups, start=1):
        cell_id = f"{cell[0]}_{int(cell[1])}_D{float(cell[2]):.2f}_H{int(cell[3])}"
        c = group.reset_index(drop=True).copy()
        c["trade_id"] = np.arange(len(c), dtype=np.int32)
        if c.path_id.duplicated().any():
            raise ValueError(f"duplicate contract path within daily grid cell {cell_id}")
        path_map = c.set_index("path_id")["trade_id"]
        exit_map = c.set_index("path_id").exit_date
        selected = np.isin(path_id_array, c.path_id.to_numpy(dtype=np.int32))
        cell_lookup = path_info.loc[selected].copy()
        cell_lookup["trade_id"] = cell_lookup.path_id.map(path_map).astype(np.int32)
        cell_lookup["exit_date"] = cell_lookup.path_id.map(exit_map)
        cell_lookup = cell_lookup[cell_lookup.date.le(cell_lookup.exit_date)]
        cell_quotes = cell_lookup.merge(daily_quotes,
                                       on=["secid", "optionid", "date"],
                                       how="left", validate="many_to_one")
        required_keys = len(cell_quotes)
        matched_keys = int(cell_quotes.best_bid.notna().sum())
        daily, summary = simulate_daily(
            c, cell_quotes[["trade_id", "date", "best_bid"]], market_calendar,
            initial_capital=initial_capital, hold=int(cell[3]), max_oi_fraction=0.10,
            per_name_fraction=0.02, cash_returns=rf, option_trading_dates=option_calendar)
        summary.update(dict(zip(cell_keys, cell)))
        summary["cell_id"] = cell_id
        summary["incremental_pnl_vs_all_cash"] = float(
            initial_capital + summary["net_pnl"] - all_cash_final)
        summary["required_daily_quote_keys"] = required_keys
        summary["matched_daily_quote_keys"] = matched_keys
        summary["daily_quote_coverage"] = matched_keys / required_keys if required_keys else None
        factor = daily[["date", "return"]].merge(factor_panel.reset_index(), on="date",
                                                  how="left", validate="one_to_one")
        factor_clean = factor.dropna(subset=["return", "RF", "Mkt-RF", "SMB", "HML", "Mom"])
        factor_y = (factor_clean["return"] - factor_clean.RF).to_numpy(dtype=float)
        factor_x = factor_clean[["Mkt-RF", "SMB", "HML", "Mom"]].to_numpy(dtype=float)
        alpha = hac_regression(factor_y, factor_x, lags=60)
        summary["four_factor_alpha_annualized"] = alpha["annualized_intercept"]
        summary["four_factor_alpha_hac_t"] = alpha["intercept_hac_t"]
        summary["four_factor_alpha_ci_low"] = alpha["annualized_intercept_95ci"][0]
        summary["four_factor_alpha_ci_high"] = alpha["annualized_intercept_95ci"][1]
        for name, beta in zip(("Mkt-RF", "SMB", "HML", "Mom"), alpha["slopes"]):
            summary[f"four_factor_beta_{name}"] = beta
        summary["factor_regression_n"] = alpha["n"]
        summary_rows.append(summary)
        daily_rows.append(daily.assign(cell_id=cell_id)[
            ["date", "cell_id", "nav", "return", "cash", "option_bid_value",
             "open_positions", "outstanding_premium"]])
        excess_columns.append(daily["return"].to_numpy(dtype=float)
                              - rf.reindex(daily.date).to_numpy(dtype=float))
        cell_ids.append(cell_id)
        prior = initial_capital
        for year, year_frame in daily.groupby(daily.date.dt.year, sort=True):
            ending = float(year_frame.nav.iloc[-1])
            yearly_rows.append({"cell_id": cell_id, **dict(zip(cell_keys, cell)),
                                "year": int(year), "start_nav": prior,
                                "end_nav": ending, "year_pnl": ending - prior,
                                "year_return": ending / prior - 1.0,
                                "trading_days": len(year_frame)})
            prior = ending
        print(f"daily grid cell {cell_number}/{len(groups)} {cell_id}: "
              f"CAGR {summary['cagr']:.3%}, excess Sharpe "
              f"{summary.get('daily_sharpe_excess_rf', float('nan')):.3f}, "
              f"alpha {alpha['annualized_intercept']:.3%}", flush=True)

    excess_matrix = np.column_stack(excess_columns)
    bootstrap, bootstrap_evidence = year_block_sharpe_diagnostics(
        excess_matrix, market_calendar, cell_ids)
    summaries = pd.DataFrame(summary_rows).merge(bootstrap, on="cell_id", validate="one_to_one")
    summaries = summaries.sort_values("incremental_pnl_vs_all_cash", ascending=False)
    yearly = pd.DataFrame(yearly_rows)
    daily_panel = pd.concat(daily_rows, ignore_index=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries.to_csv(output_dir / "daily_grid_summary.csv", index=False)
    yearly.to_csv(output_dir / "daily_grid_yearly.csv", index=False)
    daily_panel.to_csv(output_dir / "daily_grid_paths.csv", index=False)
    audit = {
        "cells": len(groups), "candidate_trades": len(t),
        "unique_contract_paths": int(path_lookup.path_id.nunique()),
        "path_days": len(path_lookup),
        "unique_daily_quote_keys": int(len(path_lookup[["secid", "optionid", "date"]].drop_duplicates())),
        "matched_unique_daily_quote_keys": int(len(daily_quotes)),
        "contract_level_quotes_persisted": False,
        "source_optionmetrics_dir": str(optionmetrics_dir),
        "study_start": str(market_calendar.min().date()),
        "study_end": str(market_calendar.max().date()),
        "all_cash_final_nav": all_cash_final,
        "factor_provenance": factor_provenance,
        "familywise_sharpe_bootstrap": bootstrap_evidence,
        "portfolio_rule": {
            "initial_capital": initial_capital, "premium_cap": 0.20,
            "max_trade_fraction": 0.0025, "max_open_option_oi_fraction": 0.10,
            "per_issuer_premium_fraction": 0.02, "cash_model": "daily French RF",
            "entry": "same-session selected ask", "mark_and_exit": "daily bid",
            "missing_intermediate_marks": "zero", "missing_exit_bid": "zero proceeds",
        },
    }
    (output_dir / "daily_grid_audit.json").write_text(json.dumps(audit, indent=2),
                                                        encoding="utf-8")
    print("daily grid ranking by incremental ending wealth:")
    print(summaries[["cell_id", "funded_trades", "cagr", "daily_sharpe_excess_rf",
                     "four_factor_alpha_annualized", "four_factor_alpha_hac_t",
                     "max_drawdown", "incremental_pnl_vs_all_cash"]]
          .head(15).to_string(index=False), flush=True)
    return audit


def run_daily_portfolio(trades: pd.DataFrame, option_calendar: pd.DatetimeIndex,
                        optionmetrics_dir: Path, data_dir: Path, output_dir: Path,
                        factor_dir: Path | None = None,
                        sizing_multipliers: tuple[float, ...] | None = None) -> dict:
    """Scan in place, run cost/mark sensitivities, and save aggregates only."""
    t = attach_prior_quarter_volatility(trades, data_dir).reset_index(drop=True)
    base_hold = int(t["hold"].iloc[0]) if "hold" in t and len(t) else 60
    if "hold" in t and not t["hold"].eq(base_hold).all():
        raise ValueError("daily portfolio sensitivity requires one scheduled hold per run")
    lookup = selected_daily_lookups(t, option_calendar)
    quotes = scan_selected_daily_quotes(lookup, optionmetrics_dir)
    exit_meta = quotes.merge(pd.DataFrame({"trade_id": np.arange(len(t)),
                                            "exit_date": t.exit_date}), on="trade_id")
    exit_meta = exit_meta[exit_meta.date.eq(exit_meta.exit_date)].set_index("trade_id")
    t["exit_ask"] = t.index.map(exit_meta.best_offer)
    quote_exit_bid = t.index.map(exit_meta.best_bid)
    both_quoted = pd.notna(quote_exit_bid) & ~t.exit_bid_missing
    if not np.allclose(t.loc[both_quoted, "exit_bid"], quote_exit_bid[both_quoted],
                       atol=1e-8):
        raise ValueError("daily scan exit bids disagree with fixed-grid exit bids")
    benchmark = pd.read_parquet(data_dir / "normalized_equity" / "market_benchmark.parquet",
                                columns=["date", "vwretd"])
    benchmark["date"] = pd.to_datetime(benchmark.date)
    start = pd.Timestamp(year=int(t.entry_date.min().year), month=1, day=1)
    end = pd.Timestamp(year=int(t.entry_date.max().year), month=12, day=31)
    market_calendar = pd.DatetimeIndex(benchmark.loc[
        benchmark.date.between(start, end), "date"].drop_duplicates().sort_values())
    if not set(pd.to_datetime(t.exit_date)).issubset(set(market_calendar)):
        raise ValueError("market calendar misses selected option exit dates")
    factor_dir = factor_dir or REPO_ROOT / "data" / "factor"
    factors, factor_provenance = load_french_daily(factor_dir)
    factor_panel = factors.set_index("date").reindex(market_calendar)
    rf = factor_panel["RF"]
    if rf.isna().any():
        raise ValueError(f"official daily RF missing on {int(rf.isna().sum())} study dates")
    all_cash_final = float(10_000_000 * np.prod(1.0 + rf.to_numpy(dtype=float)))
    summaries = []
    base_daily = None
    rf_daily = None
    for cost in (0.0, 0.01, 0.03, 0.05, 0.07):
        for policy in (("zero", "carry") if cost == 0 else ("zero",)):
            for use_rf in (False, True):
                daily, summary = simulate_daily(t, quotes, market_calendar,
                                                hold=base_hold,
                                                cost_fraction=cost, missing_policy=policy,
                                                per_name_fraction=0.02,
                                                cash_returns=rf if use_rf else None,
                                                option_trading_dates=option_calendar)
                summary["scenario"] = "frozen_day0"
                if use_rf:
                    summary["incremental_pnl_vs_all_cash"] = float(
                        10_000_000 + summary["net_pnl"] - all_cash_final)
                summaries.append(summary)
                if cost == 0 and policy == "zero":
                    if use_rf:
                        rf_daily = daily
                    else:
                        base_daily = daily
    for oi_fraction, name_fraction in ((0.01, 0.02), (0.05, 0.02), (0.10, 1.0)):
        _, capacity_summary = simulate_daily(
            t, quotes, market_calendar, hold=base_hold,
            max_oi_fraction=oi_fraction,
            per_name_fraction=name_fraction, cash_returns=rf, option_trading_dates=option_calendar)
        capacity_summary["scenario"] = f"capacity_oi{oi_fraction:g}_name{name_fraction:g}"
        capacity_summary["incremental_pnl_vs_all_cash"] = float(
            10_000_000 + capacity_summary["net_pnl"] - all_cash_final)
        summaries.append(capacity_summary)
    nonstandard = quotes.loc[(~np.isclose(quotes.cfadj.fillna(-1).to_numpy(dtype=float), 1.0))
                             | quotes.ss_flag.fillna("?").astype(str).ne("0"),
                             "trade_id"].unique()
    standard = t.drop(index=nonstandard).copy()
    standard_quotes = quotes[~quotes.trade_id.isin(nonstandard)].copy()
    id_map = dict(zip(standard.index, range(len(standard))))
    standard_quotes["trade_id"] = standard_quotes.trade_id.map(id_map)
    standard = standard.reset_index(drop=True)
    _, standard_summary = simulate_daily(standard, standard_quotes, market_calendar,
                                          hold=base_hold,
                                          per_name_fraction=0.02, cash_returns=rf, option_trading_dates=option_calendar)
    standard_summary["scenario"] = "exclude_any_cfadj_or_special_settlement"
    standard_summary["excluded_eligible_contracts"] = len(nonstandard)
    standard_summary["incremental_pnl_vs_all_cash"] = float(
        10_000_000 + standard_summary["net_pnl"] - all_cash_final)
    summaries.append(standard_summary)
    # Hold profile on the exact same scheduled-hold-eligible entry contracts. This
    # isolates timing from changing contract/event availability; it is a
    # post-hoc diagnostic and never selects a rule for this test archive.
    cal = pd.DatetimeIndex(option_calendar).sort_values()
    base_idx = cal.get_indexer(t.entry_date)
    quote_index = quotes.set_index(["trade_id", "date"])
    hold_profile = []
    profile_limit = int(t["hold"].min()) if "hold" in t and len(t) else 60
    for horizon in range(1, profile_limit + 1):
        dates_h = pd.to_datetime(cal.to_numpy()[base_idx + horizon])
        keys_h = pd.MultiIndex.from_arrays([np.arange(len(t)), dates_h],
                                            names=["trade_id", "date"])
        bids_h = quote_index.best_bid.reindex(keys_h).to_numpy(dtype=float)
        bids_h = np.nan_to_num(bids_h, nan=0.0)
        roi = bids_h / t.entry_ask.to_numpy(dtype=float) - 1.0
        hold_profile.append({"hold": horizon, "paired_eligible_contracts": len(t),
                             "equal_premium_mean_roi": float(np.mean(roi)),
                             "median_roi": float(np.median(roi)),
                             "profitable_fraction": float(np.mean(roi > 0)),
                             "missing_exit_quotes": int(np.isnan(
                                 quote_index.best_bid.reindex(keys_h).to_numpy(dtype=float)).sum())})
        if horizon in (10, 20, 30, 40, 50) and horizon <= profile_limit:
            t_h = t.copy()
            t_h["exit_date"] = dates_h
            t_h["exit_bid"] = bids_h
            t_h["exit_ask"] = quote_index.best_offer.reindex(keys_h).to_numpy(dtype=float)
            _, hold_summary = simulate_daily(t_h, quotes, market_calendar,
                                              hold=horizon, per_name_fraction=0.02,
                                              cash_returns=rf, option_trading_dates=option_calendar)
            hold_summary["scenario"] = f"paired_hold_{horizon}"
            hold_summary["incremental_pnl_vs_all_cash"] = float(
                10_000_000 + hold_summary["net_pnl"] - all_cash_final)
            summaries.append(hold_summary)
    for decile in (9, 10):
        subset = t[t.decile.eq(decile)].copy()
        mapping = dict(zip(subset.index, range(len(subset))))
        subset_quotes = quotes[quotes.trade_id.isin(mapping)].copy()
        subset_quotes["trade_id"] = subset_quotes.trade_id.map(mapping)
        subset = subset.reset_index(drop=True)
        _, decile_summary = simulate_daily(subset, subset_quotes, market_calendar,
                                            hold=base_hold,
                                            per_name_fraction=0.02, cash_returns=rf, option_trading_dates=option_calendar)
        decile_summary["scenario"] = f"decile_{decile}_only"
        decile_summary["incremental_pnl_vs_all_cash"] = float(
            10_000_000 + decile_summary["net_pnl"] - all_cash_final)
        summaries.append(decile_summary)
    # Feasibility falsification: the signal is certainly public by the next
    # session. Buy at that session's ask, using its bid/OI, then retain the
    # original scheduled exit (59 remaining sessions). Do not choose entries
    # using their eventual exit quote.
    next_map = pd.Series(pd.DatetimeIndex(option_calendar).sort_values()[1:].to_numpy(),
                         index=pd.DatetimeIndex(option_calendar).sort_values()[:-1])
    next_dates = t.entry_date.map(next_map)
    next_quotes = quotes.merge(pd.DataFrame({"trade_id": np.arange(len(t)),
                                             "next_date": next_dates}), on="trade_id")
    next_quotes = next_quotes[next_quotes.date.eq(next_quotes.next_date)].copy()
    next_quotes = next_quotes.set_index("trade_id")
    good = (next_quotes.best_bid.gt(0) & next_quotes.best_offer.ge(0.10)
            & next_quotes.best_offer.ge(next_quotes.best_bid)
            & ((next_quotes.best_offer - next_quotes.best_bid) / next_quotes.best_offer).le(0.30)
            & next_quotes.open_interest.ge(100))
    selected = next_quotes.loc[good].copy()
    delayed = t.loc[selected.index].copy()
    delayed["entry_date"] = selected.date.to_numpy()
    delayed["entry_ask"] = selected.best_offer.to_numpy()
    delayed["entry_bid"] = selected.best_bid.to_numpy()
    delayed["entry_oi"] = selected.open_interest.to_numpy()
    old_to_new = dict(zip(delayed.index, range(len(delayed))))
    delayed_quotes = quotes[quotes.trade_id.isin(old_to_new)].copy()
    delayed_quotes = delayed_quotes[delayed_quotes.date.ge(
        delayed_quotes.trade_id.map(delayed.entry_date))]
    delayed_quotes["trade_id"] = delayed_quotes.trade_id.map(old_to_new)
    delayed = delayed.reset_index(drop=True)
    scheduled_holds = (pd.DatetimeIndex(option_calendar).get_indexer(t.exit_date)
                       - pd.DatetimeIndex(option_calendar).get_indexer(t.entry_date))
    delayed_hold = max(1, int(np.median(scheduled_holds)) - 1)
    delayed_daily, delayed_summary = simulate_daily(delayed, delayed_quotes, market_calendar,
                                                     hold=delayed_hold, per_name_fraction=0.02,
                                                     cash_returns=rf, option_trading_dates=option_calendar)
    delayed_summary["rule"] = "next_session_entry_same_exit"
    delayed_summary["scenario"] = "next_session_entry_same_exit"
    delayed_summary["incremental_pnl_vs_all_cash"] = float(
        10_000_000 + delayed_summary["net_pnl"] - all_cash_final)
    summaries.append(delayed_summary)

    sizing_summaries = []
    if sizing_multipliers:
        from pead.research.options_deep_diagnostics import hac_regression

        for multiplier, sizing_daily, sizing_summary in simulate_sizing_sensitivity(
                t, quotes, market_calendar, multipliers=sizing_multipliers,
                initial_capital=10_000_000, cash_returns=rf,
                option_trading_dates=option_calendar):
            sizing_summary["scenario"] = f"sizing_x{multiplier:g}"
            sizing_summary["incremental_pnl_vs_all_cash"] = float(
                10_000_000 + sizing_summary["net_pnl"] - all_cash_final)
            factor = sizing_daily[["date", "return"]].merge(
                factor_panel.reset_index(), on="date", how="left", validate="one_to_one")
            factor_clean = factor.dropna(subset=["return", "RF", "Mkt-RF", "SMB", "HML", "Mom"])
            factor_y = (factor_clean["return"] - factor_clean.RF).to_numpy(dtype=float)
            factor_x = factor_clean[["Mkt-RF", "SMB", "HML", "Mom"]].to_numpy(dtype=float)
            alpha = hac_regression(factor_y, factor_x, lags=60)
            sizing_summary["four_factor_alpha_annualized"] = alpha["annualized_intercept"]
            sizing_summary["four_factor_alpha_hac_t"] = alpha["intercept_hac_t"]
            sizing_summary["four_factor_alpha_ci_low"] = alpha["annualized_intercept_95ci"][0]
            sizing_summary["four_factor_alpha_ci_high"] = alpha["annualized_intercept_95ci"][1]
            sizing_summary["factor_regression_n"] = alpha["n"]
            sizing_summaries.append(sizing_summary)

    volatility_summaries = []
    volatility_yearly = []
    if "iv_rv_bucket" in t:
        from pead.research.options_deep_diagnostics import hac_regression

        def run_volatility_groups(sample_trades, sample_quotes, prefix, sample_hold):
            vol_masks = {
                "iv_rv_low": sample_trades.iv_rv_bucket.eq("low"),
                "iv_rv_mid": sample_trades.iv_rv_bucket.eq("mid"),
                "iv_rv_high": sample_trades.iv_rv_bucket.eq("high"),
                "iv_rv_below_prior_q67": sample_trades.iv_rv_below_q67.fillna(False).astype(bool),
                "tvol_low": sample_trades.tvol_60d_bucket.eq("low"),
                "tvol_mid": sample_trades.tvol_60d_bucket.eq("mid"),
                "tvol_high": sample_trades.tvol_60d_bucket.eq("high"),
                "tvol_below_prior_q67": sample_trades.tvol_60d_below_q67.fillna(False).astype(bool),
            }
            for name, mask in vol_masks.items():
                old_ids = np.flatnonzero(mask.to_numpy(dtype=bool))
                if not len(old_ids):
                    continue
                old_to_new = dict(zip(old_ids.tolist(), range(len(old_ids))))
                subtrades = sample_trades.iloc[old_ids].copy().reset_index(drop=True)
                subquotes = sample_quotes[sample_quotes.trade_id.isin(old_ids)].copy()
                subquotes["trade_id"] = subquotes.trade_id.map(old_to_new).astype("int64")
                daily, summary = simulate_daily(
                    subtrades, subquotes, market_calendar, hold=sample_hold,
                    per_name_fraction=0.02, cash_returns=rf,
                    option_trading_dates=option_calendar)
                summary.update({
                    "scenario": f"{prefix}_{name}",
                    "eligible_contracts": int(len(subtrades)),
                    "eligible_fraction": float(mask.mean()),
                    "events_with_iv_rv": int(sample_trades.iv_rv.notna().sum()),
                    "events_with_tvol": int(sample_trades.tvol_60d.notna().sum()),
                    "incremental_pnl_vs_all_cash": float(
                        10_000_000 + summary["net_pnl"] - all_cash_final),
                })
                factor = daily[["date", "return"]].merge(
                    factor_panel.reset_index(), on="date", how="left", validate="one_to_one")
                factor_clean = factor.dropna(
                    subset=["return", "RF", "Mkt-RF", "SMB", "HML", "Mom"])
                alpha = hac_regression(
                    (factor_clean["return"] - factor_clean.RF).to_numpy(dtype=float),
                    factor_clean[["Mkt-RF", "SMB", "HML", "Mom"]].to_numpy(dtype=float),
                    lags=60)
                summary["four_factor_alpha_annualized"] = alpha["annualized_intercept"]
                summary["four_factor_alpha_hac_t"] = alpha["intercept_hac_t"]
                summary["four_factor_alpha_ci_low"] = alpha["annualized_intercept_95ci"][0]
                summary["four_factor_alpha_ci_high"] = alpha["annualized_intercept_95ci"][1]
                summary["factor_regression_n"] = alpha["n"]
                volatility_summaries.append(summary)
                for year, group in daily.groupby(daily.date.dt.year):
                    previous = daily.loc[daily.date.lt(group.date.iloc[0]), "nav"]
                    start_nav = float(previous.iloc[-1]) if len(previous) else 10_000_000.0
                    volatility_yearly.append({
                        "scenario": f"{prefix}_{name}", "year": int(year),
                        "start_nav": start_nav, "end_nav": float(group.nav.iloc[-1]),
                        "year_return": float(group.nav.iloc[-1] / start_nav - 1),
                        "trading_days": int(len(group)),
                    })

        run_volatility_groups(t, quotes, "same_session", base_hold)
        run_volatility_groups(delayed, delayed_quotes, "next_session", delayed_hold)

    output_dir.mkdir(parents=True, exist_ok=True)
    base_daily.to_csv(output_dir / "daily_portfolio.csv", index=False)
    rf_daily.to_csv(output_dir / "riskfree_daily_portfolio.csv", index=False)
    delayed_daily.to_csv(output_dir / "next_session_daily_portfolio.csv", index=False)
    pd.DataFrame(summaries).to_csv(output_dir / "daily_sensitivity.csv", index=False)
    if sizing_summaries:
        pd.DataFrame(sizing_summaries).to_csv(output_dir / "sizing_sensitivity.csv", index=False)
    if volatility_summaries:
        pd.DataFrame(volatility_summaries).to_csv(
            output_dir / "volatility_filter_summary.csv", index=False)
        pd.DataFrame(volatility_yearly).to_csv(
            output_dir / "volatility_filter_yearly.csv", index=False)
    pd.DataFrame(hold_profile).to_csv(output_dir / "paired_hold_profile.csv", index=False)
    yearly_rows = []
    prior_year_end = 10_000_000.0
    for year, group in rf_daily.groupby(rf_daily.date.dt.year):
        end_nav = float(group.nav.iloc[-1])
        yearly_rows.append({"year": int(year), "first_date": str(group.date.iloc[0].date()),
                            "last_date": str(group.date.iloc[-1].date()),
                            "start_nav": prior_year_end, "end_nav": end_nav,
                            "year_pnl": end_nav - prior_year_end,
                            "year_return": end_nav / prior_year_end - 1,
                            "trading_days": len(group)})
        prior_year_end = end_nav
    pd.DataFrame(yearly_rows).to_csv(output_dir / "riskfree_yearly.csv", index=False)
    audit = {
        "eligible_contracts": len(t), "required_daily_quote_lookups": len(lookup),
        "matched_daily_quotes": len(quotes),
        "daily_quote_coverage": len(quotes) / len(lookup) if len(lookup) else None,
        "eligible_contracts_with_adjustment_or_special_settlement": len(nonstandard),
        "contract_level_quotes_persisted": False,
        "source_optionmetrics_dir": str(optionmetrics_dir),
        "factor_provenance": factor_provenance,
        "all_cash_final_nav": all_cash_final,
    }
    (output_dir / "daily_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(pd.DataFrame(summaries).to_string(index=False), flush=True)
    return audit
