# OPT-03: Deep in-the-money call as a stock substitute inside the Steady Drift long-only book

- Status: planned | Priority: P1 | Effort: M | Source(s): `foundations/04_OPTIONS_CONSTRUCTION.md` (delta-equivalent comparison, "size by identical gross delta / cash outlay / max loss"); `foundations/03_PORTFOLIO_EXECUTION.md` (cash yield, accounting assertions); repo `docs/STEADY_DRIFT.md`, `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md` (stock control at 10 bp; "debit spread did not automatically improve quality"); no library source proposes this exact structure (PROPOSED).

## 1. Thesis & what we take from sources (and what we do NOT take)

Thesis: PEAD is a stock-drift phenomenon; the cheapest option expression of it is the option with the least extrinsic value: a 0.80-0.90 delta call. It behaves like 80-90 shares per 100 per contract, risks only the premium (a built-in stop at zero), and frees roughly (S - premium) of cash per share to earn the T-bill rate (the book already earns cash yield: `steady_drift_plus` uses `long_only_cash`, `pead/core/rates.py`). Edge hypothesis is NOT extra alpha; it is (a) capital efficiency under the 80% cost-basis admission cap and (b) convexity in crashes, at a cost of extrinsic decay + spread + lost dividends. It survives only if the all-in cost is below the equity cost model plus the cash-yield gain.

Take: delta-equivalent matched comparison and cash accounting from Library 03/04. Do NOT take: any Kelly selector; any claim of leverage (the book must remain no-borrowing: the premium is paid in cash, max loss = premium).

## 2. Evidence grade & known defects in sources

No source tests it. In-repo analogue: #21's high-delta calls (75 DTE, 0.70 delta, 40-session): 3.29%-3.97% CAGR with -25% to -27% drawdown versus matched stock 2.95% / -6.39% (synthesis doc). That is a warning: option drawdown was ~4x the stock's on the matched cohort, so equal-premium sizing is not risk-equal. This spec therefore sizes by identical delta-adjusted notional (the library's own advice) so risk is comparable; a loss of 100% of premium on a 0.85-delta call is still ~85% x the stock loss only for moves of at least the extrinsic, but drawdowns in a gap-down are smaller per share than stock (floor at premium), larger per dollar of capital.
Known defect to avoid: comparing option return on premium with stock return on notional (Library 03 "Cash and compounding").

## 3. Exact rules

Universe/signal: exactly the events and weights that `SteadyDriftPlus.weights(events)` selects (top-decile SUE, inverse idiosyncratic-variance sizing, 60-session hold; `pead/strategies/equity/steady_drift.py`). Signal is unchanged; only the instrument changes for eligible names.
Eligibility to substitute (per event, else stay in stock): at the entry snapshot the chain has a call with
- DTE in [150, 400] calendar days (needs a LONGER expiry than the current single ~120-DTE build; the 60-session hold + exit at DTE >= 60); if none, DTE in [100, 150] with `min_dte_at_exit = 30`;
- `delta` in [0.80, 0.92], choose closest to 0.85;
- `rel_spread <= 0.04` and `(ask - bid) / (delta * S) <= max_spread_over_deltanotional`, i.e. the quoted spread is a bounded fraction of the delta-equivalent stock notional (grid {0.005, 0.01, 0.02}; for comparison a 10 bp/side stock cost is 0.001 round-trip-half);
- `open_interest >= 100`; `bid >= 1.00`;
- extrinsic share `(ask - max(S - K, 0)) / ask <= 0.12` (deep ITM).
Instrument sizing: number of contracts per name = `floor(weight_i * NAV / (delta_i * S_i * 100))` so delta-notional equals the stock weight; cap by `open_interest * 0.05` (5% of OI, a documented participation assumption). Cash not spent on premium stays in the cash sleeve earning `daily_rf` (the book's cash convention). Total premium + stock cost basis stays <= the book's 80% cost-basis admission cap (`Sizing.exposure_cap`), so the cap can be loosened ONLY by the freed collateral; do not raise gross delta above the stock book's.
Hold: same fixed window as the stock (60 sessions) then sell at the bid; exit early if the stock position is exited by the throttle (the 12% risk target in `apply_cash_and_risk_target`) - the option sleeve must follow the stock sleeve's exposure scalar (REF-overlay: read the scalar series from the equity `EquityRun`).
Fill: buy at ask, sell at bid, spread_mult grid {1.0, 1.5}; equity legs at the engine's existing cost model (`Sizing`; verify field names in `pead/engines/equity.py`).
Dividends: the call holder forgoes dividends; add the omitted dividend as a cost: `div_yield` from CRSP distributions (`ret_raw` vs `ret_mktadj` differences are not dividends; use `retx` if present in the normalized panel; VERIFY; if absent, assume 2% annual yield for the control and report with and without).
Early exercise: none (long calls are never exercised early in the backtest).

Cost-to-edge (derived, not measured): per round trip the option costs ~ spread/2 on each side of ~ (delta x S) plus extrinsic decay over 60 sessions plus forgone dividends; the benefit is T-bill interest on ~(S - premium)/S ~ 0.8 of notional (rates 0.05%-5.8% annual in `TBILL_PCT`, i.e. ~0 to ~1.4% for 60 sessions, near zero for 2009-2013) and a floor on losses. In the 2009-2013 near-zero-rate period the carry benefit is ~0 so the structure can only lose spread; in 1998-2001 and 2006-2007 (rates 4-6%) there is up to ~1% per 60-session hold to offset costs. Likelihood of surviving ask-in/bid-out: plausible only in the high-rate years; as a whole-sample improvement over stock it is unlikely and is expected to be mostly neutral.

## 4. Data requirements & availability in THIS repo

Missing for the exact rule: long-dated expiries (current chain DTE min 95, median 144; 75th percentile 170; a few reach 933, so some >=150 contracts exist but the build keeps ONE expiry nearest 120), bid/ask for non-pick contracts, per-checkpoint quotes for the substituted contract (REF-chain-v2 with `dte_hi = 400`, REF-path-quotes), the equity run's daily weights/exposure scalar (`EquityRun`), dividends. Have: equity universe, `daily_rf`, deciles. Delta from OptionMetrics for deep ITM options is often stale (volume median 0): hygiene must require `open_interest` and a two-sided quote; exercising-style early-exercise premium in deep ITM American calls on dividend payers makes OM IV unreliable; do not use IV for selection.
2011 missing options: names/periods with no substitute stay in stock; the cash comparison must be computed over identical dates.

## 5. Framework mapping

- New: `pead/strategies/portfolio/itm_substitute.py` with `class ItmSubstitute(OverlayStrategy)` (REF-overlay), `family = "options-substitute"`, registered `options_itm_sub_p1` (headline), `options_itm_sub_p1_spread15`, `options_itm_sub_div0` (ignores dividends, labelled optimistic). It runs the equity parent via `get("steady_drift_plus").backtest(ctx.equity())` (returns `EquityRun` with positions and daily) and replaces eligible positions' P&L.
- It is an overlay on an equity book and must be reported as a full-account result, so it lives under `pead/strategies/portfolio/` as an `OverlayStrategy` (REF-overlay) with `asset_class = "portfolio"`; the module-level docstring and `family` still say options.
- New prep: `pead/prep/options/select_substitute.py` (stage `select_substitute`) -> `data/event_options/substitute_picks.parquet` (`event_id, optionid, strike, exdate, entry_ask, entry_bid, delta, S, extrinsic_share`) + path quotes at the stock hold checkpoints (use the equity hold of 60 sessions; checkpoints 1/5/10/20/40/60 from `CHECKPOINTS`).
- Engine: a small `pead/engines/substitute.py::apply_option_substitution(run, picks, path, daily_rf, spread_mult)` (REF-overlay) that subtracts the replaced stock P&L and adds the option P&L + cash interest. No change to `run_equity_backtest`.
- Reuse: `EquityStrategy.backtest`, `apply_cash_and_risk_target`, `daily_rf`, `summarize`, `register_instance`, `PortfolioStrategy` blend machinery.

## 5b. Build vs. change vs. restructure

1. NEW: `pead/strategies/options/itm_substitute.py` (or `portfolio/` per REF-overlay), `pead/prep/options/select_substitute.py`, `pead/engines/substitute.py`, `tests/test_itm_substitute.py`, dividend loader if absent.
2. CHANGE: `pead/engines/equity.py`/`pead/strategies/base.py::EquityStrategy.backtest` - expose per-position daily weights and the risk-target exposure scalar on `EquityRun` (read first whether `EquityRun.positions` has per-event entry/exit and dollar size; if not, add columns; regression risk: all 22 equity strategies' pinned NAVs in `tests/test_framework.py` and the four `steady_drift*` pins in `tests/test_steady_drift.py` must be unchanged - adding columns must not change any number). `pead/prep/options/lib/options.py::OPPRCD_COLS` (see OPT-01).
3. RESTRUCTURE: REF-overlay - an `OverlayStrategy` base in `pead/strategies/base.py` that takes a parent registered equity strategy name, runs it via the registry (`pead.strategies.registry.get`), and returns a combined `BacktestResult`; `PortfolioStrategy.run` in `pead/strategies/portfolio/sleeves.py` currently only blends already-complete sleeve daily returns and cannot express "replace part of sleeve A's P&L".
4. IMPROVE: real T-bill series (ROADMAP #2) is a precondition for quoting any result of this spec, because the entire benefit is the cash yield; dividend data; the equity cost model's per-side cost needs to be read from `Sizing` and compared apples-to-apples (document it in the report).

## 6. Acceptance tests

- Delta-equivalence: for a synthetic event with S=50, delta=0.85, weight giving 1,000 shares, contracts = floor(1000/(0.85*100))=11; P&L for a small `+dS` is within gamma-order of the stock P&L.
- Zero-move synthetic path with spread: option sleeve loses exactly spread plus extrinsic decay; cash sleeve earns exactly `daily_rf` (Library 03 assertion 4).
- Accounting: `cash + option marks + stock marks == NAV` each day; no borrowing (cash never negative).
- No look-ahead: substitution eligibility uses only entry-snapshot data; the throttle scalar used is the one known at the prior close.
- Regression: with substitution turned off (`eligible` all False) result equals `steady_drift_plus` to the cent (NAV 91,947,400.4581 in `results/steady_drift_plus/summary.json`).
- Determinism and event conservation (eligible = substituted + stayed in stock + rejected-by-reason).

## 7. Experiment plan

Baselines: `steady_drift_plus` (12.07% / 1.19 / -22.07%), `steady_drift_core`, `options_cap20` for context. Matched control = the SAME names, SAME weights in stock (this is the parent). Primary metric: net Sharpe and CAGR difference versus parent with paired moving-block bootstrap on daily returns (circular 3-month blocks as in the synthesis doc). Search window (first 70% of quarters): grid `max_spread_over_deltanotional x delta_target in {0.80,0.85,0.90} x min_oi`, all trials logged. Holdout opened once. Ablations: no cash-yield credit; dividends on/off; spread_mult 1.0/1.5/2.0; substitution only in years with T-bill >= 3% (a rule that is observable ex ante: lagged 3-month rate, NOT chosen by year). Blend/test with `steady_drift_plus` at identical gross delta.

## 8. Expected outcome, honest risks

Expected: small difference from parent, sign uncertain; the economically honest outcome is "no improvement" (cost ~ carry). Upside case: slightly lower drawdown because loss per name is floored at premium. Risks: stale deltas / quotes for deep ITM contracts, thin OI at 150+ DTE in 1998-2005 (many names will have no such contract; coverage may be small and non-random: bias toward large liquid names), dividends, participation cap, American early-exercise premium, a materially different universe than the parent after eligibility filtering (compare on the eligible subset too), data ends 2013, rates near zero after 2008.

## 9. Dependencies and implementation checklist

Depends: OPT-01 (REF-chain-v2 with long expiries, REF-path-quotes, REF-fill-model, REF-overlay, REF-spread-diag), IMP-rates, dividend data.
- [ ] spread/coverage report for delta 0.80-0.92, DTE 150-400 (gate: >= 40% of top-decile events substitutable; else stop)
- [ ] `EquityRun` exposes per-position weights and throttle scalar (pins unchanged)
- [ ] `apply_option_substitution` + tests
- [ ] strategy registered; grid on search window; trials logged
- [ ] single holdout run; paired bootstrap vs parent; leaderboard entry `research`
