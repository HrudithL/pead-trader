# I/B/E/S Earnings Data: Findings

This note documents how the raw WRDS I/B/E/S pull was turned into a clean
earnings-surprise table, one row per (company, fiscal quarter), and what was
found along the way. It assumes no finance background.

## What "surprise" and "point-in-time" mean here

When a company reports quarterly earnings (its "actual EPS" -- earnings per
share), the stock market's reaction depends on whether that number beat or
missed what investors were expecting, not on the raw number itself. "Surprise"
is a measure of that gap: actual EPS minus an expectation, scaled so
surprises are comparable across companies and time. This project computes two
versions:

- `sue_analyst`: the expectation is the average Wall Street analyst estimate
  for that quarter (from I/B/E/S), and the scaling is how much analysts
  disagreed with each other (their estimates' standard deviation). This is
  the more faithful measure when analyst coverage exists.
- `sue_seasonal`: a mechanical fallback that does not need analyst data at
  all -- the expectation is simply "the same quarter's result a year ago,"
  scaled by how volatile that year-over-year change has historically been for
  that specific company.

"Point-in-time" means: only use information that was actually public at the
time being studied. Analyst consensus estimates get updated continuously --
including AFTER a company reports -- so it is easy to accidentally compute a
"surprise" using an estimate that was itself informed by the real result
(because it was calculated after the announcement). That is called
look-ahead bias, and it would make any later PEAD result meaningless, since
it would be testing whether the market reacts to information it never
actually had. Every consensus estimate used below is checked to have been
published strictly before the announcement it is being compared against.

## Coverage found

- Cleaned announcement dates (`anndats`) span **1995-01-03 to
  2014-06-30**. The output file is named `ibes_clean_1995_2014.parquet`
  to match -- the plan's working title referenced the project's core
  1996-2013 window, but the actual WRDS pull included a one-year lookback
  and roughly six-month forward buffer around it (by design, so that
  quarters right at each edge of the core window still have prior-year
  history and prior consensus snapshots to draw on). Nothing was trimmed to
  force-fit the original filename.
- This cleans I/B/E/S at the full resolved-universe level (every ticker that
  resolved from the 8,358-secid OptionMetrics-linked universe), not filtered
  to any pilot list -- the pilot filter is a separate, later slice.
- Raw `actu_epsus.parquet`: 393,503 rows. After dropping 3,033 non-USD-denominated rows (foreign-currency echoes of the same figure for cross-listed/ADR names) and 27,353 rows with no reported EPS value at all, 362,804 usable rows remained (291,176 distinct company/fiscal-quarter combinations before duplicate resolution).
- Raw `statsumu_epsus.parquet`: 5,595,520 rows. Filtered to the current-quarter consensus (`fpi='6'`, which turned out to already imply `measure='EPS'` and `fiscalp='QTR'` for every single row -- a clean filter, not three independent judgment calls) and USD-denominated: 731,968 rows matched the fpi filter, and 3,779 of those were also non-USD and dropped, leaving 728,189 usable consensus snapshots.

## A significant data-quality finding: quarterly vs. annual actuals were conflated

The raw `actu_epsus` pull has no column distinguishing a quarterly actual EPS
from a full fiscal-year actual EPS (WRDS's I/B/E/S actuals table has one, but
it was not part of this project's pulled column set). Because a company's
fiscal Q4 and its fiscal year-end fall on the exact same calendar date, this
means the SAME (ticker, period-end date) can carry two genuinely different,
both-valid EPS figures -- the quarter alone, and the full year -- with no
column saying which is which.

This is not a rare edge case: after removing currency/null-value noise, **70,979 of 291,176 company/fiscal-quarter combinations (24.4%)** still had more than one distinct reported EPS value under an identical ticker/period-end/announcement-date. These are concentrated overwhelmingly in December period-ends (roughly three-quarters of the ambiguous cases, versus about a quarter of period-ends generally), which is exactly what this quarterly/annual conflation theory predicts, since December is by far the most common fiscal year-end among US public companies.

**How it was resolved:** for each ambiguous case, the point-in-time-safe quarterly analyst consensus (the same one used for `sue_analyst`) was looked up, and whichever of the candidate EPS values sits closer to that consensus was kept -- analysts covering a stock's quarterly estimate are forecasting the quarterly figure, so their consensus sits far closer to the true quarterly actual than to a several-times-larger annual one. 62,116 rows were resolved this way. Where no consensus was available at all to compare against (no analyst coverage for that quarter), the smaller-magnitude candidate was kept instead, on the same reasoning (a single quarter's EPS is smaller in magnitude than a full year's EPS for the same company in the large majority of cases): 8,863 rows used this fallback. Every row's resolution method is recorded in the output's `eps_actual_dup_flag` column, so this choice is auditable rather than hidden. Separately, 649 rows were dropped for being a later, second announcement of the same period (a true restatement/re-announcement) -- the earliest announcement date was kept in each case, since that is when the market actually first learned the result.

## The point-in-time filter's measured effect

Of the 291,176 (company, fiscal-quarter) events, 243,110 had at least one fpi=6 consensus snapshot for that fiscal quarter at all (eligible or not). Of those, **7,323 events had at least one candidate consensus snapshot that would have been look-ahead contamination** (published on or after the announcement date) had it not been explicitly filtered out. Across all events, **11,200 individual consensus snapshots** were excluded on exactly this basis. This is expected and mechanical, not a data error: I/B/E/S keeps publishing new consensus snapshots for years after any given historical quarter (as analysts revise their NEXT-quarter forecasts), so "the last available snapshot for this fiscal quarter" is almost always a post-announcement one -- which is precisely why an explicit `statpers < anndats` filter matters rather than simply taking the latest available row per fiscal quarter.

After the filter, 242,500 of 291,176 events (83.3%) have a usable, point-in-time-safe consensus snapshot at all.

## How the 5,803 pre-1995 pends rows were handled

`actu_epsus` has rows whose fiscal period-end (`pends`) predates 1995 even
though the pull window starts at 1995 -- these are late or restated reports
of old fiscal periods, not a pull bug (the pull filtered on announcement
date, not period-end date). They were kept, not dropped: a late or restated
result is still real information, and the point-in-time filter above is
exactly what keeps this safe regardless of how old the underlying period is
-- it compares the analyst consensus's STATPERS against this row's own
`anndats` (when the restated figure actually became public), never against
the ancient `pends`. A restated 1994 actual announced in 1996, for example,
can only ever be matched against a consensus snapshot published before that
1996 announcement, never against a stale 1994-era consensus.

## Analyst vs. seasonal coverage

- `sue_analyst` computed (usable consensus with non-null, non-negligible dispersion, i.e. more than one analyst who did not all submit the same or a near-identical estimate): 196,490 of 291,176 events (67.5%).
- `sue_seasonal` computed (an exact same-fiscal-quarter-one-year-ago match existed, and at least 4 valid diffs were available within the trailing 8 quarters to scale it): 229,284 of 291,176 events (78.7%). The scaling std dev uses a trailing 8-quarter window (matching the standard seasonal-random-walk SUE construction from the academic literature) rather than a company's entire available history -- an all-history version was tried first and rejected after it let a stale, no-longer-relevant volatility estimate from years earlier (e.g. from before a stock split, restructuring, or a multi-year reporting gap) scale a present-day surprise, occasionally by several hundredfold.
- Per the plan's preemptive-check design (prefer real analyst data, fall back to seasonal only where analyst coverage is missing): `sue_primary` came from `sue_analyst` for 196,490 events, from `sue_seasonal` for 63,387 events, and neither measure was computable for 31,299 events (10.7%) -- these rows are kept in the output (not dropped) with `sue_source="none"`, since a future slice may still want the actual EPS/announcement-date fields even without a surprise measure.
- Both measures have a small tail of extreme values: 151 `sue_analyst` values and 25 `sue_seasonal` values exceed a magnitude of 100. Spot-checking the largest of these (e.g. a company reporting an EPS of -91.60 against a tightly-clustered analyst consensus of -0.44) shows they are genuine -- large one-time write-offs, bankruptcies, and similar real events that also happen to have had unusually low estimate disagreement or historical volatility beforehand -- not computation errors. Deciding whether and how to winsorize or cap these for statistical analysis is left to whichever later slice actually runs event-study statistics on this data, not this cleaning slice.

## Identifiers

`resolved_universe_ids.parquet` is one row per OptionMetrics secid (8,358 rows), not one row per I/B/E/S ticker, and a handful of secids resolve ambiguously to more than one candidate PERMNO/GVKEY/ticker (recorded there as a comma-joined string rather than a silently-picked guess). This file's `ibes_ticker` -> PERMNO/GVKEY crosswalk was built by exploding those comma-joined ticker values into individually-joinable tickers and unioning whatever PERMNO/GVKEY values map to each one: 291,176 of 291,176 events (100.0%) matched to at least one PERMNO/GVKEY. That match rate is expected to be at or near 100% (not a sign the check is trivial): the raw I/B/E/S pull was itself already filtered, at pull time, to tickers that resolved from this same universe, so nearly every ticker `actu_epsus` could possibly contain was, by construction, already in this crosswalk.

Of the matched events, 21,896 (7.5% of matched events) carry more than one distinct candidate PERMNO or GVKEY (comma-joined in the output, `identifier_ambiguous=True`) -- most often because the same company was reassigned a new PERMNO/GVKEY after a corporate restructuring. This is flagged, not resolved, here: resolving it correctly needs announcement-date-aware matching against equity history, which belongs in the later event-table-assembly slice, not this one. 0 events had no PERMNO/GVKEY match at all.

## Output

`data/earnings/ibes_clean_1995_2014.parquet` -- 291,176 rows, one per (I/B/E/S ticker, fiscal quarter). Columns: ibes_ticker, permno, gvkey, identifier_matched, identifier_ambiguous, fpedats, anndats, anntims, eps_actual, eps_actual_dup_flag, consensus_statpers, consensus_meanest, consensus_medest, consensus_stdev, consensus_numest, sue_analyst, eps_year_ago, eps_diff_yoy, seasonal_n_prior_diffs, seasonal_diff_stdev, sue_seasonal, sue_primary, sue_source.
