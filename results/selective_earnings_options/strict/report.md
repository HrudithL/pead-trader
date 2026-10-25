# Selective Earnings Options: research result

Status: **NOT APPROVED FOR INVESTMENT**.
Mode: strict; sample 1999-01-04 to 2010-12-31.
Fixed parameters; expanding matured-history price model; no test-period parameter search.

| Metric | Strategy, net | SPY total return |
|---|---:|---:|
| CAGR | 0.00% | 1.89% |
| Sharpe (zero cash rate) | 0.00 | 0.19 |
| Maximum drawdown | 0.00% | -55.20% |

Closed positions: 0. Market-model alpha: 0.00%/year.
Config fingerprint: `1f95acd3f6b41257`.

![Development equity curve](equity_curve.png)

## Interpretation

Daily EOD buys at ask and sales at bid; $0.65/contract/side commissions; integer lots;
previous-session volume and OI; maximum-loss and collateral budgets; idle cash earns zero.
Commission is an assumption, not a broker schedule. Missing marks receive contractual
worst-loss reserves and invalidate execution. These are not asserted trade fills.
Assignment, corporate-action and historical calendar/identifier validation remain open.
The reversal sleeve cannot trade without a historical known-at-the-time schedule and
independent event-window price model. This adapter deliberately does not invent them.
Market alpha is not multifactor alpha. Paired monthly-block intervals do not replace
issuer/date cluster inference. This is development research, not an untouched holdout.

## Investment gates

- execution_valid: pass
- benchmark_total_return: pass
- minimum_three_years: pass
- cagr: FAIL
- sharpe: FAIL
- alpha: FAIL
- alpha_confidence: FAIL
- drawdown: pass
- canonical_provenance: FAIL
- minimum_100_trades: FAIL
- multifactor_and_issuer_cluster_inference: FAIL
- genuine_untouched_holdout: FAIL

## Skipped candidates

- unverified_provenance: 347,700
