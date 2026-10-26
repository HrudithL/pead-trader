# PEAD options sizing stress test (2026-10-24)

## Decision

Sizing alone does not get the existing PEAD options rule near a 15–20% CAGR. The strongest-return fixed contract cell tested here reached 6.51% CAGR at the maximum cash-only premium cap, with a 52.40% maximum drawdown. Its four-factor alpha remained statistically inconclusive. Do not increase the allocation further on this evidence; the next improvement must come from a demonstrably stronger, out-of-sample signal or contract-selection edge.

This is a risk-capacity experiment, not a newly selected strategy. It deliberately holds the contract, signal, entry, and exit rule fixed and changes only the premium budget.

## Reconciling the previous Kelly result

The remembered 15.79% CAGR / 3.28 Sharpe result is `options_cap40_legacy`, which the repository marks as a reference result. It entered and exited at mids and carried stale quotes forward, including dead options; those conventions materially overstate saleable returns. Under corrected ask-entry/bid-exit treatment, the canonical `options_cap20` Kelly backtest is about 0.28% CAGR / 0.16 Sharpe. That older selector also scored contracts using mids before paying asks, used only an eight-quarter warmup, and its quarter-lagged training labels were not mature for 99.6% of the audited 60-session outcomes. The 15.79% result therefore is not an apples-to-apples investable benchmark for this corrected daily quote study. Details are in `docs/FIXES.md` and `docs/PEAD_OPTIONS_RECONSTRUCTION_2026-10-22.md`.

## Rule held fixed

- Positive surprise events in SUE deciles 9–10; buy calls.
- Target 120 calendar days to expiry and 0.50 absolute delta.
- Select contracts using the entry-date chain and existing liquidity filters; enter at the ask.
- Exit after 60 shared CRSP/OptionMetrics trading sessions at the bid. Missing or zero-bid exits receive zero proceeds.
- $10 million initial NAV. Daily cash earns the French daily risk-free factor series used by the prior study.
- Study dates: 1998-01-02 through 2013-12-31. The 2011 options gap is cash-only and 2013 source coverage is partial.

Among the baseline funded contracts, median actual DTE was 122 days, absolute delta was 0.502, strike/spot was 1.034 (about 3.4% out of the money), and quoted spread/ask was 8.16%.

The tested strategy is the prior grid's in-sample return leader. The sizing test does not remove the grid-selection bias or resolve the same-day earnings/quote timestamp issue.

## Sizing cases and results

Each multiplier scales the 20% NAV outstanding-premium limit and the 0.25% NAV per-signal budget together. The 5× case therefore permits up to 100% NAV in paid option premium and up to 1.25% NAV per signal before the trailing signal-count scaler. The 10% of observed open interest limit, 2% per-issuer cap, pro-rata same-day allocation, whole-contract rounding, and cash-only funding remain fixed. All five cases funded 6,023 signals; higher cases purchased more lots in the same trades.

| Premium-budget multiplier | Outstanding premium cap | Ending NAV | CAGR | Annual vol. | Excess-RF Sharpe | Max drawdown | Four-factor alpha (HAC 95% CI) | Ending wealth above cash proxy |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1× | 20% NAV | $19.34m | 4.21% | 11.59% | 0.215 | −33.19% | −1.78% [−5.71%, +2.16%] | +$4.87m |
| 2× | 40% NAV | $22.34m | 5.15% | 15.80% | 0.251 | −44.35% | −1.84% [−7.57%, +3.89%] | +$7.86m |
| 3× | 60% NAV | $24.76m | 5.83% | 17.66% | 0.279 | −48.22% | −1.53% [−8.22%, +5.16%] | +$10.28m |
| 4× | 80% NAV | $25.95m | 6.14% | 18.76% | 0.289 | −50.78% | −1.36% [−8.74%, +6.03%] | +$11.47m |
| 5× | 100% NAV | $27.40m | 6.51% | 19.35% | 0.303 | −52.40% | −1.01% [−8.83%, +6.81%] | +$12.93m |

The 5× case raises CAGR by 2.30 percentage points over the baseline, while maximum drawdown deepens by 19.21 percentage points. Excess-RF Sharpe rises by 0.088, but remains low. Alpha point estimates remain negative and every interval includes zero; the widening intervals do not establish positive alpha. Trade selection is unchanged: 35.88% of funded trades are profitable and median trade ROI is −47.62% at every sizing level. The account gains still depend on a minority of large winners.

The 5× ending NAV reconciles to the $10m starting cash, $5.18m of compounded cash interest, and $12.22m of option P&L (exit proceeds less paid premium). Its ending advantage over the cash-only proxy is +$12.93m. The quote fills do not account for commissions, displayed size, queue position, or market impact. A $10m account would need about $93.5m after this 15.9945-year span to compound at 15%, or $184.7m to compound at 20%. The 5× run ends at $27.4m.

The correct conclusion is not that 6.51% is a stable forecast. It is a historical, in-sample outcome from one selected cell and a rounded public risk-free proxy. The 5× drawdown is already large, there is no statistically established positive alpha, and the 15–20% target is not reached even after using the full cash-only premium budget. More gross exposure would require borrowing or margin and would magnify an edge that has not been established.

## Reproduction

The run streamed licensed OptionMetrics files from an external archive; it did not copy raw option data. It saved aggregate portfolio paths and summaries under `results/options_c12050_sizing_sweep_2026-10-24/`.

```powershell
.venv\Scripts\python.exe -m pead.research.options_expiry_grid `
  --years 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2012 2013 `
  --dte-targets 120 --delta-targets 0.5 --holds 60 --direction C `
  --data-dir $env:PEAD_DATA_DIR `
  --optionmetrics-dir $env:OPTIONMETRICS_DIR `
  --output-dir results/options_c12050_sizing_sweep_2026-10-24 `
  --portfolio --daily-portfolio --sizing-multipliers 1 2 3 4 5
```

The focused test suite for the changed option-grid and daily-portfolio code passed: 26 tests. The ignored local `data/` and runtime files must be preserved outside a managed worktree before cleanup; none of the licensed raw option archive is included in this repository.
