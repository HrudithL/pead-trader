# MT-12: Run manifest, evidence grade and standardized result row (results schema v2)
- Status: planned | Priority: P1 | Effort: M | Source(s): library `comparisons/03_EVIDENCE_GRADING_AND_COMPARABLE_BACKTESTS.md` (A-E completeness grade, "seven numbers never to mix", standardized result row, approval checklist), `reproduction/01_REPRODUCTION_PLAYBOOK.md` sections 0 and 9 (manifest, output directory), `reproduction/04` section 8 (outputs necessary to call a result reproducible), `reproduction/05` section I (reproduction flags), `00_START_HERE.md` (DIRECT/DERIVED/PROPOSED/UNKNOWN)

## 1. Purpose & what we take from the library
Every result row says what it is (which of the seven quantities), under which timestamp policy, data version, cost model, window and validation level, and how complete its evidence is. Library grades external sources A-E for *replication completeness*; we adapt the same discipline to our own runs as two orthogonal fields: `evidence_grade` (how reproducible/verifiable the run is) and `V` level (how much validation it has passed, MT-04). This stops "validated" (engine reproduces the original script, AGENTS.md) from being read as "has an edge".

## 2. Gap analysis vs the repo today
- `results/<name>/summary.json` keys: `module_doc, name, asset_class, family, parent, status, description, thesis, params, live_ready, summary, run`; `run = {timestamp, git_rev (short hash), pandas}` (verified on `results/steady_drift_plus`, git_rev `caa9074`). Missing: git dirty flag and diff hash, python/numpy versions, data hashes, protocol/window, cost model, timestamp policy, trial count, test results, units.
- `store._git_rev()` returns `git rev-parse --short HEAD` only; uncommitted changes are invisible.
- A raw-data manifest tool exists (`pead/prep/verify_raw_data.py`: path/size/sha256 for six raw folders, `--write/--check`) but no run links to it, and derived data (`data/equity_vol_panel.parquet`, `data/events/...`) is not hashed. `data/_prefix_vintage/` vs corrected vintage is selected by env vars (`PEAD_DATA_DIR` etc., FIXES.md) with nothing in the result recording which was used -- two runs with the same git rev can differ.
- Result quantity is implicit: `annualized_return` = CAGR on whole calendar (MT-03), `alpha_benchmark.csv` = market-adjusted regression alpha, paper's Sharpe = active window; the library's seven-number taxonomy is not encoded.
- Status vocabulary (`validated|research|reference|unverified`, `Strategy.status`) conflates "engine reproduces script" with merit.

## 3. Exact specification
`results/<name>/manifest.json` (written by `save_result`; schema_version 2):
```json
{ "schema_version": 2, "run_id": "<name>-<utc>-<short>", "quantity": "continuous_portfolio_nav_cagr",
  "code": {"git_rev": "...", "dirty": false, "diff_sha256": null, "python": "3.12.10", "pandas": "3.0.5", "numpy": "..."},
  "data": {"vintage": "corrected|prefix", "env": {"PEAD_DATA_DIR": "...", "PEAD_DECILE_RANKING": "prior_quarters", "PEAD_DECAY_SEARCH_FRAC": "0.7", "PEAD_DELIST_POLICY": "..."},
           "files": {"data/equity_vol_panel.parquet": "sha256", "data/events/equity_events_1996_2013.parquet": "sha256", "data/earnings/ibes_clean_1995_2014.parquet": "sha256", "data/normalized_equity/*": "tree-hash", "results/market_benchmark": "..."},
           "raw_manifest_check": "ok|mismatch|not_run"},
  "timestamp_policy": {"rule": "after_close_cutoff_16:00;unknown->next_session", "entry": "day0_close", "feature_cutoff": "day0-1", "version": "event_time v1"},
  "costs": {"model": "legacy_quintile", "params": {...}, "borrow": "none", "impact": "none"},
  "protocol": {"id": "v1", "window": "active", "holdout_kind": "burned", "frozen": false},
  "trials": {"family": "...", "n": 0, "basis": "lower_bound_backfill"},
  "tests": {"lookahead_suite": "pass|fail|not_run", "cross_validation": "pass|not_run", "redteam": "..."},
  "evidence": {"grade": "B", "flags": {"source_verified": false, "code_verified": true, "historical_data_exact": true, "timestamp_verified": "partial", "independent_nav_recomputed": true, "source_return_reproduced": null, "out_of_sample_locked": false}},
  "units": {"annualized_return": "cagr_active_window", "sharpe": "daily_252_rf0", "alpha": "capm_hac_annual"} }
```
Evidence grade for our runs (adapting library A-E; `UNKNOWN` never filled with guesses):
- **A** exact data vintage hashed, code at a clean commit, daily NAV independently recomputed (MT-10/MT-01 LA-12), timestamp policy verified from the builder in-repo (MT-02), deterministic re-run reproduces `daily.csv.gz` byte-for-byte.
- **B** as A but some inputs unverifiable (e.g. event/IBES builder absent -- *today's state* for all equity runs; classification features unverified, MT-02 E2/E18).
- **C** methodology fully specified but data or code state not pinned (dirty tree, env vars unrecorded).
- **D** result quoted from a document without an executable run (e.g. `*_legacy` numbers reproduced only in a subprocess vintage; paper tables).
- **E** diagnostic / event-study / cross-sectional statistics, not an investable NAV (Fama-MacBeth decile tables).
Quantity taxonomy (`quantity`) = library's seven: `event_car`, `unhedged_share_return`, `option_contract_return`, `avg_selected_trade_return`, `continuous_portfolio_nav_cagr`, `factor_alpha`, `leveraged_regime_etf_cagr`. The leaderboard refuses to rank rows with different `quantity` in the same column.

Standardized result row (the library's "return reporting standard") `results/leaderboard.json -> rows[]` contains: `experiment_id, data_hash, code_commit, timestamp_policy, source_only_features (bool), feature_cutoff, fills, fees_bps, borrow, gross_leverage_avg/max, max_position, hold_sessions, OOS_start, OOS_end, daily_NAV_CAGR, Sharpe_252, sortino_252, worst_drawdown, turnover, IC_by_quarter (path), factor_alpha_t_HAC, drawdown_CI, trade_count, average_holding_days` -- every field except `IC_by_quarter` and `drawdown_CI` already derivable from existing summary keys (`n_positions`, `annual_turnover_x_capital`, `avg_gross_exposure`, `max_gross_over_nav`, `total_transaction_costs`, `holding_days` in positions); the rest come from MT-03/MT-04.

Hash policy: large files hashed with a cached `(path,size,mtime)->sha256` table in `data/.hashcache.json` (git-ignored) to avoid re-hashing multi-GB inputs each run; OptionMetrics (on `D:`, ~31GB) is identified by `universe_fingerprint` from the pull manifests (`data/metadata/wrds_pull_manifest_crsp.csv` has this column) plus file size/mtime of the opprcd parquet set, never full hashing.

## 4. Data requirements
Existing manifests: `data/metadata/wrds_pull_manifest_*.csv` (rows, pulled_at, universe_fingerprint), `pead/prep/verify_raw_data.py` output (`data/raw_data_manifest.json`; run `--write` once if absent). No new external data.

## 5. Framework mapping
`pead/core/manifest.py` (new); `pead/core/store.py::save_result` writes `manifest.json` next to `summary.json`; `load_result` tolerates its absence (all 66 existing folders); `python -m pead manifest <name>` prints it; `python -m pead grade [--all]` recomputes grades from manifests + tests + protocol; report footer renders the grade and quantity; `docs/RESULTS_SCHEMA.md`.

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** OV's trial CSV and OPT's all-trials CSV writer are subsumed by MT-04's registry; this spec's manifest is the container.
- **NEW:** `pead/core/manifest.py` (REF-run-manifest), `pead/core/grade.py`, `docs/RESULTS_SCHEMA.md`, `data/.hashcache.json` (ignored), backfill script `scripts/backfill_manifests.py` producing `manifest.json` with `evidence.grade = "C"` and `trials.basis = "unknown"` for the 66 existing runs (cannot know their data hash -> honest).
- **CHANGE:** `pead/core/store.py::save_result` (add manifest; change `_git_rev` to include `dirty` and `git diff | sha256`; regression risk: LOW; the existing tests `tests/test_fixes.py::test_load_result_tolerates_empty_or_missing_quarterlog` must keep passing); `pead/core/store.py::update_leaderboard` + `load_summaries` (new columns; quantity guard; MEDIUM -- consumers `reporting/comprehensive.py`, `reporting/paper.py` read `summary.json`/`leaderboard.json`, keep existing keys); `pead/strategies/base.py::Strategy.meta()` (declare `quantity`, `timestamp_policy_id`).
- **RESTRUCTURE:** one `schema_version` field across `summary.json`, `leaderboard.json`, `manifest.json`; keep `summary.json` readable by `paper.py` (v1 keys unchanged); `status` stays but gains the orthogonal `V` and `grade` columns on the leaderboard so the three concepts (engine reproduces / validation level / evidence completeness) are separate.
- **IMPROVE:** (a) reproducibility today depends on env vars read at import time (FIXES.md) -- record them; (b) git short hash without dirty flag lets uncommitted edits masquerade as a commit; (c) derived data and IBES-derived files unhashed; (d) `status=validated` semantic overload.

## 6. Acceptance tests
- Re-run `calm_weighted` twice: `daily.csv.gz` byte-identical and `manifest.data.files` identical; change one env var (`PEAD_DECILE_RANKING=within_quarter`, separate data dir) -> manifest records it and `vintage` differs.
- Dirty tree -> `dirty: true` and a non-null `diff_sha256`.
- `grade` rules unit-tested on synthetic manifests (missing builder -> B at best; dirty -> C; no executable run -> D).
- Leaderboard refuses to compare mismatched `quantity` (test with a fake `event_car` row).
- Backfill leaves all 66 folders loadable by `load_result` and `load_daily`.

## 7. Rollout plan
Manifest writer + backfill (additive) -> grade command -> leaderboard v2 columns (with MT-04) -> footer in reports -> make `grade >= B` a precondition of `V2`.

## 8. Risks
- Hashing cost and OneDrive/D: paths; mitigated by the hash cache.
- Schema churn breaks paper builder -> contract test that `python -m pead paper --help`/dry-run still parses.
- Grade inflation: A should be rare; today's honest maximum is B because of the missing event builders.

## 9. Dependencies and implementation checklist
Depends on: MT-01 (tests.json), MT-02 (timestamp policy id), MT-04 (protocol, trials), MT-06 (cost description). Blocks: leaderboard v2, any external claim.
- [ ] `manifest.py` + hash cache
- [ ] `save_result` writes manifest; dirty/diff hash
- [ ] backfill for 66 runs
- [ ] `grade.py` + CLI
- [ ] leaderboard quantity guard + columns
- [ ] `docs/RESULTS_SCHEMA.md`
