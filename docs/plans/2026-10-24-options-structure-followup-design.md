# PEAD Options Structure Follow-up Design

Date: 2026-10-24

## Decision

Use a matched-event comparison of the frozen top-SUE call strategy, a same-expiration debit call spread, and a stock control. Keep the current long-call selection fixed at target 75 calendar DTE, 0.70 absolute delta, and 40 trading-session exit. Preserve the study's conservative next-session entry convention. This distinguishes whether weak performance comes from the event signal, long-option carry, or the chosen structure without searching a new DTE/delta/hold grid.

## Compared instruments and execution

- **Stock control:** For each event, use the CRSP total-return path over the same entry and exit dates. At event level, scale shares to the long call's day-0 delta-equivalent shares. At portfolio level, use the same initial NAV, 20% outstanding investment cap, and 0.25% per-entry capital budget; stock invested dollars are market value while option invested dollars are net premium. Show both capital-normalized account returns and delta-dollar-normalized event returns because equal capital and equal directional exposure are distinct comparisons.
- **Long call:** Select the day-0 call under C/75/0.70/40 criteria, then enter the same option ID on the next OptionMetrics session at its ask and liquidate on the frozen exit session at its bid.
- **Debit call spread:** Pair that long call with a higher-strike call from the same expiration, targeting 0.30 short-leg delta, selected by nearest delta then narrower day-0 spread and option ID. Enter the long leg at ask and short leg at bid; liquidate long at bid and buy back short at ask. Use net debit as invested capital. A spread missing either leg at entry is ineligible; a missing exit leg gets zero spread proceeds. Limit size by the smaller leg's open interest. Record spread-only eligibility so the stock and long-call comparison can also be shown on their common matched sample.

Use day-0 quotes only to select contracts. Use next-session quotes only to determine whether selected contracts can be entered. Do not re-select a contract using future information. Report quote and eligibility attrition. The displayed daily quotes and 10%-of-open-interest participation rule remain imperfect execution proxies; no quote-size data are available.

## Signal and analyst revisions

Keep top-SUE membership and the low prior-quarter realized-volatility tercile frozen as the only primary filter comparison. Separately audit whether IBES detailed estimate history supports an analyst-revision predictor: match forecast horizons by ticker and fiscal-period end, require the same analyst to have a forecast before and after the earnings release, and use only revisions timestamped after the release and no later than the next-session entry cutoff. If `revtims`/`anntims` clock semantics, horizon mapping, or split basis cannot be validated, stop at a coverage/timestamp audit and do not report a revision strategy return. The current event panel's `consensus_statpers` is pre-announcement, so it cannot represent post-announcement revisions by itself.

## Inference and limits

The archived option data are 1998–2013, have no 2011 options file, and have partial 2013 coverage. These results are historical diagnostics, not an untouched holdout. Report event counts, quote coverage, trades, CAGR, excess-RF Sharpe, drawdown, four-factor alpha/HAC interval where daily NAV permits, cost sensitivity, and year-by-year stability. Do not retune thresholds to reach 12% or present the archive as evidence that 12% is achievable. Avoid adding any option strategy to the live registry.

## Alternatives considered

1. Continue searching DTE/delta/holding-period cells. Rejected for this follow-up because it expands the in-sample search before explaining the economic source of the call result.
2. Compare stock, long calls, and matched debit spreads on fixed events. Selected because it holds the news signal and holding period constant while isolating option convexity, theta, implied-volatility exposure, and spread friction.
3. Run only more volatility-subsample cuts. Deferred because the existing low-volatility result is a challenger and more slicing risks data-mined conclusions; volatility enters here only as a frozen low-vol comparison.
