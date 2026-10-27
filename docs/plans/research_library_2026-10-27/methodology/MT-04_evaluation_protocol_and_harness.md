# MT-04: Standard evaluation protocol and harness (search window vs holdout, trial registry, DSR gate, leaderboard integration)
- Status: planned | Priority: P0 | Effort: L | Source(s): library `comparisons/03_EVIDENCE_GRADING_AND_COMPARABLE_BACKTESTS.md` (matched-capital experiment, return reporting standard, approval checklist), `reproduction/01_REPRODUCTION_PLAYBOOK.md` sections 0 and 6, `reproduction/02_EXPERIMENTS.md` E10 and the acceptance bar, `foundations/05` acceptance criteria 1-6, `audits/03` section 5; repo `docs/FIXES.md` (D6), `docs/ROADMAP.md` item 7, `pead/prep/equity/daily_decay.py`, `pead/core/store.py`

## 1. Purpose & what we take from the library
Make "evaluated under the standard protocol" a property of every strategy that appears on the leaderboard: fixed chronological search/holdout windows declared before looking, an append-only all-trials registry (the library's "all-trials registry" and "parameter_trials.csv"), frozen parameters before the holdout is read, DSR/PSR/bootstrap on the holdout (MT-03), cost/placebo/concentration stress (MT-06/11/08) and an evidence level (MT-12). It is the single entry point `python -m pead eval`.

## 2. Gap analysis vs the repo today
- **There is no untouched holdout.** All results use 1996-2013 and every strategy constant was chosen on the full sample (FIXES.md: "partly in-sample", decision D6; ROADMAP item 7 "true holdout needs data after 2013"). The only search/holdout device in code is fix 4: `daily_decay.py:52,84` fits holding days on the first 70% of announcement quarters (49 of 71). One strategy documents a split (`calm_rank_tilt` module doc: picked on early data, lifts later-period Sharpe), parsed by regex in `paper.py:1583-1596`.
- **No trial registry.** Sweeps live as CSVs (`data/sweep_*.csv`, `results/vol_sweeps.csv`, `capacity_sweep.csv`), the paper counts rows (`paper.py:1546-1560`), and `docs/STRATEGIES.md` section 3 holds pre-fix sweeps counted by regexing a table. 66 result folders exist under `results/`.
- **Leaderboard = raw argmax** over `validated|research` (`store.py:106-118`), no uncertainty, no window, no trial count.
- **Strategy search is not hooked to the harness**: parameters live as class attributes (`Sizing(...)`, `top_rank`, `min_dollar_volume`); sweeps are ad-hoc scripts (`scripts/`, `pead/research/**`).
- Prior art that should be reused, not rewritten: `earnings_build.tmp/validation.py` (purged chronological folds `purged_folds`, paired circular block bootstrap, gate dict) and `earnings_build.tmp/freeze_holdout.py` (frozen JSON with source hashes, refuses to overwrite). Both are git-ignored scratch for the earnings-options study; promote the ideas, not the files.

## 3. Exact specification
### 3A. Protocol file `pead/eval/protocols/v1.json` (immutable once tagged; changes = new version)
```json
{ "id": "v1", "calendar": "market_benchmark",
  "warmup_quarters": 1,
  "search": {"rule": "first_fraction_of_announcement_quarters", "fraction": 0.70},
  "holdout": {"rule": "remaining", "kind": "burned"},
  "purge_sessions": 200, "embargo_sessions": 20,
  "walk_forward": {"train_years_min": 5, "test_years": 1, "step_years": 1},
  "bootstrap": {"block": "month", "block_len": 3, "n": 2000, "seed": 20261027},
  "costs": {"equity_round_trip_bps": [0, 5, 10, 20, 50], "borrow_annual": [0.0, 0.005, 0.01, 0.02, 0.05]},
  "gates": {...see 3D...} }
```
`kind` values: `pristine` (never inspected; requires data after 2013 -- BLOCKED, see MT-14), `burned` (inspected during development; the 70/30 split of the existing sample falls here for every existing strategy and the report must say so), `pseudo` (walk-forward folds). Verified window dates: the 49 search quarters are 1996Q2..2008Q2 (last search `anndats` 2008-06-30), the holdout is 2008Q3..2013Q4 (22 quarters), which begins at the financial crisis; the holdout is regime-heavy and burned. Purge 200 sessions = the longest holding period in the book (FIXES.md: "holding up to 200 days"); verify against `holding_days.max()` at build time rather than trusting the doc.

### 3B. Python API (`pead/eval/`)
```python
@dataclass(frozen=True)
class Protocol: id; windows: dict[str, Window]; ...
def load_protocol(id="v1") -> Protocol
def split_windows(calendar, ann_quarters, protocol) -> dict   # 'warmup','search','holdout' Windows in dates; quarters from event_time.quarter_of (MT-02)
def purged_folds(events, protocol) -> list[Fold]               # train rows whose [day0, exit_date] ends before fold start - embargo
class TrialRegistry:                                           # results/_trials.jsonl, append-only, file-locked
    def log(self, *, family, strategy, params, window, daily_returns|summary, kept: bool, note="") -> trial_id
    def n_trials(self, family) -> int ; def sr_variance(self, family) -> float
    def backfill(self) -> None                                 # from results/*/summary.json + sweep CSVs; rows tagged backfill_confidence in {"exact","count_only"}
def freeze(strategy_name, protocol) -> Path                    # results/<name>/frozen.json: params, git_rev, source hashes of pead/strategies/<file>.py + engines + core, protocol id, UTC time; refuses to overwrite
def evaluate(strategy_name, protocol="v1", *, stress=("cost","placebo","loo","bootstrap")) -> Evaluation
```
`evaluate` steps (all from the saved `daily.csv.gz` + positions; no re-backtest except stresses that need one):
1. Verify `frozen.json` exists and the strategy source hash matches; otherwise the run is graded `V0 exploratory` and holdout metrics are withheld ("holdout not opened"). A deliberate `--open-holdout` flag writes an `opened_at` stamp into the registry; after opening, the holdout for that *family* is marked burned for any further parameter change.
2. Slice `daily_return` into search / holdout by window dates (legitimate because sizing is causal; for `causal=False` strategies the harness refuses holdout evaluation, MT-01 LA-03).
3. Metrics on each window via `stats` (MT-03): CAGR, Sharpe with CI, MDD, alpha t (HAC), PSR; DSR on the holdout using `registry.n_trials(family)` and `sr_variance(family)`.
4. Degradation: `holdout_sharpe / search_sharpe`, `holdout_alpha - search_alpha`.
5. Stress hooks: cost grid (MT-06), concentration LOO (MT-08), placebos/time-inversion (MT-11), paired ablation vs parent (MT-09).
6. Write `results/<name>/evaluation.json` (+ `evaluation.md` block appended to the report) and the leaderboard row (3E).

### 3C. Trial accounting rules (what counts)
Each distinct `(strategy, params-hash, window, data_hash)` is a trial; every grid point of a sweep, every discarded variant and every `_legacy`/`_prefix_fix` twin counts (library audits/03 section 5: "explicit all-trials registry"; EA1904 counted 6 variants vs thousands tried). `family` = hand-declared `Strategy.family` (already a field, base.py) so equity-volatility sweeps do not deflate the options book. Backfill produces a *lower bound* and the manifest says so (`trials_basis: "lower_bound_backfill"`); DSR is reported at the lower bound **and** at 10x (sensitivity), using `paper.py::dsr_at` logic, plus `N*` (trials at which DSR < 0.95) as already done in `paper.py:1621-1628`.

### 3D. Gates (proposed constants, to be pre-registered by the owner and then frozen in `v1.json`; not derived from data)
G1 frozen before holdout; G2 `LA` suite passes (MT-01); G3 holdout CAGR > 0 after the 10 bps round-trip cost scenario; G4 holdout HAC alpha t >= 2.0 vs market (multi-factor when available); G5 DSR(holdout, registry N) >= 0.95; G6 bootstrap 2.5th percentile of holdout alpha > 0; G7 leave-one-out (MT-08): removing the best name / best quarter / best sector keeps alpha > 0; G8 placebo (MT-11): shuffled-surprise placebo alpha below the strategy's at p<0.05. Level assignment: V0 = none; V1 = G2; V2 = G1+G2+G3; V3 = V2 + G4-G8; V4 = V3 + 6 months of live paper (pead/live). Nothing is `V3` until the holdout is `pristine` (data after 2013) -- until then the highest attainable level is `V2b "burned-holdout"`.

### 3E. Leaderboard v2 (replaces `store.update_leaderboard` body)
Columns: `name, asset_class, status, V, grade, window, cagr_active, sharpe, sharpe_lo95, alpha_t, psr, dsr, n_trials, holdout_sharpe, degradation, max_drawdown`. "Best so far" lines choose among `V>=1` only; below that a clearly labelled "unvalidated best". `reference`/`unverified` stay excluded; `_legacy` rows get `V=ref`. JSON schema `leaderboard.json` gains `schema_version: 2` (the paper reads `results/leaderboard.json`, `paper.py:9`; keep old keys).

## 4. Data requirements
Existing: `results/*/daily.csv.gz`, `positions.parquet`, events (for fold purging: `day0_date`, `exit_date`, `holding_days`). A pristine holdout needs CRSP/IBES after 2013 (ROADMAP #7): **BLOCKED**; degraded proxy: `burned` 30% tail + walk-forward folds, flagged on every report.

## 5. Framework mapping
New package `pead/eval/` (`protocol.py`, `registry.py`, `harness.py`, `gates.py`, `protocols/v1.json`); CLI: `python -m pead eval <sel...> [--protocol v1] [--open-holdout]`, `python -m pead freeze <name>`, `python -m pead trials [--family f] [--backfill]`; hook in `pead/runner.py::_run_one` to log a trial on every `run` (kept=True) ; hook in `strategies/*` sweeps via `with trial_scope(family, params):`. Outputs: `results/_trials.jsonl`, `results/<name>/{frozen,evaluation}.json`, `results/leaderboard.{json,md}` v2. Docs: `docs/EVALUATION_PROTOCOL.md` generated from `v1.json`.

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-search-holdout (OPT), REF-period-split (EQ) and this spec's windows are ONE thing: `pead/core/splits.py` reading `pead/eval/protocols/v1.json`. DECISION NEEDED: EQ-30 proposes search `day0 <= 2005-12-31` (39 of 71 quarters); OPT/code use the first 70% of announcement quarters (49 of 71, 1996Q2..2008Q2, verified from `data/decile_events.parquet`). Recommended: the 70% rule, because decay holding days are already fitted on exactly those 49 quarters (`daily_decay.py:52,84`); a 2005-12 split would put 2006-2008Q2 in the holdout while holding days were fitted on it. REF-variant-grid (OV) writes into this spec's TrialRegistry instead of a second `trials.csv`.
- **NEW:** `pead/eval/` package (REF-eval-harness), `TrialRegistry` + `results/_trials.jsonl` (REF-trial-registry), `protocols/v1.json`, `pead/cli.py` subcommands `eval|freeze|trials`, `scripts/backfill_trials.py`, `docs/EVALUATION_PROTOCOL.md`.
- **CHANGE:** `pead/runner.py::_run_one` (log trial; regression risk: LOW, but worker processes write concurrently -> use `portalocker`-style lock or per-process shard files merged by `trials --merge`); `pead/core/store.py::update_leaderboard` and `save_result` (add `frozen`, `evaluation` fields to `summary.json`; leaderboard schema v2; regression risk: MEDIUM -- `paper.py` and `reporting/comprehensive.py` read `leaderboard.json` and `summary.json` keys, run `python -m pead paper` to confirm); `pead/strategies/base.py` (`family` already exists; add `search_params` declaration listing the swept attributes; LOW); `pead/prep/equity/daily_decay.py:52` (read `SEARCH_FRAC` from the protocol instead of the env default so the decay search window and the eval search window are provably the same; regression: none if both 0.7).
- **RESTRUCTURE:** (a) results layout: add `results/_trials.jsonl` and per-run `frozen.json/evaluation.json` and document in `docs/ARCHITECTURE.md` "Results layout"; (b) move scattered sweep scripts (`scripts/vol_sweeps.py`, `pead/research/**/18_param_sweep.py` etc.) onto `trial_scope` so new sweeps are counted automatically -- research archive scripts stay frozen, backfilled by count; (c) status vocabulary: keep `validated|research|reference|unverified` but add the orthogonal `V` level so "validated" (engine reproduces original script, per AGENTS.md) is never read as an edge claim.
- **IMPROVE:** (a) no run records its data hash -> MT-12; (b) sweeps are not reproducible from `summary.json` -> store `params` hash; (c) the burned-holdout caveat must be printed on every leaderboard line (`window=burned`).

## 6. Acceptance tests
- `tests/test_eval_protocol.py`: `split_windows` on the real calendar gives search = first 49 of 71 announcement quarters (equals `daily_decay.py` train_quarters); no overlap; purged folds contain no event whose `[day0, exit]` crosses the fold boundary (property test with synthetic events).
- Registry: append-only (second writer cannot truncate), `n_trials` monotone, `backfill` is idempotent, lower-bound basis recorded.
- `freeze` refuses overwrite; modifying a strategy file after freeze makes `evaluate` demote to V0 (prior-art behaviour of `freeze_holdout.py`).
- Golden run: `python -m pead eval steady_drift_plus` produces `evaluation.json` whose holdout window equals the protocol dates and whose DSR input `n_trials` equals the registry count; `calm_weighted` produces the same DSR direction as the paper's table (paper uses file-count trials).
- Leaderboard v2 generation leaves `python -m pytest tests -q` green.

## 7. Rollout plan
1. Registry + backfill (read-only, no behaviour change). 2. Protocol + windows + purged folds, library-only tests. 3. `evaluate` on existing results (no freeze -> V0, reports search window only); publish a table of search-vs-holdout for all non-legacy results as *descriptive* (burned). 4. Freeze flow for new strategies from the equity/options/overlay areas (MT-04 is their gate). 5. Leaderboard v2. 6. When post-2013 data arrives, add `v2.json` with a `pristine` holdout.

## 8. Risks
- False comfort: a `burned` holdout still looks like a holdout. Mitigation: label on every output; no V3 without `pristine`.
- Trial lower bound understates data mining; report DSR at several multiples.
- Slicing a continuous run for holdout assumes causality; refused for non-causal modes.
- Process complexity may slow research; keep `eval` optional for exploratory runs (they just stay V0).

## 9. Dependencies and implementation checklist
Depends on: MT-01 (V1), MT-03 (stats), MT-12 (manifest/grade), REF-eval-harness, REF-trial-registry, REF-run-manifest. Blocks: any strategy promotion; MT-08/09/11 plug into `evaluate`.
- [ ] `TrialRegistry` + file lock + backfill (66 runs + sweep CSVs)
- [ ] `v1.json`, `split_windows`, `purged_folds` + tests
- [ ] `freeze` / `evaluate` skeleton producing `evaluation.json`
- [ ] gates G1-G8 wired (stubs for MT-06/08/09/11 until delivered)
- [ ] leaderboard v2 + `paper.py` compatibility check
- [ ] `docs/EVALUATION_PROTOCOL.md`
- [ ] runner hook + `trial_scope` for sweeps
