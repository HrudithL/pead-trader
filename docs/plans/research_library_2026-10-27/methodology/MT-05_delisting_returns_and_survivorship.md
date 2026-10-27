# MT-05: Delisting returns and survivorship handling in the return panel
- Status: planned | Priority: P0 | Effort: S | Source(s): library `audits/03_BACKTEST_FAILURE_MODES.md` section 3 ("Delisting: use RET compounded with DLRET"), `foundations/02_EVENT_TIME_PIT.md` PIT requirement 4 and fixture "stock delisted after miss", `reproduction/01_REPRODUCTION_PLAYBOOK.md` 1A; repo `docs/FIXES.md` ("Survivorship / delisting handling: unknown")

## 1. Purpose & what we take from the library
Close the open item "delisting handling: unknown" with a measurement and a fix: compound CRSP `dlret` into the last return of every delisted name, treat missing returns explicitly instead of as zero, and keep events that delist right after the report instead of silently dropping them.

## 2. Gap analysis vs the repo today (measured 2026-10-27)
- `grep -rn "dsedelist\|dlret" pead` returns nothing; the only hits in the repo are in git-ignored `earnings_build.tmp/`. `data/raw_wrds/crsp/dsedelist.parquet` exists (columns `permno, dlstdt, dlret, dlstcd`; 3,970 rows; `dlret` missing for 4.06%).
- On the 3,890 delistings dated 1996-01-01..2013-12-31 the normalized panel (`data/normalized_equity/equity_*.parquet`, columns `permno,date,ticker,prc,prc_is_bidask_avg,ret,retx,shrout,vol,cfacpr,cfacshr`) has a row on `dlstdt` for every one (gap 0 days), but `ret` equals `dlret` for only 3.0%: the delisting return is **not** compounded in. 252 delistings have `dlret < -0.30`.
- `pead/core/data.py:58-59` converts missing `ret` into 0.0 (`fillna(0.0)`); in `equity_2008.parquet` 0.86% of rows have missing `ret`.
- Exposure in funded events: of 172,923 positions in `equity_vol_panel.parquet`, 156 contain a delisting date in `[day0, exit_date+5d]`; 9 are in decile 10 (their `dlret` average -6.1%, min -47.1%, one performance-code delisting), so the effect on long-only Steady Drift is small; for short legs (decile 1) the omission hides *gains* from bankruptcies and makes shorts look worse, so signs differ by leg. `with_fixed_hold` already cuts a fixed hold at the stock's last row (`equity.py:314`), so the data plumbing for terminal returns exists.
- Event-set survivorship: `day0_status=='no_trading_day_at_or_after_anndats'` drops 2,275 events (MT-02 E14) -- firms that vanished immediately after a report.

## 3. Exact specification
Function `pead/core/panel.py::apply_delisting_returns(panel, dsedelist, policy)`:
```python
policy = DelistPolicy(use_dlret=True,
                      missing_performance="impute",      # 'impute' | 'zero' | 'drop_event'
                      imputed_loss={"nyse_amex": -0.30, "nasdaq": -0.55},   # literature convention (Shumway 1997 for NYSE/AMEX, Shumway-Warther 1999 for Nasdaq), PARAMETERS, sweep 0 / half / full
                      codes_performance=range(400, 600)) # CRSP dlstcd 4xx-5xx
r_term = (1 + ret_last) * (1 + dlret) - 1      # compound, do not replace
```
Rules: apply on the row at `dlstdt` only; if `dlret` is missing and `dlstcd` in performance codes use `imputed_loss` by exchange (exchange from `stocknames`, REF-panel-delist), else 0 and log; never turn a missing ordinary `ret` into 0 silently: count it, keep `ret_missing` bool array in the universe, and (default) treat as 0 only inside a recorded tolerance (<= 1% of position-days per strategy) else fail loudly. Output keeps both `ret_raw_nodl` and `ret_raw` so the effect is auditable. `EquityUniverse` gains `delist_flag: np.ndarray` and `ret_missing: np.ndarray`.
Event retention: `build_deciles` keeps events with `no_trading_day_at_or_after_anndats` in the *ranking pool* (their SUE is a PIT fact) and marks them `untradable=True` (no entry possible: no session after the report) -- they are not positions, but the rank pool is no longer outcome-conditioned.
Reporting: every result summary gets `n_positions_with_delisting`, `delisting_pnl` (sum of the dlret-driven component) and `ret_missing_frac`.

## 4. Data requirements
Present: `dsedelist.parquet`, `stocknames.parquet` (exchange code history), `ccmxpf_lnkhist`. Not needed: anything new. Imputation constants are literature conventions, not estimated here -- labelled as parameters with a sweep (0%, half, full).

## 5. Framework mapping
`pead/core/panel.py` (new) called by `pead/core/data.py::_load_panel`; the cache key (`data.py` .npz keyed on source file sizes/mtimes) must include `dsedelist.parquet` and the policy hash. Prep path: `pead/prep/equity/lib/panel.py::load_mktadj_panel` also builds `ret_mktadj` and the `entry_row_idx` indices -- these indices depend on row order of the panel; compounding a return into an existing row does **not** change row order, so `positions_rankweighted_v2`/`equity_vol_panel` indices stay valid (add a test: row count and `(permno,date)` order unchanged). CLI: `python -m pead audit delist` prints the counts above; `python -m pead run <s> --delist-policy zero|dlret|impute` (env `PEAD_DELIST_POLICY`).

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-panel-delist also absorbs OV's REF-panel-hygiene (zero-filled missing returns) and EQ's 'IMPROVE: delisting returns'; one `pead/core/panel.py`.
- **NEW:** `pead/core/panel.py` (REF-panel-delist), `tests/test_delisting.py`, audit command, `docs/FIXES.md` section "Delisting (fix 5)" after the ladder is run.
- **CHANGE:** `pead/core/data.py::_load_panel` (lines 51-71: compound `dlret`, cache key; regression risk: MEDIUM -- every NAV pin in `tests/test_framework.py::test_default_vintage_pins_corrected_final_nav` moves slightly; expected tiny for long-only, larger for shorts; ship behind `PEAD_DELIST_POLICY=none` default until the ladder is documented, then flip and re-pin); `pead/prep/equity/lib/panel.py::load_mktadj_panel` (same compounding so prep features and the engine share one return definition; `vol_features` trailing windows would see the terminal return for *prior* events of the same permno only -- confirm no leakage: terminal returns occur at the end of a permno run, after any later event); `pead/prep/equity/build_deciles.py:120` (retain untradable events in the pool; MEDIUM, changes decile boundaries slightly -> same re-pin).
- **RESTRUCTURE:** one panel-construction function used by both `core/data.py` and `prep/equity/lib/panel.py` (today the two duplicate the load/merge/dedupe logic).
- **IMPROVE:** record `ret_missing` and `delist_flag` so MT-01 can assert "no phantom zero-return days after a stock's last row"; extend `with_fixed_hold` test to a delisting inside the window.

## 6. Acceptance tests
- Synthetic: stock with ret +1% then `dlret=-100%` at `dlstdt`: terminal compounded return -100%+...; a long position loses its notional; a short gains it (symmetry).
- Real data: after the policy, the share of the 3,890 delistings whose last `ret` equals `(1+ret)(1+dlret)-1` (non-null) is 100%; row count and `(permno,date)` ordering identical to before.
- Ladder: run `balanced_leverage`, `calm_weighted`, `steady_drift_plus` under `none | dlret | impute`; record delta in FIXES.md; expectation to be *verified*, not assumed (long-only small, shorts positive).

## 7. Rollout plan
Audit command first (numbers above reproduced); then implement under a flag; run the three-policy ladder; flip default and re-pin NAVs in one commit with a FIXES.md entry.

## 8. Risks
- Imputation constants are a modelling choice; the sweep exposes sensitivity.
- Options book unaffected (OptionMetrics) but the combined portfolio's equity sleeve moves.
- CRSP `dlret` coverage is not perfect; the missing-ret tolerance may fail on real data -> investigate, do not raise the tolerance silently.

## 9. Dependencies and implementation checklist
Depends on: REF-pit-test-kit. Related: MT-02 (E14/E15), MT-14 (gap register row "delisting returns: present, unused").
- [ ] `apply_delisting_returns` + unit tests
- [ ] `ret_missing`/`delist_flag` arrays in `EquityUniverse`
- [ ] cache-key change; confirm row order unchanged
- [ ] `build_deciles` retains untradable events
- [ ] three-policy ladder + FIXES.md entry; re-pin NAVs
