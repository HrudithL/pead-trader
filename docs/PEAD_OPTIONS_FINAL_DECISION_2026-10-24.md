# PEAD options: final candidate decision (2026-10-24)

## Decision in one paragraph

There is **not** a provably best, production-ready, consistently profitable options strategy in the available evidence. The corrected 54-cell historical grid does contain a positive-return candidate. The highest-return cell is a top-SUE long call targeting 120 calendar days to expiry and 0.50 absolute delta, exited after 60 sessions. For a return/risk balance, advance the 75-DTE/0.70-delta/40-session call as the **preferred research candidate for further validation**: it retains most of the return leader's historical CAGR, has a slightly lower Sharpe and a less-negative alpha point estimate, and materially reduces drawdown and typical trade losses. Neither candidate has statistically established PEAD alpha, and neither is approved for live capital.

“Best” here means best among these 54 fixed call/put, DTE, delta and hold cells under this corrected historical accounting. It does not mean a global optimum or an out-of-sample result. The configuration was chosen after looking at the grid and is therefore still subject to selection bias.

## Candidate rule to validate next

1. Use the existing analyst SUE event signal. For positive top-decile events (deciles 9–10), buy a call; the bottom-SUE put cells are not promoted.
2. Select one entry contract from information available on the entry date: target 75 calendar DTE and 0.70 absolute delta; accept at most 25 days of DTE error and 0.10 delta error. Require positive bid, ask of at least $0.10, non-crossed market, spread/ask no greater than 30%, open interest of at least 100, and at least 10 calendar days remaining after the scheduled exit. Rank acceptable contracts by DTE error, delta error, spread fraction, then option ID.
3. Enter at the ask, mark at the bid, and exit at the bid after 40 shared CRSP/OptionMetrics sessions. Do not carry a missing exit quote as a live mark: missing and zero-bid exits receive zero proceeds in the base case.
4. Use the tested daily allocator: $10m initial NAV, 20% cap on outstanding paid premium, 0.25% NAV signal budget before the trailing signal-count scaler, 10% of observed entry OI as the aggregate open-lot cap per option ID, 2% NAV per issuer, pro-rata same-day order scaling, and whole lots. Same-close exits do not finance new same-close entries.
5. Treat this as a **research specification**, not an execution specification. The original run enters at same-day EOD quotes; exact earnings-release and quote timestamps are unresolved. A next-session entry must be rerun for this exact contract rule before considering paper trading.

The funded candidate's realized median was 86 DTE, 0.699 delta, strike/spot 0.930 (about 7% in the money), and spread/ask 6.06%. Thus the rule generally buys a moderately in-the-money call, not an at-the-money call. Daily OptionMetrics quote keys were matched at 99.77%; 31 funded exits had missing bids and 173 had quoted zero bids, both assigned zero proceeds.

## Corrected daily results

The account starts at $10m and uses the shared trading calendar and the published French daily risk-free series. The series is rounded and is **not** a reconstructed, directly investable Treasury-bill account. Annualized figures span 1998-01-02 through 2013-12-31; 2011 is cash-only because usable option data are absent, and 2013 options coverage is partial.

| Rule | Funded trades | CAGR | Annual vol. | Excess-RF Sharpe | Max drawdown | Four-factor annual alpha (HAC 95% CI) | Increment vs cash proxy | Profitable trades / median trade ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **C/75/0.70/40: balanced research candidate** | 5,268 | **3.97%** | 10.40% | 0.204 | **−25.58%** | −1.26% [−4.70%, +2.18%] | +$4.156m | 42.45% / −15.88% |
| C/120/0.50/60: highest-return cell | 6,023 | 4.21% | 11.59% | 0.215 | −33.19% | −1.78% [−5.71%, +2.16%] | +$4.865m | 35.88% / −47.62% |
| C/120/0.30/60: lower-exposure comparator | 4,788 | 3.81% | 8.32% | 0.213 | −24.52% | −0.85% [−4.11%, +2.41%] | +$3.700m | 29.05% / −77.78% |

The first rule gives up 0.24 percentage points of CAGR versus the maximum-return cell. Its excess-RF Sharpe is lower by 0.011, its alpha estimate is 0.52 percentage points less negative, and its maximum drawdown is 7.61 percentage points shallower. The two alpha intervals are wide and overlap zero: there is no evidence that either alpha differs from zero or that their alpha difference is reliable. The balanced candidate is a sensible next validation target, not a statistically selected winner.

The C/120/0.30/60 comparison shows why drawdown alone is not enough: it has a shallower account drawdown and a less-negative alpha point estimate, but only 29.1% of funded trades win, its median trade loses 77.8% of premium, and its ten largest trade gains total $3.45m. Its path is more exposed to rare winners.

The highest-return cell ends at about $19.34m versus $14.48m for the rounded cash proxy. Its options themselves make about $4.91m before cash interest; its cash balance contributes about $4.43m of interest. It has a zero-RF Sharpe of 0.415 but an excess-RF Sharpe of 0.215. The excess-return measure is the relevant comparison to cash, and it is weak. Its four-factor alpha is −1.78% annualized (HAC t=−0.89; interval includes zero). The year-block 95% Sharpe interval is −0.388 to +0.743. The tested family’s max-Sharpe bootstrap p-value is 0.130; the annual settlement max-t p-value is 0.110.

The balanced candidate had positive account returns in 9 of the 14 calendar years with option observations; 2011 was cash-only. Its 2013 contribution is from a partial source year. Its 2008 account return was −17.10%; 2001 and 2002 were also negative. The maximum-return cell made +$3.51m of option trade P&L in 2013 and lost $2.67m in 2008–2012 combined. These results do not support “consistent” profitability.

## How the strategy attempts to make money

The thesis is that a large positive earnings surprise predicts continued upward stock drift after the announcement. A call gives upside convexity while limiting loss to premium. A 70-delta call has more immediate stock exposure and less extreme lottery-like payoff than a low-delta call; 75 DTE leaves time for post-announcement drift to develop. The 40-session exit limits exposure to later theta decay and unrelated price moves.

The observed result is not a clean PEAD-premium effect. For the 120/50/60 return leader, quoted mid-to-mid changes on the covered trades were +$12.37m, while entry and exit half-spreads consumed $3.29m and $3.43m. The strategy therefore relies on a strong mark move merely to overcome transaction spreads. We have not decomposed the move into stock delta/gamma, implied-volatility repricing, theta, dividends, or contract adjustments. The candidate can lose even when the stock drifts up if entry IV is rich or the option spread is costly.

## What the headline measures mean here

- **CAGR**: the compound annual growth rate of the simulated account, including the stated cash return assumption. It summarizes growth but hides the path and tail concentration.
- **Excess-RF Sharpe**: annualized mean daily return above the risk-free series divided by daily volatility. A value near 0.20 is low; it does not by itself demonstrate an attractive risk-adjusted edge.
- **Maximum drawdown**: the largest peak-to-trough account loss. The balanced candidate's simulated worst decline is about one quarter of NAV.
- **Alpha**: the regression intercept after market, size, value and momentum factor returns. The reported estimate is negative for all three rows and all intervals include zero. This is not evidence of positive abnormal return.
- **Profitable trades and median trade ROI**: how often an individual option trade made money and the middle trade's return on premium. All three candidates have a losing median trade; aggregate portfolio gains depend on a minority of large winners.
- **Increment vs cash proxy**: ending wealth less the simulated cash-only ending wealth. It is not a promise, and the rounded French series understates carry in some years.

## Why this is not complete or live-ready

- **No positive-alpha evidence:** all candidate HAC alpha intervals include zero. The full 54-cell scan has 19 unadjusted cellwise intervals entirely below zero and none entirely above zero. Search-adjusted tests do not reject the null.
- **No untouched out-of-sample test:** all cells and profiles were inspected on this 1998–2013 archive. Selecting C/75/0.70/40 after the scan creates in-sample selection bias. The 2011 options gap and partial 2013 source further reduce evidence.
- **Timing is not proven:** 7,126 event timestamps are unknown. Even known intraday announcements do not validate the OptionMetrics quote timestamp as a fillable post-announcement quote. Same-day EOD entry may precede the information for some events.
- **Execution is incomplete:** ask/bid fills account for displayed quotes but not displayed size, queue position, slippage, market impact, fees, early exercise, or an executable exit for missing/zero bids. A +3% extra-premium friction stress reduces the return leader's cash-relative gain to +$1.74m; +5% turns it negative (−$0.17m).
- **Contract identity is incomplete:** the source extract does not fully reconstruct adjusted deliverables or multipliers. Security/event linking and historical identifier vintages need independent audit.
- **Attribution and controls are missing:** no matched neutral-SUE option control, delta/vega/convexity-matched control, or Greek-level attribution proves that PEAD rather than market/factor exposure or volatility repricing produced the premium return.
- **Framework status:** the corrected study is implemented in `pead/research/` and is not registered as a canonical reusable/live options strategy. The repo’s canonical `options_cap20` result is about 0.28% CAGR and 0.16 Sharpe under its own selector and conventions; these results are not directly comparable. `validated` in the framework means reproduced, not profitable.
- **No live or paper trial:** there is no forward paper record on timestamped quotes.

## Work still required

1. Resolve announcement timestamps and use a conservative next-session entry for every uncertain event; rerun the exact C/75/0.70/40 rule under that entry convention.
2. Build a purged, chronological outer validation: choose DTE/delta/hold and signal filters using only mature training-period returns, then evaluate once on the untouched next period. Report yearly distributions, block-bootstrap intervals, and search-adjusted inference.
3. Add matched neutral-SUE calls and delta/vega/market-factor controls; attribute P&L to underlying movement, IV, theta, spread and corporate actions.
4. Model quote-size-aware fills, fees and calibrated market impact, contract deliverables, dividends/early exercise and quote staleness. Recheck all results at realistic account sizes.
5. Only after those checks, register the frozen rule in `pead/strategies/options/`, reproduce it through the canonical engine, and conduct a timestamped paper trial. Until then, recommended allocation is zero.

## Response to the 15–20% CAGR target

A fixed-rule, quote-level sizing stress test was completed for the return-leading C/120/0.50/60 cell. Scaling the premium budget from 20% to the maximum cash-only 100% of NAV raised CAGR from 4.21% to 6.51%, but deepened maximum drawdown from −33.19% to −52.40%. Excess-RF Sharpe moved from 0.215 to 0.303, while the 5× alpha estimate remained negative at −1.01% with a HAC 95% interval of [−8.83%, +6.81%]. This does not support a 15–20% CAGR objective or prove positive alpha. Details and all sizing cases are in `docs/PEAD_OPTIONS_SIZING_STRESS_2026-10-24.md`.

The earlier 15.79% CAGR / 3.28 Sharpe Kelly headline is `options_cap40_legacy`: it used midpoint fills and carried stale quotes forward, including dead options. The corrected Kelly `options_cap20` result is about 0.28% CAGR / 0.16 Sharpe, and the old selector has additional label-maturity and mid-scoring defects. The legacy figure is not evidence that the corrected strategy can deliver a 15–20% CAGR.

The requested CAGR would require roughly $93.5m–$184.7m ending wealth from $10m over this 15.9945-year archive. The maximum cash-only sizing case ended at $27.4m. Reaching the target by borrowing to buy more of the same options would add leverage to an unproven edge and substantially increase drawdown risk; the evidence supports improving the signal/contract-selection edge and testing it out of sample before increasing risk.

## Qwen and repository instructions

The supplied `AGENTS.md` instructions were followed: planning stayed with the planner, and Qwen concept exploration was attempted sequentially through `qwen-delegate` (first with the completed report, then with a compact prompt and 8k context). Both local requests hit the delegate's 600-second timeout without returning a usable critique. The model was unloaded afterward; no other app was closed. No Qwen output is treated as evidence or endorsement. The AGENTS.md path was not available in the managed worktree; its full contents were supplied in the user prompt, and the attempted GitHub fetch returned 404. The worktree remains attached and its ignored `data/` was not copied, deleted, or moved.

## Reproduction artifacts

- Main corrected grid: `results/options_expiry_grid_full_source_collision_cleaned/daily_grid_summary.csv`, `daily_grid_yearly.csv`, and `daily_grid_paths.csv`.
- Single-cell return-leader diagnostics: `results/options_frozen_call_daily_full_source_collision_cleaned/`.
- Full prior decision and methodology: `docs/PEAD_OPTIONS_BACKTEST_RESULTS_2026-10-22.md`, `docs/PEAD_OPTIONS_RECONSTRUCTION_2026-10-22.md`, and `docs/PEAD_OPTIONS_STRATEGY_SPEC_2026-10-22.md`.
- The licensed OptionMetrics archive remained external and was streamed read-only; no raw quote table was saved in the repository. Preserve local ignored data separately before removing a disposable worktree.
