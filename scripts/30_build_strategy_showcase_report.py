"""Assemble reports/PEAD_Strategy_Showcase.pdf -- performance of all 8 strategies.

This is the "here's what we built and how it performed" report: every strategy's
construction in plain language, its full-sample metrics, and the charts comparing
all 8 side by side. It assumes the reader already accepts that PEAD is a real,
tradeable effect (see PEAD_Report.pdf) and wants to know how well each way of
trading it actually did. For the story of *why* each strategy was built the way
it was, see PEAD_Strategy_Development.pdf.
"""
import pandas as pd
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, HRFlowable,
    KeepTogether
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIG = ROOT / "reports" / "figures"
OUT = ROOT / "reports" / "PEAD_Strategy_Showcase.pdf"

summary = pd.read_csv(DATA / "results_summary_v2_FINAL.csv").set_index("strategy")

def js(name):
    import json
    with open(DATA / f"backtest_v2_{name}_summary.json") as f:
        return json.load(f)

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="Body", parent=styles["Normal"], fontSize=9.7, leading=13.5, spaceAfter=8))
styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontSize=16, spaceBefore=4, spaceAfter=8, textColor=colors.HexColor("#1a3a2a")))
styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1a3a2a")))
styles.add(ParagraphStyle(name="H3", parent=styles["Heading3"], fontSize=10.8, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#2f4f3f")))
styles.add(ParagraphStyle(name="Caption", parent=styles["Normal"], fontSize=8.3, leading=11, textColor=colors.HexColor("#555555"), spaceAfter=10))
styles.add(ParagraphStyle(name="TitleSub", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor("#555555"), spaceAfter=4))

story = []
def h1(t): story.append(Paragraph(t, styles["H1"]))
def h2(t): story.append(Paragraph(t, styles["H2"]))
def h3(t): story.append(Paragraph(t, styles["H3"]))
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
h2("Overview: all 13 strategies side by side")
order = ["extreme", "rankweighted", "balanced", "strategy4", "strategy5", "strategy6",
         "strategy6_beta050", "strategy7", "strategy6_beta050_voltarget",
         "strategy6_voltarget_alpha_staticoverlay", "strategy6_fully_voltarget",
         "strategy8", "strategy8_beta050_voltarget"]
labels = ["1. Extreme decile L/S", "2. Rank-weighted", "3. Balanced (size-neutral)",
          "4. Trimmed/tilted, priority cap", "5. + Sector-neutral", "6. + Leverage cap 2.5x",
          "6+beta. + 0.5x market beta overlay", "7. Unconstrained net exposure",
          "6+beta-vt. Vol-targeted overlay", "6-vt-alpha. Vol-targeted alpha only",
          "6-fully-vt. Fully vol-managed",
          "8. + Dispersion risk filter", "8+beta-vt. Strategy 8 + overlay"]

overview = [["Strategy", "Ann. Return", "Ann. Vol", "Sharpe", "Max Drawdown", "Final NAV\n($10M start)"]]
for key, label in zip(order, labels):
    r = summary.loc[key]
    overview.append([label, f"{r['ann_return']*100:.2f}%", f"{r['ann_vol']*100:.2f}%",
                      f"{r['sharpe']:.2f}", f"{r['max_drawdown']*100:.1f}%", f"${r['final_nav']/1e6:.1f}M"])
story.append(make_table(overview, col_widths=[2.3*inch, 0.85*inch, 0.75*inch, 0.65*inch, 0.9*inch, 0.9*inch], fontsize=8.3))
caption("""Table 1. All figures 1996-2013, trailing-NAV compounding, net of transaction costs and
liquidity caps, from a $10,000,000 starting capital base. Strategy 5 has the best Sharpe and
shallowest drawdown among the original 8; Strategy 6-fully-vt now has the highest absolute return
(see the "Extension" section below); Strategy 8 (second-to-last row, a separate follow-up research
pass -- see "Extension 2" below) has the shallowest drawdown of any strategy in this table at
-10.8%, with a Sharpe matching the best of the original 8 -- see the Recommendation section at the
end of this report.""")

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

# ============================== EXTENSION: VOL-TARGETED RISK MANAGEMENT ==============================
h2("Extension: volatility-targeted risk management")
body("""A follow-up research pass asked whether Strategy 6 + 0.5x beta overlay's return could be
pushed higher without simply trading away Sharpe and drawdown -- i.e. without just raising the
overlay's size or the alpha book's leverage, both of which were already shown (the leverage-cap
sweep behind Strategy 6, and a base_unit_fraction sizing sweep re-run on this specific book) to
trade return for materially worse Sharpe and drawdown past their respective saturation points, not
to be a free improvement. The alternative tested here is <b>volatility targeting</b>
(Moreira &amp; Muir 2017-style volatility-managed exposure): instead of holding a <i>fixed</i>
notional every quarter, scale it by (a target volatility level) / (trailing realized volatility),
so the position is sized <i>down</i> in turbulent quarters and <i>up</i> in calm ones, clipped to
[0.5x, 2.0x] of its baseline sizing. The target level is calibrated to the sample's own average
trailing volatility, so average exposure stays comparable to the static baseline -- this keeps the
comparison a genuine risk-timing question, not a comparison against a different average market
exposure.""")

fig(FIG / "18_voltarget_nav_curves.png")
caption("""Figure 5. NAV curves for the Strategy 6 family: pure alpha, the current static 0.5x
overlay, and the three vol-targeted variants. The fully vol-managed variant (green, thickest line)
tracks above the static overlay for most of the sample and, critically, pulls up out of the
2008-2009 drawdown faster.""")

fig(FIG / "19_voltarget_comparison_bars.png")
caption("""Figure 6. Unlike raising the overlay's static size or the alpha book's leverage (both
shown elsewhere in this project to trade return for worse Sharpe/drawdown), vol-targeting improves
all three metrics at once for the fully-managed variant.""")

def profile_slim(num_label, name, key, construction, notes):
    h3(f"{num_label}. {name}")
    body(construction)
    r = summary.loc[key]
    rows = [["Metric", "Value"],
            ["Annualized return", f"{r['ann_return']*100:.2f}%"],
            ["Annualized volatility", f"{r['ann_vol']*100:.2f}%"],
            ["Sharpe ratio", f"{r['sharpe']:.2f}"],
            ["Max drawdown", f"{r['max_drawdown']*100:.1f}%"]]
    t = make_table(rows, col_widths=[2.3*inch, 1.6*inch], fontsize=8.6, header_bg="#d4954a")
    story.append(KeepTogether([t, Spacer(1, 4), Paragraph(notes, styles["Caption"])]))
    story.append(Spacer(1, 10))

profile_slim("6+beta-vt", "Vol-targeted 0.5x beta overlay", "strategy6_beta050_voltarget",
    """Same construction as Strategy 6+beta -- the alpha book is untouched, a separate synthetic
    index position is held alongside it -- except the overlay's notional is scaled each quarter by
    trailing 63-day realized market volatility relative to its own full-sample average, instead of
    being pinned to a fixed 0.5x NAV every quarter.""",
    """Beats the static overlay on every metric: 11.00% return (vs. 10.08%), Sharpe 1.03 (vs.
    0.97), max drawdown -18.5% (vs. -23.2%). The overlay delevers heading into 2008 instead of
    holding a fixed market-sized position through it.""")

profile_slim("6-vt-alpha", "Vol-targeted alpha book, static overlay", "strategy6_voltarget_alpha_staticoverlay",
    """Isolates the other half of the idea: leave the beta overlay static, and instead scale the
    alpha book's own exposure each quarter by the same trailing-volatility logic, applied to the
    alpha book's own realized vol rather than the market's. (Approximation: this rescales the
    already-computed daily return series rather than re-running the position-level engine at a
    different size, so it does not reproduce exactly how the liquidity cap would respond, and
    charges no extra cost for the implied rebalancing beyond what's already embedded in the
    return series.)""",
    """Improves return (10.74%) and Sharpe (1.00) over the static baseline, but on its own makes
    the drawdown slightly <i>worse</i> (-24.4% vs. -23.2%) -- vol-targeting the alpha book alone
    apparently increases exposure at some points that coincide with the overlay's own worst
    stretch. This nuance is exactly why the combined variant below, not this one alone, is the
    one worth adopting.""")

profile_slim("6-fully-vt", "Fully vol-managed (vol-targeted alpha + overlay)", "strategy6_fully_voltarget",
    """Both halves combined: the alpha book and the beta overlay are each scaled quarterly by
    their own trailing-volatility signal (same [0.5x, 2.0x] clipping, same calibration-to-average
    discipline as the two variants above).""",
    """The best result of this research pass, and the new top-return recommendation: <b>11.58%
    return, Sharpe 1.06, max drawdown -17.9%</b> -- better than the static-overlay baseline on
    every dimension (10.08% / 0.97 / -23.2%), and resolving the drawdown regression seen in the
    alpha-only variant above. See <i>PEAD_Strategy_Development.pdf</i> Stage 7 for the full
    reasoning and the sizing-sweep diagnostic that ruled out simple leverage as the answer.""")

story.append(PageBreak())

# ============================== EXTENSION 2: DISPERSION RISK FILTER ==============================
h2('Extension 2: a dispersion-based risk filter (Strategy 8)')
body("""A second follow-up research pass asked whether any signal outside SUE itself -- genuinely
new information, not another way to size or overlay the existing signal -- could improve the
strategy. Three candidates were tested from data already on hand (no new pull): a second
earnings-surprise measure (<tt>sue_seasonal</tt>, a seasonal-random-walk expectation already
computed in this project's own data), analyst estimate revisions and dispersion (from IBES's own
consensus summary file, untouched until now), and a revenue surprise built from Compustat
quarterly revenue (also untouched until now). Each was tested the same rigorous way as the EAR and
SUE-reversal dead ends earlier in this project: a standalone Fama-MacBeth predictive check, then an
actual blended-weight trading test through the full position-level engine -- not just statistical
significance in isolation.""")
body("""Two of the three failed outright despite real literature precedent and, in
<tt>sue_seasonal</tt>'s case, standalone statistical significance (t=3.25): blending either one
into the strategy's weight construction made return, Sharpe, <i>and</i> drawdown monotonically
worse at every blend weight tested. Revenue surprise -- the best-precedented candidate on paper
(Jegadeesh &amp; Livnat 2006) -- was only marginally significant standalone (t=1.47) and likewise
did not survive the blend test. All three are documented negative results, in the same spirit as
the EAR and SUE-reversal dead ends.""")
body("""<b>Analyst estimate dispersion</b> produced a real but counterintuitive result. Standalone,
it has <i>no</i> directional predictive power at all -- a quintile sort of forward returns by
dispersion alone is flat (t=-0.43). But blending it into the trim/tilt weight construction at a
modest weight changes which low-conviction positions get trimmed: Sharpe stays in a ~1.28-1.32
plateau across blend weights 0.05-0.25 while max drawdown steadily improves from -17.3% to -9.4%.
A composition check (average size quintile, liquidity quintile, dollar volume) found this is
<i>not</i> a disguised size or liquidity filter -- those are essentially unchanged. The most
plausible mechanism: dispersion measures how much analysts disagree about the estimate a surprise
is being measured against, so a high-dispersion event's "surprise" is measured against a noisier
consensus and is more likely to be noise than real information. <b>This is a risk/variance filter,
not a new alpha signal</b> -- the same kind of finding as Strategy 5's sector-neutrality (better
risk-adjusted profile, not more expected return), and it is described that way deliberately rather
than oversold.""")

fig(FIG / "20_strategy8_nav_curves.png")
caption("""Figure 7. Strategy 8 (green) tracks below Strategy 6 in absolute NAV but with visibly
smaller pullbacks throughout, including 2008-2009. Strategy 8 + the vol-targeted overlay (orange)
sits below Strategy 6-fully-vt (red) for most of the sample.""")

fig(FIG / "21_strategy8_comparison_bars.png")
caption("""Figure 8. Strategy 8 gives up 0.36pp of return relative to Strategy 6 for a Sharpe that
matches or beats it and a drawdown less than two-thirds as deep. Combining it with the vol-targeted
overlay (rightmost bars) does not improve on Strategy 6-fully-vt.""")

profile_slim("8", "Pure alpha + dispersion risk filter", "strategy8",
    """Identical to Strategy 6's construction (trim=0.15, tilt_power=1.0, size+sector-neutral,
    priority-based leverage cap at 2.5x) except the conviction rank used for trim/tilt/sizing
    blends <tt>sue_rank_pct</tt> (weight 0.80) with a point-in-time-safe analyst-dispersion rank
    (weight 0.20, from IBES's own consensus summary, using the latest snapshot strictly before the
    announcement).""",
    """5.96% return, Sharpe 1.30, max drawdown -10.8% -- the shallowest drawdown of any strategy
    in this project, at a Sharpe matching Strategy 6's. A genuine alternative pure-alpha
    recommendation to Strategy 6, not a replacement for it: choose based on whether the extra
    0.5pp of Strategy 6's return is worth carrying a materially deeper drawdown.""")

profile_slim("8+beta-vt", "Strategy 8 + vol-targeted beta overlay", "strategy8_beta050_voltarget",
    """The natural next question: do Strategy 8's drawdown improvement and the vol-targeted
    overlay's drawdown improvement (Extension 1, above) compound when combined? Same overlay
    construction as Strategy 6+beta-vt, laid on top of Strategy 8's alpha book instead of
    Strategy 6's.""",
    """They do not compound -- they don't even hold up separately. 10.58% return, Sharpe 0.98, max
    drawdown -20.0%, worse than Strategy 6-fully-vt (11.58% / 1.06 / -17.9%) on every metric,
    despite Strategy 8 alone having a much shallower drawdown than Strategy 6 alone. Reported as
    tested: this combination is not recommended. Strategy 8's value is as a pure-alpha choice on
    its own, not as an ingredient to combine with the overlay.""")

story.append(PageBreak())

# ============================== RECOMMENDATION ==============================
h2("Recommendation")
body("""<b>For live consideration: Strategy 6 or Strategy 8 for pure alpha (choose based on
return-vs-drawdown preference), or Strategy 6-fully-vt if a higher, more market-like return is
preferred.</b> Every strategy in this project is deliberately built market-adjusted and (mostly)
dollar-neutral, which means it structurally forgoes the equity risk premium that makes up most of
a simple buy-and-hold return -- comparing its nominal return to the S&amp;P 500 directly is not
apples to apples. As an external sanity check, AQR's live Equity Market Neutral Fund (QMNNX)
returns 6.19% at 7.03% volatility (Sharpe 0.69) and also trails the S&amp;P 500 over the same
window -- that is what a professionally run market-neutral strategy is supposed to look like.
Strategy 6 already beats that real fund's Sharpe (1.30 vs. 0.69), and Strategy 8 beats it further
on drawdown.""")
body("""&bull; Choose <b>Strategy 6</b> for the highest pure-alpha return (6.49%, Sharpe 1.30, max
drawdown -16.6%), or <b>Strategy 8</b> if a shallower drawdown matters more than that last 0.5pp of
return (5.96%, Sharpe 1.30, max drawdown -10.8%, the shallowest in this project) -- both close to
zero market beta by construction.<br/>
&bull; Choose <b>Strategy 6-fully-vt</b> if a return closer to 10-15% is preferred: it remains the
best return-oriented choice after testing whether Strategy 8 could improve on it in combination
(it doesn't -- see Extension 2) -- beating the original static overlay on return (11.58% vs.
10.08%), Sharpe (1.06 vs. 0.97), <i>and</i> max drawdown (-17.9% vs. -23.2%) simultaneously.<br/>
&bull; The plain static <b>Strategy 6+beta</b>, <b>Strategy 7</b>, and <b>Strategy 8+beta-vt</b>
are superseded/not recommended: the vol-targeted overlay dominates the static one on every metric,
Strategy 7 adds market exposure in a way that is measurably less controllable than the explicit
overlay, and combining Strategy 8 with the overlay underperforms Strategy 6-fully-vt on every
metric despite Strategy 8's own strong standalone drawdown profile.""")

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
Scripts: scripts/14_daily_decay.py through scripts/26_strategy7_unconstrained_netexposure.py,
scripts/33_strategy6_beta_overlay_voltarget.py, scripts/34_strategy6_voltarget_combined.py,
scripts/35_strategy6_sizing_sweep.py, scripts/36_revision_dispersion_diagnostic.py,
scripts/37_revenue_surprise_diagnostic.py, scripts/38_sue_seasonal_diagnostic.py,
scripts/39_strategy8_dispersion_filter.py, scripts/40_strategy8_beta_overlay_voltarget.py,
scripts/29_v2_strategy_charts.py, scripts/30_build_strategy_showcase_report.py.""")

doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                         leftMargin=0.75*inch, rightMargin=0.75*inch,
                         topMargin=0.7*inch, bottomMargin=0.7*inch,
                         title="PEAD Strategy Showcase")
doc.build(story)
print("wrote", OUT)
