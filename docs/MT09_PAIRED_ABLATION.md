# MT-09 paired ablation, EQ-26 spread costs, OV-04 / OV-07 (2026-10-28)

## Paired comparison of every candidate against `steady_drift_plus` (`scripts/paired_ablation.py`)
Draw = Sharpe(candidate) - Sharpe(base) on the same month-blocks resampled jointly (1,000 draws); full active window and the burned holdout alone. Full table: `results/paired_ablation/steady_drift_plus.csv`.

- **Full period:** the only candidate with a Sharpe gain whose interval clears zero is the `react_flat` artefact (a nearly all-cash book). The best honest ones are `stop5` (+0.11, 95% CI -0.10..+0.32), `gate_half` (+0.08, -0.07..+0.24), `confirm_notneg` (+0.05, -0.01..+0.12). Significantly worse: `bouddat` (-0.41), `ea1904` family (-0.27 to -0.37), `seas_s` (-0.27), `orj_only` (-0.31), `seas_or` (-0.18), `lag1/lag2` (-0.06/-0.08, i.e. the same-close fill is worth that much).
- **Holdout:** no candidate beats the base with an interval above zero. The gate variants have the best point estimates (`gate_entry` +0.21, `gate` +0.16, `gate_half` +0.14; all CIs span zero, P(better) 0.69-0.83). Significantly worse in the holdout: `ea1904` and `ea1904_nostop` (-0.8), `decay60`, `trail`, `mom_tilt`, `bouddat`, `mom_filter`.
- Conclusion: after the paired test the base `steady_drift_plus` is not beaten by anything with statistical support; the trend/breadth gates are the only family with consistently positive (but insignificant) estimates in both windows and a large, robust drawdown gain.

## EQ-26: spread-based costs (`scripts/spread_costs.py`)
Closing bid-ask on day 0 for the 12k funded positions of `steady_drift_plus`: notional-weighted half-spread 11.3 bps vs the engine's 7.2 bps one-way (by liquidity quintile, the engine is too low in quintiles 3-5: 15 vs 10, 12.7 vs 7, 8.5 vs 5 bps; quintile 2 is conservative). About 4 bps one way, 8 bps round trip, is missing from the engine: roughly 0.2 CAGR points (docs/MT06_COST_GRID.md). Closing quotes only; no impact, no intraday spreads, and the exit-day spread was not measured separately.

## OV-04 volatility-scaled stop (`steady_drift_plus_volstop`) and OV-07 multifactor composite (`steady_drift_plus_multifactor`)
| variant | full CAGR / Sharpe / MDD | search Sh | holdout Sh / CAGR / MDD |
|---|---|---|---|
| steady_drift_plus | 12.07% / 1.19 / -22.1% | 1.38 | 0.87 / 9.7% / -18.7% |
| volstop (stop = 3 x tvol_60d x sqrt(20), clipped 5-25%) | 11.43% / 1.18 / -25.0% | 1.42 | 0.78 / 8.5% / -20.9% |
| multifactor (mean of SUE, momentum, calm ranks >= 0.8) | 8.33% / 1.06 / -16.4% | 1.27 | 0.72 / 6.5% / -16.4% |

Neither helps: the volatility-scaled stop adds drawdown (-25%) without return, and the composite dilutes the SUE signal (as in OV-02). No true ATR (no high/low data); `tvol_60d` close-to-close volatility is the proxy. 2 trials logged.
