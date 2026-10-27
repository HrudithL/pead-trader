# OV-04: Volatility-scaled, close-triggered position stop (and why ATR sizing is not ported)

- Status: planned | Priority: P2 | Effort: M | Date: 2026-10-27
- Sources: library `strategies/14_VOLATILITY_REGIME_MOMENTUM.md` (sections 2-4, 6, 8, 12), `audits/06_..._DISCREPANCIES.md` section B, `reproduction/04_...` (Exit row: "Fixed time; ATR stop; target+stop").

## 1. Thesis & what we take from source (and what we do NOT take)

Thesis: a PEAD position that falls by several multiples of its normal movement after entry is more likely to be a failed signal / bad news than noise; exiting limits the left tail and frees cash and admission room under the 80% cap. The book already has: inverse idiosyncratic variance sizing (`W.causal_inverse_variance`), a 2% / 1% name cap, a 5% ADV cap and a 60-day volatility throttle. The only thing it does NOT have is any early exit (holds are fixed 60 sessions via `with_fixed_hold`).

Take: the idea of a stop expressed in volatility units, evaluated on completed closes, with exit executed after the trigger bar, and the discipline of reporting no-stop vs stop separately.
Do NOT take: ATR-expansion entry filter (`ATR14_t > 0.95*ATR14_{t-5}`), 20-day ATR-normalised momentum entry, +4 ATR profit target, 10-bar max hold, 1-2% risk tiers, the 2.5-ATR sizing / 5-ATR stop pairing (source's own sizing/stop mismatch), or any of its multi-asset results (21.0% CAGR, Sharpe 1.34 are gold/FX/index proxies 2020-2026).

## 2. Evidence grade & known defects in source

Grade B for code inspectability, but evidence against the idea in the source itself: in the author's own sweep the NO-STOP configuration is best ($226,804, Sharpe 1.34) and every stop level lowers Sharpe (2x ATR 1.10; 5x ATR 1.17). Library audit B: stops are close-triggered `strategy.close` market orders (not resting stops), the "production" run is no-stop while Pine defaults the hard stop ON, parameter 0.95 is not the best tested value (0.90 is), data downloads differ between the hold-period and robustness runs, six markets with concentration in gold and index, and a version history that expands the search (V1-V7). Monte-Carlo trade shuffling proves nothing about profitability. Conclusion: no evidence a stop helps; useful only as a tail-risk ablation on our own paths. ATR itself needs high/low prices.

## 3. Exact rules (codeable)

True ATR requires OHLC. BLOCKED for ATR proper: the panel has `prc, ret, retx, vol, shrout, cfacpr, cfacshr` only (verified schema of `data/normalized_equity/equity_2005.parquet`); no daily high/low. Proxy used instead: close-to-close volatility, i.e. `sigma_i = clip(tvol_60d_i, IVOL_CLIP)` falling back to `ivol_252d` (both trailing, known at day0; columns exist in `equity_vol_panel.parquet`). State this is NOT ATR (it ignores intraday range and gaps are included in the std).

Stop, per position, on the raw-return path from the entry close (consistent with `return_basis="raw"`):
```
path_k  = prod_{j=1..k} (1 + ret_raw[e + j]) - 1,   k = 1..H,  H = min(60, rows left)   # e = entry_row_idx
L_i     = c * sigma_i * sqrt(60)                       # c in {1.0, 1.5, 2.0}; sigma daily
trigger = first k with path_k <= -L_i
exit_row = min(e + trigger + lag, e + H)               # lag=1 (default): exit at the NEXT session close (decision after close)
```
`lag=0` (exit at the trigger close) is reported as the idealised upper bound only. No re-entry after a stop. Costs: the engine already charges the liquidity-quintile cost at exit (`exit_cal` in `run_equity_backtest`). Gap risk: close-to-close paths already contain overnight gaps; the executed exit is at the lagged close so the trigger-day loss is realised plus the next-day move.
Controls: (C1) same stop on shuffled-SUE placebo events; (C2) time-reversed control: stop level at +L_i (profit-take at same distance) to see whether any effect is just "shorter holds"; (C3) shorter fixed hold 40 sessions at matched average holding length (the repo already found 40 and 90 worse than 60: 8.7% / 7.9%, STEADY_DRIFT section 9).

## 4. Data requirements & availability in THIS repo

Available: `uni.ret_raw`, `events.entry_row_idx / exit_row_idx`, `tvol_60d`, `ivol_252d`. BLOCKED for ATR and for any intraday/touched-stop semantics: daily high and low (CRSP daily `bidlo/askhi` were not pulled; exact missing input: columns `bidlo, askhi` in the CRSP daily extract, `data/raw_wrds/crsp`). Proxy: close-to-close sigma. Delistings: `with_fixed_hold` cuts at the stock's last row; stop logic must respect `run_end`.

## 5. Framework mapping

- Files: `pead/engines/equity.py` (add `with_stop_exit`, next to `with_fixed_hold`), `pead/strategies/equity/steady_drift_stop.py` (new).
- Registration: `steady_drift_plus_stop15` (c=1.5, lag=1); grid variants via REF-variant-grid, not registered.
- Composition: strategy attribute `stop_vol_mult: float = None`, `stop_lag: int = 1`; `EquityStrategy.backtest` applies `with_fixed_hold` then `with_stop_exit(uni, self.stop_vol_mult, self.stop_lag)` before `weights()`.
- Params defaults: `stop_vol_mult=None` (off), `stop_lag=1`.
- Reuse (verified): `with_fixed_hold` (its `last = ...flatnonzero(pp[1:] != pp[:-1])` run-end logic must be shared), `EquityUniverse` fields, `Sizing`, `cap_open_book` (uses `exit_cal`, so earlier exits free room automatically).
- Engine change: new function only; `run_equity_backtest` untouched (it already reads `exit_row_idx` / `exit_date`).

## 5b. Build vs. change vs. restructure

(1) NEW
- `with_stop_exit(uni, stop_vol_mult, lag, sigma_cols=("tvol_60d","ivol_252d"))` in `pead/engines/equity.py` (or `pead/engines/exits.py` re-exported); `pead/strategies/equity/steady_drift_stop.py`; `tests/test_stop_exit.py`.
(2) CHANGE
- `pead/strategies/base.py::EquityStrategy` add `stop_vol_mult`, `stop_lag` attributes and application in `backtest`; add to `params()`. Regression: default None leaves every existing result unchanged (bit-identical check).
- `pead/engines/equity.py::with_fixed_hold`: extract run-end computation into a helper `_run_end(panel_permno)` shared by both (pure refactor; test the existing `test_fixed_hold_is_cut_...`).
(3) RESTRUCTURE
- REF-early-exit: generalise "event exit" construction so that exit rules (fixed hold, stop, later others) compose as a chain of functions on `EquityUniverse` that each only shorten `exit_row_idx`.
- REF-variant-grid, REF-validation-suite.
(4) IMPROVE
- Add daily high/low (CRSP `bidlo/askhi`) to `pead/prep` if intraday-style stops are ever to be tested; today the prep stages cannot supply them.
- `summary.json` should record stop count and average realised holding days (`holding_days` is already a column).

## 6. Acceptance tests

- Synthetic path: ret = -1% daily with sigma such that L is crossed on day 12: exit_row = e + 12 + lag; `lag=0` gives e+12; no trigger gives e+60 (identical to `with_fixed_hold`).
- Never extends a hold; never crosses into the next permno (reuse `_panel` pattern).
- Look-ahead: rewriting returns after the trigger day does not change the exit row; rewriting returns before it does.
- With `stop_vol_mult=None` NAV is identical to baseline. With huge multiplier identical to baseline.
- No-borrow invariant (marked holdings < 95% NAV, `max_book_gross_over_nav`) preserved.
- Determinism.

## 7. Experiment plan

Baseline `steady_drift_plus` (12.07% / 1.19 / -22.07%; 2008-13 Sharpe 0.79 in STEADY_DRIFT). Grid fixed in advance: c in {1.0, 1.5, 2.0} x lag in {1, 0}; 6 runs total, all reported. Acceptance: same repo rule as OV-02/03 AND tail metric improves (worst-12-month and 1st-percentile 20-day return of the book) AND the improvement exceeds the C3 matched-holding-length control AND survives placebo. Run OV-01 stress (a stop can concentrate dependence on a few crash quarters). Report stop frequency and the realised loss avoided vs the rebound given up (positions stopped out that would have finished positive at day 60).

## 8. Expected outcome and honest risks

Expected: low value. The source's own sweep shows stops cost Sharpe; PEAD drift for small/mid caps has high idiosyncratic noise and mean reversion after large drops is plausible, so stops may sell lows. Positive case: truncating -50% outcomes of a few names (STEADY_DRIFT notes delisting loss sensitivity is small: -0.1 to -0.2 pt). Risks: proxy is not ATR; stop level choice is an extra degree of freedom; 5%-ADV cap makes forced exits costlier for thin names (cost model is flat bps by liquidity quintile, no impact). Default expectation: reject; keep as ablation evidence ("stops do not help here") so the question is closed.

## 9. Dependencies and implementation checklist

Depends on: OV-01, REF-early-exit, REF-variant-grid, REF-validation-suite.
- [ ] `_run_end` refactor + existing test unchanged
- [ ] `with_stop_exit` + 6 tests
- [ ] 6-run grid + controls C1-C3
- [ ] OV-01 stress, era table, stop-frequency report
- [ ] Decision note; if rejected, record in `docs/` as a closed negative result
