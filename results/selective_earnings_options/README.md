# Selective Earnings Options: measured research outcome

**Research infrastructure is implemented. The return objective is not achieved. This version is rejected for investment.**

The independent worktree and every ignored cache are preserved. No original data was moved, deleted, linked or overwritten. RESEARCH.md is preserved on the research branch; main was not changed or pushed.

| Window | Strategy net CAGR | SPY total-return CAGR | Closed positions |
|---|---:|---:|---:|
| 1999–2010 development | -0.056% | 1.892% | 25 |
| 2002–2010 walk-forward reporting window | -0.058% | 3.005% | 23 |

These are exploratory results, not a validated investment backtest. All 25 funded development positions are directional verticals; no condors pass all filters and no reversal entry has a verified historical schedule/model. The full preparation panel contains 116,057 eligible events and 27,123 retained daily contract quotes over twelve years. Only a small fraction meets the proposed liquidity, contract and price-edge requirements.

At USD 1M starting NAV the strategy loses USD 6,706 before any idle-cash interest. Average maximum-loss risk committed is approximately 0.039% NAV; low drawdown therefore primarily reflects idle capital. Capital and double-commission stress scenarios all retain negative CAGR. Do not infer that leverage can cure a negative estimated edge.

The strict-provenance control makes zero trades. It is a data rejection, not a zero-risk strategy. Three missing-mark evaluations use conservative worst-loss reserves; no final trade exit uses such a reserve, but executable daily marks are still not fully validated. Historical identifier intervals, American assignment, corporate actions and published earnings schedules remain required. Factor/issuer-cluster inference is not implemented and its gate is explicitly false.

The strategy fails the net return, Sharpe, alpha, sample-size, provenance and final-validation gates. The 2012–2013 final holdout remains unevaluated. The correct next step is to repair the missing data and research specification before considering new frozen hypotheses, not to tune against the reserved holdout or scale this result up.

## Artifacts

- [Full development report](development/report.md), [summary](development/summary.json), [yearly results](development/yearly.csv), [walk-forward folds](development/walk_forward.json).
- [Walk-forward reporting window](walk_forward_development/report.md).
- [Strict provenance control](strict/report.md).
- [Capital and fee stresses](capital_cost_stress.csv).
- [Frozen protocol](frozen_protocol.json), [prepared input hashes](prepared_cache_hashes.json), [raw source metadata](raw_option_source_manifest.json), [environment](environment.txt), [data audit](data_audit.json).
- Implementation/protocol: `docs/research/SELECTIVE_EARNINGS_OPTIONS.md`.

## Verification

Python 3.12 / pandas 3 isolated environment. Full suite: 52 passed, 19 skipped (existing data-dependent tests; original large data is not copied into this worktree). Syntax compilation and git whitespace checks pass. The new tests cover hand-calculated cash/fees/collateral, quote ordering, missing marks, delayed entry, forecast maturity, delisting losses, chronological purging, benchmark gates, registration and holdout protection.
