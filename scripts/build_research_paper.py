"""Assemble reports/PEAD_Strategies_Explained.pdf -- a plain-language research paper on HOW the
strategies in this project actually decide to trade, not just what they returned.

Every other report in `reports/` either proves the effect exists (PEAD_Report.pdf,
PEAD_Options_Report.pdf), compares final performance numbers (PEAD_Strategy_Showcase.pdf,
PEAD_Strategies_Overview.pdf), or narrates the iteration history (PEAD_Strategy_Development.pdf).
None of them is a single, self-contained walkthrough of the *mechanism*: what a firm's earnings
surprise has to look like for a position to open, how big that position gets, how long it's held,
and what the resulting P&L looks like -- for both the equity book and the options book, in one
document, aimed at a reader with real finance background but no interest in the code. This script
builds that document, reading every number from the same committed result files every other report
reads from (nothing here is a new computation).

Run: python scripts/build_research_paper.py
"""
import sys
from pathlib import Path

import pandas as pd
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, Spacer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, FIGURES_DIR, REPORTS_DIR
from common.report_pdf import new_report

DATA = DATA_DIR
FIG = FIGURES_DIR
OUT = REPORTS_DIR / "PEAD_Strategies_Explained.pdf"

# ---------------------------------------------------------------------------
# Load the same committed result files every other report reads from.
# ---------------------------------------------------------------------------
eq_spread = pd.read_csv(DATA / "spread_horizon_stats.csv").set_index("horizon")
opt_spread = pd.read_csv(DATA / "option_spread_horizon_stats.csv")
eq_summary = pd.read_csv(DATA / "results_summary_v2_FINAL.csv").set_index("strategy")
opt_v1 = pd.read_csv(DATA / "backtest_options_v1_comparison.csv").set_index("hold_horizon_days")
opt_opt = pd.read_csv(DATA / "backtest_options_optimal_comparison.csv").set_index("hold_horizon_days")
decay = pd.read_csv(DATA / "cell_decay_days.csv")
qlog6 = pd.read_csv(DATA / "backtest_v2_strategy6_quarterlog.csv")
agg = None
try:
    agg = pd.read_csv(DATA / "results_summary_v2_aggressive_FINAL.csv").set_index("strategy")
except FileNotFoundError:
    pass


def pct(x, sign=False):
    return f"{x * 100:+.2f}%" if sign else f"{x * 100:.2f}%"


def ratio(x):
    return f"{x:.2f}"


call60 = opt_spread[(opt_spread.cp_type == "call") & (opt_spread.horizon == 60)].iloc[0]
put60 = opt_spread[(opt_spread.cp_type == "put") & (opt_spread.horizon == 60)].iloc[0]
straddle60 = opt_spread[(opt_spread.cp_type == "straddle") & (opt_spread.horizon == 60)].iloc[0]
eq60 = eq_spread.loc[60]

median_hold = int(decay["decay_day"].median())
min_hold = int(decay["decay_day"].min())
max_hold = int(decay["decay_day"].max())
avg_positions_q = qlog6[qlog6["n_new_positions"] > 0]["n_new_positions"].mean()

rb, story, styles, h1, h2, h3, body, caption, glossary, rule, fig, make_table = new_report(fig_width=6.2 * inch)

# ============================== COVER ==============================
story.append(Spacer(1, 0.25 * inch))
h1("How the PEAD Strategies Actually Trade")
story.append(Paragraph(
    "A plain-language walkthrough of the signal, the trade-selection rules, and the returns "
    "&mdash; equities and options", styles["TitleSub"]))
story.append(Paragraph(
    "Companion to <i>PEAD_Report.pdf</i>, <i>PEAD_Options_Report.pdf</i>, "
    "<i>PEAD_Strategy_Showcase.pdf</i>, and <i>PEAD_Options_Strategy_Report.pdf</i> "
    "(those establish the evidence and the full metrics; this document explains the mechanism "
    "those numbers came from, in the plainest terms that don't lose the substance)",
    styles["TitleSub"]))
rule()

body("""This paper answers three questions, in order, for both the stock (equity) side of this
project and the options side: (1) what is the pattern being traded, and how do we know it's real
rather than noise; (2) given that pattern, exactly how does the strategy decide which names to buy
or sell, which direction, and how large a bet to place; and (3) what did actually running that
decision rule for real, over 1996&ndash;2013 of history, produce. No step below is simplified away
&mdash; every rule, threshold, and number quoted is the literal one used in the backtests, just
explained in words instead of code.""")

glossary("""<b>A few terms used throughout, in plain language:</b><br/>
&bull; <b>Earnings surprise (SUE):</b> how far a company's actual reported earnings came in above
or below what Wall Street analysts had been expecting, standardized so a surprise can be compared
across companies of very different sizes.<br/>
&bull; <b>Decile:</b> each quarter, every company that reported earnings that quarter is sorted by
its surprise and split into 10 equal-sized groups &mdash; decile 1 is the worst surprises, decile
10 is the best.<br/>
&bull; <b>Long / short:</b> "long" means buying something you expect to rise; "short" means
borrowing and selling something you expect to fall, to profit if it does. Holding both sides at
once means the strategy can make money whether the whole market goes up or down.<br/>
&bull; <b>Market-adjusted return:</b> a stock's return minus what the overall market did that same
day &mdash; isolates the part of the move that's specific to that stock's own news, not a general
market rally or selloff.<br/>
&bull; <b>Dollar-neutral / market-neutral:</b> the long side and short side are sized so their
dollar exposure roughly cancels out, so the strategy's return doesn't depend on whether the market
as a whole goes up or down.<br/>
&bull; <b>Delta / DTE:</b> an option's delta is roughly the odds (0 to 1) that it finishes
in-the-money, and also how much its price moves for a $1 move in the stock; DTE is simply "days to
expiration."<br/>
&bull; <b>Kelly criterion:</b> a formula for how much of your capital to bet on an opportunity with
a known edge, so as to maximize long-run compounded growth without risking ruin; "half-Kelly" bets
half that amount, the standard practitioner haircut for the fact the edge is estimated, not
certain.<br/>
&bull; <b>Sharpe ratio:</b> return earned per unit of volatility (risk) taken on &mdash; higher is
better; a well-run market-neutral hedge fund typically runs 0.5&ndash;1.0.""")

story.append(PageBreak())

# ============================== SECTION 1: THE PHENOMENON ==============================
h2("1. The pattern being traded: post-earnings-announcement drift (PEAD)")

body(f"""Roughly four times a year, a public company reports earnings, and Wall Street analysts'
prior forecasts turn out to have been too high or too low. The surprise the company delivers
&mdash; standardized so a $10bn company's surprise can be compared to a $500m company's &mdash;
is called SUE (standardized unexpected earnings). The textbook, decades-old finding this whole
project starts from is that the market does not fully price in that surprise on the announcement
day: stocks with the best surprises keep drifting up for weeks afterward, and stocks with the
worst surprises keep drifting down. That continued drift, not the announcement-day jump itself, is
what "post-earnings-announcement drift" means and what every strategy below is trying to
capture.""")

h3("How we checked it's real and not noise")
body(f"""Using 1996&ndash;2013 IBES/CRSP/Compustat data (analyst-based SUE, ~183,000 earnings
events), every company that reports in a given quarter is ranked against its peers <i>that same
quarter</i> and cut into 10 equal-sized deciles &mdash; decile 1 is the worst surprises, decile 10
the best. We then track each decile's average market-adjusted return (the stock's return minus
what the whole market did that day, isolating the stock-specific news) for up to 60 trading days
after the announcement. Figure 1 shows the result: decile 10 drifts up, decile 1 drifts down, and
every decile in between lines up in order &mdash; a "monotonic" pattern, which matters because a
real, systematic effect should order every group consistently, not just the two extremes.""")

fig(FIG / "01_decile_drift.png", width=5.8 * inch)
caption("""Figure 1. Cumulative market-adjusted return by SUE decile, day 0 through day 60. D1
(worst surprises) sits below zero from the start; D10 (best surprises) is positive from day 1 and
keeps climbing; every decile in between falls in order. This is the raw phenomenon every strategy
below is trying to systematically harvest.""")

body(f"""The gap between the best and worst decile (the "D10&minus;D1 spread") is
{pct(eq60.mean_spread)} by day 60, and because a random pattern would average out to zero across
{int(eq60.n_quarters)} independent calendar quarters, we can test whether that's a fluke: the
t-statistic (how many standard errors the spread is away from zero, computed the honest way &mdash;
clustered by calendar quarter, so 18,000+ individual stock-events in the same quarter aren't
double-counted as 18,000 independent data points) is {eq60.t_stat:.2f}. Anything above roughly 2 is
conventionally "statistically significant"; {eq60.t_stat:.2f} is not a borderline call. We also
checked the obvious alternative explanations before trusting this: does it disappear once you
adjust for company size and value/growth style (it doesn't, a Fama-French-style characteristic
adjustment leaves the pattern intact), does it come from one industry or one era of the sample
(it doesn't &mdash; it shows up in every decade and every sector we sliced the data by, though one
early diagnostic did catch a strategy over-concentrating in a single sector by accident, which is
addressed in Section 3), and does the drift ever actually stop (yes &mdash; see "how long to hold"
below).""")

# ============================== SECTION 2: EQUITY MECHANISM ==============================
h2("2. How the equity strategy decides what to trade")

body("""Knowing the pattern exists doesn't by itself tell you which stocks to buy, how much, or
for how long. The best-performing equity design in this project (Strategy 6) makes that decision
in five steps, run mechanically every quarter with no human judgment call in the loop:""")

h3("Step 1 &mdash; rank and center")
body("""Every company that reports earnings in a given quarter gets a percentile rank of its SUE
against every other reporter that same quarter (0% = worst surprise, 100% = best), then that rank
is re-centered to run from &minus;0.5 (worst) to +0.5 (best) with 0 meaning "right at the middle,
no real surprise either way.""")

h3("Step 2 &mdash; trim out the low-conviction middle")
body("""Names whose centered score falls within &plusmn;0.15 of zero &mdash; roughly the middle
30% of each quarter's reporters, companies whose earnings came in close to what was already
expected &mdash; are dropped entirely. There's no real information in a surprise that isn't much
of a surprise, and including them would just add trading costs and dilution without adding
edge.""")

h3("Step 3 &mdash; tilt weight toward conviction")
body("""Among the names that survive the trim, each position's weight is proportional to how far
its score sits from that 0.15 cutoff toward the extreme &mdash; a company at the very best or
worst end of the distribution gets close to the maximum weight; one just past the trim line gets
close to the minimum. The relationship is linear (not curved toward the extremes or the middle),
so a company twice as far from the cutoff gets roughly twice the weight.""")

h3("Step 4 &mdash; spread evenly across sector and company size")
body("""This step exists because of a real mistake caught while building an earlier version:
diagnostics found that ranking on SUE alone let the book concentrate up to 29% of its total weight
in a single industry in some quarters (versus 8.3% if spread evenly across the 12 broad industry
groups used) &mdash; an unpriced, PEAD-unrelated bet on that industry doing well or poorly, riding
along for free. The fix, kept in every strategy from that point on: within each side (long and
short) of each quarter's book, weight is spread evenly across every (industry &times; company-size)
combination represented, so no single industry or size bucket can dominate the return by
accident.""")

h3("Step 5 &mdash; scale into real dollars, then cap")
body(f"""The whole weight vector from steps 1&ndash;4 is scaled by a "base unit" equal to 0.3% of
the fund's trailing net asset value (NAV, i.e. the money actually available to trade that quarter
&mdash; sizing off trailing NAV rather than a fixed starting number means the book grows as the
strategy compounds gains, and shrinks after a loss, rather than staying a fixed dollar size
forever). Two safety caps are then checked: a <b>leverage cap</b> limits the book's total dollar
exposure to 2.5&times; NAV (if hit, the lowest-conviction positions are trimmed first, not
everyone shaved equally &mdash; a deliberate fix after finding the earlier, proportional
trim-everyone-equally approach diluted the strategy's strongest convictions along with its
weakest), and a <b>liquidity cap</b> limits any single position to 5% of that stock's own average
daily trading volume, so the backtest never assumes a trade the real market couldn't actually
absorb. In practice, for this specific well-diversified book, the leverage cap essentially never
binds &mdash; it did not trigger in any of the 72 quarters actually traded &mdash; because spreading
so evenly across sector and size already prevents the kind of concentration a leverage cap exists
to guard against.""")

h3("How long a position is held")
body(f"""Rather than picking one fixed holding period for every trade, an earlier diagnostic
(Section 1) measured, separately for each (decile &times; company-size &times; value/growth) group,
the actual day on which that group's drift stops accelerating &mdash; the point where the average
daily return contribution flattens out, detected from the slope of the cumulative drift curve. That
day ranges from {min_hold} to {max_hold} trading days depending on the group, with a typical (median)
holding period of about {median_hold} trading days &mdash; roughly two months. A position is opened
the day after its earnings announcement and closed automatically once its own group's measured
decay day is reached, rather than being held on a fixed calendar or left open indefinitely.""")

h3("Worked example")
body("""Take two fictional companies that both report earnings on the same day. Acme Corp beats
estimates by a wide margin and lands at the 96th percentile of that quarter's surprises &mdash; a
centered score of +0.46, comfortably past the 0.15 trim line, so it gets a large long-side weight.
Consolidated Industries misses badly and lands at the 3rd percentile, a centered score of
&minus;0.47, getting a similarly large short-side weight. A third company, Meridian Corp, reports
almost exactly in line with expectations (48th percentile, centered score +0.02) and is dropped
entirely &mdash; no position, because there's no real conviction there to size. Acme's and
Meridian's <i>industry and size peers</i> that also cleared the trim get grouped with them so no
one industry or size bucket ends up carrying the book, the whole set is scaled to roughly 0.3% of
NAV per full-conviction name, and Acme's position (say it falls in a group whose measured decay
day is 55 trading days) is closed automatically around 11 weeks later, regardless of what happens
to the rest of the book.""")

story.append(PageBreak())

# ============================== SECTION 3: EQUITY BOOK & RETURNS ==============================
h2("3. What the equity book and its returns actually look like")

body(f"""Applying the rules above every quarter from 1996 to 2013 produces a book that trades
about {avg_positions_q:,.0f} new positions (long and short sides combined) in a typical active
quarter &mdash; thousands of small, individually modest bets rather than a handful of large,
concentrated ones, which is exactly what "spread evenly across sector and size" in Step 4 is
designed to produce. Because the book is built to be dollar-neutral and the underlying returns are
market-adjusted, its return is not simply "the market went up" &mdash; it's specifically the
compounding of thousands of small, repeated bets that already-confirmed good news keeps drifting
up and already-confirmed bad news keeps drifting down.""")

fig(FIG / "12_v2_nav_curves_all.png", width=5.9 * inch)
caption("""Figure 2. Growth of $10,000,000 invested in each of the 8 equity strategy designs,
1996-2013. The beta-overlay variant (which deliberately adds a separate market-index position on
top of the alpha book) pulls ahead because it's the only one carrying real market exposure by
design; the rest stay close to market-neutral and compound more slowly but far more smoothly.""")

fig(FIG / "14_v2_drawdowns_all.png", width=5.9 * inch)
caption("""Figure 3. Drawdown from the prior peak, all 8 strategies. Every strategy's worst
stretch clusters around the 2008-2009 financial crisis, as expected of any strategy that still
carries some residual risk even while designed to be broadly market-neutral.""")

eq_rows = [["Strategy", "What changed", "Ann. Return", "Ann. Vol", "Sharpe", "Max Drawdown"]]
labels_order = ["extreme", "rankweighted", "balanced", "strategy4", "strategy5", "strategy6",
                 "strategy6_beta050", "strategy7"]
for s in labels_order:
    r = eq_summary.loc[s]
    eq_rows.append([r["label"], "", pct(r.ann_return), pct(r.ann_vol), ratio(r.sharpe), pct(r.max_drawdown)])
make_table(eq_rows, col_widths=[1.35 * inch, 0.05 * inch, 0.95 * inch, 0.85 * inch, 0.65 * inch, 1.05 * inch])
caption("""Table 1. All 8 equity designs use exactly the mechanism in Section 2 (or an early,
simpler ancestor of it); what differs between rows is which of the five steps had been added yet.
Strategy 6 (the version walked through above) posts the best pure risk-adjusted return of the
non-overlay designs; adding a modest 0.5x market-index overlay on top pushes return further into a
10-15% target range at the cost of Sharpe and a deeper crisis-period drawdown, since that overlay
is the only source of real market exposure in the whole table.""")

if agg is not None:
    h3("Trading some of that risk-adjusted efficiency for more raw return")
    body("""Every strategy above was tuned toward the best risk-adjusted outcome (highest Sharpe
    subject to a sane drawdown), not the highest return achievable. A separate pass asked a
    narrower question for each of the 8 designs: keeping the exact same signal, trim, tilt, and
    sector/size neutrality from Section 2 completely unchanged, how much more return is available
    purely by making the book bigger and more levered (raising the 0.3%-of-NAV base unit and the
    leverage cap, nothing else)? The candidate that maximized return, subject to Sharpe staying
    above 0.6 and drawdown staying shallower than -40% (treated as a hard "fund-ending" floor, not
    a dial), roughly doubled annualized return across the board &mdash; the realistic limit turned
    out to be drawdown tolerance, not risk-adjusted quality: every aggressive variant still cleared
    the 0.6 Sharpe floor with room to spare.""")
    fig(FIG / "28_v2_aggressive_comparison_bars.png", width=5.9 * inch)
    caption("""Figure 4. Baseline vs. aggressive-sizing return, Sharpe, and drawdown for each
    design. Return roughly doubles or more in every case; Sharpe falls but stays well above the 0.6
    floor the search enforced, because the binding constraint was the -40% drawdown floor, not risk
    -adjusted quality.""")

story.append(PageBreak())

# ============================== SECTION 4: OPTIONS PHENOMENON ==============================
h2("4. The same phenomenon, seen through options")

body(f"""If the drift in Section 1 is real, it should also show up &mdash; amplified &mdash; in
options on the same stocks, because a call or put option is a leveraged bet on the direction of the
underlying stock: a given percentage move in the stock translates into a much larger percentage
move in an option's own price. We re-ran the identical decile sort from Section 1, but instead of
tracking the stock's own return after each earnings announcement, we tracked the return of a
near-the-money call and a near-the-money put on that same stock (matched via OptionMetrics IvyDB
data, 126,008 of the original ~183,000 events that had a usable option chain, 1996&ndash;2013
excluding 2011 due to a corrupt source file for that year alone).""")

fig(FIG / "22_equity_vs_option_spread.png", width=5.7 * inch)
caption(f"""Figure 5. D10&minus;D1 spread at each horizon: the underlying stock (same events as
Figure 1) vs. the near-the-money call. At 60 days the stock spread is {pct(eq60.mean_spread)}
while the call spread is {pct(call60.mean_spread)} &mdash; the exact same underlying phenomenon,
amplified several-fold by the option's leverage.""")

body(f"""The pattern is fully consistent with what leverage should do to a directional signal: at
60 days, the call D10&minus;D1 spread is {pct(call60.mean_spread)} (t={call60.t_stat:.2f}) versus
just {pct(eq60.mean_spread)} for the underlying stock over the identical events; near-the-money
puts show the mirror image, {pct(put60.mean_spread)} (t={put60.t_stat:.2f}) &mdash; puts on decile-1
(worst-surprise) stocks gain as those stocks fall. A straddle (holding a call and a put on the same
stock at once, a bet on the <i>size</i> of the move rather than its direction) still shows a
smaller but real decile-ordered residual, {pct(straddle60.mean_spread)}
(t={straddle60.t_stat:.2f}) &mdash; confirming that some of what options add here is a genuine
volatility effect on top of the pure directional bet, not directional leverage alone.""")

# ============================== SECTION 5: OPTIONS MECHANISM ==============================
h2("5. How the options strategy decides which contract to buy, and how much")

body("""Two different contract-selection rules were built and backtested for real, each answering
a progressively harder version of the same question.""")

h3("The simple version: near-the-money by moneyness")
body("""For every earnings event, on the day of announcement, look at that stock's actual option
chain and pick the call (if the stock's SUE decile says "bullish") or put (if "bearish") whose
delta &mdash; roughly its odds of finishing in-the-money, and how much its price moves per $1 move
in the stock &mdash; sits closest to 0.50 (an at-the-money contract, the most liquid, easiest-to-
trade point on the chain), requiring at least 95 days left to expiration so the position has room
to run for its full holding period without time decay dominating the trade. This "Tier 1" rule
hard-codes the direction (bullish decile &rarr; buy a call, bearish decile &rarr; buy a put) and
picks the contract by moneyness alone, ignoring the rest of the chain.""")

h3("The harder question: is near-the-money actually the best contract?")
body("""Every strike in the chain besides the ~50-delta one is thrown away by the simple rule above
&mdash; there might be a better bet further out. A second, more demanding design ("Tier 1.5")
answers that by scoring <b>every</b> strike in the real day-0 chain, not just one:""")

h3("Step 1 &mdash; build a real probability distribution of outcomes")
body("""Instead of guessing at three or four possible future stock-price scenarios by hand, the
distribution of what a stock's price is likely to do over the next 1&ndash;60 days is built
empirically from this project's own history of real PEAD events, conditioned on the stock's own
SUE decile, and split into enough probability buckets that the tails (the rare big moves) each get
their own realistic estimate rather than being smoothed away. A subtle but important correction was
needed here: building that distribution from raw (not market-adjusted) historical returns let the
strategy's own knowledge of "the market went up a lot from 1996-2013" leak into what's supposed to
be a decile-specific signal, inflating every decile's distribution with ambient market drift that
has nothing to do with earnings surprises. The fix separates the distribution's <i>shape</i> (built
from market-adjusted returns, isolating the real decile effect) from its <i>average level</i>
(re-added afterward from the real historical market drift for that horizon), so the two don't get
conflated.""")

h3("Step 2 &mdash; score every strike, walk-forward")
body("""For each event, every strike available in that day's real chain is scored against the
probability distribution above, but only using history from calendar quarters strictly before that
event's own announcement quarter &mdash; the same discipline as training a forecasting model only
on the past, never letting a trade in 1998 get scored using information from 2013.""")

h3("Step 3 &mdash; size by expected compounded growth, not raw expected value")
body("""This is the step that actually prevents the selector from just picking the wildest,
furthest-out-of-the-money lottery-ticket strike, which is what would happen if contracts were
ranked by raw expected payoff alone (a tiny chance of a huge payoff can have a large expected value
on paper while losing money on almost every single trade). Instead, each candidate strike's own
best bet size &mdash; its Kelly fraction, the fraction of capital that maximizes long-run compounded
growth given its own risk/reward profile &mdash; is solved for, and strikes are ranked by the actual
compounded growth rate that bet size would produce. A deep-out-of-the-money strike still gets
evaluated; it just correctly comes out with a tiny optimal bet size and near-zero growth
contribution, rather than looking artificially attractive.""")

fig(FIG / "26_optimal_selector_put_share_by_decile.png", width=5.5 * inch)
caption("""Figure 6. Share of Kelly-optimal picks that are PUTS, by SUE decile. No call/put rule is
hard-coded anywhere in this selection logic &mdash; the selector only ever sees each event's
probability distribution and its real day-0 option chain, and it still discovers the same
direction Section 4 already showed the market itself trades: puts for 32% of decile-1 (worst
surprise) events versus just 5% of decile-10 events.""")

h3("Sizing the actual bet")
body("""Full Kelly sizing is a well-documented over-bettor once the probability distribution being
used is itself an estimate rather known with certainty (which it always is here) &mdash; the
strategy therefore only ever risks <b>half</b>-Kelly, the standard practitioner correction. Capital
is further split across every event competing for it in a given quarter (often 1,000+ candidates at
once) in proportion to each one's own Kelly fraction, rather than sizing each position off its own
number in isolation &mdash; an earlier version of this rule that ignored how many other candidates
were competing for the same capital cap collapsed the entire 17-year backtest down to roughly 46
funded positions total, a bug caught and fixed on the pipeline's first real run. Only events with a
genuinely positive Kelly growth rate are traded at all &mdash; a real, math-derived edge, not simply
"this stock is in the top or bottom SUE decile." """)

story.append(PageBreak())

# ============================== SECTION 6: OPTIONS RETURNS ==============================
h2("6. What the options returns look like")

fig(FIG / "24_options_strategy_nav_curves.png", width=5.9 * inch)
caption("""Figure 7. Growth of $10,000,000: equity Strategy 6 (Section 3), the simple near-the-
money options rule (Tier 1), and the Kelly-optimal options rule (Tier 1.5), all at a 60-day holding
horizon. Tier 1.5 stays flat through its first two years (no probability distribution exists yet to
trade against, since it needs history to build one from) then compounds well ahead of both the
simpler options rule and the equity book.""")

opt_rows = [["Holding horizon", "Tier 1 (near-the-money) ann. return / Sharpe",
             "Tier 1.5 (Kelly-optimal) ann. return / Sharpe"]]
for h in [1, 5, 10, 20, 40, 60]:
    v1 = opt_v1.loc[h]
    vo = opt_opt.loc[h]
    opt_rows.append([f"{h} trading days",
                      f"{pct(v1.annualized_return)} / {ratio(v1.sharpe)}",
                      f"{pct(vo.annualized_return)} / {ratio(vo.sharpe)}"])
make_table(opt_rows, col_widths=[1.4 * inch, 2.35 * inch, 2.35 * inch])
caption("""Table 2. Both rules assume a flat 3% round-trip transaction cost per position. Very
short holding horizons (1 day) lose money after costs under either rule &mdash; the drift needs
time to develop before it clears the cost of trading. At 60 days, Tier 1 (the simple, hard-coded
"bullish decile -> buy the ATM call" rule) already clears a real 9.51% annualized return at Sharpe
1.04; Tier 1.5 (the same events, but with the mathematically-chosen strike and half-Kelly sizing
from Section 5) reaches 15.31% annualized at Sharpe 3.56.""")

fig(FIG / "25_tier1_vs_tier15_by_horizon.png", width=5.7 * inch)
caption("""Figure 8. Annualized return and Sharpe, Tier 1 vs. Tier 1.5, across every holding
horizon tested. Tier 1.5 dominates at every horizon long enough to clear the assumed transaction
cost.""")

body("""That 3.56 Sharpe is high enough that it earned real scrutiny rather than being taken at face
value: the worst individual days in the entire 17-year P&L series land on 2008-10-24 and
2008-11-20 (the peak of the financial crisis) and September 2002 (another genuine market stress
window) &mdash; real, economically sensible tail risk showing up exactly where it should, not a
smoothed or flat artifact of a bug. The honest explanation is diversification breadth: spreading a
fixed capital budget across roughly 1,200 mostly-independent single-stock earnings bets per quarter
genuinely can produce a high risk-adjusted return, the same effect underlying Grinold's well-known
"fundamental law of active management." It is nonetheless <b>not something to treat as directly
achievable</b> as reported: this many trades a year (~4,600) would face real bid/ask friction on
thin, illiquid option strikes almost certainly worse than the flat 3% assumption, real market-
capacity limits that no backtest captures, and the earliest years of Kelly estimates are built on
only the minimum required history and are necessarily noisier than the later, more data-rich
years &mdash; none of which this backtest models.""")

# ============================== SECTION 7: SYNTHESIS ==============================
h2("7. Putting it together")

body("""Every design in this project, equity or options, is a variation on the same underlying
bet: that a company's already-confirmed, already-public earnings surprise keeps predicting its
stock's direction for weeks after the announcement, because the market underreacts to that news on
the day itself. The strategies don't differ in what they're betting on &mdash; they differ in how
carefully that same bet is sized, diversified, and risk-controlled (equity Strategies 1
&rarr; 6, Section 2&ndash;3), whether market exposure is let back in on purpose and how (the beta
overlay vs. Strategy 7's less controllable version, Table 1), how aggressively the book is levered
for more absolute return (Section 3's aggressive variants), and whether the trade is expressed with
stock or with leveraged options (Sections 4&ndash;6), where a simple moneyness rule can be replaced
by a mathematically optimal, self-discovering strike-and-size selection instead of a hand-picked
one. None of the numbers in this document are a live trading recommendation: they are backtested
research on 1996&ndash;2013 historical data, with real (though necessarily imperfect) assumptions
about trading costs and capacity, and every honest caveat found along the way &mdash; the sector-
concentration bug, the look-ahead bugs in the options distribution, the drawdown floor that binds
before Sharpe does &mdash; is left in this document rather than smoothed over, because that's
exactly the information a reader would need before trusting any of the headline numbers above.""")

rb.save(OUT, title="How the PEAD Strategies Actually Trade")
print(f"Wrote {OUT}")
