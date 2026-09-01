"""
Tier 1.5: backtests the Kelly-optimal contract picks from scripts 46-49 for real, the same way
scripts/40_options_backtest.py backtests the near-ATM heuristic picks -- so the two are directly,
honestly comparable rather than one being a real backtest and the other just a selector's own
self-reported expected growth.

Two things this does differently from script 40, both principled, not arbitrary:

1. **Only trades events with a genuinely positive edge.** `kelly_growth > 0` means the selector
   found at least one contract in the real chain with a positive expected LOG return at its own
   optimal sizing -- i.e., a real advantage, not just "the least bad of a mediocre set". This
   replaces script 40's SUE-rank TRIM cutoff with a cutoff based on the actual math instead of an
   arbitrary rank threshold.
2. **Sizes each position by its own Kelly fraction, at HALF-KELLY.** Full Kelly sizing is only
   optimal for a perfectly known probability distribution; `decile_return_distributions.parquet`
   is an ESTIMATE from a finite historical sample, and full Kelly is a well-documented, provable
   over-bettor under estimation error (the classic practitioner fix is fractional Kelly -- this
   uses the standard half-Kelly, trading some theoretical growth rate for materially lower
   variance / risk of ruin under that estimation uncertainty). A portfolio-level premium-at-risk
   cap (same style as script 40's MAX_PREMIUM_FRAC, priority-ordered by kelly_growth so the
   strongest-edge picks get funded first) still applies on top, exactly like script 40.

Run for the same 6 hold horizons as script 40, for a direct side-by-side.

## Read this before trusting the headline numbers

This backtest's realized P&L is real (actual historical forward option quotes, script 49) -- but
which contract to buy, and how much, was decided by scripts/46's return distribution, which is
fit on the FULL 1996-2013 sample at once (see that script's docstring for the full caveat). That
is look-ahead: a 1998 trade's sizing/selection was informed by data through 2013. The first real
run of this script produced Sharpe ~4 at 60d, which is not a number to trust at face value --
it is almost certainly inflated by this. What the run DOES validate cleanly: the Kelly-optimal
selector, using only each event's own decile-conditioned distribution and no hard-coded call/put
rule, discovered the right side of the PEAD trade on its own (36% put share at D1 vs. 6% at D10).
The real next step is an expanding-window rebuild of script 46 before this Sharpe number means
anything as an achievable, real-time-tradeable result.

Output: data/backtest_options_optimal_h<H>d.csv / _summary.json
        data/backtest_options_optimal_comparison.csv
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from gpu_lib import StageTimer

DATA = Path("data")
INITIAL_CAPITAL = 10_000_000.0
HALF_KELLY = 0.5
MAX_PREMIUM_FRAC = 0.20
ASSUMED_ROUNDTRIP_COST_PCT = 0.03  # same documented assumption as script 40 -- see its docstring
CONTRACT_MULTIPLIER = 100
HORIZONS = [1, 5, 10, 20, 40, 60]


def run_backtest(panel: pd.DataFrame, hold_horizon: int, cal_dates: np.ndarray) -> dict:
    n_days = len(cal_dates)
    active = panel["kelly_growth"] > 0
    entry_mid = panel["kelly_premium"].to_numpy()
    kelly_frac = panel["kelly_fraction"].to_numpy() * HALF_KELLY

    day0 = pd.to_datetime(panel["day0_date"]).to_numpy().astype("datetime64[D]")
    cal_dates_d = cal_dates.astype("datetime64[D]")
    entry_cal_idx = np.searchsorted(cal_dates_d, day0)

    checkpoints = [h for h in HORIZONS if h <= hold_horizon]
    fits_calendar = (entry_cal_idx + checkpoints[-1]) < n_days
    valid = active.to_numpy() & np.isfinite(entry_mid) & (entry_mid > 0) & fits_calendar
    idx_all = np.where(valid)[0]
    n_pos = len(idx_all)

    price_cols = [entry_mid[idx_all]]
    for h in checkpoints:
        fwd_col = f"kelly_fwd_mid_{h}d"
        fwd = panel[fwd_col].to_numpy()[idx_all] if fwd_col in panel.columns else np.full(n_pos, np.nan)
        price_cols.append(fwd)
    price_matrix = pd.DataFrame(np.column_stack(price_cols)).ffill(axis=1).to_numpy()
    price_deltas = np.diff(price_matrix, axis=1)

    cal_idx_matrix = entry_cal_idx[idx_all][:, None] + np.array(checkpoints)[None, :]
    rep_pos_idx = np.repeat(idx_all, len(checkpoints))
    flat_cal_idx = cal_idx_matrix.ravel()
    flat_delta = price_deltas.ravel()

    quarter_of_day = pd.PeriodIndex(cal_dates_d, freq="Q")
    unique_quarters = quarter_of_day.unique().sort_values()
    n_quarters = len(unique_quarters)
    qcode_map = {q: i for i, q in enumerate(unique_quarters)}
    quarter_code_of_day = np.array([qcode_map[q] for q in quarter_of_day])

    entry_qcode = quarter_code_of_day[entry_cal_idx[idx_all]]
    flat_qcode = quarter_code_of_day[flat_cal_idx]
    order = np.argsort(flat_qcode, kind="stable")
    flat_qcode_sorted = flat_qcode[order]
    flat_delta_sorted = flat_delta[order]
    rep_pos_idx_sorted = rep_pos_idx[order]
    boundaries = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))

    num_contracts = np.zeros(len(panel))
    entry_cost = np.zeros(len(panel))
    trailing_nav = INITIAL_CAPITAL
    quarter_log = []

    for qi in range(n_quarters):
        q_local = np.where(entry_qcode == qi)[0]
        q_global = idx_all[q_local]
        scale_note = "ok"
        if len(q_global):
            # Each position's OWN kelly_frac is the single-bet-Kelly-optimal size IF IT WERE THE
            # ONLY BET IN THE BOOK. With ~1,000+ concurrent positive-edge candidates most
            # quarters, sizing every one of them independently at its own ~5-10%-of-NAV fraction
            # overcommits capital by two orders of magnitude -- the priority cap then collapses
            # to funding only the single highest-edge handful each quarter (~46 positions total
            # across the whole 17-year backtest, found on this script's first real run). The fix:
            # treat MAX_PREMIUM_FRAC as the book's fixed total allocation, and distribute it
            # across that quarter's candidates IN PROPORTION to their own kelly_frac (their
            # relative edge/sizing conviction) -- the same portfolio-construction idea the equity
            # strategies already use (weight-normalize within a group instead of sizing each
            # position off an independent single-bet rule).
            q_kelly = kelly_frac[q_global]
            book_budget = MAX_PREMIUM_FRAC * trailing_nav
            proportional_target = (q_kelly / q_kelly.sum()) * book_budget if q_kelly.sum() > 0 else np.zeros_like(q_kelly)
            contracts = np.floor(proportional_target / (CONTRACT_MULTIPLIER * entry_mid[q_global]))
            num_contracts[q_global] = contracts
            entry_cost[q_global] = contracts * CONTRACT_MULTIPLIER * entry_mid[q_global] * ASSUMED_ROUNDTRIP_COST_PCT

        s, e = boundaries[qi], boundaries[qi + 1]
        trade_pnl_q = float(np.sum(num_contracts[rep_pos_idx_sorted[s:e]] * CONTRACT_MULTIPLIER *
                                    flat_delta_sorted[s:e])) if e > s else 0.0
        entry_cost_q = float(entry_cost[q_global].sum()) if len(q_global) else 0.0
        quarter_pnl = trade_pnl_q - entry_cost_q
        quarter_log.append(dict(quarter=str(unique_quarters[qi]), trailing_nav_used=trailing_nav,
                                 n_new_positions=int((num_contracts[q_global] > 0).sum())
                                 if len(q_global) else 0, cap_status=scale_note,
                                 quarter_realized_pnl=quarter_pnl))
        trailing_nav += quarter_pnl

    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal_idx[idx_all], -entry_cost[idx_all])
    np.add.at(daily_pnl, flat_cal_idx, num_contracts[rep_pos_idx] * CONTRACT_MULTIPLIER * flat_delta)

    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    prior_nav = np.concatenate([[INITIAL_CAPITAL], nav[:-1]])
    daily_ret = daily_pnl / prior_nav
    years = n_days / 252.0
    total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else np.nan
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()
    n_sized = int((num_contracts > 0).sum())

    out = pd.DataFrame({"date": cal_dates_d, "daily_pnl": daily_pnl, "nav": nav, "daily_return": daily_ret})
    out.to_csv(DATA / f"backtest_options_optimal_h{hold_horizon}d.csv", index=False)

    summary = dict(hold_horizon_days=hold_horizon, n_positions_eligible=n_pos, n_positions_sized=n_sized,
                    total_return=float(total_ret), annualized_return=float(ann_ret),
                    annualized_vol=float(ann_vol), sharpe=float(sharpe), max_drawdown=float(max_dd),
                    final_nav=float(nav[-1]), half_kelly_multiplier=HALF_KELLY,
                    assumed_roundtrip_cost_pct=ASSUMED_ROUNDTRIP_COST_PCT)
    with open(DATA / f"backtest_options_optimal_h{hold_horizon}d_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  h={hold_horizon:>2}d: n_eligible={n_pos:,} n_sized={n_sized:,} ann.ret={ann_ret:.2%} "
          f"ann.vol={ann_vol:.2%} Sharpe={sharpe:.2f} maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()

    with StageTimer("50_options_backtest_optimal"):
        panel = pd.read_parquet(DATA / "event_options" / "optimal_forward_prices.parquet")
        cal = pd.read_parquet(DATA / "metadata" / "om_trading_calendar.parquet")
        cal_dates = cal.sort_values("date")["date"].to_numpy()
        print(f"panel: {len(panel):,} events, {int((panel['kelly_growth']>0).sum()):,} with "
              f"kelly_growth > 0")

        results = [run_backtest(panel, h, cal_dates) for h in HORIZONS]
        pd.DataFrame(results).to_csv(DATA / "backtest_options_optimal_comparison.csv", index=False)
        print("\nwrote data/backtest_options_optimal_comparison.csv")


if __name__ == "__main__":
    main()
