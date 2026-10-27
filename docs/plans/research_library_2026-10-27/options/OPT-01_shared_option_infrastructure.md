# OPT-01: Shared option infrastructure (chains, event clock, fill model, multi-leg engine, diagnostics)

- Status: planned | Priority: P0 | Effort: L | Source(s): `foundations/04_OPTIONS_CONSTRUCTION.md`, `foundations/06_DATA_SCHEMAS_WRDS_OPTIONMETRICS.md`, `foundations/02_EVENT_TIME_PIT.md`, `audits/03_BACKTEST_FAILURE_MODES.md` section 4, `audits/05_BAILEY_SOURCE_AUDIT.md` (B3, B5); repo: `docs/FIXES.md` (fix 3), `docs/ROADMAP.md` item 9.

This is not a trading strategy. It is the set of building blocks OPT-02 to OPT-06 share. Nothing here changes a published number unless a regression test (section 6) says it is bit-identical.

## 1. Thesis & what we take from sources (and what we do NOT take)

Thesis: after the mid-fill fix (`options_cap20` 4.12% / 1.77 -> 0.28% / 0.16; `docs/FIXES.md`), every new options idea must be evaluated by one honest fill model, on chains that contain bid/ask for ALL candidate contracts (not only the one the Kelly selector picked), with explicit event timing.

Take from sources: Library 04 (retain every contract at decision time with bid, ask, IV, delta, gamma, vega, theta, OI, volume; price legs at executable quotes; reprice at exit; attribute P&L), Library 06 join invariants (`option.quote_at <= decision_at <= fill_at`), Library 02 (BMO / AMC / intraday cases, "decision_time >= release time"), Library 03 accounting assertions (cash + marks == NAV; round trip at unchanged price loses exactly the spread), Library 03 audit sections 4.6/4.7 (selection from the as-of chain; both legs must be liquid).

Do NOT take: Arn Mehta's VWAP quotes or Polygon data (we have OptionMetrics end-of-day best bid/offer only); Bailey's date-level "morning after" entry (we have close snapshots only, so every entry is "at the close snapshot", labelled so); any intraday or tick assumption.

## 2. Evidence grade & known defects in sources

Library grades: all option sources are author-reported, none re-run (Library 00_START_HERE "Important boundaries"). Bailey: net -3.80% total, 124 trades, 28.2% win rate; audit 05 finds ranking code sorts signed predictions not absolute (B1), DTE "30-45" is a preference with fallback to any DTE (B2), date-level entry versus quote clock (B3), `exit_pnl` may apply one final hedge size to the full period (B5). Arn Mehta: mid-price returns only, no net executable result, 15 entry/exit pairs per event are correlated competing strategies, not 15 tests.

Repo-internal defects that this work must not repeat (`docs/FIXES.md`, `pead/engines/options.py` docstring): mid fills; dead-quote carry-forward (22.6% of picks have no mid at 60d, measured: `kelly_fwd_mid_60d` is NaN for 22.6% of the 113,927 picks; the documented explanation is a zero bid); decile look-ahead (fixed via `pead/common/pit_rank.py`).

## 3. Exact rules (codeable)

### 3.1 Event clock (REF-event-clock)
Inputs per event from `data/event_options/decile_events_secid.parquet` (162,632 rows): `anndats`, `anntims` (string `HH:MM:SS`), `anntims_unknown`, `day0_date`, `day0_lag_days`, `permno`, `secid`, `sue_rank_pct`, `decile`.
Measured announcement-time split (all 162,632 events): 45.4% before 09:30, 11.8% between 09:30 and 16:00 (intraday), 42.9% at or after 16:00, 10.0% exactly `00:00:00` (unknown).
Definitions (trading calendar = `data/metadata/om_trading_calendar.parquet`, loaded as `OptionsUniverse.calendar`):
- `D0` = `day0_date` = first full reaction session (BMO: announcement day; AMC: next session). Verify with the assertion that for `anntims >= 16:00` D0 is the next trading day after `anndats`.
- `Dm1` = the trading day before D0. For both BMO and AMC the close of Dm1 is strictly before the surprise is public. For intraday and unknown times set `timing_clean = False`; pre-event strategies (OPT-04) exclude them; post-event strategies include them (D0 close is after any intraday release).
- `entry_session = D0 + entry_lag` with `entry_lag in {0, 1}`; snapshot is the close of that session (OptionMetrics is end-of-day). Default for new strategies: `entry_lag = 1` (conservative, mirrors the next-session control in `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md`); `entry_lag = 0` kept only to reproduce `options_cap20`.

### 3.2 Chain snapshot (REF-chain-v2)
One row per (event, snapshot_session, contract). Snapshot sessions: `Dm1`, `D0`, `D0+1`. Columns to keep from `opprcd{year}.parquet` (verified schema: secid, date, symbol, symbol_flag, exdate, last_date, cp_flag, strike_price, best_bid, best_offer, volume, open_interest, impl_volatility, delta, gamma, vega, theta, optionid, cfadj, ss_flag, root, suffix): `optionid, cp_flag, strike_price/1000, exdate, best_bid, best_offer, impl_volatility, delta, gamma, vega, theta, volume, open_interest, cfadj, ss_flag`. Note `pead/prep/options/lib/options.py::OPPRCD_COLS` currently omits gamma, vega, theta, cfadj, ss_flag. Expiry window: all expiries with 7 <= DTE <= 150 (current build keeps ONE expiry nearest 120 with DTE >= 95: `MIN_DTE = 95`, `TARGET_DTE = 120` in `scan_full_chain.py`).
Hygiene filters (applied at selection, stored as flags, not dropped): `bid > 0`, `ask >= bid`, `delta` not null, `ss_flag == 0` (standard settlement; verify the code table), strike > 0, underlying price non-null.

### 3.3 Selection helpers (pure functions, `pead/options_lib/select.py` or `pead/prep/options/lib/select.py`)
- `pick_by_delta(chain, side, target_abs_delta, dte_lo, dte_hi, max_rel_spread, min_oi, min_bid)`: filter, then minimise `|abs(delta) - target|`, tie-break highest `open_interest`, then smallest `(ask-bid)/mid`, then smallest `optionid` (determinism).
- `pick_atm_straddle(chain, dte_lo, dte_hi, max_abs_delta_gap=0.125)`: call and put at the SAME strike and expiry, strike minimising `|S - K|`, both legs pass hygiene; reject otherwise.
- `pick_vertical(chain, side, long_delta, short_delta, dte_lo, dte_hi)`: both legs same expiry, strikes ordered correctly; both legs pass hygiene.
- `rel_spread(bid, ask) = (ask - bid) / ((ask + bid) / 2)`.
All selection uses only the snapshot at or before the decision time (join invariant).

### 3.4 Implied move and realized move (feeds OPT-04 and as a regime feature elsewhere)
- `implied_move(chain_Dm1, spot_Dm1)`: choose the first expiry with `exdate >= D0` and DTE in [7, 45] (earnings-containing expiry); ATM straddle mid / spot. Also report `straddle_ask_over_spot` (executable).
- `reaction_move = close(D0)/close(Dm1) - 1` from CRSP raw returns (`EquityUniverse.ret_raw` / `panel_date`, as loaded by `pead/core/data.py::load_equity_universe`).
- `hist_abs_move(permno, event)` = mean of `abs(reaction_move)` over that permno's previous up to 8 events whose D0 < this event's Dm1 (strictly earlier), minimum 4. `move_ratio = implied_move / hist_abs_move`.
- IV-crush measure: `crush = atm_iv(D0) / atm_iv(Dm1)` on the same expiry (ATM = |delta| closest to 0.5, average of call and put as in `pead/prep/options/event_vol_features.py`). Pure descriptive variable until a strategy uses it.

### 3.5 Fill model (REF-fill-model)
`pead/engines/fills.py`:
- `leg_fill_price(side, bid, ask, spread_mult=1.0, mode="bid_ask")`: `side=+1` (buy) pays `mid + spread_mult*(ask-mid)`; `side=-1` (sell) receives `mid - spread_mult*(mid-bid)`; `mode="mid"` returns mid (legacy only). With `spread_mult=1` this equals ask in / bid out exactly.
- `dead_quote_mark(bid, ask, mode)`: same three modes already in `OptionsSizing.dead_quote_mark` (`zero_bid_half_ask`, `zero_bid_zero`, `carry_last`). For a SHORT leg a zero bid means the buy-back is at the ask, never half-ask (asymmetry: the mark for a liability must be the cost to close).
- Structure net: `net_debit = sum(side_i * qty_i * leg_fill_price_i)` per share; crossing-the-spread cost reported separately: `spread_cost = sum(qty_i * (fill_i - mid_i) * side_i)`.
- Fees: `commission_per_contract` default `0.0`; sensitivity run at `0.65` (an ASSUMPTION about a typical retail schedule, not a number from the repo). Stress: `spread_mult in {1.0, 1.5, 2.0}`.
- Locked/crossed (`ask < bid`) or `ask == 0`: leg unfillable -> structure rejected (counted in a `n_rejected_by_reason` dict).

### 3.6 Multi-leg engine (REF-leg-engine)
New `pead/engines/options_structures.py::run_structure_backtest(legs, events, calendar, sizing, initial_capital, eligible=None)`.
Inputs: `events` (one row per structure: `event_id, entry_cal_idx, weight, structure_id`), `legs` (long table: `event_id, leg_idx, side, qty_ratio, optionid, cp_flag, strike, exdate, multiplier=100`), `path` (REF-path-quotes: `event_id, leg_idx, h, bid, ask, mid`), and an exit table `exit_h` per structure from REF-exit-rules.
Accounting: daily mark-to-mid between entry and exit; entry fills at `leg_fill_price`, exit fills at `leg_fill_price` of the exit-session quotes; P&L telescopes exactly (entry fill -> mids -> exit fill), the same property the single-leg engine documents. Daily series columns identical to `run_options_backtest` (`date, daily_pnl, nav, daily_return`) plus per-structure-type P&L columns. Sizing modes: `premium_budget` (as existing: `max_premium_frac` x trailing NAV minus outstanding premium; debit structures), `max_loss_budget` (defined-risk: max loss per structure x lots <= budget), `collateral_budget` (cash-secured shorts: strike x 100 x lots). All three share the extracted allocator `_allocate_quarter_budget(...)` (REF-leg-engine restructures it out of `run_options_backtest`).
Expiry/exercise: if `exit_h` is beyond expiry, force exit at the last quote before expiry; European index legs (OPT-06) settle at intrinsic on the settlement session (REF-assignment).

### 3.7 Liquidity / spread diagnostics (REF-spread-diag)
`pead/reporting/option_spreads.py::spread_report(chain)` -> table of `rel_spread` and `(ask-bid)/spot` by year, delta bucket, DTE bucket, SUE decile, price bucket and OI bucket; fraction of events with at least one contract passing the standard filters. This is the gating artefact: no strategy is run before its spread table exists.

## 4. Data requirements & availability in THIS repo

| Need | Status (verified) |
|---|---|
| Raw OptionMetrics quotes 1996-2013 | `D:\OptionMetrics\parquet\opprcd{1996..2013}.parquet` (no 2011 file: `opprcd2011` is corrupt per `pead/prep/options/lib/options.py` docstring; the directory listing has no `opprcd2011.parquet`). `opprcd2005` has 42,279,728 rows and the columns listed in 3.2. |
| Chain with bid/ask for all strikes | MISSING. `data/event_options/full_chain_with_underlying.parquet` (1,520,092 rows, 124,686 events) has `entry_mid`, `entry_volume`, `entry_oi`, `delta`, `iv`, `underlying_price` but NO bid/ask/gamma/vega/theta. |
| Expiry coverage | ONE expiry per event, DTE >= 95 (measured: min 95, median 144, max 933). No short-dated expiries. |
| Pre-event (Dm1) chain | MISSING (only day0 chains exist). Buildable from the same `opprcd` files with `scan_year_for_keys`. |
| Forward quotes | Only for the Kelly and naive picks: `optimal_forward_prices.parquet` (113,927 rows) has `kelly_fwd_{mid,bid,ask}_{0,1,5,10,20,40,60}d` and `naive_fwd_*`. |
| Underlying price | `underlying_price` at day0 only (`attach_underlying_price.py`). Need Dm1 and D0+1: from CRSP (`EquityUniverse.ret_raw` / price panel) or `secprd{year}`. |
| Day-0 relative spread, measured | Kelly picks, day 0: median 11.9%, mean 29.6%, 25th pct 5.7%, 75th pct 40.0%, 90th pct 88.5% (computed from `kelly_fwd_ask_0d`, `kelly_fwd_bid_0d`, `kelly_fwd_mid_0d`). At the 60-day checkpoint median 9.1% among the 77.4% with a quote. These contracts are selector-chosen (OTM-leaning), NOT representative of ATM or ITM; ATM/ITM spreads are unmeasured and must come out of `spread_report`. |
| Liquidity | Chain medians: `entry_volume` = 0, `entry_oi` = 50 (all contracts on the kept expiry). |
| Cash rate | `pead/core/rates.py::TBILL_PCT` is typed-from-memory annual averages (documented ASSUMPTION; ROADMAP item 2). |
| Dividends / early exercise data | Not in the extract used so far (`idxdvd` is index dividends). Single-name dividend table absent; American early exercise cannot be modelled precisely. |
| Index options | SPX (secid 108105) rows exist in `opprcd` (checked: 628 rows, 8 expiries on 2005-06-15). |

## 5. Framework mapping

New files (see 5b for the full list):
- `pead/prep/options/event_clock.py` (stage `event_clock`) -> `data/event_options/event_clock.parquet`.
- `pead/prep/options/chain_snapshots.py` (stage `chain_snapshots`) -> `data/event_options/chain_snap_{year}.parquet` + combined.
- `pead/prep/options/forward_quotes.py` (stage `forward_quotes`) -> `data/event_options/path_quotes_<tag>.parquet`.
- `pead/prep/options/event_move_features.py` (stage `event_move_features`) -> implied/realized/crush.
- `pead/engines/fills.py`, `pead/engines/options_structures.py`, `pead/engines/exits.py`.
- `pead/reporting/option_spreads.py`.
Registration: stages are discovered by `pead.prep.stages` (verify how `python -m pead prep --list` enumerates; add entries there). No strategy registered by OPT-01.
Reuse: `scan_year_for_keys`, `pick_full_chain_vectorized` (`pead/prep/options/lib/options.py`); `OptionsSizing`, `CONTRACT_MULTIPLIER`, `CHECKPOINTS` (`pead/engines/options.py`); `summarize` (`pead/core/metrics.py`); `OptionsUniverse` (`pead/core/data.py`); `register_instance` (`pead/strategies/registry.py`); `daily_rf` (`pead/core/rates.py`).

## 5b. Build vs. change vs. restructure

1. NEW
   - `pead/prep/options/event_clock.py` (REF-event-clock) and its table.
   - `pead/prep/options/chain_snapshots.py` (REF-chain-v2): multi-expiry, Dm1/D0/D0+1, all strikes, bid/ask + greeks + OI + volume, flags not drops.
   - `pead/prep/options/forward_quotes.py` (REF-path-quotes): arbitrary contract list x checkpoint grid -> long table.
   - `pead/prep/options/event_move_features.py`: implied move, hist move, crush.
   - `pead/engines/fills.py` (REF-fill-model), `pead/engines/options_structures.py` (REF-leg-engine), `pead/engines/exits.py` (REF-exit-rules), `pead/reporting/option_spreads.py` (REF-spread-diag).
   - `tests/test_options_infra.py`.
2. CHANGE
   - `pead/prep/options/lib/options.py::OPPRCD_COLS` add `gamma, vega, theta, cfadj, ss_flag`; regression risk none for old stages if they pass explicit `columns=` (check `forward_prices_optimal.py`, `select_entry_contracts.py` call `scan_year_for_keys(year, keys)` with the default, so extra columns increase memory but not outputs; keep a second constant `OPPRCD_COLS_V2` to avoid touching them).
   - `pead/engines/options.py::run_options_backtest`: replace the inline bid/ask/dead-quote code with calls to `fills.py` (REF-fill-model) and accept `prefix="kelly"` (REF-engine-prefix) replacing hard-coded `kelly_growth / kelly_premium / kelly_fraction / kelly_side / kelly_fwd_*`. Regression risk: HIGH if done carelessly; guard with the NAV pins in `tests/test_framework.py` (`options_cap20` 10,481,798.4; `options_cap30` 9,841,406.8) and `tests/test_fixes.py::test_bid_ask_fill_pnl_is_exactly_bid_minus_ask`.
   - `pead/core/data.py::load_options_universe`: add optional loaders for chain snapshots and path quotes (lazy, so existing runs do not need them).
3. RESTRUCTURE
   - Extract `_allocate_quarter_budget` (the per-quarter loop body in `run_options_backtest` computing `outstanding`, `budget`, `target`, `lots`) into a function shared by single-leg and multi-leg engines (REF-leg-engine).
   - Fill-model abstraction (REF-fill-model) so engines never hand-code bid/ask logic again.
   - Options sleeve into `pead/strategies/portfolio/sleeves.py`: `PortfolioStrategy.run` already blends any registered strategy by daily return, so options-only strategies plug in by name with no change; overlay strategies need REF-overlay (see OPT-03 and OPT-06).
4. IMPROVE
   - IMP-coverage: 2011 absent (corrupt `opprcd2011`); picks start 1998 (no 1996-97 events with qualifying DTE >= 95 chains); document and keep gaps as explicit NaN, never interpolate (also required by `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md` validation list).
   - IMP-rates: replace `TBILL_PCT` with a real series (ROADMAP item 2) before any cash-yield-sensitive option result (OPT-03, OPT-05) is quoted.
   - IMP-live-fill: `pead/live/broker_options_sim.py` simulates fills at "mid minus half" with the strategy's flat cost; align with `fills.py` before any live use of a new strategy.
   - IMP-tests: the options tests only cover one-leg fills; add property tests listed in section 6.

## 6. Acceptance tests

1. Regression (bit-identical): after REF-fill-model and REF-engine-prefix, `options_cap20`, `options_cap30`, `options_worst_case`, `options_cap20_midfill`, `options_cap20_legacy` final NAV equals pre-refactor to the cent (`tests/test_framework.py` pins; add `options_worst_case` and `options_cap20_midfill` pins).
2. Fill model: buy then sell the same quotes loses exactly `(ask-bid)*100*lots`; `mode="mid"` loses 0; `spread_mult=2` doubles the loss; short leg dead quote closes at ask.
3. Unchanged-price round trip of any structure on a synthetic chain loses exactly the summed leg spreads (Library 03 assertion 5); zero-spread chain gives 0 P&L.
4. Payoff tests on synthetic chains: long call, call debit spread, long straddle, cash-secured risk reversal, put debit spread; terminal P&L at expiry equals analytic payoff minus debit, for S_T on a grid including K.
5. No look-ahead: perturbing any quote with `date > snapshot_session` leaves selection and entry fill unchanged (property test over random chains); `hist_abs_move` unchanged when events with D0 >= the event's Dm1 are altered.
6. Event clock fixtures (Library 02): Friday 16:20 release with Monday holiday -> D0 = Tuesday; 15:59 vs 16:05; unknown `00:00:00` flagged `timing_clean=False`.
7. Determinism: two runs of each prep stage and engine give identical parquet hashes; selection tie-breaks fully ordered.
8. Accounting: `sum(daily_pnl) == sum(structure net_pnl)`; no position outstanding after the last calendar day; outstanding premium never exceeds `max_premium_frac * trailing NAV`.
9. Coverage assertions: 2011 sessions produce no entries; events lacking a Dm1 snapshot are counted, not silently dropped.

## 7. Experiment plan

Diagnostics only (no P&L claims): (a) `spread_report` by year, delta, DTE, decile, OI; (b) fraction of events with a qualifying single contract / vertical / straddle under each filter set; (c) quote-present rate at +1/5/10/20/40/60 sessions per delta bucket (the picks' 22.6% missing-at-60d is likely worst for OTM; ITM may be much better, UNMEASURED); (d) implied-vs-historical-move scatter (descriptive). Search window = first 70% of announcement quarters, using the same rule as `PEAD_DECAY_SEARCH_FRAC` in `pead/prep/equity/daily_decay.py` (REF-search-holdout: one helper `search_mask(quarters)` in `pead/common/` used by every OPT spec); holdout = the rest, opened once per strategy.

## 8. Expected outcome, honest risks

Expected: no alpha; a trustworthy yardstick. Risks: (i) scan cost of the new chain build (the current stages stream 131 files / 32.9 GB off an external drive that was unplugged twice on 2026-10-20; consider the open question in `docs/REPO_MAP.md` about copying to `C:`); (ii) end-of-day snapshots cannot tell what fill was available at the close; (iii) data ends 2013, so every number is partly regime-specific (pre-weeklies, pre-retail options boom); (iv) no single-name dividend or borrow data, so early exercise and any stock-hedge borrow are approximations; (v) disk size of an all-strikes multi-expiry multi-session chain for 124,686 events could be large; restrict to delta in [0.1, 0.9] and the three snapshot sessions.

## 9. Dependencies and implementation checklist

Order is in `_ENABLING_CHANGES.md`.
- [ ] REF-search-holdout helper + test
- [ ] REF-fill-model extracted; regression NAVs identical
- [ ] REF-engine-prefix; regression NAVs identical
- [ ] REF-event-clock stage + fixtures
- [ ] REF-chain-v2 stage (run on 2 sample years first, then all)
- [ ] REF-spread-diag report generated and reviewed (gate for OPT-02..06)
- [ ] REF-path-quotes stage
- [ ] REF-exit-rules module
- [ ] REF-leg-engine + budget allocator extraction
- [ ] REF-overlay interface (OPT-03, OPT-06)
- [ ] REF-assignment rules (OPT-05, OPT-06)
- [ ] `tests/test_options_infra.py` passing
