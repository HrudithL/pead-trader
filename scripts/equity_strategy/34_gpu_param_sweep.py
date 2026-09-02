"""
Strategy 8, Tier 3: a real GPU-batched hyperparameter sweep, in the same spirit as the
options-strategy pipeline's 43_gpu_param_sweep.py -- instead of hand-picking (trim, tilt_power,
base_unit_fraction, leverage cap) the way strategies 4-7 did, search a grid, and now ALSO search
how much weight to put on the walk-forward ML score (33_gpu_walkforward_signal.py) vs. the
original SUE-rank tilt every prior strategy used alone.

Two axes, on purpose kept as two different kinds of loop:

  - trim / tilt_power / ml_blend_weight (lambda) change WHICH positions are in the book and how
    much each one weighs -- a different weight vector has to be computed for each combination, so
    these stay a (small) Python loop.
  - base_unit_fraction / max_gross_leverage only change how a FIXED weight vector gets scaled and
    capped each quarter -- they don't change which positions are held, so this axis is the one
    genuinely vectorized as an extra array dimension: the whole quarterly NAV simulation for every
    (buf, leverage) combination runs as ONE batch of dense array ops per quarter (`simulate_batch`
    below), on numpy or cupy depending on --device, instead of one Python-level backtest per combo.

Search discipline: results are ranked by Sharpe on the search window only (the first
`--search-frac` of quarters, default 70%); the remaining quarters are held out and never used to
pick a winner, only reported alongside it -- the same "don't let the search see the answer" logic
the options roadmap's Tier 1.5 walk-forward selector was built around. The sweep loop itself uses
QUARTERLY-granularity P&L (cheap: one pnl scalar per quarter per combo) to keep the grid search
fast; 35_finalize_strategy8.py reruns only the ONE combo this script selects at full DAILY
precision (the same convention every prior strategy's own backtest uses) for the real reported
numbers.

Output: data/equity_gpu_sweep_results.csv (one row per combo) and
data/equity_gpu_sweep_best.json (the selected combo, for 35_finalize_strategy8.py to consume).
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, RAW_EQUITY_DIR, INITIAL_CAPITAL
from lib.gpu import get_backend, to_host, add_device_arg, add_mock_data_arg, add_smoke_test_arg, \
    StageTimer, make_mock_backtest_inputs, make_mock_ml_rank_proxy, mark_mock_output
from lib.positions import weights_size_sector_neutral

DATA = DATA_DIR
RAW = RAW_EQUITY_DIR
LIQUIDITY_CAP_FRAC = 0.05
COST_BPS = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
DEFAULT_COST_BPS = 15


def load_inputs(args):
    if args.mock_data:
        n_events = 400 if args.smoke_test else 4_000
        n_permnos = 40 if args.smoke_test else 200
        print(f"building mock backtest inputs ({n_permnos} permnos, {n_events} events)...")
        eq_ret, eq_permno, eq_date, calendar, pos = make_mock_backtest_inputs(
            n_events=n_events, n_permnos=n_permnos, seed=0)
        calendar_dt = pd.DatetimeIndex(calendar)
        pos["ml_rank_pct"] = make_mock_ml_rank_proxy(pos)
        return pos, eq_ret, eq_date, calendar_dt

    print("loading equity_feature_panel.parquet + equity_ml_signal.parquet...")
    pos = pd.read_parquet(DATA / "equity_feature_panel.parquet")
    ml = pd.read_parquet(DATA / "equity_ml_signal.parquet", columns=["event_id", "ml_rank_pct"])
    pos = pos.merge(ml, on="event_id", how="inner")   # inner: only walk-forward-scored events trade
    pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).reset_index(drop=True)
    pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
    pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    pos["exit_date"] = pd.to_datetime(pos["exit_date"])

    print("loading daily market-adjusted return panel...")
    frames = []
    for y in range(1995, 2015):
        frames.append(pd.read_parquet(RAW / f"equity_{y}.parquet", columns=["permno", "date", "ret"]))
    eq = pd.concat(frames, ignore_index=True)
    mkt = pd.read_parquet(RAW / "market_benchmark.parquet", columns=["date", "vwretd"])
    eq = eq.merge(mkt, on="date", how="left")
    eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
    eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
    calendar_dt = pd.DatetimeIndex(sorted(mkt["date"].unique()))
    return pos, eq["ret_mktadj"].to_numpy(), eq["date"].to_numpy(), calendar_dt


def build_calendar_arrays(pos, eq_ret, eq_date, calendar_dt):
    """Same construction every v2 backtest script (17 onward) uses: per-position flat day-by-day
    return rows, sorted by the CALENDAR quarter each individual trading day falls in (not the
    position's own entry quarter) so quarter qi's slice covers every day of P&L realized during
    that quarter regardless of which position or entry quarter it came from."""
    quarter_of = calendar_dt.to_period("Q")
    unique_quarters = quarter_of.unique().sort_values()
    n_quarters = len(unique_quarters)
    quarter_code_map = {q: i for i, q in enumerate(unique_quarters)}
    quarter_code_of_day = np.array([quarter_code_map[q] for q in quarter_of])

    n_pos = len(pos)
    entry_qcode = pos["day0_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
    exit_qcode = pos["exit_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
    liq_series = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
    cost_bps = liq_series.map(COST_BPS).fillna(DEFAULT_COST_BPS).to_numpy()
    adv21 = pos["avg_dollar_volume_21d"].to_numpy() if "avg_dollar_volume_21d" in pos.columns else np.full(n_pos, np.inf)

    entry_idx = pos["entry_row_idx"].to_numpy()
    exit_idx = pos["exit_row_idx"].to_numpy()
    lengths = exit_idx - entry_idx
    total_rows = int(lengths.sum())
    starts = entry_idx + 1
    rep_pos_idx = np.repeat(np.arange(n_pos), lengths)
    offsets = np.arange(total_rows) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    flat_eq_row = np.repeat(starts, lengths) + offsets
    flat_ret = eq_ret[flat_eq_row]
    flat_date = eq_date[flat_eq_row]
    flat_cal_idx = calendar_dt.searchsorted(flat_date)
    flat_qcode = quarter_code_of_day[flat_cal_idx]
    order = np.argsort(flat_qcode, kind="stable")
    flat_qcode_sorted = flat_qcode[order]
    flat_ret_sorted = flat_ret[order]
    rep_pos_idx_sorted = rep_pos_idx[order]
    boundaries = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))

    return dict(n_quarters=n_quarters, entry_qcode=entry_qcode, exit_qcode=exit_qcode,
                cost_bps=cost_bps, adv21=adv21, boundaries=boundaries,
                flat_ret_sorted=flat_ret_sorted, rep_pos_idx_sorted=rep_pos_idx_sorted)


def simulate_batch(xp, weight, cal, buf_grid, lev_grid, liq_cap_dollars_np, cost_bps_np):
    """Vectorized over the (buf, leverage) combo axis: one quarterly NAV path per combo, computed
    in lockstep across all combos at once. Returns quarterly_pnl, shape (n_quarters, n_combos)."""
    n_pos = len(weight)
    buf_arr, lev_arr = xp.asarray(np.repeat(buf_grid, len(lev_grid))), xp.asarray(np.tile(lev_grid, len(buf_grid)))
    n_combos = len(buf_arr)

    weight_xp = xp.asarray(weight)
    liq_cap_dollars = xp.asarray(liq_cap_dollars_np)
    cost_bps = xp.asarray(cost_bps_np)
    entry_qcode = xp.asarray(cal["entry_qcode"])
    exit_qcode = xp.asarray(cal["exit_qcode"])
    rep_pos_idx_sorted = xp.asarray(cal["rep_pos_idx_sorted"])
    flat_ret_sorted = xp.asarray(cal["flat_ret_sorted"])

    trailing_nav = xp.full(n_combos, float(INITIAL_CAPITAL))
    notional = xp.zeros((n_combos, n_pos))
    quarterly_pnl = xp.zeros((cal["n_quarters"], n_combos))

    for qi in range(cal["n_quarters"]):
        q_mask = (entry_qcode == qi) & (weight_xp != 0)
        idx = xp.where(q_mask)[0]
        if idx.size > 0:
            base_unit = trailing_nav * buf_arr                                    # (n_combos,)
            target = weight_xp[idx][None, :] * base_unit[:, None]                 # (n_combos, n_idx)
            cap = liq_cap_dollars[idx][None, :]
            target = xp.sign(target) * xp.minimum(xp.abs(target), cap)
            gross_requested = xp.abs(target).sum(axis=1)                          # (n_combos,)
            max_gross = lev_arr * trailing_nav                                    # (n_combos,)

            # priority-capped allocation, matching 35_finalize_strategy8.py's convention exactly:
            # positions are kept in |target| priority order up to max_gross, and the ONE boundary
            # position that would cross the cap is PARTIALLY filled with whatever capacity remains
            # (not dropped outright) -- otherwise the sweep ranks combos by a different, more
            # conservative portfolio than the one 35 actually finalizes at the selected combo.
            n_idx = target.shape[1]
            order = xp.argsort(-xp.abs(target), axis=1)
            sorted_abs = xp.take_along_axis(xp.abs(target), order, axis=1)
            sorted_signed = xp.take_along_axis(target, order, axis=1)
            cum = xp.cumsum(sorted_abs, axis=1)
            keep_sorted = cum <= max_gross[:, None]
            n_kept = keep_sorted.sum(axis=1)                                  # (n_combos,)
            n_kept_clamped = xp.clip(n_kept - 1, 0, n_idx - 1)
            cum_before = xp.take_along_axis(cum, n_kept_clamped[:, None], axis=1)[:, 0]
            cum_before = xp.where(n_kept > 0, cum_before, 0.0)
            remaining = xp.maximum(max_gross - cum_before, 0.0)              # (n_combos,)
            has_boundary = n_kept < n_idx
            boundary_idx = xp.clip(n_kept, 0, n_idx - 1)
            col_idx = xp.arange(n_idx)[None, :]
            is_boundary_col = ((col_idx == boundary_idx[:, None]) & has_boundary[:, None]
                                & (remaining[:, None] > 0))
            partial_fill = xp.sign(sorted_signed) * xp.minimum(sorted_abs, remaining[:, None])
            q_notional_sorted = xp.where(keep_sorted, sorted_signed,
                                          xp.where(is_boundary_col, partial_fill, 0.0))
            inv_order = xp.argsort(order, axis=1)
            q_notional_capped = xp.take_along_axis(q_notional_sorted, inv_order, axis=1)
            need_cap = (gross_requested > max_gross)[:, None]
            q_notional = xp.where(need_cap, q_notional_capped, target)

            idx_np = to_host(xp, idx).astype(int)
            notional[:, idx_np] = q_notional

            entry_cost = xp.sum(xp.abs(q_notional) * cost_bps[idx][None, :] / 10_000.0, axis=1)
        else:
            entry_cost = xp.zeros(n_combos)

        s, e = cal["boundaries"][qi], cal["boundaries"][qi + 1]
        if e > s:
            pos_slice = rep_pos_idx_sorted[s:e]
            trade_pnl = xp.sum(notional[:, pos_slice] * flat_ret_sorted[s:e][None, :], axis=1)
        else:
            trade_pnl = xp.zeros(n_combos)

        exit_mask = (exit_qcode == qi) & (weight_xp != 0)
        exit_idx = xp.where(exit_mask)[0]
        if exit_idx.size > 0:
            exit_idx_np = to_host(xp, exit_idx).astype(int)
            exit_cost = xp.sum(xp.abs(notional[:, exit_idx_np]) * cost_bps[exit_idx][None, :] / 10_000.0, axis=1)
        else:
            exit_cost = xp.zeros(n_combos)

        q_pnl = trade_pnl - entry_cost - exit_cost
        quarterly_pnl[qi, :] = q_pnl
        trailing_nav = trailing_nav + q_pnl

    return to_host(xp, quarterly_pnl)


def summarize_combo(quarterly_pnl_col, search_end, n_positions):
    nav = INITIAL_CAPITAL + np.cumsum(quarterly_pnl_col)
    q_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], nav[:-1]])

    def stats(sl):
        r = q_ret[sl]
        if len(r) < 4 or r.std(ddof=1) == 0:
            return dict(ann_ret=np.nan, sharpe=np.nan)
        n = nav[sl][-1] if len(nav[sl]) else INITIAL_CAPITAL
        start_nav = INITIAL_CAPITAL if sl.start in (None, 0) else nav[sl.start - 1]
        years = len(r) / 4.0
        ann_ret = (n / start_nav) ** (1 / years) - 1 if start_nav > 0 and years > 0 else np.nan
        sharpe = (r.mean() * 4) / (r.std(ddof=1) * np.sqrt(4))
        return dict(ann_ret=float(ann_ret), sharpe=float(sharpe))

    search = stats(slice(0, search_end))
    holdout = stats(slice(search_end, None))
    return dict(search_ann_ret=search["ann_ret"], search_sharpe=search["sharpe"],
                holdout_ann_ret=holdout["ann_ret"], holdout_sharpe=holdout["sharpe"],
                final_nav=float(nav[-1]), n_positions=int(n_positions))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_device_arg(parser)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    parser.add_argument("--search-frac", type=float, default=0.7,
                         help="fraction of quarters (chronological, earliest first) used to rank "
                              "combos; the rest is untouched holdout. Default: %(default)s")
    args = parser.parse_args()
    xp, device = get_backend(args.device)

    if args.smoke_test:
        trim_grid, tilt_grid, lam_grid = [0.15], [1.0], [0.0, 0.5, 1.0]
        buf_grid, lev_grid = [0.002, 0.003], [1.5, 2.5]
    else:
        trim_grid, tilt_grid, lam_grid = [0.10, 0.15, 0.20], [0.75, 1.0, 1.5], [0.0, 0.25, 0.5, 0.75, 1.0]
        buf_grid, lev_grid = [0.002, 0.0025, 0.003, 0.0035, 0.004], [1.5, 2.0, 2.5, 3.0]

    with StageTimer("34_gpu_param_sweep", extra={"device": device, "mock_data": args.mock_data}):
        pos, eq_ret, eq_date, calendar_dt = load_inputs(args)
        cal = build_calendar_arrays(pos, eq_ret, eq_date, calendar_dt)
        search_end = max(1, int(round(cal["n_quarters"] * args.search_frac)))
        liq_cap_dollars = np.where(np.isfinite(cal["adv21"]), LIQUIDITY_CAP_FRAC * cal["adv21"], np.inf)

        n_signal_variants = len(trim_grid) * len(tilt_grid) * len(lam_grid)
        n_combos_per_variant = len(buf_grid) * len(lev_grid)
        print(f"sweep: {n_signal_variants} signal variants x {n_combos_per_variant} buf/leverage "
              f"combos = {n_signal_variants * n_combos_per_variant} total, backend={xp.__name__} "
              f"(device={device}), search_end=quarter {search_end}/{cal['n_quarters']}")

        sue_rank = pos["sue_rank_pct"].to_numpy()
        ml_rank = pos["ml_rank_pct"].fillna(pos["sue_rank_pct"]).to_numpy()

        rows = []
        for trim, tilt_power, lam in itertools.product(trim_grid, tilt_grid, lam_grid):
            combined = (1 - lam) * sue_rank + lam * ml_rank
            weight = weights_size_sector_neutral(pos, combined - 0.5, trim, tilt_power)
            n_positions = int((weight != 0).sum())
            if n_positions == 0:
                continue
            quarterly_pnl = simulate_batch(xp, weight, cal, buf_grid, lev_grid, liq_cap_dollars, cal["cost_bps"])
            for ci, (buf, lev) in enumerate(itertools.product(buf_grid, lev_grid)):
                stats = summarize_combo(quarterly_pnl[:, ci], search_end, n_positions)
                rows.append(dict(trim=trim, tilt_power=tilt_power, ml_blend_weight=lam,
                                  base_unit_fraction=buf, max_gross_leverage=lev, **stats))
            print(f"  trim={trim} tilt_power={tilt_power} lambda={lam}: n_positions={n_positions:,}, "
                  f"best search_sharpe this variant={max((r['search_sharpe'] for r in rows[-n_combos_per_variant:] if np.isfinite(r['search_sharpe'])), default=float('nan')):.3f}")

        results = pd.DataFrame(rows)
        results_path = DATA / "equity_gpu_sweep_results.csv"
        results.to_csv(results_path, index=False)
        mark_mock_output(results_path, is_mock=args.mock_data)
        print(f"wrote {results_path} ({len(results)} combos)")

        valid = results[np.isfinite(results["search_sharpe"])]
        if len(valid) == 0:
            raise RuntimeError("no combo produced a finite search-window Sharpe -- widen the grid "
                                "or check --search-frac against how many quarters are available")
        best = valid.loc[valid["search_sharpe"].idxmax()].to_dict()
        best_path = DATA / "equity_gpu_sweep_best.json"
        with open(best_path, "w") as f:
            json.dump(best, f, indent=2, default=str)
        mark_mock_output(best_path, is_mock=args.mock_data)
        print(f"best combo (by search-window Sharpe): {best}")
        print(f"wrote {best_path}")


if __name__ == "__main__":
    main()
