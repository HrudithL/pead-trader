"""
Pull each matched event's underlying closing price at day0 from OptionMetrics secprd*.parquet
(the underlying-security daily price file -- 20-50MB/year, small enough to load whole, unlike
opprcd). Needed to express a risk-reversal's dollar P&L as a percentage of underlying notional:
a long call (delta ~+0.50) + short put (delta ~-0.50) has combined delta ~1.0, so by put-call
parity it behaves like a near-fully-financed synthetic long stock position -- normalizing its
P&L by the spot price makes it directly comparable to the equity strategies' notional-based
returns in PEAD_Strategy_Showcase.pdf.

Output: data/event_options/underlying_spot.parquet (secid, day0_date, spot_close)
"""
import pandas as pd
from pathlib import Path

OM_DIR = Path("D:/OptionMetrics/parquet")
OUT_DIR = Path("data/event_options")
YEARS = [y for y in range(1996, 2014) if y != 2011]

panel = pd.read_parquet(OUT_DIR / "option_event_panel.parquet", columns=["secid", "day0_date"])
panel["secid"] = panel["secid"].astype("int64")
panel["year"] = panel["day0_date"].dt.year
needed = panel[["secid", "day0_date", "year"]].drop_duplicates()
print(f"need spot price for {len(needed):,} (secid, day0_date) pairs")

chunks = []
for year, grp in needed.groupby("year"):
    f = OM_DIR / f"secprd{int(year)}.parquet"
    if not f.exists():
        print(f"{year}: no secprd file, skipping ({len(grp):,} events)")
        continue
    sec = pd.read_parquet(f, columns=["secid", "date", "close"])
    sec["secid"] = sec["secid"].astype("int64")
    sec["date"] = pd.to_datetime(sec["date"])
    matched = grp.rename(columns={"day0_date": "date"}).merge(
        sec, on=["secid", "date"], how="left")
    chunks.append(matched.rename(columns={"date": "day0_date", "close": "spot_close"}))
    hit = matched["close"].notna().mean()
    print(f"{year}: {len(grp):,} events -> {hit:.1%} matched a spot price")

spot = pd.concat(chunks, ignore_index=True)[["secid", "day0_date", "spot_close"]]
spot.to_parquet(OUT_DIR / "underlying_spot.parquet", index=False)
print(f"\nwrote {OUT_DIR / 'underlying_spot.parquet'} ({len(spot):,} rows, "
      f"{spot['spot_close'].notna().mean():.1%} matched overall)")
