"""Generate the OPT-01 spread / liquidity report from the OptionMetrics extract (read-only).

    python scripts/option_spread_report.py [--deciles 10] [--out results/option_spread_report]

Reads day-0 option chains from OPTIONMETRICS_DIR (default D:/OptionMetrics/parquet) for the events in
data/event_options/decile_events_secid_all.parquet and writes CSV + a Markdown summary. Nothing under data/ is modified.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import DATA_DIR, OPTIONMETRICS_DIR, RUNS_DIR  # noqa: E402
from pead.reporting.option_spreads import pick_contracts, spread_report  # noqa: E402

COLS = ["secid", "date", "exdate", "cp_flag", "best_bid", "best_offer", "delta", "open_interest", "optionid"]


def _md(df):
    cols = list(df.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deciles", type=int, nargs="*", default=[10])
    ap.add_argument("--out", default=str(RUNS_DIR / "option_spread_report"))
    a = ap.parse_args()
    base = DATA_DIR / "event_options"
    ev = pd.read_parquet(base / "decile_events_secid_all.parquet", columns=["secid", "day0_date", "decile", "ret_fwd_60d_mktadj",
                                                                              "om_coverage_eligible"])
    ev = ev[ev["om_coverage_eligible"] & ev["secid"].notna() & ev["decile"].isin(a.deciles)].copy()
    ev["secid"] = ev["secid"].astype(float)
    spot = pd.concat([pd.read_parquet(p) for p in sorted(base.glob("underlying_price_*.parquet"))], ignore_index=True)
    spot["secid"] = spot["secid"].astype(float)
    spot["date"] = pd.to_datetime(spot["day0_date"]).dt.date
    spot = spot[["secid", "date", "underlying_price"]].drop_duplicates(["secid", "date"])
    ev["date"] = ev["day0_date"].dt.date
    picks_all, n_events = [], {}
    for year in sorted(ev["day0_date"].dt.year.unique()):
        f = OPTIONMETRICS_DIR / f"opprcd{year}.parquet"
        if not f.exists():
            print(f"{year}: no option file, skipped")
            continue
        e = ev[ev["day0_date"].dt.year == year]
        keys = set(zip(e["secid"], e["date"]))
        chain = ds.dataset(f).to_table(columns=COLS, filter=ds.field("secid").isin(sorted(e["secid"].unique())) &
                                       ds.field("date").isin(sorted(e["date"].unique()))).to_pandas()
        chain = chain[[k in keys for k in zip(chain["secid"], chain["date"])]]
        p = pick_contracts(chain, spot)
        p["year"] = year
        picks_all.append(p)
        n_events[year] = len(e)
        print(f"{year}: {len(e)} events, {len(chain):,} chain rows, {len(p)} picks")
    picks = pd.concat(picks_all, ignore_index=True)
    total = sum(n_events.values())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    overall = spread_report(picks, total)
    by_year = pd.concat([spread_report(g, n_events[y]).assign(year=y) for y, g in picks.groupby("year")], ignore_index=True)
    overall.to_csv(out / "spread_by_bucket.csv", index=False)
    by_year.to_csv(out / "spread_by_bucket_year.csv", index=False)
    edge = ev["ret_fwd_60d_mktadj"].mean()
    md = [f"# Option spread report (day-0 chains, deciles {a.deciles}, {total} events)", "",
          f"Mean 60-session market-adjusted stock return of these events: {edge:.4f}", "",
          _md(overall.round(4)), ""]
    (out / "README.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
