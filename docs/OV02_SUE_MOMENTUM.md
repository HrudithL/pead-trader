# OV-02: SUE x prior 12-1 month momentum on steady_drift_plus

`pead/strategies/equity/steady_drift_momentum.py`. Momentum = return from row e-252 to e-21 (known at the day0 close); rank among announcers of the previous 365 days. Controls drop a random half of the base events (5 seeds).

| variant | full CAGR / Sharpe | search Sh | holdout Sh |
|---|---|---|---|
| base steady_drift_plus | 12.07% / 1.19 | 1.38 | 0.87 |
| filter (top-half momentum) | 9.28% / 1.08 | 1.33 | 0.68 |
| tilt (0.5 + rank) | 12.10% / 1.17 | 1.37 | 0.81 |
| contrarian (bottom half) | 6.92% / 1.10 | 1.48 | 0.63 |
| random drop of half, 5 seeds | 8.4-8.8% / 1.08-1.12 | 1.34-1.39 | 0.71-0.80 |

Reading: the momentum filter is no better than dropping half the names at random (9.3% vs 8.4-8.8%, Sharpe 1.08 vs 1.08-1.12), and the contrarian half is just as good in the search window. No evidence that momentum adds to the SUE signal; the tilt is neutral. Null result, not promoted. The V0 regression diagnostic from the spec was not run. 8 trials logged.
