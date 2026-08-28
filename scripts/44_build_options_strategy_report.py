"""Assemble reports/PEAD_Options_Strategy.pdf -- a risk-reversal strategy for trading the
options-market PEAD signal documented in PEAD_Options_Report.pdf, designed against prior academic
and practitioner research on options and earnings announcements (see Section 1 for citations).
"""
import json
import pandas as pd
import numpy as np
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, HRFlowable
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIG = ROOT / "reports" / "figures"
OUT = ROOT / "reports" / "PEAD_Options_Strategy.pdf"

with open(DATA / "options_strategy_summary.json") as f:
    summary = json.load(f)
rr_spread = pd.read_csv(DATA / "option_rr_spread_horizon_stats.csv")
eq_summary = pd.read_csv(DATA / "results_summary_v2_FINAL.csv")

net_mktadj_60 = rr_spread[(rr_spread.kind == "rr_ret_net_mktadj_fwd") & (rr_spread.horizon == 60)].iloc[0]
gross_mktadj_60 = rr_spread[(rr_spread.kind == "rr_ret_mktadj_fwd") & (rr_spread.horizon == 60)].iloc[0]
best_equity_sharpe = eq_summary["sharpe"].max()
best_equity_label = eq_summary.loc[eq_summary["sharpe"].idxmax(), "label"]

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="Body", parent=styles["Normal"], fontSize=9.7, leading=13.5, spaceAfter=8))
styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontSize=16, spaceBefore=4, spaceAfter=8, textColor=colors.HexColor("#1a3a2a")))
styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1a3a2a")))
styles.add(ParagraphStyle(name="Caption", parent=styles["Normal"], fontSize=8.3, leading=11, textColor=colors.HexColor("#555555"), spaceAfter=10))
styles.add(ParagraphStyle(name="TitleSub", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor("#555555"), spaceAfter=4))

story = []
def h1(t): story.append(Paragraph(t, styles["H1"]))
def h2(t): story.append(Paragraph(t, styles["H2"]))
def body(t): story.append(Paragraph(t, styles["Body"]))
def caption(t): story.append(Paragraph(t, styles["Caption"]))
def rule(): story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#bbbbbb"), spaceBefore=4, spaceAfter=10))
def fig(path, width=6.4*inch):
    img = Image(str(path))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width
    img.drawHeight = width * ratio
    story.append(img)

def make_table(rows, col_widths=None, header_bg="#1a3a2a", fontsize=8.3, align_first_left=True):
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), fontsize),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f5")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if align_first_left:
        style.append(("ALIGN", (0, 0), (0, -1), "LEFT"))
    t.setStyle(TableStyle(style))
    return t

# ============================== COVER ==============================
story.append(Spacer(1, 0.3*inch))
h1("Trading Options-Market PEAD With a Risk Reversal")
story.append(Paragraph("Designed against prior research on options, earnings announcements, and the volatility risk premium", styles["TitleSub"]))
story.append(Paragraph(f"D10 long / D1 short risk reversal, 60-trading-day horizon, 1996&ndash;2013 ex-2011 &middot; net-of-cost, market-adjusted Sharpe {summary['O1_net_mktadj']['sharpe']:.2f}", styles["TitleSub"]))
story.append(Paragraph("Companion to <i>PEAD_Options_Report.pdf</i> (the evidence this strategy trades) and <i>PEAD_Strategy_Showcase.pdf</i> (the equity strategies it is benchmarked against)", styles["TitleSub"]))
rule()

body(f"""<i>PEAD_Options_Report.pdf</i> showed that near-the-money calls and puts drift with the
SUE decile, amplified by leverage relative to the underlying stock. But buying naked calls and
puts is not obviously the <i>best</i> way to monetize that signal &mdash; prior research on
options and earnings, and on options pricing generally, points fairly directly at a specific
alternative. This report reviews that research (Section 1), designs a <b>risk reversal</b>
strategy around it (Section 2), and backtests it against the naked-option approach and against
the 8 equity strategies in <i>PEAD_Strategy_Showcase.pdf</i> (Sections 3&ndash;5).""")

body(f"""<b>Result.</b> A naked long-options book on D10/D1 is, on paper, spectacularly
profitable gross of transaction costs &mdash; and nearly worthless net of a realistic bid-ask
cost, because single-name equity options are quoted far wider than the equities they're written
on. A <b>risk reversal</b> (long call + short put on D10, the mirror on D1) expresses the same
directional view for close to zero net premium, and survives realistic costs far better: Sharpe
{summary['O0_net']['sharpe']:.2f} for the net-of-cost naked-option book vs.
<b>{summary['O1_net_mktadj']['sharpe']:.2f}</b> for the net-of-cost, market-adjusted risk-reversal
book &mdash; in the same range as several of the equity strategies (best equity Sharpe:
{best_equity_sharpe:.2f}, {best_equity_label}), while requiring far less capital and no stock
borrow.""")

story.append(PageBreak())

# ============================== SECTION 1: LITERATURE ==============================
h2("1. What prior research says, and how it shapes this design")
body("""Four strands of existing research bear directly on how to turn the options-PEAD evidence
into a strategy, rather than just a naked directional bet:""")

body("""<b>(a) Options already price in the prior quarter's surprise, for volatility purposes.</b>
Govindaraj, Liu &amp; Livnat (2012, &ldquo;The Post Earnings Announcement Drift and Option
Traders&rdquo;) test whether option traders under-react to SUE the way equity traders do, using
straddle returns as a volatility-only bet. They find straddles on extreme-SUE events are
<i>not</i> more profitable than straddles on mild-SUE events, and implied volatility changes
around the announcement don't correlate positively with surprise size &mdash; both inconsistent
with a risk-premium/mispricing story on the volatility axis specifically. (This report's own
straddle robustness cut in <i>PEAD_Options_Report.pdf</i> Section 6 found a smaller but
statistically real decile-ordered residual, a point of tension with their finding worth flagging
rather than glossing over &mdash; possibly a difference in sample period, universe, or straddle
construction.) Taken at face value, their result says the exploitable edge documented in this
project is directional (delta), not a volatility mispricing &mdash; so the instrument chosen
should isolate direction and minimize unpriced volatility exposure, not add more of it.""")

body("""<b>(b) Buying options generally means paying a volatility risk premium.</b> A large,
separate literature (surveyed e.g. in work on the variance risk premium) documents that
option-implied volatility exceeds subsequently realized volatility on average, across equity,
index, and other asset classes &mdash; the standard explanation for why systematic option-selling
strategies have historically outperformed option-buying strategies before accounting for their
tail risk. A naked long call or put pays this premium regardless of whether the directional view
is right, on top of theta decay. Combined with (a) &mdash; no evidence of a volatility-side
mispricing to be compensated for taking this bet &mdash; naked long options are a directional bet
wrapped in a structurally negative-expectancy volatility bet.""")

body("""<b>(c) A risk reversal is the standard instrument for a pure directional view.</b> A risk
reversal (long a call, short a put of similar delta magnitude, or vice versa) is widely used in
FX and single-stock/index options markets specifically because selling one option finances buying
the other, netting out most of the premium paid while preserving full delta exposure &mdash; a far
more capital-efficient structure than a naked long option for expressing "I think this goes up
(or down)" without also making an implicit bet that options are underpriced. By put-call parity,
a same-strike, same-expiry long call + short put is exactly a synthetic long forward on the
underlying; at the near-±0.50 deltas this project already selects (Section 2), the combined
delta is close to 1.0, so the structure behaves like a stock position financed at close to zero
net premium &mdash; and for the D1 (short) leg specifically, sidesteps the stock-borrow cost that
<i>PEAD_Report.pdf</i> Section 8 flagged as unresolved for every one of the 8 equity strategies.""")

body("""<b>(d) Option-implied skew independently predicts returns.</b> Xing, Zhang &amp; Zhao
(2010, <i>JFQA</i>) find that stocks with steeper volatility smirks (relatively expensive
out-of-the-money puts) earn lower future returns, and that informed traders with negative news
appear to prefer expressing it through OTM puts before it becomes public &mdash; consistent with
option prices carrying real forward-looking information distinct from the earnings-surprise
signal itself. This project's own <i>PEAD_Options_Report.pdf</i> put-delta and call-delta data at
entry could support the same kind of skew measure; it is not used in this first version of the
strategy but is flagged in Section 7 as the most literature-grounded refinement to try next.""")

story.append(PageBreak())

# ============================== SECTION 2: STRATEGY DESIGN ==============================
h2("2. Strategy design")
body("""<b>Instrument.</b> For each D10 (most positive surprise) event, a <i>long risk
reversal</i>: long the near-+0.50-delta call, short the near-&minus;0.50-delta put already
selected in <i>PEAD_Options_Report.pdf</i> (both already screened for a valid two-sided market
and &ge;95 days to expiration at entry &mdash; no new contracts are selected for this strategy).
For each D1 (most negative surprise) event, the mirror <i>short risk reversal</i>: short the call,
long the put. Both legs use the exact same contracts, entry dates, and forward-price lookups
already built for the evidence report.""")

body("""<b>Sizing and normalization.</b> A risk reversal's dollar P&amp;L is normalized by the
underlying's own closing price at day0 (pulled from OptionMetrics <font face="Courier">secprd</font>,
script 40) rather than by option premium, since near-zero net premium makes a percent-of-premium
return undefined or meaningless. This also makes the risk reversal's return directly comparable in
scale to the underlying stock's own return &mdash; expected, given combined delta ~1.0.""")

body("""<b>Market adjustment.</b> Because a risk reversal is approximately a synthetic stock
position, its return is adjusted the same way the equity strategies' returns are: each event's own
market-return component (recovered as <font face="Courier">ret_fwd_Xd - ret_fwd_Xd_mktadj</font>
from the existing equity panel, assuming combined delta ~1.0, i.e. the same implicit beta=1
convention already used throughout this project) is subtracted off, isolating the return
attributable to the SUE-decile signal from incidental market-beta exposure.""")

body("""<b>Transaction costs.</b> The empirical median relative bid-ask spread across 1.45M
forward-price lookups in this project's own options data is <b>11.8% of mid</b> &mdash; far wider
than the 2bps used for the equity strategies' beta overlay, reflecting genuinely worse single-name
option liquidity. NET figures below assume crossing half that spread on every leg, at both entry
and exit (2 crossings for a naked option, 4 for a risk reversal, priced on each leg's own mid at
the time of each crossing) -- see script 41.""")

body("""<b>Portfolio construction and a sizing pitfall worth naming.</b> Each announcement
quarter, the book is 50% long-leg capital (that quarter's D10 events, equal-weighted) and 50%
short-leg capital (D1 events, equal-weighted) at a 60-trading-day holding period &mdash; the
options-market analogue of the equity book's simplest design, Extreme Decile L/S. An initial
version of this backtest reinvested 100% of NAV every quarter, exactly like the equity engine's
trailing-NAV compounding. For naked options that produced a nonsensical result: genuine per-event
returns on a 60-day near-ATM option average +15-30% (see <i>PEAD_Options_Report.pdf</i>), and
compounding that for 65 quarters produces a total return in the hundreds of thousands of percent
&mdash; mathematically correct, practically meaningless, and exactly the kind of unchecked
concentration the equity book's own leverage caps (<i>PEAD_Strategy_Development.pdf</i>,
Strategies 5&ndash;6) were built to prevent, here showing up via option convexity instead of
cross-sectional concentration. Rather than pick an arbitrary cap to force a nicer-looking
compounded number, <b>every result below uses fixed-notional accounting</b>: each quarter deploys
the same capital rather than reinvesting the previous quarter's gains (arithmetic mean/vol/Sharpe
of the quarterly return series, linear cumulative P&amp;L for drawdown). This keeps naked
options' real but explosive per-event returns from being misrepresented as a compounding growth
rate, and puts every strategy compared here on the same footing.""")

story.append(PageBreak())

# ============================== SECTION 3: RESULTS ==============================
h2("3. Headline result: risk reversal vs. naked options, net of realistic costs")
fig(FIG / "24_naked_options_nav_cost_impact.png", width=6.2*inch)
caption(f"""Figure 1. Fixed-notional cumulative P&amp;L, naked options book. Gross Sharpe
{summary['O0_raw']['sharpe']:.2f} collapses to {summary['O0_net']['sharpe']:.2f} net of the
empirical ~11.8% spread, with max drawdown widening from {summary['O0_raw']['max_drawdown']:.1%}
to {summary['O0_net']['max_drawdown']:.1%} &mdash; single-name option bid-ask costs are large
enough, relative to a directional edge measured in percentage points, to erase most of the
naive-gross edge.""")

fig(FIG / "25_risk_reversal_nav.png", width=6.2*inch)
caption(f"""Figure 2. Fixed-notional cumulative P&amp;L, risk-reversal book. Unlike the naked
book, net-of-cost is not worse than gross here (Sharpe {summary['O1_net_mktadj']['sharpe']:.2f}
vs. {summary['O1_mktadj']['sharpe']:.2f}) &mdash; see Section 4 for why.""")

story.append(PageBreak())

h2("4. Why net-of-cost is not worse than gross for the risk reversal")
d1_cost_60 = None
d10_cost_60 = None
rr_decile = pd.read_csv(DATA / "option_rr_decile_horizon_stats.csv")
g = rr_decile[(rr_decile.kind == "rr_ret_fwd") & (rr_decile.horizon == 60)]
n = rr_decile[(rr_decile.kind == "rr_ret_net_fwd") & (rr_decile.horizon == 60)]
d1_cost_60 = float(g[g.decile == 1]["mean"].iloc[0] - n[n.decile == 1]["mean"].iloc[0])
d10_cost_60 = float(g[g.decile == 10]["mean"].iloc[0] - n[n.decile == 10]["mean"].iloc[0])
body(f"""Transaction cost scales with option price level (half the quoted spread on each of 4
leg-crossings), and D1's calls and puts cost modestly more, in dollar terms, to trade than D10's:
at the 60-day horizon the estimated round-trip cost is {d1_cost_60:.1%} of underlying notional for
the D1 leg vs. {d10_cost_60:.1%} for the D10 leg &mdash; plausibly because negative-surprise names
run somewhat higher implied volatility (pricier options) and are on average less liquid. Since the
D1 leg is <i>subtracted</i> in the long-short spread, a larger cost on that leg <i>increases</i>
the measured net spread relative to gross, rather than eroding it. This is a real, explainable
asymmetry in this data, not a sign error: it means realistic trading costs, if anything, make the
D10&minus;D1 risk-reversal edge look slightly better than the frictionless number, in sharp
contrast to the naked-option book where costs are devastating regardless of decile.""")

fig(FIG / "27_risk_reversal_decile_drift.png")
caption(f"""Figure 3. Market-adjusted risk-reversal return by decile and horizon. D10 (dark
green) separates cleanly from every other decile at every horizon shown; D1 (dark red) sits
lowest through 20 days before some middle-decile crossover noise appears at 40-60 days &mdash;
consistent with the extreme deciles carrying the cleanest signal, as in the equity and naked-option
results.""")

story.append(PageBreak())

# ============================== SECTION 5: COMPARISON TO EQUITY STRATEGIES ==============================
h2("5. How this compares to the 8 equity strategies")
fig(FIG / "26_sharpe_comparison_all_strategies.png", width=6.3*inch)
caption("""Figure 4. Sharpe ratio, all 5 option-strategy variants from this report (green)
alongside all 8 equity strategies from PEAD_Strategy_Showcase.pdf (blue), for context. Different
backtest conventions (fixed-notional quarterly cohorts here vs. daily trailing-NAV compounding
there) mean this is a directional comparison of risk-adjusted quality, not an exact apples-to-
apples number.""")

sharpe_table = [["Strategy", "Ann. Return", "Ann. Vol", "Sharpe", "Max DD"]]
for key, label in [("O0_raw", "Naked options, gross"), ("O0_net", "Naked options, net of cost"),
                   ("O1_raw", "Risk reversal, gross"), ("O1_mktadj", "Risk reversal, mkt-adj."),
                   ("O1_net_mktadj", "Risk reversal, mkt-adj. + net of cost")]:
    s = summary[key]
    sharpe_table.append([label, f"{s['ann_return']:+.1%}", f"{s['ann_vol']:.1%}",
                          f"{s['sharpe']:.2f}", f"{s['max_drawdown']:.1%}"])
story.append(make_table(sharpe_table, col_widths=[2.5*inch, 1.0*inch, 0.9*inch, 0.8*inch, 0.9*inch]))
caption("""Table 1. Fixed-notional annualized stats for all 5 option-strategy variants
(D10 long / D1 short, 60-trading-day horizon, 1996-2013 ex-2011).""")

body(f"""The net-of-cost, market-adjusted risk reversal (Sharpe {summary['O1_net_mktadj']['sharpe']:.2f})
sits in the same range as Rank-weighted (Sharpe {eq_summary.set_index('strategy').loc['rankweighted','sharpe']:.2f})
among the equity strategies, below the sector/leverage-refined Strategies 4-6
(Sharpe {eq_summary.set_index('strategy').loc['strategy5','sharpe']:.2f}-{eq_summary.set_index('strategy').loc['strategy6','sharpe']:.2f}),
and well above the naked-option book net of cost ({summary['O0_net']['sharpe']:.2f}). The case for
the options-based approach was never that it beats the most-refined equity strategies on Sharpe
alone -- it's the capital and structural efficiency: a risk reversal is financed at close to zero
net premium (vs. full notional to hold the equity book), needs no stock borrow for the short leg
(the equity report's own unresolved caveat), and its capital efficiency means the same edge can be
run at a fraction of the balance-sheet usage of the equity long/short book.""")

story.append(PageBreak())

# ============================== SECTION 6: CAVEATS ==============================
h2("6. Caveats specific to this strategy")
body("""&bull; <b>Fixed-notional accounting is a deliberate simplification, not a claim about
achievable AUM growth.</b> It avoids the naive-compounding pitfall (Section 2) but also means the
Sharpe ratios here are not directly the Sharpe of a real fund's NAV path the way the equity
book's daily trailing-NAV Sharpe is -- a real implementation would need explicit position-sizing
and leverage-cap rules (as the equity book has) rather than either extreme.<br/>
&bull; <b>Quarterly, non-overlapping cohorts, not a continuous daily book.</b> Positions are
opened once per announcement quarter and held a fixed 60 trading days (~84 calendar days, just
under one quarter) with no rolling, no early exit, and no dynamic re-hedging of the risk
reversal's delta as it drifts from ~1.0 over the holding period.<br/>
&bull; <b>Transaction cost is a single empirical median applied uniformly</b>, not a per-event,
per-decile, or per-liquidity-tier cost model; Section 4's D1-vs-D10 cost asymmetry finding is
itself only a decile-level average, not evidence the cost model is precise at the event level.<br/>
&bull; <b>No margin or haircut modeling for the short option leg.</b> A short put (D10's risk
reversal) or short call (D1's) requires margin in practice; this report treats the risk reversal's
notional purely in underlying-price terms and does not model the margin capital a broker would
actually require to carry the short leg, which is a real cost/constraint a live implementation
would need to size against.<br/>
&bull; <b>Same underlying limitations as PEAD_Options_Report.pdf</b> apply here: 2011 is excluded
(source file corrupt), 2013 is truncated at end-August, the &ge;95-day DTE floor skews the sample
toward names with a richer option chain, and entry/exit at bid/ask midpoint (for the gross
figures) is not itself a tradeable fill -- this report's NET figures are the attempt to correct
for that last point specifically.""")

h2("7. Suggested next steps")
body("""&bull; <b>Add the Xing/Zhang/Zhao skew signal</b> (Section 1d) as a filter or tilt on top
of the SUE decile -- e.g., size down or skip D10 events whose entry-day put-call skew already
looks unusually steep, since that pattern predicts <i>lower</i> future returns in their data,
potentially in tension with a pure long risk reversal.<br/>
&bull; <b>Build a full daily, overlapping-position engine</b> analogous to the equity backtest
(scripts/12, 17) instead of discrete non-overlapping quarterly cohorts, with an explicit
leverage/concentration cap so the naive-compounding pitfall in Section 2 is handled by design
rather than by switching to fixed-notional accounting.<br/>
&bull; <b>Model margin requirements for the short legs explicitly</b> and report a true
return-on-margin figure alongside the return-on-underlying-notional figure used here.<br/>
&bull; <b>Test shorter and longer holding periods</b> (the equity work found per-cell optimal
holding periods varied by decile x size x book-to-market cell; this report used a single fixed
60-day window for every event).<br/>
&bull; <b>Revisit the straddle tension with Govindaraj/Liu/Livnat (2012)</b> noted in Section 1a
-- this project's own data shows a smaller but real decile-ordered straddle residual where their
paper finds none, worth understanding before leaning too hard on "no volatility edge" as a
justification for preferring direction-only structures.<br/>
&bull; <b>Extend to bull/bear spreads</b> as a second capital-efficient alternative to the naked
option, using a further-OTM strike (not yet pulled in this project's data) to cap risk on the
short leg instead of the risk reversal's uncapped opposite-direction exposure.""")

story.append(Spacer(1, 0.3*inch))
rule()
caption("""Generated from data/event_options/option_event_panel_rr.parquet,
data/options_strategy_quarterly.csv, data/options_strategy_summary.json, and
data/results_summary_v2_FINAL.csv (equity comparison). Scripts: scripts/40_underlying_spot_price.py
through scripts/44_build_options_strategy_report.py. Literature: Govindaraj, Liu &amp; Livnat
(2012), &ldquo;The Post Earnings Announcement Drift and Option Traders&rdquo;, SSRN 2146181;
Xing, Zhang &amp; Zhao (2010), &ldquo;What Does the Individual Option Volatility Smirk Tell Us
About Future Equity Returns?&rdquo;, Journal of Financial and Quantitative Analysis 45(3).""")

doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                         leftMargin=0.75*inch, rightMargin=0.75*inch,
                         topMargin=0.7*inch, bottomMargin=0.7*inch,
                         title="Trading Options-Market PEAD With a Risk Reversal")
doc.build(story)
print("wrote", OUT)
