# Options spec sheets (PEAD and earnings events) - 2026-10-27

Documentation only. No code under `pead/` or `tests/` was changed. Companion file: `_ENABLING_CHANGES.md` (every REF / CHANGE / IMPROVE item, de-duplicated, with order).

## Where we stand (verified in this checkout)

| Book | Return / Sharpe / MaxDD | Source |
|---|---|---|
| `options_cap20` (ask in, bid out) | 0.28% / 0.16 / -18.87% | `results/options_cap20/summary.json` |
| `options_cap30` | -0.10% / -0.02 / -28.97% | same |
| `options_worst_case` (dead quotes = 0) | -1.04% / -0.52 / -32.33% | same |
| `options_cap20_midfill` (reference, old convention) | 4.43% / 1.85 / -14.45% | same |
| `steady_drift_plus` (equity parent) | 12.07% / 1.19 / -22.07% | `results/steady_drift_plus/summary.json` |

Mechanics that matter: the engine (`pead/engines/options.py`) is single-leg, long-only premium, hard-wired to `kelly_*` columns and checkpoints [1, 5, 10, 20, 40, 60]; the chain build keeps ONE expiry with DTE >= 95 (target 120) at the reaction day and bid/ask only for the picked contract; no pre-event chain; no short-dated expiries; 2011 missing; picks start 1998. Measured spread of the Kelly picks at day 0: median 11.9% of mid (mean 29.6%, 75th pct 40.0%, 90th pct 88.5%); 22.6% have no mid at 60 days (mostly zero bids). ATM / ITM / short-dated spreads are NOT yet measured: that is the first deliverable of OPT-01.

## Ranking (value x probability of surviving ask-in/bid-out / effort)

| Rank | Spec | Idea | Priority | Effort | Survival odds after real fills (honest) |
|---|---|---|---|---|---|
| 1 | [OPT-01](OPT-01_shared_option_infrastructure.md) | Shared infrastructure: chain snapshots with bid/ask for all strikes, event clock, fill model, multi-leg engine, path quotes, exit rules, spread diagnostics | P0 | L | n/a (enabler); also the gate that can kill the rest cheaply |
| 2 | [OPT-02](OPT-02_ask_aware_post_reaction_calls.md) | Ask-aware ("Tier B") post-reaction calls on top-decile SUE, liquidity-filtered, causal exits; put / vertical ablations | P0 | M | low-moderate; best evidence base, filtered liquid cell might be marginally positive |
| 3 | [OPT-03](OPT-03_deep_itm_call_stock_substitute.md) | 0.85-delta call as a stock substitute inside `steady_drift_plus` | P1 | M | moderate to be neutral, unlikely to improve; carry gain only in high-rate years |
| 4 | [OPT-04](OPT-04_pre_earnings_straddle_implied_vs_history.md) | One-session pre-earnings long straddle, gated by implied move / own history of earnings moves | P1 | L | open; only idea with a different edge; best potential edge per spread-dollar |
| 5 | [OPT-05](OPT-05_cash_secured_risk_reversal.md) | Cash-secured risk reversal (long OTM call, short OTM put) | P2 | M | low; two wide OTM spreads vs a small drift |
| 6 | [OPT-06](OPT-06_index_put_spread_overlay_steady_drift.md) | Beta-sized SPX put-spread overlay on `steady_drift_plus` | P2 | M | spread is fine; premium drag likely loses to simply holding less equity |

Recommended execution order: OPT-01 diagnostics (spread report) -> OPT-02 -> OPT-03 (reuses OPT-02 plumbing, needs REF-overlay) -> OPT-04 (needs the Dm1 chain build) -> OPT-05/OPT-06 only if earlier gates pass. Stop rules are inside each spec ("gate" in the experiment plan). Every OPT spec uses one search window (first 70% of announcement quarters, as `daily_decay.py` does) and opens the holdout once, with a logged all-trials file.

Shared rules for all strategies: ask in / bid out as the headline, `spread_mult` 1.5 and 2.0 as stresses; zero-bid dead-quote mark at zero as the primary, half-ask secondary; entry lag 1 session as default; baselines re-run (`options_cap20`, `options_cap30`, `options_worst_case`, matched stock control); blend only against `steady_drift_plus` after standalone survival; results labelled `research`, never `validated` (`validated` means "engine reproduces the original script", `AGENTS.md`).

## Rejected ideas (with reasons)

| Idea | Reason rejected |
|---|---|
| Bailey/Meche LSTM + long ATM call + daily delta hedge (Library 09) | Author-reported -3.80%, 28.2% win rate, 124 trades; audit 05 shows ranking code sorts signed not absolute predictions (B1), DTE is a preference not a rule (B2), EOD quote clock (B3). Repo has no model and no intraday hedge data. The hedged-call payoff is `0.5*Gamma*S^2*(RV^2-IV^2)`: with median day-0 `iv_rv` = 1.07 (`event_vol_features`) the long-gamma side pays the variance risk premium on average. Predicting |move| is not predicting gamma P&L (Library 09 lesson). |
| Delta-hedged "cheap vol after crush" calls (post-reaction, low `iv_rv`) | Needs a daily stock-hedge engine, share borrow for the short-stock hedge, and EOD data cannot time rehedges; the cheap-IV subset is thin (25th percentile of `iv_rv` is 0.92). Revisit only if OPT-02's low-IV/RV ablation shows an edge on the unhedged call. |
| Short straddle / short strangle around earnings | Undefined risk, margin not modelled anywhere in the repo, fat-tail gaps, short options at the bid are the worst case for the fill model; Library 04 allows defined-risk only. |
| Defined-risk short event vol (iron butterfly / condor sold pre-earnings) | Four legs = four crossed spreads; the wing credit is eaten by spreads; the same implied/realized signal is tested long-side in OPT-04 first. Revisit if OPT-04 shows implied > realized systematically AND straddle spreads < ~3%. |
| Calendar / diagonal spreads around earnings | Two expiries, four legs, term-structure dependent, short front leg has assignment risk, EOD data only, not a PEAD drift trade. |
| Standalone debit call spread on SUE | Repo evidence (#21 in the synthesis doc): matched debit spread 1.43% / Sharpe -0.123 vs long call 3.27% / 0.153; two spreads. Kept only as an ablation inside OPT-02. |
| Per-stock protective puts / collars on the ~150 open Steady Drift positions | 150 small single-name put trades each paying a wide OTM spread (picks: median 11.9%); AQR / Cboe rationale cited in the synthesis doc says protection is expensive; index overlay (OPT-06) is the only version with tolerable spreads. |
| Covered-call overwrite on the long book | Sells the upside the PEAD thesis is about; gives away convexity; not a source of ask-in/bid-out edge. |
| Unconditional pre-earnings straddle on all names | Kept only as the baseline cell inside OPT-04. |
| Naked short puts / margin risk reversals | Margin and leverage unmodelled; the Steady Drift book is no-borrowing by design. |
| Weekly / intraday / next-open option fills | Not in data: OptionMetrics extract is end-of-day, 1996-2013, pre-weeklies for single names. |
| SPX short-volatility put-credit ladder (PR #19 line) | Different trade (conditional market short vol, consumed 2012-2013 holdout, 46-51% drawdowns per the synthesis doc). Out of scope for a PEAD spec; do not mix. |
| Earnings-avoidance filter as a standalone strategy (Library 21) | Evidence is 28 events from a mean-reversion rule; adopted only as an exit rule in OPT-02 (never hold through the next announcement). |
| Kelly selector re-run with the ask instead of mid as a "fix" | Kelly on a decile return distribution assumes the distribution is well estimated (first walk-forward quarters use 8 quarters of history, per `kelly_optimal.py` caveats) and picks OTM contracts with wide spreads; OPT-02 replaces it with rule-based, liquidity-filtered picks and equal weights so sizing cannot hide edge. |
