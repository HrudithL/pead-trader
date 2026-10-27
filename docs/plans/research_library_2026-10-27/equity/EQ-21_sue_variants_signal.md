# EQ-21: Alternative SUE definitions (dispersion-floored, price-scaled, percent-of-consensus, seasonal, composite) as drop-in rank columns
- Status: planned | Priority: P0 | Effort: M | Source(s): library `strategies/07_MCRAVI8_WRDS_MINI.md` (https://github.com/mcravi8/PEAD-mini-WRDS), `11_KPH3802_FMP.md` (https://github.com/KPH3802/pead-backtest), `08_AARSH_DAYAL.md`, `12_DYLAN_JEON.md`, `foundations/01_SIGNAL_DEFINITIONS.md` sections 2-6

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Key finding from reading the repo: the repo's primary signal already IS the mcravi8 definition. `data/earnings/ibes_clean_1995_2014.parquet` computes `sue_analyst = (eps_actual_adj - consensus_meanest) / consensus_stdev` (verified on rows: (-0.04-(-0.07))/0.03 = 1.0), restricted to `consensus_numest >= 2` (verified min 2 in the analyst source) and `consensus_stdev >= 0.01` (verified min 0.01, i.e. a floor is already applied). So "port mcravi8's SUE" is a no-op; what remains useful is testing whether a different denominator or a composite carries different information, because docs/STEADY_DRIFT.md section 10 says only new information can lift the skill alpha (placebo: ~3.9 pts of ~7-8 pts alpha is surprise skill). Take: percent-of-consensus surprise (KPH/Aarsh/Dylan) with an absolute-denominator floor; price-scaled surprise (standard in the literature, not in the sources); seasonal SUE already stored as `sue_seasonal`; rank-average composite. Do NOT take: mcravi8's week x cap bucketing and long/short 30-day book (already covered, see skip list in `_README_B.md`), FMP/yfinance data, whole-sample winsorisation, the calendar-day entry rule.

## 2. Evidence grade & known defects in the source
mcravi8 grade B; reported IC 0.0183, daily alpha -0.000477 (t -1.92), Sharpe -0.333, avg gross 2.285x (author numbers, unverified; they show a weakly correct ranking does not survive costs and short borrow). KPH grade B: percent-surprise thresholds 5-10/10-20/20-30/30-50/50+, `abs(surprise_pct)` quintiles, no vintage estimates. Aarsh grade C: extreme positive surprise bucket need not drift most (nonlinearity), spurious near-zero denominators. Dylan grade E. Repo-side defect: `eps_basis_status` is `anndats_basis_assumed` for 193,389 of 194,316 analyst rows (only 446 confirmed), so price-scaling by an unadjusted price is basis-consistent only if that assumption holds; unit test a split-straddling example.

## 3. Exact rules
Event universe and timing are unchanged from steady_drift (same rows of `equity_vol_panel`; entry at day0 close; 60-session hold). Only the ranking column changes.
Inputs per event (from ibes_clean, joined on ibes_ticker + fpedats + anndats): A = `eps_actual_adj`, M = `consensus_meanest`, S = `consensus_stdev`, N = `consensus_numest`, stale = `consensus_staleness_days`, P = |prc| on the last session strictly before `anndats` (raw CRSP `prc`, same unadjusted basis as `anndats`).
Variants (suffix = column name written to the side table; each also gets `<name>_rank` from `pead.common.pit_rank.rank_pct(values, ann_quarter)` with values rounded to 8 decimals, the same recipe as `pead/prep/equity/build_deciles.py`):
- `sue_disp` control: copy of existing `sue_analyst` (assert equality to the events file to 1e-9).
- `sue_disp_f`: (A-M) / max(S, 0.05*|M|, 0.01). Floor stops tiny-dispersion events from dominating.
- `sue_px`: (A-M) / P. NaN if P < 1.0 (penny filter).
- `sue_pct`: (A-M) / max(|M|, E0) with E0 in {0.02, 0.05, 0.10} dollars (proposed constants; KPH's MIN_ABS_EPS value was not given in the library, so sweep it). Winsorise to [-5, 5] by a clip fixed in code, not by sample percentiles.
- `sue_seas`: existing `sue_seasonal` (needs `seasonal_n_prior_diffs >= 4`).
- `sue_comp`: mean of the available `_rank` columns among {sue_disp, sue_px, sue_seas} (at least 2 non-NaN), re-ranked PIT with `rank_pct`.
Filters (ablations, off by default): `N >= 3`; `stale <= 30` days; `|A| and |M|` both nonzero for `sue_pct`.
Trading rule for a variant X: identical to `SteadyDriftPlus` (`pead/strategies/equity/steady_drift.py`): mask = (X_rank >= 0.9) & (avg_dollar_volume_21d >= 2e6), weights `W.causal_inverse_variance(events, mask)`, `hold_sessions=60`, Sizing(base_unit_fraction 0.003, max_gross_leverage 0.80, name_cap_frac 0.01, cap_mode "chronological"), `risk_target_vol=0.12`, `long_only_cash=True`. No stops, no shorts. Costs by the engine (`COST_BPS` by liquidity quintile).

## 4. Data requirements & availability in THIS repo
Present: all ibes_clean columns above (verified), `data/raw_wrds/ibes/statsumu_epsus.parquet` (numup, numdown, medest, highest, lowest) if wanted, normalized_equity prc. Missing: analyst-level vintages in the repo's standard path (detu_epsus.parquet exists in raw, 14.6M rows, unused here), so "stale-estimate pruning per analyst" is possible but is L effort, out of scope for v1. Seasonal SUE requires 4+ prior seasonal diffs, so coverage is lower (`sue_source` seasonal rows have `sue_analyst` NaN and are not in the analyst-restricted event table; `sue_seas` is available only where `decile_events` carries `sue_seasonal`: verify non-null rate in the event panel before relying on it). The events table is analyst-only (`build_deciles.py` filters `sue_source == 'analyst'`), so variants are compared on the same event set.

## 5. Framework mapping
- Prep stage `sue_variants`: `pead/prep/equity/sue_variants.py`, outputs `event_features/sue_variants.parquet`, dep `["vol_features"]` (needs the final event rows). Key `(permno, day0_date, dup_rank)` (REF-event-key).
- Strategy file `pead/strategies/equity/steady_drift_signals.py`: class `SteadyDriftSignal(SteadyDriftPlus)` with class attribute `signal_col`, plus registered subclasses via `@register`: `steady_drift_plus_sue_disp_f`, `_sue_px`, `_sue_pct`, `_sue_comp` (family `equity-longonly`, parent `steady_drift_plus`, status `research`, `legacy_twin = False`, `required_columns = ("day0_date", signal_col+"_rank", "avg_dollar_volume_21d", "ivol_252d")`). `weights()` copies SteadyDrift.weights but reads `events[self.signal_col+"_rank"]`.
- Reuse: `W.causal_inverse_variance`, `rank_pct`, `Sizing`. Engine change: none.
- Params dict defaults: top_rank 0.9, min_dollar_volume 2e6, E0 0.05, floor_k 0.05, floor_abs 0.01.

## 5b. Build vs. change vs. restructure
1. NEW: `pead/prep/equity/sue_variants.py`; `pead/strategies/equity/steady_drift_signals.py`; `tests/test_sue_variants.py`; side table `data/event_features/sue_variants.parquet`.
2. CHANGE: `pead/prep/stages.py::STAGES` (+1 Stage); `pead/core/data.py::_load_events` via REF-event-feature-store; `pead/strategies/equity/steady_drift.py` optional refactor: lift the `weights()` body into a classmethod taking the rank column name so the new file does not duplicate it (regression risk: must keep the four steady_drift NAVs bit-identical; run `tests/test_steady_drift.py` plus a before/after NAV diff).
3. RESTRUCTURE: REF-event-feature-store; REF-event-key. A tiny `pead/strategies/signals.py` helper `rank_column(events, name)` that raises a clear error if the side-table column is absent (the live engine, `pead/live/features.py`, would otherwise silently see NaN).
4. IMPROVE: no test pins `sue_rank_pct` provenance; add one that recomputes the rank from `sue_analyst` and compares (guards the PIT rank against silent pandas-version changes: docs note pandas 2 vs 3 produce different deciles). Live path (`pead/live/data_sources.py`) uses yfinance surprise, not IBES dispersion SUE: ROADMAP #3 mismatch, note it per variant.

## 6. Acceptance tests
- Synthetic: 3 hand-made events reproduce each formula to 1e-12 (including S below floor, P below 1, A or M zero).
- No look-ahead: rank of an event in quarter q is unchanged if all events from quarters > q are deleted (reuse the pattern of `tests/test_fixes.py::test_pit_rank_uses_only_strictly_earlier_quarters`); price P taken at row index < `entry_row_idx` (assert on perturbation of later rows).
- Determinism: stage output hash stable across two runs.
- Control parity: `steady_drift_plus_sue_disp` equals `steady_drift_plus` NAV exactly.
- Split case: stock with 2:1 split between `anndats` and day0: P and A on the same basis (or event excluded with a counted flag).
- Columns present in `required_columns`; `python -m pead list` shows the new strategies.

## 7. Experiment plan
1. EQ-30 harness on each `_rank` column: quarterly Spearman IC against `hold_ret` and `ret_fwd_60d_mktadj`, by era, partial IC controlling `sue_rank_pct`. Baseline: control column.
2. Strategy runs of the four variants vs `steady_drift_plus` (12.07% / Sharpe 1.19 / MaxDD -22.07%) and `steady_drift` (10.59% / 1.16 / -20.00%). Ablations: top_rank {0.8, 0.9, 0.95}; filters N>=3, stale<=30; E0 sweep; composite weights.
3. Search 1996-2005 chooses E0 and floor_k; holdout 2006-2013 is read once. All variants count toward the FDR family in `results/_signal_tests.csv`.
4. Shuffled-signal placebo on the winning variant; skill must be >= the control's (+4.3 pts return, +3.9 pts alpha in docs).
5. Overlap with the other equity agent: revision/dispersion as separate information (statsumu `numup/numdown`) is NOT in this spec.

## 8. Expected outcome / hypotheses and risks
Hypothesis H1: variants are highly rank-correlated with `sue_disp` (expect Spearman > 0.6), so strategy-level differences are within noise, consistent with docs/STEADY_DRIFT.md section 9 (35 + 31 variants none beat the top-decile rule beyond noise). H2: `sue_px` and `sue_comp` give the only chance of modest incremental IC because price scaling fixes the tiny-dispersion blow-ups. Risks: multiple testing (6 variants x ablations), basis assumption in item 2, tie handling when many events share SUE exactly 0 (rank_pct uses average ranks; D10 membership then fluctuates), data ends 2013 and the signal is in-sample for the design.

## 9. Dependencies and implementation checklist
- [ ] REF-event-key + REF-event-feature-store
- [ ] `sue_variants` stage + parity assertion with `sue_analyst`
- [ ] Strategy classes + registry check (`python -m pead list`)
- [ ] EQ-30 harness (IC, placebo) available
- [ ] Unit tests; before/after NAV diff of existing four steady_drift strategies
- [ ] Runs on search window, then single holdout read; results appended to `results/leaderboard.md` only if status stays `research`
