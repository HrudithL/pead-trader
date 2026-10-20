"""Options strategies -- Kelly-optimal contract selection around earnings.

WHAT IT DOES. For every earnings announcement, look at the whole option chain (every strike, calls AND
puts, ~13 contracts/event on average, up to 60) on the announcement day and buy the single contract
with the highest expected LOG growth under an empirical, walk-forward distribution of the stock's
60-day return conditional on its SUE decile. The direction is never hard-coded: the math picks puts
for bad surprises and calls for good ones on its own (32% puts in D1 vs 5% in D10).

WHY KELLY AND NOT EXPECTED VALUE. A naive "maximize E[return]" selector loves deep-OTM lottery
tickets (huge payoff x tiny probability = big mean) that lose money on almost every draw. Scoring
each contract by max over f of E[log(1 + f*R)] penalizes ruin: a lottery ticket is still scored, it
just earns a tiny optimal fraction f* and negligible growth. The selector therefore prefers
contracts whose payoff is large exactly in the scenarios the SUE decile makes likely.

HOW IT IS SIZED. Only events whose best contract has positive expected log growth are traded.
Each such event's size is HALF its own Kelly fraction (full Kelly over-bets when the distribution is
itself an estimate), then the quarter's candidates share a premium budget (`max_premium_frac` of
trailing NAV minus premium still outstanding from earlier quarters) in proportion to those fractions.

WHERE THE RETURN COMES FROM (see the generated report): OTM calls on low-volatility, high-SUE stocks
-- the post-announcement upside tail is under-priced relative to the drift -- diversified across
~1,200 mostly independent single-stock bets per quarter (Grinold's breadth), which is what turns a
modest per-trade edge into a Sharpe of 3+.

THE BACKTEST CONVENTION THAT WAS WRONG, AND THE FIX. The original backtest valued a position at each
checkpoint (+1, 5, 10, 20, 40, 60 trading days) from the contract's mid quote and, when the quote was
missing, CARRIED THE PREVIOUS QUOTE FORWARD. 22% of picks have no quote at 60 days, and 93% of those are not
missing at all: the bid is ZERO (best_offer ~ $0.10) -- the option has died and nobody will buy it. Carrying
its last live price forward never marks a dying option down. The strategies below value a zero-bid checkpoint
at half the ask (or at zero, for the conservative bound) using the quotes now kept by the forward-price prep
stage. Effect on the 20%-premium-cap book: 8.58% / Sharpe 3.78 as originally reported -> 5.87% / 2.50 with
the corrected mark -> 4.12% / 1.77 once each contract also pays at least one $0.05 tick round trip ->
2.34% / 0.99 if dead options are worth nothing.

REMAINING CAVEATS: no market impact; the early walk-forward distributions rest on only 8 quarters of
history; ~4,600 trades a year would meet real capacity limits; most of the profit comes from 2003 and 2013.
"""
import numpy as np

from pead.engines.options import OptionsSizing, run_options_backtest
from pead.strategies.base import BacktestResult, OptionsStrategy
from pead.strategies.registry import register_instance


class KellyOptimal(OptionsStrategy):
    family = "options-kelly"
    live_ready = True
    description = "Kelly-optimal contract per earnings event, half-Kelly sized, shared premium budget"
    thesis = ("Buy the contract whose payoff profile best matches the SUE-conditioned return "
              "distribution, sized by Kelly, spread across hundreds of independent events.")

    def __init__(self, name, cap, horizon=60, parent=None, status="validated", description=None, **sizing_kw):
        self.name, self.parent, self.status = name, parent, status
        if description:
            self.description = description
        self.sizing = OptionsSizing(hold_horizon=horizon, max_premium_frac=cap, **sizing_kw)

    def params(self):
        s = self.sizing
        return dict(hold_horizon=s.hold_horizon, half_kelly=s.half_kelly, max_premium_frac=s.max_premium_frac,
                    roundtrip_cost_pct=s.roundtrip_cost_pct, dead_quote_mark=s.dead_quote_mark, fill_mode=s.fill_mode,
                    min_roundtrip_cost_per_share=s.min_roundtrip_cost_per_share, min_premium=s.min_premium)

    def eligible(self, uni):
        """Hook for subclasses/filters: boolean mask over uni.picks of events allowed to trade."""
        return None

    def run(self, ctx):
        uni = ctx.options()
        daily, qlog, positions, summary = run_options_backtest(
            uni.picks, uni.calendar, self.sizing, uni.initial_capital, self.eligible(uni))
        return BacktestResult(self.name, "options", daily, summary, qlog, positions, self.meta())


# Real fills: buy at the day-0 ask, sell at the final checkpoint's bid, dead quotes at half-ask. The quoted spread IS the
# cost, so the flat 3% and the $0.05 tick floor are off (set either > 0 to charge extra friction on top).
register_instance(KellyOptimal("options_cap20", 0.20, roundtrip_cost_pct=0.0,
                               description="Kelly picks, 20% premium cap, bought at the ask / sold at the bid, dead quotes at half-ask"))
register_instance(KellyOptimal("options_cap30", 0.30, parent="options_cap20", roundtrip_cost_pct=0.0,
                               description="Kelly picks, 30% premium cap, bought at the ask / sold at the bid, dead quotes at half-ask"))
# The conservative bound: a dying option is worth nothing (liquidating means hitting the zero bid).
register_instance(KellyOptimal("options_worst_case", 0.20, parent="options_cap20", status="research",
                               roundtrip_cost_pct=0.0, dead_quote_mark="zero_bid_zero",
                               description="options_cap20 with dead quotes worth zero -- the pessimistic bound"))
# MID-FILL reference: the previous headline convention (mid in / mid out, 3% + >= $0.05/share flat cost, dead quotes at
# half-ask: 4.12% / Sharpe 1.77). Kept so that number stays reproducible and the size of the fill fix stays visible.
register_instance(KellyOptimal("options_cap20_midfill", 0.20, parent="options_cap20", status="reference", fill_mode="mid",
                               min_roundtrip_cost_per_share=0.05,
                               description="options_cap20 with the previous convention: mid fills, flat 3% + >= $0.05/share cost"))
register_instance(KellyOptimal("options_cap30_midfill", 0.30, parent="options_cap20_midfill", status="reference", fill_mode="mid",
                               min_roundtrip_cost_per_share=0.05,
                               description="options_cap30 with the previous convention: mid fills, flat 3% + >= $0.05/share cost"))
# As ORIGINALLY reported (dead quotes carried forward at their last live price, flat 3% cost). Kept so the old
# headline numbers stay reproducible and so the size of the convention's effect stays visible -- not for use.
for _cap, _par in ((0.20, None), (0.30, "options_cap20_legacy"), (0.40, "options_cap20_legacy")):
    register_instance(KellyOptimal(f"options_cap{int(_cap * 100)}_legacy", _cap, parent=_par, status="reference",
                                   dead_quote_mark="carry_last", fill_mode="mid",
                                   description=f"Kelly picks, {int(_cap * 100)}% cap, ORIGINAL convention: dead quotes carried forward, flat 3% cost"))
