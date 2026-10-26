"""Cleaned stock PEAD drift and four-quarter SUE reversal diagnostics.

Writes only aggregate summaries from the event panel. Returns are market-adjusted
CRSP returns, not option P&L or an investable strategy portfolio.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from pead.research.options_expiry_grid import exclude_duplicate_event_dates

YEARS = [*range(1998, 2011), 2012, 2013]
HORIZONS = [1, 5, 10, 20, 40, 60]


def _load_clean_events(data_dir: Path, years: list[int]) -> tuple[pd.DataFrame, dict]:
    columns = ["secid", "permno", "day0_date", "anndats", "decile", "sue_analyst_r",
               "ret_fwd_1d_mktadj", "ret_fwd_5d_mktadj", "ret_fwd_10d_mktadj",
               "ret_fwd_20d_mktadj", "ret_fwd_40d_mktadj", "ret_fwd_60d_mktadj"]
    events = pd.read_parquet(data_dir / "event_options" / "decile_events_secid.parquet",
                             columns=columns)
    events["day0_date"] = pd.to_datetime(events.day0_date)
    events["anndats"] = pd.to_datetime(events.anndats)
    events = events[events.day0_date.dt.year.isin(years)].copy()
    return exclude_duplicate_event_dates(events)


def _hac_mean(series: pd.Series, maxlags: int = 4) -> dict:
    y = pd.to_numeric(series, errors="coerce").dropna()
    if len(y) < maxlags + 3:
        return {"quarters": int(len(y)), "mean": float(y.mean()) if len(y) else None,
                "hac_t": None, "hac_95_ci_low": None, "hac_95_ci_high": None}
    model = sm.OLS(y.to_numpy(), np.ones((len(y), 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags, "use_correction": True})
    mean, se = float(model.params[0]), float(model.bse[0])
    return {"quarters": int(len(y)), "mean": mean, "hac_t": float(model.tvalues[0]),
            "hac_95_ci_low": mean - 1.96 * se, "hac_95_ci_high": mean + 1.96 * se}


def _drift_tables(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ev = events[events.decile.isin([1, 2, 9, 10])].copy()
    ev["sue_side"] = np.where(ev.decile.ge(9), "positive", "negative")
    ev["event_quarter"] = ev.anndats.dt.to_period("Q").astype(str)
    for h in HORIZONS:
        ev[f"cum_{h}"] = pd.to_numeric(ev[f"ret_fwd_{h}d_mktadj"], errors="coerce")
    interval_rows, cumulative_rows = [], []
    prev_h = 0
    for h in HORIZONS:
        if prev_h == 0:
            ev[f"interval_{prev_h}_{h}"] = ev[f"cum_{h}"]
        else:
            ev[f"interval_{prev_h}_{h}"] = (
                (1.0 + ev[f"cum_{h}"]) / (1.0 + ev[f"cum_{prev_h}"]) - 1.0)
        column = f"interval_{prev_h}_{h}"
        q = ev.groupby(["event_quarter", "sue_side"])[column].mean().unstack()
        q = q.dropna(subset=["positive", "negative"])
        q["high_minus_low"] = q.positive - q.negative
        pooled = ev.groupby("sue_side")[column].agg(["mean", "median", "count"])
        hac = _hac_mean(q.high_minus_low)
        interval_rows.append({
            "window_sessions": f"{prev_h}-{h}",
            "positive_sue_mean": float(pooled.loc["positive", "mean"]),
            "negative_sue_mean": float(pooled.loc["negative", "mean"]),
            "positive_minus_negative_mean": float(
                ev.loc[ev.sue_side.eq("positive"), column].mean()
                - ev.loc[ev.sue_side.eq("negative"), column].mean()),
            "positive_events": int(pooled.loc["positive", "count"]),
            "negative_events": int(pooled.loc["negative", "count"]),
            "quarterly_spread_hac_mean": hac["mean"],
            "quarterly_spread_hac_t": hac["hac_t"],
            "quarterly_spread_hac_ci_low": hac["hac_95_ci_low"],
            "quarterly_spread_hac_ci_high": hac["hac_95_ci_high"],
            "quarters": hac["quarters"],
        })
        prev_h = h
    for h in HORIZONS:
        col = f"cum_{h}"
        pooled = ev.groupby("sue_side")[col].agg(["mean", "median", "count"])
        q = ev.groupby(["event_quarter", "sue_side"])[col].mean().unstack()
        q = q.dropna(subset=["positive", "negative"])
        spread = _hac_mean(q.positive - q.negative)
        cumulative_rows.append({
            "horizon_sessions": h,
            "positive_sue_mean_cumulative_mktadj": float(pooled.loc["positive", "mean"]),
            "negative_sue_mean_cumulative_mktadj": float(pooled.loc["negative", "mean"]),
            "positive_minus_negative_mean": float(
                ev.loc[ev.sue_side.eq("positive"), col].mean()
                - ev.loc[ev.sue_side.eq("negative"), col].mean()),
            "positive_events": int(pooled.loc["positive", "count"]),
            "negative_events": int(pooled.loc["negative", "count"]),
            "quarterly_spread_hac_mean": spread["mean"],
            "quarterly_spread_hac_t": spread["hac_t"],
            "quarterly_spread_hac_ci_low": spread["hac_95_ci_low"],
            "quarterly_spread_hac_ci_high": spread["hac_95_ci_high"],
            "quarters": spread["quarters"],
        })
    return pd.DataFrame(cumulative_rows), pd.DataFrame(interval_rows)


def _sue_lag4(events: pd.DataFrame) -> dict:
    d = events.dropna(subset=["permno", "anndats", "sue_analyst_r", "decile"]).copy()
    d = d.sort_values(["permno", "anndats", "day0_date"], kind="stable")
    pairs = []
    for permno, group in d.groupby("permno", sort=False):
        dates = group.anndats.to_numpy(dtype="datetime64[ns]")
        sue = pd.to_numeric(group.sue_analyst_r, errors="coerce").to_numpy(float)
        decile = pd.to_numeric(group.decile, errors="coerce").to_numpy(float)
        for i, date in enumerate(dates):
            lower = np.searchsorted(dates[:i], date - np.timedelta64(400, "D"), side="left")
            upper = np.searchsorted(dates[:i], date - np.timedelta64(340, "D"), side="right")
            if upper > lower:
                j = upper - 1  # Closest earlier event within the inclusive lag window.
                pairs.append((int(permno), sue[j], sue[i], decile[j]))
    p = pd.DataFrame(pairs, columns=["permno", "sue_lag4", "sue_now", "lag_decile"])
    if p.empty:
        return {"pairs": 0, "correlation_lag4": None, "mean_current_sue_by_lag_decile": []}
    corr = p.sue_lag4.corr(p.sue_now)
    by_decile = (p.groupby("lag_decile").sue_now.agg(["mean", "count"]).reset_index()
                 .rename(columns={"mean": "mean_current_sue", "count": "pairs"}))
    values = by_decile.to_dict(orient="records")
    values = [{"lag_decile": int(x["lag_decile"]),
               "mean_current_sue": float(x["mean_current_sue"]),
               "pairs": int(x["pairs"])} for x in values]
    return {"pairs": int(len(p)), "correlation_lag4": float(corr),
            "mean_current_sue_by_lag_decile": values,
            "interpretation": "positive correlation indicates persistence; negative indicates reversal",
            "note": "Descriptive same-firm SUE association; not a return signal or causal test."}


def run(data_dir: Path, output_dir: Path, years: list[int] = YEARS) -> tuple[Path, Path, Path]:
    events, collision_audit = _load_clean_events(data_dir, years)
    cumulative, intervals = _drift_tables(events)
    summary = {
        "study": "cleaned SUE drift, post-drift giveback, and lag-4 SUE association",
        "years": years,
        "event_rows_after_collision_filter": len(events),
        "duplicate_policy": collision_audit,
        "return_definition": "CRSP market-adjusted cumulative returns from sessions +1 through +h after day0; excludes day0 return",
        "groups": "positive SUE deciles 9-10 versus negative SUE deciles 1-2",
        "inference": "quarterly equal-weight high-minus-low return spread; intercept HAC standard errors with 4 quarterly lags",
        "lag4_sue": _sue_lag4(events),
        "limitations": [
            "Stock-event diagnostics are not option portfolio performance or proof of investable alpha.",
            "Quarter-level HAC uncertainty does not correct selection across previously inspected strategy variants.",
            "The historical source ends in 2013 and has a missing OptionMetrics year (2011) and partial 2013 option coverage.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "pead_drift_reversal_audit.json"
    cumulative_path = output_dir / "pead_drift_reversal_cumulative.csv"
    interval_path = output_dir / "pead_drift_reversal_intervals.csv"
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    cumulative.to_csv(cumulative_path, index=False)
    intervals.to_csv(interval_path, index=False)
    return summary_path, cumulative_path, interval_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    outputs = run(args.data_dir, args.output_dir)
    for path in outputs:
        print(f"Wrote aggregate audit: {path}")


if __name__ == "__main__":
    main()
