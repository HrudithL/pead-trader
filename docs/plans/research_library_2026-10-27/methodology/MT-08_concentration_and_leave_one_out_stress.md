# MT-08: Concentration and leave-one-out stress (ticker, quarter, year, sector, timing class, top trades)
- Status: planned | Priority: P1 | Effort: S | Source(s): library `strategies/20_FACTOR_BACKTESTER_CONCENTRATION.md` (NVDA leave-one-out: 37.01% -> 26.10% CAGR; "leave-one-ticker/quarter/sector/event-type-out"), `comparisons/03` ("remove the strongest-performing ticker, best single year, hottest earnings quarter and strongest sector"), `foundations/05` (robustness by sector, liquidity, regime, concentration), `audits/03` section 5

## 1. Purpose & what we take from the library
Show whether a headline return depends on one name, one quarter, one sector or a handful of trades, and make the answer a gate (MT-04 G7). PEAD books are naturally tail-dependent (small caps, big gaps), and the repo's small-cap-heavy long-only books (`steady_drift*`) are exactly the case the library warns about.

## 2. Gap analysis vs the repo today
- Per-position P&L already exists: `run_equity_backtest` returns `positions` with `permno, ann_quarter, ff12_sector, size_quintile, notional, gross_pnl, cost, net_pnl` (saved as `results/<name>/positions.parquet`, git-ignored, regenerable).
- Era split exists (`metrics.ERAS`: 1996_2001, 2002_2007, 2008_2013 -> `sharpe_<era>`), yearly table exists (`metrics.yearly_table`), long/short attribution exists (`reporting/attribution.py`), `reporting/situations.py` slices by situation. Nothing removes a name/quarter/sector and re-evaluates, no top-N-trade dependence, no HHI/Gini of P&L, no "tail-dependent" classification, and none of it feeds a gate.
- Because the engine is `run_equity_backtest(uni, weight, sizing)` with `weight` an array aligned to `uni.events` (`equity.py:145-147`), an *exact* leave-out is a one-liner (`weight[mask] = 0`) and re-runs in 1-3 s (ARCHITECTURE.md), so the cheap first-order approximation is only needed for the screening pass.

## 3. Exact specification
```python
# pead/eval/stress/concentration.py
def pnl_concentration(positions) -> dict   # top1/top10/top50 trade share of total net_pnl; HHI by permno; Gini; share of pnl from best quarter / best year; n_positive_quarters / n_quarters
def leave_out_masks(events, positions, spec) -> dict[str, np.ndarray]   # name -> boolean mask over events
def loo(strategy, mask_name) -> LooResult   # EXACT: weights = strategy.weights(events); weights[mask]=0; run_equity_backtest; returns active-window CAGR, Sharpe, alpha_t, max_dd
def loo_screen(positions, mask) -> dict      # FIRST-ORDER: subtract net_pnl of masked positions from daily P&L along their holding path (needs flat path) -- screening only
```
Mask families (`spec`): `top_k_names` (k=1,3,5,10 by total net_pnl), `top_k_trades` (k=10,50,100), `each_quarter` (all announcement quarters; report worst/best ten), `each_year` (18), `best_year`, `each_sector` (12 `ff12_sector`), `top_sector`, `size_quintile=1` (micro), `timing_class` in {premarket, intraday, after_close, unknown} (from MT-02), `sue_decile` extremes, `delisting_events` (MT-05), `post_2008` / `exclude_2008_2009` / `exclude_2000_2002` (library: re-run with/without stress years).
Outputs per mask: `d_cagr`, `d_sharpe`, `d_alpha`, `alpha_t`, `sign_flip` (alpha changes sign), `retained_fraction = cagr_without / cagr_with`. Classification: `tail_dependent` if removing top 10 trades or the best single name or best quarter loses more than `x` of CAGR, with `x` a **proposed pre-registered constant** (default 50%) -- stored in the protocol file, not tuned on results. Placement of the data: `results/<name>/concentration.csv` + a figure (waterfall of retained fraction) in the report.

Bootstrapped alternative for noise: cluster-resample names (draw permnos with replacement, recompute P&L from the position table, first-order only) to get a CI on CAGR that reflects name concentration -- complements the time-block bootstrap of MT-03.

## 4. Data requirements
`positions.parquet` per run (present for runs executed with the current engine; regenerate with `python -m pead run <s>` otherwise). Sector and size already in events. Timing class from MT-02 (`first_executable_session` outputs; until then derive from `anntims` hour as in the audit).

## 5. Framework mapping
`pead/eval/stress/concentration.py`; CLI `python -m pead stress concentration <name...> [--exact]`; `evaluate()` calls the screen always and exact reruns for the worst 5 masks; summary keys `top10_trade_share, hhi_names, best_quarter_share, loo_min_retained, tail_dependent`. Report section "Concentration" in `reporting/single.py` and a column in the comprehensive report (`reporting/comprehensive.py`).

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** OV-01 owns the strategy-facing stress run inside `pead/validation/` (REF-validation-suite); the mask families and the gate constants here are its specification; implement the concentration code in `pead/validation/stress.py`, not in a second `pead/eval/stress/`.
- **NEW:** `pead/eval/stress/concentration.py`, `tests/test_concentration.py`, `results/<name>/concentration.csv`.
- **CHANGE:** `pead/engines/equity.py::run_equity_backtest` -- no change needed for exact LOO (weight array already external); but `EquityRun.positions` should also keep `day0_date`, `exit_date` (already present as event columns) and an explicit `event_id` for stable joins (check `equity_vol_panel` `event_id` column exists: it does); `pead/reporting/single.py` (add section; LOW risk); `pead/strategies/base.py::EquityStrategy.backtest` (accept `exclude_mask` optional kwarg for the exact path; regression risk none when None).
- **RESTRUCTURE:** unify the several ad-hoc slicing helpers (`reporting/situations.py`, `reporting/attribution.py`, `metrics.yearly_table`, `ERAS` loop) behind one `slice_by(positions, key)` utility used by MT-08/MT-09/situations.
- **IMPROVE:** `positions.parquet` is git-ignored and regenerable but the concentration table is small -> track `concentration.csv` in git alongside `summary.json`; options runs have no per-position parquet equivalent of the same schema (options area to supply a `positions` frame with `underlying permno`, `event_id`, `net_pnl`).

## 6. Acceptance tests
- Synthetic: a book where one name earns 90% of P&L -> `tail_dependent=True`, retained fraction about 0.1 after exact LOO; uniform book -> retained fraction about (n-1)/n.
- Exact vs first-order agree within 5% for a low-overlap synthetic book; differences documented for overlapping real books (cap interaction).
- `weight[mask]=0` leaves other positions' *notional* unchanged when no cap binds (test with generous cap) and changes them only via cap/trailing NAV when binding.
- Real data smoke: `steady_drift_plus` top-10 trade share, HHI, best-quarter share computed and reproducible; no numbers are asserted in the test beyond finiteness and monotone retained-fraction ordering.

## 7. Rollout plan
Implement `pnl_concentration` + each_quarter/year/sector screens first (read-only on existing positions); then exact reruns; then gate G7 and report section; run across all non-legacy equity strategies and publish a table (descriptive).

## 8. Risks
- Re-running with names removed changes caps and compounding; exact vs screen differences must be shown, not hidden.
- With a small universe of strategies, "tail dependent" threshold choice is subjective -> pre-register and record.
- Name-level LOO on 20k events is noisy; use top-k and cluster bootstrap instead of every permno.

## 9. Dependencies and implementation checklist
Depends on: MT-03 (stats), MT-02 (timing class, optional). Feeds: MT-04 G7, MT-12 evidence grade.
- [ ] `pnl_concentration` + tests
- [ ] mask families + exact LOO
- [ ] first-order screen
- [ ] report section + comprehensive column
- [ ] gate wiring and constants in `v1.json`
