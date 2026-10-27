# Enabling changes for the overlays / adjacent specs (OV-01 .. OV-07)

Date: 2026-10-27. Consolidates every REF / CHANGE / IMPROVE item from the seven spec sheets, de-duplicated. IDs are stable; other spec folders may reference the same REF names. All paths and function names were verified against the repo as of this date.

## A. REF items (restructures)

| ID | What | Where | Needed by |
|---|---|---|---|
| REF-validation-suite | Package `pead/validation/` owning stress (leave-one-out), placebo, era splits, paired bootstrap, no-borrow check, factor-IC; CLI `python -m pead stress`/`validate` in `pead/cli.py` | new `pead/validation/*`, `pead/cli.py` | OV-01 (owner), 02, 03, 04, 05, 06, 07 |
| REF-placebo | Shuffled-SUE placebo (permute `sue_rank_pct` within `ann_quarter`, 3+ seeds) ported from the prototype `meta/steady_placebo.py` into the framework, callable on any `EquityStrategy` | `pead/validation/placebo.py` | 02, 03, 04, 05, 07 (and OV-01 control) |
| REF-variant-grid | Run unregistered parametrised clones of a strategy (copy + attribute override) and write to `results/_experiments/`, not the leaderboard; trial log `results/_experiments/trials.csv` | `pead/validation/grid.py` (uses `registry.get`; avoids duplicate-name error of `register_instance`) | all |
| REF-event-features | Causal per-event feature registry `pead/core/event_features.py`; `EquityStrategy.extra_features`; contract: pure function of `EquityUniverse`, uses rows <= `entry_row_idx` only; disk cache in `data/cache/` | new file + `pead/strategies/base.py` | 02, 03(B), 05, 06, 07 |
| REF-exposure-hook | `exposure_mult` argument in `apply_cash_and_risk_target` + `EquityStrategy.exposure_overlay(uni)` hook; invariant `0 <= mult <= 1` | `pead/engines/equity.py`, `pead/strategies/base.py` | 03 (also reusable by ROADMAP 6 decay-aware throttle) |
| REF-market-state | Causal daily market-state panel (index trend, breadth) cached in `EquityUniverse.extras` | new `pead/core/market_state.py` | 03, 06 |
| REF-early-exit | Compose exit rules as functions that only shorten `exit_row_idx` (shared `_run_end` helper with `with_fixed_hold`) | `pead/engines/equity.py` | 04 |
| REF-synthetic-events | Build pseudo-event tables for non-event sleeves (`monthly_topk_events`) so `run_equity_backtest` can run them | new `pead/core/synthetic_events.py` | 06 |
| REF-shared-cash-ledger | Combined sleeves must share one cash ledger (see `docs/RETURN_TARGET_AUDIT_2026-10-26.md`); preferred route is pseudo-events in one `run_equity_backtest` call; `pead/portfolio/combine.py::blend` is NAV-level only | 06 | 06 |
| REF-fundq-pull | PIT fundamentals table with `avail_date` per gvkey-quarter (+ `oancfy` pull); replace ad hoc 130-day rule in `pead/prep/equity/ff_adjustment.py` over time | new prep stage, WRDS pull | 05, 07 |
| REF-panel-hygiene | `ret_raw` zero-fills missing returns (`_load_panel`); add gap flag / span guards; optional `prc, vol, shrout` extras in a separate cache; NASDAQ volume convention check | `pead/core/data.py` | 02, 06 |
| REF-tbill-real | Replace typed-from-memory T-bill (ROADMAP item 2) | `pead/core/rates.py` | 03 (strongly), all cash-leg comparisons |
| REF-synth-fixtures | Move the synthetic `_panel` helper from `tests/test_steady_drift.py` into a shared test fixture module | `tests/` | 01, 02, 03, 04, 06 |

## B. CHANGE items (existing code modified)

| # | File / function | Change | Regression risk / guard | Specs |
|---|---|---|---|---|
| C1 | `pead/strategies/base.py::EquityStrategy.backtest` | add `event_mask=None` (ex post exclusion), `extra_features` attach step, `exposure_overlay` hook, optional stop exit; make the `long_only_cash or risk_target_vol` condition also true when a gate is set | defaults leave behavior unchanged; test bit-identical NAV of the four `steady_drift*` vs committed `results/<name>/daily.csv.gz` | 01, 02, 03, 04 |
| C2 | `pead/engines/equity.py::apply_cash_and_risk_target` | new optional `exposure_mult` (applied after `min(1, target/vol)`, before the turnover-cost line); add `gate_mult` column | `None` default; same regression test | 03 |
| C3 | `pead/engines/equity.py::with_fixed_hold` | factor out run-end logic into `_run_end`; add `with_stop_exit` | existing test `test_fixed_hold_is_cut_at_the_stocks_last_row...` must pass unchanged | 04 |
| C4 | `pead/core/data.py` | add `load_panel_extras()` (do NOT alter `_load_panel` return tuple or the `equity_panel.npz` key); optional `EquityUniverse` fields after `panel_permno` default None | none if separate cache | 06 |
| C5 | `pead/strategies/weights.py` | add single `causal_rank_pct` (or place it in `event_features.py`; define once) | pure addition | 02, 05, 07 |
| C6 | `pead/prep/stages.py` + new stage; WRDS pull list for `comp.fundq` | add `oancfy`; new `fundamentals_pit` stage | raw master changes: follow REPO_MAP data policy (real dir, hash manifest, `D:` master first) | 05 |
| C7 | `pead/prep/equity/vol_features.py` | carry `gvkey` into `equity_vol_panel.parquet` (instead of joins on `(permno, day0_date)`) | derived-data rebuild; compare all existing columns equal | 05 (optional) |
| C8 | `pead/reporting/single.py` | optional stress / IC / gate sections | text only | 01, 03, 07 |
| C9 | `pead/cli.py` | `stress` subcommand | none | 01 |
| C10 | `EquityStrategy.params()` | include new attributes in `summary.json` | none | 02, 03, 04 |

## C. IMPROVE items (infrastructure weaknesses)

- I1 `positions.parquet` is git-ignored; per-position daily P&L is not stored (OV-01 limits).
- I2 No trial registry: 66 variants already run by hand; add `results/_experiments/trials.csv` (REF-variant-grid).
- I3 No clean holdout: after ROADMAP item 7 (data after 2013) freeze code and use it once; until then label 2008-13 "not a holdout".
- I4 Live engine cannot compute new features: all new strategies `live_ready = False`.
- I5 Cost model is flat bps by liquidity quintile; no impact or turnover-based sensitivity (matters for OV-06 monthly sleeve).
- I6 High/low prices unavailable (CRSP `bidlo/askhi` not pulled): needed only for ATR-style stops.
- I7 Document PIT-lag conventions in one place (currently only in `ff_adjustment.py` docstring).
- I8 Known small look-ahead in size/liquidity quintile breakpoints (STEADY_DRIFT section 6): do not reuse the pattern in new features.

## D. Suggested order

1. REF-synth-fixtures, C1 (`event_mask`), REF-validation-suite core, C9 -> OV-01 complete (regression test first).
2. REF-placebo, REF-variant-grid (small).
3. REF-event-features + C5 -> OV-02 V0 diagnostic (no backtest needed). In parallel REF-market-state -> OV-03 diagnostics (B2 distribution; no returns).
4. REF-tbill-real (before any cash-shifting result is quoted); REF-exposure-hook + C2 -> OV-03 backtests.
5. On "go" only: OV-02 V1-V4; OV-07 IC harness; OV-04 (REF-early-exit, C3).
6. OV-05 stage 1 (ROA proxy) after REF-fundq-pull design; WRDS pull C6 needs owner approval.
7. OV-06 last (REF-panel-hygiene, REF-synthetic-events, REF-shared-cash-ledger); only if the owner wants a cash-fill diversifier.

Regression gate for steps 1-4: after each CHANGE, run `python -m pytest tests -q` and recompute `steady_drift`, `steady_drift_calm`, `steady_drift_core`, `steady_drift_plus` and compare NAV arrays to the committed `results/<name>/daily.csv.gz` (must be bit-identical).
