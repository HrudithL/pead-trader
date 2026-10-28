# EQ-24: overnight-gap surprise (ORJ) on steady_drift_plus

`pead/core/gap_features.py` (split-adjusted open(day0)/close(day0-1) from CRSP `openprc`, market-adjusted by the same-day cross-section median, scaled by 60-session gap volatility) and `pead/strategies/equity/steady_drift_orj.py`. Gap rank correlates 0.37 with the SUE rank (spec H1: 0.3-0.5).

| variant | full CAGR / Sharpe | search Sh | holdout Sh |
|---|---|---|---|
| base steady_drift_plus | 12.07% / 1.19 | 1.38 | 0.87 |
| orj_only (gap top decile, no SUE) | 5.99% / 0.89 | 0.88 | 0.91 |
| sue_and_orj | 8.98% / 1.12 | 1.42 | 0.70 |
| comp (SUE/gap composite) | 9.94% / 1.04 | 1.16 | 0.88 |
| weak_gap (SUE decile, muted gap) | 6.48% / 1.17 | 1.51 | 0.75 |

Reading: the gap on its own is a weaker signal than SUE, and adding it to SUE does not beat SUE alone; the intersection and the composite lose return and the intersection also degrades in the holdout. The falsification variant (SUE with a muted gap) keeps a similar Sharpe with fewer names, i.e. a muted open does not mean the price already knew. Null result, consistent with the spec's prior; the follow-up with potential is the uncovered (no-IBES) universe, which is out of scope here. 4 trials logged. Not run: tie-handling and the partial-IC diagnostic.
