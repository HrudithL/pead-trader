# MT-09: Paired factor and risk ablation harness (ladders, same-event pairing, matched-risk comparison)
- Status: planned | Priority: P1 | Effort: M | Source(s): library `reproduction/04_PAIRED_FACTOR_AND_RISK_ABLATIONS.md` (sections 1, 3, 4, 6, 7), `reproduction/01_REPRODUCTION_PLAYBOOK.md` section 4 (baseline ladder A-J), `reproduction/05` section H (four score legs, paired same-event sample), `reproduction/02_EXPERIMENTS.md` E1-E5, `audits/03` section 6 (benchmark and leverage confusion)

## 1. Purpose & what we take from the library
Every claim "component X helps" must be a *paired* comparison: identical events, dates, costs, capital and execution engine, one thing changed, uncertainty from a paired block bootstrap, and the comparison made at matched risk (a levered variant cannot "beat" an unlevered one). The library's ladder (benchmarks -> equal-weight long-only fixed hold -> long/short deciles -> + filters -> + exits -> + vol sizing -> options) becomes a runnable, versioned spec.

## 2. Gap analysis vs the repo today
- Lineage exists: `Strategy.parent` (base.py:57) and the engine is shared, so pairs are natural (e.g. `steady_drift` -> `steady_drift_calm` -> `steady_drift_core` -> `steady_drift_plus`; `calm_weighted` -> `calm_weighted_filter`). The comprehensive report compares strategies side by side (`reporting/comprehensive.py`) but does **not** test differences: no paired bootstrap, no event-set check, no matched-risk comparison.
- Ablation scripts are ad hoc and live outside the package: `meta/ablate.py`, `ablate2.py`, `ablate3.py`, `meta/steady_placebo.py`, `meta/steady_validate.py` (tracked). They hand-roll their own engine wrappers and metrics (`meta/analyze_sdb.py`), so they are not guaranteed to match `python -m pead run`.
- Risk-matching: `portfolio/combine.py::scale_to_vol` and `market_beta` exist; leaderboard compares raw CAGR across books with very different volatility (e.g. `steady_drift_core` 11.99% at 13.1% vol vs `steady_drift_calm` 9.55% at 7.8% vol in `results/leaderboard.md`). `docs/FIXES.md` shows `_max` books lose Sharpe as leverage rises.
- Benchmarks in the ladder: VW and EW market returns exist (`market_benchmark.parquet`: `vwretd`, `ewretd`); ROADMAP item 1 (investable small-cap benchmark) is open; library benchmarks (SPY/QQQ, 12-1 momentum, 3x ETF) are not in the repo (MT-14).
- Library warns about changing universe/calendar/sector mix inside an "ablation"; the repo's variants differ in their `weights()` masks and sizing, so event sets differ silently.

## 3. Exact specification
```python
# pead/eval/ablation.py   (REF-ablation-harness)
@dataclass
class Rung: id: str; base: str; overrides: dict | None = None      # strategy name + attribute/Sizing overrides, or a callable weights transform
@dataclass
class Ladder: id: str; rungs: list[Rung]; pairs: list[tuple[str,str]]; benchmark: str = "vwretd"; match: str = "vol"  # 'vol' | 'beta' | 'gross' | 'none'
def run_ladder(ladder) -> dict[str, EquityRun]          # same universe object, same cost model, same protocol window (MT-04)
def event_set_report(a: EquityRun, b: EquityRun) -> dict   # n_only_a, n_only_b, n_both, share of notional; pair is 'paired' only if n_only_* / n_both < tau (default 0.02), else 'unpaired, universe changed'
def matched(a_ret, b_ret, how) -> (a', b')               # scale to common ex-post vol (or beta) for COMPARISON only; reports the scale factors; never feeds back into a strategy
def paired_bootstrap(a_ret, b_ret, block="month", n=2000, seed=...) -> DataFrame   # d_cagr, d_sharpe, d_alpha, d_mdd, with 2.5/97.5 and P(d>0)
def ablation_report(ladder) -> Path                      # results/ablations/<ladder>/{paired.csv,report.md,figures/}
```
Rung overrides use `Strategy.variant(**kw)` -> returns a transient subclass instance (not registered, so the registry is not polluted); every rung evaluation is logged as a trial (MT-04) because ablations multiply comparisons.

Ladder `pead_ladder_v1` (mirrors library playbook section 4, mapped to what the repo can run today; rungs needing absent data are marked BLOCKED, not faked):
| rung | content | repo basis |
|---|---|---|
| L0 | benchmarks: VW, EW market (and matched-vol versions) | `market_benchmark.parquet` |
| L1 | long-only top-decile, equal weight, fixed 60-session hold, legacy cost | `steady_drift` with `risk_power`-free weights (`W.causal_inverse_variance` replaced by `mask.astype(float)`) |
| L2 | L1 + inverse-idio-variance sizing | `steady_drift` (sizing only) |
| L3 | L2 + risk throttle (`risk_target_vol=0.10`) + cash yield | `steady_drift_calm`/`steady_drift` family |
| L4 | long/short deciles, dollar/size/sector neutral | `balanced_leverage` family |
| L5 | + volatility filter / calm weighting | `calm_weighted`, `volatility_filter` |
| L6 | + exit rules (ATR/time stop) | **BLOCKED**: engine has no stop logic (MT-01 LA-19 contract exists, implement with an exits area) |
| L7 | + graph/spillover | **BLOCKED**: no relationship data (MT-14) |
| L8 | options expression of the same signal | options engine, bid/ask fills |
Risk ablations (separate ladder `risk_v1`): `cap_mode` in {priority, proportional, chronological} (flagging non-causal ones), `exposure_cap` in {entry_cohort (legacy), open_book, open_book_qstart}, `name_cap_frac` grid, `hold_sessions` in {5,10,20,40,60,120}, `risk_target_vol` in {None,.08,.10,.15}, `liquidity_cap_frac` in {.02,.05,.10}, long-only vs L/S, `return_basis` mktadj vs raw, cost multiplier (MT-06), beta overlay 0/.5.
Signal ablations (`signal_v1`): SUE flavour (MT-07), vol scaling variants (`vol.py`: `inverse_ivol_scale`, `low_vol_rank_scale`), conditional filters, reaction cell, momentum control (needs pre-event momentum feature -- compute from the panel with the `trailing_window` helper).
Report conventions copy the library: for each adjacent pair print `d_*` with CI, `event_set_report`, avg gross/net/beta of both, and the matched-vol versions; a pair is only called "improvement" when the paired CI of `d_alpha` (market-adjusted, HAC) excludes zero **and** the matched-vol `d_sharpe` CI excludes zero.

Beta/factor control: `alpha_hac` from MT-03 with `[vwretd, ewretd-vwretd]` until FF/momentum factors exist; each rung's alpha and beta shown, so "beta explains the edge" (library Trend row) is visible.

## 4. Data requirements
Present: events, panel, market benchmark, costs. Needed and absent: FF5+MOM daily factors and rf (MT-14, downloadable), SPY/QQQ/ETF series (BLOCKED for sample 1996-2013 for QQQ-leveraged ETFs which did not exist; SPY exists from 1993 but is not in the repo), historical S&P membership (BLOCKED), pre-event momentum (derivable), analyst revisions (derivable from `detu_epsus`, MT-07).

## 5. Framework mapping
`pead/eval/ablation.py`, `pead/eval/ladders/*.yaml`; `Strategy.variant()` in `pead/strategies/base.py`; CLI `python -m pead ablate <ladder> [--protocol v1]`; outputs under `results/ablations/`; comprehensive report gains a "paired differences" table replacing eyeballed side-by-side; harness is the home for the existing meta experiments (they become ladder files).

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-variant-grid (OV) provides `Strategy.variant`-style clones; unify as one helper. Paired bootstrap/matched-vol code lives in `pead/validation/` with MT-08/MT-11; `pead/eval/ablation.py` only orchestrates ladders.
- **NEW:** `pead/eval/ablation.py` (REF-ablation-harness), `pead/eval/ladders/{pead_ladder_v1,risk_v1,signal_v1}.yaml`, `Strategy.variant`, `tests/test_ablation.py`, `results/ablations/`.
- **CHANGE:** `pead/strategies/base.py` (variant + deep-copy of `Sizing` via `dataclasses.replace`; LOW); `pead/reporting/comprehensive.py` (add paired table; LOW); `pead/portfolio/combine.py::scale_to_vol` reuse for matched comparison (no change).
- **RESTRUCTURE:** migrate `meta/ablate*.py`, `meta/steady_placebo.py`, `meta/steady_validate.py` onto the harness (keep scripts as thin callers; retire `meta/analyze_sdb.py`'s private engine wrapper which duplicates `run_equity_backtest` logic -- risk: results must be re-verified to match `python -m pead run`, tolerance 1e-9 on NAV); ladders are YAML in-repo so they are versioned with the protocol.
- **IMPROVE:** (a) pairing check prevents the silent universe change the library warns about; (b) comparisons currently mix leverage and risk -- matched-vol output fixes the leaderboard's apples-to-oranges problem; (c) uses the same registry so ablation grids deflate DSR honestly.

## 6. Acceptance tests
- Identical strategy vs itself: all `d_*` = 0, CI = [0, 0], event sets equal.
- Synthetic known-lift: add a deterministic +x bp/day to rung B; `d_cagr` CI excludes 0 and recovers x within bootstrap error.
- Event-set mismatch: variant that drops 10% of events is flagged `unpaired`.
- Matched-vol: scaling a book by 2 yields identical matched Sharpe/alpha t; raw CAGR differs.
- Reproduction: `L2`/`L3` rungs reproduce `results/steady_drift` and `steady_drift_calm` daily returns to 1e-9 (harness equals `python -m pead run`).
- Paired bootstrap uses the same resampled blocks for both series (assert index equality).

## 7. Rollout plan
1. `variant`, `event_set_report`, `matched`, `paired_bootstrap` + unit tests. 2. `risk_v1` ladder (all runnable today). 3. `pead_ladder_v1` rungs L0-L5. 4. Port meta scripts. 5. Add blocked rungs as data/engines arrive (exits, graph, ETF/regime overlays from other areas).

## 8. Risks
- Many pairs = many comparisons: each is a registered trial; ladder-wide FDR (Benjamini-Hochberg over pair p-values) reported alongside raw intervals.
- Matched-vol scaling is ex-post and overstates comparability for books with different tail shape; show both raw and matched.
- 1996-2013 sample limits power; CIs will often include 0 -- that is the honest result.

## 9. Dependencies and implementation checklist
Depends on: MT-03 (stats), MT-04 (registry, protocol windows), MT-06 (cost model consistency), MT-07 (flavours). Used by: all equity/overlay areas.
- [ ] `Strategy.variant` and tests
- [ ] pairing, matching, paired bootstrap
- [ ] `risk_v1` ladder run and published
- [ ] `pead_ladder_v1` L0-L5
- [ ] port `meta/` ablations and verify equality with `pead run`
- [ ] BH correction across pairs
