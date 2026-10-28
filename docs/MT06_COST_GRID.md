# MT-06: transaction-cost sensitivity (long-only books)

`scripts/cost_grid.py`: a flat round-trip cost replaces the liquidity-quintile table (committed table: 10 / 14 / 20 / 30 / 50 bps round trip for quintiles 5..1). Long-only books, so no borrow; market impact is NOT modelled.

| round trip | steady_drift_plus CAGR / Sharpe / MDD | gate_half | gate_entry |
|---|---|---|---|
| 0 bps | 12.39% / 1.22 / -21.8% | 10.41% / 1.31 | 9.77% / 1.27 |
| 10 | 12.16% / 1.20 / -22.0% | 10.23% / 1.29 | 9.60% / 1.25 |
| 20 | 11.93% / 1.18 / -22.2% | 10.04% / 1.26 | 9.44% / 1.23 |
| 40 | 11.46% / 1.14 / -22.6% | 9.66% / 1.22 | 9.12% / 1.19 |
| 80 | 10.49% / 1.05 / -23.5% | 8.87% / 1.12 | 8.46% / 1.11 |
| 160 | 8.55% / 0.87 / -25.7% | 7.31% / 0.94 | 7.13% / 0.94 |

Reading: costs are a second-order issue for these books (turnover is low: positions are held 60 sessions). Each extra 10 bps round trip costs about 0.25 CAGR points on `steady_drift_plus`; the committed 10-50 bps table costs under 1 point in total, and the edge survives even 160 bps round trip (Sharpe 0.87), about 3x the worst liquidity bucket. This says nothing about capacity: market impact is not modelled and the 5%-of-volume cap, not cost, is what limits size (ROADMAP #3).
