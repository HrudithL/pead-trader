# OPT-01 go/no-go: should OPT-02..06 be built?

Evidence: `results/option_spread_report/` (day-0 chains, 19,291 top-SUE-decile events, 1996-2013, OptionMetrics EOD from D:), `docs/SIGNAL_REPORT.md`, existing options results.

## The numbers
- Edge the options would ride: mean 60-session market-adjusted stock return of top-decile events is 3.3% (before 2008-07) and 2.7% (after); raw 5.4% / 5.2%.
- Cost to cross the spread (ask in, bid out, same quotes at exit; one full spread), expressed as the stock move needed to break even = (ask-bid)/(|delta| x spot), medians:
  - ATM call, 30-60d: 1.4% (1.3% pre-2008, 1.5% post); relative spread 12.8% median
  - 0.8-delta call, 30-90d: 1.4% (1.3% pre, 1.9% post); relative spread 8.2%
  - 0.3-delta OTM call: 2.0%; relative spread 23.5%
  - 75th percentile breakevens are 2.4-3.8%, i.e. at or above the whole average edge.
- Availability: only 48% (ATM), 63% (0.8 delta) of events have a live-bid contract in the bucket on day 0; the share with relative spread <= 10% is 37% (ATM), 62% (0.8 delta), 16% (OTM).
- Equity comparison: the stock book pays 5-25 bps one way (engine cost table), so crossing costs are 5-10x higher in options before theta, financing or the exit quote disappearing (the picks' 60-day missing-quote rate was 22.6%, unmeasured for ITM).
- Existing results agree: `options_cap20` 0.28% CAGR / Sharpe 0.16, `options_worst_case` negative.

## Decision
**NO-GO for OPT-02 (post-reaction calls), OPT-04 (pre-earnings straddle), OPT-05 (risk reversal), OPT-06 (index put-spread overlay).** The average edge net of one spread leaves roughly 1.3-1.9 points of stock-equivalent return on the covered 50-60% of events, before theta and exit-quote risk, against the stock book's near-zero crossing cost; the holdout edge is smaller (2.7% vs 1.9% breakeven for 0.8-delta calls post-2008 leaves under 1 point). No leverage benefit changes this for a long-only book that is already cash-filled and never borrows.

**CONDITIONAL / LOW PRIORITY for OPT-03 (deep-ITM call as stock substitute).** It is the only structure with tolerable spreads (0.8-0.9 delta, median 7-8%), but its purpose would be capital efficiency, which does not matter while the book earns T-bill rates on idle cash (the series is typed from memory, ROADMAP #2). Revisit only if (a) real T-bill/FF data are in place, (b) the strategy needs leverage the no-borrowing rule forbids, or (c) a bid/ask-at-exit study for ITM contracts shows quote presence above 90% at +60 sessions.

## What was not measured
Exit-day quotes by bucket, intraday spreads, theta decay and early-exercise/dividend effects, 2011 (option file missing), and size-limited fills. None of these can make the result better than the quoted-spread bound used here.
