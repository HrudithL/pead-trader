# MT-10: Cross-validate our engines against the library's clean-room `pead_reference.py`
- Status: planned | Priority: P1 | Effort: S | Source(s): library `reproduction/pead_reference.py`, `reproduction/test_pead_reference.py`, `reproduction/03_REFERENCE_PYTHON.md`, `reproduction/05` section G (10 invariance tests), `metadata/MANIFEST.json` (sha256), `metadata/UNIT_TEST_RESULTS.txt`

## 1. Purpose & what we take from the library
An independent, 150-line oracle for the arithmetic that matters (surprise, lagged SUE, chronology, next-open fills, fees, concurrency, drawdown with initial NAV, performance, option vertical P&L). We port it into `tests/reference/` unchanged and use it as a *second implementation* to check our engine's accounting, our metrics (MT-03), our SUE flavours (MT-07) and, later, the multi-leg options ledger. Our engines were validated against our own earlier scripts ("reproduces the original script NAV to the dollar", ARCHITECTURE.md), which shares assumptions with them; the reference is the first truly independent check.

## 2. Gap analysis vs the repo today
- No external oracle in `tests/` (`tests/test_framework.py`, `test_fixes.py`, `test_steady_drift.py` check self-consistency and pins).
- Verified 2026-10-27 in a scratch copy: the reference test-suite passes 8/8 on this repo's interpreter (Python 3.12.10, pandas 3.0.5), file hashes `pead_reference.py` sha256 `8c9fd5c8313f5606298bb4f9ad95cc15b6680af68a332382e33952711445fe95`, `test_pead_reference.py` sha256 `9713f4eefe3f46008eedf7a4bd5e924fd2f63a4b6e5fe41ba10111f927847692`. (The library's `__pycache__` contains cpython-313 bytecode; irrelevant, no import needed.)
- Structural differences between engine and reference that the port must respect (they are not bugs):
  | aspect | PEAD repo engine (`engines/equity.py`) | reference simulator |
  |---|---|---|
  | entry | at day0 **close**; P&L accrues from `entry_row_idx+1` | next **open** after a signal decided at close t |
  | sizing | fixed dollar notional set quarterly off trailing NAV, never rebalanced | target fraction of NAV, rebalanced every day |
  | costs | bps on notional at entry and exit by liquidity quintile | `stock_fee_bps` on every traded delta, daily borrow, cash/financing rates |
  | drawdown | `metrics.drawdown(nav)` without initial capital | includes initial cash as first peak (test 7) |
  | years | `len(nav)/252` (both) | `len(nav)/252` |
  | Sharpe | daily, ddof=1, rf=0 | daily, `rf_daily` param |
- Expected discrepancy to be *discovered by the port*: max drawdown when the first marked day is a loss (our `drawdown(nav)` ignores the starting NAV; see MT-03 item 2).

## 3. Exact specification
Layout: `tests/reference/pead_reference.py` and `test_pead_reference.py` copied byte-identical (record the two sha256 above in `tests/reference/SOURCE.txt` with the library release name "PEAD Research Library Expanded 2026-10-27" and "independently authored, not copied from the audited repos"; the library's own licence/usage statement should be re-read before redistribution -- if unclear, keep the files git-ignored and download-on-demand via a documented path instead of committing). Our adapter tests live in `tests/test_cross_validation.py`:

| ID | Check | Method |
|---|---|---|
| XV-01 | Library suite passes in our env | `unittest.main` wrapper; skip if file absent |
| XV-02 | P&L equivalence, frictionless | Build a 5-stock, 60-day synthetic panel with random returns. Engine side: `EquityUniverse` + events with `day0=t0`, fixed hold H, tiny notionals (0.5% NAV each, so fixed-dollar vs daily-rebalanced weights differ at second order), `cost_bps=0` via a zero cost model (MT-06 hook; until then monkeypatch `COST_BPS` to zeros and `DEFAULT_COST_BPS=0`). Reference side: `opens = closes = P`, `signals_at_close` dated `t0-1` so the reference fills at "open" of day t0 equal to the close of t0 (the same economic fill), flat `signals` to exit after H. Assert the two daily return series agree to 1e-4 in total return and 5e-5 per day; assert the alignment trick separately (a one-day-shifted adapter must disagree by the first day's return, library test 4). |
| XV-03 | Fees | Single position, constant price, `bps=b`: engine `total_transaction_costs == 2*N*b/1e4`; reference `orders.fee.sum() == 2*N*b/1e4` with `stock_fee_bps=b` (library test 5: $200 on $100k at 10 bps). Equal. |
| XV-04 | Concurrency | Two 50% positions, +2% gap: both give +2% NAV (library test 6) -- run through `run_equity_backtest` with `max_gross_leverage` large enough. |
| XV-05 | Metrics equivalence | Same NAV Series into `pead.core.metrics.summarize` (or `stats` after MT-03) and `performance_from_nav`: total_return, annualized_return, sharpe equal to 1e-12; max_drawdown equal **after** the MT-03 initial-capital fix (before it: expected-fail, recorded as `xfail(strict=True, reason=MT-03)`). |
| XV-06 | Chronology | Port `confirm_signal_not_before_release` as `event_time.assert_event_invariants` property: our event table must satisfy `released_at <= feature_available_at <= fill`; feed the library's Friday-16:20/holiday and 15:59-vs-16:05 fixtures (foundations/02) to `event_time.first_executable_session` (MT-02). |
| XV-07 | SUE oracle | `lagged_historical_sue` vs `pead.signals.sue.hist_sd` on random event frames (identical to 1e-12); `pct_surprise` vs `signals.sue.pct` (MT-07). |
| XV-08 | Options vertical | `option_debit_spread_pnl(4,1,5,1.5) == 47.4` is the acceptance oracle for the future multi-leg options ledger (REF-multileg-options); for the current single-leg engine: ask-in/bid-out P&L equals reference single-leg formula `(bid_exit-ask_entry)*100*contracts - commissions`. |
| XV-09 | Reference as drift detector | Pin: the reference's own 8 tests keep passing with our pandas pin; if pandas upgrades break them (AGENTS.md: results reproduce only under pandas 3), CI flags it early. |

Differences that are *accepted and documented* (never "fixed" to match): fixed-notional vs rebalanced weights, close vs open entry, cost schedule semantics. Each accepted difference gets one assertion that quantifies it on the synthetic case so a future engine change cannot widen it silently.

Optional extension (P2): feed a small *real* slice (e.g. 200 events of 2005) through both implementations with the real panel prices converted to a prices frame (`openprc` from raw dsf, MT-13) to check engine vs next-open reference on live-shaped data; differences must be explainable by the table above.

## 4. Data requirements
Synthetic only for XV-01..XV-09. Real-slice extension needs raw `dsf` `openprc` (present) -- see MT-13.

## 5. Framework mapping
`tests/reference/`, `tests/test_cross_validation.py`, marker `reference`; run in the default `pytest tests -q` (fast). A short note in `docs/ARCHITECTURE.md` "Numbers you can trust": two independent implementations agree on the accounting; list of accepted differences.

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** none; new (REF-reference-oracle). Complements EQ-08.
- **NEW:** `tests/reference/{pead_reference.py,test_pead_reference.py,SOURCE.txt}` (REF-reference-oracle), `tests/test_cross_validation.py`, `tests/reference/adapters.py` (engine-universe <-> frames converters).
- **CHANGE:** `pead/core/metrics.py::drawdown` (include initial capital; this is the one engine-side change the port is expected to force; regression risk: LOW -- only affects runs whose first marked day is a loss; leaderboard `max_drawdown` for such runs changes slightly) ; zero-cost configuration hook in `pead/engines/equity.py` (needs the MT-06 `CostModel`, or a constructor argument `cost_bps_override`, LOW).
- **RESTRUCTURE:** none beyond a `tests/reference/` directory and a pytest marker.
- **IMPROVE:** retire reliance on "reproduces our own original script" as the sole accounting validation; make accepted differences explicit; guard the pandas pin.

## 6. Acceptance tests
XV-01..XV-09 pass (XV-05 drawdown strict-xfail until MT-03 lands, then green). A mutation check: break the engine's `entry_idx + 1` accrual (MT-01 LA-04 twin) and confirm XV-02 fails.

## 7. Rollout plan
Copy files + XV-01; adapters + XV-02/03/04; XV-05 with xfail; XV-06/07 when MT-02/MT-07 land; XV-08 with the multi-leg options ledger.

## 8. Risks
- Licensing/redistribution of the library file -- verify before committing.
- The reference is "idealized open fills"; do not treat agreement as validation of realism, only of arithmetic.
- Tolerances must reflect the second-order fixed-vs-rebalanced difference; choose small weights so tests are not flaky.

## 9. Dependencies and implementation checklist
Depends on: MT-06 (zero-cost hook), MT-03 (drawdown), MT-07/MT-02 (for XV-06/07). Feeds: MT-01, MT-12 (`reference_agreement` flag).
- [ ] copy reference + record hashes/licence check
- [ ] XV-01/09
- [ ] adapters + XV-02/03/04
- [ ] XV-05 xfail then green
- [ ] XV-06/07/08 as dependencies land
- [ ] ARCHITECTURE.md note listing accepted differences
