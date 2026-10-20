"""A few facts quoted in the hand-written part of docs/SITUATIONS.md that are not columns of results/situations_*.csv
(whole-book quarter statistics, overlaps between labels). Writes results/situations_facts.json for the reference book.
python scripts/situations_facts.py   (run with PEAD_DATA_DIR / PEAD_RAW_EQUITY_DIR at the corrected data vintage)"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pead.core.data import load_equity_universe
from pead.core.store import load_result
from pead.reporting.attribution import equity_positions_with_alpha
from pead.reporting.situations import _tag_market, _tag_other

NAME = "balanced_leverage"
events = load_equity_universe().events
market = pd.read_parquet(ROOT / "data/normalized_equity/market_benchmark.parquet")
p = _tag_other(_tag_market(equity_positions_with_alpha(load_result(NAME), events), market))
p = p[p["notional"].abs() > 0]
q = p.groupby("ann_quarter", observed=True).agg(a=("alpha_net", "sum"), n=("notional", lambda v: v.abs().sum()), mk=("mkt_ret_hold", "mean"))
q["bp"] = 1e4 * q["a"] / q["n"]
worst = q["a"].nsmallest(6)
q1 = p[p["season"].str.startswith("Q1") & (p["ann_quarter"].dt.year != 2009)]
q1_all = p[p["season"].str.startswith("Q1")]
facts = dict(
    strategy=NAME, quarters=int(len(q)), pct_quarters_positive=float((q["a"] > 0).mean()),
    best10_share_of_alpha=float(q["a"].nlargest(10).sum() / q["a"].sum()),
    worst_quarter=str(worst.index[0]), worst_quarter_M=float(worst.iloc[0] / 1e6),
    next_five_worst_M=float(worst.iloc[1:].sum() / 1e6),
    q1_reports_bp=float(1e4 * q1_all["alpha_net"].sum() / q1_all["notional"].abs().sum()),
    q1_reports_bp_ex2009=float(1e4 * q1["alpha_net"].sum() / q1["notional"].abs().sum()),
    corr_size_liquidity=float(p["size_quintile"].corr(p["liquidity_quintile"])),
    corr_vol_beta=float(p["ivol_252d_pct"].corr(p["beta_252d"].rank(pct=True))),
    pct_jumpy_high_beta=float(((p["vol_group"] == "jumpy stocks") & (p["beta_group"] == "high-beta stocks")).sum() / (p["vol_group"] == "jumpy stocks").sum()),
    pct_calm_high_beta=float(((p["vol_group"] == "calm stocks") & (p["beta_group"] == "high-beta stocks")).sum() / (p["vol_group"] == "calm stocks").sum()),
    corr_quarter_alpha_market=float(q["bp"].corr(q["mk"])),
    notes="corr_quarter_alpha_market: quarterly alpha bp per $ vs the average market return over that quarter's holding windows")
(ROOT / "results" / "situations_facts.json").write_text(json.dumps(facts, indent=1))
print(json.dumps(facts, indent=1))
