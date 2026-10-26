# PEAD options: performance meaning, improvement tests, and decision

Date: 2026-10-24
Study checkout: `codex/pead-options-next-study`
Archive: OptionMetrics/CRSP/IBES, 1998–2013; no usable option file for 2011 and partial OptionMetrics coverage in 2013.

## Decision

The tested options rules do not meet the 12% CAGR hurdle, and none has statistically established positive four-factor alpha. Raising the premium cap from 20% to 100% changes the return from 4.21% to 6.51% CAGR but leaves the trade selection unchanged, keeps the excess-RF Sharpe at 0.303, and deepens the reported drawdown to 52.40%. This is more risk applied to an unproven edge, not evidence that the edge improved.

The best improvement found in the new volatility experiment is to exclude the highest pre-entry realized-volatility tercile. With next-session entry, that subset has 3.69% CAGR, 0.270 excess-RF Sharpe, and a −0.07% four-factor alpha estimate (HAC 95% CI −1.87% to +1.72%). The high-realized-volatility tercile has 1.98% CAGR, −0.054 Sharpe, and −1.37% alpha (CI −3.17% to +0.44%). The low-volatility filter improves the risk/return profile in this archive, but it is still far from 12% and has no significant alpha.

The IV/realized-volatility ratio gives the opposite result: excluding its highest tercile reduces next-session CAGR to 2.97% and Sharpe to 0.117, while the high-ratio tercile returns 3.32% with 0.227 Sharpe. Do not adopt a generic “avoid rich IV” filter from this result. The variable is an event-level, near-ATM IV divided by trailing realized volatility, not the exact IV of the selected 70-delta contract, and its return association may reflect signal composition or event risk.

If the objective is specifically to reach 12%, stop increasing the options premium cap. Use the repo’s long-only equity PEAD strategy as a research comparator: `steady_drift_plus` has about 12.07% historical CAGR in the same old sample, but it was selected in-sample, fell to 9.0% in 2008–2013, and its own forward planning estimate is only 6–7%. That historical number is not a forecast or an apples-to-apples S&P result. The options strategy should remain at zero live allocation until there is post-2013, timestamp-safe validation and realistic execution evidence.

## What the table statistics mean

| Measure | How it is calculated | What the reported values say |
|---|---|---|
| **CAGR** | `(ending NAV / starting NAV)^(1 / elapsed years) - 1`. It is a geometric, compounded account growth rate, including the study’s cash return assumption. | 4.21% means $10m grows to about $19.34m over 15.995 years. At the 5× cap, 6.51% grows it to about $27.42m. CAGR summarizes the endpoints; it does not show how uncertain or uneven the path was. |
| **Excess-RF Sharpe** | `sqrt(252) × mean(daily portfolio return − daily RF) / standard deviation(daily excess return)`. | 0.215 and 0.303 are low risk-adjusted excess returns. A value of 0.303 is not “30.3% return”; it means the average daily excess return is only about 0.303 annualized standard deviations. The RF input is the rounded French daily factor series, a proxy rather than a reconstructed investable bill account. |
| **Four-factor alpha, HAC 95% CI** | Regress daily portfolio excess return on `Mkt−RF`, `SMB`, `HML`, and momentum (`Mom`); annualize the intercept by 252. The interval uses Newey–West/Bartlett heteroskedasticity-and-autocorrelation robust errors with 60 lags. | The baseline estimate is −1.78% [−5.71%, +2.16%]; at 5× it is −1.01% [−8.83%, +6.81%]. Both intervals include zero and are wide. These runs do not establish positive abnormal return after the four factor controls. They also do not control for every nonlinear option exposure or liquidity risk. |

The factor names and daily series follow the [Kenneth French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html). “HAC” changes the uncertainty estimate to allow residual autocorrelation; it does not make the strategy more profitable or remove selection bias.

Using 12% as the stated hurdle, $10m compounded for 15.995 years would become about $61.27m. The 5× options case ends near $27.42m. It therefore falls well short even before adding commissions, market impact, quote-size constraints, or a live fill test. The 5× case’s $12.93m advantage over the study’s cash proxy is positive historically, but it is not alpha evidence: its HAC alpha remains inconclusive and its premium selection is unchanged.

## Fresh quote-level result: C/75/0.70/40

This is the prior study’s balanced research candidate: top-SUE calls, target 75 calendar DTE, 0.70 absolute delta, and a 40-session exit. The rerun excludes every row in a duplicate `(secid, day0_date)` event cluster before selecting signals (27 source rows removed, including 15 eligible signals). It uses entry asks, daily bid marks/exits, the shared CRSP/OptionMetrics calendar, zero proceeds for missing or zero-bid exits, the $10m daily allocator, and the French daily RF series.

| Entry / subset | Funded trades | CAGR | Annual vol. | Excess-RF Sharpe | Four-factor alpha, HAC 95% CI | Increment vs cash proxy |
|---|---:|---:|---:|---:|---:|---:|
| Same-session, all candidate events | 5,268 | 3.97% | 10.40% | 0.204 | −1.26% [−4.70%, +2.18%] | +$4.16m |
| Next-session, all candidate events | 5,195 | 3.30% | 10.41% | 0.142 | −1.90% [−5.28%, +1.49%] | +$2.33m |
| Next-session, lowest trailing-realized-vol tercile | 2,030 | 3.69% | 5.39% | 0.270 | −0.07% [−1.87%, +1.72%] | +$3.37m |
| Next-session, highest trailing-realized-vol tercile | 1,663 | 1.98% | 4.58% | −0.054 | −1.37% [−3.17%, +0.44%] | −$0.79m |
| Next-session, IV/RV at or below prior-quarter 67th percentile | 3,559 | 2.97% | 8.14% | 0.117 | −1.50% [−4.23%, +1.24%] | +$1.51m |

All listed alpha intervals include zero. The same-session case has 42.45% winning funded trades and a −15.88% median trade ROI; the next-session case has 42.19% winners and a −17.00% median. Portfolio gains still depend on a minority of large winners. The 0% extra-cost same-session result falls from 3.97% to 2.23% CAGR with a 3% additional premium friction assumption and to 1.02% with 5%; the 3% case no longer beats the cash proxy. The entry/exit bid/ask spread is already charged in the base case; these are additional friction stresses.

For the next-session scenario, the contract is selected from the day-0 chain using the frozen DTE/delta rule, then the same option ID is entered at the next session's ask if it still meets quote/liquidity checks. Exit remains on the original scheduled date. This is a conservative timing check, not a fresh next-session re-selection of the best contract.

The event-level stock diagnostic in the repository points the same way for realized volatility: the per-quarter D10−D1 holding-return spread is 4.47% in low volatility, 3.59% in the middle tercile, and 2.39% in high volatility. Its high-minus-low t-statistic is −1.77; the SUE-by-volatility interaction is −0.0396 (t = −2.73). This is useful hypothesis evidence, but stock returns do not prove an options premium. In the new option book, the low-vol subset improves risk-adjusted results but not the CAGR hurdle.

## Improvement avenues, ranked by what is most likely to matter

1. **Improve the earnings-news signal before changing option size.** Add post-announcement analyst forecast revisions, estimate dispersion, and revisions that disagree with the reported SUE. A UCLA dissertation finds that aggregated analyst revisions sort a stronger drift than earnings-surprise measures, particularly for large surprises and when revisions contradict SUE ([Huang, 2022](https://escholarship.org/uc/item/2r7980f3)). The local raw I/B/E/S Detail History file is available. A conservative coverage audit maps 33,048 of 33,108 positive-SUE events to a plausible next-quarter forecast and finds a changed existing-analyst forecast in 40.7% of events after one post-event session, 60.5% after five, and 66.8% after ten. This is feature coverage, not a return test; share-split basis and timestamp semantics still need to be resolved before use. See [the options structure follow-up](PEAD_OPTIONS_STRUCTURE_FOLLOWUP_2026-10-24.md). Also test revenue/guidance surprises, the announcement-day stock move, and peer earnings news as distinct signals rather than tuning more DTE/delta cells.

2. **Keep low pre-entry realized volatility as a challenger, not a winner.** The prior-quarter low-vol tercile is the only added filter here that improves next-session Sharpe (0.270 versus 0.142 overall). Its CAGR is still 3.69%, its alpha interval spans zero, and it uses only about 39% of the candidate events. Pre-register it and evaluate it on a future period; do not select the 67% cutoff again after inspecting results.

3. **Use volatility surfaces and option flow, not only ATM IV/RV.** Earnings-event risk is substantial and changes through time; option-implied event uncertainty forecasts future realized volatility ([Dubinsky et al., *Review of Financial Studies*](https://doi.org/10.1093/rfs/hhy060)). Post-announcement straddle returns and their relation to volatility vary by event timing and volatility regime ([Chung & Louis, *Journal of Empirical Finance*](https://doi.org/10.1016/j.jempfin.2016.07.010)). Test selected-contract IV minus trailing realized volatility, term-structure event premium, call-minus-put IV, skew, and risk-neutral jump measures, with features sampled only at the actual planned entry. In this run, the simple event-level IV/RV filter did not improve performance.

4. **Treat directional option activity as separate signals.** A recent 1996–2024 study reports that call/put balance contains information hidden by total options volume and that the relationship varies across market eras ([Taheri Hosseinkhani & Taheri Hosseinkhani, 2026](https://www.sciencedirect.com/science/article/pii/S2214845026001286)). The current data include option volume and open interest, so an event-time-safe call-minus-put activity feature is feasible. It must be measured before the public announcement if used as a predictor; day-0 data cannot stand in for pre-event flow.

5. **Compare shares, calls, and vertical call spreads on a matched exposure basis.** PEAD is a directional stock-return hypothesis; a long call adds theta decay, implied-volatility sensitivity, convexity, and spread cost. Compare the same event signal and holding window using stock, the current call, and defined-risk debit call spreads, matched on initial delta dollars. Mark every leg at the bid, pay the ask at entry, and cap by quoted size. A vertical can lower net premium and vega but sells away some upside, so it may raise or lower CAGR; it is an experiment, not a presumed improvement. Decompose realized P&L into delta/underlying movement, IV, gamma/realized variance, theta, dividends/corporate actions, and execution.

6. **Make entry timing conservative.** Moving the balanced candidate to the next session reduces CAGR from 3.97% to 3.30% and Sharpe from 0.204 to 0.142. The release timestamps and OptionMetrics quote times are not fully aligned, so same-session fills may use quotes unavailable after some announcements. Use next-session entries as the base until timestamps are reconstructed. Then test delayed entry and exit profiles without selecting on the final holdout.

7. **Do not optimize around a single target CAGR.** The C/120/0.50/60 54-cell scan has a familywise Sharpe bootstrap p-value of 0.130 and no positive four-factor alpha interval. Every extra volatility bucket, exit, spread limit, and structure adds another trial. Use nested chronological training/validation/test windows with at least a 40-session embargo, year-block uncertainty, and a search-adjusted test such as combinatorially symmetric cross-validation/PBO ([Bailey et al., 2017](https://doi.org/10.21314/jcf.2016.322)). The existing archive ends in 2013, so it cannot provide the needed modern untouched holdout; refresh the data through the present before making a forward claim.

## Practical research sequence

1. Freeze the low-`tvol_60d` next-session rule as one challenger; do not change its cutpoint after seeing the current results.
2. Refresh point-in-time earnings, stock, and option data beyond 2013. Reconstruct release timestamps and ensure the first entry quote is after public release.
3. Add IBES revisions plus option-specific IV/flow features. Train feature weights only on past quarters; hold out complete future years and purge overlapping 40-session labels.
4. Compare stock, long call, and debit call spread using the same signal, quote timestamps, sizing rules, and liquidity constraints. Include commissions, quote-size fills, and capacity.
5. Require positive out-of-sample excess-RF Sharpe, four-factor alpha whose interval excludes zero after search correction, and a return path that survives realistic execution costs before increasing risk or considering paper trading.

The historical `steady_drift_plus` equity result is a useful control, not proof that the options overlay works. Its approximately 12.07% in-sample CAGR met the numerical hurdle in this archive; its 2008–2013 CAGR was 9.0%, and its documented forward planning estimate is 6–7%. A target number cannot substitute for out-of-sample evidence.

## Reproduction and output files

The rerun streamed OptionMetrics quotes without saving contract-level quotes. It completed 5,522 eligible option contracts and 226,402 daily quote lookups with 99.77% quote coverage. Source rows in repeated event-date clusters were excluded before selecting contracts. Factor ZIPs are copied into this study checkout's local `data/factor/` and left unstaged; the original data-bearing worktree was read only and left in place.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.task-deps')
& 'C:\Users\hrudi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pead.research.options_expiry_grid `
  --years 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2012 2013 `
  --dte-targets 75 --delta-targets 0.7 --holds 40 --direction C `
  --data-dir 'C:\Users\hrudi\Documents\BEI\PEAD_Trading\data' `
  --optionmetrics-dir 'D:\OptionMetrics\parquet' `
  --output-dir 'results\options_next_session_c75_d70_h40_2026-10-24' `
  --daily-portfolio
```

Key outputs are in `results/options_next_session_c75_d70_h40_2026-10-24/`: `daily_sensitivity.csv`, `volatility_filter_summary.csv`, `volatility_filter_yearly.csv`, `riskfree_daily_portfolio.csv`, `next_session_daily_portfolio.csv`, `daily_audit.json`, and `event_collision_audit.json`. The earlier C/120 sizing table and study methodology are in [the final decision record](PEAD_OPTIONS_FINAL_DECISION_2026-10-24.md) and [the sizing stress report](PEAD_OPTIONS_SIZING_STRESS_2026-10-24.md).
