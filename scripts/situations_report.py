"""Situation analysis for every saved equity run -> results/<name>/situations.csv, results/situations_all.csv,
results/situations_contrasts.csv.   python scripts/situations_report.py [name ...]"""
import json, sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pead.core.data import load_equity_universe
from pead.reporting.situations import analyze
from pead.core.store import load_result

R = ROOT / "results"
events = load_equity_universe().events
market = pd.read_parquet(ROOT / "data/normalized_equity/market_benchmark.parquet")
names = sys.argv[1:] or sorted(d.name for d in R.iterdir() if (d / "positions.parquet").exists()
                               and json.loads((d / "summary.json").read_text())["asset_class"] == "equity")
allrows, allc, books, drv = [], [], [], []
for n in names:
    res = load_result(n)
    a = analyze(res, events, market)
    s = a["slices"]; s.insert(0, "strategy", n); s.to_csv(R / n / "situations.csv", index=False); allrows.append(s)
    c = a["contrasts"].copy(); c.insert(0, "strategy", n); allc.append(c)
    d_ = a["drivers"].copy(); d_.insert(0, "strategy", n); drv.append(d_)
    books.append(dict(strategy=n, **a["book"]))
    print(n, a["book"])
pd.concat(allrows).to_csv(R / "situations_all.csv", index=False)
pd.concat(allc).to_csv(R / "situations_contrasts.csv", index=False)
pd.concat(drv).to_csv(R / "situations_drivers.csv", index=False)
pd.DataFrame(books).to_csv(R / "situations_books.csv", index=False)
