# OPT-05: Cash-secured risk reversal (long OTM call + short OTM put) on top-decile SUE

- Status: planned | Priority: P2 | Effort: M | Source(s): `foundations/04_OPTIONS_CONSTRUCTION.md` (defined-risk only; assignment, early exercise, margin must be modelled), `audits/03_BACKTEST_FAILURE_MODES.md` 4.3 / 4.7 / 4.8; repo git tag `archive/options-strategy-risk-reversal` (superseded earlier design, script numbers collide with main - read for ideas only, not numbers; `docs/REPO_MAP.md`); `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md` (cash-secured, "does not eliminate economic leverage").

## 1. Thesis & what we take from sources (and what we do NOT take)

Thesis: after a good surprise, put implied volatility is typically higher than call implied volatility at equal delta (skew). Selling the OTM put and buying the OTM call at the same expiry (a) expresses the same bullish drift view, (b) pays part or all of the call premium with the put premium, so the net debit is small, and (c) the short put is cash-secured (strike x 100 reserved in cash), consistent with the no-borrowing Steady Drift book, where owning stock below the strike is an outcome the book already accepts. The put leg earns a skew/variance premium; the call leg keeps upside convexity. Edge is a mix of drift and skew carry; costs are two crossed spreads on OTM options, which are the widest (selector-pick median rel. spread 11.9%).

Take: defined collateral (cash-secured) only; assignment and early-exercise handling; both legs must pass liquidity. Do NOT take: naked short puts or margin-based leverage; the archived tag's numbers; any short call; Bailey/Arn Mehta (no short options in either source).

## 2. Evidence grade & known defects in sources

No source evidence. Archive tag `archive/options-strategy-risk-reversal` is an old single-commit design, superseded by the Kelly selector and built on mid prices (pre-fix), so it carries no reliable result. Structurally: a short put has asymmetric loss (stock to zero), so a mid-fill backtest overstates it even more than a long option because the put premium earned on the bid is smaller than the mid and buy-back at the ask is larger. Library 03 warns long and short options differ in assignment, dividends, early exercise, margin.

## 3. Exact rules

Event: `decile == 10`, entry `D0 + 1` close (default).
Expiry: DTE in [60, 120] days (same expiry both legs).
Legs: buy call with delta closest to +0.30 (grid {0.25, 0.35}); sell put with |delta| closest to 0.25 (grid {0.15, 0.25}); filters for BOTH legs: `bid >= 0.20`, `rel_spread <= 0.15`, `open_interest >= 50`; reject if put strike < 0.70 x S (extreme skew / data error) or if `C_ask - P_bid > 0.15 * S` (debit too large) or net credit exceeds `0.05*S` (data error / stale put).
Collateral/sizing: cash secures `strike_put x 100 x lots`; total collateral <= `collateral_frac` x NAV, `collateral_frac` in {0.20, 0.40}, additionally each name <= 1% NAV collateral (`name_cap`). Net debit paid from cash. No borrowing; the cash sleeve earns `daily_rf` on uncommitted cash (collateral held in T-bills also earns it; assume yes, flagged as an assumption, with a no-interest ablation).
Exit: time stop 40 sessions (grid {20, 40}); close both legs at: call at bid, put buy-back at ASK. Early close rules checked at checkpoints (the data are EOD): if `put delta <= -0.85` or put ask >= 3x the entry put bid: close both (stop); if the stock drops through the put strike by more than 10%: close. Assignment model (REF-assignment): American put early exercise only matters for deep ITM puts; treat a checkpoint with `put intrinsic >= 0.9 x ask` and `exdate - date > 0` as "assignable": force-close at ask on that checkpoint. Expiry before exit horizon is prevented by the DTE rule.
Fill: net structure through `fills.py`: call buy at ask, put sell at bid at entry, call sell at bid, put buy at ask at exit; `spread_mult` {1.0, 1.5, 2.0}; commission sensitivity 0.65/contract/leg. Dead-quote rules: a zero bid on the call = worth 0 (conservative); a zero ask on the put cannot occur (ask>0 required); for a short put with missing quote at exit, use the last two-sided quote then flag; default to `zero_bid_zero` for calls and ask-based for the put.
Cost-to-edge (derived, NOT measured): two OTM legs, each ~6-9% rel. spread at half-crossing per side at entry and exit, i.e. each leg gives up around its full relative spread in round trip; on a call premium of ~3-4% of spot and a put premium of ~2-3% of spot this is ~0.3-0.5% of spot of spread cost per structure over 40 sessions, similar in size to the whole 40-session PEAD drift of the stock in the top decile after reaction (placebo work attributes only about four annual points to surprise skill, `docs/OPTIONS_STRATEGY_SYNTHESIS_2026-10-26.md` "Equity parent"; 4% x 40/252 is about 0.6% per 40 sessions, derived and for the whole stock position, i.e. before the options' delta below 1). Survival after ask-in/bid-out: unlikely unless spreads are ~half of the Kelly-pick median; the put-skew edge would have to supply the margin. Low confidence either way.

## 4. Data requirements & availability in THIS repo

Need both legs' bid/ask at entry and checkpoints for puts as well as calls at one expiry with DTE 60-120. Existing chain has one expiry with DTE >= 95 (so DTE 95-120 subset only) and no bid/ask for non-picks. Missing: REF-chain-v2 (60-120 DTE), REF-path-quotes for two legs, dividend data for early-exercise reasoning (absent). Put-skew sign is checkable from the existing chain (`iv`, `delta`, `cp_flag` at day 0) as a free descriptive step.
Volume median 0, OI median 50: the 50-OI filter will drop a large share of OTM puts.

## 5. Framework mapping

- New: `pead/strategies/options/risk_reversal.py`, `class CashSecuredRiskReversal(OptionsStrategy)`, `family = "options-structure"`, names `options_rr_25_25`, `options_rr_35_15`, `options_rr_nocarry` (no interest on collateral), `options_rr_mid` (reference).
- New prep: `pead/prep/options/select_rr.py` (stage `select_rr`) -> `data/event_options/rr_legs.parquet` (leg table: `event_id, leg_idx, side, optionid, cp_flag, strike, exdate`) and `rr_events.parquet`.
- Engine: REF-leg-engine `run_structure_backtest` with `sizing_mode="collateral_budget"` and interest on collateral; the single-leg engine cannot represent a short leg or a collateral budget.
- Params: `dict(call_delta, put_delta, dte_lo, dte_hi, rel_spread_max, oi_min, collateral_frac, name_cap, hold_h, stops, collateral_interest, spread_mult, commission)`.
- Reuse: `fills.py`, `daily_rf`, `PortfolioStrategy` blend, `OptionsSizing` pattern, `summarize`.

## 5b. Build vs. change vs. restructure

1. NEW: `risk_reversal.py`, `select_rr.py`, `rr` tests, REF-leg-engine (collateral mode), REF-assignment rule module `pead/engines/assignment.py`.
2. CHANGE: `pead/prep/options/lib/options.py` (columns, see OPT-01); `pead/core/data.py::load_options_universe` (optional leg-table loader).
3. RESTRUCTURE: REF-leg-engine (short legs, collateral accounting, per-structure exits); REF-fill-model must support signed legs; REF-overlay not needed (stand-alone cash-secured sleeve; blend via `PortfolioStrategy`).
4. IMPROVE: dividend data for single names (early exercise of short puts is mostly interest-driven for deep ITM, but ex-dividend calls early-exercise risk is on short calls, which we do not sell); IMP-rates (collateral interest); max-loss reporting (stress: stock to zero on all positions; report worst-day loss and collateral utilization).

## 6. Acceptance tests

- Payoff test on synthetic chain: terminal P&L = `max(S-Kc,0) - max(Kp-S,0) - net_debit` for S grid including both strikes; max loss = `Kp*100 - credit` per put at S=0; collateral exactly `Kp*100*lots`.
- Fill asymmetry test: round trip at unchanged quotes loses exactly (call spread + put spread); short put buy-back at ask.
- Collateral accounting: cash never negative; `collateral + cash + marks == NAV`; no position without full collateral.
- No look-ahead and determinism as in OPT-02; assignment rule uses only checkpoint data.
- Regression: existing options strategies unchanged.

## 7. Experiment plan

1. Free descriptive step: put-minus-call IV at equal |delta| in the existing day-0 chain for decile 10 versus decile 1 (is the skew sign/size what the thesis needs?).
2. Spread/OI gate (both legs fundable in >= 25% of decile-10 events at `rel_spread <= 0.15`).
3. Baselines: `options_cap20/30/worst_case`, OPT-02 headline cell, stock control (`steady_drift_plus` equal-weight 40-session on the same events).
4. Search window grid (call_delta x put_delta x hold_h x collateral_frac = 2x2x2x2 = 16), log trials; holdout once; stresses; placebo (shuffle deciles).
5. Tail: stress day report (2008Q4) and max loss as % NAV; compare against OPT-02 at equal downside (matched max-loss).
6. Blend with `steady_drift_plus` only after standalone survival; check that short-put collateral does not double count the equity book's cash.

## 8. Expected outcome, honest risks

Expected: marginal; the structure doubles spread cost while the drift edge is small; a possible bright spot is the skew premium. Risks: crash risk (short put assigned into 2008-style gaps in correlated names; every position is long the same factor), cash-secured does not mean low risk, wide OTM spreads, OI filter coverage, stale put quotes giving fake credits, EOD-only checkpoints can miss stop levels, data ends 2013, no borrow needed (a plus), early assignment approximated.

## 9. Dependencies and implementation checklist

Depends: OPT-01 REF-chain-v2, REF-path-quotes, REF-fill-model (signed legs), REF-leg-engine (collateral), REF-assignment, IMP-rates; OPT-02 for the comparison baseline.
- [ ] skew descriptive report
- [ ] gate on two-leg liquidity
- [ ] leg-table prep + tests
- [ ] collateral-mode engine tests
- [ ] strategy registered; grid; holdout; stress day report
