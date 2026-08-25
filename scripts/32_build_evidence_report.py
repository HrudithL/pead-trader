"""Assemble reports/PEAD_Report.pdf -- evidence that PEAD is real, measurable, and not
explained away by risk or a narrow subsample.

This is a trimmed, locally-runnable version of the original report: it keeps the full
decile-drift test, the quarter-clustered significance test, the size/book-to-market
robustness check, and the sector/size/era subsampling (Sections 1-5, unchanged in
substance from the original). It drops the old Section 6 (a 3-strategy backtest using
the since-corrected, pre-bugfix Sharpe calculation) -- full strategy performance,
covering all 8 strategies with the corrected calculation, now lives in its own report,
PEAD_Strategy_Showcase.pdf, with the development reasoning in PEAD_Strategy_Development.pdf.
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
OUT = ROOT / "reports" / "PEAD_Report.pdf"

decile_stats = pd.read_csv(DATA / "decile_horizon_stats.csv")
spread_stats = pd.read_csv(DATA / "spread_horizon_stats.csv")
ff_spread_stats = pd.read_csv(DATA / "ff_spread_horizon_stats.csv")
ff_coverage = pd.read_csv(DATA / "ff_coverage_stats.csv")

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

def make_table(df_rows, col_widths=None, header_bg="#1a3a2a", fontsize=8.3, align_first_left=True):
    t = Table(df_rows, colWidths=col_widths, hAlign="LEFT")
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

# ============================== COVER / TITLE ==============================
story.append(Spacer(1, 0.3*inch))
h1("Post-Earnings-Announcement Drift in the IBES Sample")
story.append(Paragraph("A decile-sorted event study of analyst-based earnings surprise, 1996&ndash;2013", styles["TitleSub"]))
story.append(Paragraph("Data: WRDS I/B/E/S consensus and actuals, CRSP daily returns, Compustat classification &middot; 182,712 analyst-sourced earnings events across 6,766 tickers (see Section 1 for full universe and coverage scope)", styles["TitleSub"]))
story.append(Paragraph("Companion reports: <i>PEAD_Strategy_Showcase.pdf</i> (all 8 strategies' performance) and <i>PEAD_Strategy_Development.pdf</i> (why each strategy was built)", styles["TitleSub"]))
rule()

body("""This report tests whether the post-earnings-announcement drift (PEAD) anomaly is present
in this project's cleaned IBES/CRSP dataset. PEAD is the well-documented finding that stock prices
continue to move in the direction of an earnings surprise for weeks after the announcement, rather
than adjusting immediately &mdash; a pattern that is difficult to reconcile with market efficiency.
The test here is the standard one in the literature: sort earnings events into ten groups (deciles) by
the size of their standardized unexpected earnings (SUE), then track each decile's average
market-adjusted return over the following 1 to 60 trading days. If PEAD is present, decile 10
(the most positive surprises) should drift up, decile 1 (the most negative surprises) should drift
down, and the ordering in between should be roughly monotonic.""")

body("""<b>Result: yes, cleanly.</b> Across every horizon tested (1, 5, 10, 20, 40, and 60 trading
days), the ten deciles are monotonically or near-monotonically ordered, the spread between decile 10
and decile 1 is positive and grows with the horizon, and the spread remains statistically significant
after clustering standard errors by announcement quarter (the project's own stated remedy for the
one caveat &mdash; earnings-season clustering &mdash; that the underlying pipeline documentation
flags as unaddressed). A size-and-book-to-market risk adjustment (Section 4b) shows the drift is not
simply compensation for holding riskier stocks: the adjusted spread is, if anything, larger than the
raw one. The effect is not industry-specific: a formal test finds no significant difference in drift
magnitude across the twelve Fama-French industry sectors. It is, however, strongly size-related:
small-cap stocks drift roughly four times more than large-cap stocks, consistent with the standard
limits-to-arbitrage explanation for why this anomaly persists. This signal is turned into eight
tradeable, backtested portfolio constructions in the companion reports referenced above &mdash; all
eight are profitable net of transaction costs and liquidity limits, with Sharpe ratios from 0.91 to
1.33.""")

story.append(PageBreak())

# ============================== METHODOLOGY ==============================
h2("1. Data and sample construction")
body("""<b>Universe scope.</b> Every number in this report is drawn from this project's existing
WRDS pull, which was already restricted at the pull stage to the <b>OptionMetrics-linked
universe</b>: 8,358 secids (8,115 distinct CRSP permnos) &mdash; every US common stock that had
listed options on it at some point in this project's window, per
<font face="Courier">data/metadata/optionmetrics_universe.parquet</font> and the
<font face="Courier">universe_fingerprint</font> recorded in the CRSP pull manifest. This is a
broad cross-section (it spans all five size quintiles roughly evenly, from micro/small caps to
mega caps &mdash; see Section 5b), not a narrow index like the S&amp;P 500, but it is <b>not the
full CRSP/IBES universe</b>: a small-cap or micro-cap stock that never had listed options at any
point is not in this data at all, regardless of whether it had earnings announcements. All results
below should be read as applying to the optionable-stock universe specifically.""")

body("""<b>Ticker coverage within that universe.</b> This report does not use all 8,115 permnos
either. Of those, 7,222 have at least one earnings event with both a usable IBES match and a
resolved day-0 trading session in <font face="Courier">equity_events_1996_2013.parquet</font> (the
remaining 893 have no usable earnings-announcement event of any kind in this data, unrelated to the
choice below). Of those 7,222, this report is further restricted to the 6,766 tickers
(93.7% of the 7,222; 83.4% of the full 8,115) that have <b>at least one analyst-sourced
(<font face="Courier">sue_source == 'analyst'</font>) event</b>. The remaining <b>456 tickers are
entirely absent from this report</b> &mdash; every one of their earnings events used only the
seasonal (year-over-year) surprise measure or had no usable surprise measure at all, and were
dropped along with that filter, not sampled out. This was a deliberate scope choice (pooling the
analyst and seasonal measures without adjustment mixes two differently-scaled surprise
constructions), not a coverage gap that appeared by accident.""")
body("""This analysis builds directly on data already cleaned and validated earlier in this project
(see <i>data/earnings/FINDINGS.md</i>, <i>data/events/FINDINGS.md</i>, and
<i>data/results/FINDINGS.md</i> for the full derivation, all point-in-time and split-adjustment
fixes, and their measured effects). In summary, the underlying event table already:""")
body("""&bull; restricts every earnings-surprise measure to information that was strictly public
before the announcement (a hard <font face="Courier">statpers &lt; anndats</font> filter, i.e. no
look-ahead bias);<br/>
&bull; corrects both the analyst estimate and the reported actual for stock splits between the
consensus snapshot and the announcement (three known extreme-outlier events, e.g. FEMP going
from &minus;104.9 to +0.87, were pure split artifacts before this fix);<br/>
&bull; computes forward returns from the first trading day at/after the announcement (&ldquo;day
0&rdquo;), market-adjusted against the CRSP value-weighted index total return, for six horizons
(1, 5, 10, 20, 40, and 60 trading days).""")

body("""For this report specifically, the sample is narrowed and re-sorted as follows:""")
body("""&bull; <b>Event filter:</b> only events with a resolved day-0 trading session
(<font face="Courier">day0_status == 'ok'</font>), 261,537 of 268,606 candidate events (97.4%).<br/>
&bull; <b>Surprise source:</b> restricted to <font face="Courier">sue_source == 'analyst'</font>
&mdash; i.e. events with a usable, point-in-time analyst consensus estimate &mdash; rather than
pooling in the seasonal (year-over-year) fallback measure. The pipeline documentation notes these
two measures are on different scales (sample sd 9.01 vs. 3.36); restricting to one source avoids
that scale-mixing issue entirely for the headline result. This leaves <b>182,712 events</b>
spanning 72 announcement quarters (1996&ndash;2013).<br/>
&bull; <b>Decile assignment:</b> within each announcement calendar quarter, analyst-sourced events
are ranked by <font face="Courier">sue_analyst</font> (rounded to 8 decimals to avoid ordering
exact ties by floating-point noise) and cut into ten equal-count groups. Ranking within quarter, not
across the full sample, keeps the sort point-in-time-safe: a 1997 event's decile never depends on
2013 data.""")

caption("""Note on cell balance: because 11.6% of analyst-sourced events have a surprise of exactly
0.0000 (a large tied mass), deciles are not perfectly equal-sized where that tie mass falls
&mdash; decile 4 has 22,423 events versus roughly 15,000&ndash;19,000 for most other deciles. This
is a real feature of the data (heavy exact-zero-surprise mass, likely a mix of true zero surprises
and coarse consensus rounding), not a bug in the sort.""")

h2("2. Inference: clustering by announcement quarter")
body("""Earnings announcements cluster heavily within each fiscal quarter's reporting season, so
plain i.i.d. standard errors understate uncertainty. This report addresses that directly using the
standard remedy in the PEAD literature: <b>Fama-MacBeth aggregation</b>. For each decile (and for
the D10&minus;D1 spread), the average return is computed separately within each of the 72
announcement quarters; the reported mean is the average of those 72 quarterly averages, and the
standard error is the standard deviation of the 72 quarterly averages divided by &radic;72. This is
robust to arbitrary correlation among events within the same quarter, at the cost of basing
inference on 72 quarterly observations rather than 182,712 firm-quarter observations &mdash; a
conservative trade in favor of honest uncertainty.""")

story.append(PageBreak())

# ============================== SECTION 3: HEADLINE RESULT ==============================
h2("3. Headline result: deciles drift as expected")
fig(FIG / "01_decile_drift.png")
caption("""Figure 1. Cumulative market-adjusted return by SUE decile and horizon. D1 (most
negative surprise, dark red) sits below zero at every early horizon and only crosses back above
zero after 40+ trading days; D10 (most positive surprise, dark green) is positive from day 1 and
rises steadily to +3.24% by day 60. The ordering across deciles is monotonic at every horizon from
+10 days onward (a minor D4/D5 crossover appears only at the +1d and +5d marks, consistent with a
small amount of short-horizon noise around the announcement itself, and resolves by +10d).""")

decile_table_data = [["Decile", "+1d", "+5d", "+10d", "+20d", "+40d", "+60d"]]
pivot_mean = decile_stats.pivot(index="decile", columns="horizon", values="mean")
for d in range(1, 11):
    row = [f"D{d}"] + [f"{pivot_mean.loc[d, h]*100:+.2f}%" for h in [1, 5, 10, 20, 40, 60]]
    decile_table_data.append(row)
story.append(make_table(decile_table_data, col_widths=[0.7*inch] + [0.85*inch]*6))
caption("""Table 1. Mean market-adjusted return (%) by decile and horizon (trading days after
announcement). D1 = most negative surprise, D10 = most positive.""")

h2("4. The D10 &minus; D1 spread: the formal PEAD test")
fig(FIG / "02_spread_by_horizon.png", width=5.6*inch)
caption("""Figure 2. The spread between the top and bottom surprise deciles, with 95% confidence
intervals from the quarter-clustered (Fama-MacBeth) standard error described in Section 2.""")

spread_table_data = [["Horizon", "D10-D1 spread", "Clustered SE", "t-stat", "Quarters"]]
for _, r in spread_stats.iterrows():
    spread_table_data.append([
        f"+{int(r['horizon'])}d", f"{r['mean_spread']*100:+.2f}%", f"{r['se']*100:.2f}%",
        f"{r['t_stat']:.2f}", f"{int(r['n_quarters'])}"
    ])
story.append(make_table(spread_table_data, col_widths=[1.1*inch, 1.5*inch, 1.3*inch, 1.0*inch, 1.1*inch]))
caption("""Table 2. D10&minus;D1 spread by horizon. All t-statistics exceed 5.2 even after
quarter-clustering (versus the much larger, and overstated, t-statistics that plain i.i.d. SEs would
produce) &mdash; the spread is not a small-sample or clustering artifact. The spread widens from
+0.83% at 1 day to +3.05% at 60 days, consistent with genuine drift rather than a one-time reaction
that happens to differ by decile.""")

story.append(PageBreak())

# ============================== SECTION 4b: FAMA-FRENCH ROBUSTNESS ==============================
h2("4b. Robustness: is the drift just a size/value risk premium?")
body("""Section 3 showed that D1 (the most negative-surprise decile) does not fall monotonically
in market-adjusted terms &mdash; after an initial drop through +5 days, it recovers and crosses back
above zero by +60 days, even as D10 keeps climbing. One explanation raised for that pattern is that
extreme negative-surprise firms tend to be smaller and more value/distressed than extreme
positive-surprise firms, and small, high book-to-market stocks carry a higher unconditional expected
return simply as compensation for risk &mdash; a Fama-French style size and value premium, not a
market inefficiency. A single-factor market adjustment (Section 3's methodology) cannot separate that
risk premium from a genuine mispricing correction. This section tests that directly.""")

body("""<b>Method.</b> Rather than pull an unvetted external Fama-French factor file, this
reconstructs the substance of the size/value control from data already inside this project: book
equity from Compustat <font face="Courier">ceq</font> (funda.parquet has no
<font face="Courier">txditc</font>, so BE = ceq exactly, a standard simplification; firms with
ceq &le; 0 are dropped), matched to each event with at least a 130-day reporting lag before the
announcement to stay point-in-time-safe, divided by the event's own market cap to form
book-to-market. Each event is placed into one of 15 cells (5 size quintiles &times; 3
book-to-market terciles, both computed within the same announcement quarter). For every event, a
<b>characteristic-adjusted return</b> is computed by subtracting the leave-one-out average
market-adjusted return of every other event in the same quarter/size/book-to-market cell &mdash;
so each event is benchmarked against economically similar firms reporting around the same time,
not just the overall market.""")

cov = ff_coverage.iloc[0]
body(f"""Coverage: {int(cov['has_book_equity']):,} of {int(cov['n_events']):,} events
({cov['has_book_equity']/cov['n_events']:.1%}) matched a point-in-time book-equity figure;
{int(cov['has_both_size_and_bm']):,} ({cov['has_both_size_and_bm']/cov['n_events']:.1%}) had both a
size quintile and a book-to-market tercile and could be characteristic-adjusted. The decile sort
itself is unchanged from Section 3 &mdash; only the outcome variable (raw market-adjusted return vs.
characteristic-adjusted return) differs.""")

fig(FIG / "06_ff_d1_d10_paths.png", width=6.0*inch)
caption("""Figure 6. D1 and D10 cumulative return paths under the two adjustments. Once size and
book-to-market are controlled for (dashed lines), D1 no longer recovers &mdash; it declines
monotonically to &minus;1.71% by +60 days, exactly the pattern the underreaction story predicts for
bad news. D10's path is barely changed by the adjustment. This indicates that a meaningful share of
D1's apparent “recovery” in the raw market-adjusted numbers was in fact a size/value risk
premium riding on top of the drift, not evidence against PEAD.""")

ff_table_data = [["Horizon", "Raw D10-D1", "Size+BM-adj. D10-D1", "Adj. t-stat", "Quarters"]]
for _, r in ff_spread_stats.iterrows():
    raw_row = spread_stats[spread_stats["horizon"] == r["horizon"]].iloc[0]
    ff_table_data.append([
        f"+{int(r['horizon'])}d", f"{raw_row['mean_spread']*100:+.2f}%",
        f"{r['mean_spread']*100:+.2f}%", f"{r['t_stat']:.2f}", f"{int(r['n_quarters'])}"
    ])
story.append(make_table(ff_table_data, col_widths=[0.9*inch, 1.2*inch, 1.6*inch, 1.1*inch, 1.0*inch]))
caption("""Table 3. D10&minus;D1 spread, raw market-adjusted vs. size+book-to-market adjusted. The
adjusted spread is, if anything, larger than the raw spread at every horizon (+3.76% vs. +3.05% at
60 days) and remains highly significant (t-stats 9.25&ndash;14.21) after the same
quarter-clustering as Table 2. The drift is not explained away by size or value risk exposure.""")

fig(FIG / "07_ff_spread_comparison.png", width=5.6*inch)
caption("""Figure 7. Side-by-side comparison of the raw and characteristic-adjusted D10&minus;D1
spread across all six horizons, with 95% confidence intervals.""")

body("""<b>Reading this result honestly:</b> this is a characteristic (cell-mean) adjustment against
other events in this same restricted universe, not a regression against externally-published,
whole-market Fama-French factors. It controls for size and value tilts within this project's own
sample well, but it would not catch a risk exposure this universe is itself skewed on relative to
the broader market (e.g. if the OptionMetrics-linked universe as a whole is more mega-cap-tilted
than the total CRSP universe). A regression against the officially published Fama-French factors
remains a natural follow-up once either WRDS access or a verified factor-file source is available.""")

story.append(PageBreak())

# ============================== SECTION 5: SUBSAMPLING ==============================
h2("5. Does the effect vary across industries, firm sizes, and eras?")
body("""A natural follow-up question is whether PEAD is a uniform phenomenon or concentrated in
particular kinds of companies. This section reuses the classification features already built earlier
in the project (<font face="Courier">data/results/event_classification_features.parquet</font>:
Fama-French 12-industry sector, firm-size quintile at announcement, and a 5-bin era) and asks, for
each grouping variable, whether the D10&minus;D1 spread at the +60d horizon differs across groups
&mdash; using the same Fama-MacBeth clustering as above, plus a one-way ANOVA across each group's
72 (or fewer, where a group lacks quarters) quarterly spread observations as a formal significance
test of whether the groups can be pooled.""")

h2("5a. Industry sector &mdash; no significant difference; safe to pool")
fig(FIG / "03_sector_spread.png", width=5.4*inch)
caption("""Figure 3. D10&minus;D1 spread by Fama-French 12 sector, 95% CI. One-way ANOVA across
the twelve sectors: F=0.30, p=0.986 &mdash; the null of equal average spread across sectors cannot
be rejected. Every sector's point estimate falls in the 1.0%&ndash;3.8% range and every confidence
interval overlaps every other sector's.""")
body("""<b>Implication:</b> the drift effect is economically similar in magnitude across
Financials (Money), Business Equipment, Healthcare, Manufacturing, Energy, and every other FF12
sector tested. A trading or research application does not need sector-specific decile cutoffs or
sector-specific position sizing; one pooled model across sectors is statistically justified by this
data. (This does not mean a strategy's <i>book</i> can safely concentrate in one sector by
accident, however -- see <i>PEAD_Strategy_Development.pdf</i> for a case where exactly that
happened and had to be corrected.)""")

h2("5b. Firm size &mdash; a strong, monotonic gradient; should NOT be pooled")
fig(FIG / "04_size_spread.png", width=4.6*inch)
caption("""Figure 4. D10&minus;D1 spread by firm-size quintile at announcement (1=smallest,
5=largest). One-way ANOVA: F=6.50, p&lt;0.0001. The spread falls monotonically from 6.71% (smallest
quintile) to 1.60% (largest quintile) &mdash; more than a 4x difference in effect size.""")
body("""<b>Implication:</b> unlike sector, size is not safe to pool over. This matches the standard
explanation in the literature: PEAD is a limits-to-arbitrage phenomenon &mdash; small, illiquid,
less-covered stocks are harder and more costly for sophisticated investors to trade against, so the
mispricing persists longer and larger. Every strategy in the companion showcase report explicitly
neutralizes for this by size.""")

story.append(PageBreak())

h2("5c. Era &mdash; suggestive decline, not statistically conclusive")
fig(FIG / "05_era_spread.png", width=5.0*inch)
caption("""Figure 5. D10&minus;D1 spread by 5-bin era. One-way ANOVA: F=1.81, p=0.138 &mdash; not
significant at conventional levels, though the point estimates are suggestive: the spread is largest
in 1996&ndash;2000 (4.92%) and 2004&ndash;2007 (4.06%), and smallest in 2001&ndash;2003, 2008&ndash;2009,
and 2010&ndash;2013 (1.4%&ndash;1.7%).""")
body("""<b>Implication:</b> this is the one place this report is deliberately cautious rather than
declarative. A pattern consistent with the literature's own narrative &mdash; PEAD shrinking over
time as more capital specifically targets it &mdash; is visible in the point estimates, but with
only 5 era buckets and non-trivial within-era volatility (note the wide 2008-2009 CI, driven by only
8 quarters and the 2008 crisis's own volatility), the ANOVA does not clear a conventional significance
bar. This should be read as a hypothesis for further work, not a confirmed finding.""")

story.append(PageBreak())

# ============================== SECTION 6: POINTER TO STRATEGY REPORTS ==============================
h2("6. From signal to strategy")
body("""Sections 3-5 establish that the surprise-decile signal is real, survives a risk adjustment,
and is broadly consistent across industries. Turning that signal into actual traded portfolios
&mdash; deciding how long to hold each decile, how to weight positions, how to control for the size
effect found in Section 5b, and what it would have earned net of realistic transaction costs and
liquidity limits &mdash; is a substantial piece of work in its own right, covering 8 distinct
strategy designs built and iterated on in sequence. That work has its own two reports:""")
body("""&bull; <b><i>PEAD_Strategy_Showcase.pdf</i></b> &mdash; full performance numbers, NAV
curves, and drawdowns for all 8 strategies side by side, plus a recommendation for live
consideration.<br/>
&bull; <b><i>PEAD_Strategy_Development.pdf</i></b> &mdash; the narrative of why each strategy was
built the way it was: what problem was diagnosed in the previous version, and what changed as a
result, including two deliberate dead ends.""")

story.append(PageBreak())

# ============================== SECTION 7: CAVEATS ==============================
h2("7. Caveats specific to this evidence")
body("""&bull; <b>Survivorship in longer horizons.</b> <font face="Courier">data/events/FINDINGS.md</font>
documents that return coverage shrinks from 96.8% at +1d to 95.7% at +60d, almost entirely because a
security's own price panel ends (predominantly delisting) before the target date. Longer-horizon
statistics in this report implicitly condition on surviving that long &mdash; attrited events have a
significantly more negative average surprise than retained ones (t=&minus;4.83), so this attrition
is not random and could modestly understate the true magnitude of drift for the worst-surprise
decile at long horizons specifically.<br/>
&bull; <b>Reaction/drift conflation at the shortest horizon.</b> Daily-frequency data cannot fully
separate an announcement's own price reaction from its first day of drift when the announcement
lands during market hours; some of the "reaction" at +1d may already be drift, or vice versa.<br/>
&bull; <b>These sections are gross, unlevered returns.</b> Sections 3-5 do not account for bid-ask
spreads, market impact, or borrowing costs for the short leg &mdash; a material omission
specifically for the smallest-size quintile in Section 5b, where trading costs are typically
highest and the raw spread is largest. See the companion showcase report for costed, tradeable
results.<br/>
&bull; <b>Quarter-level clustering only.</b> Fama-MacBeth clustering by announcement quarter
addresses earnings-season clustering but not, for example, clustering by industry-day if multiple
same-industry firms report on the same date within a quarter.<br/>
&bull; <b>Universe scope.</b> Every result in this report, including the Section 4b risk
adjustment, is confined to the OptionMetrics-linked universe (~8,100 permnos) that this project's
WRDS pull already used. It is a broad cross-section but excludes any stock that never had listed
options.<br/>
&bull; <b>The size/BM adjustment is characteristic-based, not a factor regression against
official Fama-French series.</b> Section 4b benchmarks each event against other events in the same
universe, same quarter, same size/book-to-market cell &mdash; it controls for size and value tilts
within this sample, but would not catch a risk exposure the OptionMetrics universe is itself
systematically skewed on relative to the full market.<br/>
&bull; <b>Data ends in 2013, and PEAD is a well-known, heavily studied anomaly.</b> A live strategy
today would need current data revalidation; the era analysis in Section 5c already found weaker
point estimates in the most recent (2010-2013) years of this sample, consistent with the
literature's own narrative of the effect shrinking as more capital targets it.""")

h2("8. Suggested next steps")
body("""&bull; Extend Section 5's sector/size/era cuts to interaction terms (e.g. size x era) once
cell sizes allow it &mdash; several era buckets are already thin (8&ndash;12 quarters) and would need
care before slicing further.<br/>
&bull; Add the seasonal-sourced (<font face="Courier">sue_source == 'seasonal'</font>) subsample as
an explicit robustness cut on its own scale, rather than folding it into the pooled
<font face="Courier">sue_rank_pct</font> the underlying pipeline already computes.<br/>
&bull; Build a day-by-day (rather than fixed-horizon) cumulative abnormal return curve using the
daily CRSP panel directly, to see the drift's exact shape rather than six snapshot points.<br/>
&bull; Replace Section 4b's within-universe characteristic adjustment with a regression against the
officially published Fama-French factor series (and consider adding momentum/profitability/
investment factors for a full 5-factor check) once WRDS access or a verified factor-file source is
available.<br/>
&bull; Expand the universe past the OptionMetrics-linked ~8,100 permnos if the full CRSP/IBES
universe is wanted &mdash; requires a new WRDS pull.<br/>
&bull; The empty <font face="Courier">data/event_options/</font> and
<font face="Courier">data/raw_options/</font> folders in this project suggest an options-based
version of this strategy was anticipated (e.g. buying calls on D10 events, puts on D1 events) --
that would sidestep the short-borrow problem entirely and add convexity, and is a natural next
build once options data is pulled.<br/>
&bull; For strategy-level next steps (walk-forward validation, borrow-cost modeling, transaction
cost calibration), see the closing sections of <i>PEAD_Strategy_Showcase.pdf</i>.""")

story.append(Spacer(1, 0.3*inch))
rule()
caption("Generated from data/events/equity_events_1996_2013.parquet, data/results/event_classification_features.parquet, data/earnings/ibes_clean_1995_2014.parquet, data/normalized_equity/*.parquet, and data/raw_wrds/comp/funda.parquet. Scripts: scripts/01_build_deciles.py through scripts/10_extended_ff_decay.py, scripts/32_build_evidence_report.py.")

doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                         leftMargin=0.75*inch, rightMargin=0.75*inch,
                         topMargin=0.7*inch, bottomMargin=0.7*inch,
                         title="Post-Earnings-Announcement Drift in the IBES Sample")
doc.build(story)
print("wrote", OUT)
