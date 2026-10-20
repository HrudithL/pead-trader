"""Per-strategy profiles: best/worst trade recipes, what the biggest winners and losers look like, consistency of losses.
Writes results/profiles_*.csv.   python scripts/strategy_profiles.py
(Run with PEAD_DATA_DIR / PEAD_RAW_EQUITY_DIR pointing at the corrected data vintage so the events match the corrected results.)"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pead.core.data import load_equity_universe
from pead.core.store import load_result
from pead.reporting.attribution import equity_positions_with_alpha
from pead.reporting.situations import _tag_market, _tag_other, _quarter_series, _tstat

R = ROOT / "results"
NAMES = ["extreme_picks", "graded_bets", "size_balanced", "conviction_tilt", "sector_balanced", "balanced_leverage",
         "calm_weighted", "calm_rank_tilt", "market_exposed"]
events = load_equity_universe().events
market = pd.read_parquet(ROOT / "data/normalized_equity/market_benchmark.parquet")

def prep(name):
    p = equity_positions_with_alpha(load_result(name), events)
    p = _tag_other(_tag_market(p, market))
    p = p[p["notional"].abs() > 0].copy()
    # size-matched benchmark: the average announcer of the SAME size group in the same quarter. (The default benchmark, the
    # average of all announcers, is biased for any cut that mixes side with company size: big stocks structurally lag it.)
    bm = events.dropna(subset=["hold_ret"]).groupby(["ann_quarter", "size_quintile"])["hold_ret"].mean()
    p["bm_size"] = bm.reindex(pd.MultiIndex.from_arrays([p["ann_quarter"], p["size_quintile"]])).to_numpy()
    p["excess"] = p["notional"] * (p["hold_ret"] - p["bm_size"])
    p["alpha_net"] = p["excess"] - p["cost"]
    p["liq3"] = pd.cut(p["liquidity_quintile"], [0, 2, 3, 5], labels=["thin", "mid-liquidity", "liquid"])
    p["hold2"] = np.where(p["holding_days"] > 60, "long hold (>60d)", "short hold (<=60d)")
    p["beta3"] = p["beta_group"].astype(str).str.replace(" stocks", "")
    p["recipe"] = p["leg"] + " | " + p["liq3"].astype(str) + " | " + p["beta3"] + " | " + p["hold2"]
    return p

def recipes(p):
    tot_a, tot_n = p["alpha_net"].sum(), p["notional"].abs().sum()
    rows = []
    for key, g in p.groupby("recipe", observed=True):
        qs = _quarter_series(g)
        eras = {e: x["alpha_net"].sum() for e, x in g.groupby("era", observed=True)}
        eras_net = {e: x["net_pnl"].sum() for e, x in g.groupby("era", observed=True)}
        n = g["notional"].abs().sum()
        rows.append(dict(recipe=key, positions=len(g), pct_dollars=n / tot_n, alpha_M=g["alpha_net"].sum() / 1e6,
                         pct_alpha=g["alpha_net"].sum() / tot_a, bp=1e4 * g["alpha_net"].sum() / n, t=_tstat(qs),
                         pct_q_pos=float((qs > 0).mean()), eras_neg=sum(1 for v in eras.values() if v < 0),
                         net_M=g["net_pnl"].sum() / 1e6, net_bp=1e4 * g["net_pnl"].sum() / n, eras_neg_net=sum(1 for v in eras_net.values() if v < 0), hit=float((g["alpha_net"] > 0).mean())))
    return pd.DataFrame(rows)

def trade_profile(p):
    """What do the best 1% / worst 1% of positions (by dollars of alpha) look like vs the average position?"""
    a = p["alpha_net"]; k = max(1, int(len(p) * 0.01))
    top, bot = p.nlargest(k, "alpha_net"), p.nsmallest(k, "alpha_net")
    def desc(x):
        return dict(n=len(x), pct_long=float((x["side"] > 0).mean()), avg_size_q=float(x["size_quintile"].mean()), avg_liq_q=float(x["liquidity_quintile"].mean()),
                    avg_beta=float(x["beta_252d"].mean()), avg_hold=float(x["holding_days"].mean()), avg_ivol_pct=float(x["ivol_252d_pct"].mean()),
                    avg_ret_on_notional=float((x["alpha_net"] / x["notional"].abs()).mean()), pct_decile_extreme=float(x["decile"].isin([1, 10]).mean()))
    out = {"top1%": desc(top), "bottom1%": desc(bot), "all": desc(p)}
    out["top1%"]["share_of_total_alpha"] = float(top["alpha_net"].sum() / a.sum())
    out["bottom1%"]["share_of_total_alpha"] = float(bot["alpha_net"].sum() / a.sum())
    out["all"]["win_rate"] = float((a > 0).mean()); out["all"]["median_bp"] = float(1e4 * (a / p["notional"].abs()).median())
    return pd.DataFrame(out).T

def timing(p):
    q = p.groupby("ann_quarter").agg(a=("alpha_net", "sum"), n=("notional", lambda v: v.abs().sum()))
    q["bp"] = 1e4 * q["a"] / q["n"]
    y = p.assign(year=p["day0_date"].dt.year).groupby("year").agg(a=("alpha_net", "sum"), n=("notional", lambda v: v.abs().sum()))
    y["bp"] = 1e4 * y["a"] / y["n"]
    neg = (q["a"] < 0).to_numpy(); run = best = 0
    for v in neg:
        run = run + 1 if v else 0; best = max(best, run)
    cost = p["cost"].sum(); gross = p["excess"].sum()
    sec = p.groupby(["ann_quarter", "ff12_sector"], observed=True)["notional"].apply(lambda v: v.abs().sum())
    sec = sec / sec.groupby(level=0).transform("sum")        # each FF12 sector's share of that announcement quarter's new dollars
    return dict(pct_quarters_pos=float((q["a"] > 0).mean()), worst_q=str(q["a"].idxmin()), worst_q_M=float(q["a"].min() / 1e6),
                longest_losing_streak=best, neg_years=",".join(str(i) for i in y.index[y["a"] < 0]),
                best10_share=float(q["a"].nlargest(10).sum() / q["a"].sum()), cost_pct_of_excess=float(cost / gross),
                long_alpha_M=float(p.loc[p.leg == "long", "alpha_net"].sum() / 1e6), short_alpha_M=float(p.loc[p.leg == "short", "alpha_net"].sum() / 1e6),
                long_bp=float(1e4 * p.loc[p.leg == "long", "alpha_net"].sum() / p.loc[p.leg == "long", "notional"].abs().sum()),
                short_bp=float(1e4 * p.loc[p.leg == "short", "alpha_net"].sum() / p.loc[p.leg == "short", "notional"].abs().sum()),
                max_sector_share_of_quarter=float(sec.max()), top_name_pct_alpha=float(p.groupby("permno")["alpha_net"].sum().nlargest(20).sum() / p["alpha_net"].sum()))

def beta_legs(p):
    """The high-beta-short question: alpha and real dollars of (high-beta shorts / low+mid-beta shorts / high-beta longs /
    low+mid-beta longs). money = percent of the book's dollars traded; real = actual P&L (net_pnl), alpha = size-matched."""
    tot_n = p["notional"].abs().sum()
    hi = p["beta3"] == "high-beta"
    groups = {"high-beta shorts": (p["leg"] == "short") & hi, "low/mid-beta shorts": (p["leg"] == "short") & ~hi,
              "high-beta longs": (p["leg"] == "long") & hi, "low/mid-beta longs": (p["leg"] == "long") & ~hi}
    rows = []
    for k, m in groups.items():
        g = p[m]; n = g["notional"].abs().sum()
        eras_a = [x["alpha_net"].sum() for _, x in g.groupby("era", observed=True)]
        eras_r = [x["net_pnl"].sum() for _, x in g.groupby("era", observed=True)]
        rows.append(dict(group=k, money=100 * n / tot_n, alpha_M=g["alpha_net"].sum() / 1e6, alpha_bp=1e4 * g["alpha_net"].sum() / n,
                         t=_tstat(_quarter_series(g)), eras_alpha_neg=sum(v < 0 for v in eras_a), real_M=g["net_pnl"].sum() / 1e6,
                         real_bp=1e4 * g["net_pnl"].sum() / n, eras_real_neg=sum(v < 0 for v in eras_r)))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    rec_all, tp_all, tm_all, bl_all = [], [], [], []
    for n in NAMES:
        p = prep(n)
        r = recipes(p); r.insert(0, "strategy", n); rec_all.append(r)
        t = trade_profile(p); t.insert(0, "strategy", n); tp_all.append(t.reset_index().rename(columns={"index": "group"}))
        tm_all.append(dict(strategy=n, **timing(p)))
        b = beta_legs(p); b.insert(0, "strategy", n); bl_all.append(b)
        print(n, "done", flush=True)
    pd.concat(rec_all).to_csv(R / "profiles_recipes.csv", index=False)
    pd.concat(tp_all).to_csv(R / "profiles_trades.csv", index=False)
    pd.DataFrame(tm_all).to_csv(R / "profiles_timing.csv", index=False)
    pd.concat(bl_all).to_csv(R / "profiles_beta_legs.csv", index=False)
