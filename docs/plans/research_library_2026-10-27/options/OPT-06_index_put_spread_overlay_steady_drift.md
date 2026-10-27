# OPT-06: Beta-sized SPX put-spread tail overlay on the Steady Drift book

- Status: planned | Priority: P2 | Effort: M | Source(s): `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md` (candidate 3, "Steady Drift with a beta-sized index hedge", whole-lot feasibility gate, compare against reducing exposure + cash), `docs/RETURN_TARGET_AUDIT_2026-10-26.md` (carry with paid protection 1.40% / Sharpe -0.678; direct SPY beta hedge Sharpe 0.897 vs 0.674 with CAGR 10.37% -> 7.29%), `foundations/04_OPTIONS_CONSTRUCTION.md` (defined-risk, debit vertical), `foundations/03_PORTFOLIO_EXECUTION.md` (benchmark at matched risk). External rationale named in the synthesis doc (Cboe PUT/CLL comparison, Israelov "Pathetic Protection") is not re-verified here.

## 1. Thesis & what we take from sources (and what we do NOT take)

Thesis: `steady_drift_plus` earns 12.07% with -22.07% max drawdown and is long small/mid caps (market beta, plus the announcing-stock premium). A cheap, defined-risk index put spread cuts left-tail drawdown at a cost of premium. The thesis is risk management, not alpha: it passes only if it beats the obvious alternative (hold less equity, more T-bills) on drawdown per unit of return given up.

Take: beta from trailing returns of the book (not an unweighted sum of stock betas - the synthesis doc rejected such a worker suggestion), whole-lot feasibility as an early stopping gate, comparison with exposure reduction. Do NOT take: single-name protective puts (rejected, see `_README.md`); leverage; SPX short-put carry (PR #19-#22 material, separate research line with a consumed holdout); any assumption that the hedge is favourable.

## 2. Evidence grade & known defects in sources

Repo evidence (from the audit docs, other PR branches not in this checkout): paid protection on SPX carry lost (1.40% CAGR, Sharpe -0.678); a direct SPY beta hedge on the funded equity parent raised Sharpe 0.674 -> 0.897 and cut max drawdown -28.67% -> -6.93% but lowered CAGR 10.37% -> 7.29%; a hedge reduces exposures and "does not automatically supply additional expected return". These show a linear (SPY) hedge, not an option hedge; option premium adds drag on top. Index beta does not retain small/mid-cap, sector, liquidity premia.

## 3. Exact rules

Book: `steady_drift_plus` daily returns and daily NAV (its own cash accounting).
Hedge instrument: SPX European options (secid 108105 in `opprcd`; AM-settled monthly third-Friday contracts, settlement on actual SET; the extract has secid for SET per the audit docs; VERIFY the SET secid and the settlement price source `secprd`/`indexd` before building).
Schedule: first trading day of each month (`rebalance_day`), close snapshot.
Beta: `beta_t` = OLS slope of the book's daily returns on the CRSP VW market return (`EquityUniverse.vwretd`) over the trailing 126 sessions ending at the prior close, no shrinkage in the baseline (shrinkage `0.5*beta + 0.5` as an ablation); floor 0, cap 1.5.
Structure: buy 1 put at `|delta|` closest to 0.25 and sell 1 put at `|delta|` closest to 0.10 (same expiry), expiry DTE in [60, 100]; both legs `bid > 0`, `open_interest >= 100` (SPX is liquid; check), strike spacing consistent.
Notional: `lots = floor(hedge_ratio * beta_t * NAV_book_exposure / (SPX_level * 100))` with `hedge_ratio` in {0.5, 1.0} of beta-exposed notional, subject to premium budget `annual_premium_budget` in {0.5%, 1.0%} of NAV per year allocated monthly (`budget_month = annual/12 * NAV`); if lots == 0 (whole-lot infeasible) the month is unhedged and counted in `n_months_unfundable` (do not raise the budget). Hedge only while the book is invested: scale by the book's current gross/NAV (use the exposure series).
Hold: to the next rebalance (one month) then SELL the remaining spread at bid and re-buy at ask (roll, costs both ways) or hold to expiry (ablation `roll=expiry`); choose on the search window.
Fill: buy long put at ask, sell short put at bid at entry; at exit sell long put at bid, buy short put at ask; `spread_mult` {1.0, 1.5, 2.0}. Settlement for expiry exits: intrinsic from actual SET (REF-assignment, REF-index-underlying); no early-exercise risk (European).
Cash: premium paid from the book's cash sleeve (the book holds idle cash earning `daily_rf` by construction), never borrowed.
Comparison arms on the SAME dates: (a) unhedged parent; (b) parent with exposure scaled by `(1 - drag)` where `drag` is chosen so the unhedged-scaled parent has the same realized average premium drag (cash replaces stock); (c) linear SPY-beta hedge at equal beta notional (costless futures-like benchmark, using `vwretd`) as the upper bound for hedge efficiency.
Cost-to-edge (derived, NOT measured): a 25/10-delta 60-100 DTE put spread costs a few tenths of a percent of notional per month at typical SPX IV and the spread on SPX options is small relative to single names (measured for single names only: 11.9% median; SPX unmeasured, but a given ask/bid cost is small versus the premium of an index spread in absolute terms; REF-spread-diag will measure). With a 1% annual premium budget the hedge buys partial coverage only; payoff occurs only in sharp index sell-offs, and the book's drawdown (-22.07%) is partly not market-driven. Survival after ask-in/bid-out: the spread itself is likely not fatal; the premium is. Expected: negative carry that must be repaid by drawdown reduction; likely fails the return-per-drawdown comparison against arm (b).

## 4. Data requirements & availability in THIS repo

Have: SPX options in `opprcd{year}` (secid 108105 present; 628 rows on 2005-06-15 across 8 expiries); the book's returns. Missing: SPX index level and SET settlement series for the full period (`indexd.parquet`, `secprd{year}.parquet` exist in `D:\OptionMetrics\parquet`; the audit docs mention SET SECID 118963 has no rows in 1996-97; so SPX-settlement coverage starts later than 1998 maybe; VERIFY), a dedicated SPX chain build (monthly snapshot only: 216 months, tiny), the book's per-day exposure series, 2011 absent (no hedge in 2011: counted as unhedged months).
Index dividends: `idxdvd.parquet` exists (index dividend yield; useful for put-call parity sanity only).

## 5. Framework mapping

- New: `pead/strategies/portfolio/steady_drift_hedged.py`, `class IndexPutSpreadOverlay(OverlayStrategy)` (REF-overlay), names `steady_plus_putspread_b100`, `steady_plus_putspread_b50`, `steady_plus_cash_matched` (arm b), `steady_plus_spy_beta` (arm c, linear benchmark).
- New prep: `pead/prep/options/index_chain.py` (stage `index_chain`) -> `data/event_options/spx_monthly_chain.parquet` (monthly snapshot of SPX put chain with bid/ask/delta/OI; plus path quotes to next rebalance; REF-path-quotes reused with an index-underlying mode) and `data/metadata/spx_level.parquet`.
- Engine: `pead/engines/index_overlay.py::apply_put_spread_overlay(run, chain, beta_series, budget, spread_mult)` returns the combined daily series; uses REF-leg-engine fill functions for 2 legs but no event budget (a calendar-driven sleeve).
- Params: `dict(beta_window=126, hedge_ratio, delta_long=0.25, delta_short=0.10, dte_lo=60, dte_hi=100, annual_premium_budget, roll, spread_mult, beta_cap=1.5)`.
- Reuse: `get("steady_drift_plus").backtest(ctx.equity())` (`EquityStrategy.backtest`), `daily_rf`, `vwretd`, `summarize`, `PortfolioStrategy` pattern for reporting `market_beta` / `corr_market`.

## 5b. Build vs. change vs. restructure

1. NEW: `pead/strategies/portfolio/steady_drift_hedged.py`, `pead/prep/options/index_chain.py`, `pead/engines/index_overlay.py`, SPX level/SET loader, `tests/test_index_overlay.py`.
2. CHANGE: `pead/strategies/base.py` (add `OverlayStrategy`), `pead/engines/equity.py`/`EquityRun` expose daily gross/NAV exposure series (see OPT-03; regression: pinned NAVs of all equity strategies unchanged).
3. RESTRUCTURE: REF-overlay (overlay interface: parent run in, adjusted daily series out), REF-fill-model (two-leg index structure), REF-assignment (European cash settlement).
4. IMPROVE: IMP-rates (the cash alternative arm needs a real T-bill series), report additions: hedge premium paid, hedge P&L, cash interest, option/stock P&L separated (the synthesis doc's validation list), drawdown-per-premium table, unfundable months.

## 6. Acceptance tests

- Beta window uses only returns up to the prior close; changing the future leaves `beta_t` unchanged.
- Whole-lot test: with NAV so small that lots==0 the overlay is exactly the parent (to the cent) and `n_months_unfundable` equals the month count.
- Payoff test on synthetic SPX chain: terminal P&L of the put spread equals `max(Kl-S,0)-max(Ks-S,0)-debit`, max gain `(Kl-Ks)*100`; settlement at SET not close.
- Round trip at unchanged quotes loses exactly the two-leg spread.
- Cash/accounting: premium never exceeds the budget; `cash + marks + stock == NAV`.
- Regression: overlay with budget 0 equals `steady_drift_plus` final NAV 91,947,400.4581 (`results/steady_drift_plus/summary.json`).

## 7. Experiment plan

Baselines: `steady_drift_plus`; arms (b) and (c). Metrics: CAGR, Sharpe, max drawdown, Calmar; premium drag per year; worst 5% market sessions mean return (the synthesis doc's table style); paired circular 3-month block bootstrap versus parent and versus arm (b). Search window: `hedge_ratio x annual_premium_budget x roll x delta pair` (2x2x2x2 = 16 cells, logged); holdout once. Stress: `spread_mult` 2.0; 2008Q3-Q4 drilled separately; check that the hedge pays in the book's actual worst 20 days (the book's worst days are not necessarily index crashes). Gate before building the engine: monthly cost of 1 lot versus budget at NAV 10,000,000 (INITIAL_CAPITAL) - if lots==0 in most months the idea is infeasible at this size and stops.

## 8. Expected outcome, honest risks

Expected: lower drawdown, lower CAGR, Sharpe flat to lower versus parent; very likely worse than arm (b) per unit of drawdown reduced. Risks: basis risk (small-cap book vs SPX), index skew makes puts expensive in crises exactly when needed, premium drag compounding, settlement-coverage gaps, hedge ratio from a noisy beta, correlated drawdown with the steady book (worst 5% SPY sessions: equity -1.212% vs SPY -3.156% per the synthesis doc table, i.e. beta ~0.4 in those sessions, so the hedge notional must be small), data ends 2013, 2011 absent.

## 9. Dependencies and implementation checklist

Depends: OPT-01 REF-overlay, REF-fill-model, REF-assignment, REF-index-underlying, IMP-rates; no dependency on OPT-02..05.
- [ ] whole-lot feasibility table
- [ ] SPX level / SET settlement series verified
- [ ] monthly SPX chain stage
- [ ] overlay engine + tests
- [ ] arms (a), (b), (c) registered; grid; holdout once
- [ ] leaderboard entry labelled `research`
