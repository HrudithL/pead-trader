"""Audit point-in-time IBES forecast-revision coverage around PEAD events.

This is a feasibility audit, not a return backtest. It reads the local IBES
Detail History extract, conservatively excludes event-day updates, and writes
only aggregate coverage statistics (never analyst-level forecast rows).
"""
from __future__ import annotations

import argparse
import bisect
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from pead.research.options_expiry_grid import prepare_strategy_events

YEARS = [*range(1998, 2011), 2012, 2013]
RAW_COLUMNS = ["ticker", "fpedats", "value", "analys", "revdats", "revtims", "measure"]


def _read_events(data_dir: Path, years: list[int]) -> tuple[pd.DataFrame, dict]:
    columns = ["secid", "ibes_ticker", "fpedats", "day0_date", "decile", "sue_primary",
               "anntims_unknown"]
    events = pd.read_parquet(data_dir / "event_options" / "decile_events_secid.parquet",
                             columns=columns)
    events["day0_date"] = pd.to_datetime(events.day0_date)
    events["fpedats"] = pd.to_datetime(events.fpedats)
    selected, audit = prepare_strategy_events(events, years)
    selected = selected[selected.decile.ge(9)].copy()
    selected["ticker"] = selected.ibes_ticker.fillna("").astype(str).str.strip().str.upper()
    selected = selected[selected.ticker.ne("") & selected.fpedats.notna()].copy()
    selected = selected.reset_index(drop=True)
    selected["event_id"] = np.arange(len(selected), dtype=np.int64)
    return selected, audit


def _read_calendar(data_dir: Path) -> pd.DatetimeIndex:
    option = pd.DatetimeIndex(pd.to_datetime(
        pd.read_parquet(data_dir / "metadata" / "om_trading_calendar.parquet").date))
    market = pd.DatetimeIndex(pd.to_datetime(pd.read_parquet(
        data_dir / "normalized_equity" / "market_benchmark.parquet", columns=["date"]).date))
    return option.intersection(market).sort_values().unique()


def _iter_ibes_rows(path: Path, ticker_set: set[str]):
    """Yield only selected US EPS ticker rows from parquet batches."""
    pf = pq.ParquetFile(path)
    retained_total = 0
    for batch_no, batch in enumerate(pf.iter_batches(columns=RAW_COLUMNS, batch_size=500_000), 1):
        frame = batch.to_pandas(date_as_object=False)
        frame["ticker"] = frame.ticker.fillna("").astype(str).str.strip().str.upper()
        frame = frame[frame.ticker.isin(ticker_set) & frame.measure.eq("EPS")].copy()
        if frame.empty:
            continue
        retained_total += len(frame)
        frame["fpedats"] = pd.to_datetime(frame.fpedats, errors="coerce")
        frame["revdats"] = pd.to_datetime(frame.revdats, errors="coerce")
        frame["value"] = pd.to_numeric(frame.value, errors="coerce")
        frame["analys"] = pd.to_numeric(frame.analys, errors="coerce")
        yield frame.dropna(subset=["fpedats", "revdats", "value", "analys"])
        if batch_no % 5 == 0:
            print(f"IBES scan: batch {batch_no}/{pf.metadata.num_row_groups} processed; "
                  f"{retained_total:,} selected-ticker EPS rows retained", flush=True)


def _load_target_forecasts(raw_path: Path, events: pd.DataFrame) -> tuple[dict, dict]:
    """Find each event's nearest plausible next-quarter forecast horizon and rows."""
    tickers = set(events.ticker)
    periods: dict[str, set[pd.Timestamp]] = defaultdict(set)
    # Pass one: obtain period-end dates for the selected ticker universe.
    for frame in _iter_ibes_rows(raw_path, tickers):
        for ticker, dates in frame.groupby("ticker", sort=False).fpedats:
            periods[ticker].update(pd.Timestamp(x) for x in dates.dropna().unique())
    print(f"IBES period map built for {len(periods):,} tickers", flush=True)
    sorted_periods = {ticker: sorted(dates) for ticker, dates in periods.items()}

    target_period: dict[int, pd.Timestamp] = {}
    pair_keys: set[tuple[str, pd.Timestamp]] = set()
    for row in events.itertuples(index=False):
        base = pd.Timestamp(row.fpedats)
        sorted_dates = sorted_periods.get(row.ticker, [])
        pos = bisect.bisect_right(sorted_dates, base)
        candidates = []
        for d in sorted_dates[pos:]:
            distance = (d - base).days
            if distance > 180:
                break
            if distance >= 45:
                candidates.append(d)
                break
        if candidates:
            target_period[int(row.event_id)] = candidates[0]
            pair_keys.add((row.ticker, candidates[0]))
    print(f"Mapped {len(target_period):,}/{len(events):,} events to a future quarter "
          f"across {len(pair_keys):,} ticker-period pairs", flush=True)

    # Pass two: load forecasts only for the selected next-quarter ticker/period pairs.
    rows: dict[tuple[str, pd.Timestamp], list[pd.DataFrame]] = defaultdict(list)
    for frame in _iter_ibes_rows(raw_path, tickers):
        frame["pair"] = list(zip(frame.ticker, frame.fpedats))
        frame = frame[frame.pair.isin(pair_keys)]
        if frame.empty:
            continue
        for key, group in frame.groupby("pair", sort=False):
            rows[key].append(group.drop(columns="pair"))
    histories = {key: pd.concat(parts, ignore_index=True)
                 for key, parts in rows.items()}
    print(f"Loaded detail rows for {len(histories):,} selected ticker-period pairs", flush=True)
    return target_period, histories


def _event_stats(history: pd.DataFrame, event_date: pd.Timestamp,
                 cutoffs: dict[str, pd.Timestamp]) -> dict:
    """Summarize unique analyst forecast changes available before each entry."""
    result = {"baseline_analysts": 0}
    if history.empty:
        for name in cutoffs:
            result.update({f"{name}_changed_analysts": 0, f"{name}_new_analysts": 0,
                           f"{name}_median_change": np.nan})
        return result
    h = history[history.revdats.lt(event_date)].copy()
    if h.empty:
        baselines = pd.DataFrame(columns=["analys", "value"])
    else:
        h = h.sort_values(["analys", "revdats", "revtims"], kind="stable")
        baselines = h.drop_duplicates("analys", keep="last")[["analys", "value"]]
    baseline = dict(zip(baselines.analys.astype(int), baselines.value.astype(float)))
    result["baseline_analysts"] = len(baseline)
    post_all = history[history.revdats.gt(event_date)].copy()
    post_all = post_all.sort_values(["analys", "revdats", "revtims"], kind="stable")
    for name, entry_date in cutoffs.items():
        # A revision stamped on the entry date is excluded because the time-zone
        # and end-of-day ordering against the option quote are not established.
        post = post_all[post_all.revdats.lt(entry_date)]
        latest = post.drop_duplicates("analys", keep="last")
        latest_map = dict(zip(latest.analys.astype(int), latest.value.astype(float)))
        changed = [latest_map[a] - baseline[a] for a in baseline.keys() & latest_map.keys()
                   if not np.isclose(latest_map[a], baseline[a], rtol=0, atol=1e-12)]
        new_count = len(set(latest_map) - set(baseline))
        result[f"{name}_changed_analysts"] = len(changed)
        result[f"{name}_new_analysts"] = new_count
        result[f"{name}_median_change"] = float(np.median(changed)) if changed else np.nan
    return result


def run(data_dir: Path, raw_path: Path, output_dir: Path,
        years: list[int] = YEARS) -> tuple[Path, Path]:
    events, collision_audit = _read_events(data_dir, years)
    calendar = _read_calendar(data_dir)
    idx = calendar.get_indexer(pd.DatetimeIndex(events.day0_date))
    valid = (idx >= 0) & (idx + 12 < len(calendar))
    events = events.loc[valid].copy().reset_index(drop=True)
    idx = idx[valid]
    events["event_id"] = np.arange(len(events), dtype=np.int64)
    events["next_session"] = calendar.to_numpy()[idx + 1]
    # Each cutoff is the date on which the feature is frozen. The actual entry
    # would be the following session, so same-date IBES reissues are excluded.
    # Build cutoffs event by event because the market calendar index differs.
    event_cutoffs = {
        int(row.event_id): {f"d{h}": pd.Timestamp(calendar[i + h + 1])
                            for h in (1, 5, 10)}
        for row, i in zip(events.itertuples(index=False), idx)
    }
    target_period, histories = _load_target_forecasts(raw_path, events)
    records = []
    for row in events.itertuples(index=False):
        target = target_period.get(int(row.event_id))
        history = histories.get((row.ticker, target), pd.DataFrame()) if target else pd.DataFrame()
        record = {"event_id": int(row.event_id), "ticker": row.ticker,
                  "period_end": target, "day0_date": row.day0_date,
                  "entry_next_session": row.next_session,
                  "sue": float(row.sue_primary) if pd.notna(row.sue_primary) else np.nan,
                  "baseline_analysts": 0,
                  "d1_changed_analysts": 0, "d1_new_analysts": 0, "d1_median_change": np.nan,
                  "d5_changed_analysts": 0, "d5_new_analysts": 0, "d5_median_change": np.nan,
                  "d10_changed_analysts": 0, "d10_new_analysts": 0, "d10_median_change": np.nan}
        if target is not None:
            record.update(_event_stats(history, pd.Timestamp(row.day0_date),
                                       event_cutoffs[int(row.event_id)]))
        records.append(record)
        if int(row.event_id) and int(row.event_id) % 10_000 == 0:
            print(f"Summarized {int(row.event_id):,}/{len(events):,} events", flush=True)
    event_audit = pd.DataFrame(records)
    summaries = []
    n_events = len(event_audit)
    for horizon in ("d1", "d5", "d10"):
        changed_col = f"{horizon}_changed_analysts"
        new_col = f"{horizon}_new_analysts"
        change = event_audit[changed_col]
        summaries.append({
            "feature_freeze": horizon,
            "events": n_events,
            "events_with_target_quarter": int(event_audit.period_end.notna().sum()),
            "events_with_any_pre_event_forecast": int(event_audit.baseline_analysts.gt(0).sum()),
            "events_with_3plus_pre_event_analysts": int(event_audit.baseline_analysts.ge(3).sum()),
            "events_with_changed_existing_analyst": int(change.gt(0).sum()),
            "changed_existing_analysts_total": int(change.sum()),
            "events_with_new_post_event_analyst": int(event_audit[new_col].gt(0).sum()),
            "median_changed_analysts_per_event": float(change.median()),
            "median_revision_value_change_when_present": (
                float(event_audit.loc[change.gt(0), f"{horizon}_median_change"].median())
                if change.gt(0).any() else None),
            "revision_date_rule": "revdats strictly after event date and strictly before next-session-after-feature entry date",
        })
    summary = {
        "study": "top-SUE positive PEAD event IBES detail-history revision feasibility",
        "years": years,
        "selected_positive_events": n_events,
        "collision_audit": collision_audit,
        "raw_ibes_rows": int(pq.ParquetFile(raw_path).metadata.num_rows),
        "source_file": str(raw_path),
        "forecast_horizon_rule": "nearest future fpedats 45-180 calendar days after reported-period end",
        "measurement": "same-analyst change from latest pre-event forecast to latest strictly post-day0 forecast available before frozen entry",
        "revtims_used_for_order_within_analyst": True,
        "revtims_timezone_assumed": False,
        "entry_date_same_day_revisions_excluded": True,
        "limitations": [
            "This is a feature-coverage audit, not a return backtest or evidence of profitability.",
            "The extract is unadjusted detail EPS; analyst forecast and actual share bases need split-basis reconciliation.",
            "REVDATS and REVTIMS do not identify a timezone in this extract; same-date updates are excluded.",
            "The next fiscal-quarter horizon is inferred from fpedats rather than an explicit quarterly forecast-period indicator.",
            "New analyst coverage and changed forecasts are reported separately; reissues with unchanged values are not counted as revisions.",
        ],
        "horizon_summaries": summaries,
        "no_raw_estimate_rows_written": True,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "ibes_revision_feasibility.json"
    csv_path = output_dir / "ibes_revision_feasibility_summary.csv"
    json_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    pd.DataFrame(summaries).to_csv(csv_path, index=False)
    return json_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ibes-detail", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    json_path, csv_path = run(args.data_dir, args.ibes_detail, args.output_dir)
    print(f"Wrote aggregate audit: {json_path}")
    print(f"Wrote aggregate summary: {csv_path}")


if __name__ == "__main__":
    main()
