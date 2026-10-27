# MT-11: Red-team experiments (time inversion, delayed entry, shuffled-surprise and random-date placebos, friction and stability sweeps)
- Status: planned | Priority: P1 | Effort: S | Source(s): library `audits/03_BACKTEST_FAILURE_MODES.md` section 7 ("three mandatory red-team experiments") and section 5 (placebo earnings dates, randomized SUE directions), `reproduction/01_REPRODUCTION_PLAYBOOK.md` section 8, `reproduction/02_EXPERIMENTS.md` E0 and E10, `foundations/02` (stress +1/+5/+30 minute delays)

## 1. Purpose & what we take from the library
Turn "is the effect real and is the pipeline causal?" into a standard battery that every strategy runs: (R1) event-order time inversion, (R2) delayed entry, (R3) shuffled-surprise placebo with an empirical p-value, (R4) random-announcement-date placebo, (R5) execution-friction sweep (MT-06), (R6) validation stability across sub-periods with parameters frozen (MT-04), (R7) unknown-timestamp policy stress. The library's point: the battery must also be able to *fail* a good-looking number.

## 2. Gap analysis vs the repo today
- A shuffled-surprise placebo exists but outside the package: `meta/steady_placebo.py` permutes `sue_rank_pct` within each `ann_quarter` and re-runs, **3 seeds only** (`for s in range(3)`), results quoted in `docs/STEADY_DRIFT.md` ("steady_drift real 10.6% vs placebo 5.6-7.3%; steady_drift_plus real 12.4% vs placebo 7.3-8.8%"). Three draws give no p-value.
- No time inversion, no delayed entry, no random-date placebo, no unknown-timestamp stress. The engine accepts a `weight` array and an `EquityUniverse`; `with_fixed_hold(uni, hold_sessions)` (equity.py:314) shows the pattern for universe transforms (cut at the stock's last row), so entry-shift transforms are straightforward.
- Cost sweep is a post-hoc borrow table only (MT-06). Stability: eras via `metrics.ERAS`; no frozen-parameter protocol (MT-04).

## 3. Exact specification
Universe transforms in `pead/eval/redteam/transforms.py` (all return a new `EquityUniverse`, never mutate; each respects permno boundaries like `with_fixed_hold`):
```python
def with_entry_shift(uni, k: int, hold: str = "keep_length"|"keep_exit") -> EquityUniverse
    # entry_row_idx += k (clipped to the permno's first/last row), day0_date = panel_date[new], exit_row_idx shifted by k if keep_length
def with_random_entry_offsets(uni, max_abs: int = 60, seed: int, exclude: int = 5) -> EquityUniverse
    # each event gets a uniform offset in [-max_abs,-exclude] U [exclude,max_abs] sessions: placebo announcement dates
def with_permuted_signal(events, col="sue_rank_pct", within="ann_quarter", seed) -> DataFrame
    # as meta/steady_placebo.py: permute within quarter; ALSO within (quarter, size_quintile) variant to keep size/vol composition
def with_unknown_time_policy(events, policy: "next_session"|"same_session"|"plus_k", k) -> DataFrame   # needs MT-02 layer
```
Experiments (`pead/eval/redteam/experiments.py`, each returns a tidy frame and a pass/fail with a pre-registered rule stored in the protocol):
| ID | Experiment | Procedure | Expected behaviour of a causal pipeline / pass rule (proposed constants, to be preregistered) |
|---|---|---|---|
| R1 | time inversion | `with_entry_shift(uni, -1)` and `-2`: enter *before* day0 (score "known too early"), keep the same SUE | Gross performance should rise sharply (the unavailable gap); the report prints `leak_value = cagr(-1) - cagr(0)`. Fail if `cagr(-1) <= cagr(0)` -- the score would then not even be time-aligned (library: "if no difference, investigate"). |
| R2 | delayed entry | shifts +1, +2, +5, +10, +20 sessions | Alpha should decay with delay. No pass/fail rule: the curve is the deliverable (it also measures how much of the repo's drift survives a next-open vs next-close-style delay, cf. library leeyawnnn 4.1% vs 1.6%). |
| R3 | shuffled-surprise placebo | >= 200 seeds (parallel via runner pool; ~1-3 s each) permuting `sue_rank_pct` within quarter; recompute everything incl. decile membership used by `weights()` | empirical p = share of placebo alphas >= real; pass if p < 0.05 for alpha and for CAGR-excess; also report placebo mean as the "universe premium" (STEADY_DRIFT: about half of excess return is the small/low-vol universe, not skill). |
| R4 | random announcement dates | `with_random_entry_offsets`, >= 100 seeds | Alpha distribution centred at the universe/market premium, real alpha in the top tail; pass if p < 0.05. |
| R5 | friction sweep | MT-06 `cost_sweep` | Alpha vs traded dollars curve; break-even multiplier; fail if break-even < 1.0 x legacy cost. |
| R6 | stability | frozen parameters; sub-periods: eras (`ERAS`), search/holdout (MT-04), `exclude 2000-2002`, `exclude 2008-2009`, each `year` | Print per-window Sharpe/alpha; fail if alpha sign flips in >= 1 era with t < -1 (proposed). |
| R7 | unknown-time stress | `unknown -> same_session` (optimistic), `plus_k` minutes (k=1,5,30 as library), `intraday_buffer` 15 min | Quantify sensitivity to the 12.6% unknown-time events (MT-02 E3) and 15:55-15:59 stamps (E4). |
Randomness: all seeds derive from `protocol.bootstrap.seed + experiment id`; seeds logged in the manifest. Each placebo run counts as a trial **only if used to select** anything; placebo draws are not strategy trials (documented to prevent registry inflation).

Signed test manifest (library: "deliver a signed test manifest so no unfavourable scenarios are omitted"): `results/<name>/redteam.json` lists every experiment run with status; MT-12 refuses `V3` if an experiment in the default battery is missing.

## 4. Data requirements
Panel and events (present). R1 with pre-market releases is economically "the gap exists before the open", so R1 uses close-to-close shifts only (CRSP daily); an intraday gap decomposition (previous close -> open -> close of day0) needs `openprc` (present in raw dsf, MT-13) and gives the library's three-part decomposition (overnight / intraday / post-reaction drift). Minute-level delays (R7 `plus_k`) cannot be resolved with daily data: BLOCKED for real minute stress, degraded to session-level policies (MT-14).

## 5. Framework mapping
`pead/eval/redteam/`; CLI `python -m pead redteam <name...> [--only R3 --seeds 200]`; hooks in `evaluate()` (MT-04 G8); report section "Red team"; figures: delay-decay curve, placebo histogram with real value.

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-placebo-runner (EQ-B, `pead/core/placebo.py`) and OV's REF-placebo are the R3 implementation; put it in `pead/validation/placebo.py` (REF-validation-suite) and have `pead/core/placebo.py` re-export, or pick one location and delete the other; `with_entry_shift` here = EQ's REF-entry-lag `with_entry_lag(uni, k)` (one function).
- **NEW:** `pead/eval/redteam/{transforms,experiments}.py`, `tests/test_redteam.py`, `results/<name>/redteam.json`.
- **CHANGE:** `pead/strategies/base.py::EquityStrategy.backtest` (accept optional `uni_transform` and `events_transform` callables so placebos run through the *same* code path as real runs; LOW risk, None default); `pead/runner.py` (a `run_many(callables)` helper to fan placebo seeds over the existing worker-pool logic in `run_strategies`; the pool is currently keyed by registered strategy names, `registry.get(name)`, so generalise to picklable job specs; MEDIUM risk -- keep old path intact).
- **RESTRUCTURE:** move `meta/steady_placebo.py` logic into `pead/eval/redteam` (script becomes a wrapper); store placebo summaries (not full dailies) to keep the repo small.
- **IMPROVE:** raise placebo draws from 3 to >=200 so a p-value exists; add the within-(quarter, size) variant so placebo does not wipe out size/vol composition effects; make R1 an always-on regression test on synthetic data (a causal engine must show leak value > 0).

## 6. Acceptance tests
- Synthetic: a signal that *is* the next-day return gives high R1 leak value and R3 p ~ 0; a pure-noise signal gives R3 p roughly uniform over 200 repeated placebo experiments (KS test at 5%).
- `with_entry_shift(uni, 0)` is the identity (daily frames identical).
- Shifts never cross a permno boundary (reuse the test pattern of `test_fixed_hold_is_cut_at_the_stocks_last_row_and_never_crosses_into_the_next_stock`).
- `with_permuted_signal` preserves the multiset of ranks within each quarter.
- Reproduce `docs/STEADY_DRIFT.md` placebo headline (3 seeds) within tolerance before extending to 200 seeds.

## 7. Rollout plan
Port the existing placebo (R3) with 200 seeds on `steady_drift`, `steady_drift_plus`, `calm_rank_tilt`; add R2 (delay curve) and R1; then R4, R6, R7; finally the manifest and gate G8.

## 8. Risks
- Placebo within-quarter permutation of `sue_rank_pct` leaves deciles used by some strategies (`decile` column) unchanged unless also permuted; permute all derived signal columns together (`decile`, `sue_rank_pct`).
- 200 full re-runs at 1-3 s are cheap; for options books the cost is higher (use fewer seeds).
- Entry shifts change cap interactions and sizing quarter assignment; results are comparative, not additive.

## 9. Dependencies and implementation checklist
Depends on: MT-02 (R7), MT-04 (windows), MT-06 (R5), MT-13 (R1 gap decomposition). Feeds: MT-04 G8, MT-12.
- [ ] transforms + identity/boundary tests
- [ ] R3 with 200 seeds, p-value, within-(quarter,size) variant
- [ ] R2 delay curve, R1 leak value
- [ ] R4 random dates
- [ ] R6/R7 and signed manifest
- [ ] runner job-spec generalisation
