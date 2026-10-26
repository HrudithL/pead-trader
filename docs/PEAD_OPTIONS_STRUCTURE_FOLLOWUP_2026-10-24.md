# PEAD options: return meaning and improvement study

Date: 2026-10-24
Research checkout: `codex/pead-options-next-study`
Historical source: CRSP, IBES and OptionMetrics, 1998–2013. The options calendar has no usable 2011 file and ends on 2013-08-30, so 2013 is partial.

## Decision

The presented options results do not support a 12% annualized return objective. The base 20%-premium-cap call book compounded at 4.21%; using a 100%-of-NAV premium cap raised it to 6.51%, but the excess-risk-free Sharpe remained 0.303 and the drawdown became much deeper. The 5× cap changes exposure, not the underlying signal. Neither sizing case establishes positive four-factor alpha.

The follow-up tests point to the underlying stock drift as the stronger and more stable part of the thesis. On events eligible for both calls and debit spreads, the long-call book earned a 3.27% CAGR, versus 3.05% for the delta-matched stock control with 10 bp per-side stock costs; the call Sharpe was 0.153 versus 0.278 for stock, with a substantially deeper call drawdown. Debit call spreads reduced drawdown but returned only 1.43% CAGR. The low pre-entry realized-volatility call subset was the strongest options challenger on Sharpe, at 0.280, but its CAGR was only 3.43%.

The practical decision is to stop raising the premium cap and stop promoting an in-sample “winner.” Keep low realized volatility as one frozen challenger, compare the signal first in stock and options, and build the next test around analyst revisions and option-specific event-risk measures. These are research candidates, not a 12% forecast or an allocation recommendation.

## What the table’s statistics mean

### CAGR

For starting account value (V_0), ending value (V_T), and elapsed years (T):

\[
\mathrm{CAGR} = (V_T/V_0)^{1/T}-1.
\]

CAGR is the single annual compounding rate that links the two endpoints. It is not the average trade return or the arithmetic mean of yearly returns. Here it includes the account’s cash return assumption as well as option P&L. A 4.21% CAGR turns $10 million into about $19.34 million over the 15.995-year sample; 6.51% turns it into about $27.42 million. A 12% CAGR over the same span would require about $61.3 million. CAGR says nothing by itself about the likelihood of that path, the sequence of losses, or whether the return repeats.

### Excess-risk-free Sharpe ratio

The reported daily Sharpe is:

\[
\sqrt{252}\;\frac{\operatorname{mean}(r_{p,t}-r_{f,t})}
{\operatorname{sd}(r_{p,t}-r_{f,t})}.
\]

It compares the average daily return above the daily risk-free proxy with the day-to-day variation in that excess return, then annualizes using 252 sessions. It is unitless: 0.303 does **not** mean a 30.3% return. It means only about 0.303 annualized units of average excess return for each unit of excess-return volatility. Values of 0.215 and 0.303 are weak risk-adjusted results; the small increase at 5× is not enough to make the strategy attractive by itself. The risk-free input is the rounded daily French factor series ([Kenneth French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html)), not a reconstructed Treasury-bill account. Sharpe also treats upside and downside variation alike and can understate tail risk in option strategies.

### Four-factor alpha and its HAC 95% interval

The daily regression is of the form:

\[
r_{p,t}-r_{f,t}=\alpha+\beta_m(Mkt-RF)_t+\beta_s SMB_t+\beta_h HML_t+\beta_{mom}Mom_t+\epsilon_t.
\]

The intercept estimates average return left over after those market, size, value and momentum exposures. The report annualizes the intercept by multiplying by 252. The interval uses a Newey–West/Bartlett heteroskedasticity-and-autocorrelation-consistent (HAC) standard error with 60 daily lags; HAC changes the estimated uncertainty, not the strategy return. A 95% interval that includes zero means these data do not distinguish the alpha from zero at that conventional level. It is not a 95% probability that the true alpha lies in the displayed range, and it does not correct for choosing the best result after searching many variants.

For the supplied table, baseline alpha is −1.78% per year with interval [−5.71%, +2.16%]; at the 100% premium cap it is −1.01% with interval [−8.83%, +6.81%]. Both are inconclusive and include zero. The wide 5× interval is consistent with considerable uncertainty. Four factors also cannot fully capture options’ nonlinear delta, gamma, volatility, jump and liquidity exposures.

## Rechecked and newly tested results

### User’s sizing table

| Premium cap | CAGR | Excess-RF Sharpe | Max drawdown | Four-factor annual alpha, HAC 95% CI |
|---|---:|---:|---:|---:|
| 20% of NAV | 4.21% | 0.215 | −33.19% | −1.78% [−5.71%, +2.16%] |
| 100% of NAV | 6.51% | 0.303 | −52.40% | −1.01% [−8.83%, +6.81%] |

This is more money allocated to the same contract-selection signal. It does not improve the statistical evidence for an edge, and reaching 12% by scaling the premium budget alone is not supported.

### Next-session call book and matched structures

The next-session C/75/0.70/40 long-call result reconciles with the prior run: 5,195 funded trades, 3.29% CAGR, 0.141 excess-RF Sharpe, −27.25% max drawdown and −1.90% annual four-factor alpha [−5.28%, +1.49%]. The comparison below restricts the call book to events where a same-expiry short call could also be selected, then compares the same signal dates with stock and debit spreads.

| Common event cohort / structure | Funded trades | CAGR | Annual vol. | Excess-RF Sharpe | Max drawdown | Four-factor annual alpha, HAC 95% CI |
|---|---:|---:|---:|---:|---:|---:|
| Long call | 3,099 | 3.27% | 7.96% | 0.153 | −20.36% | −1.22% [−3.81%, +1.38%] |
| Debit call spread | 3,150 | 1.43% | 5.91% | −0.123 | −18.65% | −2.57% [−4.37%, −0.76%] |
| Delta-matched stock, 10 bp per-side costs | 3,069 | 2.95% | 2.24% | 0.278 | −6.39% | +0.01% [−0.69%, +0.71%] |

The call has a 0.32-point CAGR advantage over the stock control in this common sample, but considerably weaker Sharpe and much deeper drawdown. The spread is not an improvement in this test: its return and Sharpe are lower, even though the maximum drawdown is smaller. Its alpha interval is entirely negative in this fitted sample; because this is one of several exploratory comparisons, it should be treated as a diagnostic rather than a final inference.

The additional friction stress charges another 3% of net option debit on top of the quoted ask/bid spread. In the common sample, call CAGR drops from 3.27% to 2.13%, and spread CAGR drops from 1.43% to 0.43%. Displayed bid/ask quotes are already charged in the base run; the extra percentage is a sensitivity, not a claim that actual costs equal 3%.

At the event level, average 40-session market-adjusted stock return per initial option-delta dollar was +1.96% over 3,169 matched events, compared with −0.31% for the call and −1.02% for the spread. This measure is a matched-event diagnostic, not a premium return and not portfolio CAGR. It is consistent with the idea that option theta, volatility repricing and spreads consume part of the stock drift.

### Volatility conditioning

The lowest prior trailing-realized-volatility tercile produced a 3.43% call CAGR, 0.280 Sharpe, −7.49% drawdown and +0.04% annual alpha [−1.30%, +1.38%] in the common comparison. Its stock control with 10 bp per-side costs returned 2.74% with a 0.516 Sharpe and −1.56% drawdown. On the matched events, the low-volatility stock/call/spread average outcomes per initial delta dollar were +2.71% / +1.01% / +0.15%; in the high-volatility tercile they were +0.59% / −2.61% / −2.80%.

This is a useful risk-conditioning result, but it does not produce a high-return strategy and the low-volatility cutoff was inspected on the same history. A generic event-level ATM IV/realized-volatility ratio filter did not improve the earlier call test: the at-or-below-67th-percentile group had 2.97% CAGR and 0.117 Sharpe, below the all-event next-session result. “Avoid expensive implied volatility” is not validated by that coarse ratio.

### Is the PEAD followed by a reversal?

We removed every repeated `(secid, day0_date)` cluster and recomputed the stock event profile using CRSP market-adjusted returns from sessions +1 onward, excluding the announcement-session return. Positive-SUE deciles 9–10 had a larger cumulative return than negative-SUE deciles 1–2 at every measured horizon. The positive-minus-negative spread was 0.76% after 1 session, 1.25% after 5, 1.41% after 10, 1.55% after 20, 1.65% after 40, and 1.90% after 60 sessions. The equal-weight quarterly spread’s HAC t-statistic was 3.91 at 40 sessions and 3.79 at 60 sessions.

The 40-to-60-session incremental high-minus-low return remained positive: 0.23% event-weighted; the equal-quarter estimate was 0.48% with HAC t=2.31. The 10-to-20 and 20-to-40 incremental spreads were not significant in this simple diagnostic. So this event panel shows continued drift, not a reversal of the stock return during the first 60 sessions. This is not proof of a realizable option edge: it is an event-level stock study, before option costs and nonlinear exposures.

A separate cleaned same-firm quarterly check found lag-four SUE correlation of +0.050 across 102,433 pairs. Its decile pattern indicates modest persistence, not a fade-able SUE reversal. This is different from a stock-price reversal test and should not be compared mechanically with older papers’ coefficients, which use different sample and SUE constructions.

### Can the local IBES data support analyst-revision features?

Yes. The raw I/B/E/S Detail History file exists locally (`detu_epsus.parquet`, 14.65 million rows), so the previous improvement memo’s claim that a new point-in-time extract is needed was too strong. A conservative coverage audit found 33,048 of 33,108 selected positive-SUE events with a plausible next-quarter forecast horizon, and 32,084 with at least one pre-event forecast; 27,151 had at least three.

For each existing analyst, the audit compares the latest forecast before the event date with the latest different forecast on a later calendar date, using the nearest future `fpedats` 45–180 days after the just-reported period. Same-event-day revisions are excluded. It then freezes the feature at the end of one, five or ten post-event sessions and assumes entry only on the following session. At least one existing analyst had a changed forecast by the freeze date in 13,478 events (40.7%) after one post-event session, 20,043 (60.5%) after five, and 22,118 (66.8%) after ten. New analyst forecasts are counted separately.

This establishes data feasibility, not predictive power or returns. Detail History uses `REVDATS`/`REVTIMS` for forecast reissue dates/times, and a reissued estimate can retain the same value, so the audit compares values instead of counting every reissue ([I/B/E/S timing discussion](https://www.nber.org/papers/w24293.pdf)). The extract is unadjusted EPS; WRDS warns that estimate values can change share basis across stock splits ([WRDS note on unadjusted I/B/E/S](https://wrds-www.wharton.upenn.edu/documents/5/A_Note_on_IBES_Unadjusted_Data_pdf.pdf)). The horizon is inferred from period-end dates, not an explicit quarterly-period code, and the extract does not establish the timestamps’ time zone. Therefore same-date revisions are excluded, and revision direction/size must be audited against CRSP split factors before using the feature. Do not add this signal to a strategy until a delayed-entry return test proves it survives costs.

## Research-backed improvement experiments, in priority order

1. **Improve the event signal first.** Compare SUE with post-release analyst revisions, revision breadth, analyst disagreement, and revisions that contradict SUE. In a UCLA dissertation, Huang reports stronger post-announcement drift when firms are sorted by aggregated analyst forecast revisions than by earnings surprise alone, with particularly informative cases when revision direction conflicts with SUE ([Huang, 2022](https://escholarship.org/uc/item/2r7980f3)). The local audit says revisions are measurable after a deliberate delay, not that they will improve returns. Add guidance and revenue surprises only after point-in-time coverage and accounting bases are verified.

2. **Separate ordinary volatility from earnings jump risk.** Replace a single ATM IV/RV ratio with selected-contract IV minus a forecast of ordinary volatility, event variance isolated from the term structure, call-put implied-volatility spread, skew, and risk-neutral jump/tail measures. Earnings announcements change implied volatility most strongly in shorter maturities and that event component can dissipate afterward ([options-market earnings response](https://doi.org/10.1016/j.intfin.2012.01.006)). Research also finds that pre-announcement implied-skewness and kurtosis changes contain information about later stock and call returns ([Diavatopoulos et al., 2012](https://doi.org/10.1016/j.jbankfin.2011.09.012)); volatility spreads have been studied as predictors of announcement returns ([Atilgan, 2014](https://doi.org/10.1016/j.jbankfin.2013.10.007)). For this post-announcement strategy, measure features at the actual post-release entry and do not import pre-event option prices into the trade decision.

3. **Test directional option flow separately from surface pricing.** Compare pre-announcement call volume, put volume, call-put balance and abnormal volume against the SUE signal; recent three-decade evidence reports information in directional call/put balance that is obscured by total option volume ([Taheri Hosseinkhani & Taheri Hosseinkhani, 2026](https://doi.org/10.1016/j.bir.2026.100908)). This is a predictor of the later event/PEAD return, not a reason to buy a call without pricing it. Keep all volume inputs strictly before the event.

4. **Make the underlying-versus-option comparison an explicit gate.** Use the same event signals, dates and holding windows for stock, long calls and defined-risk spreads. Report both portfolio results and delta-normalized event P&L, then attribute option P&L to delta, gamma, IV, theta, dividends/corporate actions and execution. The present result suggests reserving options for cases where forecast drift and post-event volatility pricing together justify the premium; otherwise the stock exposure is cleaner.

5. **Test entry and exit delay as a fixed grid, not a post-hoc optimum.** Freeze entry at the first safe session, then compare one-, five- and ten-session signal-formation delays with 10/20/40/60-session exits and an exit before the next earnings event. The analyst feature is only available after the event, so this is necessary for a fair revisions test. Use timestamps and stale-quote checks; never let a quote from before the release price a post-release decision.

6. **Use a modern untouched validation and account for the search.** Choose the signal and contract family on old training windows, then lock it before an untouched future period. Purge overlapping 40-session labels, use year/quarter block uncertainty, and report multiple-testing-adjusted inference. Combinatorially symmetric cross-validation can estimate backtest-overfitting risk, but it is supplementary to a genuinely chronological holdout ([Bailey et al., 2017](https://doi.org/10.21314/jcf.2016.322)). This archive ends in 2013, so it cannot be the modern holdout.

The PEAD literature documents long-horizon drift but also debates underreaction, risk, trading frictions, and event dynamics; it does not imply that buying calls converts the stock anomaly into option alpha ([Fink’s PEAD review](https://doi.org/10.1016/j.jbef.2020.100446)).

## What would be required before considering a 12% target

The objective is not met by any tested options case. The 100% premium-cap portfolio ends near $27.4 million versus about $61.3 million required for 12% compounding over this interval. Raising premium exposure further would put more capital behind an edge whose alpha interval includes zero, with higher account-level tail risk. The rational path toward a higher return is to find a stronger predictive signal and better net option pricing, not to solve for a sizing multiple that forces the historical CAGR upward.

The repository’s long-only equity candidate `steady_drift_plus` did show about 12.07% historical CAGR in the same archive, but it was selected in-sample, returned 9.0% over 2008–2013, and its documented forward planning estimate is 6–7%. That is a useful strategy comparator and a reason to test the PEAD signal in shares first; it is not evidence of a repeatable 12% forward return or a same-period S&P comparison. See [the steady-drift research note](C:/Users/hrudi/Documents/BEI/PEAD_Trading/docs/STEADY_DRIFT.md).

Before a strategy could be described as a serious candidate, require:

- Correct point-in-time earnings, forecast and option timestamps, including split-basis reconciliation for IBES.
- A frozen signal/contract/entry rule evaluated on later years that were not used to choose it.
- Positive net results after ask/bid, commissions, displayed-size limits, realistic slippage and capacity.
- A stock benchmark and a premium-risk benchmark evaluated over the same dates; do not treat a 12% hurdle as a measured S&P return for this sample.
- Search-adjusted uncertainty and stability across years, volatility regimes and liquidity groups.

Until those conditions are met, these results support continued research only. They do not establish an options strategy capable of reliably earning 12% or more.

## Reproducibility

New aggregate outputs are in `results/options_structure_comparison_2026-10-24/`:

- `portfolio_summary.csv`, `portfolio_yearly.csv`, and `daily_nav_*.csv`: account-level results and paths for long calls, debit spreads, stock controls, volatility subsets and added cost scenarios.
- `matched_event_pnl_per_delta_dollar.csv`: common event outcome diagnostics by instrument and volatility group.
- `study_audit.json`: source coverage, collisions, accounting and quote-match diagnostics.
- `ibes_revision_feasibility.json` and `ibes_revision_feasibility_summary.csv`: aggregate IBES coverage only; no analyst-level forecasts were saved.
- `pead_drift_reversal_audit.json`, `pead_drift_reversal_cumulative.csv`, and `pead_drift_reversal_intervals.csv`: cleaned PEAD horizon and reversal diagnostics.

Re-runnable scripts are `pead/research/options_structure_comparison.py`, `pead/research/ibes_revision_feasibility.py`, and `pead/research/pead_drift_reversal_audit.py`. OptionMetrics quote rows were streamed read-only from the licensed external archive and were not written to the study. The original user-protected worktree and its ignored data were not removed or changed.
