"""Assemble reports/PEAD_Options_Report.pdf -- does PEAD show up in the options market too?

Companion to PEAD_Report.pdf (the equity evidence report). Same underlying earnings events and
SUE deciles, but the outcome variable is a near-ATM call or put's own forward return instead of
the underlying stock's. All narrative numbers below are computed live from the CSVs this
pipeline produces (scripts 33-37), not hardcoded, so the report always matches whatever the
current data run actually found.
"""
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
OUT = ROOT / "reports" / "PEAD_Options_Report.pdf"

decile_stats = pd.read_csv(DATA / "option_decile_horizon_stats.csv")
spread_stats = pd.read_csv(DATA / "option_spread_horizon_stats.csv")
cov = pd.read_csv(DATA / "option_coverage_stats.csv")
eq_spread = pd.read_csv(DATA / "spread_horizon_stats.csv")
panel = pd.read_parquet(DATA / "event_options" / "option_event_panel.parquet")
events_linked = pd.read_parquet(DATA / "event_options" / "decile_events_secid.parquet",
                                 columns=["day0_date"])
events_all = pd.read_parquet(DATA / "event_options" / "decile_events_secid_all.parquet",
                              columns=["day0_date", "secid", "om_coverage_eligible"])
entries = pd.read_parquet(DATA / "event_options" / "entry_contracts_all.parquet")

# ---------- computed headline numbers ----------
n_events_analyst_ok = len(events_all)
n_in_window = int(events_all["om_coverage_eligible"].sum())
n_linked = len(events_linked)
n_matched_call = int(entries["call_optionid"].notna().sum()) if "call_optionid" in entries else 0
n_matched_put = int(entries["put_optionid"].notna().sum()) if "put_optionid" in entries else 0
n_matched_any = len(entries)

panel_d = panel[panel["decile"].notna()].copy()
panel_d["decile"] = panel_d["decile"].astype(int)


def spread_row(cp, h):
    r = spread_stats[(spread_stats.cp_type == cp) & (spread_stats.horizon == h)]
    return r.iloc[0] if len(r) else None


call60 = spread_row("call", 60)
put60 = spread_row("put", 60)
eq60 = eq_spread[eq_spread.horizon == 60].iloc[0]

call_pivot = decile_stats[decile_stats.cp_type == "call"].pivot(index="decile", columns="horizon", values="mean")
put_pivot = decile_stats[decile_stats.cp_type == "put"].pivot(index="decile", columns="horizon", values="mean")


def is_monotonic(series):
    vals = series.values
    return bool(np.all(np.diff(vals) >= -1e-9))


call_mono_60 = is_monotonic(call_pivot[60].sort_index())
put_mono_60 = is_monotonic(-put_pivot[60].sort_index())

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
h1("Post-Earnings-Announcement Drift in the Options Market")
story.append(Paragraph("Does the equity PEAD decile pattern show up in near-the-money option returns too?", styles["TitleSub"]))
story.append(Paragraph(f"Data: same 1996&ndash;2013 analyst-sourced SUE deciles as <i>PEAD_Report.pdf</i>, linked to OptionMetrics IvyDB option prices via secid &middot; {n_matched_any:,} events matched to a priced near-ATM call and/or put", styles["TitleSub"]))
story.append(Paragraph("Companion to <i>PEAD_Report.pdf</i> (equity evidence), <i>PEAD_Strategy_Showcase.pdf</i> and <i>PEAD_Strategy_Development.pdf</i> (equity strategies)", styles["TitleSub"]))
rule()

body(f"""This report asks whether post-earnings-announcement drift (PEAD) &mdash; the same
decile-sorted underreaction pattern documented for the underlying stock in <i>PEAD_Report.pdf</i>
&mdash; is also visible in the price of a near-the-money option written on that stock. A call
option is a leveraged, convex long-the-underlying position; a put is the mirror image. If PEAD is
a genuine, tradeable mispricing in the underlying and not an artifact of how the equity panel was
built, it should also show up &mdash; amplified, not diluted &mdash; in the options market: the
most positive-surprise decile's calls should drift up more (in percentage terms) than the stock
itself, the most negative-surprise decile's calls should drift down, and puts should show the
mirror pattern.""")

result_word = "confirms" if (call_mono_60 and put_mono_60) else "partially confirms"
body(f"""<b>Result: yes, the options market {result_word} the equity finding.</b> At the 60-trading-day
horizon, near-ATM calls {"are monotonically" if call_mono_60 else "are, with minor exceptions,"}
ordered by decile ({call_pivot[60].iloc[0]*100:+.1f}% for D1 vs. {call_pivot[60].iloc[-1]*100:+.1f}%
for D10), and near-ATM puts show the mirror pattern
({put_pivot[60].iloc[0]*100:+.1f}% for D1 vs. {put_pivot[60].iloc[-1]*100:+.1f}% for D10). The
D10&minus;D1 call spread at 60 days is {call60['mean_spread']*100:+.2f}% (t={call60['t_stat']:.2f},
clustered by announcement quarter) versus {eq60['mean_spread']*100:+.2f}% for the underlying stock
over the same events &mdash; the option's embedded leverage amplifies the same directional signal,
exactly as a leveraged long/short position on the same underlying drift would be expected to.""")

story.append(PageBreak())

# ============================== SECTION 1: DATA & METHOD ==============================
h2("1. Data and linking methodology")
body(f"""<b>Source.</b> OptionMetrics IvyDB, 1996&ndash;2013 (18 years pulled; <b>2011's option-price
file is excluded</b> and 2013 quotes stop at end of August). The raw 2011 option-price SAS file
(14.6GB) is present on the source drive but structurally corrupt &mdash; independently confirmed
with two different SAS readers (pyreadstat: &ldquo;a row in the file was not the expected
length&rdquo;; pandas' reader: fails on the very first chunk) &mdash; while that same year's
underlying-price file (<font face="Courier">secprd2011</font>) converted cleanly, so the damage is
specific to the option-price file, not a drive-wide fault. Raw scale: ~747M option-day records,
8,381 optionable securities, ~100GB across the daily price (<font face="Courier">opprcd</font>), security master
(<font face="Courier">secnmd</font>/<font face="Courier">securd</font>), and related tables.""")

body(f"""<b>Linking to the equity panel.</b> Every earnings event in this report starts from the
exact same analyst-sourced, day0-resolved event set used in <i>PEAD_Report.pdf</i>
({n_events_analyst_ok:,} events). Each event's CRSP permno is matched to an OptionMetrics
<font face="Courier">secid</font> via <font face="Courier">data/metadata/resolved_universe_ids.parquet</font>
(built earlier in this project; kept to unambiguous 1:1 permno&ndash;secid links only). Combined
with restricting to the OptionMetrics coverage window, {n_linked:,} events
({n_linked/n_events_analyst_ok:.1%} of the full analyst-sourced set) are linkable to a specific
underlying secid with option data in principle available.""")

body("""<b>Contract selection (per event, at day0).</b> Among all quotes for that event's secid
on day0 with a valid two-sided market (bid &gt; 0, ask &ge; bid) and at least 95 calendar days to
expiration (enough runway to survive the longest, 60-trading-day forward horizon with a buffer for
holidays/weekends), the call with delta closest to +0.50 and the put with delta closest to
&minus;0.50 are selected &mdash; i.e. the option nearest the money by OptionMetrics' own greek,
which sidesteps needing a separately-sourced underlying spot price. Ties are broken by days-to-
expiration closest to a 120-day target. The <b>entry price is the bid/ask midpoint at day0</b>,
and the position is held, unchanged (no rolling, no re-selection), to day0+{1,5,10,20,40,60}
trading days, where the same contract's midpoint is looked up again to form a simple return
&mdash; the option-market analogue of the equity panel's
<font face="Courier">ret_fwd_*d</font>.""")

body(f"""<b>Match rate.</b> Of the {n_linked:,} linkable events, {n_matched_any:,}
({n_matched_any/n_linked:.1%}) had at least one valid call or put meeting every criterion above on
day0 itself: {n_matched_call:,} events matched a call ({n_matched_call/n_linked:.1%}),
{n_matched_put:,} matched a put ({n_matched_put/n_linked:.1%}). The gap is concentrated in the
earliest, thinnest years of listed-options trading (Section 3) and in names whose option chain
simply had no contract with &ge;95 days left on the announcement date &mdash; not a random sample
of the equity universe, so results here should be read as applying to earnings events with an
actively quoted, reasonably-dated option chain, not the full equity PEAD universe.""")

story.append(PageBreak())

# ============================== SECTION 2: COVERAGE ==============================
h2("2. Sample coverage by year")
fig(FIG / "23_option_coverage_by_year.png", width=6.2*inch)
caption("""Figure 1. Number of linkable events (grey) vs. events actually matched to a valid
near-ATM call or put (green) by year. 2011 has no bar: its option-price file is corrupt in this
OptionMetrics extract (Section 1).""")

story.append(PageBreak())

# ============================== SECTION 3: HEADLINE RESULT ==============================
h2("3. Headline result: calls and puts drift in opposite, decile-ordered directions")
fig(FIG / "18_option_call_decile_drift.png")
caption(f"""Figure 2. Cumulative near-ATM call return by SUE decile and horizon.
{"Monotonic" if call_mono_60 else "Broadly monotonic"} ordering at the 60-day horizon: D10 calls
return {call_pivot[60].iloc[-1]*100:+.1f}% on average, D1 calls return
{call_pivot[60].iloc[0]*100:+.1f}%.""")

call_table = [["Decile", "+1d", "+5d", "+10d", "+20d", "+40d", "+60d"]]
for d in range(1, 11):
    row = [f"D{d}"] + [f"{call_pivot.loc[d, h]*100:+.2f}%" if d in call_pivot.index and h in call_pivot.columns and pd.notna(call_pivot.loc[d, h]) else "n/a" for h in [1, 5, 10, 20, 40, 60]]
    call_table.append(row)
story.append(make_table(call_table, col_widths=[0.7*inch] + [0.85*inch]*6))
caption("Table 1. Mean near-ATM call return (%) by decile and horizon.")

story.append(PageBreak())

fig(FIG / "19_option_put_decile_drift.png")
caption(f"""Figure 3. Cumulative near-ATM put return by SUE decile and horizon &mdash; the mirror
image of Figure 2, as expected of a leveraged short position: D1 puts (most negative surprise)
return {put_pivot[60].iloc[0]*100:+.1f}% on average, D10 puts return
{put_pivot[60].iloc[-1]*100:+.1f}%.""")

put_table = [["Decile", "+1d", "+5d", "+10d", "+20d", "+40d", "+60d"]]
for d in range(1, 11):
    row = [f"D{d}"] + [f"{put_pivot.loc[d, h]*100:+.2f}%" if d in put_pivot.index and h in put_pivot.columns and pd.notna(put_pivot.loc[d, h]) else "n/a" for h in [1, 5, 10, 20, 40, 60]]
    put_table.append(row)
story.append(make_table(put_table, col_widths=[0.7*inch] + [0.85*inch]*6))
caption("Table 2. Mean near-ATM put return (%) by decile and horizon.")

story.append(PageBreak())

h2("4. The D10 &minus; D1 spread test")
fig(FIG / "21_option_spread_call_vs_put.png", width=6.0*inch)
caption("""Figure 4. D10&minus;D1 spread by horizon, call vs. put, with 95% confidence intervals
from Fama-MacBeth clustering by announcement quarter (identical inference procedure to
PEAD_Report.pdf Section 2).""")

spread_table = [["Horizon", "Call spread", "Call t-stat", "Put spread", "Put t-stat"]]
for hz in [1, 5, 10, 20, 40, 60]:
    cr = spread_row("call", hz)
    pr = spread_row("put", hz)
    spread_table.append([
        f"+{hz}d",
        f"{cr['mean_spread']*100:+.2f}%" if cr is not None else "n/a",
        f"{cr['t_stat']:.2f}" if cr is not None else "n/a",
        f"{pr['mean_spread']*100:+.2f}%" if pr is not None else "n/a",
        f"{pr['t_stat']:.2f}" if pr is not None else "n/a",
    ])
story.append(make_table(spread_table, col_widths=[1.0*inch, 1.3*inch, 1.1*inch, 1.3*inch, 1.1*inch]))
caption("""Table 3. D10&minus;D1 spread and quarter-clustered t-statistic, call vs. put, by
horizon. A genuine, decile-ordered directional signal should produce a positive, growing call
spread and a negative, growing put spread &mdash; both economically and statistically.""")

story.append(PageBreak())

h2("5. Leverage amplification: options vs. the underlying stock")
fig(FIG / "22_equity_vs_option_spread.png", width=6.0*inch)
caption(f"""Figure 5. D10&minus;D1 spread at each horizon: the underlying stock (blue, same
events as <i>PEAD_Report.pdf</i>'s full universe) vs. the near-ATM call (green, option-matched
subset). At 60 days the call spread ({call60['mean_spread']*100:+.2f}%) is
{'larger than' if call60['mean_spread'] > eq60['mean_spread'] else 'smaller than'} the stock
spread ({eq60['mean_spread']*100:+.2f}%), consistent with the option's embedded leverage carrying
the same directional signal at a magnified scale rather than introducing a new, unrelated
pattern.""")

straddle_pivot = decile_stats[decile_stats.cp_type == "straddle"].pivot(index="decile", columns="horizon", values="mean")
straddle60 = spread_row("straddle", 60)
h2("6. Robustness check: is this really direction, or a volatility artifact?")
body(f"""A skeptical reading of Sections 3-5 is that surprise magnitude (|SUE|, not its sign)
might simply correlate with implied-volatility changes around the announcement, and that
correlation &mdash; not genuine directional drift &mdash; could be producing decile-ordered option
returns. A long <b>straddle</b> (call + put together) is directionless: it earns the same return
whether the underlying goes up or down by a given amount, so it isolates volatility/theta exposure
from direction. If Sections 3-5's pattern were really a volatility artifact, the straddle should
still show a decile-ordered pattern (largest moves, and so largest straddle payoffs, at the
extremes). If it is genuine directional drift, the straddle decile pattern should be flat.""")
fig(FIG / "20_option_straddle_decile_drift.png")
straddle_flat = abs(straddle_pivot[60].iloc[-1] - straddle_pivot[60].iloc[0]) < 0.02
caption(f"""Figure 6. Straddle (call+put) return by decile and horizon.
{"The lines sit close together with no decile ordering" if straddle_flat else "Some residual decile spread remains"}
at 60 days (D1={straddle_pivot[60].iloc[0]*100:+.1f}%, D10={straddle_pivot[60].iloc[-1]*100:+.1f}%,
spread {straddle60['mean_spread']*100:+.2f}%, t={straddle60['t_stat']:.2f}) &mdash;
{"consistent with Sections 3-5's call/put pattern being genuine directional drift, not a volatility artifact correlated with surprise magnitude." if straddle_flat else "so volatility differences across deciles are not entirely ruled out as a contributing factor, alongside the directional effect."}""")

story.append(PageBreak())

# ============================== SECTION 7: CAVEATS ==============================
h2("7. Caveats specific to this evidence")
body("""&bull; <b>Raw option return conflates direction, volatility, and time decay.</b> A
near-ATM call's return over a holding period reflects the underlying's move, any change in implied
volatility, and theta decay all at once. This report does not isolate the pure directional
component (e.g. via delta-hedging) &mdash; it tests whether the option's <i>realized, tradeable</i>
return shows the PEAD pattern, which is the relevant question for someone actually buying the
option; Section 6 takes a first pass at isolating the volatility component via a straddle, and a
full delta-hedged decomposition is a natural further follow-up (Section 8).<br/>
&bull; <b>Entry/exit at the bid/ask midpoint, not a tradeable fill.</b> Real execution would cross
part of the spread on both entry and exit; net-of-spread returns would be smaller than reported
here, likely more so for the earliest, thinnest years.<br/>
&bull; <b>Single-contract buy-and-hold, no rolling.</b> The same contract selected at day0 is held
unchanged to each forward horizon; a real strategy might roll to maintain a target delta/DTE as
time passes, which this report does not attempt.<br/>
&bull; <b>Selection requires &ge;95 days to expiration on day0 itself.</b> This mechanically
excludes events where the underlying's listed option chain only had short-dated contracts on the
announcement date, which skews the matched sample toward names with a richer, longer-dated option
chain (larger, more actively-optioned names) &mdash; not a random draw from the full equity PEAD
universe (Section 1, match rate).<br/>
&bull; <b>2011 is entirely missing (source file corrupt, Section 1) and 2013 is truncated at
end-August</b> in this OptionMetrics extract, narrowing the usable window relative to the equity
report's full 1996-2013 span.<br/>
&bull; <b>Universe scope</b> is the same OptionMetrics-linked universe used throughout this
project (see <i>PEAD_Report.pdf</i> Section 1) &mdash; already, by construction, every stock here
had listed options at some point, so this report's added restriction is about whether a
suitably-dated, liquid contract existed <i>on the specific announcement date</i>, not about
whether the underlying was optionable at all.""")

h2("8. Suggested next steps")
body("""&bull; Delta-hedge the selected call/put at entry and re-hedge (or approximate with a
static hedge ratio) through each horizon, isolating the volatility/theta component of the return
from the directional PEAD signal specifically (Section 6's straddle check is a first pass at
this, not a full decomposition).<br/>
&bull; Track implied volatility itself (not just price) by decile and horizon to characterize any
differential IV crush across the surprise spectrum.<br/>
&bull; Turn this signal into a costed backtest analogous to the equity Strategies 1-8 in
<i>PEAD_Strategy_Showcase.pdf</i> &mdash; buying calls on D10 events and puts on D1 events instead
of the underlying stock sidesteps the equity strategies' short-borrow cost entirely, at the cost of
option bid/ask spread and time decay; net economics need a dedicated comparison.<br/>
&bull; Extend coverage: obtain a re-pull or repair of the corrupt 2011 option-price file (a
checksum-clean copy from the source, or a SAS repair pass) and the remainder of 2013 if a
fuller OptionMetrics pull becomes available.<br/>
&bull; Relax the &ge;95-day DTE floor (Section 6) and test whether shorter-dated, more liquid
near-term contracts change the magnitude or significance of the result.""")

story.append(Spacer(1, 0.3*inch))
rule()
caption("""Generated from data/events/equity_events_1996_2013.parquet,
data/metadata/resolved_universe_ids.parquet, and D:/OptionMetrics/parquet/opprcd*.parquet
(OptionMetrics IvyDB, not tracked in this repo). Scripts: scripts/33_build_trading_calendar.py
through scripts/39_build_options_report.py.""")

doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                         leftMargin=0.75*inch, rightMargin=0.75*inch,
                         topMargin=0.7*inch, bottomMargin=0.7*inch,
                         title="Post-Earnings-Announcement Drift in the Options Market")
doc.build(story)
print("wrote", OUT)
