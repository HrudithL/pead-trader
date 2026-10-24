# Options PEAD audit — 2026-10-21

## Existing corrected result

`options_cap20` earns 0.282% annualized, Sharpe 0.156, and has a −18.9% maximum drawdown in the committed 1996–2013 simulation (`results/options_cap20/summary.json`). With zero-bid contracts valued at zero, `options_worst_case` loses 1.04% annualized. The original 8.58%/3.78 return/Sharpe came from midpoint fills and stale marks; it is not an executable benchmark (`docs/FIXES.md`). The option data omit 2011 and end in August 2013.

The committed `summary.json` still contains `"live_ready": true`. That flag predates this audit and must not be treated as a deployment decision.

The linked top/bottom-two-decile event file contains 72,170 events. Of those, 7,126 have unknown announcement time encoded as 00:00, and all 7,126 are assigned a day-0 date at least one calendar day after the announcement date. Another 8,509 known-time announcements during market hours have day 0 on the same date; the option quotes are end-of-day observations without a matching quote timestamp, so their post-announcement availability still needs verification. Known after-16:00 releases are not assigned the same date as day 0. This is a partial clock audit, not a fill-timing proof.

The nominal 20% cap book has 59,153 funded positions. At the 60-day exit, 39,518 (66.8%) have a positive bid, 17,195 (29.1%) have a zero bid, and 2,440 (4.1%) have no bid. The existing engine credits approximately $2.085M of half-ask proceeds to zero-bid exits and carries approximately $1.807M of prior marks for missing-bid exits. The reported entire-book net profit is only $0.482M. A missing bid may reflect a data or corporate-action problem rather than a worthless contract, but carried prices cannot establish executable proceeds.

Capacity is also unresolved. In the committed funded book, 72.2% of selected contracts have zero recorded entry-day volume, 79.1% of desired orders exceed that day's volume, 21.7% have zero open interest, and 50.3% of orders exceed 10% of open interest. Entry-day volume is an after-the-fact diagnostic, not a valid entry filter at the close. Open interest is a conservative sizing screen, not proof of available displayed size.

## Code findings

| Component | Finding | Consequence |
|---|---|---|
| `pead/prep/options/scan_full_chain.py` | Retains one expiration near 120 calendar days, minimum 95. | No historical expiry optimization has been performed. |
| `pead/prep/options/optimal_contract_selector.py` | Scores 60-trading-day intrinsic payoff using entry midpoint and a 0.05–0.95 Kelly fraction grid; arbitrarily truncates chains above 60 rows. | Score differs from ask-entry/bid-exit P&L; 808 chains have more than 60 contracts. |
| `pead/prep/options/return_distributions.py` | Uses all announcements from earlier quarters, including forward return labels not matured by the next quarter's start. | Distribution can see future outcomes. |
| `pead/engines/options.py` | Quarter's event sizing normalizes Kelly weights using later announcements from the same quarter. | Earlier orders depend on unknown future events. |
| `pead/engines/options.py` | Falls back to midpoint if an entry ask is missing; half-ask at a zero-bid exit; carries a mark if exit bid is missing. | Executable returns are overstated or unidentified. |
| `pead/engines/options.py` | Uses six checkpoints, and trades around missing 2011 can bridge the year gap. | Daily Sharpe is based on sparse marks; 1,192 funded 2010 entries have synthetic 2012 exits across the gap. |
| `pead/reporting/attribution.py` | Defines `low vol` by a full-sample tercile of funded positions. | Attractive low-vol slice cannot be used as a point-in-time trading rule. |

The corrected report's low-vol, 3–20% OTM call slice earned about $1.13M on $8.92M premium under the original half-ask/carry convention. It was found after seeing the sample. Excluding 2003 and 2013, its net P&L is negative even under that convention; more than 60% of its contracts had zero entry-day volume. It is a research hypothesis, not a validated edge.

## Interpretation

PEAD is a tendency for stock returns to drift after an earnings surprise. A long call or put earns money only if the resulting option bid increase exceeds entry spread, time decay, volatility changes, and market impact. An equity short leg with positive *relative* alpha does not imply that buying a put has positive absolute expected value.

No finite historical sample can prove the globally most profitable future strategy. The defensible target is a predeclared rule that clears executable net-return, Sharpe, alpha, and capacity gates in an untouched forward sample. Until then, no options trade is the supported live decision.
