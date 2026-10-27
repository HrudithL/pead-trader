# Funded risk-synthesis experiments

Read [the results and decisions](../../docs/OPTIONS_RISK_SYNTHESIS_RESULTS_2026-10-26.md) before interpreting a saved curve. No account is investment accepted or live enabled.

**Publication scope:** this branch contains code, research documents, aggregate tables, paired uncertainty and figures only. Full daily account paths, trade-level ledgers, per-account audits, frozen trial protocols and the local input manifest remain on the preserved local `codex/options-risk-synthesis` branch at `93893e7`. They are deliberately excluded from this publication and its Git history.

The governing/follow-up runs total **196 account/scenario observations**, not independent strategies:

| Completed trial | Accounts | Purpose |
|---|---:|---|
| `final_v6_development_all_suite` | 56 | Initial frozen policies and cost/capacity/funding stresses, 1999–2010 |
| `final_recent_v2_recent_all_suite` | 56 | Same policies, separately cash-started 2012–August 2013; consumed history |
| `pricing_v1_development_options_suite` | 16 | Frozen prior-IV compensation screen and matched parent/stresses |
| `pricing_recent_v1_recent_options_suite` | 16 | Same pricing challenger on recent consumed history |
| `linear_v2_development_equity_suite` | 26 | Actual-share SPY beta hedge, stock/cash controls and costs/funding |
| `linear_recent_v2_recent_equity_suite` | 26 | Same direct hedge on recent consumed history |

`comparison.csv`, `yearly.csv` and `paired_uncertainty.json` also preserve six earlier completed initial implementation observations. `trial_register.csv` includes all failed, stopped and smoke vintages; those are excluded from the governing conclusions. A missing `complete.json` is not a completed run.

Each full local trial has a pre-run `protocol.json` with policy/source hashes and per-account compressed daily NAV/ledger, closed-trade records and `audit.json`. Stock terminal reserve records are explicitly **not asserted executable zero-price sales**. Native quote rows, raw CRSP/IBES data and environment files are not published here.

Reproduce through `python -m pead.risk_research.study` using licensed inputs, then `python -m pead.risk_research.report --root <full-local-results> --bootstrap 1000 --quiet`; use new labels rather than overwriting a trial. Running the reporter or plotter without full local ledgers fails safely and preserves the published summaries/figures. The plot script accepts `--root` and explicit trial-name overrides. See the report for dates, cash model, source gaps and limitations.
