# Shuffled-surprise placebo: steady_drift_plus (MT-11 / G8)

`scripts/placebo_report.py steady_drift_plus --seeds 200`: SUE ranks permuted within each announcement quarter, strategy otherwise identical.

| | CAGR | Sharpe |
|---|---|---|
| real | 12.07% | 1.19 |
| placebo mean (200 seeds) | 8.05% | 0.83 |
| placebo max | 9.15% | 0.93 |

Real Sharpe exceeds every placebo (empirical p = 1/201 = 0.005). About 4 of the 12 CAGR points come from the signal; the rest is the long-only, cash-yield, size/liquidity structure that a random ranking also earns. This matches the earlier 3-seed finding that the placebo halves the alpha, and it is an in-sample (full window) test, so it does not address holdout decay (see docs/SIGNAL_REPORT.md).
