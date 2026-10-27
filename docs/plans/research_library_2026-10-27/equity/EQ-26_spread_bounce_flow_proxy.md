# EQ-26: Quoted-spread cost model, entry-price bounce correction, and a daily order-flow-asymmetry proxy (degraded version of the UK OFI study)
- Status: planned | Priority: P1 | Effort: M | Source(s): library `strategies/24_UK_ORDER_FLOW_BID_ASK_ASYMMETRY.md` (Zhang, Gregoriou, Wu, IRFA 94, art. 103316, 2024; https://www.sciencedirect.com/science/article/abs/pii/S1057521924002485), `strategies/07_MCRAVI8_WRDS_MINI.md` (cost units, turnover), `foundations/02_EVENT_TIME_PIT.md`, `comparisons/03_EVIDENCE_GRADING_AND_COMPARABLE_BACKTESTS.md` (5/15/30 bps grid)

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
The UK paper's message for us: part of measured PEAD can be a bid/ask-bounce and order-flow artefact of last-trade prices, and returns computed on trade prices overstate what is executable. Our engine buys at the day0 CLOSING TRADE price (`prc`, which for CRSP can be a trade at the bid or the ask, or a bid/ask average when negative) and charges a flat per-liquidity-quintile cost (`pead/engines/equity.py::COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}`, charged at entry and again at exit). Take: (1) measure how large the entry bounce bias is; (2) replace the flat cost with a quoted-spread-based cost as a sensitivity; (3) test whether a day-0 closing order-pressure proxy predicts post-entry returns. Do NOT take: Lee-Ready tick classification, TAQ trade/quote data, UK FTSE-350 results, the claim that good-news drift is weaker than bad-news drift (we are long-only on the good side; that claim only warns that our paid leg may be the thinner one, already visible as the short leg netting -4.4 pts in docs/STEADY_DRIFT.md).

## 2. Evidence grade & known defects in the source
Library grade D/E: abstract and highlights only; UK sample 2000-2021, licensed microstructure data, no executable rule, no performance number. OFI formula in the library is explicitly "a proposed implementation, not confirmed to be the paper's measure". Repo-side limits: CRSP daily gives only closing `bid`/`ask` (98.4% non-null in sampled years), no volume at bid/ask, `numtrd` only ~46-51% non-null (Nasdaq-era), and CRSP closing quotes can be stale for illiquid names.

## 3. Exact rules
Per event, using CRSP dsf row at r-1 (spread known before day0) and row r (day0 close):
- `qspread_prev = (ask[r-1] - bid[r-1]) / ((ask[r-1] + bid[r-1]) / 2)`; NaN if bid<=0, ask<=bid, or `qspread_prev > 0.5`.
- `entry_bounce_bps = 1e4 * (mid[r] - |prc[r]|) / mid[r]` where mid = (bid+ask)/2 on day0; positive means the entry price was below mid (entry looks cheap). Executable long entry is the ask: `entry_slip_bps = 1e4 * (ask[r] - |prc[r]|) / |prc[r]|`, clipped at [0, 1000].
- `close_loc = (|prc[r]| - mid[r]) / (ask[r] - bid[r])` in [-0.5, 0.5] typically; +0.5 = closing trade at the ask (buy pressure), -0.5 = at the bid. This is the daily degraded OFI proxy; sign convention documented, never used with information after r.
- `cost_bps_event = max(COST_BPS[liq], a * 1e4 * qspread_prev / 2 + c)` with a = 1.0 (half-spread) and c = 2.0 bps impact floor (proposed constants, grid a in {0.5, 1, 1.5}); alternative pure-spread model `cost_bps_event = 1e4*qspread_prev/2 + c`. Cost is charged at entry and exit as today.
Strategy rules (derived from `steady_drift_plus`, long-only, unchanged signal and sizing):
- `spreadcost`: same book, engine uses `cost_bps_event` instead of the quintile table (sensitivity; this is a reporting variant: is the edge robust to realistic spreads?).
- `spreadfilter`: additionally require `qspread_prev <= x`, x in {0.02, 0.04}.
- `flowtilt`: weight x (1 + 0.5 * clip(close_loc / 0.5, -1, 1)) normalised to unchanged mean weight in the mask (tests whether closing buy pressure on the signal day is a positive or negative predictor; sign not assumed).
- `entry_ask`: subtract `entry_slip_bps` once at entry per position (engine hook `extra_entry_bps`, see section 5).

## 4. Data requirements & availability in THIS repo
Available: `bid`, `ask`, `prc`, `openprc`, `numtrd`, `vol` in `data/raw_wrds/crsp/dsf_YYYY.parquet` (verified columns). Missing: trade-by-trade signed volume, NBBO midpoint series intraday (BLOCKED: needs TAQ/millisecond feed from 1996-2013 with the exact release timestamps; exact missing input: consolidated trades and quotes plus release timestamps). Options-spread analysis in the UK note is options territory (other workstream; the options book already pays real bid/ask, docs/FIXES.md fix 3).

## 5. Framework mapping
- Prep stage `quote_features` (`pead/prep/equity/quote_features.py`) -> `event_features/quote.parquet` with `qspread_prev, entry_bounce_bps, entry_slip_bps, close_loc, cost_bps_event`; uses REF-dsf-reader.
- Strategy file `pead/strategies/equity/steady_drift_quotes.py`: registered `steady_drift_plus_spreadcost`, `_spreadfilter`, `_flowtilt`, `_entry_ask`. New `Sizing` field `cost_source: str = "liquidity_quintile"` (other value `"event_column"`) and `cost_column: str = "cost_bps_event"`; plus `extra_entry_bps_column: str = None`.
- Engine change (REF-cost-source): in `pead/engines/equity.py::run_equity_backtest` replace `cost_bps = liq.map(COST_BPS)...` (line ~169) by a helper `event_cost_bps(pos, sizing)`; `entry_costs = np.abs(notional) * cost_bps / 1e4` currently applies the same bps at entry and exit (lines ~229-232 add `-entry_costs` at both entry and exit calendar days), so the extra-entry term needs its own array.
- Params defaults: `half_spread_mult=1.0`, `impact_floor_bps=2.0`, `max_qspread=None`, `flow_lambda=0.5`.

## 5b. Build vs. change vs. restructure
1. NEW: `quote_features.py`; `steady_drift_quotes.py`; `tests/test_quote_features.py`, `tests/test_cost_source.py`.
2. CHANGE: `pead/engines/equity.py::Sizing` (add 3 defaulted fields; frozen dataclass so positional construction elsewhere is safe only if added last: verify no positional use in `pead/strategies/*`), `run_equity_backtest` cost lines (regression risk: all 59 strategies run through here; default path must be bit-identical, pinned by `tests/test_fixes.py::test_default_vintage_pins_corrected_final_nav` and `tests/test_framework.py`); `pead/strategies/base.py::EquityStrategy.params()` include new Sizing fields (report meta changes only).
3. RESTRUCTURE: REF-cost-source (cost model injected, not a module-level dict); REF-dsf-reader; REF-event-feature-store.
4. IMPROVE: the flat cost table has no documented derivation, and docs/STEADY_DRIFT.md only tests 2x and 3x costs (10.5% and 10.1%); this spec adds a spread-grounded version plus the library's 5/15/30 bp grid (library `03_...`) as reporting columns. The entry fill at the closing trade, not the ask, is an optimistic convention shared by every equity strategy; quantify, do not silently change defaults.

## 6. Acceptance tests
- Synthetic: scripted bid/ask/prc rows give exact `qspread_prev`, `entry_bounce_bps`, `close_loc` in [-0.5, 0.5]; bid <= 0 and crossed quotes give NaN.
- No look-ahead: spread uses row r-1 only; `close_loc` uses row r only; perturb rows > r, assert unchanged.
- Engine: with `cost_source="liquidity_quintile"` the NAV of `steady_drift_plus` is bit-identical; with a constant-cost event column equal to `COST_BPS[liq]` the NAV is identical (proves the plumbing); costs scale linearly in a.
- Determinism; loader join keeps row order.

## 7. Experiment plan
1. Descriptive: distribution of `qspread_prev`, `entry_bounce_bps` for D10 events by year and size quintile; mean `entry_slip_bps` (the free lunch in the entry convention).
2. Strategy runs vs `steady_drift_plus` 12.07% / 1.19 / -22.07%: report return, Sharpe, alpha t, turnover-cost totals (`summary["total_transaction_costs"]`), each of the four variants. Pre-register a in {0.5,1,1.5}.
3. Harness IC of `close_loc` vs post-entry returns (EQ-30); two-sided; search 1996-2005, holdout 2006-2013 once; FDR with the other specs.
4. Compare `entry_ask` result to the docs/STEADY_DRIFT.md claim that 2x/3x costs leave 10.5%/10.1% (prototype).

## 8. Expected outcome / hypotheses and risks
H1: spread-based costs for small, low-priced stocks exceed the 5-25 bp table in 1996-2000 (fractions and wide Nasdaq spreads before decimalisation in 2001) and fall afterwards, so returns drop most in the early search window and 2008-13 is little changed. H2: `entry_bounce` mean is close to 0 bp with a heavy tail. H3: `close_loc` predicts at most a few bp of 60-session return; with a daily proxy a null is likely. Risks: stale closing quotes; bid/ask averages in `prc`; `close_loc` is mechanically related to the day0 return; the cost change lowers the headline numbers (honest, wanted).

## 9. Dependencies and implementation checklist
- [ ] REF-dsf-reader, REF-event-feature-store, REF-event-key
- [ ] `quote_features` stage + tests
- [ ] REF-cost-source engine change + parity pins green
- [ ] Strategy file and registry
- [ ] EQ-30 harness for `close_loc`
- [ ] Cost grid report (5/15/30 bp and spread model) saved under `results/_signal/`
