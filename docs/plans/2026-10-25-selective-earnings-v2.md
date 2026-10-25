# Selective earnings v2: research redesign

User authorized a thorough redesign on 2026-10-25, explicitly including opportunity audits, improved contract selection and alternative liquidity conditions. Scope remains RESEARCH.md; no existing strategy is blended in to manufacture the target.

## Findings and decisions
2007 unique-event funnel: 8,685 raw quoted events; 7,792 with 30–60DTE; 2,034 after price/spread/OI/volume rules; only 143 with the fixed delta pair. Fixed delta windows and uniform protection-wing filters are a major bottleneck. This does not establish profitable broader trades.

Replace fixed delta selection with a finite menu of executable directional debit/credit verticals and post-event condors. Compare structure economics at whole-structure level. Use zero-bid protection when ask is positive and settlement standard; OI and small-lot capacity constrain allocation rather than requiring 50 contracts traded in every leg the previous day. Preserve daily quotes and never use future liquidity to select entries.

Replace remaining-IV Black–Scholes scenario screening with direct, actually quoted historical net structure outcomes. Training observations include entry cashflow, exit bid/ask and commissions, with explicit outcome-maturity dates. Fit only prior-matured outcomes; evaluate issuer/date dependence and within-archetype sample counts. No observed test payoff enters its own prediction.

Chronology: 1999–2004 discovery; 2005–2007 validation; 2008–2010 development test; 2012–2013 remains reserved. The old policy's aggregate development results have already been seen; this is a redesign, not a pristine new historical holdout. No knob is selected from final-holdout performance. Target 12–15% NAV CAGR plus benchmark/risk gates, not return on option premium.

Finite research comparisons: (1) expanded structures with prior-matured empirical payoff model; (2) directional-only and short-vol-only attribution using the same payoff model; (3) conservative versus moderately wider quoted spread/capacity policy; (4) predefined risk-budget stresses. Record every configuration and do not select a high-return cell without validation. Failed hypotheses remain recorded.

Implementation: configurable liquidity and candidate enumeration; cache full entry chains; quote actual candidate exits for training; chronological predictor; callback into the existing tested cash/collateral ledger; scan daily marks for funded candidates; reports and regression tests. All original caches, source data and worktrees remain preserved. No cleanup or worktree removal.


Discovery refinement before validation: the 1999�2001 direct outcome distributions are negative for every unconditional continuation shape. Preserve these `candidates_YEAR` prototypes. The replacement `candidates_flexible_YEAR` explicitly includes both continuation and fading of current announcement news, conditioned on SUE alignment, announcement-return alignment and IV/RV. It can learn which payoff direction is economically justified. No validation/test return was consulted for this change. The completed prior worker scan was stopped by its verified process ID; no source or cache was removed.


Before validation: predeclare three holding rules (10 sessions, 20 sessions, and carry for up to 45 sessions, each closed no later than expiry minus two calendar days and year-end). This tests early reversal versus slower drift and the economics of collecting more option decay instead of paying to exit with substantial time value remaining. All candidate daily quote histories are cached, making the same data usable for honest daily marking and fixed-horizon labels. Direct payoff regressions will condition on observable friction, IV/RV, surprise/announcement-return alignment and trailing market regime, with a rolling four-year training window. No validation or test returns have been used to choose these hypotheses. The earlier fixed-horizon prototype remains preserved.
