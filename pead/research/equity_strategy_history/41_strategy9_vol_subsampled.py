"""
Strategy 9: Strategy 6 with volatility-based subsampling / sizing of the SUE deciles.

What 40_vol_decile_diagnostic.py found, and why these particular variants:
  - The PEAD spread is strongest in LOW-volatility stocks and weakest in HIGH-volatility ones
    (D10-D1 holding-window spread +4.3% / +3.3% / +2.1% by idiosyncratic-vol tercile; the
    Fama-MacBeth sue x vol interaction is t = -2.4 to -2.7). That's the opposite of the classic
    limits-to-arbitrage prediction, and vol has no effect on its own (t ~ -0.1) -- it doesn't change
    average returns, it changes how much the SUE signal is worth.
  - Strategy 6's short leg has a negative signed holding return in every vol tercile (deciles 1-4
    all have positive market-adjusted returns); it earns its keep as a hedge of that common
    component, not standalone. So "cut the losing short cells" is the wrong filter -- a cell's
    contribution has to be measured RELATIVE to the average event in its quarter.
  - The effect is a smooth gradient, not one broken cell, which favors sizing over filtering.

Variants (every one keeps Strategy 6's trim/tilt, size+sector neutralization, 2.5x leverage cap,
liquidity cap, costs and engine -- see lib/backtest_v2.py -- only the per-event scale changes):

  9a  walk_forward_filter   drop a (side, ivol tercile) cell for new entries in quarter Q only if
                            its historical EXCESS contribution -- side x (hold_ret minus that
                            announcement quarter's cross-sectional mean hold_ret) -- has a
                            Fama-MacBeth mean <= 0, using only announcement quarters whose
                            positions had ALL fully exited before Q began, and only once >= 12 such
                            quarters exist. No full-sample information.
  9b  inverse_ivol          scale each event by 1 / ivol_252d (clipped to fixed 0.8%-8% daily,
                            i.e. ~13%-127% annualized, so a data error can't produce a huge
                            weight). Because neutralization normalizes within each (quarter, side,
                            sector, size) group, only relative vol within a group matters -- the
                            book is still size/sector/dollar neutral. No fitted parameters.
  9c  inverse_ivol + filter both of the above.
  ref drop_high_ivol_insample  drop the high-ivol tercile outright. LOOK-AHEAD: this rule was
                            chosen by looking at 40's full-sample result. Reported only as an
                            upper-bound reference, never as a tradeable result.
  sens_*                    robustness of 9b's specification, not separate candidates: inverse
                            TOTAL vol (tvol_60d) instead of idiosyncratic, and a half-strength
                            1/sqrt(ivol) scale. If 9b only worked for one exact specification it
                            would be a fitted result, not a real effect.

Each variant is run twice: at Strategy 6's own sizing (base_unit_fraction 0.003), and "vol-matched"
-- base_unit_fraction solved (a few secant steps; vol isn't linear in sizing once the liquidity cap
binds and NAV compounds) so its annualized vol equals Strategy 6's to within 0.02pp, which answers
"at the SAME risk, how much more return". Every summary also carries Sharpe by era (1996-2001,
2002-2007, 2008-2013) so an improvement that only lives in one period is visible. A same-run
Strategy 6 baseline is included and checked against the committed backtest_v2_strategy6.csv NAV to
the dollar before anything else is trusted.

Output: data/backtest_v2_strategy9_<variant>.csv / _quarterlog.csv / _summary.json
        data/backtest_v2_strategy9_comparison.csv
        data/strategy9_filter_log.csv (which cells 9a/9c dropped, per quarter)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pead.common.paths import DATA_DIR, RAW_EQUITY_DIR, INITIAL_CAPITAL
from lib.backtest_v2 import load_mktadj_panel, run_backtest
from lib.positions import weights_size_sector_neutral

TRIM, TILT_POWER, BUF, MAX_GROSS_LEVERAGE = 0.15, 1.0, 0.003, 2.5  # Strategy 6, unchanged
VOL_COL = "ivol_252d"
IVOL_CLIP = (0.008, 0.08)
MIN_HISTORY_QUARTERS = 12
ERAS = {"1996_2001": ("1996", "2001"), "2002_2007": ("2002", "2007"), "2008_2013": ("2008", "2013")}
VOL_MATCH_TOL = 0.0002


def era_sharpes(daily):
    r = daily.set_index("date")["daily_return"]
    out = {}
    for name, (y0, y1) in ERAS.items():
        x = r.loc[y0:y1]
        out[f"sharpe_{name}"] = float(x.mean() / x.std(ddof=1) * np.sqrt(252)) if x.std() > 0 else np.nan
    return out


def run(pos, w, eq_ret, eq_date, calendar_dt, buf):
    return run_backtest(pos, w, eq_ret, eq_date, calendar_dt, buf, MAX_GROSS_LEVERAGE, INITIAL_CAPITAL)


def solve_vol_matched(pos, w, eq_ret, eq_date, calendar_dt, target_vol, buf0, vol0):
    """Secant search on base_unit_fraction for annualized vol == target_vol."""
    b_prev, v_prev = buf0, vol0
    b = buf0 * target_vol / vol0
    for _ in range(8):
        res = run(pos, w, eq_ret, eq_date, calendar_dt, b)
        v = res[2]["annualized_vol"]
        if abs(v - target_vol) < VOL_MATCH_TOL or v == v_prev:
            return b, res
        b, b_prev, v_prev = b + (target_vol - v) * (b - b_prev) / (v - v_prev), b, v
    return b, res


def walk_forward_filter_scale(pos, side, terc):
    """1.0 for kept events, 0.0 for events in a (side, tercile) cell the walk-forward rule drops
    at their entry quarter. Returns (scale, log DataFrame)."""
    excess = pos["hold_ret"] - pos.groupby("ann_quarter")["hold_ret"].transform("mean")
    signed_excess = side * excess
    ann_q = pos["ann_quarter"]
    # an announcement quarter becomes usable history once every one of its positions has exited
    q_done = pos.groupby("ann_quarter")["exit_date"].max()
    entry_q = pos["day0_date"].dt.to_period("Q")
    cell_q = (pd.DataFrame({"ann_quarter": ann_q, "side": side, "terc": terc, "x": signed_excess})
              [(side != 0) & terc.notna()]
              .groupby(["side", "terc", "ann_quarter"])["x"].mean())

    scale = np.ones(len(pos))
    log = []
    for q in sorted(entry_q.unique()):
        q_start = q.start_time
        usable = set(q_done[q_done < q_start].index)
        for (s, t), series in cell_q.groupby(level=["side", "terc"]):
            hist = series[series.index.get_level_values("ann_quarter").isin(usable)]
            n = len(hist)
            mean = hist.mean() if n else np.nan
            drop = n >= MIN_HISTORY_QUARTERS and mean <= 0
            log.append(dict(entry_quarter=str(q), side=int(s), ivol_tercile=int(t),
                            n_history_quarters=n, hist_mean_signed_excess=mean, dropped=drop))
            if drop:
                scale[((entry_q == q) & (side == s) & (terc == t)).to_numpy()] = 0.0
    return scale, pd.DataFrame(log)


def main():
    print("loading daily panel and vol panel...")
    eq, calendar_dt = load_mktadj_panel(RAW_EQUITY_DIR)
    eq_ret = eq["ret_mktadj"].to_numpy()
    eq_date = eq["date"].to_numpy()
    pos = pd.read_parquet(DATA_DIR / "equity_vol_panel.parquet")
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    pos["exit_date"] = pd.to_datetime(pos["exit_date"])

    d = pos["sue_rank_pct"].to_numpy() - 0.5
    side = pd.Series(np.where(d > TRIM, 1, np.where(d < -TRIM, -1, 0)), index=pos.index)
    terc = pos[f"{VOL_COL}_tercile"].astype("float")

    vol = pos[VOL_COL].fillna(pos["tvol_60d"]).to_numpy()
    inv_vol = np.where(np.isfinite(vol), 1.0 / np.clip(vol, *IVOL_CLIP), 1.0 / np.nanmedian(vol))
    filt, filter_log = walk_forward_filter_scale(pos, side, terc)
    filter_log.to_csv(DATA_DIR / "strategy9_filter_log.csv", index=False)
    drop_high = np.where(terc.to_numpy() == 2, 0.0, 1.0)

    variants = {
        "strategy6_baseline": None,
        "9a_walk_forward_filter": filt,
        "9b_inverse_ivol": inv_vol,
        "9c_inverse_ivol_filter": inv_vol * filt,
        "ref_drop_high_ivol_insample": drop_high,
        "sens_inverse_tvol": 1.0 / np.clip(pos["tvol_60d"].fillna(pos["tvol_60d"].median()).to_numpy(), *IVOL_CLIP),
        "sens_inverse_sqrt_ivol": np.sqrt(inv_vol),
    }

    rows = []
    base_vol = None
    for name, scale in variants.items():
        w = weights_size_sector_neutral(pos, d, TRIM, TILT_POWER, scale=scale)
        daily, qlog, s = run(pos, w, eq_ret, eq_date, calendar_dt, BUF)
        runs = [(name, BUF, daily, qlog, s)]
        if name == "strategy6_baseline":
            committed = pd.read_csv(DATA_DIR / "backtest_v2_strategy6.csv")
            diff = float(np.abs(committed["nav"].to_numpy() - daily["nav"].to_numpy()).max())
            assert diff < 1.0, f"baseline does not reproduce committed Strategy 6 (max NAV diff ${diff:,.2f})"
            print(f"baseline reproduces committed Strategy 6 NAV (max diff ${diff:.2f})")
            base_vol = s["annualized_vol"]
        else:
            buf_m, (daily_m, qlog_m, s_m) = solve_vol_matched(
                pos, w, eq_ret, eq_date, calendar_dt, base_vol, BUF, s["annualized_vol"])
            runs.append((f"{name}_volmatched", buf_m, daily_m, qlog_m, s_m))
        for tag, buf, daily, qlog, s in runs:
            s = dict(variant=tag, **s, **era_sharpes(daily),
                     avg_net_exposure_frac_of_gross=float(
                         (daily["net_exposure"] / daily["gross_exposure"].replace(0, np.nan)).mean()))
            rows.append(s)
            daily.to_csv(DATA_DIR / f"backtest_v2_strategy9_{tag}.csv", index=False)
            qlog.to_csv(DATA_DIR / f"backtest_v2_strategy9_{tag}_quarterlog.csv", index=False)
            with open(DATA_DIR / f"backtest_v2_strategy9_{tag}_summary.json", "w") as f:
                json.dump(s, f, indent=2)
            print(f"  {tag:<40} ret={s['annualized_return']:6.2%} vol={s['annualized_vol']:5.2%} "
                  f"Sharpe={s['sharpe']:.3f} [{s['sharpe_1996_2001']:.2f}/{s['sharpe_2002_2007']:.2f}/"
                  f"{s['sharpe_2008_2013']:.2f}] maxDD={s['max_drawdown']:7.2%} buf={buf:.4f} "
                  f"liqcap={s['pct_liquidity_capped']:.0%}")

    comp = pd.DataFrame(rows)
    comp.to_csv(DATA_DIR / "backtest_v2_strategy9_comparison.csv", index=False)
    dropped = filter_log[filter_log["dropped"]]
    print(f"\nwalk-forward filter dropped {len(dropped)} (quarter, side, tercile) cell-quarters of "
          f"{len(filter_log)}; by cell:")
    if len(dropped):
        print(dropped.groupby(["side", "ivol_tercile"]).size().to_string())
    print("\nwrote data/backtest_v2_strategy9_comparison.csv")


if __name__ == "__main__":
    main()
