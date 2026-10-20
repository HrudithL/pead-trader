"""Alpha benchmark for every saved equity run: python scripts/alpha_benchmark.py
Writes results/alpha_benchmark.csv and .md."""
import json, sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pead.core.alpha import alpha_stats, strategy_returns

R = ROOT / "results"
mkt = pd.read_parquet(ROOT / "data/normalized_equity/market_benchmark.parquet").set_index("date")
mkt.index = pd.DatetimeIndex(mkt.index)
rows = []
for d in sorted(R.iterdir()):
    s = d / "summary.json"
    if not s.exists() or d.name.startswith(("opt_", "hedged")):
        continue
    meta = json.loads(s.read_text())
    if meta.get("asset_class") != "equity":
        continue
    summ = meta["summary"]
    daily = pd.read_csv(d / "daily.csv.gz", parse_dates=["date"])
    ic = 10_000_000.0
    ret = strategy_returns(daily, ic)
    a = alpha_stats(ret, mkt)
    a2 = alpha_stats(ret, mkt, size_factor=True)
    row = dict(name=d.name, status=meta["status"], ann_return=summ["annualized_return"], sharpe=summ["sharpe"], max_dd=summ["max_drawdown"],
               alpha=a["alpha"], alpha_t=a["alpha_t"], beta=a["beta"], info_ratio=a["info_ratio"], resid_vol=a["resid_vol"],
               alpha_size=a2["alpha"], alpha_size_t=a2["alpha_t"], size_loading=a2["size_loading"])
    for era, (y0, y1) in {"a_96_01": ("1996", "2001"), "a_02_07": ("2002", "2007"), "a_08_13": ("2008", "2013")}.items():
        row[era] = alpha_stats(ret.loc[y0:y1], mkt.loc[y0:y1])["alpha"]
    rows.append(row)
df = pd.DataFrame(rows).sort_values("alpha", ascending=False)
df.to_csv(R / "alpha_benchmark.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print(df.round(3).to_string(index=False))
