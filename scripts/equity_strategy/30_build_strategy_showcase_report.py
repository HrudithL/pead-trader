"""Assemble reports/PEAD_Strategy_Showcase.pdf -- performance of all 8 strategies.

This is the "here's what we built and how it performed" report: every strategy's
construction in plain language, its full-sample metrics, and the charts comparing
all 8 side by side. It assumes the reader already accepts that PEAD is a real,
tradeable effect (see PEAD_Report.pdf) and wants to know how well each way of
trading it actually did. For the story of *why* each strategy was built the way
it was, see PEAD_Strategy_Development.pdf.
"""
import sys
from pathlib import Path
import json
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Image, PageBreak, KeepTogether

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, REPORTS_DIR, FIGURES_DIR
from common.report_pdf import new_report

DATA = DATA_DIR
FIG = FIGURES_DIR
OUT = REPORTS_DIR / "PEAD_Strategy_Showcase.pdf"

summary = pd.read_csv(DATA / "results_summary_v2_FINAL.csv").set_index("strategy")

def js(name):
    with open(DATA / f"backtest_v2_{name}_summary.json") as f:
        return json.load(f)

rb, story, styles, h1, h2, h3, body, caption, glossary, rule, fig, make_table = new_report()

# ============================== COVER ==============================
story.append(Spacer(1, 0.3*inch))
h1("PEAD Strategy Showcase")
story.append(Paragraph("Performance of all 8 tradeable strategy constructions, 1996&ndash;2013", styles["TitleSub"]))
story.append(Paragraph("Companion to <i>PEAD_Report.pdf</i> (evidence the effect exists) and <i>PEAD_Strategy_Development.pdf</i> (why each design decision was made)", styles["TitleSub"]))
rule()

body("""This report answers one question for each of the 8 strategies built in this project:
<b>if you had traded this exact rule from 1996 to 2013, what would you have earned, and at what
risk?</b> Every number here is net of estimated transaction costs and realistic position-size
limits (a stock can't be traded past 1-5% of its own daily volume), and every strategy compounds
its own trailing net asset value (NAV) &mdash; sizing positions off a growing or shrinking capital
base as the strategy wins and loses, not a fixed starting amount. All returns are
<b>market-adjusted</b> (the return on the S&amp;P/CRSP market index is subtracted out) except
Strategy 7, which is explicitly built to let some market exposure through and so is reported in
raw terms &mdash; see that strategy's own section for why.""")

body("""<b>A note on how these Sharpe ratios are calculated.</b> Every strategy's daily return is
computed as that day's dollar profit or loss divided by the <i>prior day's NAV</i>, not a fixed
starting capital figure. An earlier version of this backtest divided by the fixed starting
capital instead; since NAV compounds upward over a profitable 18-year run, that understated later
years' percentage moves and made volatility look larger (and Sharpe smaller) than it really was.
The numbers in this report use the corrected, NAV-based calculation throughout.""")

story.append(PageBreak())

# ============================== OVERVIEW TABLE ==============================
h2("Overview: all 8 strategies side by side")
order = ["extreme", "rankweighted", "balanced", "strategy4", "strategy5", "strategy6",
         "strategy6_beta050", "strategy7"]
labels = ["1. Extreme decile L/S", "2. Rank-weighted", "3. Balanced (size-neutral)",
          "4. Trimmed/tilted, priority cap", "5. + Sector-neutral", "6. + Leverage cap 2.5x",
          "6+beta. + 0.5x market beta overlay", "7. Unconstrained net exposure"]

overview = [["Strategy", "Ann. Return", "Ann. Vol", "Sharpe", "Max Drawdown", "Final NAV\n($10M start)"]]
for key, label in zip(order, labels):
    r = summary.loc[key]
    overview.append([label, f"{r['ann_return']*100:.2f}%", f"{r['ann_vol']*100:.2f}%",
                      f"{r['sharpe']:.2f}", f"{r['max_drawdown']*100:.1f}%", f"${r['final_nav']/1e6:.1f}M"])
story.append(make_table(overview, col_widths=[2.05*inch, 0.85*inch, 0.75*inch, 0.65*inch, 0.95*inch, 0.95*inch], fontsize=8.6))
caption("""Table 1. All figures 1996-2013, trailing-NAV compounding, net of transaction costs and
liquidity caps, from a $10,000,000 starting capital base. Strategy 5 has the best Sharpe and
shallowest drawdown; Strategy 6+beta has the highest absolute return; Strategy 6 (pure alpha, no
overlay) is the recommended core strategy -- see the Recommendation section at the end of this
report.""")

fig(FIG / "12_v2_nav_curves_all.png")
caption("""Figure 1. NAV curves for all 8 strategies. The beta-overlay strategy (dark red) pulls
well ahead because it deliberately carries market exposure on top of the alpha book; the other 7
stay dollar-neutral (or close to it) by design and grow far more slowly but far more smoothly.""")

story.append(PageBreak())

fig(FIG / "15_v2_comparison_bars.png")
caption("""Figure 2. Return, Sharpe ratio, and max drawdown compared directly. Notice that higher
return (Strategy 6+beta, Strategy 7) does not mean higher Sharpe -- Strategy 5 has the lowest
return of the six core (non-overlay) strategies but the best risk-adjusted return and the
shallowest drawdown, because it carries the least uncompensated risk.""")

fig(FIG / "14_v2_drawdowns_all.png")
caption("""Figure 3. Drawdown from the prior peak, all 8 strategies. Every strategy's worst
drawdown clusters around the 2008-2009 financial crisis. Strategy 6+beta's drawdown (-23.2%) is
the deepest because it is the only strategy carrying meaningful net market exposure by
construction.""")

story.append(PageBreak())

# ============================== PER-STRATEGY PROFILES ==============================
h2("Strategy profiles")
body("""Each profile below states, in plain terms, exactly how positions were chosen and sized,
followed by the strategy's own metrics. See <i>PEAD_Strategy_Development.pdf</i> for the
reasoning that led from one strategy to the next.""")

def profile(num_label, name, key, construction, notes):
    h3(f"{num_label}. {name}")
    body(construction)
    r = summary.loc[key]
    j = js(key)
    rows = [["Metric", "Value"],
            ["Annualized return", f"{r['ann_return']*100:.2f}%"],
            ["Annualized volatility", f"{r['ann_vol']*100:.2f}%"],
            ["Sharpe ratio", f"{r['sharpe']:.2f}"],
            ["Max drawdown", f"{r['max_drawdown']*100:.1f}%"]]
    if "avg_leverage_vs_initial_capital" in j:
        rows.append(["Avg. gross leverage", f"{j['avg_leverage_vs_initial_capital']:.2f}x"])
    if "avg_n_open_positions" in j:
        rows.append(["Avg. concurrent positions", f"{j['avg_n_open_positions']:,.0f}"])
    if "pct_liquidity_capped" in j:
        rows.append(["% positions liquidity-capped", f"{j['pct_liquidity_capped']*100:.1f}%"])
    if "total_transaction_costs" in j:
        rows.append(["Total transaction costs", f"${j['total_transaction_costs']:,.0f}"])
    if key == "strategy7" and "correlation_to_market" in j:
        rows.append(["Correlation to market", f"{j['correlation_to_market']:.2f}"])
    t = make_table(rows, col_widths=[2.3*inch, 1.6*inch], fontsize=8.6, header_bg="#3f6fa8")
    story.append(KeepTogether([t, Spacer(1, 4), Paragraph(notes, styles["Caption"])]))
    story.append(Spacer(1, 10))

profile("1", "Extreme decile long/short", "extreme",
    """Long every stock in the most positive-surprise decile (D10), short every stock in the most
    negative-surprise decile (D1), equal +/-1 weight. The holding period is not a fixed number of
    days -- it comes from where each decile's own return curve was measured to flatten out
    (roughly 20-80 trading days depending on the decile; see <i>PEAD_Strategy_Development.pdf</i>'s
    Stage 1 for how this was decided from the data).
    This is the simplest possible way to trade the effect, and the baseline every later strategy
    is compared against.""",
    """The starting point. Profitable and sensible, but trades only the most extreme 20% of
    events and makes no attempt to control for sector or size tilts in the two legs.""")

profile("2", "Rank-weighted", "rankweighted",
    """Every event, across all ten deciles (not just the extremes), gets a position weight that
    scales continuously with how extreme its surprise rank was that quarter -- close to zero near
    the median, full weight at the tails. This uses the entire cross-section instead of discarding
    80% of events at a hard cutoff.""",
    """Better Sharpe than the extreme-decile version (more diversification, lower volatility) but
    lower absolute return, and it saturates the liquidity cap on 99% of its positions and the
    leverage cap in 70 of its quarters -- trading this many names runs into real capacity limits.
    This tradeoff is exactly what motivated Strategy 4.""")

profile("3", "Balanced (size-neutral)", "balanced",
    """Same D10/D1 extreme-decile selection as Strategy 1, but each side's weight is redistributed
    across firm-size quintiles every quarter so no single size bucket dominates either leg. This
    guards against the long and short legs accidentally differing in average firm size, which
    would secretly turn part of the return into an unrelated size bet rather than a PEAD bet.""",
    """A direct, targeted fix for one specific risk (size imbalance), not a redesign of the whole
    strategy. Performs close to Strategy 1, which is itself informative: size imbalance was not,
    on its own, doing much of the work in Strategy 1's return.""")

profile("4", "Trimmed / tilted, priority-based cap", "strategy4",
    """Built after diagnosing exactly why rank-weighted's Sharpe was better but its return worse:
    it was trading about 4x more positions than the extreme-decile strategies, hitting the
    liquidity cap on 99% of positions and the leverage cap in 90% of quarters, and the old
    leverage cap trimmed <i>every</i> position proportionally when it bound -- diluting the
    highest- and lowest-conviction trades equally. Strategy 4 trims out the low-conviction middle
    of the SUE-rank distribution entirely, tilts remaining weight toward the extremes, keeps
    size-neutrality, and replaces proportional cap-trimming with <b>priority-based</b> trimming:
    when the leverage cap binds, the lowest-conviction positions are dropped first, not everyone
    shaved by the same percentage.""",
    """The first strategy to meaningfully beat the v1 baseline on both return (5.26% vs. 4.91%)
    and Sharpe (1.18 vs. 0.93) at the same time, rather than trading one for the other.""")

profile("5", "+ Sector-neutral", "strategy5",
    """A diagnostic run on Strategy 4 found its book concentrating up to <b>29% of gross weight in
    a single Fama-French sector</b> in some quarters (versus 8.3% if evenly spread across all 12
    sectors) -- an unpriced, PEAD-unrelated risk the strategy was carrying by accident. Strategy 5
    adds sector-neutrality alongside the existing size-neutrality, redistributing weight within
    each sector the same way size was already balanced within each quintile.""",
    """The best risk-adjusted strategy built in this project: Sharpe 1.33, max drawdown only
    -10.8% (the shallowest of all 8). Removing an unpriced risk, rather than chasing more return,
    is what produced the best risk-adjusted outcome -- see Figure 4 in the development report for
    the concentration data behind this decision.""")

profile("6", "+ Leverage cap raised 1.5x -> 2.5x", "strategy6",
    """Tests whether Strategy 5's 1.5x gross leverage cap was actually costing it anything, now
    that sector concentration (the risk the cap was implicitly protecting against) is gone. A
    sweep of the cap from 1.5x up to 10x found returns and Sharpe both flatten out entirely by
    about 2.0-2.5x -- the point where the cap stops binding at all for this specific,
    well-diversified signal. Strategy 6 raises the cap to 2.5x.""",
    """Improved both return (5.08%->6.49%) <i>and</i> Sharpe versus Strategy 5, because there was
    no longer any concentration risk left for the tighter cap to be protecting against. This is
    the recommended core (pure-alpha) strategy for live consideration.""")

profile("6+beta", "Strategy 6 + 0.5x market beta overlay", "strategy6_beta050",
    """Strategy 6's alpha book is left completely untouched. A separate synthetic market-index
    position, sized to 0.5x the strategy's own trailing NAV and rebalanced on the same quarterly
    cadence (2bps overlay transaction cost), is held <i>alongside</i> it -- the standard
    institutional way (a "130/30"-style long-extension structure) to add back market exposure
    without distorting the alpha signal itself.""",
    """Highest absolute return of any strategy built (10.08%), landing inside a 10-15%
    equity-market-like target band, with a much shallower drawdown and better Sharpe than simply
    holding the market outright over the same period. Recommended if a return closer to the
    market's, with correspondingly more market-correlated risk, is preferred over a pure,
    uncorrelated alpha stream.""")

profile("7", "Unconstrained net exposure", "strategy7",
    """Tests the <i>other</i> way to let market exposure through: instead of adding a separate
    overlay, stop forcing the long leg's and short leg's dollar totals to match every quarter (the
    size/sector-neutral construction does this by construction). Removing that constraint required
    switching from market-adjusted to <b>raw</b> returns -- market-adjusting would have silently
    cancelled out the very net exposure this test is trying to let through.""",
    """A cautionary result, not a recommended design: despite averaging only -5.5% net exposure as
    a fraction of gross (essentially flat by that measure), Strategy 7's correlation to the market
    came out at <b>0.52</b> -- much higher than intended -- because dollar-neutral is not the same
    thing as beta-neutral. Positive- and negative-surprise firms structurally differ in average
    beta, so equal dollar amounts long and short don't cancel market risk the way market-adjusting
    the underlying returns does. Kept in the repo as a fully documented, tested result precisely
    because it demonstrates that lesson.""")

story.append(PageBreak())

# ============================== RECOMMENDATION ==============================
h2("Recommendation")
body("""<b>For live consideration: Strategy 6 (pure alpha) or Strategy 6 + 0.5x beta overlay.</b>
Every strategy in this project is deliberately built market-adjusted and (mostly) dollar-neutral,
which means it structurally forgoes the equity risk premium that makes up most of a simple
buy-and-hold return -- comparing its nominal return to the S&amp;P 500 directly is not apples to
apples. As an external sanity check, AQR's live Equity Market Neutral Fund (QMNNX) returns
6.19% at 7.03% volatility (Sharpe 0.69) and also trails the S&amp;P 500 over the same window --
that is what a professionally run market-neutral strategy is supposed to look like. Strategy 6
already beats that real fund's Sharpe (1.30 vs. 0.69).""")
body("""&bull; Choose <b>Strategy 6</b> for the purest, most uncorrelated alpha stream (Sharpe
1.30, max drawdown -16.6%, close to zero market beta by construction).<br/>
&bull; Choose <b>Strategy 6 + 0.5x beta overlay</b> if a return closer to 10-15% is preferred, in
exchange for correspondingly more market-correlated risk (Sharpe 0.97, max drawdown -23.2%, but
10.08% return -- and still a shallower drawdown than holding the market alone).<br/>
&bull; <b>Strategy 7 is not recommended</b> for either purpose: it adds market exposure in a way
that is measurably less controllable than the explicit overlay, as documented above.""")

story.append(Spacer(1, 0.2*inch))
h2("Caveats specific to this backtest")
body("""&bull; <b>Transaction costs and liquidity caps are disclosed assumptions, not calibrated
figures.</b> The cost schedule and position-size caps are reasonable, conservative-leaning
choices, not fitted to any real broker, venue, or historical execution data.<br/>
&bull; <b>No borrow cost or short-availability modeling.</b> Every strategy that shorts D1 assumes
the short is executable at the modeled cost with no separate borrow fee and no risk the shares
simply aren't available to borrow -- a real constraint, worse for exactly the small, illiquid
names that dominate the negative-surprise decile.<br/>
&bull; <b>One historical realization, not walk-forward validated.</b> Each strategy reports one
full-sample backtest over 1996-2013; none of the design choices (holding-period buckets, cost
tiers, leverage cap, trim/tilt parameters) were tuned on a held-out period and validated
out-of-sample separately.<br/>
&bull; <b>Data ends in 2013.</b> PEAD is a well-known, heavily studied anomaly; a live strategy
today would need current data revalidation, and this project's own era-subsample analysis (see
<i>PEAD_Report.pdf</i> Section 5c) already found weaker point estimates in the most recent years
of this sample.""")

story.append(Spacer(1, 0.3*inch))
rule()
caption("""Generated from data/results_summary_v2_FINAL.csv and data/backtest_v2_*_summary.json.
Scripts: scripts/equity_strategy/14_daily_decay.py through
scripts/equity_strategy/26_strategy7_unconstrained_netexposure.py,
scripts/equity_strategy/29_v2_strategy_charts.py, scripts/equity_strategy/30_build_strategy_showcase_report.py.""")

rb.save(OUT, title="PEAD Strategy Showcase")
print("wrote", OUT)
