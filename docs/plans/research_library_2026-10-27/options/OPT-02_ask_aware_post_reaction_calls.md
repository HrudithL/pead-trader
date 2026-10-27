# OPT-02: Ask-aware ("Tier B") post-reaction directional options on top-decile SUE (calls; put and vertical ablations)

- Status: planned | Priority: P0 | Effort: M | Source(s): `docs/ROADMAP.md` item 9 and `docs/REPO_MAP.md` "Tier B"; `foundations/04_OPTIONS_CONSTRUCTION.md` (directional long call/put, debit vertical); `strategies/21_QUANT_SCANNER_EARNINGS_AVOIDANCE.md` (earnings-window risk rule, used as an exit rule only); `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md` (matched stock / call / debit-spread controls, low trailing-vol conditioning)

## 1. Thesis & what we take from sources (and what we do NOT take)

Thesis: PEAD is a slow directional drift after the first reaction. A long call is a levered, capped-loss way to own it, but the current book (`options_cap20`, 0.28% / Sharpe 0.16) loses its edge to the bid/ask spread and to dying OTM contracts. The selector (`pead/prep/options/optimal_contract_selector.py`, Kelly on mid prices) never saw the ask. Tier B: choose the contract and the exit using the executable cost, restrict to liquid, higher-delta contracts where spread is a smaller fraction of expected move, and test on a held-out window.

Take: Library 04 "choose using forecast distribution ... not purely a predicted direction", "reprice candidate contracts at the exact exit", "compare same event, fill and exit across equity/call/vertical". Take from the repo's own #21 study (read in `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md`): low trailing-volatility call subset was the only cell with a stable positive result (3.43% / Sharpe 0.280, four-factor annual alpha +0.04% with HAC interval [-1.30%, +1.38%], i.e. not distinguishable from zero).
Do NOT take: Bailey's long call + daily delta hedge (rejected, see `_README.md`); Kelly sizing from walk-forward decile distributions (the half-Kelly weights were estimated on 8 quarters early on; replace with equal premium weights so sizing cannot hide or manufacture edge); the 3% flat cost or mid fills.

## 2. Evidence grade & known defects in sources

- Repo evidence, high quality but negative: with ask-in / bid-out fills `options_cap20` 0.28% / 0.16, `options_cap30` -0.10% / -0.02, `options_worst_case` -1.04% / -0.52 (`results/options_*/summary.json`; `docs/FIXES.md`). 97.1% of funded positions are calls (`pct_calls` 0.9714), so the book is already effectively a call book.
- #21 (not in this checkout; from the synthesis doc): balanced 75 DTE / 0.70-delta call, 40-session hold, same-day entry 3.97% / -25.58% DD / Sharpe 0.204; next-session entry 3.29% / 0.141; debit call spread 1.43% / Sharpe -0.123; matched stock at 10 bp/side 2.95% / -6.39% / 0.278. So on the repo's best evidence, the call does not beat the stock on a risk basis.
- Bailey (Library 09, audit 05): author-reported -3.80%; unverified; irrelevant to the unhedged directional call except as a warning that theta + IV decay + spread dominate short holds.
- Selection bias: any cell chosen after seeing results is in-sample; the repo's constants are already partly in-sample (`docs/FIXES.md` D6).

## 3. Exact rules

Event: `decile == 10` of `decile_events_secid.parquet` (point-in-time rank, `pead/common/pit_rank.py`); variant `decile >= 9`. Signal known at D0 close (`sue_rank_pct` uses strictly earlier quarters). `timing_clean` not required.

Entry: close snapshot of `D0 + entry_lag` (default 1; `entry_lag = 0` reproduces the options_cap20 convention for comparison).

Contract (all from the entry-session chain, REF-chain-v2):
- Side C (calls). Ablation A: side P on `decile == 1` (bearish, no stock borrow needed) and `decile in (1, 10)` combined.
- Expiry: DTE in [90, 150] calendar days (hold <= 40 sessions leaves >= ~50 calendar days at exit; at 60 sessions leaves ~30; time-stop must keep exit DTE >= 21 as a hard rule).
- Delta: `delta_target` in {0.50, 0.65, 0.80} (grid; chosen on the search window only), choose by `pick_by_delta` (OPT-01 3.3).
- Liquidity filters (all pre-registered): `bid >= 0.25`, `rel_spread <= max_rel_spread` in {0.05, 0.08, 0.12}, `open_interest >= oi_min` in {50, 100, 250}, underlying >= $5, premium (ask) >= $0.50 (penny options excluded; `OptionsSizing.min_premium` exists but is 0.0 by default).
- Optional conditioning (ablation, toggled): `iv_rv` (from `event_vol_features.parquet`, columns `atm_iv, tvol_60d, rv_ann, iv_rv`) in the lowest tercile using breakpoints from strictly prior quarters only (the #21 low-vol idea; breakpoints must be computed as in `pit_rank.py`).

Exit (REF-exit-rules, evaluated only on checkpoint bids so every exit is causal and executable):
- Time stop `hold_h` in {20, 40} sessions (default 40) at the bid of that checkpoint.
- Optional (ablations, one at a time): stop-loss exit at the first checkpoint where `bid <= (1 - stop) * entry_ask`, `stop in {0.5}`; take-profit at the first checkpoint where `bid >= (1 + tp) * entry_ask`, `tp in {1.0}`; delta-collapse exit if `delta < 0.20` at a checkpoint.
- Earnings-window rule (Library 21): force exit before the next expected announcement (expected = this event's `anndats` + 85 calendar days minus 5; at <= 40 sessions this should never bind; log if it does).
- Zero bid at exit: `dead_quote_mark = "zero_bid_zero"` as the primary (pessimistic) mark; `zero_bid_half_ask` as the secondary, both reported.

Fill: ask in, bid out (OPT-01 3.5), `spread_mult` 1.0 headline, 1.5 and 2.0 stress, commission 0 headline and 0.65/contract sensitivity.

Sizing: equal premium per funded position within a quarter (`weights_col = 1`), total outstanding premium <= `max_premium_frac` x trailing NAV (existing engine rule in `run_options_backtest`), `max_premium_frac` in {0.10, 0.20}; whole lots of 100; position premium cap 1% of NAV. Initial capital 10,000,000 (`pead/common/paths.py::INITIAL_CAPITAL`).

Structure ablations (all via the same entry/exit): (B) call debit spread long delta_target, short delta_target - 0.25, both legs through hygiene (needs REF-leg-engine); (C) put on decile 1.

Cost-to-edge (derived back-of-envelope, NOT measured): a 120-DTE 0.5-delta call premium is roughly 0.4 x IV x sqrt(T) of spot; with the chain's median ATM IV of 0.412 (`event_vol_features.atm_iv`) and T ~ 0.33 yr that is ~9.5% of spot. A 1-2% stock drift over the hold at delta 0.5 gives ~0.5-1% of spot, i.e. 5-10% on premium, against a measured selector-pick median round-trip spread burden of roughly 10% of premium (entry 11.9% + exit about 9.1%, each half crossed). Edge/cost ~ 0.5-1 at best for these contracts; it only improves if the spread filter finds contracts with 3-5% relative spread. Survival after ask-in/bid-out: unlikely at unfiltered quality (it did not survive); plausible only in the filtered, liquid cell, and even then probably marginal.

## 4. Data requirements & availability in THIS repo

Have: `optimal_forward_prices.parquet` (113,927 picks with bid/ask at 0/1/5/10/20/40/60 for ONE Kelly contract per event), `full_chain_with_underlying.parquet` (all strikes at one ~120 DTE expiry, with mid, delta, iv, volume, OI, underlying), `event_vol_features.parquet` (124,686 events), deciles in `decile_events_secid.parquet`. Calendar `om_trading_calendar.parquet`.
Missing: bid/ask for non-picked contracts at entry and at every checkpoint (REF-chain-v2 + REF-path-quotes for the top-decile subset only: ~19,291 decile-10 events in the 162,632-row events table, of which only those with an option chain qualify; a few thousand contracts per year is a small scan, similar in cost to `forward_prices_optimal.py`). The 2011 gap and the 1996-97 gap remain. Entry at `D0+1` needs the `D0+1` snapshot (REF-chain-v2 includes it).

## 5. Framework mapping

- New: `pead/strategies/options/tier_b_calls.py` defining `class TierBCalls(OptionsStrategy)` with `family = "options-tierb"`, params dict `dict(side, decile_set, entry_lag, dte_lo, dte_hi, delta_target, max_rel_spread, oi_min, min_bid, min_premium, ivrv_gate, hold_h, stop, tp, max_premium_frac, spread_mult, commission, dead_quote_mark)`, `live_ready = False`.
- Registered names (via `register_instance`, pattern of `kelly_optimal.py`): `options_tierb_c65` (headline), `options_tierb_c50`, `options_tierb_c80`, `options_tierb_c65_lowiv`, `options_tierb_c65_lag0` (for like-for-like with `options_cap20`), `options_tierb_p_d1` (put ablation), `options_tierb_spread` (needs REF-leg-engine), `options_tierb_c65_mid` (reference: mid fills, to show the fill cost).
- New prep: `pead/prep/options/select_tierb.py` (stage `select_tierb`): reads chain snapshots, applies filters, writes `data/event_options/tierb_picks.parquet` with the same `<prefix>_*` columns as `optimal_forward_prices.parquet` so `run_options_backtest(prefix="tierb")` runs unchanged (REF-engine-prefix) plus REF-path-quotes columns for checkpoints.
- Engine: `run_options_backtest` needs (a) `prefix`, (b) per-position `exit_h` (today the engine marks all positions to `checkpoints[-1] = hold_horizon` and sells at the final checkpoint's bid; early-exit rules need an `exit_h` column and the same bid-out on that checkpoint), (c) `weights_col` for equal weighting instead of `kelly_fraction`. Today's engine line `kelly_frac = panel["kelly_fraction"] * half_kelly` becomes `w = panel[weights_col]` with default reproducing Kelly.
- Reuse: `OptionsSizing`, `run_options_backtest`, `summarize`, `BacktestResult`, `OptionsStrategy`, `register_instance`, `optimal_forward_prices` columns as a schema model.

## 5b. Build vs. change vs. restructure

1. NEW: `pead/strategies/options/tier_b_calls.py`; `pead/prep/options/select_tierb.py`; `data/event_options/tierb_picks.parquet`; `tests/test_tierb.py`; OPT-01 items REF-chain-v2, REF-event-clock, REF-path-quotes, REF-exit-rules, REF-spread-diag.
2. CHANGE: `pead/engines/options.py::run_options_backtest` (add `prefix`, `weights_col`, `exit_h`; regression risk: pinned NAVs for all eight registered options strategies must stay identical, `tests/test_framework.py`, `tests/test_fixes.py`); `OptionsSizing` gets new fields with defaults equal to current behaviour (`weights_col="kelly_fraction"`, `exit_col=None`); `pead/strategies/options/__init__.py` unchanged (auto-discovery by `pkgutil`, `pead/strategies/registry.py::discover`).
3. RESTRUCTURE: REF-fill-model first; REF-leg-engine only for the spread ablation (can be deferred).
4. IMPROVE: quote-presence handling at checkpoints (22.6% of Kelly picks have no mid at 60d; the new path table must record `bid`, `ask`, `has_quote`, `dead` so the dead-quote share is a reported statistic by delta bucket); a per-position delta/vega/theta attribution (IMP-greeks-attribution, uses gamma/vega/theta now kept by REF-chain-v2); report that splits P&L into delta, vega(IV change), theta, spread.

## 6. Acceptance tests

- No look-ahead: changing any quote after the entry snapshot or any event after D0 leaves the picks unchanged; `ivrv` breakpoints use only prior quarters; entry price uses the entry-session ask only.
- Fill unit tests: synthetic panel with ask 1.10 / bid 0.90 / mid 1.00 gives P&L exactly `lots*100*(0.90-1.10)` (extends `tests/test_fixes.py::test_bid_ask_fill_pnl_is_exactly_bid_minus_ask`); early exit at `exit_h=20` uses the 20-day bid, not the 40-day.
- Exit rules: stop/tp trigger only on checkpoint bids; a position with no quote at its exit checkpoint takes the dead-quote mark, never the last live mid.
- Payoff test: at expiry (synthetic chain with DTE small) long call P&L = `max(S-K,0) - ask`; vertical = `width - debit` cap.
- Determinism: two runs, identical `daily.csv.gz` hash.
- Regression: `options_cap20/30/worst_case/midfill/legacy` final NAV unchanged.
- Event-count conservation: funded + rejected-by-reason == eligible.

## 7. Experiment plan

1. Spread report for decile-10 contracts by delta and DTE (gate: proceed only if some cell has median `rel_spread <= 0.08` with >= 30% of events fundable; otherwise stop and record "no liquid cell").
2. Baselines (rerun, not quoted from memory): `options_cap20`, `options_cap30`, `options_worst_case`, and the matched stock control `steady_drift_plus` equal-weight 40-session hold on the same events (equity engine with `with_fixed_hold`, `hold_sessions=40`, as in `pead/strategies/base.py::EquityStrategy.backtest`).
3. Grid on the SEARCH window only (first 70% of announcement quarters; REF-search-holdout): `delta_target x max_rel_spread x oi_min x hold_h` = 3x3x3x2 = 54 cells; record ALL trials in `results/options_tierb_trials.csv` (Library audit 03 section 5: all-trials registry). Choose by search-window Sharpe at spread_mult 1.5, not 1.0.
4. Freeze one cell; open the holdout once at spread_mult 1.0 / 1.5 / 2.0, zero-bid zero and half-ask marks.
5. Ablations: lag 0 vs 1; low-IV/RV gate on/off; stop/tp; side P on decile 1; debit spread; `decile>=9`.
6. Placebos: shuffle SUE decile labels within quarter (as the equity placebo does), randomised D0 +/- 20 sessions.
7. Blend: add to `PortfolioStrategy` in `pead/strategies/portfolio/` as `steady_plus_tierb` with weights `{"steady_drift_plus": 1.0, "options_tierb_<frozen>": w}`, `w` such that premium at risk <= 10% of NAV; report correlation (`sleeve_correlation` in `PortfolioStrategy.run`), drawdown and Sharpe versus `steady_drift_plus` alone (12.07% / 1.19 / -22.07% per `results/leaderboard.md`). Note the sleeve blend adds returns on one NAV (sleeve weight is a NAV multiple), so check premium cash is not double counted with the equity book's 80% admission cap.

## 8. Expected outcome, honest risks

Expected: the filtered cell may reach a small positive net return (low single digits) at spread_mult 1.0, most likely fails at 1.5. The honest base rate: the same signal went from 4.12% to 0.28% on honest fills; filtering for liquidity removes the worst spread cost but also the most-mispriced contracts. Risks: selection on the search window (54 cells), overlapping 40-session holds, correlation with the equity book (corr(equity, calls) 0.579 in the synthesis doc), 2011 absent, dead-quote marking dominating results, data ends 2013, high-delta calls cost more premium so fewer lots at the same budget, positive alpha could come from the announcing-stock universe not the surprise (placebo needed).

## 9. Dependencies and implementation checklist

Depends on OPT-01: REF-search-holdout, REF-fill-model, REF-engine-prefix, REF-event-clock, REF-chain-v2, REF-path-quotes, REF-exit-rules, REF-spread-diag. Spread ablation also REF-leg-engine.
- [ ] spread gate passed
- [ ] `select_tierb` stage + tests
- [ ] engine `weights_col` / `exit_h` with regression pins
- [ ] strategy file + registered names
- [ ] baselines rerun; trials registry file
- [ ] frozen cell; single holdout run; stress matrix
- [ ] placebos; blend test; leaderboard entry labelled `research`
