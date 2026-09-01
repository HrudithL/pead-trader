"""
Fama-French-style robustness check: characteristic (size x book-to-market) adjusted returns.

Why this version of "Fama-French adjustment" rather than a regression on official factor
returns: this session has no WRDS access and no verified source for the official Fama-French
factor time series, and per this project's own house rules (data/*/FINDINGS.md), nothing here
should rely on an unvetted external data pull. Instead this replicates the substance of the
Fama-French size/value control using data already validated inside this project:

  - Size: size_quintile, already computed in data/results/event_classification_features.parquet.
  - Book-to-market: built here from Compustat `ceq` (common/ordinary equity -- funda.parquet has
    no `txditc`, so BE = ceq exactly, a standard simplification when deferred taxes aren't
    available; firms with ceq <= 0 are dropped from the BM sort, the standard Fama-French
    convention since B/M is not meaningful for negative book equity) divided by the event's own
    market cap (already computed, in event_classification_features.parquet).
  - Point-in-time safety: for each event, only Compustat fiscal years whose datadate is at least
    130 calendar days (~4.3 months) before the announcement are eligible, and the most recent
    such fiscal year is used -- a deliberately conservative reporting lag (Compustat's own
    typical filing lag is 2-4 months; this project's earlier documents apply similar
    conservatism elsewhere) rather than the exact July-June convention of the official
    Fama-French portfolios, which needs a full continuous universe this project doesn't have
    already built. This is a disclosed simplification, not a claim of matching Fama-French's
    exact methodology.

Method: for each announcement quarter x size-quintile x BM-tercile cell, take the leave-one-out
mean of every OTHER event's raw market-adjusted return in that cell at each horizon -- this is
the "characteristic benchmark" return. Subtract it from each event's own market-adjusted return
to get a characteristic-adjusted return. Re-run the exact same decile sort and Fama-MacBeth
D10-D1 spread test from 02_decile_summary.py on this adjusted return instead. If the spread
survives, the drift is not simply compensation for carrying more size/value risk.

Output: data/ff_decile_horizon_stats.csv, data/ff_spread_horizon_stats.csv,
        data/ff_coverage_stats.csv
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR
from common.stats import fama_macbeth

DATA = DATA_DIR
RAW = DATA_DIR

ev = pd.read_parquet(DATA / "decile_events.parquet")
funda = pd.read_parquet(RAW / "raw_wrds/comp/funda.parquet")

HORIZONS = [1, 5, 10, 20, 40, 60]
RET_COLS = {h: f"ret_fwd_{h}d_mktadj" for h in HORIZONS}

# ---------- 1. Book equity from Compustat, point-in-time matched per event ----------
funda = funda[["gvkey", "datadate", "ceq"]].dropna(subset=["ceq"])
funda = funda[funda["ceq"] > 0].copy()  # standard convention: drop negative/zero book equity
funda["datadate"] = pd.to_datetime(funda["datadate"])
funda = funda.sort_values(["gvkey", "datadate"])

# primary gvkey candidate only (first of any comma-joined ambiguous set -- same convention
# already used elsewhere in this project for ambiguous identifiers, e.g. permno_ambiguous)
ev["gvkey_primary"] = ev["gvkey"].astype(str).str.split(",").str[0]

events_small = ev[["gvkey_primary", "anndats"]].reset_index().rename(columns={"index": "event_idx"})
events_small["anndats"] = pd.to_datetime(events_small["anndats"])
events_small["cutoff"] = events_small["anndats"] - pd.Timedelta(days=130)

merged = events_small.merge(
    funda.rename(columns={"gvkey": "gvkey_primary"}), on="gvkey_primary", how="left"
)
eligible = merged[merged["datadate"] <= merged["cutoff"]]
# most recent eligible fiscal year per event
best = eligible.sort_values("datadate").groupby("event_idx").tail(1)[["event_idx", "ceq", "datadate"]]
best = best.rename(columns={"ceq": "book_equity", "datadate": "be_fye"})

ev = ev.merge(best, left_index=True, right_on="event_idx", how="left").drop(columns=["event_idx"])
n_matched = ev["book_equity"].notna().sum()
print(f"book equity matched (>=130d lag): {n_matched:,} / {len(ev):,} ({n_matched/len(ev):.1%})")

# BM = book equity ($M) / market cap ($K -> $M)
ev["book_to_market"] = ev["book_equity"] / (ev["market_cap"] / 1000.0)
ev.loc[~np.isfinite(ev["book_to_market"]), "book_to_market"] = np.nan

# ---------- 2. BM tercile within announcement quarter (same convention as size_quintile) ----------
def tercile(s):
    return pd.qcut(s, 3, labels=[1, 2, 3], duplicates="drop")

ev["bm_tercile"] = ev.groupby("ann_quarter")["book_to_market"].transform(
    lambda s: tercile(s) if s.notna().sum() >= 15 else pd.Series(np.nan, index=s.index)
)

coverage = ev[["ann_quarter"]].copy()
coverage["has_bm"] = ev["bm_tercile"].notna()
coverage["has_size"] = ev["size_quintile"].notna()
coverage["has_both"] = coverage["has_bm"] & coverage["has_size"]
cov_summary = pd.DataFrame({
    "n_events": [len(ev)],
    "has_book_equity": [ev["book_equity"].notna().sum()],
    "has_bm_tercile": [coverage["has_bm"].sum()],
    "has_size_quintile": [coverage["has_size"].sum()],
    "has_both_size_and_bm": [coverage["has_both"].sum()],
})
cov_summary.to_csv(DATA / "ff_coverage_stats.csv", index=False)
print(cov_summary.to_string(index=False))

# ---------- 3. Leave-one-out characteristic-benchmark adjustment ----------
cell_cols = ["ann_quarter", "size_quintile", "bm_tercile"]
usable = ev.dropna(subset=cell_cols).copy()
print(f"events usable for characteristic adjustment: {len(usable):,} / {len(ev):,}")

for h in HORIZONS:
    col = RET_COLS[h]
    grp = usable.groupby(cell_cols)[col]
    cell_sum = grp.transform("sum")
    cell_n = grp.transform("count")
    # leave-one-out mean of the cell excluding the event's own return
    loo_mean = (cell_sum - usable[col].fillna(0)) / (cell_n - (usable[col].notna().astype(int)))
    usable[f"{col}_adj"] = usable[col] - loo_mean

usable.to_parquet(DATA / "decile_events_ff_adjusted.parquet", index=False)

# ---------- 4. Fama-MacBeth decile stats + D10-D1 spread on the adjusted return ----------
decile_rows = []
for h in HORIZONS:
    col = f"{RET_COLS[h]}_adj"
    for d in range(1, 11):
        sub = usable[usable["decile"] == d]
        mean, se, t, n, nq = fama_macbeth(sub, col)
        decile_rows.append(dict(horizon=h, decile=d, mean=mean, se=se, t_stat=t, n_events=n, n_quarters=nq))
ff_decile_stats = pd.DataFrame(decile_rows)
ff_decile_stats.to_csv(DATA / "ff_decile_horizon_stats.csv", index=False)
print(ff_decile_stats.pivot(index="decile", columns="horizon", values="mean").round(4))

spread_rows = []
for h in HORIZONS:
    col = f"{RET_COLS[h]}_adj"
    d1 = usable[usable["decile"] == 1][["ann_quarter", col]].dropna()
    d10 = usable[usable["decile"] == 10][["ann_quarter", col]].dropna()
    q1 = d1.groupby("ann_quarter")[col].mean()
    q10 = d10.groupby("ann_quarter")[col].mean()
    common = q1.index.intersection(q10.index)
    spread_q = q10.loc[common] - q1.loc[common]
    n_q = len(spread_q)
    mean = spread_q.mean()
    se = spread_q.std(ddof=1) / np.sqrt(n_q)
    t = mean / se if se > 0 else np.nan
    spread_rows.append(dict(horizon=h, mean_spread=mean, se=se, t_stat=t, n_quarters=n_q,
                             n_d1=len(d1), n_d10=len(d10)))
ff_spread_stats = pd.DataFrame(spread_rows)
ff_spread_stats.to_csv(DATA / "ff_spread_horizon_stats.csv", index=False)
print()
print(ff_spread_stats.round(4))
