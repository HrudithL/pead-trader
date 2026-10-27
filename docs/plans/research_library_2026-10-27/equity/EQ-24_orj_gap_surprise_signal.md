# EQ-24: Price-based surprise (overnight-gap "ORJ" proxy) as a second ranking column and a SUE+gap composite
- Status: planned | Priority: P1 | Effort: M | Source(s): library `strategies/23_LAN_ORJ_INVESTOR_ATTENTION.md` (Lan, Xie, Mi, Zhang, Int. Rev. Financial Analysis 95, art. 103460, 2024; https://www.sciencedirect.com/science/article/abs/pii/S1057521924003922), `strategies/17_ALBERT_PEREZ_POST_EARNINGS_INTRADAY.md`, `foundations/01_SIGNAL_DEFINITIONS.md` section 9

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Lan et al. argue that the market's own overnight reaction is a simple earnings-surprise measure that avoids sourcing EPS consensus. The repo already holds IBES surprises, so the question is not "replace SUE" but "does the gap carry information about 60-session drift that the analyst SUE does not". Take: gap and a volatility-normalised gap as a rank column; a rank-average composite with SUE; the sign asymmetry hypothesis (positive surprises matter more) which is automatically honoured by our long-only top-decile rule. Do NOT take: the paper's unavailable ORJ transformation (explicitly "not accessible" in the library), the quarterly rebalanced "dozens of stocks" portfolio, the 6.78% per quarter excess claim (average quarterly excess in China A-shares, not a US CAGR; do not multiply by four), their attention story (tested separately in EQ-25).

## 2. Evidence grade & known defects in the source
Library grade D/E: only abstract and publisher excerpts, no code, Chinese market with price limits, retail dominance and short-sale constraints; the gap formula `Open(first reaction session)/Close(previous regular session) - 1` is the only part read. US large caps may differ. Repo-side: (a) the gap is information the strategy does not capture (entry is the day0 close), so any value comes from the gap's rank predicting POST-entry returns; (b) day-0 timing is contaminated for 1996-98 (`anntims_unknown` 95%/85%/27%), so a 2-session variant is included.

## 3. Exact rules
Features from EQ-22: `gap`, `gap_z`, `er2`, `d0_excess`. Market adjustment of the gap: no index open exists, so `gap_adj = gap - median(gap of all events whose day0_date == this day0_date)` when that day has >= 20 events, else `gap - median(gap over events in the same ann_week)` (week = ISO week); documented as proxy.
Columns written by the prep stage (side table `event_features/orj.parquet`): `orj_gap = gap_adj`, `orj_z = gap_z`, `orj_er2 = er2`, each with PIT rank `*_rank = rank_pct(values, ann_quarter)` (expanding pool of strictly earlier quarters, `pead/common/pit_rank.py`). Events with `gap_ok=False` get NaN.
Composite: `orj_comp_rank = rank_pct( mean(sue_rank_pct, orj_z_rank) )` requiring both non-NaN.
Strategies (all derive from `steady_drift_plus`, long-only, 60-session hold from the day0 close, same sizing):
- `orj_only`: top decile of `orj_z_rank` (>= 0.9).
- `sue_and_orj`: `sue_rank_pct >= 0.9` AND `orj_z_rank >= 0.7` (intersection).
- `comp`: `orj_comp_rank >= 0.9`.
- `sue_given_weak_gap` (falsification of "price already knew"): `sue_rank_pct >= 0.9` AND `orj_z_rank < 0.5`.
Hold periods 1/5/20/60 sessions are evaluated only by the harness (event study of post-entry mktadj returns), not as separate strategies; the only traded horizon is 60 (D_results_A shows 40 and 90 are worse).
Costs per engine; no shorts.

## 4. Data requirements & availability in THIS repo
Available: CRSP `openprc`, `prc`, `cfacpr` in `data/raw_wrds/crsp/dsf_*.parquet`; ~98% openprc coverage in sampled years. Missing: index overnight return (so the market adjustment is a same-day cross-section median proxy), intraday/pre-market quotes, events without IBES consensus (the repo's event table is analyst-only; extending to the 34.7k `sue_source == 'none'` rows would be a new universe and needs its own day0 resolution: out of scope, flag as follow-up, it is where a price-based surprise would add true breadth), Chinese-market features.

## 5. Framework mapping
- Prep stage `orj_features`: `pead/prep/equity/orj_features.py` (reads `event_features/reaction.parquet` and `sue_rank_pct` from the vol panel; writes `event_features/orj.parquet`). Registry entry in `pead/prep/stages.py::STAGES`, deps `["reaction_features"]`.
- Strategy file `pead/strategies/equity/steady_drift_orj.py`: `SteadyDriftOrj(SteadyDriftPlus)` with `rule` in {orj_only, sue_and_orj, comp, sue_given_weak_gap}; registered names `steady_drift_plus_orj_only`, `_sue_and_orj`, `_comp`, `_weak_gap`. `required_columns += ("orj_z_rank","orj_comp_rank")`.
- Reuse `W.causal_inverse_variance`, `rank_pct`, `Sizing` from `steady_drift_plus`. Engine change: none.
- Params: `orj_top=0.9`, `orj_inter=0.7`, `min_day_events=20`.

## 5b. Build vs. change vs. restructure
1. NEW: `pead/prep/equity/orj_features.py`; `pead/strategies/equity/steady_drift_orj.py`; `tests/test_orj.py`.
2. CHANGE: `pead/prep/stages.py::STAGES`; `pead/core/data.py::_load_events` (side-table join, REF-event-feature-store). No existing strategy changes.
3. RESTRUCTURE: REF-event-feature-store, REF-event-key, REF-dsf-reader (all from EQ-22). Extract SteadyDrift mask helper (see EQ-23, `SteadyDrift._base_mask`).
4. IMPROVE: `rank_pct` is expanding-pool and cannot absorb market-wide regimes (docs/FIXES.md design caveat): a gap rank in a crash quarter ranks almost every event "low gap"; test a trailing-8-quarter pool variant (`pit_rank.rank_pct(mode=...)` has only two modes today) and add it as a third mode `trailing_k` without changing defaults. Also: 4th-quarter 2008 and 1998-01 zero-gap clusters (limit/rounding) need tie handling tests.

## 6. Acceptance tests
- Synthetic: scripted open/close paths; gap and gap_adj exactly as specified including the day-median fallback; z uses only prior 60 sessions (perturb row r and r+1, assert unchanged).
- No look-ahead: `orj_z_rank` for quarter q unchanged if later quarters deleted (same fixture as `test_pit_rank_uses_only_strictly_earlier_quarters`); the strategies never read `ret_fwd_*`.
- Determinism; NaN gap events excluded and counted; `comp` requires both inputs.
- Split-day case: gap computed on cfacpr-adjusted prices equals the unsplit-equivalent gap.

## 7. Experiment plan
1. Harness (EQ-30): IC of `orj_*_rank` vs `hold_ret` and vs `ret_fwd_60d_mktadj` by era; partial IC given `sue_rank_pct`; double sort 5x5 SUE rank x gap rank, with market-adjusted post-entry returns; positive vs negative gap asymmetry; `timing_known` subset.
2. Strategy runs vs `steady_drift_plus` (12.07% / 1.19 / -22.07%) with the gate from EQ-30. Search 1996-2005 chooses `orj_inter` in {0.5, 0.7, 0.8}; holdout 2006-2013 read once.
3. Placebo: shuffle `orj_z_rank` within quarter (keep SUE real); shuffle SUE (keep gap real).
4. Event study horizons 1/5/20/60 after entry for each rule.

## 8. Expected outcome / hypotheses and risks
H1: `gap` rank correlates 0.3-0.5 with SUE rank (the day-0 reaction is partly SUE); H2: partial IC given SUE is small positive (< 0.01) because the earlier in-repo day-0 filters (EQ-23, D_results_A) moved nothing; H3: `orj_only` underperforms SUE-only. The honest prior is that this adds little on the analyst-covered universe and could matter only for uncovered events (follow-up). Risks: gap is the reaction itself so continuation vs reversal sign can flip by regime (2008 reversals); mis-dated early years; extra 4 variants enlarge the FDR family.

## 9. Dependencies and implementation checklist
- [ ] EQ-22 (reaction features, loader) complete
- [ ] `orj_features` stage + tests
- [ ] Strategy file, registry check
- [ ] Optional `trailing_k` ranking mode in `pit_rank.py` with unchanged defaults and a test
- [ ] EQ-30 harness runs; results logged to `results/_signal_tests.csv`
- [ ] Decision memo: keep as signal for EQ-29 composite, or drop
