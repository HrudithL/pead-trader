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

REPRESENTATIVE = ["extreme_picks", "graded_bets", "conviction_tilt", "sector_balanced", "balanced_leverage",
                  "market_exposed", "calm_weighted", "volatility_filter", "calm_rank_tilt", "market_boost",
                  "options_cap20", "options_cap30", "options_cap20_legacy", "hedged_blend"]
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
    doc.p("Every strategy that has been run, by asset class and Sharpe. `validated` means the engine reproduces the original script and no "
          "parameter was fitted to the sample -- it does NOT mean the strategy has an edge. `research` are explorations/variants or books whose "
          "parameters were chosen in-sample; `reference` are kept as documented counter-examples or pre-correction twins.")
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
    doc.p("`d_*` columns are the change from the parent strategy. Reading down: neutralizing size and sector (Conviction Tilt -> Sector Balanced) and "
          "inverse-vol sizing (Balanced Leverage -> Calm Weighted) buy Sharpe; raising the leverage cap buys return; the max-size variants buy "
          "return at the cost of Sharpe because the liquidity cap starts to bind.")

    # ---- 3. era decay ---------------------------------------------------------------------------
    doc.h2("3. Stability across eras")
    era = df.set_index("name")[["sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013"]]
    era = era.loc[[n for n in keep if n in era.index]]
    doc.table(era, fmt={c: "{:.2f}" for c in era.columns})
    yrs = {n: yearly_table(load_daily(n)[0], 1e7).set_index("year")["ret"] for n in
           [k for k in ("calm_weighted", "options_cap20", "hedged_blend") if k in keep]}
    ymk = mk.groupby(mk.index.year).apply(lambda x: (1 + x).prod() - 1)
    ytab = pd.DataFrame(yrs).assign(market=ymk).loc[1996:2013]
    doc.table(ytab, fmt={c: "{:.1%}" for c in ytab.columns})
    doc.image(C.bar_chart(ytab, fig / "years_sleeves.png", "Calendar-year return by sleeve", list(ytab.columns),
                          ylabel="return", rotation=60, figsize=(10, 3.6)),
              "Calendar-year return of the equity sleeve, the options sleeve, the blend and the market.")
    if "calm_weighted" in ytab:
        a, b = ytab.loc[1996:1999, "calm_weighted"].mean(), ytab.loc[2011:2013, "calm_weighted"].mean()
        doc.p(f"**The equity drift has decayed**: Calm Weighted's return averaged {a:.1%} a year in 1996-99 and {b:.1%} in 2011-13, the "
              "usual signature of a drift being arbitraged away.")

    # ---- 4. long vs short across equity books -------------------------------------------------------
    doc.h2("4. Equity books: long leg vs. short leg")
    rows = []
    for n in [k for k in ("extreme_picks", "graded_bets", "conviction_tilt", "sector_balanced",
                          "balanced_leverage", "calm_weighted") if k in set(df["name"])]:
        ls = A.leg_summary(load_result(n))
        rows.append(dict(strategy=n, long_pnl_M=ls["long"]["pnl_M"], short_pnl_M=ls["short"]["pnl_M"],
                         long_excess_M=ls["long"]["excess_M"], short_excess_M=ls["short"]["excess_M"],
                         long_bp_per_dollar=ls["long"]["gross_bp_per_dollar"], short_bp_per_dollar=ls["short"]["gross_bp_per_dollar"],
                         corr_long_short=ls["corr_long_short_daily_pnl"]))
    doc.table(pd.DataFrame(rows), index=False, fmt={c: "{:,.1f}" for c in ["long_pnl_M", "short_pnl_M", "long_excess_M", "short_excess_M",
                                                                           "long_bp_per_dollar", "short_bp_per_dollar"]} | {"corr_long_short": "{:.2f}"})
    tab = pd.DataFrame(rows).set_index("strategy")
    all_short_lose = bool((tab["short_pnl_M"] < 0).all())
    ratio = tab["long_excess_M"] / tab["short_excess_M"]
    bl = A.equity_attribution(load_result("balanced_leverage")) if "balanced_leverage" in set(df["name"]) else None
    bullets = [
        ("**In every equity strategy the short leg loses money in absolute terms** while the long leg earns more than the total. " if all_short_lose
         else "The short leg loses money in absolute terms in some, not all, of these books. ") +
        "The headline is real but misleading: returns are measured against the cap-weighted market, and the average event stock beats that "
        "benchmark (small-cap tilt plus the earnings-announcement premium), flattering the long leg and penalizing the short leg by about the same amount.",
        f"Stripped of that common component (columns `*_excess_M`, measured against the average announcer of the quarter) the long leg's signal "
        f"earns {ratio.min():.1f}x to {ratio.max():.1f}x the short leg's: close to equal against this benchmark, not exactly equal. "
        "Against a size-matched benchmark the long leg is clearly ahead per dollar (`docs/STRATEGY_PROFILES.md`, `results/profiles_benchmark_compare.csv`); "
        "the by-side conclusion therefore depends on the benchmark.",
        f"The legs' daily P&L are {tab['corr_long_short'].min():.2f} to {tab['corr_long_short'].max():.2f} correlated: the short leg is a hedge. "
        "Dropping it for a long-only book would multiply the volatility and add market-relative factor exposure without adding alpha.",
        "The backtest charges no stock-borrow fee. The short leg is small-cap-heavy where borrow is expensive; the per-strategy report's borrow table "
        "gives the cost of 0.5% to 5% a year."]
    if bl is not None:
        sz, lq, hd, er = bl["size"]["alpha_bp_per_$"], bl["liquidity"]["alpha_bp_per_$"], bl["holding"]["alpha_bp_per_$"], bl["era"]["alpha_bp_per_$"]
        bullets.append(
            "Balanced Leverage, alpha per dollar traded against the average announcer of the quarter (all-announcer benchmark): "
            f"smallest size quintile {sz.iloc[0]:.0f} bp, second {sz.iloc[1]:.0f} bp, largest {sz.iloc[-1]:.0f} bp; least-liquid quintile {lq.iloc[0]:.0f} bp, "
            f"most liquid {lq.iloc[-1]:.0f} bp; holds over 90 days {hd.iloc[-1]:.0f} bp vs {hd.iloc[0]:.0f} bp for holds up to 40 days; "
            f"by era {er.iloc[0]:.0f} / {er.iloc[1]:.0f} / {er.iloc[2]:.0f} bp ({er.iloc[0] / er.iloc[2]:.1f}x fall from first to last period). "
            "The size pattern depends on the benchmark: a size-matched benchmark (docs/STRATEGY_PROFILES.md) gives the smallest quintile a much higher figure.")
    doc.bullets(bullets)

    # ---- 5. options -----------------------------------------------------------------------------
    if "options_cap20" in set(df["name"]):
        doc.h2("5. Options book: what the return is made of, and how much of it is real")
        cur = df.set_index("name")
        if "options_cap20_legacy" in cur.index and "options_worst_case" in cur.index:
            mid = (f" -> {cur.loc['options_cap20_midfill', 'annualized_return']:.2%} / {cur.loc['options_cap20_midfill', 'sharpe']:.2f} "
                   "with mid fills on the corrected deciles (`options_cap20_midfill`)") if "options_cap20_midfill" in cur.index else ""
            doc.p("**The originally reported options result does not survive a careful look at the quotes and the fills.** The original backtest marked "
                  "a missing forward quote at the previous live quote and filled at the mid. Re-querying one year of quotes (a pre-fix check, not repeated) found that "
                  "most missing 60-day quotes were zero-bid dead contracts that the convention never marked down. Results: "
                  f"{cur.loc['options_cap20_legacy', 'annualized_return']:.2%} / Sharpe {cur.loc['options_cap20_legacy', 'sharpe']:.2f} as reported"
                  f"{mid} -> {cur.loc['options_cap20', 'annualized_return']:.2%} / {cur.loc['options_cap20', 'sharpe']:.2f} "
                  "filled at the real ask in and bid out (`options_cap20`) -> "
                  f"{cur.loc['options_worst_case', 'annualized_return']:.2%} / {cur.loc['options_worst_case', 'sharpe']:.2f} "
                  "if dead options are worth nothing. **After real fills the options book has no measurable edge** "
                  f"(Sharpe {cur.loc['options_cap20', 'sharpe']:.2f}).")
        att = A.options_attribution(load_result("options_cap20"))
        c = att["concentration"]
        dm = att["moneyness"].xs("call", level="leg").loc[["deep ITM <0.8"]]
        doc.bullets([
            f"{c['pct_calls']:.1%} of funded trades are calls, producing {c['call_share_of_gross']:.1%} of gross P&L -- across ALL deciles, "
            "including the worst surprises. The Kelly selector finds cheap OTM calls better than puts even on bad news.",
            f"Win rate {c['hit_rate']:.0%}, median trade {c['median_roi']:.0%}, mean {c['mean_roi']:.0%}: a convex pay-off. The top 1% of trades "
            f"are {c['top1pct_share_of_gross']:.0%} of gross P&L.",
            "Where the (near-zero) net result comes from, by moneyness and volatility, is in the tables below and in `results/options_cap20/report.md`: "
            f"deep in-the-money calls take {dm['premium_M'].sum() / att['moneyness']['premium_M'].sum():.0%} of the premium "
            f"at {dm['gross_M'].sum() / dm['premium_M'].sum():.1%} on it, and the high-volatility tercile returns {att['vol']['roi_on_premium'].iloc[-1]:.1%} on premium.",
            "Return on premium does not rise cleanly with SUE decile, so this is not a pure drift trade, and the profit is dominated by a few years "
            f"(largest single-year share of net P&L: {att['year']['share_of_net'].abs().max():.0%})."])
        doc.table(att["vol"][["trades", "premium_M", "gross_M", "net_M", "share_of_net", "roi_on_premium"]],
                  fmt={"share_of_net": "{:.1%}", "roi_on_premium": "{:.1%}"})
        doc.h3("Execution and marking sensitivity")
        from pead.strategies import registry
        from pead.strategies.base import RunContext
        doc.table(A.options_execution_sensitivity(registry.get("options_cap20"), RunContext()),
                  fmt={"annualized_return": "{:.2%}", "annualized_vol": "{:.2%}", "max_drawdown": "{:.1%}"}, index=False)
        doc.p("The `_legacy` and `_prefix_fix` options books are the historical headline only. Everything the hedged blend says about "
              "options is built on the corrected `options_cap20`, whose edge is approximately zero.")

    # ---- 6. correlations -----------------------------------------------------------------------
    doc.h2("6. How the strategies move together")
    corr = R.assign(market=mk).corr()
    doc.image(C.heatmap(corr, fig / "corr.png", "Daily-return correlation"), "Equity books cluster together; options are independent of them.")
    nm = [n for n in ("extreme_picks", "graded_bets", "conviction_tilt", "sector_balanced", "balanced_leverage", "calm_weighted",
                      "volatility_filter", "calm_rank_tilt") if n in corr.index]
    mn = pd.Series([corr.loc[a, b] for k, a in enumerate(nm) for b in nm[k + 1:]])
    doc.bullets([
        f"The market-neutral equity books are {mn.min():.2f} to {mn.max():.2f} correlated with each other -- they are one bet expressed several ways, so combining them adds nothing.",
        f"Equity vs. options correlation: {corr.loc['calm_weighted', 'options_cap20']:.2f}. The options book's correlation to the market is "
        f"{corr.loc['market', 'options_cap20']:.2f}; the market-neutral equity books' range is {corr.loc[nm, 'market'].min():.2f} to {corr.loc[nm, 'market'].max():.2f}.",
        f"`market_boost` is {corr.loc['market', 'market_boost']:.2f} correlated with the market by construction; it answers 'beat the S&P', not 'diversify'."])

    # ---- 7. combination ---------------------------------------------------------------------------
    _combination_section(doc, fig, R, mk, keep)

    # ---- 8. recommendations ---------------------------------------------------------------------
    doc.h2("8. Conclusions and next steps")
    cs = df.set_index("name")
    op = cs.loc["options_cap20"] if "options_cap20" in cs.index else None
    doc.bullets([
        "Use `calm_weighted` as the equity alpha sleeve (validated, no fitted parameters; `calm_rank_tilt` is the stronger research candidate; "
        "the constants of both were chosen before the methodology fixes and not re-selected). "
        + (f"The options sleeve (`options_cap20`: {op['annualized_return']:.2%} a year, Sharpe {op['sharpe']:.2f}) shows no measurable edge after real "
           "bid/ask fills, so no options sleeve or blend weights are recommended; `hedged_blend` is kept as a reference. " if op is not None else "")
        + "See section 7 for how the blend compares with its equity sleeve.",
        "Do not add more market-neutral equity variants -- they are one bet. New equity ideas must change the signal or the holding rule, not "
        "the sizing.",
        "Options: the quote and fill conventions were the biggest errors found in this project (docs/FIXES.md). Open follow-ups: re-score the Kelly "
        "selector at the ask instead of the mid, and test on a holdout; do not size anything on the options sleeve until an edge survives real fills.",
        "The measured weak spots to attack on the equity side: the era decay (Balanced Leverage's alpha per dollar by era is in section 4), the <=40-day "
        "holding cells that earn little after costs, and the mid deciles that earn almost no excess per dollar."])
    return doc.save(out, "report", pdf=pdf)


def _combination_section(doc, fig, R, mk, keep):
    doc.h2("7. Combining an equity book with an options book")
    e = "calm_weighted"
    o = "options_cap20"
    if e not in R or o not in R:
        doc.p("Run `calm_weighted` and `options_cap20` first.")
        return
    RR = R[[e, o]]
    st = {k: P.stats(RR[k]) for k in RR}
    # capital-split frontier
    ws = np.round(np.arange(0, 1.0001, 0.05), 2)
    fr = pd.DataFrame([dict(w_equity=w, **P.stats(P.blend(RR, {e: w, o: 1 - w}))) for w in ws]).set_index("w_equity")
    doc.h3("Capital split: fraction of NAV in each sleeve (weights sum to 1)")
    doc.image(C.bar_chart(fr.iloc[::2][["sharpe"]], fig / "frontier_sharpe.png", "Sharpe by equity share of capital", ["sharpe"],
                          ylabel="Sharpe"), f"Sharpe by equity share of capital; the in-sample peak is at {fr['sharpe'].idxmax():.0%} equity.")
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
    sp = P.market_beta(P.blend(RR, {e: 0.8, o: 1.6}), mk)
    bs, es = P.stats(P.blend(RR, {e: 0.8, o: 1.6})), st[e]
    doc.bullets([
        f"The options book's worst year (2008, {yrs.loc[2008, o]:+.1%}) coincided with the equity book's {yrs.loc[2008, e]:+.1%}; in 2009 the signs flipped. "
        "A low correlation across time is the only hedge here; it is not an offsetting position.",
        f"**Blend vs its equity sleeve.** The registered blend (0.8x equity + 1.6x options) has return {bs['annualized_return']:.1%}, Sharpe {bs['sharpe']:.2f}, "
        f"max drawdown {bs['max_drawdown']:.1%}; `calm_weighted` alone has {es['annualized_return']:.1%}, {es['sharpe']:.2f}, {es['max_drawdown']:.1%}. "
        f"The best in-sample capital split has Sharpe {fr['sharpe'].max():.2f}; the walk-forward split {P.stats(r_wf)['sharpe']:.2f}. "
        "Where these are not clearly above the equity sleeve alone, the options sleeve adds risk, not return.",
        f"The blend's market beta is {sp:.3f}: there is nothing left for an index hedge to remove. Layering a short-index overlay on top would only "
        "pay costs; a market-beta overlay (`market_boost`) goes the other way, adding the equity risk premium on purpose.",
        "Everything here is in-sample except the walk-forward row. The caveats that apply to the sleeves apply to the blend: the options sleeve's "
        "costs, the equity sleeve's missing borrow fees, and no financing cost on the stacked multiples."])
    fr.to_csv(RUNS_DIR / "comprehensive" / "capital_split_frontier.csv")
