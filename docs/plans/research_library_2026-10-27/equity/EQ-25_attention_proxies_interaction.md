# EQ-25: Investor-attention proxies (abnormal volume, analyst coverage, size) as interaction and sizing tilts on the top-SUE-decile book
- Status: planned | Priority: P2 | Effort: M | Source(s): library `strategies/23_LAN_ORJ_INVESTOR_ATTENTION.md` (Lan et al. 2024, IRFA 95:103460), `foundations/07_ACADEMIC_CONTEXT.md` (limited attention / anchoring / risk), `foundations/01_SIGNAL_DEFINITIONS.md` section 8 (abnormal volume)

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Lan et al. find attention mediates the surprise-to-drift link in China (unexpectedly good news attracts attention and the delayed flow amplifies drift). The classic US reading (library 07: limited attention, neglected firms) predicts the opposite: more drift when attention is LOW. The two make opposite predictions, which makes this a clean two-sided test and the reason to pre-register direction-agnostic reporting. Take: attention proxies that exist in WRDS (abnormal volume, analyst count, size), interaction with the surprise rank, sign asymmetry. Do NOT take: Google/SEC-search attention, retail-order-flow, social media (not in repo), the paper's own attention index (not accessible), or any China-calibrated threshold.

## 2. Evidence grade & known defects in the source
Library grade D/E (abstract-only, no code). The mechanism is a hypothesis. Repo-side defects: CRSP volume for NASDAQ stocks is inflated pre-2004 (double counted dealer volume), so a firm-normalised ratio is used and `vol_era` flagged; `consensus_numest` is the count of estimates in the last IBES statistical period, not true coverage; docs/STEADY_DRIFT.md already shows the book is small/mid-cap and calm-weighted (the neglect channel is already partly in play).

## 3. Exact rules
Proxies (per event, known at day0 close):
- `abn_vol` = vol[day0] / median(vol[day0-60 : day0-1]) (EQ-22); `abn_vol_rank` = PIT rank within `size_quintile` (prior-quarter pool, same recipe as `pead/prep/equity/vol_features.py::prior_quarter_ranks`).
- `cov_n` = `consensus_numest` (ibes_clean); `cov_rank` PIT rank within size quintile.
- `size_quintile` (exists).
Interaction study (harness): within `sue_rank_pct >= 0.9`, tercile by each proxy; compare post-entry 60-session mktadj return, by era and by sign of `d0_excess`. Regression: `hold_ret = a + b1*sue_c + b2*attn + b3*sue_c*attn + size FE + sector FE` with two-way clustering (EQ-30).
Strategy ablations (not production; derive from `steady_drift_plus`): `attn_hi` (top-decile SUE and `abn_vol_rank >= 0.67`), `attn_lo` (`<= 0.33`), `neglect_tilt` (weight x (1 + 0.5*(0.5 - cov_rank)) clipped [0.5, 1.5], reweighted so mean weight over the mask is unchanged). Everything else as steady_drift_plus: 60 sessions, causal inverse variance, 12% risk target, long-only.

## 4. Data requirements & availability in THIS repo
Available: CRSP vol (dsf), `consensus_numest`, `size_quintile`, `avg_dollar_volume_21d`. Missing, BLOCKED: institutional ownership (needs Thomson 13F: not in `data/raw_wrds`), search-volume/news counts (RavenPack/Google), retail order flow, real coverage history (IBES detail file `detu_epsus.parquet` is in raw and could give analyst counts by month: L effort, not v1). Proxy accepted for v1: abnormal volume + analyst count + size.

## 5. Framework mapping
- Prep stage `attention_features` (`pead/prep/equity/attention_features.py`) reading `event_features/reaction.parquet` (abn_vol) and ibes_clean; writes `event_features/attention.parquet`.
- Strategy file `pead/strategies/equity/steady_drift_attention.py` with the three ablation strategies (status `research`, parent `steady_drift_plus`, `legacy_twin=False`).
- Reuse: `prior_quarter_ranks` from `pead/prep/equity/vol_features.py` (verify it is importable without running the module-level script: if not, move it, see 5b), `W.causal_inverse_variance`.
- Engine change: none.

## 5b. Build vs. change vs. restructure
1. NEW: `attention_features.py`, `steady_drift_attention.py`, `tests/test_attention.py`.
2. CHANGE: `pead/prep/stages.py::STAGES`; `pead/core/data.py::_load_events` (REF-event-feature-store).
3. RESTRUCTURE: `prior_quarter_ranks` lives inside `pead/prep/equity/vol_features.py` next to executable top-level code; extract to `pead/common/pit_rank.py` (`rank_pct_within_group_prior_quarter`) so two stages share it (regression risk: `vol_features` output hash must stay identical; compare `equity_vol_panel.parquet` before and after). REF-event-feature-store.
4. IMPROVE: no persisted record of the dispersion/coverage diagnostics (docs/ROADMAP #5 notes scripts 36-38 print to console only); this spec's harness run writes tables to `results/_signal/`. NASDAQ volume regime break is not handled anywhere in the repo; add `vol_era` column.

## 6. Acceptance tests
- Synthetic: scripted volume path gives exact `abn_vol`; median excludes day0; fewer than 30 valid prior days -> NaN.
- No look-ahead: perturbing vol at rows >= day0+1 changes nothing; rank pool uses only earlier quarters (test as in `test_fixes.py`).
- Weight-preservation: mean weight over the mask under `neglect_tilt` equals the untilted mean within 1e-9.
- Determinism, row-count parity of loader join, existing pins green.

## 7. Experiment plan
Pre-registered primary test: coefficient b3 on `sue_c * abn_vol_rank` (and on `cov_rank`) over the search window 1996-2005, two-sided; holdout 2006-2013 read once; Benjamini-Hochberg across all interaction tests (EQ-30). Strategy ablations are run only if the interaction is significant after FDR in the search window. Baselines: `steady_drift_plus` 12.07% / 1.19 / -22.07%. Placebo: shuffle the attention proxy within quarter. Sub-sample: exclude 1996-98.

## 8. Expected outcome / hypotheses and risks
H1 (US neglect): drift is larger for low-coverage, low-abnormal-volume names; H2 (Lan): the opposite for abnormal volume. Most likely both effects are small and confounded with size and idiosyncratic volatility, which the book already weights by (inverse variance). Risks: collinearity of abn_vol with |d0 return| (volume spikes with big moves; control for `d0_excess` magnitude); small effective sample per tercile; high multiple-testing load; the result may simply restate the small-cap premium (ROADMAP #1). Treat a null as the expected case.

## 9. Dependencies and implementation checklist
- [ ] EQ-22 `abn_vol`
- [ ] EQ-30 harness (clustered OLS, FDR)
- [ ] Extract `prior_quarter_ranks` (hash-identical vol panel)
- [ ] `attention_features` stage and tests
- [ ] Interaction report; strategies only if FDR-significant on search window
