# MT-06: Cost-model extension (quoted spread, borrow, financing, impact, options friction) and cost-stress harness
- Status: planned | Priority: P1 | Effort: M | Source(s): library `foundations/03_PORTFOLIO_EXECUTION.md` (costs, short mechanics, scenarios 0/5/10/20/50 bps round trip), `comparisons/03` (5/15/30 bps one-way grid, borrow, hard-to-borrow rejection), `audits/03` sections 3-4 (liquidity, short leg, options traps), `foundations/04` (options spreads/fees), `reproduction/pead_reference.py::CostSchedule`, `strategies/05_LEEYAWNNN_EVENT_STUDY.md` (break-even cost); repo `pead/engines/equity.py`, `pead/engines/options.py`, `pead/reporting/attribution.py`

## 1. Purpose & what we take from the library
Replace one hard-coded liquidity-quintile cost table with a pluggable cost model whose components are the ones the library says decide whether a PEAD edge is real: half-spread, impact, borrow, financing/collateral, options bid/ask and commissions; and add a standard cost sweep with a **break-even cost** (the leeyawnnn pattern: strategy-specific break-even bps vs assumed vs observed).

## 2. Gap analysis vs the repo today
- Equity cost = `COST_BPS = {1:25, 2:15, 3:10, 4:7, 5:5}` bps by `liquidity_quintile`, default 15 (`equity.py:39-40`), charged on notional at entry and again at exit (`equity.py:220-232`, `cost = 2*entry_costs` in positions). Static, not spread-based, not size-aware, same in 1996 and 2013, same for longs and shorts.
- Borrow: **not modelled in the engine**. Post-hoc linear fee only: `reporting/attribution.py:99-112 borrow_cost_sensitivity` (rates 0/0.5/1/2/5%, subtracts `rate * sum(short_exposure)/252` from final NAV; hard-codes `1e7`). Financing and short collateral not modelled (FIXES.md; `steady_drift.py` docstring says collateral ties up 1.2-5.6x NAV for market-neutral books).
- Market impact: none beyond the liquidity cap `liquidity_cap_frac=0.05` of 21-day ADV (`Sizing`, `cap_notionals`).
- Overlays: beta overlay 2 bps (`apply_beta_overlay`), risk-target overlay 10 bps on exposure change (`apply_cash_and_risk_target`) -- hard-coded defaults.
- Options: `OptionsSizing.fill_mode='bid_ask'` (ask in, bid out, `options.py:44`) is already library-grade; missing: spread multiplier stress (library: 2x spreads), per-contract commissions, partial price improvement, multi-leg (two-sided) fills.
- Data for a spread model exists but is not carried into the panel: raw `data/raw_wrds/crsp/dsf_*.parquet` has `bid, ask, bidlo, askhi, openprc, numtrd` (missing share ~1% in 2008, ~1.7% in 1997); `data/normalized_equity/equity_*.parquet` kept only `prc, prc_is_bidask_avg, ret, retx, shrout, vol, cfacpr, cfacshr`.
- Dividends in lieu for shorts: because `ret` is a total return, a short's P&L (`-notional*ret`) already pays distributions -- no extra term needed (check by test), unlike the library's price-only engines.

## 3. Exact specification
```python
# pead/costs/model.py   (REF-cost-hooks)
class CostModel(Protocol):
    def one_way_bps(self, pos: DataFrame, *, side: str) -> np.ndarray      # side in {'entry','exit'}; uses only pre-day0 columns
    def carry_per_day(self, pos: DataFrame, panel_dates) -> np.ndarray | None   # (n_pos,) daily bps of notional while held, sign-aware
    def describe(self) -> dict                                             # goes into the manifest (MT-12)

class LegacyQuintileCost(CostModel):    # == COST_BPS exactly; default; keeps every NAV pin
class SpreadImpactCost(CostModel):
    half_spread_bps = max(floor_bps, k_spread * 0.5 * median_20d((ask-bid)/mid) * 1e4)   # trailing 20 sessions ending day0-1; fallback LegacyQuintile
    impact_bps = c_impact * tvol_60d_bps * sqrt(|notional| / avg_dollar_volume_21d)        # square-root law; both inputs already in events
    one_way = half_spread_bps + impact_bps
class BorrowFinance(CostModel):
    borrow_annual(size_quintile) -> float       # table parameter; default grid 0/0.5/1/2/5% (same as attribution.py) ; hard-to-borrow multiplier for bottom size & price<5
    short_rebate: float = 0.0                   # fraction of rf rebated on short proceeds; needs rf series (REF-tbill-real)
    financing_spread: float                     # charged on max(gross - NAV, 0) at rf + spread
class OptionsFriction:  # options.py
    spread_mult: float = 1.0; commission_per_contract: float = 0.65 (library example) ; legs: int ; price_improvement_frac: float = 0.0
```
Parameter provenance rules: `k_spread, c_impact, floor_bps, borrow table` are **unestimated parameters** (no execution data in the repo to calibrate; library gives only grids). They always run as a grid, never a point estimate; the report prints the whole curve plus the **break-even multiplier** `m*` such that CAGR_active(m* x cost) = 0 and alpha_HAC(m* x cost) = 0 (bisection, 12 iterations).

Engine hook (`equity.py`): replace the vector `cost_bps = liq.map(COST_BPS)...` (line 169) by `cm.one_way_bps(pos, side='entry')` / `side='exit'`; add the carry as a daily P&L term via `np.add.at(daily_pnl, flat_cal, -carry_per_day[rep]*|notional[rep]|/1e4)` for the holding days (needs `rep`, `flat_cal`, already computed at `equity.py` ~180-190). Sizing feedback of carry on trailing NAV is automatic because carry is inside `daily_pnl` and `q_pnl` is derived from the same flat arrays only if added to the per-quarter `trade_pnl`; implement it there so compounding is consistent (otherwise borrow would be post-hoc and understate the sizing feedback) and add a test.

Standard cost sweep `pead/eval/costs.py::cost_sweep(strategy)`: runs the backtest for each point of `{equity_round_trip_bps: 0,5,10,20,50} x {borrow: 0,0.5,1,2,5%} x {spread_mult 1,2 (options)}`, returns a tidy frame with CAGR/Sharpe/alpha/turnover/total_cost and `breakeven_mult`. Capacity curve: re-run with `initial_capital` x {0.1,1,10,100} (liquidity cap binds) -> `capacity_curve.csv` (consolidates `capacity_sweep.csv` logic used by `paper.py`).

## 4. Data requirements
Equity spread model: raw dsf `bid, ask` (present; add to normalized panel via a prep stage `panel_quotes`) -- coverage ~98-99% of rows. Impact: events columns `tvol_60d`, `avg_dollar_volume_21d` (present). Borrow rates: **BLOCKED** (no borrow/lending data; WRDS/Markit securities-lending not in repo) -> degraded proxy = grid by size quintile, flagged `borrow_source='grid'`. Short interest: BLOCKED (MT-14). Options: OptionMetrics bid/ask (present on `D:`), no depth/size so no size-based slippage.

## 5. Framework mapping
`pead/costs/{model.py,spread.py,borrow.py,options.py}`; `Sizing.cost_model: str = "legacy"` (name resolved via registry, hashed into manifest); `EquityStrategy.cost_model` attribute; CLI `python -m pead costs <name...> [--grid]`, `python -m pead run <s> --cost spread_impact`; report block "Cost stress" in `reporting/single.py`; `evaluation.json` gate G3 (MT-04).

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-cost-hooks (the `CostModel` container) is implemented ONCE; EQ's REF-cost-model / REF-cost-source and REF-borrow-fee become its `SpreadImpactCost` and `BorrowFinance` components (their `Sizing` fields are parameters of those classes); OPT's REF-fill-model supplies the options leg fills (this spec's `OptionsFriction` is its parameter set).
- **NEW:** `pead/costs/` package (REF-cost-hooks); prep stage `panel_quotes` producing `data/panel_quotes.parquet` (`permno,date,bid,ask,openprc,numtrd`, same row order as the normalized panel -- also used by MT-13); `spread_bps_20d` feature column in `equity_vol_panel.parquet` (computed with the `trailing_window` helper, ending day0-1); `tests/test_costs.py`; `results/<name>/cost_sweep.csv`.
- **CHANGE:** `pead/engines/equity.py::run_equity_backtest` (cost lines 169, 220-232, 229-232; add carry term; regression risk: LOW if `LegacyQuintileCost` is bit-identical -> assert all 22 legacy pins and five corrected NAV pins unchanged before any new model is used); `apply_beta_overlay`/`apply_cash_and_risk_target` (take `cost_bps` from the model's `overlay_bps`; no change to defaults); `pead/reporting/attribution.py::borrow_cost_sensitivity` (delegate to `BorrowFinance`, use `INITIAL_CAPITAL` not `1e7`; the post-hoc method remains as a cross-check); `pead/engines/options.py::OptionsSizing` (add `spread_mult`, `commission_per_contract`, `price_improvement_frac`; defaults reproduce current results).
- **RESTRUCTURE:** move the cost constants out of `engines/equity.py` to `pead/costs/legacy.py` (re-export `COST_BPS` from the old location for `pead/live/engine.py` and tests that import it -- `grep` before moving); make the live engine use the same `CostModel` objects ("Shared by the backtest loop and the live engine so both size identically", `cap_notionals` docstring).
- **IMPROVE:** (a) cost is currently invariant to date -- 1996-2001 decimalisation changed spreads materially; the spread model captures this, the legacy table does not; (b) overlay cost defaults (2 bps, 10 bps) are undocumented guesses; (c) report `total_transaction_costs` already exists in summary (`equity.py` summary) -- add cost as % of gross P&L so "edge vs cost" is visible on the leaderboard.

## 6. Acceptance tests
- `LegacyQuintileCost` reproduces `final_nav` of `balanced_leverage`, `calm_weighted`, `calm_rank_tilt`, `options_cap20`, `options_cap30` to the cent (existing pins).
- Zero-return path with `SpreadImpactCost`: NAV falls by exactly the sum of `(entry+exit) bps x notional` (library test 3); with all components 0 NAV unchanged (library G3).
- Borrow: short notional N for d days at rate r loses `N*r*d/252` inside `daily_pnl` and in the quarter sizing NAV (test compares to hand computation); a long-only book pays zero borrow.
- Short total return test: price flat, dividend yield y paid -> short loses y (confirms `ret` is total return).
- Monotonicity: CAGR non-increasing in every cost multiplier; break-even multiplier finite and positive for `balanced_leverage`.
- Spread feature PIT: `spread_bps_20d` unchanged when quotes at/after day0 are perturbed.

## 7. Rollout plan
1. Package + `LegacyQuintileCost` + engine hook with zero numeric change (pins green). 2. `panel_quotes` stage and `spread_bps_20d`. 3. `SpreadImpactCost`, grid reports for the current top books (`steady_drift_plus`, `calm_rank_tilt`, market-neutral books). 4. Borrow/financing inside the engine. 5. Options friction. 6. Gate G3 in MT-04.

## 8. Risks
- Uncalibrated parameters can create false precision; always report curves and break-evens.
- CRSP closing bid/ask in 1996-2001 (fractions) and Nasdaq dealer quotes bias spread estimates.
- Engine edit touches the hottest code; the bit-identical default plus pins is the safeguard.
- Impact from square-root law is for order sizes relative to ADV at *daily* scale; our 5% ADV cap bounds it.

## 9. Dependencies and implementation checklist
Depends on: REF-cost-hooks, REF-tbill-real (rebate/financing), MT-03 (alpha for break-even), `panel_quotes`. Blocks: MT-04 G3, equity/options areas that add shorts or multi-leg trades.
- [ ] `CostModel` protocol + `LegacyQuintileCost` + engine hook, pins unchanged
- [ ] `panel_quotes` stage; `spread_bps_20d`
- [ ] `SpreadImpactCost` + tests
- [ ] carry inside the engine's quarter P&L; borrow tests
- [ ] cost sweep + break-even + capacity curve; report block
- [ ] options friction params
- [ ] live engine uses the same model
