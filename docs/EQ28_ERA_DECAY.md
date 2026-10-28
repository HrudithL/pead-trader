# EQ-28: era interaction and decay-aware exposure

## Part A: has the SUE payoff compressed? (`scripts/era_interaction.py`)
y = a + b*s + c*post + d*(s*post) + sector and size FE, s = `sue_rank_pct`, one row per (permno, quarter), errors clustered by announcement quarter.

| outcome | break | b (t) | d: change after break (t) |
|---|---|---|---|
| 1-day mktadj | 2001-04-09 | 0.0050 (3.1) | +0.0045 (2.6) |
| 1-day mktadj | 2005-01-01 | 0.0075 (6.7) | +0.0015 (1.1) |
| 1-day mktadj | 2008-01-01 | 0.0082 (10.0) | +0.0004 (0.3) |
| 60-day mktadj | 2001-04-09 | 0.0526 (2.9) | -0.0277 (-1.3) |
| 60-day mktadj | 2005-01-01 | 0.0408 (3.6) | -0.0162 (-1.0) |
| 60-day mktadj | 2008-01-01 | 0.0423 (5.2) | -0.0279 (-1.5) |

The 60-day payoff of a full-range SUE move falls by 1.6-2.8 points after each break (about a third to two thirds of b), but no estimate is significant (|t| <= 1.5): compression is the direction of the data, not an established fact. The 1-day reaction does not shrink (it rises after 2001), i.e. the drift, not the immediate reaction, is what weakens.

## Part B: decay-aware exposure (`pead/core/decay.py`, `steady_drift_plus_decay[60]`)
| variant | full CAGR / Sharpe / MDD | search Sh / MDD | holdout Sh / CAGR / MDD |
|---|---|---|---|
| steady_drift_plus | 12.07% / 1.19 / -22.1% | 1.38 | 0.87 / 9.7% |
| decay (K=20) | 10.06% / 1.21 / -21.0% | 1.46 / -10.0% | 0.77 / 7.0% / -17.8% |
| decay60 | 8.37% / 1.20 / -19.9% | 1.48 / -9.8% | 0.51 / 3.2% / -17.6% |

Scaling lowers return roughly in proportion to the exposure given up, leaves full-period Sharpe unchanged, and does worse than the base in the holdout (0.77 and 0.51 vs 0.87): the recent-spread signal is too noisy and lagged to time the 2008-09 and later regimes. Not promoted. Part B uses the new `exposure_scale` hook in `apply_cash_and_risk_target` (None is a no-op; the base reproduces 12.07% / 1.19 exactly). 2 trials logged.
