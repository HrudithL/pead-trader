# OPT-04: Pre-earnings long straddle gated by implied move versus the firm's own history of earnings moves

- Status: planned | Priority: P1 | Effort: L | Source(s): `strategies/10_ARN_MEHTA_STRADDLES.md` (screens F1-F7, same-contract entry/exit, T-3..T0 entry grid), `foundations/04_OPTIONS_CONSTRUCTION.md` (straddle, IV crush, "never claim a straddle is profitable simply because an earnings gap was large"), `strategies/09_BAILEY_MECHE_OPTIONS.md` conceptual lesson (need realized-vs-implied, not just |move|), `audits/03_BACKTEST_FAILURE_MODES.md` section 4, `foundations/02_EVENT_TIME_PIT.md`; Gao, Xing & Zhang (2018) is cited by the library as inspiration only and is NOT verified here.

## 1. Thesis & what we take from sources (and what we do NOT take)

Thesis: the market prices the earnings gap in the front-month straddle. If, for a given firm, that price is low relative to how far the stock has actually moved on its previous earnings days, the long straddle bought the close before the release (Dm1) and sold at the reaction-day close (D0) has positive expected value gross of costs; if it exceeds the move, it does not. This is a volatility-event strategy, independent of the SUE signal (SUE is unknown at Dm1) and uncorrelated in principle with the drift books. It is the only idea here that does not depend on the 60-day drift.

Take: Arn Mehta's contract-screen (premium >= $0.125, price >= $5, DTE 10-60, |delta| 0.375-0.625, strike/spot 0.9-1.1, same contract pinned at entry and exit) and the executable form `100 x [(C_bid,b + P_bid,b) - (C_ask,a + P_ask,a)]`. Library 04 attribution discipline.
Do NOT take: mid-price returns as the headline (the source reports mid only); the 15-pair entry/exit grid as 15 independent tests (we use ONE pre-registered pair: entry Dm1 close, exit D0 close); VWAP quotes; S&P-500-only 2016-2025 conclusions (different era and liquidity); any short-volatility or "composite" claim (Library 10 states the composite is a new proposal not attributable to the author).

## 2. Evidence grade & known defects in sources

Arn Mehta: README-only, no net result stated, 1,522 earnings events / 63,021 option rows, Polygon data, VWAP entry quote availability issue flagged by the library itself; academic basis not validated in the library. Treat as UNVERIFIED hypothesis. Known structural issue: a long straddle bought before earnings pays the IV run-up and then suffers the IV crush; the crush is large relative to typical stock moves, so profit requires realized move to exceed the implied move plus ~one full straddle spread. Our data cannot observe intraday run-up; we hold exactly one session so run-up is irrelevant (Dm1 close to D0 close).
Repo measure: median day-0 `atm_iv` = 0.412 and median `iv_rv` = 1.07 (`event_vol_features.parquet`, 124,686 events) - the 120-DTE ATM implied vol after the announcement is on average slightly above trailing realized vol, i.e. no cheap vol on average (descriptive, post-event, long expiry; not the pre-event front-month implied move).

## 3. Exact rules

Event set: events with `timing_clean = True` (OPT-01 3.1): `anntims` before 09:30 or at/after 16:00 and not `00:00:00`. Measured: 45.4% + 42.9% = 88.3% of events have a time, of which the intraday 11.8% and the unknown 10.0% are excluded, leaving about 78% of the 162,632 events before options-availability filters.
Entry snapshot: close of `Dm1`. Exit snapshot: close of `D0` (hold one session). Exit price: bid of both legs at D0 close.
Contract (REF-chain-v2 Dm1 chain): single expiry `X = first listed expiry with exdate >= D0 + 1 trading day` (so it contains the announcement) with DTE (from Dm1) in [7, 45]; strike K minimising `|S_Dm1 - K|`; call and put both exist at (K, X); filters: `ask(C), ask(P) > 0`, `bid > 0` both legs, `C_ask + P_ask >= 0.50` (Arn Mehta's single-option $0.125 floor applied to the pair would be too lax for a two-leg spread), `S_Dm1 >= 5`, strike/spot in [0.9, 1.1], `|delta|` of each leg in [0.375, 0.625], `rel_spread(straddle) <= max_straddle_spread` in {0.06, 0.10} where straddle spread = `(C_ask + P_ask - C_bid - P_bid) / ((C_ask + P_ask + C_bid + P_bid)/2)`, `open_interest(C), open_interest(P) >= 100`.
Signal (PIT): `implied_move = (C_mid + P_mid)/S_Dm1` (OPT-01 3.4); `hist_abs_move` = mean |reaction move| over the permno's previous up to 8 earnings events, min 4, all with D0 strictly before this event's Dm1; `move_ratio = implied_move / hist_abs_move`.
Trade if `move_ratio <= theta` with `theta` in {0.70, 0.80, 0.90} (grid on search window). Baseline cell for comparison: trade every filtered event (ratio unconstrained) - that is the "unconditional straddle" the library asks for. Optional second gate (ablation): `crush_prior` = median of the permno's previous `atm_iv(D0)/atm_iv(Dm1)` over the same prior events (requires Dm1 and D0 chains for historical events; same builds).
Sizing: equal premium per trade, `max_premium_frac` of trailing NAV outstanding (hold is one session so outstanding is small), per-trade cap 0.5% of NAV, per-day cap 10% of NAV across all straddles (many names report the same days; peak days cluster), whole lots. Alternative sizing for ablation: equal vega.
Fill: ask in, bid out for four legs-prices (two legs entry, two legs exit): `net = 100 * [(C_bid_D0 + P_bid_D0) - (C_ask_Dm1 + P_ask_Dm1)]`; `spread_mult` 1.0 / 1.5 / 2.0; commission 0 and 0.65/contract (x4 transactions per straddle) sensitivity.
Exercise/assignment: not applicable (positions closed before expiry; if X expires D0 or D0+1 the event is dropped: require `X >= D0 + 2 trading days`).
Delta-hedge: none (ablation only if the expected move-ratio edge appears; stock hedge needs a single overnight share trade at the Dm1 close which equals a stock position taken at the same time as the entry: delta of straddle ~0 at ATM so skip).

Cost-to-edge (derived, NOT measured): breakeven needs `E|reaction| - theta_effects >= implied_move + straddle spread`. For a front-month ATM straddle the premium is on the order of `0.8 x S x sigma x sqrt(T)`; spread unknown and must be measured. If the straddle spread is ~6% of premium and the implied move is ~6% of spot, the spread alone costs ~0.36% of spot, i.e. a realized-minus-implied gap of ~0.4% of spot before any edge. The edge available is the dispersion of `move_ratio`: if the signal moves the mean ratio of realized-to-implied from ~1.0 to ~1.2 in the lowest-ratio bucket, a gain of ~20% of premium would clear a 6% spread. Whether such a bucket exists is exactly the experiment. Survival after ask-in/bid-out: genuinely open; of all ideas here this one has the best ratio of potential edge per spread-dollar because it trades only once and holds one session, but ATM short-dated spreads for non-S&P-500 names in 1998-2010 may be wide.

## 4. Data requirements & availability in THIS repo

Missing (all must be built): Dm1 chains with short expiries (current chain: ONE expiry, DTE >= 95); D0 chains for the same contracts (the existing `full_chain_with_underlying` at D0 has DTE >= 95 only, so it does NOT contain the pre-event expiry); prior-event reaction moves (derivable from CRSP daily returns in `EquityUniverse.ret_raw`, the `panel_date` array and `calendar`). Underlying price at Dm1 (CRSP close or `secprd`). Announcement time of day: have `anntims` (10.0% unknown, 11.8% intraday).
Quality caveats: OptionMetrics `best_bid/best_offer` are end-of-day; the Dm1 close ask is a legitimate fill assumption only as "at the closing snapshot"; the pre-announcement quote for an AMC reporter at the 16:00 close is before the release, fine; for BMO reporters, Dm1 close is the prior evening, fine.
Events: 124,686 events have a day-0 chain today; coverage for Dm1 chains is unknown until built (a name may have no listed 7-45 DTE expiry containing the announcement in 1998-2002). 2011 absent.

## 5. Framework mapping

- New prep: `pead/prep/options/straddle_candidates.py` (stage `straddle_candidates`): from `chain_snapshots` (Dm1 and D0, short expiries) build one row per event: `event_id, X, K, call_optionid, put_optionid, ask/bid/mid at Dm1 for both legs, bid/ask/mid at D0, S_Dm1, S_D0, implied_move, straddle_spread, oi, delta_c, delta_p`; plus `data/event_options/event_move_features.parquet` (`hist_abs_move`, `move_ratio`, `crush_prior`, `n_hist`).
- New strategy: `pead/strategies/options/earnings_straddle.py`, `class EarningsStraddle(OptionsStrategy)`, `family = "options-eventvol"`, registered `options_straddle_all` (unconditional baseline), `options_straddle_r80` (headline guess; final theta chosen on the search window), `options_straddle_r70`, `options_straddle_r90`, `options_straddle_mid` (reference: mid fills).
- Engine: use REF-leg-engine `run_structure_backtest` with a 1-session checkpoint list; because hold is one session, a degenerate-case fast path is acceptable: P&L per straddle = `lots*100*(exit_bid_sum - entry_ask_sum)`, daily series at D0. Alternative without REF-leg-engine: treat the straddle as one synthetic "option" with `ask = C_ask + P_ask`, `bid = C_bid + P_bid`, `mid`, and call `run_options_backtest(prefix="straddle", hold_horizon=1)`, since the existing engine only needs one entry ask, interim mids and a final bid, and `CHECKPOINTS = [1,...]` already contains horizon 1. This is the S-effort path and is recommended for the first pass; it requires `prefix` support (REF-engine-prefix), `side` column set to `"S"` (engine uses `kelly_side == "C"` only for the call/put P&L split) and the 100x multiplier (correct for both legs). Set the panel's `day0_date` field to `Dm1` (the engine enters at `day0_date` and its horizon-1 checkpoint is then the D0 close).
- Params dict: `dict(dte_lo, dte_hi, theta, max_straddle_spread, oi_min, min_premium, hist_n_max, hist_n_min, per_trade_cap, per_day_cap, spread_mult, commission)`.
- Reuse: `scan_year_for_keys`, `OptionsSizing`, `run_options_backtest`, `event_total_vol` (`pead/common/volatility.py`) for trailing vol context, `event_vol_features.py` for ATM-IV selection idea (delta closest to +/-0.5, average).

## 5b. Build vs. change vs. restructure

1. NEW: `pead/prep/options/event_clock.py` and `chain_snapshots.py` (OPT-01, Dm1 and D0 sessions with DTE 7-45); `straddle_candidates.py`; `event_move_features.py`; `pead/strategies/options/earnings_straddle.py`; `tests/test_straddle.py`.
2. CHANGE: `pead/engines/options.py::run_options_backtest` - `prefix` argument (REF-engine-prefix) and call/put split column made optional (`pnl_call/pnl_put` become `pnl_by_side` keyed by a side column); regression risk: pinned NAVs of the eight existing options strategies; `pead/prep/options/lib/options.py` default columns (see OPT-01).
3. RESTRUCTURE: REF-event-clock (new table keyed by `event_id`; `event_id` exists in `optimal_forward_prices` but NOT in `decile_events_secid`; define a stable `event_id = f"{secid}_{day0_date:%Y%m%d}"` and assert uniqueness; note `n_events_same_permno_day0` column exists in the events table for duplicates); REF-leg-engine only if per-leg attribution is wanted.
4. IMPROVE: duplicate / multiple-announcement handling per (permno, day) (events table column `n_events_same_permno_day0`); realised-move history needs delisting-safe returns (CRSP `ret_raw` includes delistings? VERIFY before use); report must show per-quarter clustering (many events on the same few days); IV-crush descriptive report.

## 6. Acceptance tests

- Clock fixtures: AMC Friday with Monday holiday; BMO Monday; intraday and unknown excluded; `Dm1` strictly before D0 in all cases and `Dm1` close precedes the release in both BMO and AMC.
- No look-ahead: `hist_abs_move` unchanged when any event with D0 >= this Dm1 is perturbed (property test); `S_Dm1` and all quotes are Dm1-dated; expiry choice depends only on D0 date (known from the calendar and `anndats`, which is a schedule fact, but flag that real-time knowledge of the report date requires the date to be announced before Dm1; `anndats` in IBES is the actual date; mild look-ahead risk if the date was only set later - quantify by requiring the announcement date to appear within the IBES `anndats` record... VERIFY IBES field for expected date; if not available, document the risk).
- Payoff test on synthetic chain: unchanged price and spreads -> loss equals summed four half-spreads; S moves +/-implied move -> gain equals analytic straddle change with fixed IV.
- Pinned-contract test: exit uses the same two `optionid`s as entry.
- Fill: `spread_mult=2` doubles the spread cost; missing/zero exit bid -> dead-quote mark (a zero bid on a leg liquidates at 0, `zero_bid_zero`).
- Determinism, event conservation, per-day cap enforced.

## 7. Experiment plan

1. Descriptive first (no strategy): distribution of `move_ratio`, `reaction/implied`, `crush`, by year, size, and straddle spread; show unconditional mean `straddle_bid_D0/straddle_ask_Dm1 - 1`. Gate: if the unconditional mean net return is below -(straddle spread median) AND the lowest `move_ratio` quintile does not exceed the highest by a material margin on the search window, stop.
2. Baselines: `options_straddle_all` (no gate) and the repo options baselines (`options_cap20/30/worst_case`) for context.
3. Search window (first 70% of quarters): theta in {0.70, 0.80, 0.90}, `max_straddle_spread` in {0.06, 0.10}, `hist_n_min` in {4, 6}; quintile analysis of `move_ratio` as a monotonicity check; log all trials.
4. Freeze, open holdout once; stresses `spread_mult` 1.5/2.0 and commissions.
5. Placebos: random shuffle of `move_ratio` within quarter; randomized historical window (use events from the wrong firm).
6. Attribution: gamma (realized move), vega (IV crush), theta, spread - requires gamma/vega/theta now kept (REF-chain-v2).
7. Blend: `PortfolioStrategy` with `steady_drift_plus` (weight chosen by per-day capital use), report correlation (expected near zero) and marginal Sharpe via paired bootstrap.

## 8. Expected outcome, honest risks

Expected: unconditional straddle is negative after spreads; a gated version may be near zero to modestly positive, with large day-clustering and fat tails (a straddle is long convexity so losses are capped at premium, but the typical outcome is a small loss and a few large wins; win rate likely below 50%, which the library notes for Bailey's similar pattern). Risks: wide ATM spreads in 1998-2005; implied move estimated from a single EOD mid; stale quotes at Dm1 close for illiquid names (OI filter); event-date knowledge (see tests); selection of theta; heavy overlap of events on a few reporting days in a quarter (cap per day); 2008-2009 vol regime; data ends 2013 pre-weekly options so pre-event expiry choice differs from today; short-dated options near expiry have large gamma and American early exercise around ex-dividend dates; no stock hedge or borrow needed (a plus).

## 9. Dependencies and implementation checklist

Depends: REF-event-clock, REF-chain-v2 (Dm1 + D0, DTE 7-45), REF-engine-prefix, REF-fill-model, REF-search-holdout, REF-spread-diag; optional REF-leg-engine.
- [ ] event clock with `timing_clean`
- [ ] Dm1/D0 short-expiry chain build (2 sample years first)
- [ ] spread report for ATM straddles by year; gate on median straddle spread
- [ ] `move_ratio` features + PIT property test
- [ ] synthetic-straddle path through `run_options_backtest(prefix="straddle", hold_horizon=1)`
- [ ] descriptive study; go/no-go
- [ ] strategy + registration; search/holdout; stress; placebos; blend
