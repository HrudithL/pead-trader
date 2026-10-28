# OV-03: market-trend and earnings-breadth gate on steady_drift_plus

`pead/core/market_state.py`, `pead/strategies/equity/steady_drift_gate.py`. Trend = CRSP VW index above its 200-day mean and up over 126 sessions. Breadth = share of the last 20 sessions' announcers with SUE rank >= 0.75 and a positive day-0 return; OFF when <= c = 0.15, fixed from the breadth distribution alone (~22nd percentile) before any backtest. Gate at the close of t-1 scales day t's exposure to 1 or `s_off`; cash earns the T-bill rate; never levers.

| variant | full CAGR / Sharpe / MDD | search Sh / MDD | holdout Sh / CAGR / MDD |
|---|---|---|---|
| steady_drift_plus | 12.07% / 1.19 / -22.1% | 1.38 | 0.87 / 9.7% / -18.7% |
| trend only | 8.93% / 1.20 / -14.4% | 1.35 / -12.1% | 0.94 / 8.0% / -14.4% |
| breadth only (control) | 9.27% / 1.02 / -24.4% | 1.18 / -17.3% | 0.77 / 8.1% / -20.1% |
| trend + breadth, off = cash | 8.12% / 1.16 / -13.1% | 1.25 / -12.1% | 1.03 / 8.5% / -13.1% |
| trend + breadth, off = half | 10.15% / 1.28 / -15.1% | 1.44 / -9.7% | 1.01 / 9.2% / -10.5% |

Random-gate placebo (`scripts/gate_placebo.py`, 200 seeds, same ON fraction 0.60 and mean ON/OFF run lengths 28/19 sessions, half exposure when off): placebo Sharpe mean 1.15 (95th pct 1.27), max drawdown mean -19.0%; the real half-off gate is 1.28 / -15.1%, p = 0.045 for Sharpe and 0.080 for drawdown.

Reading
- The trend gate is what does the work: it cuts drawdown by about a third and holds Sharpe, at the cost of 3 points of CAGR. Breadth alone is worse than nothing (1.02, with no drawdown help).
- Trend + breadth with half exposure is the best risk-adjusted variant in both windows (holdout Sharpe 1.01 vs 0.87, drawdown -10.5% vs -18.7%) and marginally beats random de-risking of the same duration (p about 0.05-0.08). That is suggestive, not conclusive: effectively two bear regimes (2000-02, 2007-09) drive it, the constants were fixed ex ante but the design is still tested on the same 17 years, and the T-bill series behind the cash leg is typed from memory.
- Returns are lower than the base (10.2% vs 12.1%): this is a drawdown/Sharpe improvement, not a return improvement. Not promoted; `steady_drift_plus_gate_half` stays `research`.
- 4 trials logged. Not run: SMA window/c sensitivity grid, entry-gate variant B, the growth-regime control G5.

## Stress results (2026-10-28, `scripts/gate_stress.py`; 10 more trials logged)

Half-exposure gate, SMA window x breadth threshold c (all causal; the alpha book is the same in every cell):

| SMA | c | CAGR | Sharpe | MDD | search Sh | holdout Sh | holdout MDD |
|---|---|---|---|---|---|---|---|
| 150 | 0.13 / 0.15 / 0.18 | 10.1 / 9.8 / 8.8% | 1.28 / 1.26 / 1.22 | -15.5 / -15.5 / -13.8% | 1.52 / 1.49 / 1.47 | 0.87 / 0.89 / 0.84 | -12.4 / -12.4 / -11.3% |
| 200 | 0.13 / 0.15 / 0.18 | 10.5 / 10.2 / 9.1% | 1.29 / 1.28 / 1.24 | -15.1 / -15.1 / -13.9% | 1.48 / 1.44 / 1.42 | 0.97 / 1.01 / 0.96 | -11.2 / -10.5 / -10.3% |
| 250 | 0.13 / 0.15 / 0.18 | 11.0 / 10.7 / 9.5% | 1.35 / 1.34 / 1.29 | -13.6 / -13.6 / -13.1% | 1.58 / 1.54 / 1.50 | 0.97 / 0.99 / 0.95 | -11.5 / -11.5 / -10.3% |

- The result is not a knife-edge parameter: every one of the 9 cells beats the base's full-period Sharpe (1.19) and drawdown (-22.1%), with Sharpe 1.22-1.35 and drawdown -13% to -15.5%. The long-SMA cells are best, i.e. the 200/0.15 default was not an optimum. Holdout Sharpe is 0.84-1.01 against 0.87, so the holdout gain is only reliable for SMA >= 200; the drawdown gain (about -10% to -12.4% vs -18.7%) holds in every cell. Higher c (more time off) costs return monotonically.
- Walk-forward (expanding window, cell chosen on past years only, test years 2001-2014): selected gate Sharpe 1.12, MDD -13.6%, vs fixed default gate 1.07 / -15.1% and the base 0.99 / -22.1% over the same years. It converged on SMA 250 and c = 0.13 from 2003 on. The selection only searches a 3 x 3 grid, so the optimism from selection is small, but the gate still rests on two bear markets.
- Entry-gate variant (B), `steady_drift_plus_gate_entry` (skip events that would enter while the gate is OFF, no exposure scaling): 9.53% CAGR / Sharpe 1.24 / MDD -12.2% over the full period; search Sharpe 1.37, holdout Sharpe 1.09, holdout CAGR 10.2%, holdout MDD -12.2%. It is the weaker brake on paper, yet its holdout is the best of all variants, which says the benefit is largely avoiding new entries into weak markets, not de-risking old positions.
- Verdict: the effect survives a parameter grid, a walk-forward and a different implementation, so it is more credible than a single-cell result. It is still tested only on 1996-2013 with the typed-from-memory T-bill series and no independent holdout; the status stays `research`, and promotion waits for real T-bill data (ROADMAP #2) and post-2013 data (#7).
