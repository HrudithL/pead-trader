"""How much does the choice of benchmark matter? For each core equity book, alpha per $ (bp) for the long and short legs, each
leg within each company-size quintile, and a few one-dimensional cuts, under three yardsticks:
  real        actual net P&L per $ traded (market-relative drift included)
  size_matched  excess over the average announcer of the same size group and quarter (the benchmark used in docs/STRATEGY_PROFILES.md)
  all_announcers  excess over the average of ALL announcers that quarter (the benchmark of docs/SITUATIONS.md and the book reports)
Writes results/profiles_benchmark_compare.csv.   python scripts/benchmark_compare.py
Run with PEAD_DATA_DIR / PEAD_RAW_EQUITY_DIR pointing at the corrected data vintage (see strategy_profiles.py)."""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy_profiles import prep, events, NAMES, R

qm = events.groupby("ann_quarter")["hold_ret"].mean()
rows = []
for n in NAMES:
    p = prep(n)
    p["alpha_all"] = p["notional"] * (p["hold_ret"] - p["ann_quarter"].map(qm)) - p["cost"]
    tot = p["notional"].abs().sum()
    cuts = {"all": p.index == p.index, "long leg": p["leg"] == "long", "short leg": p["leg"] == "short",
            "long, liquid": (p["leg"] == "long") & (p["liq3"] == "liquid"), "short, liquid": (p["leg"] == "short") & (p["liq3"] == "liquid"),
            "thin": p["liq3"] == "thin", "low-beta": p["beta3"] == "low-beta", "smallest size quintile": p["size_quintile"] == 1,
            "largest size quintile": p["size_quintile"] == 5}
    for s in range(1, 6):
        cuts[f"size {s}"] = p["size_quintile"] == s
        for leg in ("long", "short"):
            cuts[f"size {s} {leg}"] = (p["size_quintile"] == s) & (p["leg"] == leg)
    for k, m in cuts.items():
        g = p[m]; d = g["notional"].abs().sum()
        rows.append(dict(strategy=n, cut=k, money_pct=100 * d / tot, real_bp=1e4 * g["net_pnl"].sum() / d,
                         size_matched_bp=1e4 * g["alpha_net"].sum() / d, all_announcers_bp=1e4 * g["alpha_all"].sum() / d,
                         size_matched_M=g["alpha_net"].sum() / 1e6, all_announcers_M=g["alpha_all"].sum() / 1e6))
out = pd.DataFrame(rows); out.to_csv(R / "profiles_benchmark_compare.csv", index=False)
print(out[out.strategy == "balanced_leverage"].round(1).to_string())
