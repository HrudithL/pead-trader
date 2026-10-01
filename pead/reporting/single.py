"""Per-backtest report: how one strategy works, how it performed, and where its return comes from.

Saved next to the run: results/<name>/report.md, report.pdf and figures/. The report also places the
run against the leaderboard so a new strategy is immediately comparable to the best so far.
"""
import json

import numpy as np
import pandas as pd

from pead.common.paths import RUNS_DIR
from pead.core.metrics import yearly_table
from pead.reporting import attribution as A
from pead.reporting import charts as C
from pead.reporting.doc import Doc

PERF_ROWS = [("Annualized return", "annualized_return", "{:.2%}"), ("Annualized volatility", "annualized_vol", "{:.2%}"),
             ("Sharpe (NAV-pct)", "sharpe", "{:.2f}"), ("Sortino", "sortino", "{:.2f}"),
             ("Max drawdown", "max_drawdown", "{:.1%}"), ("Calmar", "calmar", "{:.2f}"),
             ("Final NAV ($10M start)", "final_nav", "${:,.0f}"), ("Worst day", "worst_day", "{:.2%}"),
             ("% positive days", "pct_positive_days", "{:.1%}"),
             ("Sharpe 1996-2001", "sharpe_1996_2001", "{:.2f}"), ("Sharpe 2002-2007", "sharpe_2002_2007", "{:.2f}"),
             ("Sharpe 2008-2013", "sharpe_2008_2013", "{:.2f}")]


def _market_stats(daily):
    mk = C.market_series(daily["date"])
    years = len(mk) / 252
    ann = (1 + mk).prod() ** (1 / years) - 1
    vol = mk.std(ddof=1) * np.sqrt(252)
    nav = (1 + mk).cumprod()
    return dict(ret=ann, vol=vol, sharpe=mk.mean() * 252 / vol, dd=float((nav / nav.cummax() - 1).min()),
                corr=float(np.corrcoef(daily["daily_return"], mk)[0, 1]))


def _leaderboard_line(result):
    path = RUNS_DIR / "leaderboard.json"
    if not path.exists():
        return None
    board = json.loads(path.read_text()).get(result.asset_class, {})
    lines = []
    for m, label in (("sharpe", "Sharpe"), ("annualized_return", "return"), ("calmar", "Calmar")):
        b = board.get(m)
        if b:
            mine = result.summary.get(m)
            tag = "this strategy" if b["strategy"] == result.name else f"`{b['strategy']}` ({b['value']:.3g})"
            lines.append(f"best {label} among {result.asset_class}: {tag}" + (f"; this one: {mine:.3g}" if mine == mine else ""))
    return lines


def build_strategy_report(result, pdf=True):
    folder = RUNS_DIR / result.name
    fig = folder / "figures"
    fig.mkdir(parents=True, exist_ok=True)
    s, meta = result.summary, result.meta
    doc = Doc(f"{result.name}", f"{result.asset_class} strategy | family: {meta.get('family')} | status: {meta.get('status')}"
              + (f" | derived from `{meta['parent']}`" if meta.get("parent") else ""))

    # ---- what it is -----------------------------------------------------------------------------
    doc.h2("What this strategy does")
    doc.p(meta.get("description", ""))
    if meta.get("thesis"):
        doc.p("**Why it should work.** " + meta["thesis"])
    if meta.get("module_doc"):
        doc.p("**Design notes** (from the strategy's source file).")
        for para in meta["module_doc"].split("\n\n"):
            doc.p(" ".join(para.split()))
    doc.table(pd.DataFrame({"parameter": list(meta.get("params", {}).keys()),
                            "value": [str(v) for v in meta.get("params", {}).values()]}), index=False)

    # ---- performance ----------------------------------------------------------------------------
    doc.h2("Performance")
    mk = _market_stats(result.daily)
    rows = [(lab, fmt.format(s[k])) for lab, k, fmt in PERF_ROWS if k in s and s[k] == s[k]]
    perf = pd.DataFrame(rows, columns=["metric", result.name])
    perf["market (CRSP VW)"] = ["{:.2%}".format(mk["ret"]) if m == "Annualized return" else
                                "{:.2%}".format(mk["vol"]) if m == "Annualized volatility" else
                                "{:.2f}".format(mk["sharpe"]) if m == "Sharpe (NAV-pct)" else
                                "{:.1%}".format(mk["dd"]) if m == "Max drawdown" else "" for m in perf["metric"]]
    doc.table(perf, index=False)
    doc.p(f"Correlation of daily returns with the market: **{mk['corr']:.2f}**.")
    lb = _leaderboard_line(result)
    if lb:
        doc.bullets(lb)
    doc.image(C.nav_chart(result.daily, fig / "nav.png", "Growth of $1 vs. market"), "NAV (log scale)")
    doc.image(C.drawdown_chart(result.daily, fig / "drawdown.png", "Drawdown"), "Drawdown from running peak")
    doc.image(C.yearly_chart(result.daily, fig / "yearly.png", "Calendar-year returns"), "Calendar-year returns")
    doc.image(C.rolling_sharpe_chart(result.daily, fig / "rolling_sharpe.png", "Rolling 1-year Sharpe"),
              "Is the edge stable, or concentrated in a few years?")

    # ---- attribution ----------------------------------------------------------------------------
    if result.asset_class == "equity" and result.positions is not None:
        _equity_attribution_section(doc, result, fig)
    elif result.asset_class == "options" and result.positions is not None:
        _options_attribution_section(doc, result, fig)
    elif result.asset_class == "portfolio":
        _portfolio_section(doc, result, fig)

    path = doc.save(folder, "report", pdf=pdf)
    return path


def _equity_attribution_section(doc, result, fig):
    att = A.equity_attribution(result)
    legs = A.leg_summary(result)
    L, S = legs["long"], legs["short"]
    doc.h2("Where the return comes from")
    doc.image(C.legs_chart(result.daily, fig / "legs.png", "Cumulative P&L by leg", "pnl_long", "pnl_short",
                           "long leg", "short leg"), "Long leg vs. short leg vs. total")
    doc.table(pd.DataFrame({
        "": ["P&L ($M, before costs)", "P&L after costs ($M)", "of which common (average-event) component ($M)",
             "of which excess / PEAD component ($M)", "excess minus costs ($M)", "gross return per $ traded (bp)",
             "positions in the money (hit rate)", "stand-alone Sharpe (leg only)"],
        "long leg": [L["pnl_M"], L["net_of_cost_M"], L["common_M"], L["excess_M"], L["alpha_net_M"],
                     L["gross_bp_per_dollar"], L["hit_rate"], L["sharpe_standalone"]],
        "short leg": [S["pnl_M"], S["net_of_cost_M"], S["common_M"], S["excess_M"], S["alpha_net_M"],
                      S["gross_bp_per_dollar"], S["hit_rate"], S["sharpe_standalone"]]}).set_index(""),
        fmt={"long leg": "{:,.2f}", "short leg": "{:,.2f}"})
    doc.p(f"Daily P&L correlation between the two legs: **{legs['corr_long_short_daily_pnl']:.2f}**.")
    verdict = []
    if S["pnl_M"] < 0 < L["pnl_M"]:
        verdict.append(
            f"**The short leg loses money in absolute terms** ({S['pnl_M']:+.1f}M before costs) while the long leg earns "
            f"{L['pnl_M']:+.1f}M. That is mostly a benchmark effect, not a signal failure: the average earnings-event stock "
            f"beat the cap-weighted market during its holding window, which flatters the long leg and hurts the short leg by "
            f"{abs(L['common_M']):.1f}M / {abs(S['common_M']):.1f}M. Stripping that common component, the surprise signal itself "
            f"earned {L['excess_M']:+.1f}M on the long side and {S['excess_M']:+.1f}M on the short side.")
    elif L["pnl_M"] > 0 and S["pnl_M"] > 0:
        verdict.append("Both legs make money on their own.")
    verdict.append(f"The two legs' daily P&L are {legs['corr_long_short_daily_pnl']:.2f} correlated: the short leg is the hedge "
                   f"that makes the long leg's variance disappear (stand-alone leg Sharpe {L['sharpe_standalone']:.2f} long / "
                   f"{S['sharpe_standalone']:.2f} short vs {result.summary['sharpe']:.2f} combined).")
    doc.bullets(verdict)

    fmt = {"alpha_share": "{:.1%}", "hit_rate": "{:.1%}"}
    doc.h3("By decile and side (signal-driven part only)")
    doc.table(att["decile_leg"][["positions", "notional_M", "net_pnl_M", "excess_M", "cost_M", "alpha_net_M",
                                  "alpha_share", "alpha_bp_per_$"]], fmt=fmt)
    doc.image(C.bar_chart(att["decile_leg"].reset_index().assign(
        label=lambda d: d["leg"].str[0].str.upper() + d["decile"].astype(int).astype(str)).set_index("label"),
        fig / "decile.png", "Excess P&L net of costs by decile ($M)", ["alpha_net_M"], ["alpha net of cost, $M"]),
        "D10 and the short deciles carry the alpha; the middle of each leg earns little")
    for key, title in (("size", "By size quintile (1 = smallest)"), ("liquidity", "By liquidity quintile (1 = least liquid)"),
                       ("holding", "By holding-window length"), ("era", "By era"), ("sector", "By FF12 sector")):
        doc.h3(title)
        doc.table(att[key][["positions", "notional_M", "net_pnl_M", "excess_M", "cost_M", "alpha_net_M", "alpha_share",
                            "alpha_bp_per_$"]], fmt=fmt)
    if "vol" in att:
        doc.h3("By idiosyncratic-volatility tercile")
        doc.table(att["vol"][["positions", "notional_M", "net_pnl_M", "excess_M", "cost_M", "alpha_net_M", "alpha_share",
                              "alpha_bp_per_$"]], fmt=fmt)
    doc.h3("Short-borrow sensitivity (not modeled in the backtest)")
    doc.table(A.borrow_cost_sensitivity(result), fmt={"borrow_rate": "{:.1%}", "fee_M": "{:,.1f}",
                                                     "annualized_return": "{:.2%}", "drag": "{:.2%}"}, index=False)
    doc.p("The engine charges the same transaction cost on shorts as on longs and no stock-borrow fee. The short leg is "
          "concentrated in small, illiquid names, where borrow is scarce and expensive -- read the table as a floor on "
          "the cost of running the short leg.")


def _options_attribution_section(doc, result, fig):
    att = A.options_attribution(result)
    c = att["concentration"]
    doc.h2("Where the return comes from")
    d = result.daily
    doc.image(C.legs_chart(d, fig / "legs.png", "Cumulative P&L: call positions vs put positions", "pnl_call", "pnl_put",
                           "calls", "puts"), "Call leg vs put leg")
    doc.bullets([
        f"**{c['pct_calls']:.1%} of funded trades are calls** and they produce **{c['call_share_of_gross']:.1%}** of gross P&L -- "
        f"even in the worst-surprise deciles, where the selector's best contract is usually still a call (a cheap OTM call "
        f"has more convexity per dollar than a put once the bad news is in the price).",
        f"Hit rate is only {c['hit_rate']:.1%} and the median trade returns {c['median_roi']:.1%} on premium, yet the mean is "
        f"{c['mean_roi']:.1%}: a classic convex pay-off. The best 1% of trades produce **{c['top1pct_share_of_gross']:.0%}** of gross "
        f"P&L and the best 10% produce {c['top10pct_share_of_gross']:.0%}.",
        "Read the moneyness, volatility and premium tables together: the profit is concentrated in cheap out-of-the-money calls "
        "on low-volatility stocks -- exactly the contracts whose real-world bid/ask costs are hardest to model."])
    fmt = {"hit_rate": "{:.1%}", "share_of_net": "{:.1%}", "roi_on_premium": "{:.1%}"}
    cols = ["trades", "premium_M", "gross_M", "cost_M", "net_M", "share_of_net", "roi_on_premium", "hit_rate"]
    for key, title in (("leg", "By contract side"), ("decile", "By SUE decile"), ("moneyness", "By side and moneyness (strike / spot)"),
                       ("vol", "By the stock's pre-event volatility tercile"), ("vol_moneyness", "Calls only: volatility x moneyness"),
                       ("premium", "By premium per share"), ("year", "By year")):
        if key in att:
            doc.h3(title)
            doc.table(att[key][cols], fmt=fmt)
    doc.h3("Execution sensitivity (the backtest assumes a flat 3% round-trip cost)")
    from pead.strategies import registry
    from pead.strategies.base import RunContext
    doc.table(A.options_execution_sensitivity(registry.get(result.name), RunContext()),
              fmt={"annualized_return": "{:.2%}", "annualized_vol": "{:.2%}", "max_drawdown": "{:.1%}"}, index=False)
    doc.p("Each row re-runs the identical picks and quote history with a harsher cost of getting into the contract. A flat "
          "percentage understates the cost of $0.10-$0.50 contracts, whose bid/ask spread is often a multiple of the 3% assumed.")


def _portfolio_section(doc, result, fig):
    d = result.daily
    sleeves = [c[4:] for c in d.columns if c.startswith("pnl_")]
    doc.h2("Where the return comes from")
    import numpy as np
    from pead.reporting.charts import COLORS, plt, apply_style
    apply_style()
    f, ax = plt.subplots(figsize=(9, 3.6))
    dates = pd.DatetimeIndex(d["date"])
    for i, k in enumerate(sleeves):
        ax.plot(dates, d[f"pnl_{k}"].cumsum() / 1e6, color=COLORS[i], label=k)
    ax.plot(dates, d["daily_pnl"].cumsum() / 1e6, color="#222222", lw=2, label="blend")
    ax.set_ylabel("cumulative P&L, $M")
    ax.legend(frameon=False)
    f.tight_layout()
    f.savefig(fig / "sleeves.png", dpi=140)
    plt.close(f)
    doc.image(fig / "sleeves.png", "Each sleeve's contribution to the blend's cumulative P&L")
    rows = []
    for k in sleeves:
        pnl = d[f"pnl_{k}"]
        rows.append(dict(sleeve=k, weight=result.meta["params"].get(f"w_{k}"), pnl_M=pnl.sum() / 1e6,
                         share_of_pnl=pnl.sum() / d["daily_pnl"].sum()))
    doc.table(pd.DataFrame(rows), index=False, fmt={"share_of_pnl": "{:.1%}", "weight": "{:.2f}"})
    s = result.summary
    doc.bullets([f"Correlation between the sleeves' daily returns: **{s.get('sleeve_correlation', float('nan')):.2f}**.",
                 f"Blend market beta {s['market_beta']:.3f}, correlation to the market {s['corr_market']:.2f}."])
    doc.p("Return, volatility and Sharpe of the blend are in the Performance table above; the cross-strategy report "
          "(`python -m pead compare`) shows the full weight frontier, the walk-forward (out-of-sample) weights and the "
          "volatility-target table.")
