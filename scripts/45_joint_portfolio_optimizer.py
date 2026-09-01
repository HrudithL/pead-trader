"""
Tier 4: given the equity alpha book (Strategy 6), the market-beta overlay, and an options sleeve
(the best Tier 1/2/3 result available) all exist as independent daily-return series, what's the
best JOINT allocation across all three at once, rather than treating the options sleeve as a
totally separate decision from the equity strategy the rest of this project already finalized?

This is deliberately scoped as a FIRST CUT at Tier 4, not the full vision described in the
README's roadmap table (multi-leg option structures, regime-conditional sizing) -- those depend
on Tier 2/3's real multi-hour/day GPU results actually existing first. What's built here is
already the largest tensor in the project: each sleeve gets its own independent weight/leverage
scalar (not a sum-to-1 simplex -- exactly like the existing beta overlay, which is already "a
SEPARATE position sized to 0.5x trailing NAV held ALONGSIDE the untouched alpha book", scripts
23-25's own methodology, not a reallocation of a fixed pool), and every combination of the three
sleeves' weights is evaluated at once as an (n_days, n_w_equity, n_w_options, n_w_beta) tensor of
daily portfolio returns, reduced to Sharpe/return/drawdown per combination -- the same
"batch the parameter grid as extra tensor axes" pattern as scripts 42 and 43, just with a third
weight axis instead of one or two.

Inputs (real data, when not --mock-data):
  data/backtest_v2_strategy6.csv               (equity alpha sleeve, `daily_return` column)
  data/backtest_v2_strategy6_beta050.csv        (beta overlay sleeve's OWN incremental return --
                                                  see note below on isolating it)
  data/backtest_options_v1_h<H>d.csv            (options sleeve; H auto-picked as the horizon
                                                  with the best standalone Sharpe in
                                                  data/backtest_options_v1_comparison.csv, unless
                                                  --options-horizon is given)

Output: data/joint_portfolio_summary.json  (best combo + its metrics)
        data/joint_portfolio_sweep.csv     (every combo's Sharpe/return/maxDD)

CAVEAT, printed again at runtime -- read before trusting a headline number out of this script:
w_equity/w_options/w_beta > 1.0 means literally multiplying that sleeve's OWN daily return by the
weight -- a costless, unconstrained linear leverage assumption with no margin/borrowing cost, no
capacity limit, and no check that 1+w*r stays sane on a bad day. Strategy 6 is ALREADY internally
leveraged up to 2.5x; w_equity=1.5 on top of that is assuming ~3.75x effective equity leverage is
free and unlimited, which it never is in practice. Treat any combo with weights above ~1.0 as an
upper-bound signal from the search (interesting *direction*, not an achievable number) unless a
real cost-of-leverage term is added -- this first-cut script does not yet do that.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from gpu_lib import get_backend, to_host, add_device_arg, add_mock_data_arg, add_smoke_test_arg, \
    StageTimer

DATA = Path("data")


def load_pnl_series(path, mock_seed=0, mock_mean_pnl=2000.0, mock_vol_pnl=60000.0, n_days=4500):
    """Load a sleeve's DOLLAR daily P&L (not its `daily_return` column). This matters: every
    backtest CSV in this repo (17 onward, including this project's own Strategy 6) still has its
    `daily_return` column computed as `daily_pnl / INITIAL_CAPITAL` (a fixed constant) -- the
    exact bug the README's "note on the Sharpe calculation" describes as found-and-fixed for the
    *headline metrics*, but the per-day `daily_return` COLUMN in these CSVs was never
    regenerated with the fix. Compounding that column multiplicatively (cumprod) to combine
    sleeves -- an earlier version of this script did exactly that -- silently reintroduces the
    bug and badly distorts the result (verified: replaying Strategy 6 alone through that path
    inflates its real, documented $34.0M/-16.6%-drawdown outcome into a fictitious
    $96.0M/-44.6%-drawdown one). Working in raw dollar P&L and deriving percentage returns
    ourselves via `pnl / prior_day_nav` (see `sharpe_return_dd`) avoids inheriting the bug."""
    if path.exists():
        df = pd.read_csv(path, parse_dates=["date"])
        return df.set_index("date")["daily_pnl"]
    rng = np.random.default_rng(mock_seed)
    dates = pd.bdate_range("1996-01-01", periods=n_days)
    return pd.Series(rng.normal(mock_mean_pnl, mock_vol_pnl, size=n_days), index=dates)


def pick_best_options_horizon(comparison_path, requested_horizon=None):
    if requested_horizon is not None:
        return requested_horizon
    if not comparison_path.exists():
        return 60
    comp = pd.read_csv(comparison_path)
    return int(comp.sort_values("sharpe", ascending=False).iloc[0]["hold_horizon_days"])


INITIAL_CAPITAL = 10_000_000.0  # matches every other backtest in this repo (script 24 etc.)


def sharpe_return_dd(daily_pnl: np.ndarray):
    """Takes DOLLAR daily P&L (not a return series) and derives NAV/returns the same way
    script 24's corrected calculation does: daily_return[t] = pnl[t] / nav[t-1], never
    pnl[t] / a fixed initial capital -- see load_pnl_series's docstring for why that distinction
    matters here specifically."""
    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    prior_nav = np.concatenate([[INITIAL_CAPITAL], nav[:-1]])
    daily_ret = daily_pnl / prior_nav
    ann_ret = (nav[-1] / INITIAL_CAPITAL) ** (252.0 / len(daily_pnl)) - 1.0 if len(daily_pnl) else np.nan
    ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
    running_max = np.maximum.accumulate(nav)
    max_dd = ((nav - running_max) / running_max).min()
    return sharpe, ann_ret, ann_vol, max_dd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_device_arg(parser)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    parser.add_argument("--options-horizon", type=int, default=None,
                         help="force a specific options hold horizon instead of auto-picking "
                              "the best-Sharpe one from data/backtest_options_v1_comparison.csv")
    parser.add_argument("--w-equity-grid", type=str, default="0.0,0.5,1.0,1.5,2.0")
    parser.add_argument("--w-options-grid", type=str, default="0.0,0.5,1.0,2.0,3.0,4.0")
    parser.add_argument("--w-beta-grid", type=str, default="0.0,0.25,0.5,0.75,1.0")
    args = parser.parse_args()

    w_e_grid = [float(x) for x in args.w_equity_grid.split(",")]
    w_o_grid = [float(x) for x in args.w_options_grid.split(",")]
    w_b_grid = [float(x) for x in args.w_beta_grid.split(",")]
    if args.smoke_test:
        w_e_grid, w_o_grid, w_b_grid = w_e_grid[:2], w_o_grid[:2], w_b_grid[:2]
        print(f"--smoke-test: shrunk weight grids to {w_e_grid} x {w_o_grid} x {w_b_grid}")

    with StageTimer("45_joint_portfolio_optimizer", extra={"device": args.device}):
        xp, resolved_device = get_backend(args.device)
        print(f"backend: {xp.__name__} (device={resolved_device})")

        if args.mock_data:
            pnl_equity = load_pnl_series(Path("__nonexistent_equity__"), mock_seed=1,
                                          mock_mean_pnl=2500, mock_vol_pnl=60000)
            pnl_beta = load_pnl_series(Path("__nonexistent_beta__"), mock_seed=2,
                                        mock_mean_pnl=3500, mock_vol_pnl=110000)
            pnl_options = load_pnl_series(Path("__nonexistent_options__"), mock_seed=3,
                                           mock_mean_pnl=3000, mock_vol_pnl=90000)
            options_h = args.options_horizon or 60
        else:
            pnl_equity_full = load_pnl_series(DATA / "backtest_v2_strategy6.csv")
            pnl_beta050_full = load_pnl_series(DATA / "backtest_v2_strategy6_beta050.csv")
            # the beta overlay file's daily_pnl is the COMBINED (alpha + overlay) dollar P&L, not
            # the overlay's own incremental contribution -- subtract the alpha-only series (same
            # dates) to isolate what the overlay itself actually contributed each day, since this
            # script treats the two as independently-weightable sleeves rather than one bundled
            # position. Subtracting DOLLAR amounts (not the flawed daily_return columns) is valid
            # regardless of the bug described in load_pnl_series's docstring.
            common_idx = pnl_equity_full.index.intersection(pnl_beta050_full.index)
            pnl_equity = pnl_equity_full.loc[common_idx]
            pnl_beta = pnl_beta050_full.loc[common_idx] - pnl_equity_full.loc[common_idx]

            options_h = pick_best_options_horizon(DATA / "backtest_options_v1_comparison.csv",
                                                   args.options_horizon)
            options_path = DATA / f"backtest_options_v1_h{options_h}d.csv"
            pnl_options = load_pnl_series(options_path)

        common_idx = pnl_equity.index.intersection(pnl_beta.index).intersection(pnl_options.index)
        pnl_equity = pnl_equity.loc[common_idx].to_numpy()
        pnl_beta = pnl_beta.loc[common_idx].to_numpy()
        pnl_options = pnl_options.loc[common_idx].to_numpy()
        n_days = len(common_idx)
        print(f"options sleeve: {options_h}d hold horizon; {n_days:,} overlapping trading days "
              f"across all three sleeves")

        w_e = xp.asarray(w_e_grid)
        w_o = xp.asarray(w_o_grid)
        w_b = xp.asarray(w_b_grid)
        pe = xp.asarray(pnl_equity)[:, None, None, None]
        po = xp.asarray(pnl_options)[:, None, None, None]
        pb = xp.asarray(pnl_beta)[:, None, None, None]

        # (n_days, I, J, K) -- every (w_equity, w_options, w_beta) combo's daily DOLLAR portfolio
        # P&L at once (weight literally scales that sleeve's own dollar P&L before summing -- see
        # the module-level leverage caveat). This is the largest tensor built anywhere in this
        # pipeline: n_days can be ~4500 and the grid a few hundred combos, so this is explicitly
        # sized for the 5090's memory, not this dev machine -- --smoke-test shrinks the grids for
        # a quick local check.
        port_pnl = (pe * w_e[None, :, None, None] + po * w_o[None, None, :, None] +
                    pb * w_b[None, None, None, :])

        port_pnl_h = to_host(xp, port_pnl)  # (n_days, I, J, K)
        rows = []
        for i, we in enumerate(w_e_grid):
            for j, wo in enumerate(w_o_grid):
                for k, wb in enumerate(w_b_grid):
                    sharpe, ann_ret, ann_vol, max_dd = sharpe_return_dd(port_pnl_h[:, i, j, k])
                    rows.append(dict(w_equity=we, w_options=wo, w_beta=wb, sharpe=sharpe,
                                      annualized_return=ann_ret, annualized_vol=ann_vol,
                                      max_drawdown=max_dd))
        results = pd.DataFrame(rows)
        results.to_csv(DATA / "joint_portfolio_sweep.csv", index=False)

        best = results.sort_values("sharpe", ascending=False).iloc[0]
        summary = dict(options_horizon_used=options_h, n_days=n_days,
                        n_combos=len(results), best=best.to_dict())
        with open(DATA / "joint_portfolio_summary.json", "w") as f:
            json.dump(summary, f, indent=2, default=float)
        print(f"\nswept {len(w_e_grid)} x {len(w_o_grid)} x {len(w_b_grid)} = {len(results)} combos")
        print(f"\nbest combo: w_equity={best.w_equity}, w_options={best.w_options}, "
              f"w_beta={best.w_beta} -> Sharpe={best.sharpe:.2f}, "
              f"ann.ret={best.annualized_return:.2%}, maxDD={best.max_drawdown:.2%}")
        if max(best.w_equity, best.w_options, best.w_beta) > 1.0:
            print("CAVEAT: the winning combo scales at least one sleeve's return by >1x -- see "
                  "the module docstring. This assumes free, unlimited leverage on top of a "
                  "strategy (Strategy 6) that is already leveraged up to 2.5x internally. Treat "
                  "this as a directional signal from the search, not an achievable number, until "
                  "a real cost-of-leverage term is added.")
        print(f"\nwrote {DATA / 'joint_portfolio_summary.json'} and "
              f"{DATA / 'joint_portfolio_sweep.csv'}")


if __name__ == "__main__":
    main()
