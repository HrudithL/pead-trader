"""
Strategy 8, Tier 4: finalize the compute-heavy strategy the pipeline above was built for --
blends the walk-forward ML score (33) with the original SUE-rank tilt, at the (trim, tilt_power,
ml_blend_weight, base_unit_fraction, max_gross_leverage) combo 34_gpu_param_sweep.py selected on
its search window, then re-simulates the FULL history at daily precision (not the sweep's cheaper
quarterly approximation) -- the same NAV-loop construction 23/24_finalize_strategyN.py use for
every prior strategy, so Strategy 8's numbers are directly comparable to theirs.

Reads data/equity_gpu_sweep_best.json (34's output) for every tunable; this script has no
hardcoded strategy constants of its own -- the whole point of building the sweep was to stop
hand-picking them.

Produces the same output shape as strategies 4-7 so it drops into the same comparison/report
tooling: data/positions_strategy8_v2.parquet, data/backtest_v2_strategy8.csv,
data/backtest_v2_strategy8_quarterlog.csv, data/backtest_v2_strategy8_summary.json.

data/backtest_v2_comparison.csv (the running table 20/23/24 append to) is only updated when NOT
running --mock-data -- mock-mode numbers are fixture output for testing this script's own
plumbing, not a real result, and must never overwrite that tracked, real-numbers file.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, RAW_EQUITY_DIR, INITIAL_CAPITAL
from lib.gpu import add_mock_data_arg, add_smoke_test_arg, StageTimer, make_mock_backtest_inputs, \
    make_mock_ml_rank_proxy
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
        pos["ml_rank_pct"] = make_mock_ml_rank_proxy(pos)
        return pos, eq_ret, eq_date, pd.DatetimeIndex(calendar)

    print("loading equity_feature_panel.parquet + equity_ml_signal.parquet...")
    pos = pd.read_parquet(DATA / "equity_feature_panel.parquet")
    ml = pd.read_parquet(DATA / "equity_ml_signal.parquet", columns=["event_id", "ml_rank_pct"])
    pos = pos.merge(ml, on="event_id", how="inner")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    args = parser.parse_args()

    with StageTimer("35_finalize_strategy8", extra={"mock_data": args.mock_data}):
        best_path = DATA / "equity_gpu_sweep_best.json"
        if not best_path.exists():
            raise FileNotFoundError(
                f"{best_path} not found. Run 34_gpu_param_sweep.py first "
                f"(with the same --mock-data/--smoke-test flags if using those here).")
        with open(best_path) as f:
            best = json.load(f)
        TRIM, TILT_POWER, LAM = float(best["trim"]), float(best["tilt_power"]), float(best["ml_blend_weight"])
        BUF, MAX_GROSS_LEVERAGE = float(best["base_unit_fraction"]), float(best["max_gross_leverage"])
        print(f"using sweep-selected combo: trim={TRIM} tilt_power={TILT_POWER} "
              f"ml_blend_weight={LAM} base_unit_fraction={BUF} max_gross_leverage={MAX_GROSS_LEVERAGE}")

        pos, eq_ret, eq_date_arr, calendar_dt = load_inputs(args)
        n_days = len(calendar_dt)
        date_to_idx = {d: i for i, d in enumerate(calendar_dt)}
        quarter_of = calendar_dt.to_period("Q")
        unique_quarters = quarter_of.unique().sort_values()
        n_quarters = len(unique_quarters)
        quarter_code_map = {q: i for i, q in enumerate(unique_quarters)}
        quarter_code_of_day = np.array([quarter_code_map[q] for q in quarter_of])

        pos = pos.reset_index(drop=True)
        n_pos = len(pos)
        entry_qcode = pos["day0_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
        exit_qcode = pos["exit_date"].dt.to_period("Q").map(quarter_code_map).to_numpy()
        liq_series = pos["liquidity_quintile"] if "liquidity_quintile" in pos.columns else pd.Series(np.nan, index=pos.index)
        cost_bps = liq_series.map(COST_BPS).fillna(DEFAULT_COST_BPS).to_numpy()
        adv21 = pos["avg_dollar_volume_21d"].to_numpy() if "avg_dollar_volume_21d" in pos.columns else np.full(n_pos, np.inf)
        entry_cal_idx = pos["day0_date"].map(date_to_idx).to_numpy()
        exit_cal_idx = pos["exit_date"].map(date_to_idx).to_numpy()

        entry_idx = pos["entry_row_idx"].to_numpy()
        exit_idx = pos["exit_row_idx"].to_numpy()
        lengths = exit_idx - entry_idx
        total_rows = int(lengths.sum())
        starts = entry_idx + 1
        rep_pos_idx = np.repeat(np.arange(n_pos), lengths)
        offsets = np.arange(total_rows) - np.repeat(np.cumsum(lengths) - lengths, lengths)
        flat_eq_row = np.repeat(starts, lengths) + offsets
        flat_ret = eq_ret[flat_eq_row]
        flat_date = eq_date_arr[flat_eq_row]
        flat_cal_idx = calendar_dt.searchsorted(flat_date)
        flat_qcode = quarter_code_of_day[flat_cal_idx]
        order = np.argsort(flat_qcode, kind="stable")
        flat_qcode_sorted, flat_ret_sorted, rep_pos_idx_sorted = flat_qcode[order], flat_ret[order], rep_pos_idx[order]
        boundaries = np.searchsorted(flat_qcode_sorted, np.arange(n_quarters + 1))

        sue_rank = pos["sue_rank_pct"].to_numpy()
        ml_rank = pos["ml_rank_pct"].fillna(pos["sue_rank_pct"]).to_numpy()
        combined = (1 - LAM) * sue_rank + LAM * ml_rank
        weight = weights_size_sector_neutral(pos, combined - 0.5, TRIM, TILT_POWER)

        liq_cap_dollars = np.where(np.isfinite(adv21), LIQUIDITY_CAP_FRAC * adv21, np.inf)
        trailing_nav = INITIAL_CAPITAL
        notional = np.zeros(n_pos)
        base_unit_at_entry = np.zeros(n_pos)
        quarter_log = []

        for qi in range(n_quarters):
            q_mask = (entry_qcode == qi) & (weight != 0)
            base_unit = trailing_nav * BUF
            scale_note = "ok"
            n_dropped_q = 0
            if q_mask.any():
                idx = np.where(q_mask)[0]
                target = weight[idx] * base_unit
                cap = liq_cap_dollars[idx]
                target = np.sign(target) * np.minimum(np.abs(target), cap)
                gross_requested = float(np.abs(target).sum())
                max_gross = MAX_GROSS_LEVERAGE * trailing_nav
                q_notional = target.copy()
                if gross_requested > max_gross and gross_requested > 0:
                    scale_note = "priority_capped"
                    order_priority = np.argsort(-np.abs(target))
                    cum = np.cumsum(np.abs(target[order_priority]))
                    keep_mask = cum <= max_gross
                    q_notional = np.zeros_like(target)
                    q_notional[order_priority[keep_mask]] = target[order_priority[keep_mask]]
                    n_kept = keep_mask.sum()
                    if n_kept < len(target):
                        remaining = max_gross - (cum[n_kept - 1] if n_kept > 0 else 0.0)
                        boundary = order_priority[n_kept]
                        if remaining > 0:
                            q_notional[boundary] = np.sign(target[boundary]) * min(abs(target[boundary]), remaining)
                        n_dropped_q = len(target) - n_kept - (1 if remaining > 0 else 0)
                notional[idx] = q_notional
                base_unit_at_entry[idx] = base_unit
            s, e = boundaries[qi], boundaries[qi + 1]
            trade_pnl_q = float(np.sum(notional[rep_pos_idx_sorted[s:e]] * flat_ret_sorted[s:e])) if e > s else 0.0
            entry_cost_q = float(np.sum(np.abs(notional[q_mask]) * cost_bps[q_mask] / 10_000.0)) if q_mask.any() else 0.0
            exit_mask = (exit_qcode == qi) & (weight != 0)
            exit_cost_q = float(np.sum(np.abs(notional[exit_mask]) * cost_bps[exit_mask] / 10_000.0)) if exit_mask.any() else 0.0
            quarter_pnl = trade_pnl_q - entry_cost_q - exit_cost_q
            quarter_log.append(dict(quarter=str(unique_quarters[qi]), trailing_nav_used=trailing_nav,
                                     base_unit=base_unit, n_new_positions=int(q_mask.sum()),
                                     cap_status=scale_note, n_dropped_low_conviction=n_dropped_q,
                                     quarter_realized_pnl=quarter_pnl))
            trailing_nav += quarter_pnl

        pos_out = pos.copy()
        pos_out["weight"] = weight
        pos_out = pos_out[pos_out["weight"] != 0].reset_index(drop=True)
        pos_out.to_parquet(DATA / "positions_strategy8_v2.parquet", index=False)

        entry_costs = np.abs(notional) * (cost_bps / 10_000.0)
        exit_costs = entry_costs.copy()
        daily_pnl = np.zeros(n_days)
        np.add.at(daily_pnl, entry_cal_idx, -entry_costs)
        np.add.at(daily_pnl, exit_cal_idx, -exit_costs)
        flat_notional = notional[rep_pos_idx]
        np.add.at(daily_pnl, flat_cal_idx, flat_notional * flat_ret)

        turnover_dollars = np.zeros(n_days)
        np.add.at(turnover_dollars, entry_cal_idx, np.abs(notional))
        np.add.at(turnover_dollars, exit_cal_idx, np.abs(notional))

        exposure_delta = np.zeros(n_days + 1)
        open_delta = np.zeros(n_days + 1)
        np.add.at(exposure_delta, entry_cal_idx, np.abs(notional))
        np.add.at(exposure_delta, exit_cal_idx, -np.abs(notional))
        np.add.at(open_delta, entry_cal_idx, (weight != 0).astype(float))
        np.add.at(open_delta, exit_cal_idx, -(weight != 0).astype(float))
        gross_exposure = np.cumsum(exposure_delta[:n_days])
        n_open = np.cumsum(open_delta[:n_days]).astype(int)

        nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
        daily_ret = np.diff(nav, prepend=INITIAL_CAPITAL) / np.concatenate([[INITIAL_CAPITAL], nav[:-1]])

        out = pd.DataFrame({"date": list(calendar_dt), "daily_pnl": daily_pnl, "nav": nav,
                             "gross_exposure": gross_exposure, "n_open_positions": n_open,
                             "turnover_dollars": turnover_dollars, "daily_return": daily_ret})
        out.to_csv(DATA / "backtest_v2_strategy8.csv", index=False)
        pd.DataFrame(quarter_log).to_csv(DATA / "backtest_v2_strategy8_quarterlog.csv", index=False)

        years = n_days / 252.0
        total_ret = (nav[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
        ann_ret = (1 + total_ret) ** (1 / years) - 1
        ann_vol = daily_ret.std(ddof=1) * np.sqrt(252)
        sharpe = (daily_ret.mean() * 252) / ann_vol if ann_vol > 0 else np.nan
        running_max = np.maximum.accumulate(nav)
        max_dd = ((nav - running_max) / running_max).min()
        liquidity_capped = (np.abs(weight * base_unit_at_entry) > np.abs(notional) + 1e-6) & (base_unit_at_entry > 0)
        total_costs = entry_costs.sum() + exit_costs.sum()
        annual_turnover = turnover_dollars.sum() / years / INITIAL_CAPITAL

        summary = dict(
            name="strategy8_gpu_ml_signal", trim=TRIM, tilt_power=TILT_POWER, ml_blend_weight=LAM,
            base_unit_fraction=BUF, liquidity_cap_frac=LIQUIDITY_CAP_FRAC, max_gross_leverage=MAX_GROSS_LEVERAGE,
            n_positions=int((weight != 0).sum()), total_return=total_ret, annualized_return=ann_ret,
            annualized_vol=ann_vol, sharpe=sharpe, max_drawdown=max_dd,
            avg_gross_exposure=float(gross_exposure.mean()),
            avg_leverage_vs_initial_capital=float(gross_exposure.mean() / INITIAL_CAPITAL),
            avg_n_open_positions=float(n_open.mean()), max_n_open_positions=int(n_open.max()),
            total_transaction_costs=float(total_costs), annual_turnover_x_capital=float(annual_turnover),
            pct_liquidity_capped=float(liquidity_capped.mean()), final_nav=float(nav[-1]),
            n_quarters_leverage_capped=int(sum(1 for q in quarter_log if q["cap_status"] == "priority_capped")),
            total_positions_dropped_by_priority_cap=int(sum(q["n_dropped_low_conviction"] for q in quarter_log)),
            sweep_search_sharpe=best.get("search_sharpe"), sweep_holdout_sharpe=best.get("holdout_sharpe"),
        )
        with open(DATA / "backtest_v2_strategy8_summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(f"strategy8: ann.ret={ann_ret:.2%} ann.vol={ann_vol:.2%} Sharpe={sharpe:.3f} "
              f"maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f}")

        if args.mock_data:
            print("--mock-data: skipping backtest_v2_comparison.csv update (mock numbers are not "
                  "a real result -- run for real to update the tracked comparison table).")
            return

        comparison_rows = []
        for fname in ["extreme", "rankweighted", "balanced", "strategy4", "strategy5", "strategy6"]:
            with open(DATA / f"backtest_v2_{fname}_summary.json") as f:
                comparison_rows.append(json.load(f))
        comparison_rows.append(summary)
        pd.DataFrame(comparison_rows).to_csv(DATA / "backtest_v2_comparison.csv", index=False)
        print("\nwrote data/backtest_v2_comparison.csv (7 strategies, extreme..strategy8)")


if __name__ == "__main__":
    main()
