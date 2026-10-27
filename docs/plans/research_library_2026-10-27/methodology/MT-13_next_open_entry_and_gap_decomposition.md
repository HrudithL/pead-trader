# MT-13: Next-open entry variant and overnight / intraday / drift decomposition
- Status: planned | Priority: P2 | Effort: M | Source(s): library `foundations/02_EVENT_TIME_PIT.md` (cases table, time-window estimands), `foundations/01_SIGNAL_DEFINITIONS.md` sections 7 and 9 (gap, reaction_intraday), `strategies/05_LEEYAWNNN_EVENT_STUDY.md` (overnight +1.08pp / intraday +0.25pp / drift +0.78pp; next-open 4.1% vs next-close 1.6%), `reproduction/pead_reference.py::simulate_next_open_stocks`, `comparisons/03` (two response variables: `total_event_reaction` vs `tradable_future`)

## 1. Purpose & what we take from the library
Separate three estimands the repo currently blends: announcement effect (not tradable), open-fill opportunity (tradable by a pre-market / after-close reporter at the next open) and post-reaction drift (what we now capture by entering at the day0 close). Quantify how much edge the repo's conservative day0-close entry forgoes and what a next-open entry would earn, so repo numbers can be compared with next-open studies (leeyawnnn) on an equal footing, and so the live engine (which will in practice trade near the open) is backed by the right backtest.

## 2. Gap analysis vs the repo today
- All equity entries are the day0 close, P&L from `entry_row_idx+1` close-to-close (`engines/equity.py` accrual `flat_row = ... entry_idx + 1`; `docs/STEADY_DRIFT.md:28`, `docs/META_STRATEGY.md:38`). Pre-market and after-close reporters therefore forgo the open-to-close return of day0 (and, for after-close, the overnight gap is correctly excluded). ~86.8k of 261.5k day0-ok events have release hours 06-09 (MT-02 E5), ~104.8k hours 16-20.
- The panel used by the engine has close-to-close `ret` only (`data.py::_load_panel`); `openprc` exists in raw `data/raw_wrds/crsp/dsf_*.parquet` (missing ~1-2% of rows in the 1997 and 2008 files) but was dropped from `data/normalized_equity/` (columns `permno,date,ticker,prc,prc_is_bidask_avg,ret,retx,shrout,vol,cfacpr,cfacshr`).
- No decomposition of event returns into overnight/intraday/drift exists: `ret_fwd_*` columns in the events file are close-to-close forward returns from day0 (`ret_fwd_1d` ... `ret_fwd_60d`, plus `_mktadj`); `prep/equity/extended_horizons.py` and `daily_decay.py` build decay curves from close-to-close.
- The live engine cannot be validated against a next-open backtest because none exists (ROADMAP #3/#4).

## 3. Exact specification
Data: new prep stage `panel_quotes` (shared with MT-06) writes `data/panel_quotes.parquet` with `permno,date,openprc,prc,bid,ask,numtrd` in the **same row order** as the (permno,date)-sorted normalized panel; `prc` signs: negative `prc` = bid/ask average (flag `prc_is_bidask_avg` already in the normalized files) -> use `abs(prc)` and set `oc_valid=False` when the flag is set or `openprc` is missing/zero.

Return pieces per event (stored in the event table as outcome-class columns, hence in `OUTCOME_COLUMNS`):
`r_on = openprc_day0 / prc_{day0-1} * (cfacpr ratio) - 1` (overnight), `r_oc = prc_day0 / openprc_day0 - 1` (intraday, day0 is the first session at/after the release), `r_drift_h = prc_{day0+h}/prc_day0 - 1` (existing `ret_fwd_h`). Split factor ratios from `cfacpr` (both prices on same date share a factor; use same-day factor for `r_oc`, previous-day for `r_on`).

Engine option (`Sizing.entry_fill: str = "close"`): `"close"` (default, unchanged) | `"open"`. For `"open"`: for events with `timing_class in {premarket, after_close, unknown}` (all have a tradable day0 open once the release precedes 09:30; for after_close and unknown-shifted events day0 is already the next session) the position is established at the day0 open: add the partial-day return `r_oc` as a one-day P&L term on the day0 calendar date (`notional * r_oc` added at `entry_cal`), then accrue close-to-close from day0+1 as now; events with `timing_class == intraday` keep the close fill (no intraday data, MT-14). Market adjustment: for `return_basis='mktadj'` the day0 partial return cannot subtract an open-to-close market return (the index open is not in the panel), and no split of `vwretd_day0` into overnight and intraday parts is available. The open variant is therefore defined only for `return_basis='raw'` books (all Steady Drift variants); for market-neutral books it is `BLOCKED(no index open)`, with the optional degraded proxy of subtracting the full day0 `vwretd` (over-subtracts, labelled conservative). Entry cost: open-auction spread is wider; cost model (MT-06) takes `fill='open'` and multiplies the spread by a parameter grid {1, 1.5, 2} (no calibration data).

Decomposition report (`python -m pead decomp`): for each SUE decile x timing class, mean of `r_on`, `r_oc`, `r_drift_h` (h = 5, 20, 60), t-stats clustered by announcement quarter (`common/stats.py::fama_macbeth` pattern), D10-D1 and D10-minus-market versions; identifies how much of the D10 spread is the gap (untradable unless held through the release), the open-fill part and drift. Quantity tag: `event_car` (grade E).

New sanity check mirroring the library: for after-close releases, `r_on` of D10 minus D1 should be large and positive (news arrives in the overnight gap); for pre-market releases the overnight gap contains the news too, so `r_on` is not tradable by a pre-open order -- the open fill captures only `r_oc` and later. The exercise should reproduce the *shape* (not the numbers) of the library's leeyawnnn decomposition (gap about half of the spread).

## 4. Data requirements
Raw dsf (`openprc`, `prc`, `bid`, `ask`, `cfacpr`) present; coverage ~98-99%. Intraday bars and exact release minutes: BLOCKED (MT-14) so intraday releases and premarket fill quality cannot be resolved. Index open (for market-neutral open books): BLOCKED; proxy as above.

## 5. Framework mapping
Prep stage `panel_quotes` (`pead/prep/equity/panel_quotes.py`), event columns `r_on, r_oc, oc_valid` appended in `vol_features`/`positions` chain; `Sizing.entry_fill`; `EquityUniverse` gets `panel_r_oc` (day0 partial return lookup keyed by `entry_row_idx`); CLI `python -m pead decomp`, `python -m pead run <s> --entry-fill open`; results `results/decomposition/`.

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-dsf-reader (EQ) is this spec's `panel_quotes` stage (one implementation); REF-entry-leg (EQ-03) = the `entry_fill='open'` branch; REF-entry-lag = MT-11's shift.
- **NEW:** `panel_quotes` stage + parquet; `pead/eval/decomposition.py`; `tests/test_entry_fill.py`; `docs/ENTRY_TIMING.md` (the cases table of the library applied to our timing classes).
- **CHANGE:** `pead/engines/equity.py::run_equity_backtest` (new `entry_fill` branch adding a day0 P&L term at `entry_cal`; regression risk: none for `"close"`; confirm all pins); `pead/core/data.py` (`_load_events` keeps `r_oc`; add to cache key if panel arrays are extended); `pead/prep/stages.py` (register stage); `pead/strategies/base.py::EquityStrategy` (`sizing` already carries the new field).
- **RESTRUCTURE:** none (this rides on MT-02's `timing_class`).
- **IMPROVE:** the `ret_fwd_*` columns mix "from day0 close" semantics with a day0 that is sometimes the release session and sometimes the following one; document that their start is always the day0 close (consistent with entry) and add `r_on`/`r_oc` so the full announcement window can be reconstructed.

## 6. Acceptance tests
- Synthetic: price path open 100, close 110, next close 110: `entry_fill='open'` earns +10% on day0, `'close'` earns 0 (library test 4 analogue).
- `r_on * r_oc` identity: `(1+r_on)(1+r_oc) = 1 + ret_day0` within split-factor rounding on 99% of valid events (consistency with CRSP `ret`).
- Pins unchanged for `entry_fill='close'`.
- Decomposition smoke: totals `r_on + r_oc + drift` approximately reconstruct `ret_fwd_60d` from the previous close.

## 7. Rollout plan
`panel_quotes` stage -> decomposition report (descriptive, no engine change; this alone answers "how much is the gap") -> engine `entry_fill='open'` for raw-basis books -> comparison table close vs open vs delay (MT-11 R2) on `steady_drift*`.

## 8. Risks
- CRSP `openprc` is the first trade (NASDAQ) or opening auction price; opening-auction capacity for small caps is limited; liquidity cap (5% ADV) is a daily-volume concept -- a separate open-volume cap is unknowable here.
- Open-fill result is an upper bound for what a retail-latency strategy gets; label as such.
- Mixed `prc` sign conventions can silently corrupt returns -> the `oc_valid` mask and a unit test on negative `prc`.

## 9. Dependencies and implementation checklist
Depends on: MT-02 (`timing_class`), MT-06 (`panel_quotes`, fill-aware spread), REF-outcome-firewall (new outcome columns). Feeds: MT-11 R1/R2, live engine validation.
- [ ] `panel_quotes` stage; coverage report by year
- [ ] decomposition report by decile and timing class
- [ ] `entry_fill='open'` for raw-basis books + tests
- [ ] close vs open vs delayed comparison on Steady Drift
- [ ] docs/ENTRY_TIMING.md
