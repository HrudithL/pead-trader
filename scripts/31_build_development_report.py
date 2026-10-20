"""Assemble reports/PEAD_Strategy_Development.pdf -- the development story.

This report explains, in plain language, *why* each strategy was built the way it
was: what problem was found in the previous version, how it was diagnosed, and
what changed as a result. It is the narrative companion to PEAD_Strategy_Showcase.pdf
(which has the full performance numbers) and assumes the reader already accepts
PEAD_Report.pdf's finding that the underlying signal is real.
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
OUT = ROOT / "reports" / "PEAD_Strategy_Development.pdf"

summary = pd.read_csv(DATA / "results_summary_v2_FINAL.csv").set_index("strategy")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="Body", parent=styles["Normal"], fontSize=9.7, leading=13.5, spaceAfter=8))
styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontSize=16, spaceBefore=4, spaceAfter=8, textColor=colors.HexColor("#1a3a2a")))
styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1a3a2a")))
styles.add(ParagraphStyle(name="H3", parent=styles["Heading3"], fontSize=10.8, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#2f4f3f")))
styles.add(ParagraphStyle(name="Caption", parent=styles["Normal"], fontSize=8.3, leading=11, textColor=colors.HexColor("#555555"), spaceAfter=10))
styles.add(ParagraphStyle(name="TitleSub", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor("#555555"), spaceAfter=4))
styles.add(ParagraphStyle(name="Glossary", parent=styles["Normal"], fontSize=9.0, leading=12.5,
                           textColor=colors.HexColor("#1a3a2a"), backColor=colors.HexColor("#f0f4f1"),
                           borderColor=colors.HexColor("#c8d8cc"), borderWidth=0.6, borderPadding=8,
                           spaceAfter=10))

story = []
def h1(t): story.append(Paragraph(t, styles["H1"]))
def h2(t): story.append(Paragraph(t, styles["H2"]))
def h3(t): story.append(Paragraph(t, styles["H3"]))
def body(t): story.append(Paragraph(t, styles["Body"]))
def caption(t): story.append(Paragraph(t, styles["Caption"]))
def glossary(t): story.append(Paragraph(t, styles["Glossary"]))
def rule(): story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#bbbbbb"), spaceBefore=4, spaceAfter=10))
def fig(path, width=6.2*inch):
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
h1("From Signal to Strategy: The Development Story")
story.append(Paragraph("Why each of the 8 PEAD strategies was built, in the order it was built", styles["TitleSub"]))
story.append(Paragraph("Companion to <i>PEAD_Report.pdf</i> (evidence the effect exists) and <i>PEAD_Strategy_Showcase.pdf</i> (full performance numbers)", styles["TitleSub"]))
rule()

body("""<i>PEAD_Report.pdf</i> establishes that stocks with the most positive earnings surprises
keep drifting up, and stocks with the most negative surprises keep drifting down, for weeks after
the announcement. Knowing a pattern exists is not the same as knowing how to trade it well. This
report walks through, in order, how a simple first attempt at trading that pattern was tested,
found wanting in a specific and measurable way, and improved &mdash; eight times &mdash; into the
strategies compared in <i>PEAD_Strategy_Showcase.pdf</i>. Two dead ends are included as well,
because ruling something out is also a real result.""")

glossary("""<b>A few terms used throughout this report, in plain language:</b><br/>
&bull; <b>Long/short:</b> buying stocks you expect to go up (the "long" side) while simultaneously
selling borrowed stocks you expect to go down (the "short" side), so the strategy can profit
whether the whole market rises or falls.<br/>
&bull; <b>Dollar-neutral:</b> the long side and the short side are sized to the same total dollar
amount, so a plain market-wide move up or down affects both sides roughly equally and cancels out.<br/>
&bull; <b>Decile:</b> splitting all of a quarter's earnings events into 10 equal-sized groups,
ranked from most negative surprise (decile 1) to most positive (decile 10).<br/>
&bull; <b>Leverage cap:</b> a limit on the total dollar size of all positions combined, relative to
the strategy's own capital, so it can't take on unlimited risk.<br/>
&bull; <b>Liquidity cap:</b> a limit on any single position's size relative to how much of that
stock actually trades in a day, so the backtest never assumes a trade the real market couldn't
absorb.<br/>
&bull; <b>Sector/size-neutral:</b> spreading a strategy's weight evenly across industries (or firm
sizes) so its return isn't secretly driven by one industry or size bucket outperforming for
reasons unrelated to earnings surprises.<br/>
&bull; <b>Sharpe ratio:</b> return earned per unit of volatility (risk) taken on -- higher is
better risk-adjusted performance, independent of how large the raw return is.""")

story.append(PageBreak())

# ============================== STAGE 1: v1 baseline ==============================
h2("Stage 1: three first attempts (v1)")
body("""The first version of this project asked the most direct question possible: can the
decile-drift pattern be turned into positions and traded? Before building any strategy, one basic
question had to be answered first: <b>how long should a position actually be held?</b> Rather than
guess, the size+book-to-market-adjusted return was tracked out to 80, 100, 120, 150, and 180
trading days, and each decile's <i>marginal</i> return -- the extra return earned specifically
between two adjacent checkpoints -- was tracked to find where it flattens toward zero.""")

fig(FIG / "11_decay_curves.png", width=5.8*inch)
caption("""Figure 1. Marginal return per additional trading day of holding, by decile,
size+book-to-market adjusted. Every decile's marginal contribution is large and directional in
the first 5-10 days, then settles toward a small, noisy band close to zero by roughly day 20-40.
The extreme deciles (D1, D2, D9, D10) keep contributing a small but consistently signed amount out
to 80 days; the near-median deciles (D5, D6) flatten out almost immediately, consistent with their
weak, often statistically insignificant drift documented in <i>PEAD_Report.pdf</i>.""")

body("""From this, three holding-period buckets are used across every strategy in this project:
<b>extreme</b> deciles (1, 2, 9, 10) hold 80 trading days; <b>shoulder</b> deciles (3, 4, 7, 8)
hold 40 trading days; <b>near-median</b> deciles (5, 6) hold 20 trading days, a short duration
reflecting that there is little measured edge there to wait for. This is a deliberate
simplification of a noisier per-decile decay estimate into three defensible, coarser bands rather
than chasing every noisy inflection point.""")

body("""With holding periods settled, three portfolio constructions were built and compared side
by side, all using a fixed-notional backtest (positions sized off a fixed starting capital, not a
compounding balance):""")
body("""&bull; <b>Extreme decile long/short</b> &mdash; long every D10 event, short every D1
event, the simplest possible framing.<br/>
&bull; <b>Rank-weighted</b> &mdash; every decile traded, with a position weight that scales
continuously with how extreme the surprise rank was, rather than throwing away 80% of the
cross-section at a hard cutoff.<br/>
&bull; <b>Balanced</b> &mdash; the extreme-decile trade, but with each leg rebalanced across firm
size quintiles so neither leg is secretly dominated by one size bucket.""")
body("""All three were profitable net of transaction costs and liquidity limits. Rank-weighted had
the better Sharpe ratio (it diversifies across far more positions), but the lower raw return of
the three &mdash; a tradeoff, not a clean win, and the first real question the project needed to
answer.""")

# ============================== STAGE 2: diagnosing rank-weighted, Strategy 4 ==============================
h2("Stage 2: why did rank-weighted trade Sharpe for return? -> Strategy 4")
body("""Rather than accept the tradeoff, the project measured <i>why</i> it existed. Rank-weighted
trades roughly 4x as many positions as the extreme-decile strategies (it holds a small position in
every decile, not just the top and bottom). That volume ran the strategy into two real capacity
constraints: the <b>liquidity cap bound on 99% of its positions</b> (nearly every position wanted
to be larger than the 1% of daily trading volume it was allowed), and the <b>leverage cap bound in
70% of its quarters</b>. Worse, the old leverage cap trimmed every position down by the same
percentage when it bound &mdash; shaving the strategy's highest-conviction bets (the most extreme
surprises) by exactly as much as its weakest, lowest-conviction ones.""")

body("""<b>Strategy 4</b> addressed this directly, not by tuning a parameter but by changing the
construction: <b>trim</b> the low-conviction middle of the SUE-rank distribution out entirely
(don't even hold small positions in near-median events), <b>tilt</b> remaining weight toward the
extremes, keep size-neutrality, and replace proportional cap-trimming with <b>priority-based</b>
trimming &mdash; when the leverage cap binds, the lowest-conviction positions still in the book are
dropped first, protecting the strongest convictions.""")
body("""Result: the first strategy to improve <i>both</i> return and Sharpe over the v1 baseline at
the same time, rather than trading one for the other.""")

story.append(PageBreak())

# ============================== STAGE 3: sector diagnostic, Strategy 5 ==============================
h2("Stage 3: an unpriced risk hiding in Strategy 4 -> Strategy 5")
body("""With Strategy 4 working, the project ran a diagnostic most backtests skip: does the book's
industry composition drift away from the overall market's, in a way that has nothing to do with
earnings surprises? It did. In some quarters, Strategy 4's book had up to <b>29% of its total
gross weight concentrated in a single Fama-French industry sector</b>, versus roughly 8.3% if
weight were spread evenly across all 12 sectors.""")

body("""That concentration is a real, uncompensated risk: if that one sector has a bad quarter for
reasons unrelated to earnings surprises, the strategy takes the hit regardless of whether its PEAD
signal was right. <b>Strategy 5</b> adds sector-neutrality on top of the existing size-neutrality
&mdash; redistributing weight within each sector the same way it was already being redistributed
within each size quintile.""")
body("""Result: Strategy 5 has a <i>lower</i> raw return than Strategy 4 (4.06% vs. 5.26%), but the
<b>best Sharpe ratio (1.33) and shallowest max drawdown (-10.8%) of all 8 strategies built</b>.
This is the clearest example in the whole project of a decision that traded some return for a
larger reduction in un-earned risk &mdash; a good trade on a risk-adjusted basis, and it became the
foundation every later strategy builds on.""")

# ============================== STAGE 4: leverage sweep, Strategy 6 ==============================
h2("Stage 4: was the leverage cap still doing anything useful? -> Strategy 6")
body("""Strategy 5's leverage cap (1.5x gross exposure relative to capital) had originally been set
as a conservative guard against exactly the kind of concentration risk Stage 3 just removed. With
that risk gone, was the cap still protecting against anything, or just leaving return on the
table? The project swept the cap from 1.5x up through 10x and measured what happened to Sharpe and
return at each level.""")

fig(FIG / "16_leverage_sweep.png", width=5.6*inch)
caption("""Figure 1. Both Sharpe ratio and annualized return flatten out completely by around
2.0-2.5x leverage (dashed line) -- past that point, raising the cap further changes nothing,
because the strategy's own position-level and liquidity limits are what's actually constraining
it, not the leverage cap. Below that point, the cap was costing real return for no risk-reduction
benefit.""")

body("""<b>Strategy 6</b> raises the leverage cap to 2.5x, the point where it stops binding
entirely. Result: both return (5.08% -> 6.49%, at the same base position-sizing parameter) and
Sharpe improved versus Strategy 5, because there was no concentration risk left for the tighter
cap to be protecting against. Strategy 6 is the strategy this project recommends as the pure-alpha
core for live consideration.""")

story.append(PageBreak())

# ============================== STAGE 5: closing the return gap ==============================
h2("Stage 5: Strategy 6 still trails the S&amp;P 500 -- is that a problem?")
body("""Strategy 6 earns 6.49% a year. The S&amp;P 500 earned roughly double that over the same
window. Before treating that as a shortfall, it's worth being precise about what's being compared.
Every strategy in this project is deliberately built market-adjusted and dollar-neutral, which
means it structurally gives up the equity risk premium that makes up most of a simple buy-and-hold
return &mdash; by design, not by accident. As an external sanity check, AQR's live Equity Market
Neutral Fund (QMNNX) returns about 6.19% a year at similar volatility and Sharpe (0.69), and it
<i>also</i> trails the S&amp;P 500 by a wide margin &mdash; that is what a professionally run
market-neutral strategy is supposed to look like. Strategy 6 already beats that real fund's Sharpe
(1.30 vs. 0.69).""")
body("""Still, the project treated "can we responsibly close some of that gap" as a real research
question, and tested two different ways to do it, guided by how institutions actually approach
this in practice: blend the alpha into a multi-factor book and add market exposure back as a
<i>separate</i> position on top of an untouched alpha book (a "130/30"-style structure), rather
than distorting the alpha signal itself to let beta leak in.""")

h3("6a. Strategy 6 + 0.5x market beta overlay -- the institutional way to add exposure")
body("""Strategy 6's alpha book is left completely untouched. A separate, synthetic market-index
position sized to 0.5x the strategy's own trailing NAV is held alongside it, rebalanced on the
same quarterly cadence with its own small transaction cost. Result: <b>10.08% return</b>, squarely
inside a 10-15% equity-like target band, with a meaningfully shallower drawdown and better Sharpe
than simply holding the market outright over the same period. (This static-sizing design is later
superseded by a vol-targeted version of the same idea -- see Stage 7.)""")

h3("6b. Strategy 7 -- the other way, and why it's a cautionary result")
body("""The alternative way to let some market exposure through is to stop forcing the long leg's
and short leg's dollar totals to match every quarter (Strategy 5/6's neutralization does this by
construction). Removing that constraint required switching from market-adjusted to <b>raw</b>
returns, because market-adjusting would have silently cancelled out the very net exposure this
test was trying to let through.""")
body("""The result is the most important lesson of this whole project: despite averaging only
-5.5% net exposure as a fraction of gross (essentially flat, by that one measure), Strategy 7's
realized correlation to the market came out at <b>0.52</b> &mdash; far higher than the small,
controlled net-dollar tilt the strategy appeared to be taking. <b>Dollar-neutral is not the same
thing as beta-neutral.</b> Positive- and negative-surprise firms structurally differ in average
market beta, so holding equal dollar amounts long and short doesn't cancel market risk the way
market-adjusting the underlying returns does. Strategy 7 is kept in the repository, fully
documented and runnable, specifically because it demonstrates this distinction &mdash; not because
it is a recommended design.""")

story.append(PageBreak())

# ============================== STAGE 6: negative results ==============================
h2("Two deliberate dead ends")
body("""Not every research thread in this project improved the strategy, and both of the following
are documented rather than quietly dropped, because ruling something out is itself useful
information for anyone continuing this work.""")

h3("Dead end 1: the announcement-day return doesn't add information")
body("""Question: does a firm's own price reaction on the announcement day itself (its
"announcement-window return," or EAR) predict anything <i>beyond</i> what the earnings-surprise
number (SUE) already predicts? An early version of this test found a strong, ever-increasing
Sharpe as more weight was put on EAR &mdash; which turned out to be a bug: it used the return from
day 0 to day 0+1, which mechanically overlaps with a position's own first day of held profit,
since positions start accruing the day <i>after</i> entry. Fixed to use only day 0's own,
already-realized return, the result flattens out entirely: Sharpe stays in a narrow ~1.00-1.06
band regardless of how much weight is put on EAR. Not incorporated into any numbered strategy.""")

h3("Dead end 2: earnings surprises don't reverse the way the literature says they should")
body("""A well-known finding (Bernard &amp; Thomas, 1990) is that a firm's own earnings surprise
tends to <i>reverse</i> about four fiscal quarters later (they report roughly -0.24
autocorrelation) &mdash; a firm that beat big this quarter tends to miss next-year's comparable
quarter, and vice versa. If true in this data, it would suggest a tradeable "fade your own past
surprise" signal.""")

fig(FIG / "17_sue_reversal.png", width=5.4*inch)
caption("""Figure 2. Average SUE today, grouped by each firm's own SUE decile four quarters
earlier. If reversal were present, this line would slope <i>downward</i> (high past SUE
predicting low current SUE). It slopes upward instead: firms with the highest surprise four
quarters ago still average the highest surprise now.""")

body("""In this data, the lag-4 autocorrelation is <b>+0.05</b> &mdash; the opposite sign from the
literature, and the decile breakdown confirms it's not noise: it's clean <b>persistence</b>, not
reversal. This is why none of Strategies 4-7 attempt to fade a firm's own earnings-surprise
history.""")

story.append(PageBreak())

# ============================== STAGE 7: VOL-TARGETED RISK MANAGEMENT ==============================
h2("Stage 7: pushing return further -- why leverage doesn't work, and what does")
body("""With Strategy 6+beta established as the return-focused recommendation, the natural next
question was whether its 10.08% return could be pushed meaningfully higher while keeping Sharpe
and drawdown in a similar range. Two of the obvious levers were tested first, precisely because
they're the ones people reach for first, and both turned out to already be accounted for or to not
be free:""")
body("""&bull; <b>Raising the alpha book's own leverage further</b> was already tested in Stage 4:
the cap sweep showed return and Sharpe both flatten out completely by about 2.0-2.5x, because the
book's own diversification (not the cap) is what's actually limiting it. Re-running a
base_unit_fraction sizing sweep directly on Strategy 6's own sector+size-neutral book (rather than
the pre-neutralization signal the original sizing sweep used) confirmed this holds here too: a
modest elbow exists around base_unit_fraction=0.004 (+0.8pp return over the current 0.003), but
Sharpe drops from 0.99 to 0.88 and max drawdown deepens from -17.3% to -20.8% to get it --
a real cost, not a free improvement, and a worse trade than the vol-targeting result below.<br/>
&bull; <b>Making the beta overlay's static 0.5x notional bigger</b> just mixes the portfolio
further toward simply holding the market outright (9.94% return, Sharpe 0.59, -55.5% max drawdown
over the same window) -- more of the overlay's size directly imports more of the market's own
worse risk-adjusted profile, not a free scaling.""")

body("""The alternative tested is <b>volatility targeting</b> (the same idea behind Moreira &amp;
Muir's 2017 "volatility-managed portfolios" result): instead of a fixed notional every quarter,
scale the position by (a target volatility level) / (that position's own trailing realized
volatility), so exposure shrinks automatically heading into a turbulent period and grows back in a
calm one. This was applied two ways: to the beta overlay's own notional (scaled by trailing market
volatility), and separately to the alpha book's own notional (scaled by the alpha book's own
trailing volatility) -- both using a 63-trading-day trailing window, clipped to [0.5x, 2.0x], with
the target level calibrated to the sample's own average trailing volatility so the AVERAGE exposure
stays comparable to the static design (a genuine risk-timing comparison, not a comparison against
a different average market exposure).""")

fig(FIG / "18_voltarget_nav_curves.png", width=5.6*inch)
caption("""Figure 3. The fully vol-managed variant (thick green line) tracks above the static
overlay for most of the sample and recovers from the 2008-2009 drawdown faster -- the position
had already been scaled down heading into it.""")

body("""Vol-targeting the overlay alone improved every metric versus the static design: 11.00%
return (vs. 10.08%), Sharpe 1.03 (vs. 0.97), max drawdown -18.5% (vs. -23.2%). Vol-targeting the
alpha book alone told a more nuanced story: return and Sharpe both improved (10.74%, 1.00), but
max drawdown got slightly <i>worse</i> (-24.4%) -- a useful diagnostic in its own right, showing
that scaling the alpha book's exposure by its own trailing volatility isn't automatically
risk-reducing in isolation. Combining both -- <b>Strategy 6-fully-vt</b> -- resolved that
regression and produced the best result of this research pass: <b>11.58% return, Sharpe 1.06, max
drawdown -17.9%</b>, beating the static-overlay baseline on all three metrics simultaneously. This
supersedes the plain static overlay as the recommended way to reach a higher, more market-like
return -- see the Recommendation section of <i>PEAD_Strategy_Showcase.pdf</i> for the full
comparison table and per-variant profiles.""")

story.append(PageBreak())

# ============================== STAGE 8: NEW SIGNAL RESEARCH ==============================
h2("Stage 8: scoping a genuinely new signal, without a new data pull")
body("""Stage 7 established that pushing return further isn't about leverage or a bigger overlay.
The other lever discussed was <b>breadth</b>: the Fundamental Law of Active Management
(IR ~ IC x sqrt(breadth)) says raising the risk-adjusted return of the alpha book itself needs a
genuinely independent signal, not another way to size or overlay the existing one. An audit of the
raw data already pulled for this project (no new WRDS query) found three untouched candidates:
IBES analyst estimate revisions and dispersion (raw_wrds/ibes/statsumu_epsus.parquet -- IBES's own
consensus summary, never opened by any prior script), a Compustat quarterly revenue field
(fundq.parquet's saleq, sitting next to the book-equity field that <i>is</i> used), and
sue_seasonal (a second earnings-surprise measure, already computed, used until now only as an
appendix robustness cut).""")

body("""Each was tested the way this project already tests candidate signals (scripts/27's
two-part template): a standalone Fama-MacBeth predictive check first, then an actual blended
trading-weight test through the full position-level engine -- not treated as a pass on statistical
significance alone.""")

h3("Two more dead ends, despite real precedent")
body("""<b>sue_seasonal</b> is standalone significant (a top-bottom quintile spread with t=3.25 on
60-day forward returns) -- but blending it into the weight construction made return, Sharpe, and
drawdown monotonically <i>worse</i> at every blend weight tested. It measures the same earnings
event as sue_analyst through a different expectations model (correlation 0.30), and apparently
adds conflicting noise rather than complementary information once blended in.""")
body("""<b>Revenue surprise</b> (built the same seasonal-random-walk way as sue_seasonal, since no
analyst revenue estimates exist in this data pull) is the best-precedented candidate on paper --
Jegadeesh &amp; Livnat (2006) find revenue surprise adds incremental drift beyond EPS surprise --
but here it was only marginally significant standalone (t=1.47, and a double sort against SUE
decile showed no clean pattern) and, like sue_seasonal, made the blended strategy monotonically
worse. <b>Analyst revisions</b> (net up/down revisions from the same IBES file) fared similarly:
weak and unevenly-covered standalone, and a clean monotonic decline in the blend test. Both are
documented negative results, the same way EAR and SUE-reversal were.""")

h3("Dispersion: a risk filter, not a new alpha signal")
body("""<b>Analyst estimate dispersion</b> (the spread of analyst estimates around the consensus,
also from statsumu_epsus.parquet) produced a genuine but counterintuitive result. Standalone, a
quintile sort of forward returns by dispersion is completely flat (t=-0.43) -- it has no
directional predictive power on its own, unlike every other candidate tested. Yet blending it into
the trim/tilt weight construction at a modest weight (0.05-0.25) held Sharpe in a ~1.28-1.32
plateau while max drawdown steadily improved from -17.3% to -9.4%.""")
body("""Before trusting that, a composition check compared the average size quintile, liquidity
quintile, and dollar volume of the book at blend weight 0 versus 0.20: all three were essentially
unchanged, ruling out "disguised size/liquidity quality filter" as the explanation. The most
plausible mechanism instead: dispersion measures how much analysts disagree about the very
estimate a surprise is measured against, so a high-dispersion event's surprise is measured against
a noisier consensus and is more likely to reflect that noise than real information. Downweighting
those positions is a <b>risk/variance filter</b>, not a return-predictive tilt -- the same category
of result as Strategy 5's sector-neutrality (a better risk-adjusted profile, not more expected
return), and described that way deliberately.""")

fig(FIG / "21_strategy8_comparison_bars.png", width=5.6*inch)
caption("""Figure 4. Strategy 8 (dispersion blend weight 0.20) gives up 0.36pp of return relative
to Strategy 6 for a matching-or-better Sharpe and a drawdown less than two-thirds as deep -- the
shallowest of any strategy in this project. Combining it with the vol-targeted overlay
(rightmost bars) does not beat Strategy 6-fully-vt.""")

body("""<b>Strategy 8</b> (scripts/39) bakes this in at blend weight 0.20, chosen from where the
Sharpe plateau and drawdown improvement both look strongest: <b>5.96% return, Sharpe 1.30, max
drawdown -10.8%</b> -- the shallowest drawdown of any strategy in this project, at a Sharpe
matching Strategy 6's best. The natural follow-up question -- does this compound with Stage 7's
vol-targeted overlay? -- was tested directly (scripts/40) rather than assumed: it does not. Strategy
8 + the vol-targeted overlay comes out worse than Strategy 6-fully-vt on every metric (10.58% /
0.98 / -20.0% vs. 11.58% / 1.06 / -17.9%), despite Strategy 8 alone having a much shallower
drawdown than Strategy 6 alone. Reported as tested, not smoothed over: Strategy 8's value is as an
alternative pure-alpha choice, not as an ingredient for the overlay combination.""")

story.append(PageBreak())

# ============================== SUMMARY TIMELINE ==============================
h2("The whole arc, in order")
cell_style = ParagraphStyle(name="Cell", parent=styles["Normal"], fontSize=8.2, leading=10.4)
def cell(t): return Paragraph(t, cell_style)

timeline_rows = [
    ("1. v1 baseline", "3 first-pass constructions built and compared",
     "Establish that the signal is tradeable at all"),
    ("2. Strategy 4", "Trim + tilt + priority-based cap trimming",
     "Rank-weighted saturated liquidity/leverage caps; proportional trimming diluted conviction"),
    ("3. Strategy 5", "+ Sector-neutral",
     "Found up to 29% gross weight in one sector; an uncompensated risk"),
    ("4. Strategy 6", "Leverage cap 1.5x -> 2.5x",
     "Swept the cap; found it stopped binding by ~2.0-2.5x once concentration risk was gone"),
    ("5a. Strategy 6+beta", "+ 0.5x market beta overlay (alpha book untouched)",
     "Institutional-style way to close the return gap to equity-like targets"),
    ("5b. Strategy 7", "Removed long=short dollar constraint (raw returns)",
     "The other way to add exposure -- found to leak far more beta than intended (cautionary)"),
    ("Dead end", "EAR signal blend", "Caught its own look-ahead bug; flat Sharpe once fixed -- not adopted"),
    ("Dead end", "SUE lag-4 reversal test", "Data shows persistence (+0.05), not reversal -- not adopted"),
    ("7. Strategy 6-fully-vt", "Vol-target the alpha book and the beta overlay (quarterly, trailing 63d vol)",
     "Leverage/bigger-overlay sizing sweeps confirmed no free return left; vol-targeting improved return, Sharpe, and drawdown together"),
    ("Dead end", "sue_seasonal, revenue surprise, analyst revisions",
     "All 3 tested via the full blend-weight engine; each made the strategy worse despite real or literature-backed standalone signal -- not adopted"),
    ("8. Strategy 8", "Blend in analyst-dispersion rank (weight 0.20)",
     "Dispersion has no standalone predictive power but acts as a risk filter when blended; shallowest drawdown of any strategy built"),
]
timeline = [["Stage", "What changed", "Why"]] + [[cell(s), cell(w), cell(y)] for s, w, y in timeline_rows]
story.append(make_table(timeline, col_widths=[1.15*inch, 2.35*inch, 2.85*inch], fontsize=8.2))

story.append(Spacer(1, 0.25*inch))
h2("Where this leaves the project")
body("""Strategy 6, Strategy 8, and Strategy 6-fully-vt are the three designs recommended for live
consideration: Strategy 6 and Strategy 8 as alternative pure-alpha choices (trade 0.5pp of return
for a much shallower drawdown, or don't), and Strategy 6-fully-vt as the higher-return choice --
confirmed, not just assumed, to remain the best return-oriented design after testing whether
Strategy 8 could improve on it in combination (Stage 8: it doesn't). Full performance numbers for
every strategy are in <i>PEAD_Strategy_Showcase.pdf</i>; the underlying evidence that the
earnings-drift effect being traded is real, and not an artifact of risk exposure or a narrow
subsample, is in <i>PEAD_Report.pdf</i>.""")

story.append(Spacer(1, 0.3*inch))
rule()
caption("""Sources: README.md's documented strategy evolution, data/sweep_strategy4_leverage.csv,
data/sweep_strategy6_sizing.csv, data/sue_reversal_summary.json, data/sue_reversal_by_decile.csv,
and the sector-concentration and EAR diagnostics printed by scripts/21_leverage_and_improvements.py,
scripts/22_sector_neutral_and_cadence.py, and scripts/27_ear_signal_diagnostic.py. Scripts:
scripts/20_strategy4_tilted.py through scripts/28_sue_reversal_test.py,
scripts/33_strategy6_beta_overlay_voltarget.py, scripts/34_strategy6_voltarget_combined.py,
scripts/35_strategy6_sizing_sweep.py, scripts/36_revision_dispersion_diagnostic.py,
scripts/37_revenue_surprise_diagnostic.py, scripts/38_sue_seasonal_diagnostic.py,
scripts/39_strategy8_dispersion_filter.py, scripts/40_strategy8_beta_overlay_voltarget.py,
scripts/29_v2_strategy_charts.py, scripts/31_build_development_report.py.""")

doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                         leftMargin=0.75*inch, rightMargin=0.75*inch,
                         topMargin=0.7*inch, bottomMargin=0.7*inch,
                         title="From Signal to Strategy: The Development Story")
doc.build(story)
print("wrote", OUT)
