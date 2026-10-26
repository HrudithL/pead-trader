# Earnings-qualified SPX settlement ladder

The research now has a concrete candidate that clears the user's absolute 12–15% CAGR hurdle in historical simulations at both account sizes. It remains a **high-risk research candidate**, not an investment-approved strategy. It fails the declared 30% drawdown selector, and its holdout risk-adjusted performance is worse than the S&P proxy.

## Fixed trading rules

1. At the first market session of each week, admit bullish spreads only when the prior-close S&P wealth index is above its 200-session average, observed earnings-response breadth is positive relative to its prior trailing baseline, and prior 21-session realized market volatility is at most 30% annualized. Company reactions enter breadth only after their announcement session; no EPS forecast or old equity-strategy weights are imported.
2. Select standard, traditional AM-settled monthly SPX put spreads, 35–75 calendar days to expiry, nearest 45 days. Sell around 35 absolute delta and buy around 15 delta. Selection uses the **previous session's** Greeks, current executable bid/ask and lagged volume/open interest. Historical Saturday expirations fall on days 16–22.
3. Sell at bid and buy at ask. Reject crossed, nonstandard, insufficiently liquid or excessively costly structures. Up to eight feasible tickets per week can use different strikes/maturities, sharing capacity across both legs. Baseline capacity is at most 5% of prior volume, with an explicit assumed one-contract floor, and 2% of prior open interest. The floor-free stress removes that assumption.
4. The deployed profile admits at most 30% of NAV in initial maximum loss per ticket and entry cohort, and 80% in aggregate. Cash secures the complete contractual spread width; entry credits and all fees are included. These are **admission limits**, not a continuous cap on risk as NAV falls. There is no account borrowing and no promise that several positions cannot lose together.
5. Initiate liquidation when the existing lagged market trend turns negative or lagged realized volatility exceeds 30%. Persist that decision until the position closes. Stage integer lots against actual quotes and shared daily exit/entry capacity. A close can release its own collateral but cannot spend collateral reserved for another holding. Missing quotes defer execution.
6. Remaining contracts settle to the actual native **SET special opening quotation**, not the ordinary SPX closing price. Fee allowance is $1.50 per leg on entry and exit/settlement. No early assignment model is needed for these European index contracts.
7. Carry across calendar years. Stop new entries 75 calendar days before the predetermined sample end so admitted contracts can resolve. Accrue the disclosed, full-prior-month Treasury proxy less 10bp/year on unencumbered cash only. Cash interest is attributed separately from option P&L.

## Results after quoted costs and commissions

| Account | 1999–2010 CAGR | Maximum drawdown | 2008–2010 CAGR | Frozen holdout CAGR | Holdout drawdown |
|---|---:|---:|---:|---:|---:|
| $100,000 | 16.11% | 46.94% | 2.86% | 38.24% | 45.73% |
| $1,000,000 | 13.38% | 51.17% | 10.93% | 34.76% | 47.06% |

The frozen holdout is January 3, 2012–August 30, 2013: 418 sessions, matching the available native option and SET coverage. S&P proxy CAGR was 19.61%, maximum drawdown 9.69%, and Sharpe 1.52. Candidate Sharpe was 0.86 / 0.81; estimated annual market alpha was −11.86% / −15.12%. Higher CAGR therefore does **not** establish positive risk-adjusted alpha. The paired block bootstrap gives wide intervals, including negative CAGR and alpha; see each holdout summary.

The 2011 native option extract is absent. The first January 2012 weekly entry lacks prior Greeks and is skipped. There are no fabricated prior quotes. Holdout positions had zero missing marks and zero terminal reserves. For the $100K account, the June 4, 2012 trough was $63,960 after a $117,862 peak: that loss was real in the quoted simulation, even though the account later recovered.

Both accounts had three losing development years. The $100K result compounds to $598,787 over the development sample; the $1M result compounds to $4,502,311. These totals include Treasury-proxy income. The holdout ends at $171,107 and $1,640,224 respectively. Results cannot be scaled between account sizes because option lots and quoted capacity are discrete.

## Execution and accounting stresses

| Development implementation | $100K CAGR | $1M CAGR |
|---|---:|---:|
| Baseline staged exits | 16.11% | 13.38% |
| Double commissions | 15.91% | 13.10% |
| No minimum-lot floor | 15.69% | 14.57% |

These are separate predeclared stresses, not a combined worst-case test. Higher returns in one stressed case reflect changed contract choices and compounding; they do not imply execution constraints help universally. No knobs were changed after the holdout. An independent audit reconciles 1,382 development/holdout trade records to actual entry/exit quotes and native SET, within $7.3e-12 per trade. Full trial cash reconciliation is within $0.000001.

## Interpretation

The earlier tiny return was partly a deployment/construction problem: cash sat unused, rigid paired contracts excluded opportunities, and early closes incurred repeated spreads. The redesign tests feasible multiple-ticket allocation, correct monthly expirations, continuous positions and actual settlement. Enlarging exposure can clear the return hurdle, but the fundamental short-volatility risk remains. Pure settlement without risk exits reached 18.16% at $100K while suffering a 77.61% drawdown. Increasing concentration further was worse.

The new candidate is useful for further backtesting and paper research. It is not accepted for deployment: the drawdown gate fails, earlier development has a few pessimistic missing-mark observations, the holdout is short, and EOD quote sizes, market impact and modern-market performance are unverified. A bid/ask backtest is conditional on those execution assumptions. A fresh validation would require new data and a newly frozen protocol; the 2012–2013 holdout has now been consumed.

## Reproduce in the preserved worktree

Use its Python 3.12 / pandas 3 environment:

```powershell
& ./.venv/Scripts/python.exe -m pead.earnings_options.index_settlement --risk-exit
& ./.venv/Scripts/python.exe -m pead.earnings_options.index_settlement --risk-exit --double-fee
& ./.venv/Scripts/python.exe -m pead.earnings_options.index_settlement --risk-exit --zero-floor
& ./.venv/Scripts/python.exe -m pead.earnings_options.index_holdout
& ./.venv/Scripts/python.exe -m pead.earnings_options.research_audit
& ./.venv/Scripts/python.exe -m pead.earnings_options.research_report
& ./.venv/Scripts/python.exe -m pytest tests -q
```

The holdout runner verifies eight source hashes against its pre-test freeze. Do not overwrite that freeze to conceal later code changes. Preserve raw `D:` sources and all ignored caches; **do not remove, archive, prune or clean this worktree**.

The full register, failed profiles, earlier implementation vintages, price audit and figures are in `results/selective_earnings_research/README.md`. The original concept remains in tracked `RESEARCH.md`.
