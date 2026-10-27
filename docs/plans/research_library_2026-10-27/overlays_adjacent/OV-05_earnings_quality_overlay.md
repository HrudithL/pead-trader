# OV-05: Earnings-quality overlay for the SUE book (profitability proxy now; cash-flow/accrual blocked)

- Status: planned (partly BLOCKED on data) | Priority: P2 | Effort: L | Date: 2026-10-27
- Sources: library `strategies/18_CASH_FLOW_QUALITY_MOMENTUM.md` (including "PEAD adaptations"), `strategies/16_FINLAB_TEN_SYSTEM_FAMILY.md` (FCF / quality-growth / value+quality breadth systems), `foundations/02_EVENT_TIME_PIT.md` themes via `audits/03_BACKTEST_FAILURE_MODES.md` section 1.

## 1. Thesis & what we take from source (and what we do NOT take)

Thesis (hypothesis from the source's own "PEAD adaptations" paragraph, not a finding): a positive surprise accompanied by real operating cash flow / low accruals is more persistent than one driven by accrual adjustments, so drift should be larger for high-quality beats.

Take: (i) the point-in-time lineage discipline (a fundamental becomes usable only after its filing / report date, with amendment history), (ii) the formula `accrual = (NI - CFO) / average_assets`, `CFO strength = CFO / average_assets`, winsorize 1-99% per formation date then z-score, (iii) the frozen-holdout and manifest habit.
Do NOT take: the 45 present-day large-cap universe, annual-only 10-K data, the momentum+quality composite as a stock-picking strategy, the 22.7% vs 13.6% holdout CAGR, or the FinLab "FCF breadth" ETF system (its 31.5% CAGR is the shared QQQ-gate ETF engine).

## 2. Evidence grade & known defects in source

Grade B (library): good methodology, weak evidence. Holdout alpha t-statistic 1.13 ("not statistically significant", author's own words); survivorship acknowledged; financials excluded; yfinance prices and a 2026 fixed universe; 111 of 138 post-hoc comparisons agree (selected robustness). It is not a PEAD test at all. The FinLab FCF/quality systems share MDD -21.1% and OOS CAGR with the others (same risk engine), so they carry no factor-specific evidence. Net: the only admissible statement is "worth a test"; expected effect size unknown.

## 3. Exact rules (codeable)

Per event i (permno, day0_date, gvkey via `data/events/equity_events_1996_2013.parquet`; the vol panel has no gvkey: join on `permno, day0_date` and assert 1:1, resolving `n_events_same_permno_day0 > 1` by dropping ambiguous rows to NaN):
Availability of a Compustat quarter q (table `data/raw_wrds/comp/fundq.parquet`, columns verified: `gvkey, datadate, rdq, fyearq, fqtr, atq, ceqq, saleq, niq, epspxq`): `avail_q = rdq` if present (85.1% non-null, verified) else `datadate + 90 days` (conservative; rule chosen before looking at returns). Exclude the event's OWN quarter: use only quarters with `avail_q <= day0_date - 5 calendar days` (the just-announced quarter's balance sheet may not be public at the press release).
Proxy factor available now (profitability, not cash flow):
```
ROA_ttm   = sum(niq over last 4 usable quarters) / mean(atq over those 4)
SALEG_yoy = saleq_last / saleq_(last-4) - 1
Q_i       = causal percentile of ROA_ttm among events in the previous 365 days (same pool function as OV-02)
```
BLOCKED factor (what the source uses): operating cash flow and accrual intensity. Exact missing input: `oancfy` (YTD cash flow from operations) in `comp.fundq` (quarterly CFO = `oancfy - lag(oancfy)` within `fyearq`, Q1 = `oancfy`), plus `atq`, `niq` already present; the current pull stops at the 14 columns above (manifest `data/metadata/wrds_pull_manifest_compustat.csv`). Once pulled: `CFO_ttm/avg_assets`, `ACC_ttm = (NI_ttm - CFO_ttm)/avg_assets`, `quality_z = z(CFO_ttm/AA) - z(ACC_ttm)`, winsorized 1-99% per causal pool.
Variants (fixed in advance): Q1 filter: drop SUE-top-decile events with `Q_i < 0.2` (bottom quintile quality); Q2 tilt: weight *= `(0.75 + 0.5*Q_i)`; Q3 (after CFO pull) same with `quality_z` percentile. V0 diagnostic first: interaction regression `ret_fwd_60d_mktadj ~ sue_rank_pct * Q_i`, quarter FE, clustered by `ann_quarter`; also for the mechanism claim: beat quality split (high vs low accrual).
Financials (SIC 6xxx) should be excluded from accrual tests as in the source; the repo has `ff12_sector`: exclude its financials group for the accrual/CFO variant (check the actual label strings in the column first).

## 4. Data requirements & availability in THIS repo

Available: `fundq` with `rdq, atq, niq, saleq` (1995-01 to 2014-06, verified by reading the file); `funda` (annual: `at, ceq, sale, ni, epspx`). Precedent for PIT matching and its simplification: `pead/prep/equity/ff_adjustment.py` (130-day lag on annual data). BLOCKED: operating cash flow (`oancfy`), hence accruals and CFO strength: needs a new WRDS pull (cannot be proxied honestly; balance-sheet accruals need `act, che, lct, dlc, txp, dp` also absent). `rdq` as the availability date is itself a vendor field with 15% missing; vendor restatements are not tracked (Compustat quarterly is "as-restated" in many cases: PIT risk is higher than the SEC-lineage the source uses; flag).

## 5. Framework mapping

- Files: `pead/prep/equity/fundamentals_pit.py` (new prep stage producing `data/fundamentals_pit.parquet`: one row per gvkey-quarter with `avail_date`), `pead/core/event_features.py` (add `roa_ttm`, `quality_rank`), `pead/strategies/equity/steady_drift_quality.py`.
- Registration: `steady_drift_plus_qfilter`, `steady_drift_plus_qtilt` (and CFO variants after pull).
- Composition: subclass `SteadyDriftPlus`, `extra_features=("quality_rank",)`.
- Params: `q_cut=0.2`, `tilt_floor=0.75`, `pit_lag_days=5`, `fallback_lag_days=90`.
- Reuse: `pead.prep.stages` dependency-graph pattern (see `prep/equity/vol_features.py`), `W.causal_inverse_variance`.

## 5b. Build vs. change vs. restructure

(1) NEW
- `pead/prep/equity/fundamentals_pit.py` + stage registration in `pead/prep/stages.py`; tests with fixture rows (original vs amended record where available).
- Feature functions + `steady_drift_quality.py`.
(2) CHANGE
- `pead/prep/stages.py` stage list (add stage, update `python -m pead prep --list` expectation). Regression risk: none for existing stages (new output file).
- WRDS pull definition for Compustat `fundq` (manifest `wrds_pull_manifest_compustat.csv` records a fixed column list): add `oancfy` (and `dpq` if desired). Re-pull changes the raw master: follow REPO_MAP data policy (real directory, hash manifest, `D:` master first).
(3) RESTRUCTURE
- REF-event-features (shared per-event feature registry) and REF-fundq-pull (PIT fundamentals table with explicit `avail_date`, usable by OV-05/OV-07 and the existing book-to-market code in `ff_adjustment.py`, which currently re-implements its own 130-day PIT rule).
- REF-variant-grid, REF-validation-suite.
(4) IMPROVE
- Identifier joins: vol panel lacks `gvkey`; carry it through `vol_features.py` so feature code does not join on `(permno, day0_date)`.
- Document the PIT-lag convention in one place (currently implicit in `ff_adjustment.py` docstring).
- `raw_data_manifest.json` update with the new pull's hash.

## 6. Acceptance tests

- PIT: a quarter with `rdq` after `day0_date - 5` is never used (synthetic fixture); rewriting later quarters leaves the feature unchanged.
- Own-quarter exclusion test: the quarter whose `rdq == day0_date` is excluded.
- Fallback lag test when `rdq` is NaN.
- Join uniqueness: 1:1 mapping assertion with counted ambiguous rows reported.
- Quarterly CFO differencing from YTD (after pull): Q1 = YTD; Q2 = YTD2 - YTD1; handles missing quarter as NaN.
- Determinism; bit-identical NAV for unmodified strategies.

## 7. Experiment plan

Baseline `steady_drift_plus` (12.07% / 1.19 / -22.07%). Stage 1 (now): V0 diagnostic with ROA proxy only; stop if no robust interaction (same sign in all three eras, clustered |t| >= 2). Stage 2 (after pull): CFO / accrual interaction. Variants Q1-Q3 only; acceptance rule as OV-02/03; control: random-drop of the same fraction; placebo SUE; OV-01 stress; report by size quintile as well (the pooled number can hide opposite signs; do not conclude from it alone). Holdout: none is clean; if the pull extends past 2013 (ROADMAP 7) use post-2013 as the first real holdout and freeze code before looking.

## 8. Expected outcome and honest risks

Low confidence. ROA is not the source's factor; PIT quality of Compustat quarterly is weaker than SEC lineage; each filter cuts breadth (the one lever the repo found useful); correlation of quality with low vol which the sizing already favours. Risk of mining: three eras x two sectors x several definitions invites false positives; limit to the variants listed. Likely outcome: no effect or a size-specific effect too small to survive the placebo.

## 9. Dependencies and implementation checklist

Depends on: OV-01, OV-02 (shared pool-rank function), REF-event-features, REF-fundq-pull, REF-validation-suite; data pull for CFO.
- [ ] Decide and request WRDS pull of `comp.fundq.oancfy` (owner approval: touches raw master)
- [ ] `fundamentals_pit.py` stage + fixtures
- [ ] Join gvkey into events (assert 1:1)
- [ ] V0 diagnostic with ROA proxy
- [ ] Only if go: Q1/Q2 backtests, placebo, stress, era table
- [ ] CFO/accrual variants after pull
