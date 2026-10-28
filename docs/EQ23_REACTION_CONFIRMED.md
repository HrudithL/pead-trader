# EQ-23: reaction-confirmed steady_drift_plus and entry-lag test

Registered in `pead/strategies/equity/steady_drift_reaction.py`; `d0_excess` = raw return on the day0 close minus the market return, known at the entry close. Windows from `pead/eval/protocols/v1.json` (search 1996Q2..2008Q2, holdout burned).

| variant | full CAGR / Sharpe | search Sharpe | holdout Sharpe | holdout CAGR |
|---|---|---|---|---|
| steady_drift_plus (base) | 12.07% / 1.19 | 1.38 | 0.87 | 9.7% |
| confirm_pos | 11.17% / 1.22 | 1.49 | 0.83 | 8.9% |
| confirm_notneg | 12.01% / 1.25 | 1.49 | 0.85 | 9.3% |
| confirm_z | 11.98% / 1.24 | 1.49 | 0.85 | 9.4% |
| contrarian (falsification) | 5.81% / 1.07 | 1.45 | 0.62 | 4.3% |
| lag1 | 11.47% / 1.14 | 1.30 | 0.86 | 9.5% |
| lag2 | 11.19% / 1.11 | 1.26 | 0.86 | 9.5% |

Readings
- Confirmation filters raise search-window Sharpe (1.38 to 1.49) but do NOT improve the holdout (0.83-0.85 vs 0.87): in-sample improvement that does not carry over. Not promoted.
- The contrarian filter keeps a Sharpe of 1.07 over the full period (though halves CAGR): much of the book's Sharpe comes from the event universe and cash leg, not the reaction, consistent with the placebo.
- Entry lag: one session costs about 0.6 CAGR points and 0.05 Sharpe, two sessions about 0.9 points. A same-close fill assumption matters but the edge survives a 1-2 session delay.
- 6 trials were logged to the registry (family equity-longonly). Holdout results were read once, here. The gate on alpha t >= 5.5 was not checked.
