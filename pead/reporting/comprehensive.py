"""Comprehensive cross-strategy report: how the strategies relate to each other and what to combine.

Reads the saved runs under results/ (run `python -m pead run all` first) and writes
results/comprehensive/report.md|pdf. Sections: league table, risk/return map, lineage, era decay,
long-vs-short attribution across the equity books, the options book's concentration and execution
sensitivity, the daily-return correlation matrix, and the equity + options combination study.
"""
import numpy as np
import pandas as pd

from pead.common.paths import RUNS_DIR
from pead.core.metrics import yearly_table
from pead.core.store import load_daily, load_result, load_summaries
from pead.portfolio import combine as P
from pead.reporting import attribution as A
from pead.reporting import charts as C
from pead.reporting.doc import Doc

REPRESENTATIVE = ["extreme_decile", "rank_weighted", "s4_tilted_size_neutral", "s5_sector_neutral", "s6_neutral_levered",
                  "s7_directional", "s9b_inverse_vol", "s9a_vol_cell_filter", "s10_vol_rank_tilt", "s6_beta_overlay",
                  "opt_kelly_cap20", "opt_kelly_cap30", "opt_kelly_cap20_legacy", "hedged_blend"]
TABLE_COLS = ["name", "family", "status", "annualized_return", "annualized_vol", "sharpe", "max_drawdown", "calmar",
              "sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013"]


def _league(df):
    d = df.sort_values(["asset_class", "sharpe"], ascending=[True, False])
    return d[[c for c in TABLE_COLS if c in d.columns]].set_index("name")


def _lineage(df):
    s = df.set_index("name")
    rows = []
    for n, r in s.iterrows():
        par = r.get("parent")
        if isinstance(par, str) and par in s.index:
            p = s.loc[par]
            rows.append(dict(strategy=n, parent=par, d_return=r["annualized_return"] - p["annualized_return"],
                             d_vol=r["annualized_vol"] - p["annualized_vol"], d_sharpe=r["sharpe"] - p["sharpe"],
                             d_maxdd=r["max_drawdown"] - p["max_drawdown"]))
    return pd.DataFrame(rows)


def _returns_matrix(names):
    cols = {}
    for n in names:
        daily, ac = load_daily(n)
        cols[n] = daily.set_index("date")["daily_return"]
    eq = [n for n in names if load_daily(n)[1] == "equity"]
    idx = cols[eq[0]].index
    return pd.DataFrame({n: s.reindex(idx) for n, s in cols.items()}).fillna(0.0)


def build_comprehensive_report(names=None, pdf=True):
    df = load_summaries()
    if names:
        df = df[df["name"].isin(names)]
    out = RUNS_DIR / "comprehensive"
    fig = out / "figures"
    fig.mkdir(parents=True, exist_ok=True)
    doc = Doc("PEAD strategy suite -- comprehensive report",
              f"{len(df)} strategies, 1996-2013, $10M start, NAV-percentage Sharpe")
    keep = [n for n in REPRESENTATIVE if n in set(df["name"])]
    R = _returns_matrix(keep)
    mk = P.market_returns(R.index)

    # ---- 1. league table --------------------------------------------------------------------------
    doc.h2("1. League table")
    doc.p("Every strategy that has been run, by asset class and Sharpe. `validated` strategies are candidates for use, "
          "`research` are explorations/variants, `reference` are kept as documented counter-examples.")
    doc.table(_league(df), fmt={"sharpe": "{:.2f}", "calmar": "{:.2f}", "sharpe_1996_2001": "{:.2f}",
                                "sharpe_2002_2007": "{:.2f}", "sharpe_2008_2013": "{:.2f}"})
    mkt_row = dict(name="market (CRSP VW)", annualized_return=(1 + mk).prod() ** (252 / len(mk)) - 1,
                   annualized_vol=mk.std() * np.sqrt(252), asset_class="benchmark")
    pts = pd.concat([df[["name", "asset_class", "annualized_return", "annualized_vol"]], pd.DataFrame([mkt_row])])
    doc.image(C.frontier_scatter(pts, fig / "frontier.png", "Return vs. volatility"),
              "Each dot is one backtest. Options books and the hedged blend sit high-left (high return per unit of risk).")

    # ---- 2. lineage -----------------------------------------------------------------------------
    doc.h2("2. What each iteration changed")
    lin = _lineage(df)
    doc.table(lin, index=False, fmt={"d_return": "{:+.2%}", "d_vol": "{:+.2%}", "d_sharpe": "{:+.2f}", "d_maxdd": "{:+.1%}"})
    doc.p("`d_*` columns are the change from the parent strategy. Reading down: neutralizing size and sector (S4->S5) and "
          "inverse-vol sizing (S6->S9b) buy Sharpe; raising the leverage cap buys return; the aggressive variants buy "
          "return at the cost of Sharpe because the liquidity cap starts to bind.")

    # ---- 3. era decay ---------------------------------------------------------------------------
    doc.h2("3. Stability across eras")
    era = df.set_index("name")[["sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013"]]
    era = era.loc[[n for n in keep if n in era.index]]
    doc.table(era, fmt={c: "{:.2f}" for c in era.columns})
    yrs = {n: yearly_table(load_daily(n)[0], 1e7).set_index("year")["ret"] for n in
           [k for k in ("s9b_inverse_vol", "opt_kelly_cap20", "hedged_blend") if k in keep]}
    ymk = mk.groupby(mk.index.year).apply(lambda x: (1 + x).prod() - 1)
    ytab = pd.DataFrame(yrs).assign(market=ymk).loc[1996:2013]
    doc.table(ytab, fmt={c: "{:.1%}" for c in ytab.columns})
    doc.image(C.bar_chart(ytab, fig / "years_sleeves.png", "Calendar-year return by sleeve", list(ytab.columns),
                          ylabel="return", rotation=60, figsize=(10, 3.6)),
              "Equity alpha is front-loaded (1996-2003); the options book is back-loaded (2003-2013) and lost in 2008 when equity gained.")
    doc.p("**The equity drift has decayed**: Strategy 9b's return fell from ~14% a year in 1996-99 to ~2-5% in 2011-13, the "
          "usual signature of PEAD being arbitraged. The options book shows the opposite shape in this sample, which is part of "
          "why the two combine well -- and part of why its recent strength should not be extrapolated without a skeptical look.")

    # ---- 4. long vs short across equity books -------------------------------------------------------
    doc.h2("4. Equity books: long leg vs. short leg")
    rows = []
    for n in [k for k in ("extreme_decile", "rank_weighted", "s4_tilted_size_neutral", "s5_sector_neutral",
                          "s6_neutral_levered", "s9b_inverse_vol") if k in set(df["name"])]:
        ls = A.leg_summary(load_result(n))
        rows.append(dict(strategy=n, long_pnl_M=ls["long"]["pnl_M"], short_pnl_M=ls["short"]["pnl_M"],
                         long_excess_M=ls["long"]["excess_M"], short_excess_M=ls["short"]["excess_M"],
                         long_bp_per_dollar=ls["long"]["gross_bp_per_dollar"], short_bp_per_dollar=ls["short"]["gross_bp_per_dollar"],
                         corr_long_short=ls["corr_long_short_daily_pnl"]))
    doc.table(pd.DataFrame(rows), index=False, fmt={c: "{:,.1f}" for c in ["long_pnl_M", "short_pnl_M", "long_excess_M", "short_excess_M",
                                                                           "long_bp_per_dollar", "short_bp_per_dollar"]} | {"corr_long_short": "{:.2f}"})
    doc.bullets([
        "**In every equity strategy the short leg loses money in absolute terms** while the long leg earns several times the total. "
        "The headline is real but misleading: returns are measured against the cap-weighted market, and the average event stock beats that "
        "benchmark (small-cap tilt plus the earnings-announcement premium), flattering the long leg and penalizing the short leg by the "
        "same amount.",
        "Stripped of that common component, **the surprise signal earns the same on both legs** (columns `*_excess_M`): the shorts are "
        "pulling their weight, they are just paid in relative performance, not in absolute declines.",
        "The legs' daily P&L are ~-0.8 correlated: the short leg is a hedge. Dropping it for a long-only book would multiply the volatility "
        "and add market-relative factor exposure without adding alpha.",
        "The backtest charges no stock-borrow fee. The short leg is small-cap-heavy where borrow is expensive; each 1% a year of borrow costs "
        "~0.6pp of Strategy 6's return (see the per-strategy report's borrow table).",
        "Alpha per dollar traded is ~7x higher in the smallest size quintile (~11x in the least liquid quintile) than in the largest, and the "
        ">90-day holding cells earn ~8x the alpha per dollar of the <=40-day cells. The book's capacity is therefore bounded by small illiquid names."])

    # ---- 5. options -----------------------------------------------------------------------------
    if "opt_kelly_cap20" in set(df["name"]):
        doc.h2("5. Options book: what the return is made of, and how much of it is real")
        cur = df.set_index("name")
        if "opt_kelly_cap20_legacy" in cur.index and "opt_kelly_cap20_conservative" in cur.index:
            doc.p("**The originally reported options result does not survive a careful look at the quotes.** The original backtest marked "
                  "a missing forward quote at the previous live quote. 22% of picks have no quote at 60 days and 93% of those are zero-bid "
                  "options -- dead contracts nobody will buy -- which that convention never marked down. Corrected: "
                  f"{cur.loc['opt_kelly_cap20_legacy', 'annualized_return']:.2%} / Sharpe {cur.loc['opt_kelly_cap20_legacy', 'sharpe']:.2f} as reported "
                  f"-> {cur.loc['opt_kelly_cap20', 'annualized_return']:.2%} / {cur.loc['opt_kelly_cap20', 'sharpe']:.2f} "
                  "(dead quotes at half-ask, plus a $0.05 minimum tick cost) -> "
                  f"{cur.loc['opt_kelly_cap20_conservative', 'annualized_return']:.2%} / {cur.loc['opt_kelly_cap20_conservative', 'sharpe']:.2f} "
                  "if dead options are worth nothing. A real, smaller edge remains.")
        att = A.options_attribution(load_result("opt_kelly_cap20"))
        c = att["concentration"]
        doc.bullets([
            f"{c['pct_calls']:.1%} of funded trades are calls, producing {c['call_share_of_gross']:.1%} of gross P&L -- across ALL deciles, "
            "including the worst surprises. The Kelly selector finds cheap OTM calls better than puts even on bad news.",
            f"Win rate {c['hit_rate']:.0%}, median trade {c['median_roi']:.0%}, mean {c['mean_roi']:.0%}: a convex pay-off. The top 1% of trades "
            f"are {c['top1pct_share_of_gross']:.0%} of gross P&L.",
            "What survives the corrected marks is concentrated in calls struck at or just out of the money (1.0-1.2x spot) on low-volatility stocks. "
            "Deep in-the-money calls absorb about half the premium budget and earn ~5% on it; high-volatility stocks earn ~7%.",
            "Return on premium does not rise with SUE decile, so the edge is not the directional drift alone: it is mostly cheap post-earnings "
            "convexity on calm stocks, in a sample that was net bullish and whose profit is dominated by two years (2003 and 2013)."])
        doc.table(att["vol"][["trades", "premium_M", "gross_M", "net_M", "share_of_net", "roi_on_premium"]],
                  fmt={"share_of_net": "{:.1%}", "roi_on_premium": "{:.1%}"})
        doc.h3("Execution and marking sensitivity")
        from pead.strategies import registry
        from pead.strategies.base import RunContext
        doc.table(A.options_execution_sensitivity(registry.get("opt_kelly_cap20"), RunContext()),
                  fmt={"annualized_return": "{:.2%}", "annualized_vol": "{:.2%}", "max_drawdown": "{:.1%}"}, index=False)
        doc.p("Treat the corrected `opt_kelly_cap20` / `opt_kelly_cap30` as the options sleeve and the `_legacy` strategies as the "
              "historical headline only. Everything the hedged blend says about options is built on the corrected numbers.")

    # ---- 6. correlations -----------------------------------------------------------------------
    doc.h2("6. How the strategies move together")
    corr = R.assign(market=mk).corr()
    doc.image(C.heatmap(corr, fig / "corr.png", "Daily-return correlation"), "Equity books cluster together; options are independent of them.")
    doc.bullets([
        "All market-neutral equity variants are 0.7-0.99 correlated -- they are one bet expressed several ways, so combining them adds nothing.",
        f"Equity vs. options correlation: {corr.loc['s9b_inverse_vol', 'opt_kelly_cap20']:.2f}. The options book's correlation to the market is "
        f"{corr.loc['market', 'opt_kelly_cap20']:.2f}; the market-neutral equity books' is ~0.",
        "`s6_beta_overlay` is 0.94 correlated with the market by construction; it answers 'beat the S&P', not 'diversify'."])

    # ---- 7. combination ---------------------------------------------------------------------------
    _combination_section(doc, fig, R, mk, keep)

    # ---- 8. recommendations ---------------------------------------------------------------------
    doc.h2("8. Conclusions and next steps")
    doc.bullets([
        "Use `s9b_inverse_vol` as the equity alpha sleeve (validated, no fitted parameters, ahead of Strategy 6 in every era; `s10_vol_rank_tilt` is the stronger research candidate) and `opt_kelly_cap20` "
        "(or the cap30 variant) as the options sleeve -- a real but modest edge once dead quotes are marked down -- and blend by equal "
        "risk (`hedged_blend`).",
        "Do not add more market-neutral equity variants -- they are one bet. New equity ideas must change the signal or the holding rule, not "
        "the sizing.",
        "Options: the quote convention was the biggest error found in this project. Next: backtest with real bid/ask on entry and exit (the data "
        "has them), restrict to the contract region that survives (0.97-1.2x moneyness, low vol, premium >= $0.25), and test on a holdout -- "
        "2003 and 2013 carry most of the profit.",
        "The measured weak spots to attack on the equity side: the era decay (alpha per dollar fell ~4x), the <=40-day holding cells that "
        "earn little after costs, and the mid deciles (D7, D9) that earn almost no excess per dollar."])
    return doc.save(out, "report", pdf=pdf)


def _combination_section(doc, fig, R, mk, keep):
    doc.h2("7. Combining an equity book with an options book")
    e = "s9b_inverse_vol"
    o = "opt_kelly_cap20"
    if e not in R or o not in R:
        doc.p("Run `s9b_inverse_vol` and `opt_kelly_cap20` first.")
        return
    RR = R[[e, o]]
    st = {k: P.stats(RR[k]) for k in RR}
    # capital-split frontier
    ws = np.round(np.arange(0, 1.0001, 0.05), 2)
    fr = pd.DataFrame([dict(w_equity=w, **P.stats(P.blend(RR, {e: w, o: 1 - w}))) for w in ws]).set_index("w_equity")
    doc.h3("Capital split: fraction of NAV in each sleeve (weights sum to 1)")
    doc.image(C.bar_chart(fr.iloc[::2][["sharpe"]], fig / "frontier_sharpe.png", "Sharpe by equity share of capital", ["sharpe"],
                          ylabel="Sharpe"), "Sharpe peaks around 15-25% equity: diversification, then the better sleeve dominates.")
    methods = []
    for m in ("equal", "inverse_vol", "max_sharpe"):
        w = P.METHODS[m](RR)
        methods.append(dict(method=m + (" (in-sample)" if m == "max_sharpe" else ""), w_equity=w[0], w_options=w[1],
                            **{k: v for k, v in P.stats(P.blend(RR, dict(zip(RR.columns, w)))).items()
                               if k in ("annualized_return", "annualized_vol", "sharpe", "max_drawdown")}))
    r_wf, w_hist = P.walk_forward(RR)
    methods.append(dict(method="walk-forward max-Sharpe (out-of-sample)", w_equity=float(w_hist[e].mean()), w_options=float(w_hist[o].mean()),
                        **{k: v for k, v in P.stats(r_wf).items() if k in ("annualized_return", "annualized_vol", "sharpe", "max_drawdown")}))
    doc.table(pd.DataFrame(methods), index=False, fmt={"w_equity": "{:.2f}", "w_options": "{:.2f}", "sharpe": "{:.2f}"})
    doc.h3("Stacked on one NAV (weights are multiples of each sleeve's own sizing)")
    rho = RR.corr().iloc[0, 1]
    se, so = [RR[c].std() * np.sqrt(252) for c in RR]
    rows = []
    for tv in (0.03, 0.05, 0.08):
        k = tv / np.sqrt(2 + 2 * rho)
        a, b = k / se, k / so
        s = P.stats(RR[e] * a + RR[o] * b)
        rows.append(dict(target_vol=tv, equity_multiple=a, options_multiple=b, annualized_return=s["annualized_return"],
                         annualized_vol=s["annualized_vol"], sharpe=s["sharpe"], max_drawdown=s["max_drawdown"],
                         worst_day=s["worst_day"]))
    doc.table(pd.DataFrame(rows), index=False, fmt={"target_vol": "{:.0%}", "equity_multiple": "{:.2f}", "options_multiple": "{:.2f}",
                                                    "sharpe": "{:.2f}", "worst_day": "{:.1%}"})
    doc.p("Equal-risk weights (each sleeve contributes the same stand-alone volatility). The 5% row is the registered `hedged_blend`. Larger "
          "multiples assume the extra options premium and equity gross exposure can actually be financed and traded at the backtested costs.")
    # stress years
    yrs = pd.DataFrame({e: yearly_table(load_daily(e)[0], 1e7).set_index("year")["ret"],
                        o: yearly_table(load_daily(o)[0], 1e7).set_index("year")["ret"],
                        "hedged_blend": yearly_table(load_daily("hedged_blend")[0], 1e7).set_index("year")["ret"] if "hedged_blend" in keep else np.nan})
    ymk = mk.groupby(mk.index.year).apply(lambda x: (1 + x).prod() - 1)
    yrs["market"] = ymk
    stress = yrs.loc[[2000, 2001, 2002, 2008, 2009]]
    doc.h3("The hedge in action: stress years")
    doc.table(stress, fmt={c: "{:.1%}" for c in stress.columns})
    sp = P.market_beta(P.blend(RR, {e: 0.8, o: 1.8}), mk)
    doc.bullets([
        f"The options book's worst year (2008, {yrs.loc[2008, o]:+.1%}) coincided with the equity book's {yrs.loc[2008, e]:+.1%}; in 2009 the signs flipped. "
        "That offset is the hedge -- it comes from low correlation across time, not from any offsetting position.",
        f"The blend's market beta is {sp:.3f}: there is nothing left for an index hedge to remove. Layering a short-index overlay on top would only "
        "pay costs; a market-beta overlay (`s6_beta_overlay`) goes the other way, adding the equity risk premium on purpose.",
        "Everything here is in-sample except the walk-forward row. The caveats that apply to the sleeves apply to the blend: the options sleeve's "
        "costs, the equity sleeve's missing borrow fees, and no financing cost on the stacked multiples."])
    fr.to_csv(RUNS_DIR / "comprehensive" / "capital_split_frontier.csv")
