"""
Tier 1 of the options-strategy roadmap (see README, "Options-strategy GPU roadmap"): turn the
descriptive options-PEAD result in PEAD_Options_Report.pdf (near-ATM call D10-D1 spread +14.38%
at 60 trading days, t=4.69; near-ATM put mirrors it at -10.79%, t=-6.29) into an actual,
capital-sized, cost-aware backtest -- something that did not exist before this script. Everything
before this was decile-sorted descriptive statistics on fixed-horizon forward returns, not a P&L
series.

Design, deliberately mirroring the equity Strategy 5/6 methodology (scripts 23/24) so the two are
directly comparable, and using the exact same "flatten once, then a true sequential quarter loop
for trailing-NAV sizing" implementation pattern as those scripts:

  - Signal: same trim/tilt scheme as scripts 23/24, but built off `decile` (1-10) rather than the
    continuous SUE rank percentile -- option_event_panel.parquet only carries the discretized
    decile (events_meta in script 36 didn't project sue_rank_pct through), so this is a coarser
    version of the same idea, not a different one. Positive tilt -> buy a near-ATM CALL; negative
    tilt -> buy a near-ATM PUT. Options are long-only here (no shorting a long option position),
    so the equity strategy's "short leg" is replaced by "buy a put", not "sell the stock".
  - Sector + size neutrality: weights are normalized within (ann_quarter, side, ff12_sector,
    size_quintile) cells, exactly like `compute_weights_size_sector` in script 24.
  - Sizing: quarterly trailing-NAV compounding (same invariant as script 24: a quarter's base
    unit is only ever set from NAV realized through the END of the prior quarter). Target premium
    per position = |weight| * base_unit, rounded down to whole contracts (100 shares/contract). A
    portfolio-level cap limits TOTAL premium outstanding at once to MAX_PREMIUM_FRAC * trailing
    NAV (the options analogue of the equity book's gross-leverage cap: a long option can only
    ever lose its premium, so this cap bounds the single worst-case max-loss of the whole book,
    not "leverage" in the equity sense) -- trimmed priority-based (lowest-conviction dropped
    first), same style as Strategy 6.
  - Mark-to-market path: the panel carries forward mid prices at {1,5,10,20,40,60} trading days
    for every held contract (script 36) -- a coarser, checkpoint-based path rather than a true
    daily series. This script marks P&L at each checkpoint <= the chosen hold horizon, forward-
    filling any missing interim checkpoint from the last valid price (a position only needs its
    OWN entry price to be valid; a missing interim mark just means "no new information," not "no
    position"), so every valid entry produces a usable, if coarser-than-daily, P&L path.
  - Transaction cost: NOT yet a real bid/ask spread -- `entry_mid`/`fwd_mid` are already
    midpoints, and best_bid/best_offer were read from the raw OM scan (options_lib.OPPRCD_COLS)
    but dropped before scripts 35/36 wrote their output. ASSUMED_ROUNDTRIP_COST_PCT below is an
    explicit, clearly-flagged assumption (documented near-ATM equity-option round-trip spread
    cost) standing in for that until a follow-up script re-scans the same (secid, date,
    optionid) keys already selected here to attach real best_bid/best_offer -- a cheap addition
    (same narrow date filter, no new full-history scan), left as a noted next step, not done here.

Run for every one of the 6 available hold horizons (cheap, and the "right" horizon for options is
genuinely unclear a priori: the call spread's t-stat is actually highest at 1 day (13.3) but
economically tiny (4.0%), and largest in magnitude at 60 days (14.4%) but noisier (t=4.69) -- a
real Sharpe-vs-magnitude tradeoff, not a single obviously-correct answer, so report all 6 rather
than silently picking one).

Output: data/backtest_options_v1_h<H>d.csv           (checkpoint-spaced NAV series, one per horizon)
        data/backtest_options_v1_h<H>d_summary.json
        data/backtest_options_v1_comparison.csv       (all 6 horizons side by side)
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, INITIAL_CAPITAL
from lib.gpu import make_mock_option_event_panel, add_mock_data_arg, StageTimer

DATA = DATA_DIR
TRIM, TILT_POWER = 0.15, 1.0
# Rough starting point, not yet swept -- with ~165k events over 65 quarters (~2,500/quarter) and
# roughly 80% surviving TRIM, sizing every one of them at BASE_UNIT_FRACTION=0.05 (the equity
# strategies' order of magnitude) overwhelms MAX_PREMIUM_FRAC immediately and the priority cap
# ends up keeping only a token handful of positions per quarter. 0.0015 is picked so a typical
# quarter's total requested premium lands near, not wildly above, the MAX_PREMIUM_FRAC cap --
# exactly the "sizing elbow" scripts 18/19 found by sweeping for the equity strategies. This
# script's own comparison output already shows the tradeoff; script 43 (Tier 3) is where this
# actually gets swept properly instead of hand-picked.
BASE_UNIT_FRACTION = 0.0015
MAX_PREMIUM_FRAC = 0.20            # cap: total outstanding premium <= 20% of trailing NAV at once
ASSUMED_ROUNDTRIP_COST_PCT = 0.03  # documented assumption -- see docstring above
CONTRACT_MULTIPLIER = 100
HORIZONS = [1, 5, 10, 20, 40, 60]


def compute_weights(panel: pd.DataFrame) -> np.ndarray:
    """Trim/tilt/sector+size-neutralize, analogous to script 24's compute_weights_size_sector,
    but decile-based (see module docstring)."""
    d = (panel["decile"].to_numpy() - 5.5) / 4.5   # in [-1, 1], D1=-1 .. D10=+1
    frac = np.clip(np.abs(d), 0, 1)
    tilt = np.sign(d) * (frac ** TILT_POWER)
    keep = np.abs(d) > TRIM
    w = np.where(keep, tilt, 0.0)
    side = np.sign(w)

    kept_idx = np.where(keep)[0]
    grp_key = pd.Series(list(zip(panel["ann_quarter"].iloc[kept_idx], side[kept_idx],
                                  panel["ff12_sector"].iloc[kept_idx],
                                  panel["size_quintile"].iloc[kept_idx])))
    qside_key = pd.Series(list(zip(panel["ann_quarter"].iloc[kept_idx], side[kept_idx])))

    w_kept = w[kept_idx]
    grp_totals = pd.Series(w_kept).groupby(grp_key.values).transform(lambda x: x.abs().sum()).to_numpy()
    n_groups = grp_key.groupby(qside_key.values).transform("nunique").to_numpy()

    safe = grp_totals > 0
    w_neutral = np.zeros(len(kept_idx))
    w_neutral[safe] = w_kept[safe] / grp_totals[safe] / np.maximum(n_groups[safe], 1)

    out = np.zeros(len(panel))
    out[kept_idx] = w_neutral
    nz = out[out != 0]
    if len(nz):
        out[out != 0] = out[out != 0] / np.abs(nz).mean() * 0.5
    return out


def run_backtest(panel: pd.DataFrame, hold_horizon: int, cal_dates: np.ndarray) -> dict:
    n_days = len(cal_dates)
    weight = compute_weights(panel)
    is_call = weight > 0
    is_put = weight < 0
    active = weight != 0

    entry_mid = np.where(is_call, panel["call_entry_mid"].to_numpy(),
                          np.where(is_put, panel["put_entry_mid"].to_numpy(), np.nan))

    day0 = pd.to_datetime(panel["day0_date"]).to_numpy().astype("datetime64[D]")
    cal_dates_d = cal_dates.astype("datetime64[D]")
    entry_cal_idx = np.searchsorted(cal_dates_d, day0)

    checkpoints = [h for h in HORIZONS if h <= hold_horizon]
    fits_calendar = (entry_cal_idx + checkpoints[-1]) < n_days
    valid = active & np.isfinite(entry_mid) & (entry_mid > 0) & fits_calendar
    idx_all = np.where(valid)[0]
    n_pos = len(idx_all)

    # ---- build each valid position's checkpoint price path, forward-filling missing marks ----
    price_cols = [entry_mid[idx_all]]
    for h in checkpoints:
        call_fwd = panel[f"call_fwd_mid_{h}d"].to_numpy()[idx_all] if f"call_fwd_mid_{h}d" in panel else np.nan
        put_fwd = panel[f"put_fwd_mid_{h}d"].to_numpy()[idx_all] if f"put_fwd_mid_{h}d" in panel else np.nan
        fwd = np.where(is_call[idx_all], call_fwd, np.where(is_put[idx_all], put_fwd, np.nan))
        price_cols.append(fwd)
    price_matrix = pd.DataFrame(np.column_stack(price_cols)).ffill(axis=1).to_numpy()
    price_deltas = np.diff(price_matrix, axis=1)  # shape (n_pos, n_checkpoints), per-contract $

    cal_idx_matrix = entry_cal_idx[idx_all][:, None] + np.array(checkpoints)[None, :]

    rep_pos_idx = np.repeat(idx_all, len(checkpoints))
    flat_cal_idx = cal_idx_matrix.ravel()
    flat_delta = price_deltas.ravel()

    # ---- quarter bookkeeping: every calendar day gets a quarter code, exactly as in script 24 ----
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

    # ---- true sequential quarter loop: size each quarter's new positions off trailing NAV ----
    num_contracts = np.zeros(len(panel))
    entry_cost = np.zeros(len(panel))
    trailing_nav = INITIAL_CAPITAL
    quarter_log = []

    for qi in range(n_quarters):
        q_local = np.where(entry_qcode == qi)[0]          # positions (in idx_all/local space) entering this quarter
        q_global = idx_all[q_local]
        base_unit = trailing_nav * BASE_UNIT_FRACTION
        scale_note = "ok"
        if len(q_global):
            target_premium = np.abs(weight[q_global]) * base_unit
            contracts = np.floor(target_premium / (CONTRACT_MULTIPLIER * entry_mid[q_global]))
            premium = contracts * CONTRACT_MULTIPLIER * entry_mid[q_global]
            gross_requested = float(premium.sum())
            max_premium = MAX_PREMIUM_FRAC * trailing_nav
            if gross_requested > max_premium and gross_requested > 0:
                scale_note = "priority_capped"
                priority = np.argsort(-np.abs(weight[q_global]))
                cum = np.cumsum(premium[priority])
                keep_n = int(np.searchsorted(cum, max_premium))
                mask_keep = np.zeros(len(q_global), dtype=bool)
                mask_keep[priority[:keep_n]] = True
                contracts = np.where(mask_keep, contracts, 0)
            num_contracts[q_global] = contracts
            entry_cost[q_global] = contracts * CONTRACT_MULTIPLIER * entry_mid[q_global] * ASSUMED_ROUNDTRIP_COST_PCT

        s, e = boundaries[qi], boundaries[qi + 1]
        trade_pnl_q = float(np.sum(num_contracts[rep_pos_idx_sorted[s:e]] * CONTRACT_MULTIPLIER *
                                    flat_delta_sorted[s:e])) if e > s else 0.0
        entry_cost_q = float(entry_cost[q_global].sum()) if len(q_global) else 0.0
        quarter_pnl = trade_pnl_q - entry_cost_q
        quarter_log.append(dict(quarter=str(unique_quarters[qi]), trailing_nav_used=trailing_nav,
                                 base_unit=base_unit, n_new_positions=int((num_contracts[q_global] > 0).sum())
                                 if len(q_global) else 0, cap_status=scale_note,
                                 quarter_realized_pnl=quarter_pnl))
        trailing_nav += quarter_pnl

    # ---- final full-history daily P&L / NAV series, now that every position's contracts are fixed ----
    daily_pnl = np.zeros(n_days)
    np.add.at(daily_pnl, entry_cal_idx[idx_all], -entry_cost[idx_all])
    np.add.at(daily_pnl, flat_cal_idx, num_contracts[rep_pos_idx] * CONTRACT_MULTIPLIER * flat_delta)

    nav = INITIAL_CAPITAL + np.cumsum(daily_pnl)
    # daily_pnl / prior-day NAV, NOT / a fixed INITIAL_CAPITAL -- the latter is the exact bug the
    # README's "note on the Sharpe calculation" documents finding in scripts 17-24: it understates
    # Sharpe once NAV has grown, by measuring later dollar swings against a stale, too-small
    # denominator. total_ret/ann_ret/max_dd below are unaffected either way (computed from `nav`
    # directly), matching that same note -- only ann_vol/sharpe depend on this.
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

    out = pd.DataFrame({"date": cal_dates_d, "daily_pnl": daily_pnl, "nav": nav,
                         "daily_return": daily_ret})
    out.to_csv(DATA / f"backtest_options_v1_h{hold_horizon}d.csv", index=False)
    pd.DataFrame(quarter_log).to_csv(DATA / f"backtest_options_v1_h{hold_horizon}d_quarterlog.csv",
                                      index=False)

    summary = dict(
        hold_horizon_days=hold_horizon, n_positions_eligible=n_pos, n_positions_sized=n_sized,
        total_return=float(total_ret), annualized_return=float(ann_ret),
        annualized_vol=float(ann_vol), sharpe=float(sharpe), max_drawdown=float(max_dd),
        avg_premium_outstanding=float(np.abs(num_contracts * CONTRACT_MULTIPLIER *
                                              np.nan_to_num(entry_mid)).sum() / max(n_quarters, 1)),
        final_nav=float(nav[-1]), assumed_roundtrip_cost_pct=ASSUMED_ROUNDTRIP_COST_PCT,
        n_quarters_premium_capped=int(sum(1 for q in quarter_log if q["cap_status"] == "priority_capped")),
    )
    with open(DATA / f"backtest_options_v1_h{hold_horizon}d_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  h={hold_horizon:>2}d: n_eligible={n_pos:,} n_sized={n_sized:,} ann.ret={ann_ret:.2%} "
          f"ann.vol={ann_vol:.2%} Sharpe={sharpe:.2f} maxDD={max_dd:.2%} final_nav=${nav[-1]:,.0f}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_mock_data_arg(parser)
    args = parser.parse_args()

    with StageTimer("40_options_backtest", extra={"mock_data": args.mock_data}):
        if args.mock_data:
            print(f"using synthetic mock panel ({args.mock_n_events:,} events) -- "
                  "no OptionMetrics access required")
            panel = make_mock_option_event_panel(n_events=args.mock_n_events)
            panel["day0_date"] = pd.to_datetime(
                panel["ann_quarter"].map(lambda q: pd.Period(q, freq="Q").start_time))
            cal_dates = pd.bdate_range(panel["day0_date"].min(),
                                        panel["day0_date"].max() + pd.Timedelta(days=120)).to_numpy()
        else:
            panel_path = DATA / "event_options" / "option_event_panel.parquet"
            if not panel_path.exists():
                raise FileNotFoundError(
                    f"{panel_path} not found. Run scripts 33-36 first (needs the OptionMetrics "
                    "drive attached), or pass --mock-data to test this script without it.")
            panel = pd.read_parquet(panel_path)
            panel = panel[panel["decile"].notna()].reset_index(drop=True)
            panel["decile"] = panel["decile"].astype(int)
            panel["ann_quarter"] = panel["ann_quarter"].astype(str)
            cal = pd.read_parquet(DATA / "metadata" / "om_trading_calendar.parquet")
            cal_dates = cal.sort_values("date")["date"].to_numpy()

        print(f"panel: {len(panel):,} events")
        results = []
        for h in HORIZONS:
            results.append(run_backtest(panel, h, cal_dates))
        pd.DataFrame(results).to_csv(DATA / "backtest_options_v1_comparison.csv", index=False)
        print("\nwrote data/backtest_options_v1_comparison.csv")


if __name__ == "__main__":
    main()
