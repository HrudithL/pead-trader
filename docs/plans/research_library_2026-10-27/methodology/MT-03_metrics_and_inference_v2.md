# MT-03: Canonical metrics and statistical inference (CAGR, alpha, PSR, DSR, bootstrap, clustering)
- Status: planned | Priority: P0 | Effort: M | Source(s): library `foundations/05_METRICS_AND_STATISTICS.md`, `comparisons/03_EVIDENCE_GRADING_AND_COMPARABLE_BACKTESTS.md` (return reporting standard), `reproduction/pead_reference.py::performance_from_nav`, `audits/01_EA1904_SOURCE_AUDIT.md` (Sortino, DSR with 6 variants); repo `pead/core/metrics.py`, `pead/core/alpha.py`, `pead/common/stats.py`, `pead/reporting/paper.py:1512-1660`, `pead/core/store.py`

## 1. Purpose & what we take from the library
One implementation of every number the repo prints: CAGR on a defined active window, daily-NAV Sharpe, Sortino (semi-deviation over all observations), drawdown including the initial capital peak, HAC factor alpha (market-only today, multi-factor when factor data exist), probabilistic Sharpe, deflated Sharpe with an *honest trial count* (MT-04 registry), block-bootstrap confidence intervals with cluster resampling (calendar month / earnings quarter), and per-year / per-era tables. Every number carries a `definition` tag so the seven "numbers that must never be mixed" (library comparisons/03) cannot be confused.

## 2. Gap analysis vs the repo today
Already correct (verified in code): daily NAV-based returns (`metrics.py::summarize` divides P&L by *prior* NAV); Sharpe sqrt(252); Sortino denominator `sqrt(sum(downside^2)/(n-1))` over all observations (matches the library's semideviation definition up to n-1 vs n); NW(5) HAC t-stats in `alpha.py::_ols_hac`; Fama-MacBeth quarter-clustered stats in `common/stats.py`; PSR and DSR formulas exist, but only inside `reporting/paper.py::deflated/dsr_at/inference_section`.

Defects and gaps, with evidence:
1. **Warm-up days in headline stats.** `summarize` uses all `len(nav)` days (`metrics.py:24`) while `alpha_stats` drops the flat warm-up (`alpha.py:43`) and `paper.py::active_returns` does too. Measured on `results/balanced_leverage/daily.csv.gz`: 4,908 days from 1995-01-03 to 2014-06-30, first non-zero return 1996-04-02 (316 flat days), last non-zero 2014-06-20. Reported Sharpe 1.092 / CAGR 5.38% vs active-window 1.129 / 5.76%. The leaderboard (`store.py::update_leaderboard`) therefore ranks on a diluted series, and books with a cash leg (steady_drift*, daily return non-zero from day 0) are on a *different* effective window from market-neutral books. Not comparable.
2. **Drawdown omits the starting NAV** (`metrics.py::drawdown(nav)` takes the NAV path only; `summarize` calls it at line 29); the library's `test_initial_day_drawdown_counted` shows the miss.
3. **No PSR/DSR/bootstrap in the framework or the leaderboard**; `paper.py:1548-1549` counts trials as `len(eq_runs)+n_sweep+...` from files, an ad-hoc count not owned by any registry.
4. **Alpha is CAPM/CAPM+size only, rf=0** (`alpha.py` docstring); no HML/RMW/CMA/MOM and no risk-free (library: multi-factor with HAC; Fama-French data absent from the repo -- `grep -ri famafrench pead` hits only `alpha.py`'s docstring-level size proxy).
5. **No clustering/bootstrap for portfolio-level uncertainty**; event-level Fama-MacBeth only.
6. **No per-leg, per-sampling labels**: summary has `sharpe` and `sharpe_fixed_capital` but nothing records sampling frequency or window.
7. `info_ratio`, `alpha_t` exist per run in `results/alpha_benchmark.csv` (script `scripts/alpha_benchmark.py`) but are not part of `summary.json`.

## 3. Exact specification
New module `pead/core/stats.py` (REF-metrics-v2) and a rewrite of `metrics.summarize` as a thin wrapper that delegates to it (same keys retained, new keys added; legacy keys keep their legacy meaning behind `*_fullcal` names so old pins survive).

```python
@dataclass(frozen=True)
class Window: start: Timestamp; end: Timestamp; label: str   # 'active' | 'search' | 'holdout' | 'era:2002_2007' | 'full_calendar'
def active_window(daily) -> Window            # first and last day with |daily_return|>0 AND (gross_exposure>0 OR cash leg); cash-yield books: first day with any open position
def returns_from_daily(daily, cap0) -> Series # pnl / prior NAV (as now)
def cagr(nav_start, nav_end, n_days, basis='trading252'|'calendar365')    # (end/start)**(252/n_days)-1
def arithmetic_annualized(r)                  # 252*mean -- exported only under that name; never called 'annualized_return'
def sharpe(r, rf=0.0, periods=252, ddof=1)
def sortino(r, mar=0.0, periods=252)          # sqrt(mean(min(r-mar,0)**2)) over ALL obs
def max_drawdown(nav, initial)                # prepend initial capital
def psr(sr_hat, T, skew, kurt, sr_bench=0.0)  # Bailey-Lopez de Prado; sr per period; kurt is NON-excess
def expected_max_sr(n_trials, var_sr_trials)  # (1-g)*Z(1-1/N)+g*Z(1-1/(N e)), g=0.5772156649
def dsr(sr_hat, T, skew, kurt, n_trials, var_sr_trials)   # PSR with sr_bench = expected_max_sr
def block_bootstrap(r, block='month'|'quarter'|int, n=2000, seed, stat=...)   # circular blocks of calendar months, returns draws
def paired_block_bootstrap(r_a, r_b, ...)     # for MT-09 ablations; same resampled dates for both
def alpha_hac(r, factors, rf, lags='auto')    # Newey-West lag = floor(4*(T/100)**(2/9)) default; user override; returns alpha_ann, t, factor loadings, R2
def yearly_table / era_table(daily, windows)  # existing yearly_table kept
def minimum_track_record_length(sr, skew, kurt, sr_bench, alpha=0.05)
```
Rules: (i) `annualized_return` always means CAGR over the **active window**; the old whole-calendar value is stored as `annualized_return_fullcal`; (ii) `n_days_active`, `window_start`, `window_end`, `sampling='daily'` written to summary; (iii) DSR default `n_trials` comes from the registry (MT-04), never from file counts; default `var_sr_trials` = variance of the per-period Sharpes of *all* registered trials in the same family; a documented fallback (`n_trials=len(eq_runs)`) is allowed only when the registry is empty and the result is then labelled `dsr_trials_basis='file_count'`; (iv) bootstrap default: circular blocks of 3 calendar months (matches the prior art in git-ignored `earnings_build.tmp/validation.py::evaluate`, repetitions 1000+; use 2,000), seed fixed in the manifest; (v) report CI for CAGR, Sharpe, max drawdown, alpha; (vi) HAC lag default = Bartlett with `floor(4*(T/100)**(2/9))` (~9 for T=4,500) vs today's fixed 5 -- both recorded (`alpha_t_nw5`, `alpha_t_hac_auto`).

Formulas (as in `paper.py:1512-1520`, kept bit-compatible): `z = (s - sr0) * sqrt(T-1) / sqrt(1 - skew*s + (kurt-1)/4*s**2)`, `s` per-period Sharpe; DSR = `Phi(z)`.

Summary additions: `window_start, window_end, n_days_active, cagr, arith_ann_252, sharpe_active, sortino, max_drawdown_incl_initial, psr_0, dsr, dsr_n_trials, sharpe_ci95, cagr_ci95, mdd_ci95, alpha_capm, alpha_t, alpha_ci95, skew, kurtosis`.

## 4. Data requirements
Existing `daily.csv.gz` per run. Alpha: `vwretd`, `ewretd` from `market_benchmark.parquet` (present). Multi-factor alpha needs FF5 + momentum daily factors and a daily risk-free (REF-factor-data, REF-tbill-real): free from Kenneth French's data library and FRED -- **external download, not in repo** (explicit permission needed before fetching; BLOCKED until downloaded). Until then `alpha_hac` runs with `[vwretd, smb_proxy]` and the report labels it "market-only".

## 5. Framework mapping
`pead/core/stats.py` (new) <- `metrics.py`, `alpha.py`, `common/stats.py` (Fama-MacBeth stays), `paper.py` deflated functions (moved; paper imports them). CLI: `python -m pead stats <name...> [--bootstrap 2000] [--trials-from registry]` writes `results/<name>/inference.json` and a table block appended to the per-strategy report (`reporting/single.py`) and `results/leaderboard.md` (new columns `cagr_active`, `sharpe_lo95`, `psr`, `dsr`). Leaderboard sorting key becomes `dsr` or `sharpe_lo95` (configurable) in `store.update_leaderboard`.

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** portfolio-level statistics live in `pead/core/stats.py` (REF-metrics-v2); event-level numpy-only IC/clustered-OLS/BH-FDR is EQ's REF-stats-nodeps (`pead/core/event_stats.py`); both must import the single Newey-West routine `alpha._ols_hac` (do not copy it a third time). REF-tbill-real is OV's name for the real T-bill series.
- **NEW:** `pead/core/stats.py`; `tests/test_metrics.py`; `scripts/restat_all.py` (recompute inference for every saved run from `daily.csv.gz`, no re-backtest needed); `results/inference_all.csv`.
- **CHANGE:** `pead/core/metrics.py::summarize` (delegate; add active-window keys; keep legacy keys; regression risk: HIGH for any test or doc quoting old `annualized_return`/`sharpe`; `tests/test_framework.py` pins final NAV not Sharpe, but `results/leaderboard.md`, `docs/FIXES.md` tables, `docs/paper` fragments change -> regenerate with `python -m pead run all` and `python -m pead paper`; document "active-window restatement: balanced_leverage 1.09 -> 1.13, 5.38% -> 5.76%" as a presentational, not performance, change); `pead/core/metrics.py::drawdown` (initial capital; small effect on first-day losses); `pead/core/alpha.py::alpha_stats` (call `stats.alpha_hac`; keep key names); `pead/core/store.py::update_leaderboard` (new columns/order; leaderboard markdown schema change); `pead/reporting/paper.py::inference_section` (import from `stats`, use registry trials -- changes DSR numbers in the paper, regression risk MEDIUM: DSR will fall if the registry's honest count exceeds the file count).
- **RESTRUCTURE:** consolidate three places that each define annualisation (`metrics.py`, `alpha.py`, `attribution.py:99-112` which uses `len(d)/252` and `1e7` hard-coded initial capital) into `stats.py`; stop hard-coding `1e7` (use `INITIAL_CAPITAL` from `common/paths.py`); `ERAS` dict moves to `stats.py` and is also the default `era_table`.
- **IMPROVE:** (a) leaderboard currently ranks raw max Sharpe across all `validated|research` runs with no uncertainty (`store.py:112-118`); (b) steady_drift* cash legs earn T-bill rates typed from memory (`rates.py`) -- Sharpe/alpha are computed with rf=0 on top of a cash return, so excess returns are overstated relative to the library convention; add `sharpe_excess_rf` once REF-tbill-real lands; (c) store skew/kurtosis so PSR is reproducible from `summary.json`.

## 6. Acceptance tests (`tests/test_metrics.py`)
- Constant +0.1%/day: `cagr == 1.001**252-1`, `arith_ann_252 == 0.252`, they differ; library test 7 (first-day loss visible in drawdown).
- Cross-check against `pead_reference.performance_from_nav` (MT-10): total_return, annualized_return, sharpe match to 1e-12 on the same NAV; drawdown matches after the initial-capital fix.
- `psr`/`dsr` against a hand-computed example (Bailey-Lopez de Prado numerical example in the docstring) and against `paper.py` outputs for `calm_weighted` (psr 0 within 1e-9 before the trial count changes).
- Bootstrap: seed-stable; CI of a known iid normal sample covers the truth in >= 93% of 200 simulations (tolerance test, `slow`); block bootstrap CI wider than iid for an AR(1) series.
- Active window of `balanced_leverage` starts 1996-04-02; of `steady_drift_plus` starts at its first open position (not 1995-01-03).
- Round-trip: `restat_all.py` reproduces `results/alpha_benchmark.csv` alpha and t within rounding for `alpha_t_nw5`.

## 7. Rollout plan
1. Implement `stats.py` additively (no caller changes) + tests. 2. `restat_all.py` over the 66 result folders, publish `inference_all.csv`. 3. Switch `summarize` to the active window behind env `PEAD_METRICS_WINDOW=active` default-off, diff leaderboards, then flip default and regenerate reports. 4. Switch leaderboard ordering (MT-04). 5. Multi-factor alpha when factors are downloaded.

## 8. Risks
- Restatement confusion: two Sharpes in circulation; mitigated by the `*_fullcal` keys and a one-paragraph note in `docs/FIXES.md`.
- DSR depends on the trial count; an under-count makes DSR meaningless -- hence MT-04 owns the registry and refuses to emit DSR from an empty one.
- Bootstrap on a 1996-2013 sample with 3 eras is a single regime set; CI will be wide; that is the point.

## 9. Dependencies and implementation checklist
Depends on: REF-trial-registry (MT-04) for DSR defaults; REF-tbill-real, REF-factor-data (MT-14) for excess/multi-factor. Blocks: MT-04 gates, MT-08, MT-09, MT-12.
- [ ] `stats.py` functions + docstring examples
- [ ] `tests/test_metrics.py` incl. reference cross-check
- [ ] `restat_all.py`, `inference_all.csv`
- [ ] `summarize` active-window switch + legacy keys
- [ ] drawdown with initial capital
- [ ] `alpha_stats` delegation, HAC auto lag
- [ ] paper.py imports from `stats`
- [ ] leaderboard columns/order
