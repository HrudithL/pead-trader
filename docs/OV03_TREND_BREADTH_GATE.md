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
