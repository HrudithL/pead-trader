"""Matched PEAD stock, long-call, and debit-call-spread study.

OptionMetrics quote rows are streamed from the licensed archive and never saved.
Only aggregate portfolio curves, summaries, and audits are written.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pead.common.paths import INITIAL_CAPITAL, REPO_ROOT
from pead.research.french_factors import load_french_daily
from pead.research.options_daily_portfolio import attach_prior_quarter_volatility
from pead.research.options_deep_diagnostics import hac_regression
from pead.research.options_expiry_grid import prepare_strategy_events

CONTRACT_MULTIPLIER = 100
DTE_TARGET, LONG_DELTA, SHORT_DELTA, HOLD = 75, 0.70, 0.30, 40
MAX_DTE_DISTANCE, MAX_DELTA_DISTANCE = 25, 0.10
MAX_SPREAD_FRACTION, MIN_OI = 0.30, 100
OPTION_QUOTE_COLUMNS = ["secid", "date", "exdate", "cp_flag", "strike_price", "best_bid",
                        "best_offer", "delta", "impl_volatility", "volume", "open_interest",
                        "optionid", "cfadj", "ss_flag"]
DAILY_QUOTE_COLUMNS = ["secid", "date", "optionid", "best_bid", "best_offer",
                       "open_interest", "volume", "cfadj", "ss_flag"]


def _read_calendar(data_dir: Path):
    option = pd.DatetimeIndex(pd.to_datetime(
        pd.read_parquet(data_dir / "metadata" / "om_trading_calendar.parquet").date))
    market = pd.DatetimeIndex(pd.to_datetime(pd.read_parquet(
        data_dir / "normalized_equity" / "market_benchmark.parquet", columns=["date"]).date))
    return option.intersection(market).sort_values().unique(), option.sort_values().unique()


def _load_events(data_dir: Path, years: list[int]):
    path = data_dir / "event_options" / "decile_events_secid.parquet"
    cols = ["secid", "permno", "ibes_ticker", "fpedats", "anndats", "anntims",
            "anntims_unknown", "sue_primary", "day0_date", "decile", "avg_dollar_volume_21d"]
    events = pd.read_parquet(path, columns=cols)
    events["day0_date"] = pd.to_datetime(events.day0_date)
    events["secid"] = events.secid.astype("int64")
    events["permno"] = pd.to_numeric(events.permno, errors="coerce")
    events["fpedats"], events["anndats"] = pd.to_datetime(events.fpedats), pd.to_datetime(events.anndats)
    events, audit = prepare_strategy_events(events, years)
    return events[events.decile.ge(9)].copy(), audit


def _scan_event_chains(year: int, events: pd.DataFrame, optionmetrics_dir: Path) -> pd.DataFrame:
    path = optionmetrics_dir / f"opprcd{year}.parquet"
    if not path.exists() or events.empty:
        return pd.DataFrame(columns=OPTION_QUOTE_COLUMNS)
    keys = events[["secid", "day0_date"]].drop_duplicates().rename(columns={"day0_date": "date"})
    secids = set(keys.secid.astype("int64"))
    matches, pf = [], pq.ParquetFile(path)
    for batch in pf.iter_batches(columns=OPTION_QUOTE_COLUMNS, batch_size=1_000_000):
        raw = batch.to_pandas(date_as_object=False)
        raw = raw[raw.secid.isin(secids)]
        if raw.empty:
            continue
        hit = raw.merge(keys, on=["secid", "date"], how="inner", validate="many_to_one")
        if len(hit):
            matches.append(hit)
    if not matches:
        return pd.DataFrame(columns=OPTION_QUOTE_COLUMNS)
    return pd.concat(matches, ignore_index=True).drop_duplicates(["secid", "date", "optionid"])


def _select_long_and_short(raw: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    event_cols = [c for c in events.columns if c != "day0_date"]
    r = raw.copy()
    r["date"], r["exdate"] = pd.to_datetime(r.date), pd.to_datetime(r.exdate)
    r["strike"] = pd.to_numeric(r.strike_price, errors="coerce") / 1000.0
    r["dte"] = (r.exdate - r.date).dt.days
    r["spread_fraction"] = (r.best_offer - r.best_bid) / r.best_offer.where(r.best_offer.gt(0))
    r = r.merge(events[event_cols + ["day0_date"]], left_on=["secid", "date"],
                right_on=["secid", "day0_date"], how="inner", validate="many_to_one")
    valid = (r.cp_flag.eq("C") & r.delta.notna() & r.best_bid.gt(0)
             & r.best_offer.ge(r.best_bid) & r.best_offer.ge(0.10)
             & r.spread_fraction.le(MAX_SPREAD_FRACTION)
             & r.open_interest.ge(MIN_OI) & r.dte.gt(0))
    r = r.loc[valid].copy()
    if r.empty:
        return pd.DataFrame()
    r["dte_error"] = (r.dte - DTE_TARGET).abs()
    event_keys = ["secid", "day0_date"]
    longs = r[r.dte_error.le(MAX_DTE_DISTANCE)
              & (r.delta.abs() - LONG_DELTA).abs().le(MAX_DELTA_DISTANCE)].copy()
    if longs.empty:
        return pd.DataFrame()
    longs["delta_error"] = (longs.delta.abs() - LONG_DELTA).abs()
    longs = longs.sort_values(event_keys + ["dte_error", "delta_error", "spread_fraction", "optionid"],
                              kind="stable").drop_duplicates(event_keys).copy()
    long_basis = longs[event_keys + ["exdate", "strike", "optionid"]].rename(
        columns={"exdate": "long_exdate", "strike": "long_strike", "optionid": "long_id"})
    shorts = r.merge(long_basis, on=event_keys, how="inner", validate="many_to_one")
    shorts = shorts[(shorts.exdate.eq(shorts.long_exdate)) & shorts.strike.gt(shorts.long_strike)
                    & shorts.optionid.ne(shorts.long_id)
                    & (shorts.delta.abs() - SHORT_DELTA).abs().le(MAX_DELTA_DISTANCE)].copy()
    if len(shorts):
        shorts["short_delta_error"] = (shorts.delta.abs() - SHORT_DELTA).abs()
        shorts = shorts.sort_values(event_keys + ["short_delta_error", "spread_fraction", "optionid"],
                                    kind="stable").drop_duplicates(event_keys)
        short_cols = shorts[event_keys + ["optionid", "strike", "delta", "best_bid", "best_offer",
                                          "open_interest", "volume", "spread_fraction"]].rename(columns={
            "optionid": "short_optionid", "strike": "short_strike", "delta": "short_delta",
            "best_bid": "day0_short_bid", "best_offer": "day0_short_ask",
            "open_interest": "day0_short_oi", "volume": "day0_short_volume",
            "spread_fraction": "day0_short_spread_fraction"})
        longs = longs.merge(short_cols, on=event_keys, how="left", validate="one_to_one")
    else:
        longs["short_optionid"] = np.nan
        longs["short_strike"] = np.nan
        longs["short_delta"] = np.nan
    return longs.rename(columns={"optionid": "long_optionid", "strike": "long_strike",
                                 "delta": "long_delta", "best_bid": "day0_long_bid",
                                 "best_offer": "day0_long_ask", "open_interest": "day0_long_oi",
                                 "volume": "day0_long_volume",
                                 "spread_fraction": "day0_long_spread_fraction"})


def _schedule_trades(candidates: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    t = candidates.copy().reset_index(drop=True)
    t["short_optionid"] = pd.to_numeric(t.short_optionid, errors="coerce")
    cal = pd.DatetimeIndex(calendar)
    idx = cal.get_indexer(pd.to_datetime(t.day0_date))
    valid = (idx >= 0) & (idx + HOLD < len(cal))
    t, idx = t.iloc[np.flatnonzero(valid)].copy(), idx[valid]
    gaps = np.r_[False, np.diff(cal.to_numpy()).astype("timedelta64[D]").astype(int) > 10]
    no_gap = (np.cumsum(gaps)[idx + HOLD] - np.cumsum(gaps)[idx]) == 0
    t, idx = t.iloc[np.flatnonzero(no_gap)].copy(), idx[no_gap]
    t["entry_date"], t["exit_date"] = cal.to_numpy()[idx + 1], cal.to_numpy()[idx + HOLD]
    t = t[(pd.to_datetime(t.exdate) - pd.to_datetime(t.exit_date)).dt.days.ge(10)].copy()
    t["trade_id"] = np.arange(len(t), dtype="int64")
    return t.reset_index(drop=True)


def _make_quote_lookup(trades: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    rows, cal = [], pd.DatetimeIndex(calendar)
    for row in trades.itertuples(index=False):
        a, b = cal.get_indexer([row.entry_date])[0], cal.get_indexer([row.exit_date])[0]
        if a < 0 or b <= a:
            continue
        legs = [("long", int(row.long_optionid))]
        if pd.notna(row.short_optionid):
            legs.append(("short", int(row.short_optionid)))
        for leg, optionid in legs:
            rows.extend((int(row.trade_id), int(row.secid), optionid, leg, date)
                        for date in cal[a:b + 1])
    return pd.DataFrame(rows, columns=["trade_id", "secid", "optionid", "leg", "date"])


def _scan_daily_quotes(lookup: pd.DataFrame, optionmetrics_dir: Path) -> pd.DataFrame:
    if lookup.empty:
        return pd.DataFrame(columns=[*lookup.columns, *DAILY_QUOTE_COLUMNS[3:]])
    keys = lookup[["secid", "date", "optionid"]].drop_duplicates()
    found = []
    for year, group in keys.groupby(keys.date.dt.year):
        path = optionmetrics_dir / f"opprcd{int(year)}.parquet"
        if not path.exists():
            continue
        secids = set(group.secid.astype("int64"))
        matches, pf = [], pq.ParquetFile(path)
        for batch in pf.iter_batches(columns=DAILY_QUOTE_COLUMNS, batch_size=1_000_000):
            raw = batch.to_pandas(date_as_object=False)
            raw = raw[raw.secid.isin(secids)]
            if raw.empty:
                continue
            hit = raw.merge(group, on=["secid", "date", "optionid"], how="inner",
                            validate="many_to_one")
            if len(hit):
                matches.append(hit)
        n = sum(len(x) for x in matches)
        print(f"daily quote scan {year}: {len(group):,} keys, {n:,} matching rows", flush=True)
        if matches:
            found.append(pd.concat(matches, ignore_index=True))
    if not found:
        return pd.DataFrame(columns=[*lookup.columns, *DAILY_QUOTE_COLUMNS[3:]])
    out = pd.concat(found, ignore_index=True).drop_duplicates(["secid", "date", "optionid"])
    out = lookup.merge(out, on=["secid", "date", "optionid"], how="inner",
                       validate="many_to_one")
    if out.duplicated(["trade_id", "leg", "date"]).any():
        raise ValueError("daily option quote keys are not unique")
    return out

def _scan_stock_panel(trades: pd.DataFrame, calendar: pd.DatetimeIndex,
                      data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), {"stock_paths_built": 0}
    permnos = set(pd.to_numeric(trades.permno, errors="coerce").dropna().astype("int64"))
    min_year = int(pd.to_datetime(trades.entry_date).dt.year.min())
    max_year = int(pd.to_datetime(trades.exit_date).dt.year.max())
    frames = []
    for year in range(min_year, max_year + 1):
        path = data_dir / "normalized_equity" / f"equity_{year}.parquet"
        if not path.exists():
            continue
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(columns=["permno", "date", "prc", "ret"],
                                     batch_size=1_000_000):
            part = batch.to_pandas(date_as_object=False)
            part = part[part.permno.isin(permnos)]
            if len(part):
                frames.append(part)
        print(f"stock return scan {year}: retained rows so far "
              f"{sum(len(x) for x in frames):,}", flush=True)
    if not frames:
        return pd.DataFrame(), pd.DataFrame(), {"stock_paths_built": 0}
    equity = pd.concat(frames, ignore_index=True).drop_duplicates(["permno", "date"])
    equity["date"] = pd.to_datetime(equity.date)
    equity["prc"] = pd.to_numeric(equity.prc, errors="coerce").abs()
    equity["ret"] = pd.to_numeric(equity.ret, errors="coerce")
    by_permno = {int(p): g.sort_values("date").set_index("date")
                 for p, g in equity.groupby("permno", sort=False)}
    paths, info = [], []
    gaps_total = fallbacks_total = 0
    cal = pd.DatetimeIndex(calendar)
    for row in trades.itertuples(index=False):
        g = by_permno.get(int(row.permno)) if pd.notna(row.permno) else None
        if g is None or row.entry_date not in g.index or row.exit_date not in g.index:
            continue
        entry_price = float(g.at[row.entry_date, "prc"])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue
        session_dates = cal[(cal >= row.entry_date) & (cal <= row.exit_date)]
        prev_price, factor = entry_price, 1.0
        event_paths = [(int(row.trade_id), row.entry_date, factor)]
        missing = fallbacks = 0
        for date in session_dates[session_dates > row.entry_date]:
            if date not in g.index:
                missing += 1
                event_paths.append((int(row.trade_id), date, factor))
                continue
            r = g.at[date, "ret"]
            price = float(g.at[date, "prc"]) if pd.notna(g.at[date, "prc"]) else np.nan
            if not np.isfinite(r):
                if np.isfinite(price) and np.isfinite(prev_price) and prev_price > 0:
                    r, fallbacks = price / prev_price - 1.0, fallbacks + 1
                else:
                    missing += 1
                    event_paths.append((int(row.trade_id), date, factor))
                    continue
            factor = factor * max(0.0, 1.0 + float(r))
            if np.isfinite(price) and price > 0:
                prev_price = price
            event_paths.append((int(row.trade_id), date, factor))
        if not any(d == row.exit_date for _, d, _ in event_paths):
            continue
        exit_factor = float(event_paths[-1][2])
        info.append({"trade_id": int(row.trade_id), "stock_entry_price": entry_price,
                     "stock_exit_factor": exit_factor, "stock_return": exit_factor - 1.0,
                     "stock_missing_return_days": missing,
                     "stock_price_return_fallbacks": fallbacks,
                     "stock_adv21": float(row.avg_dollar_volume_21d)
                     if pd.notna(row.avg_dollar_volume_21d) else np.nan})
        paths.extend(event_paths)
        gaps_total += missing
        fallbacks_total += fallbacks
    audit = {"candidate_stock_events": int(len(trades)), "stock_paths_built": int(len(info)),
             "stock_missing_return_days_carried": int(gaps_total),
             "stock_price_return_fallbacks": int(fallbacks_total)}
    return pd.DataFrame(info), pd.DataFrame(paths, columns=["trade_id", "date", "factor"]), audit


def _entry_quote_table(trades: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    fields = ["best_bid", "best_offer", "open_interest", "volume", "cfadj", "ss_flag"]
    if quotes.empty:
        return pd.DataFrame(columns=["trade_id", *[f"entry_{leg}_{f}" for leg in ("long", "short")
                                                       for f in fields]])
    entry = quotes.merge(trades[["trade_id", "entry_date"]], on="trade_id", how="inner",
                         validate="many_to_one")
    entry = entry[entry.date.eq(entry.entry_date)].copy()
    wide = entry.pivot(index="trade_id", columns="leg", values=fields)
    wide.columns = [f"entry_{leg}_{field}" for field, leg in wide.columns]
    wide = wide.reindex(columns=[f"entry_{leg}_{f}" for leg in ("long", "short") for f in fields])
    return wide.reset_index()


def _quote_eligibility(trades: pd.DataFrame, entry: pd.DataFrame):
    t = trades.merge(entry, on="trade_id", how="left", validate="one_to_one")
    for leg in ("long", "short"):
        for col in ("best_bid", "best_offer", "open_interest", "volume", "cfadj", "ss_flag"):
            name = f"entry_{leg}_{col}"
            if name not in t:
                t[name] = np.nan
    def valid_leg(leg: str) -> pd.Series:
        bid, ask, oi = (t[f"entry_{leg}_{c}"] for c in ("best_bid", "best_offer", "open_interest"))
        spread = (ask - bid) / ask.where(ask.gt(0))
        return bid.gt(0) & ask.ge(bid) & ask.ge(0.10) & oi.ge(MIN_OI) & spread.le(MAX_SPREAD_FRACTION)
    t["long_entry_valid"], t["short_entry_valid"] = valid_leg("long"), valid_leg("short")
    t["spread_entry_debit"] = t.entry_long_best_offer - t.entry_short_best_bid
    t["spread_width"] = t.short_strike - t.long_strike
    t["spread_entry_valid"] = (t.long_entry_valid & t.short_entry_valid
                                & t.spread_entry_debit.gt(0)
                                & t.spread_entry_debit.le(t.spread_width))
    t["long_entry_cost_per_contract"] = t.entry_long_best_offer * CONTRACT_MULTIPLIER
    t["spread_entry_cost_per_contract"] = t.spread_entry_debit * CONTRACT_MULTIPLIER
    t["long_entry_oi"] = t.entry_long_open_interest
    t["spread_entry_oi"] = t[["entry_long_open_interest", "entry_short_open_interest"]].min(axis=1)
    funnel = {"selected_long_candidates": int(len(t)),
              "longs_with_short_day0_pair": int(t.short_optionid.notna().sum()),
              "valid_long_next_session_entry": int(t.long_entry_valid.sum()),
              "valid_short_next_session_entry": int(t.short_entry_valid.sum()),
              "valid_debit_spreads_next_session": int(t.spread_entry_valid.sum()),
              "missing_long_entry_quote": int(t.entry_long_best_offer.isna().sum()),
              "missing_short_entry_quote": int(t.entry_short_best_offer.isna().sum())}
    return t, funnel


def _build_marks(quotes: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame(columns=["trade_id", "date", "call_mark", "spread_mark"])
    wide = quotes.pivot_table(index=["trade_id", "date"], columns="leg",
                              values=["best_bid", "best_offer", "cfadj", "ss_flag"], aggfunc="first")
    wide.columns = [f"{field}_{leg}" for field, leg in wide.columns]
    wide = wide.reset_index()
    wide["call_mark"] = wide.get("best_bid_long", np.nan) * CONTRACT_MULTIPLIER
    spread = wide.get("best_bid_long", np.nan) - wide.get("best_offer_short", np.nan)
    wide["spread_mark"] = spread.clip(lower=0) * CONTRACT_MULTIPLIER
    return wide


def _metrics(daily: pd.DataFrame, factors: pd.DataFrame, initial_capital: float) -> dict:
    if daily.empty:
        return {}
    d = daily.copy()
    d["return"] = d.nav.pct_change().fillna(d.nav.iloc[0] / initial_capital - 1.0)
    rf = factors.set_index("date").RF.reindex(d.date).fillna(0.0).to_numpy(dtype=float)
    excess = d["return"].to_numpy(dtype=float) - rf
    vol = float(d["return"].std(ddof=1) * np.sqrt(252)) if len(d) > 1 else np.nan
    excess_vol = float(np.std(excess, ddof=1) * np.sqrt(252)) if len(excess) > 1 else np.nan
    years = max((d.date.iloc[-1] - d.date.iloc[0]).days / 365.25, 1 / 365.25)
    wealth = float(d.nav.iloc[-1] / initial_capital)
    out = {"study_start": str(d.date.iloc[0].date()), "study_end": str(d.date.iloc[-1].date()),
           "ending_nav": float(d.nav.iloc[-1]), "net_pnl": float(d.nav.iloc[-1] - initial_capital),
           "cagr": float(wealth ** (1.0 / years) - 1.0) if wealth > 0 else -1.0,
           "annualized_volatility": vol,
           "excess_rf_sharpe": float(np.mean(excess) * 252 / excess_vol)
           if excess_vol and excess_vol > 0 else np.nan,
           "max_drawdown": float((d.nav / d.nav.cummax() - 1.0).min()),
           "cash_model": "French daily RF proxy"}
    f = d[["date", "return"]].merge(factors, on="date", how="left", validate="one_to_one")
    clean = f.dropna(subset=["return", "RF", "Mkt-RF", "SMB", "HML", "Mom"])
    if len(clean) > 100:
        alpha = hac_regression(clean["return"].to_numpy() - clean.RF.to_numpy(),
                               clean[["Mkt-RF", "SMB", "HML", "Mom"]].to_numpy(), lags=60)
        out.update({"four_factor_alpha_annualized": alpha["annualized_intercept"],
                    "four_factor_alpha_hac_ci_low": alpha["annualized_intercept_95ci"][0],
                    "four_factor_alpha_hac_ci_high": alpha["annualized_intercept_95ci"][1],
                    "four_factor_alpha_hac_t": alpha["intercept_hac_t"],
                    "factor_regression_n": alpha["n"]})
    return out

def _simulate_option_book(trades: pd.DataFrame, marks: pd.DataFrame,
                          calendar: pd.DatetimeIndex, factors: pd.DataFrame,
                          strategy: str, *, initial_capital: float = INITIAL_CAPITAL,
                          premium_cap: float = 0.20, max_trade_fraction: float = 0.0025,
                          max_oi_fraction: float = 0.10, per_name_fraction: float = 0.02,
                          hold: int = HOLD - 1,
                          extra_cost_fraction: float = 0.0) -> tuple[pd.DataFrame, dict]:
    if trades.empty:
        return pd.DataFrame(), {}
    cost_col = "long_entry_cost_per_contract" if strategy == "call" else "spread_entry_cost_per_contract"
    oi_col = "long_entry_oi" if strategy == "call" else "spread_entry_oi"
    mark_col = "call_mark" if strategy == "call" else "spread_mark"
    t = trades.reset_index(drop=True).copy()
    t = t[t.long_entry_valid].copy() if strategy == "call" else t[t.spread_entry_valid].copy()
    if t.empty:
        return pd.DataFrame(), {}
    if strategy == "spread":
        widths = t[["trade_id", "spread_width"]].drop_duplicates("trade_id")
        marks = marks.merge(widths, on="trade_id", how="inner", validate="many_to_one")
        marks[mark_col] = marks[mark_col].clip(upper=marks.spread_width * CONTRACT_MULTIPLIER)
    entry_groups = t.groupby("entry_date", sort=False).indices
    by_day = {date: dict(zip(g.trade_id.astype(int), g[mark_col]))
              for date, g in marks[["trade_id", "date", mark_col]].groupby("date", sort=False)}
    cal = pd.DatetimeIndex(calendar)
    rf = factors.set_index("date").RF
    cash, prior_nav = float(initial_capital), float(initial_capital)
    open_pos, last_mark, trailing = {}, {}, []
    funded = missing_exit = zero_exit = capacity_days = 0
    total_cost = total_debit = 0.0
    records = []
    for date in cal:
        cash *= 1.0 + float(rf.get(date, 0.0))
        trailing = [d for d in trailing if (date - d).days <= 365]
        entrants = list(entry_groups.get(date, []))
        if entrants:
            n = len(trailing) + len(entrants)
            frac = min(max_trade_fraction, premium_cap * 252 / (max(1, n) * hold))
            outstanding = sum(p["premium"] for p in open_pos.values())
            room = max(0.0, min(cash, premium_cap * prior_nav - outstanding))
            desired = []
            for i in entrants:
                row = t.iloc[i]
                unit, oi = float(row[cost_col]), float(row[oi_col])
                if not (np.isfinite(unit) and unit > 0 and np.isfinite(oi) and oi > 0):
                    desired.append(0)
                    continue
                already_lots = 0
                for p in open_pos.values():
                    if int(p["secid"]) != int(row.secid):
                        continue
                    if (strategy == "call" and p["long_optionid"] == int(row.long_optionid)) or (
                            strategy == "spread" and (p["long_optionid"] == int(row.long_optionid)
                                                      or p["short_optionid"] == int(row.short_optionid))):
                        already_lots += int(p["lots"])
                oi_limit = max(0, int(np.floor(max_oi_fraction * oi)) - already_lots)
                name_premium = sum(p["premium"] for p in open_pos.values()
                                   if int(p["secid"]) == int(row.secid))
                budget = min(frac * prior_nav,
                             max(0.0, per_name_fraction * prior_nav - name_premium))
                desired.append(max(0, min(oi_limit, int(np.floor(budget / unit)))))
            desired_debit = sum(lots * float(t.iloc[i][cost_col]) for i, lots in zip(entrants, desired))
            scale = min(1.0, room / desired_debit) if desired_debit > 0 else 0.0
            capacity_days += int(scale < 1.0 and desired_debit > 0)
            allocated = [int(np.floor(lots * scale)) for lots in desired]
            for i, lots in zip(entrants, allocated):
                if lots <= 0:
                    continue
                row = t.iloc[i]
                debit = lots * float(row[cost_col])
                fee = debit * extra_cost_fraction
                if cash + 1e-8 < debit + fee:
                    continue
                cash -= debit + fee
                total_debit += debit
                total_cost += fee
                funded += 1
                short_id = int(row.short_optionid) if pd.notna(row.short_optionid) else -1
                open_pos[int(row.trade_id)] = {
                    "lots": lots, "premium": debit, "secid": int(row.secid),
                    "long_optionid": int(row.long_optionid), "short_optionid": short_id,
                    "exit_date": row.exit_date,
                }
                last_mark[int(row.trade_id)] = 0.0
            trailing.extend([date] * len(entrants))
        day_quotes = by_day.get(date, {})
        market_value = 0.0
        for trade_id, pos in list(open_pos.items()):
            raw = day_quotes.get(trade_id, np.nan)
            if np.isfinite(raw) and raw >= 0:
                value_per_contract = float(raw)
                last_mark[trade_id] = value_per_contract
                zero_exit += int(value_per_contract == 0 and date == pos["exit_date"])
            elif date == pos["exit_date"]:
                value_per_contract = 0.0
                missing_exit += 1
            else:
                value_per_contract = last_mark[trade_id]
            value = pos["lots"] * value_per_contract
            if date == pos["exit_date"]:
                cash += value
                del open_pos[trade_id]
                last_mark.pop(trade_id, None)
            else:
                market_value += value
        nav = cash + market_value
        records.append((date, nav, cash, market_value,
                        sum(p["premium"] for p in open_pos.values()), len(open_pos)))
        prior_nav = nav
    daily = pd.DataFrame(records, columns=["date", "nav", "cash", "market_value",
                                           "outstanding_premium", "open_positions"])
    summary = _metrics(daily, factors, initial_capital)
    summary.update({"strategy": strategy, "funded_trades": funded, "initial_debit": total_debit,
                    "extra_cost": total_cost, "missing_exits": missing_exit,
                    "zero_exits": zero_exit, "capacity_binding_days": capacity_days,
                    "unclosed_positions": len(open_pos),
                    "extra_cost_fraction_of_debit": extra_cost_fraction})
    return daily, summary


def _simulate_stock_book(trades: pd.DataFrame, stock_paths: pd.DataFrame,
                         calendar: pd.DatetimeIndex, factors: pd.DataFrame,
                         *, initial_capital: float = INITIAL_CAPITAL,
                         capital_cap: float = 0.20, max_trade_fraction: float = 0.0025,
                         per_name_fraction: float = 0.02,
                         participation: float = 0.10, cost_bps_per_side: float = 10.0,
                         hold: int = HOLD - 1) -> tuple[pd.DataFrame, dict]:
    if trades.empty or stock_paths.empty:
        return pd.DataFrame(), {}
    t = trades.reset_index(drop=True).copy()
    entry_groups = t.groupby("entry_date", sort=False).indices
    factor_by_day = {date: dict(zip(g.trade_id.astype(int), g.factor.astype(float)))
                     for date, g in stock_paths.groupby("date", sort=False)}
    cal = pd.DatetimeIndex(calendar)
    rf, fee_rate = factors.set_index("date").RF, cost_bps_per_side / 10000.0
    cash, prior_nav = float(initial_capital), float(initial_capital)
    open_pos, trailing = {}, []
    funded = capacity_days = 0
    total_buy = total_fees = 0.0
    records = []
    for date in cal:
        cash *= 1.0 + float(rf.get(date, 0.0))
        trailing = [d for d in trailing if (date - d).days <= 365]
        entrants = list(entry_groups.get(date, []))
        if entrants:
            n = len(trailing) + len(entrants)
            frac = min(max_trade_fraction, capital_cap * 252 / (max(1, n) * hold))
            outstanding = sum(p["market_value"] for p in open_pos.values())
            room = max(0.0, min(cash, capital_cap * prior_nav - outstanding))
            desired = []
            for i in entrants:
                row = t.iloc[i]
                price, adv = float(row.stock_entry_price), float(row.stock_adv21)
                if not (np.isfinite(price) and price > 0 and np.isfinite(adv) and adv > 0):
                    desired.append(0)
                    continue
                already_name = sum(p["market_value"] for p in open_pos.values()
                                   if int(p["permno"]) == int(row.permno))
                budget = min(frac * prior_nav,
                             max(0.0, per_name_fraction * prior_nav - already_name))
                volume_limit = participation * adv
                shares = max(0, min(int(np.floor(budget / (price * (1.0 + fee_rate)))),
                                    int(np.floor(volume_limit / price))))
                desired.append(shares)
            desired_value = sum(shares * float(t.iloc[i].stock_entry_price) * (1.0 + fee_rate)
                                for i, shares in zip(entrants, desired))
            scale = min(1.0, room / desired_value) if desired_value > 0 else 0.0
            capacity_days += int(scale < 1.0 and desired_value > 0)
            allocated = [int(np.floor(shares * scale)) for shares in desired]
            for i, shares in zip(entrants, allocated):
                if shares <= 0:
                    continue
                row = t.iloc[i]
                basis = shares * float(row.stock_entry_price)
                fee = basis * fee_rate
                if cash + 1e-8 < basis + fee:
                    continue
                cash -= basis + fee
                total_buy += basis
                total_fees += fee
                funded += 1
                open_pos[int(row.trade_id)] = {"shares": shares, "entry_price": float(row.stock_entry_price),
                                               "permno": int(row.permno), "exit_date": row.exit_date,
                                               "factor": 1.0, "market_value": basis}
            trailing.extend([date] * len(entrants))
        day_factors = factor_by_day.get(date, {})
        market_value = 0.0
        for trade_id, pos in list(open_pos.items()):
            if trade_id in day_factors and np.isfinite(day_factors[trade_id]):
                pos["factor"] = float(day_factors[trade_id])
            value = pos["shares"] * pos["entry_price"] * pos["factor"]
            if date == pos["exit_date"]:
                exit_fee = value * fee_rate
                cash += value - exit_fee
                total_fees += exit_fee
                del open_pos[trade_id]
            else:
                pos["market_value"] = value
                market_value += value
        nav = cash + market_value
        records.append((date, nav, cash, market_value, len(open_pos)))
        prior_nav = nav
    daily = pd.DataFrame(records, columns=["date", "nav", "cash", "market_value", "open_positions"])
    summary = _metrics(daily, factors, initial_capital)
    summary.update({"strategy": "stock_delta_matched_signal", "funded_trades": funded,
                    "stock_buy_notional": total_buy, "stock_trading_cost": total_fees,
                    "stock_cost_bps_per_side": cost_bps_per_side,
                    "capacity_binding_days": capacity_days, "unclosed_positions": len(open_pos)})
    return daily, summary

def _summarize_event_returns(trades: pd.DataFrame, quotes: pd.DataFrame,
                             stock_info: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    t = trades.merge(stock_info, on="trade_id", how="inner", validate="one_to_one")
    exits = t[["trade_id", "exit_date"]].merge(quotes, on="trade_id", how="left")
    exits = exits[exits.date.eq(exits.exit_date)]
    wide = exits.pivot_table(index="trade_id", columns="leg", values=["best_bid", "best_offer"],
                             aggfunc="first")
    wide.columns = [f"exit_{leg}_{field}" for field, leg in wide.columns]
    wide = wide.reindex(columns=["exit_long_best_bid", "exit_long_best_offer",
                                  "exit_short_best_bid", "exit_short_best_offer"])
    t = t.merge(wide.reset_index(), on="trade_id", how="left", validate="one_to_one")
    t["exit_long_bid"] = t.exit_long_best_bid.fillna(0).clip(lower=0)
    t["long_call_pnl"] = CONTRACT_MULTIPLIER * (t.exit_long_bid - t.entry_long_best_offer)
    t["spread_exit_value"] = (t.exit_long_bid - t.exit_short_best_offer).clip(lower=0).fillna(0)
    t["spread_exit_value"] = t["spread_exit_value"].clip(upper=t.spread_width)
    t["spread_pnl"] = CONTRACT_MULTIPLIER * (t.spread_exit_value - t.spread_entry_debit)
    delta_dollars = (t.long_delta.abs() * CONTRACT_MULTIPLIER * t.stock_entry_price).replace(0, np.nan)
    t["stock_delta_normalized"] = t.stock_return
    t["call_delta_normalized"] = t.long_call_pnl / delta_dollars
    t["spread_delta_normalized"] = t.spread_pnl / delta_dollars
    t["vol_bucket"] = t.get("tvol_60d_bucket", pd.Series("missing", index=t.index)).fillna("missing")
    groups = [("all", t)] + [(str(name), group) for name, group in t.groupby("vol_bucket", dropna=False)]
    rows = []
    for bucket, g in groups:
        for instrument, col in (("stock", "stock_delta_normalized"),
                                ("long_call", "call_delta_normalized"),
                                ("debit_spread", "spread_delta_normalized")):
            x = g[col].dropna()
            rows.append({"volatility_subset": bucket, "instrument": instrument,
                         "events": int(len(x)),
                         "mean_return_per_delta_dollar": float(x.mean()) if len(x) else np.nan,
                         "median_return_per_delta_dollar": float(x.median()) if len(x) else np.nan,
                         "win_fraction": float(x.gt(0).mean()) if len(x) else np.nan,
                         "p10_return_per_delta_dollar": float(x.quantile(.10)) if len(x) else np.nan,
                         "p90_return_per_delta_dollar": float(x.quantile(.90)) if len(x) else np.nan})
    return pd.DataFrame(rows)


def _yearly_nav(daily_by_strategy: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for strategy, daily in daily_by_strategy.items():
        if daily.empty:
            continue
        x = daily.copy()
        x["year"] = x.date.dt.year
        for year, g in x.groupby("year"):
            rows.append({"strategy": strategy, "year": int(year),
                         "nav_first": float(g.nav.iloc[0]), "nav_last": float(g.nav.iloc[-1]),
                         "calendar_year_return": float(g.nav.iloc[-1] / g.nav.iloc[0] - 1.0),
                         "min_nav": float(g.nav.min()), "max_nav": float(g.nav.max())})
    return pd.DataFrame(rows)


def run(years: list[int], data_dir: Path, optionmetrics_dir: Path,
        output_dir: Path, *, initial_capital: float = INITIAL_CAPITAL,
        factor_dir: Path | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    factor_dir = factor_dir or (REPO_ROOT / "data" / "factor")
    factors_all, factor_provenance = load_french_daily(factor_dir)
    option_market_calendar, _ = _read_calendar(data_dir)
    full_market = pd.DatetimeIndex(pd.to_datetime(pd.read_parquet(
        data_dir / "normalized_equity" / "market_benchmark.parquet", columns=["date"]).date))
    events, event_audit = _load_events(data_dir, years)
    entry_frames, entry_scan_years = [], []
    for year in years:
        year_events = events[events.day0_date.dt.year.eq(year)]
        if year_events.empty:
            continue
        print(f"entry chain scan {year}: {len(year_events):,} high-SUE events", flush=True)
        raw = _scan_event_chains(year, year_events, optionmetrics_dir)
        entry_scan_years.append(year)
        selected = _select_long_and_short(raw, year_events)
        if len(selected):
            entry_frames.append(selected)
        print(f"entry chain scan {year}: {len(raw):,} chain rows, "
              f"{len(selected):,} long calls selected", flush=True)
    if not entry_frames:
        raise ValueError("no eligible long-call candidates found")
    candidates = _schedule_trades(pd.concat(entry_frames, ignore_index=True), option_market_calendar)
    candidates = attach_prior_quarter_volatility(candidates, data_dir)
    lookup = _make_quote_lookup(candidates, option_market_calendar)
    print(f"selected leg/date lookup rows: {len(lookup):,}", flush=True)
    option_quotes = _scan_daily_quotes(lookup, optionmetrics_dir)
    print(f"matched daily leg quotes: {len(option_quotes):,}", flush=True)
    entry = _entry_quote_table(candidates, option_quotes)
    candidates, funnel = _quote_eligibility(candidates, entry)
    study_start = full_market[full_market.year == min(years)].min()
    study_end = full_market[full_market.year == max(years)].max()
    simulation_calendar = full_market[(full_market >= study_start) & (full_market <= study_end)]
    stock_calendar = simulation_calendar
    stock_info, stock_paths, stock_audit = _scan_stock_panel(candidates, stock_calendar, data_dir)
    stock_info = stock_info[pd.to_numeric(stock_info.stock_adv21, errors="coerce").gt(0)].copy()
    stock_ids = set(stock_info.trade_id.astype(int))
    common = candidates[candidates.spread_entry_valid & candidates.trade_id.isin(stock_ids)].copy()
    all_calls = candidates[candidates.long_entry_valid].copy()
    if common.empty:
        raise ValueError("no common stock/call/spread next-session entries")
    common_ids, all_call_ids = set(common.trade_id.astype(int)), set(all_calls.trade_id.astype(int))
    stock_paths = stock_paths[stock_paths.trade_id.isin(common_ids)]
    stock_info_common = stock_info[stock_info.trade_id.isin(common_ids)]
    quote_common = option_quotes[option_quotes.trade_id.isin(common_ids)]
    quote_all = option_quotes[option_quotes.trade_id.isin(all_call_ids)]
    marks_common, marks_all = _build_marks(quote_common), _build_marks(quote_all)
    factors = factors_all[factors_all.date.isin(simulation_calendar)].copy()

    portfolios: dict[str, tuple[pd.DataFrame, dict]] = {}
    d, s = _simulate_option_book(all_calls, marks_all, simulation_calendar, factors, "call",
                                 initial_capital=initial_capital)
    portfolios["long_call_all_valid"] = (d, s)
    d, s = _simulate_option_book(common, marks_common, simulation_calendar, factors, "call",
                                 initial_capital=initial_capital)
    portfolios["long_call_common_spread_sample"] = (d, s)
    d, s = _simulate_option_book(common, marks_common, simulation_calendar, factors, "spread",
                                 initial_capital=initial_capital)
    portfolios["debit_call_spread"] = (d, s)
    stock_trades = common.merge(stock_info_common, on="trade_id", how="inner", validate="one_to_one")
    for bps in (0.0, 10.0, 25.0):
        d, s = _simulate_stock_book(stock_trades, stock_paths, simulation_calendar, factors,
                                    initial_capital=initial_capital, cost_bps_per_side=bps)
        portfolios[f"stock_common_{int(bps)}bps_side"] = (d, s)
    d, s = _simulate_option_book(common, marks_common, simulation_calendar, factors, "call",
                                 initial_capital=initial_capital, extra_cost_fraction=0.03)
    portfolios["long_call_common_plus_3pct_cost"] = (d, s)
    d, s = _simulate_option_book(common, marks_common, simulation_calendar, factors, "spread",
                                 initial_capital=initial_capital, extra_cost_fraction=0.03)
    portfolios["debit_spread_plus_3pct_cost"] = (d, s)

    low = common[common.tvol_60d_bucket.eq("low")].copy()
    low_ids = set(low.trade_id.astype(int))
    low_quotes = quote_common[quote_common.trade_id.isin(low_ids)]
    low_marks = _build_marks(low_quotes)
    d, s = _simulate_option_book(low, low_marks, simulation_calendar, factors, "call",
                                 initial_capital=initial_capital)
    portfolios["long_call_common_low_tvol"] = (d, s)
    d, s = _simulate_option_book(low, low_marks, simulation_calendar, factors, "spread",
                                 initial_capital=initial_capital)
    portfolios["debit_spread_common_low_tvol"] = (d, s)
    low_stock = stock_trades[stock_trades.trade_id.isin(low_ids)]
    low_paths = stock_paths[stock_paths.trade_id.isin(low_ids)]
    d, s = _simulate_stock_book(low_stock, low_paths, simulation_calendar, factors,
                                initial_capital=initial_capital, cost_bps_per_side=10.0)
    portfolios["stock_common_low_tvol_10bps_side"] = (d, s)

    summary_rows, daily_by_strategy = [], {}
    for name, (daily, summary) in portfolios.items():
        if not summary:
            continue
        summary["scenario"] = name
        summary["candidate_events"] = int(len(all_calls) if name == "long_call_all_valid" else
            len(low) if "low_tvol" in name else len(common))
        summary_rows.append(summary)
        daily_by_strategy[name] = daily
        daily.to_csv(output_dir / f"daily_nav_{name}.csv", index=False)
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(output_dir / "portfolio_summary.csv", index=False)
    _yearly_nav(daily_by_strategy).to_csv(output_dir / "portfolio_yearly.csv", index=False)
    _summarize_event_returns(common, quote_common, stock_info_common).to_csv(
        output_dir / "matched_event_pnl_per_delta_dollar.csv", index=False)
    quote_audit = {}
    for leg, group in option_quotes.groupby("leg"):
        quote_audit[f"{leg}_rows"] = int(len(group))
        quote_audit[f"{leg}_nonstandard_cfadj_or_settlement_rows"] = int(
            ((~np.isclose(pd.to_numeric(group.cfadj, errors="coerce").fillna(-1), 1.0))
             | group.ss_flag.fillna("?").astype(str).ne("0")).sum())
    audit = {
        "study": "frozen C/75/0.70/40 next-session stock/call/debit-spread comparison",
        "years_requested": years,
        "option_calendar_first": str(option_market_calendar.min().date()),
        "option_calendar_last": str(option_market_calendar.max().date()),
        "portfolio_calendar_first": str(simulation_calendar.min().date()),
        "portfolio_calendar_last": str(simulation_calendar.max().date()),
        "event_collision_audit": event_audit,
        "entry_chain_years_scanned": entry_scan_years,
        "selected_long_candidates": int(len(candidates)), "entry_funnel": funnel,
        "common_spread_stock_event_count": int(len(common)),
        "common_unique_permnos": int(common.permno.nunique()),
        "common_low_tvol_events": int(len(low)),
        "daily_quote_lookups": int(len(lookup)), "daily_quotes_matched": int(len(option_quotes)),
        "daily_quote_match_fraction": float(len(option_quotes) / len(lookup)) if len(lookup) else None,
        "quote_audit": quote_audit, "stock_path_audit": stock_audit,
        "accounting": {"initial_capital": initial_capital, "capital_cap_fraction": 0.20,
            "max_trade_fraction": 0.0025, "option_oi_participation": 0.10,
            "per_name_cap_fraction": 0.02,
            "stock_adv21_participation": 0.10, "call_entry_exit": "ask next session / bid scheduled exit",
            "spread_entry_exit": "long ask minus short bid / long bid minus short ask",
            "stock_entry_exit": "CRSP close proxy; daily CRSP total returns; same event and exit dates",
            "stock_trading_cost_sensitivities_bps_per_side": [0, 10, 25],
            "extra_option_cost_sensitivity": "3% of net debit in addition to quoted leg bid/ask"},
        "factor_provenance": factor_provenance,
    }
    (output_dir / "study_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return {"summary": summary_df, "audit": audit}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+",
                        default=[y for y in range(1998, 2014) if y != 2011])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--optionmetrics-dir", type=Path, default=Path("D:/OptionMetrics/parquet"))
    parser.add_argument("--output-dir", type=Path,
                        default=REPO_ROOT / "results" / "options_structure_comparison_2026-10-24")
    parser.add_argument("--factor-dir", type=Path, default=REPO_ROOT / "data" / "factor")
    parser.add_argument("--initial-capital", type=float, default=INITIAL_CAPITAL)
    args = parser.parse_args()
    result = run(args.years, args.data_dir, args.optionmetrics_dir, args.output_dir,
                 initial_capital=args.initial_capital, factor_dir=args.factor_dir)
    print(result["summary"].to_string(index=False))
    print(json.dumps(result["audit"], indent=2))


if __name__ == "__main__":
    main()
