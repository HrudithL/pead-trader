"""
Build a global OptionMetrics trading-day calendar (1996-2013, excluding 2011 which has no
opprcd file) from the underlying-security daily price files (secprd*.parquet -- small, fast
to scan). This calendar is used to compute forward trading-day offsets (day0+1, +5, +10, +20,
+40, +60) for the option event panel, the same way the existing equity pipeline uses the CRSP
calendar for ret_fwd_*d.

Output: data/metadata/om_trading_calendar.parquet (single column 'date', sorted, deduped)
"""
import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path

OM_DIR = Path("D:/OptionMetrics/parquet")
OUT = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/data/metadata/om_trading_calendar.parquet")

years = [y for y in range(1996, 2014) if y != 2011]
all_dates = set()
for y in years:
    f = OM_DIR / f"secprd{y}.parquet"
    tbl = pq.read_table(f, columns=["date"])
    dates = tbl.column("date").to_pandas()
    all_dates.update(dates.unique().tolist())
    print(f"{y}: {len(dates):,} rows, {dates.nunique()} unique dates, running total {len(all_dates)}")

cal = pd.DataFrame({"date": sorted(all_dates)})
cal["date"] = pd.to_datetime(cal["date"])
cal = cal.drop_duplicates().sort_values("date").reset_index(drop=True)
print(f"\nfinal calendar: {len(cal)} trading days, {cal['date'].min()} .. {cal['date'].max()}")
gaps = cal["date"].diff().dt.days
print("gap size distribution (days between consecutive calendar entries):")
print(gaps.value_counts().sort_index().tail(10))

OUT.parent.mkdir(parents=True, exist_ok=True)
cal.to_parquet(OUT, index=False)
print(f"wrote {OUT}")
