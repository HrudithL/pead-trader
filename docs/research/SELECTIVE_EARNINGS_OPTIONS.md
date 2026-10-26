# Selective Earnings Options: implementation and research protocol

This implements only the portfolio proposed in RESEARCH.md. No existing strategy's signals, fitted holding periods, Kelly contract selections, weights or historical leaderboard results are used.

## Working safely

Dedicated branch: `codex/selective-earnings-research`.
Dedicated worktree: `C:/Users/hrudi/.codex/worktrees/selective-earnings-research/PEAD_Trading`.
Original WRDS inputs: `C:/Users/hrudi/Documents/BEI/PEAD_Trading/data` (read only).
OptionMetrics source: `D:/OptionMetrics/parquet` (read only; never copied).
Derived research cache: this worktree's `data/selective_earnings/` (git ignored).
**Preserve this entire worktree. Do not remove, prune, clean, delete or archive it.** Its ignored caches are part of the reproducible research workspace. There are no data symlinks or junctions.
The original main checkout and its untracked RESEARCH.md remain intact. RESEARCH.md and AGENTS.md are committed on the research branch; no main push was necessary.

## Reproduce

Run PowerShell from the dedicated worktree:

```powershell
& ./.venv/Scripts/python.exe -m pytest tests -q
& ./.venv/Scripts/python.exe -m pead earnings-options audit --data-dir C:/Users/hrudi/Documents/BEI/PEAD_Trading/data
& ./.venv/Scripts/python.exe -m pead earnings-options prepare --data-dir C:/Users/hrudi/Documents/BEI/PEAD_Trading/data --start 1999 --end 2010
& ./.venv/Scripts/python.exe -m pead earnings-options backtest --start 1999 --end 2010 --exploratory
```

Omit `--exploratory` to require verified timing, pre-release consensus, point-in-time identifier linkage and corporate-action evidence. The available static identifier map fails that strict requirement; strict zero-trade results must not be interpreted as strategy performance.

`python -m pead list` lists the independent `selective_earnings_options` strategy. The generic `python -m pead run selective_earnings_options --no-report` uses strict provenance. Use the dedicated research command for the SPY comparison, candidate exclusion audit and statistical diagnostics; the generic report machinery uses the repository's broader market conventions.

Use `--max-events N --cache data/selective_earnings_smoke` only for an adapter smoke test. Limited-event caches are rejected for performance reporting. Preparation is resumable per complete year. Never substitute a missing year with cash. Raw options are missing in 2011. CLI development runs reserve 2012â€“2013; no genuine final holdout has been evaluated.

## Signals and the economic filter

Analyst SUE is ranked against strictly earlier quarters. No same-quarter or full-sample percentile enters the signal. Directional positions require the top/bottom historical decile and a same-direction announcement response relative to SPY. Stock liquidity and RV use data through the previous session only.

Training labels explicitly mature after 1â€“20 **benchmark sessions**. They retain observed CRSP delisting returns; subsequent proceeds are held as cash. A missing delisting return invalidates the label. Shorter-expiry positions use a matching shorter forecast horizon. Training at a date can see only label ends strictly earlier than that date, including for the expanding online model during development test windows.

The valuation model draws empirical stock returns from this matured history, conditioned on confirmed extreme surprise for directional trades. It values the remaining spread with a zero-rate, zero-dividend Blackâ€“Scholes approximation using trailing RV as a residual-IV assumption. It charges current exit half-spreads and round-trip commissions. Required expected P&L is at least 5% of theoretical maximum loss. This is a **model-based economic screen**, not proof of a positive expected return. It does not solve American option pricing, dividend risk, uncertain IV dynamics, sector-conditioned distributions or forecast estimation error. Those limitations preclude investment validation even if an exploratory return happens to be high.

The condor sleeve additionally requires short-leg mean IV / prior realized-vol forecast >= 1.30. The directional/condor positions use the same 20-session maximum holding rule, shortened by a seven-calendar-day expiry buffer. Condors can also close at 50% of initial credit profit or a loss of 1.75 times initial credit.

The reversal engine supports a separate prior-news signal and requires a known-at-the-time pre-event schedule and an independently supplied economic-edge estimate. The local adapter cannot supply a historical published calendar or a reversal-specific event-window model. It therefore makes **no reversal trades**, rather than using subsequently observed earnings dates to manufacture entry opportunities. Calendar spreads and unconditional pre-earnings long volatility are not implemented; the approved design selected the three core sleeves.

## Contract selection and execution

Directional: buy 55â€“65 delta, sell 25â€“35 delta, same expiry, 30â€“60 DTE. Reversal: 50â€“60 / 25â€“35 delta, 3â€“10 DTE. Condor: short 15â€“20 and long 5â€“10 absolute delta on each side, same expiry, 14â€“35 DTE. Deterministic nearest-target expiry/delta and option-ID tie breaking. No future price participates in selection.

Each leg requires prior-session volume >= 50, OI >= 250, midpoint >= $0.50, relative quoted spread <= 10%. Underlying prior ADV >= $25M. Capacity is <= 5% of the least-liquid leg's previous-session volume. Previous-session liquidity is conservative but cannot establish actual participation on the execution day; the source is EOD, not intraday NBBO.

Buys execute at ask and sales at bid. The base commission assumption is $0.65 per contract per side with multiplier 100; actual broker, exchange and regulatory schedules must replace it before validation. Only nominally standard contracts pass the adapter's flags; those flags alone do not certify deliverables. Nonstandard or ambiguous duplicate quote rows are excluded.

Daily NAV is cash plus conservative executable liquidation value less exit commission reserves. No event-level return averaging or annualizing of trade returns. Zero bids are valid zero liquidation values. Missing quotes are never forward filled: the ledger reserves contractual worst loss, labels any resulting exit as a reserve rather than an actual fill, and fails execution validation.

## Capital and limits

Risk is the full spread maximum loss plus round-trip commission, using integer lots. Condor cash is encumbered by full wing width, with received credit reducing net funded capital. Entry allocation must fit free cash as well as maximum-loss budgets. Budgets are 30bp directional and 20bp condor/reversal; issuer 1%, sector 5%, entry cohort 5%, aggregate 25% NAV. All unknown sectors share one conservative bucket. No borrowing or equity hedge is added to force the return hurdle.

These are entry admission limits, not continuous liquidation limits after NAV changes. There is no production portfolio-Greeks controller, sector-factor model, broker margin model or full overnight stress/assignment engine. In particular, American early assignment can create interim stock exposure beyond a spread's terminal payoff cap. Until that is simulated and funded, the theoretical maximum-loss budget is a research allocation rule, not a guaranteed worst-path margin requirement.

## Evaluation and acceptance

Benchmark: CRSP SPY permno 84398 daily `ret`, including distributions. No current-constituent universe is back-projected. Idle cash earns zero as an explicit conservative baseline; the repository's memory-based T-bill approximation is not used. CAGR and Sharpe use 252 sessions/year. The report includes initial-capital drawdown, trade ledgers, skip reasons, yearly attribution and paired circular monthly-block bootstrap intervals for CAGR, Sharpe, drawdown and market-model alpha.

Chronological fold diagnostics use five initial training years, one validation year and annual tests, excluding training/validation labels that have not matured at the next boundary. Parameters are fixed, so validation is a diagnostic, not a hyperparameter search. Test windows report a continuously running book; they are not independently re-funded portfolios. Full-development numbers are not untouched-holdout numbers.

Acceptance requires net CAGR > max(12%, same-date SPY CAGR) + 2 percentage points, Sharpe >= SPY, positive alpha and positive bootstrap lower alpha bound, drawdown magnitude <= 1.25 times SPY, >= three years and 100 trades, executable/provenance validity, multifactor and issuer/date-cluster inference, and a genuine untouched holdout. The last two are explicitly incomplete gates and cannot pass in this implementation. Bootstrap nonpositive-alpha frequency is not a formal null-hypothesis p-value. No claim of S&P outperformance follows from a published event return, an option return-on-premium, leverage or a theoretical marked price.

## Before investment research can advance

1. Inspect measured development results and the exclusion audit before altering any signal or liquidity threshold. If changes follow observed development performance, declare a new research version and keep the final holdout untouched.
2. Obtain point-in-time identifier intervals, verified EPS/share adjustments and published earnings schedules. Implement the reversal-specific event-window training model only with that schedule.
3. Add dividends, American exercise/assignment, deliverables, stock created by assignment, real fees, funding, delisting option settlement and broker house margin.
4. Add portfolio Greeks and gap stresses, model uncertainty, multifactor controls and issuer/date clustered inference. Audit concentration and capacity at realistic capital.
5. Freeze the entire protocol and data manifest before a one-time 2012â€“2013 test. That historical holdout is short and cannot establish current-market effectiveness; obtain modern data for an additional test.

The appropriate outcome of a failed gate is rejection or further independently motivated research, not leverage selected after looking at returns.

## Primary-source anchors

- Milian, Journal of Accounting Research (2015), next-announcement reversal: https://doi.org/10.1111/1475-679x.12070
- Chung and Louis, Earnings Announcements and Option Returns: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2886040
- Options Industry Council, volatility crush mechanism: https://www.optionseducation.org/news/the-crush-is-real

These sources motivate hypotheses; none validates this portfolio or its performance.

## Additional diagnostics

Run "python -m pead.earnings_options.diagnostics" for fixed-rule USD 100,000 / USD 1,000,000 / USD 10,000,000 capital and double-commission scenarios. This keeps the precomputed economic signals fixed and recomputes integer sizing and actual fees; it does not select a scenario or retune the model. The zero-rate cash assumption applies to every scenario.


## Canonical input contract

Use `pead.earnings_options.engine.run(events, quotes, benchmark, Config(...))` for a new, independently verified vendor panel.

- Events: `event_id`, `secid`, `sector`, `entry_date`, `release_date`, `rank`, `reaction`, `rv`, `adv`; sleeve-specific `directional_edge`, `condor_edge`, `reversal_edge` expressed as expected net P&L / theoretical maximum loss. These forecasts must be computed before entry. Provenance booleans are `timing_verified`, `consensus_verified`, `identity_verified`, `corporate_actions_verified`, `assignment_verified`. `schedule_known` and `scheduled_date` describe a next announcement that was actually published by entry; reversal additionally needs `prior_rank` and entry before the current release date. A boolean is an asserted upstream verification result, not evidence supplied by this engine.
- Quotes: `date`, `secid`, `optionid`, `cp` (C/P), `strike` (dollars), `expiry`, `bid`, `ask`, `delta` (signed), `prior_volume`, `prior_oi`, `iv`, `standard_contract`. Liquidity must be the immediately preceding market session, not current-day closing volume used for a same-close decision. Contract IDs and dates must be unique.
- Calendar/benchmark: one `date` per actual market session, `benchmark` as a fractional **total return**, and `rf` as the fractional per-session cash return. All dates use a consistently normalized US-market session date. The model does not accept an intraday timestamp as proof that intraday execution occurred.

The return value supplies daily self-financing NAV, closed trades, rejected candidates and execution/provenance diagnostics. An empty strict trade book is a data-eligibility outcome, not a profitable strategy. There is deliberately no live-trading adapter.


## Measured development outcome

See `results/selective_earnings_options/README.md`. The corrected 1999–2010 exploratory run returns −0.056% CAGR versus SPY +1.892%, from only 25 directional positions. Strict provenance admits zero positions. Capital and double-commission stresses remain negative. This version is rejected for investment; the requested high-return objective has not been demonstrated.
