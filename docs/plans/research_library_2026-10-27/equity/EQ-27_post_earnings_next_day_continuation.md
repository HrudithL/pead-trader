# EQ-27: Earnings-gap continuation, daily-data version (tradable next-session open-to-close; Perez-style same-session version as a diagnostic only)
- Status: planned | Priority: P2 | Effort: L | Source(s): library `strategies/17_ALBERT_PEREZ_POST_EARNINGS_INTRADAY.md` (https://github.com/AlbertPerez7/earnings-stock-analyzer), `foundations/02_EVENT_TIME_PIT.md`

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Perez's idea: the overnight gap after earnings is information revealed before you can trade; the open-to-close move that follows may continue in the gap direction for historically suitable stocks. The two returns are disjoint and only the second is tradable. Take: the four-quadrant bookkeeping (gap sign x subsequent intraday sign), stock-specific continuation score estimated only on history before the trade, equal split of capital among that day's active trades, cash when none. Do NOT take: entering at the open auction of the same session in which the gap is first observed (infeasible without foreknowledge of the gap; the library itself says to test a 1-15 minute delay, which needs intraday bars we do not have), one-time top-25 selection frozen at 2017, the "momentum bias" score whose exact formula the library says must be read from the script, direction chosen by gap alone including shorts (long-only repo rule; see below), zero costs.

## 2. Evidence grade & known defects in the source
Library grade B/C: README and pipeline names read, no numbers established, train 2003-2017 vs OOS 2018-2024 boundary inconsistent (2017 vs 2018), "No modeled commission, spread, opening auction slippage, short borrow, impact". Not a multi-week PEAD hold. Anything built here is a new research result.

## 3. Exact rules
Definitions per event with reaction session row r (EQ-22): `gap = open[r]/close[r-1]-1`, `h0 = close[r]/open[r]-1` (diagnostic, infeasible fill), and for the tradable version `h1 = close[r+1]/open[r+1]-1` (next session open to close). Signal known at the close of row r: `g_sign = sign(gap)`, `d0_excess`.
Continuation score for stock p at event time t: `C_p(t) = (# prior events of p with sign(gap)*sign(h1) > 0) / (# prior events with gap != 0 and h1 defined)`, using only prior events whose r+1 close is strictly before t's day0 date; requires >= 8 prior events, else not eligible. Shrunk: `C_shrunk = (k + 0.5*m) / (n + m)` with m = 8 (proposed).
Selection (PIT, re-ranked every day0 with the information then available): eligible if `C_shrunk >= q_80` of that stock-universe's scores computed on the previous 365 days of events, and `gap > 0` (long-only: positive-gap continuation only), `avg_dollar_volume_21d >= 2e6`, price >= 5.
Trade: buy at open[r+1], sell at close[r+1] (same session). Notional per trade = NAV_lag / max(n_active_that_day, 20) capped at 5% of the stock's ADV, so on a quiet day most capital is cash earning the T-bill (`pead/core/rates.py::daily_rf`). Costs: round trip `2 * (half-spread + 5 bps)` using `qspread_prev` from EQ-26 when available, else `COST_BPS` table; sensitivity to 50 bp round trip as the library suggests. No overnight holding, no stops, no shorts.
Diagnostics reported separately (not traded): quadrant frequencies; `h0` continuation (the Perez same-session figure, labelled "not investable"); positive-gap vs negative-gap asymmetry.

## 4. Data requirements & availability in THIS repo
Available: CRSP daily open/close (`openprc`, `prc`) via REF-dsf-reader, event timing from the events file. Missing, BLOCKED: any minute-level bars or opening-auction prints (exact input: consolidated 1-minute bars with exchange timestamps for 1996-2013 events, or TAQ), needed for the delayed-entry test and for confirming that the release preceded the open. Daily proxy: use next-session open (decision at the close, so no foreknowledge). Early-year day0 mis-dating (EQ-22 section 2) invalidates r for 1996-98 events: exclude `anntims_unknown`.

## 5. Framework mapping
- Signal build: `pead/prep/equity/gap_history.py` -> `event_features/gap_continuation.parquet` (`h1`, prior-event counts, `C_shrunk`, `c_rank`).
- Engine: the multi-day engine (`run_equity_backtest`) enters at day0 close and cannot hold open-to-close; add a small separate function `run_intraday_event_book(uni, events, r_trade, cost_bps, rf)` in new `pead/engines/intraday.py` returning an `EquityRun`-compatible `daily` frame (columns date, daily_pnl, nav, daily_return, gross_exposure) so `pead/core/store.py`, reports and leaderboard work unchanged.
- Strategy base: new `IntradayEventStrategy(Strategy)` in `pead/strategies/base.py` with `asset_class = "equity"`, `run()` calling the new engine; registered `gap_continuation_nextday` (family `equity-intraday`, status `research`, `legacy_twin=False`).
- Params: `min_prior_events=8`, `shrink_m=8`, `select_quantile=0.8`, `min_price=5`, `min_dollar_volume=2e6`, `cost_roundtrip_floor_bps=10`.

## 5b. Build vs. change vs. restructure
1. NEW: `pead/engines/intraday.py`; `gap_history.py`; `steady_drift`-independent strategy file `pead/strategies/equity/gap_continuation.py`; tests.
2. CHANGE: `pead/strategies/base.py` (new base class; `pead/strategies/registry.py::resolve` already filters by `asset_class`, so no change there; check `pead/reporting/comprehensive.py` for hard-coded asset_class lists before registering a new family).
3. RESTRUCTURE: REF-dsf-reader, REF-event-feature-store, REF-event-key; make `pead/core/store.py` tolerate engines that do not emit `quarterlog` (it already tolerates empty/missing quarterlog per `tests/test_fixes.py::test_load_result_tolerates_empty_or_missing_quarterlog`, so likely nothing).
4. IMPROVE: no intraday data anywhere; report this as the hard limit. The universe-level T-bill rate is typed from memory (ROADMAP #2); a strategy that is mostly cash makes the cash series matter, so do ROADMAP #2 first or quote only excess-over-cash.

## 6. Acceptance tests
- Synthetic: 3 stocks with scripted open/close paths: PnL equals notional * h1 minus costs; days with no trades have NAV growth equal to rf.
- No look-ahead: C_shrunk for an event is unchanged if all events with day0 >= its own day0 are rewritten; the entry price used is open[r+1], which is not used in the selection (assert by perturbing open[r+1]: selection identical, PnL changes).
- Capital: sum of notionals each day <= NAV (no borrowing), confirmed on a clustered-earnings-day fixture (500 events one day).
- Determinism; excluded `anntims_unknown` counted.

## 7. Experiment plan
Event study first (EQ-30): mean `h1` conditional on `gap>0`, by C_shrunk quintile, by year; only if net-of-50bp mean is positive in the search window 1996-2005 build the portfolio. Train/test: rolling PIT selection replaces Perez's one-time freeze; search 1996-2005, holdout 2006-2013 once. Compare net Sharpe/alpha against cash (rf) and against `steady_drift_plus` 12.07% / 1.19 / -22.07% (different mandate: report correlation of daily returns to see diversification). Ablations: remove best ticker and best two quarters (library test), no-continuation-score control (all positive-gap events), non-earnings gap control (same stocks, non-event days) to isolate the earnings increment.

## 8. Expected outcome / hypotheses and risks
Prior: low. Daily open-to-close continuation after a known gap, with 5-25 bp spreads and a round-trip cost likely larger than the edge, is expected to be null or negative net; the sources show nothing positive to anchor on (no figures established). The value of the spec is the falsification and the clean gap/intraday bookkeeping. Risks: opening prices in CRSP daily are noisy; Nasdaq opening quote vs trade; heavy clustering of events in a few weeks gives lumpy capital use; survivorship in the continuation score (stocks with many events); data ends 2013.

## 9. Dependencies and implementation checklist
- [ ] EQ-22 (gap, open/close features; `anntims` flag)
- [ ] EQ-26 (`qspread_prev`) for costs
- [ ] ROADMAP #2 (real T-bill) preferred
- [ ] `intraday.py` engine + base class + registry
- [ ] Event study go/no-go before portfolio work
- [ ] Mark delayed-entry variants BLOCKED (minute bars)
