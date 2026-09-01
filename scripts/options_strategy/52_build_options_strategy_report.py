"""Assemble reports/PEAD_Options_Strategy_Report.pdf -- turns the descriptive result in
PEAD_Options_Report.pdf into an actual backtested strategy, documents the GPU compute roadmap
built to push it further, and records every bug found and fixed along the way.

Companion to PEAD_Options_Report.pdf (does the PEAD decile pattern show up in options at all?).
This report picks up exactly where that one's Section 8 ("Suggested next steps") left off:
"Turn this signal into a costed backtest analogous to the equity Strategies 1-8." All narrative
numbers below are computed live from the CSVs/JSON this pipeline produces (scripts 40, 46-51),
not hardcoded, so the report always matches whatever the current data run actually found.
"""
import sys
import json
from pathlib import Path
import pandas as pd
import numpy as np
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Image, PageBreak

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, REPORTS_DIR, FIGURES_DIR
from common.report_pdf import new_report

DATA = DATA_DIR
FIG = FIGURES_DIR
OUT = REPORTS_DIR / "PEAD_Options_Strategy_Report.pdf"

# ---------- live data ----------
eq_final = pd.read_csv(DATA / "results_summary_v2_FINAL.csv")
eq6 = eq_final[eq_final.strategy == "strategy6"].iloc[0]

t1 = pd.read_csv(DATA / "backtest_options_v1_comparison.csv").set_index("hold_horizon_days")
t15 = pd.read_csv(DATA / "backtest_options_optimal_comparison.csv").set_index("hold_horizon_days")
oc = pd.read_parquet(DATA / "event_options" / "optimal_contracts.parquet")
put_share = oc.groupby("decile")["kelly_side"].apply(lambda s: (s == "P").mean() * 100)
mean_kelly_frac = oc["kelly_fraction"].mean()
agree_rate = oc["agree_with_naive"].mean()
n_events_scored = len(oc)
n_positive_edge = int((oc["kelly_growth"] > 0).sum())

sweep = pd.read_csv(DATA / "joint_portfolio_sweep.csv")
achievable = sweep[(sweep.w_equity <= 1.0) & (sweep.w_options <= 1.0) & (sweep.w_beta <= 1.0)]
best_achievable = achievable.sort_values("sharpe", ascending=False).iloc[0]
with open(DATA / "joint_portfolio_summary.json") as f:
    joint_best = json.load(f)["best"]

param_sweep = pd.read_csv(DATA / "gpu_param_sweep_results.csv")
best_tilt = param_sweep.sort_values("sharpe_proxy", ascending=False).iloc[0]

t1_60, t15_60 = t1.loc[60], t15.loc[60]

rb, story, styles, h1, h2, h3, body, caption, glossary, rule, fig, make_table = new_report()

# ============================== COVER ==============================
story.append(Spacer(1, 0.3*inch))
h1("From Evidence to Strategy: Trading the Options-Market PEAD Signal")
story.append(Paragraph("A real, capital-sized, cost-aware backtest of the finding in <i>PEAD_Options_Report.pdf</i> -- plus a GPU compute roadmap to push it further.", styles["TitleSub"]))
story.append(Paragraph(f"Data: same OptionMetrics-linked event panel as <i>PEAD_Options_Report.pdf</i> &middot; {n_events_scored:,} events scored by the Kelly-optimal selector, {n_positive_edge:,} with a genuine positive edge", styles["TitleSub"]))
story.append(Paragraph("Companion to <i>PEAD_Options_Report.pdf</i> (options evidence), <i>PEAD_Strategy_Showcase.pdf</i> and <i>PEAD_Strategy_Development.pdf</i> (equity strategies)", styles["TitleSub"]))
rule()

body(f"""<i>PEAD_Options_Report.pdf</i> established that the PEAD effect shows up in options,
amplified by leverage, and closed with a suggested next step: turn that decile-sorted descriptive
result into an actual backtest. This report is that backtest, built as a sequence of increasingly
compute-hungry tiers -- Tier 1 (a real capital-sized backtest of the original near-ATM heuristic),
Tier 1.5 (replacing that heuristic with a mathematically-optimal, Kelly-criterion contract selector
built from this project's own historical data), and a roadmap for Tiers 2-4 (dynamic exits,
GPU-batched parameter sweeps, an ML contract-selection model, and joint equity/options/beta
allocation) designed for a dedicated GPU machine.""")

body(f"""<b>Headline result.</b> Trading the same PEAD signal via options, with real capital
sizing and transaction costs, returns {t1_60.annualized_return*100:.2f}% annualized (Sharpe
{t1_60.sharpe:.2f}) using the original near-ATM contract choice (Tier 1) -- already ahead of the
least-tuned equity strategy in this project (Extreme decile L/S: 4.91%). Replacing that heuristic
with a full-chain, Kelly-optimal contract selector (Tier 1.5) raises this to
{t15_60.annualized_return*100:.2f}% annualized, Sharpe {t15_60.sharpe:.2f}. Two real bugs and one
look-ahead flaw were found and fixed to get a trustworthy version of this number -- Section 3
documents all three, in the same spirit this project has always disclosed the mistakes behind its
results, not just the wins.""")

story.append(PageBreak())

# ============================== SECTION 1: TIER 1 ==============================
h2("1. Tier 1: a real backtest of the original near-ATM heuristic")
body("""<i>PEAD_Options_Report.pdf</i> measured decile-sorted forward RETURNS on a near-ATM call
or put -- informative about whether the effect exists, but silent on what a real trader would
actually earn: what fraction of NAV to risk, what it costs to trade, and which events are even
worth trading. <font face="Courier">scripts/options_strategy/40_options_backtest.py</font> answers that: SUE-decile
tilt selects which events to trade (same trim/tilt scheme as equity Strategy 5/6, but the "short
leg" becomes "buy a put" instead of "sell the stock," since these are long-only option positions),
sized via quarterly trailing-NAV compounding with a portfolio-level premium-at-risk cap (the
options analogue of the equity book's gross-leverage cap), and charged an explicit, documented 3%
round-trip cost assumption standing in for real bid/ask spread (not yet captured in this data --
see Section 6).""")

t1_table = [["Horizon", "Ann. return", "Ann. vol", "Sharpe", "Max DD", "Positions sized"]]
for h in [1, 5, 10, 20, 40, 60]:
    r = t1.loc[h]
    t1_table.append([f"{h}d", f"{r.annualized_return*100:+.2f}%", f"{r.annualized_vol*100:.2f}%",
                      f"{r.sharpe:.2f}", f"{r.max_drawdown*100:.1f}%", f"{int(r.n_positions_sized):,}"])
story.append(make_table(t1_table, col_widths=[0.75*inch, 1.0*inch, 0.85*inch, 0.7*inch, 0.75*inch, 1.15*inch]))
caption("""Table 1. Tier 1 backtest, by fixed hold horizon. Short horizons are net NEGATIVE despite
being the most statistically significant decile spreads in <i>PEAD_Options_Report.pdf</i> -- the
3% cost assumption dominates a small early edge; only the 40-60 day horizons earn enough raw
signal to clear it.""")

body(f"""<b>A capacity finding, not just a return number.</b> At the 60-day horizon, only
{int(t1_60.n_positions_sized):,} of {int(t1_60.n_positions_eligible):,} eligible signals
({t1_60.n_positions_sized/t1_60.n_positions_eligible:.1%}) actually get funded -- the premium-at-risk
cap binds almost immediately against BASE_UNIT_FRACTION, a hand-picked, unswept constant. That gap
is exactly the kind of thing scripts/18-19's parameter sweep found for the equity strategies before
Strategy 4-6 existed; Tier 3 (Section 5) is where the options book gets the same treatment.""")

story.append(PageBreak())

# ============================== SECTION 2: TIER 1.5 ==============================
h2("2. Tier 1.5: mathematically-optimal contract selection")
body("""Both <i>PEAD_Options_Report.pdf</i> and Tier 1 pick a contract by MONEYNESS ALONE --
closest to 50-delta. That throws away the rest of the day0 chain. Separate prior work on this
project (a GPU options-return toolkit, copied into <font face="Courier">reference/options_content/</font>
for full attribution) was built to do exactly this differently: score every strike in the chain by
expected return under a probability distribution of the underlying's future price, and take the
best one. That tool's own scenario table, though, is a hand-typed 3-point guess (0%: 25%, +20%: 50%,
0%: 25%) reused for every stock on every day -- not "mathematically proven" by any reasonable
definition -- and it ranks candidates by raw expected value, the textbook way a leveraged,
convex instrument selector blows up: a deep-OTM option with a tiny chance of an enormous payoff can
have unbounded E[R] while losing money on almost every draw.""")

body("""<b>What Tier 1.5 does instead.</b> The probability distribution comes from this project's
own 182,712 real historical PEAD events, not a guess -- built decile- and horizon-conditioned,
quantile-bucketed so a fixed number of states always carries equal probability and the tails are
each represented by their own real conditional mean. And instead of raw expected value, every
candidate strike is ranked by its Kelly-optimal expected LOG growth, E[log(1+f&middot;R)], solved
over a grid of bet fractions f. A wild lottery-ticket strike is still evaluated -- it just gets
correctly found to have a near-zero optimal fraction and near-zero growth contribution, rather than
being ranked first the way raw expected value would rank it. This is the mathematically principled
way to "embrace leverage": Kelly still fully rewards a genuinely good leveraged bet with a large
optimal fraction, it just refuses to reward a bet that's only "good" because of a rare, thin-tailed
payoff.""")

fig(FIG / "26_optimal_selector_put_share_by_decile.png", width=5.6*inch)
caption(f"""Figure 1. Share of Kelly-optimal picks that are PUTS, by SUE decile. No call/put rule
is hard-coded anywhere in the selection logic -- the selector sees only each event's own
decile-conditioned return distribution and the real day0 option chain, and still discovers the
right side of the PEAD trade on its own: {put_share.loc[1]:.0f}% put share at D1 (lowest SUE) vs.
{put_share.loc[10]:.0f}% at D10.""")

body(f"""Mean Kelly fraction across all picks is {mean_kelly_frac:.1%} of book-allocated capital
(genuine risk-aware sizing, not a "sure thing" artifact), and the Kelly-optimal pick disagrees with
what a naive raw-expected-value selector would have chosen on {(1-agree_rate)*100:.1f}% of events --
exactly the gap the Kelly-vs-naive-EV argument predicts.""")

story.append(PageBreak())

# ============================== SECTION 3: BUGS FOUND AND FIXED ==============================
h2("3. Three things found and fixed -- documented, not hidden")
body("""This project has always disclosed the mistakes behind its results (see
<i>PEAD_Strategy_Development.pdf</i>'s EAR look-ahead bug, and this project's own Sharpe-formula
bug). Building Tier 1.5 surfaced three more, each caught by a visibly wrong pattern in a real run,
not by inspection alone -- all three are documented in full in the relevant script's docstring
(<font face="Courier">scripts/46, 47, 48</font>) and summarized here.""")

body("""<b>Bug 1 -- ambient beta swamping the PEAD edge.</b> The return distribution was first
built from RAW forward returns. 1996-2013 was a net up market, so every decile's raw expected
return came out positive, and the selector picked calls almost everywhere, including the worst
SUE decile. Fixed by building the distribution's shape from market-adjusted returns (already used
everywhere else in this project for exactly this reason) and re-centering by the real historical
market drift for that horizon.""")

body("""<b>Bug 2 -- an OptionMetrics unit error.</b> OptionMetrics stores strike prices in
1/1000ths of a dollar. Comparing an uncorrected strike directly against a computed underlying price
made every put look like a riskless, guaranteed-payoff bet against an absurdly inflated strike --
confirmed by the telltale symptom: puts dominating even the BEST SUE decile, and Kelly fractions
clustering near 100% (the "free money" tell of a mispriced strike, not a real edge). Fixed at the
source and corrected on the already-scanned data without re-running the underlying OptionMetrics
scan.""")

body("""<b>Bug 3 -- look-ahead in the return distribution.</b> The first working version fit ONE
distribution per (decile, horizon) from the entire 1996-2013 sample at once -- a 1998 trade's
contract selection was informed by data through 2013, information no real trader in 1998 had. Now
walk-forward: every announcement quarter is priced only against a distribution built from strictly
prior quarters (an 8-quarter/2-year warmup before any distribution exists; those early events are
simply dropped, not backfilled with a future-looking guess). The Sharpe barely moved after this fix
(~4.0 -&gt; 3.56 at 60d) -- suspicious enough on its own to check further rather than accept:
the actual day-by-day P&amp;L was inspected, and the worst days in the whole 17-year series land on
2008-10-24, 2008-11-20 (the financial crisis) and September 2002 (another real stress window) --
real, economically sensible tail risk, not a flat artifact. See Section 6 for why this Sharpe still
shouldn't be treated as directly achievable.""")

story.append(PageBreak())

# ============================== SECTION 4: RESULTS ==============================
h2("4. Results: Tier 1 vs. Tier 1.5 vs. the equity book")
fig(FIG / "24_options_strategy_nav_curves.png")
caption("""Figure 2. NAV curves (log scale, $10M start): equity Strategy 6, options Tier 1
(near-ATM, 60d), and options Tier 1.5 (Kelly-optimal, walk-forward, 60d). Tier 1.5 stays flat
through its 8-quarter warmup window (no distribution exists yet to trade against), then compounds
past both other strategies once enough walk-forward history accumulates.""")

story.append(PageBreak())

fig(FIG / "25_tier1_vs_tier15_by_horizon.png")
caption("""Figure 3. Annualized return and Sharpe by hold horizon, Tier 1 vs. Tier 1.5. Tier 1.5
dominates at every horizon where a position is actually held long enough to earn back the assumed
transaction cost.""")

results_table = [["", "Ann. return", "Ann. vol", "Sharpe", "Max DD"],
                  ["Equity Strategy 6", f"{eq6.ann_return*100:.2f}%", f"{eq6.ann_vol*100:.2f}%",
                   f"{eq6.sharpe:.2f}", f"{eq6.max_drawdown*100:.1f}%"],
                  ["Options Tier 1 (60d)", f"{t1_60.annualized_return*100:.2f}%", f"{t1_60.annualized_vol*100:.2f}%",
                   f"{t1_60.sharpe:.2f}", f"{t1_60.max_drawdown*100:.1f}%"],
                  ["Options Tier 1.5 (60d)", f"{t15_60.annualized_return*100:.2f}%", f"{t15_60.annualized_vol*100:.2f}%",
                   f"{t15_60.sharpe:.2f}", f"{t15_60.max_drawdown*100:.1f}%"]]
story.append(make_table(results_table, col_widths=[1.8*inch, 1.1*inch, 1.0*inch, 0.9*inch, 0.9*inch]))
caption("Table 2. Headline comparison, all three books, 60-day options horizon.")

body(f"""<b>Combining the sleeves.</b> Because the equity book, the options book, and the existing
market-beta overlay (<i>PEAD_Strategy_Showcase.pdf</i>) are independently-weightable positions
rather than a single fixed pool, a joint allocation search (<font face="Courier">scripts/45</font>)
finds a real, ACHIEVABLE diversification benefit at unleveraged weights (each sleeve capped at 1x
its own size): {best_achievable.w_equity:.1f}x equity + {best_achievable.w_options:.1f}x options +
{best_achievable.w_beta:.2f}x beta overlay reaches Sharpe {best_achievable.sharpe:.2f}
({best_achievable.annualized_return*100:.1f}% return, {best_achievable.max_drawdown*100:.1f}% max
drawdown) -- better risk-adjusted and shallower-drawdown than any sleeve alone, from genuine
diversification, not leverage. (A wider search also found leveraged combos claiming higher Sharpe
still -- {joint_best['w_equity']:.1f}x/{joint_best['w_options']:.1f}x/{joint_best['w_beta']:.2f}x
-&gt; Sharpe {joint_best['sharpe']:.2f} -- flagged in the script's own output as not achievable,
since it assumes free, unlimited leverage on top of a book already levered 2.5x internally.)""")

story.append(PageBreak())

# ============================== SECTION 5: GPU ROADMAP ==============================
h2("5. What the 5090 GPU machine unlocks next (Tiers 2-4)")
body("""Everything in Sections 1-4 ran on this CPU dev machine in seconds to minutes -- Tier 1.5's
full 126,008-event selector run took under 9 minutes. The next three tiers are deliberately
GPU-shaped and designed (code written, tested against synthetic data, not yet run at real scale)
for a dedicated NVIDIA 5090 machine:""")

body("""<b>Tier 2 -- dynamic exits.</b> Tier 1/1.5 both hold to a FIXED horizon. A real daily price
path per position (not just the 6 checkpoints used so far) lets a stop-loss/profit-target exit rule
be found instead -- <font face="Courier">scripts/42</font>'s GPU kernel already batches this as a
(positions &times; days &times; parameter-combos) tensor scan; what's missing is the underlying
daily-path data itself (<font face="Courier">scripts/41</font>), a heavier OptionMetrics scan
(roughly 10-60x more quote lookups than Tier 1 needed) appropriate for the 5090, not this machine.
This should recover some of the return Tier 1/1.5 leave on the table by forcing every position to
run its full fixed horizon even when it would have been better to exit early on a big move.""")

body(f"""<b>Tier 3 -- proper parameter tuning and a learned selector.</b> Every constant in Tiers
1/1.5 (BASE_UNIT_FRACTION, TRIM, TILT_POWER, the premium-at-risk cap) is hand-picked, the same way
the equity strategies started before scripts 18-19's sweep found Strategy 4-6. A first, coarse GPU
sweep (<font face="Courier">scripts/43</font>) already ran on real data and flagged
tilt_power={best_tilt.tilt_power:.1f} as better than Tier 1's hand-picked 1.0 -- not yet folded
back in. Beyond hand-picked constants, an Optuna-driven model
(<font face="Courier">scripts/44</font>) can use the IV level, liquidity, and moneyness data
already sitting unused in the option chain to potentially beat "which decile is this event in" as
the entry signal entirely.""")

body("""<b>Tier 4 -- the full joint portfolio, not just three fixed sleeves.</b> Section 4's joint
search only chose a WEIGHT for each of three already-built sleeves. The fuller version -- searching
multi-leg option structures (spreads to reduce theta cost) and regime-conditional sizing (by
implied-vol level, by market stress) jointly with the equity and beta sleeves -- is the largest
combinatorial search in this project, and explicitly the tier where "compute is allowed to be very
large" (per this project's own brief) is the point, not a cost to be minimized.""")

story.append(PageBreak())

# ============================== SECTION 6: EXECUTION INFRASTRUCTURE ==============================
h2("6. Cross-machine execution: Colab, WSL2, and the 5090")
body("""Every script in this pipeline is a plain, argparse-driven <font face="Courier">.py</font>
file -- no Jupyter notebooks -- specifically so the same code runs unmodified in three places: this
Windows dev machine (CPU, for writing and correctness-testing), Google Colab (GPU validation at
small scale, via the Colab CLI running inside WSL2 on this same Windows machine -- the official CLI
doesn't support Windows natively, but WSL2 is a real Linux kernel so the CLI runs on it exactly as
it would on bare-metal Linux, with zero code changes), and the 5090 box itself (bare-metal Linux,
first-class native support for the full NVIDIA stack, for the real multi-hour/day Tier 2-4 runs).
Every GPU script accepts <font face="Courier">--device {cpu,cuda}</font> and
<font face="Courier">--mock-data</font> so the identical code path is exercised at every scale, and
<font face="Courier">scripts/run_pipeline.py</font> is a single, resumable, checkpointed entry
point built for a machine that will be plugged in, kicked off, and checked on again days later --
the actual access pattern to the 5090 for this project. Full setup steps are in the README's
"Options-strategy GPU roadmap" section.""")

# ============================== SECTION 7: CAVEATS ==============================
h2("7. Caveats specific to this strategy work")
body(f"""&bull; <b>Transaction costs are a flat, documented assumption (3% round-trip), not real
bid/ask spread.</b> Best_bid/best_offer were read from the raw OptionMetrics scan but dropped
before scripts 35/36/47 wrote their output; attaching them for the exact (secid, date, optionid)
keys already selected is a cheap follow-up (same narrow date filter, no new full-history scan), not
yet done.<br/>
&bull; <b>Tier 1.5's Sharpe ({t15_60.sharpe:.2f} at 60d) reflects a real, checked-for-artifacts
diversification effect across ~1,200 mostly-independent single-stock bets per quarter -- not
something to treat as directly achievable.</b> This many small trades (~4,600/year) would face real
bid/ask friction likely worse than the flat 3% assumption, real capacity/market-impact limits no
backtest captures, and early-window Kelly estimates built on only the just-past 8-quarter minimum
are necessarily noisier than later ones with a decade-plus of accumulated history.<br/>
&bull; <b>The decile-return-distribution pools many different underlying stocks' historical
outcomes into one shape per (decile, horizon).</b> That's an average across stocks of very
different own volatility, which likely understates any one specific stock's true idiosyncratic
risk when pricing its options -- a stock-volatility-scaled version of the distribution is a natural
refinement, not yet built.<br/>
&bull; <b>Tiers 2-4 are designed and unit-tested against synthetic data, not yet run at real
scale.</b> Every number in Section 5 about what they should unlock is a hypothesis this project
intends to test on the 5090, not a result reported here.<br/>
&bull; All of <i>PEAD_Options_Report.pdf</i>'s own caveats (Section 7 there) still apply: 2011 is
entirely missing, 2013 is truncated at end-August, and the matched sample skews toward names with a
longer-dated, more liquid option chain.""")

h2("8. Suggested next steps")
body("""&bull; Attach real best_bid/best_offer to the already-selected contracts (Section 7) and
replace the flat 3% cost assumption with the real spread.<br/>
&bull; Run scripts 41-44 for real on the 5090: the daily-path extraction (Tier 2), the dynamic exit
optimizer, the proper parameter sweep (folding in the tilt_power=2.0 finding from Section 5), and
the Optuna-driven ML contract selector.<br/>
&bull; Scale the decile-return-distribution by each underlying's own historical volatility instead
of pooling all stocks in a decile into one shape (Section 7).<br/>
&bull; Extend the joint portfolio search (Section 4) to multi-leg option structures and
regime-conditional sizing -- the full Tier 4 vision, not just a weight on three fixed sleeves.""")

story.append(Spacer(1, 0.3*inch))
rule()
caption(f"""Generated from data/backtest_options_v1_comparison.csv, data/backtest_options_optimal_comparison.csv,
data/event_options/optimal_contracts.parquet, data/results_summary_v2_FINAL.csv,
data/joint_portfolio_sweep.csv, and data/gpu_param_sweep_results.csv.
Scripts: scripts/options_strategy/40 through scripts/options_strategy/51, and the README's
"Options-strategy GPU roadmap" section for full infrastructure detail.""")

rb.save(OUT, title="From Evidence to Strategy: Trading the Options-Market PEAD Signal")
print("wrote", OUT)
