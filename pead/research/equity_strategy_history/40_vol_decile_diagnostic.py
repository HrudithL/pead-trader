"""
Strategy 9, step 2 (the go/no-go gate): does pre-announcement volatility actually change how much
PEAD drift each SUE decile earns? Built before any vol-based strategy, so the strategies in 41 are
motivated by a measured effect rather than by tuning until a backtest looks good.

For each vol measure from 39 (tvol_60d, ivol_252d; terciles are prior-quarter, within-size-quintile
ranks, so every split below is one a trader could have made in real time):

  1. decile x vol tercile   quarter-clustered (Fama-MacBeth) mean return per cell, for both the
                            position's own holding-window return (hold_ret -- what the backtest
                            actually earns per dollar) and the fixed 60-day market-adjusted return.
  2. D10-D1 spread by tercile, plus the high-minus-low-vol difference in that spread (its own
                            per-quarter series, so its t-stat is a real test of the interaction,
                            not two separately-significant numbers eyeballed against each other).
  3. Strategy 6's two legs   signed return (side x hold_ret) of the long leg (sue_rank_pct > 0.65)
                            and short leg (< 0.35) -- Strategy 6's exact trim -- by vol tercile,
                            overall and by era (1996-2001, 2002-2007, 2008-2013).
  4. Fama-MacBeth regression  per quarter, hold_ret ~ d + v + d*v with d = sue_rank_pct - 0.5 and
                            v = vol_pct - 0.5; the interaction coefficient is the single-number
                            answer to "does drift strength depend on volatility".

Output: data/vol_decile_diagnostic.csv (every cell of tables 1-3, long format)
        data/vol_decile_diagnostic_summary.json (tables 2 and 4, and the leg x tercile headline)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pead.common.paths import DATA_DIR
from pead.common.stats import fama_macbeth

MEASURES = ["tvol_60d", "ivol_252d"]
OUTCOMES = ["hold_ret", "ret_fwd_60d_mktadj"]
TRIM = 0.15  # Strategy 6's trim: long if sue_rank_pct > 0.65, short if < 0.35
ERAS = {"1996-2001": (1996, 2001), "2002-2007": (2002, 2007), "2008-2013": (2008, 2013)}
TERCILE_NAMES = {0: "low", 1: "mid", 2: "high"}


def fm_series(qseries):
    """mean / t-stat of an already per-quarter series."""
    q = qseries.dropna()
    if len(q) < 2:
        return np.nan, np.nan, len(q)
    se = q.std(ddof=1) / np.sqrt(len(q))
    return q.mean(), (q.mean() / se if se > 0 else np.nan), len(q)


def main():
    df = pd.read_parquet(DATA_DIR / "equity_vol_panel.parquet")
    df["year"] = pd.to_datetime(df["day0_date"]).dt.year
    d = df["sue_rank_pct"] - 0.5
    df["side"] = np.where(d > TRIM, 1, np.where(d < -TRIM, -1, 0))
    rows, summary = [], {}

    for m in MEASURES:
        terc_col = f"{m}_tercile"
        sub = df[df[terc_col].notna()].copy()
        sub["terc"] = sub[terc_col].astype(int)
        summary[m] = {}

        # 1. decile x tercile
        for out in OUTCOMES:
            for (dec, t), g in sub.groupby(["decile", "terc"]):
                mean, se, tstat, n, nq = fama_macbeth(g, out)
                rows.append(dict(measure=m, table="decile_x_tercile", outcome=out, decile=int(dec),
                                 tercile=TERCILE_NAMES[t], era="all", mean=mean, t_stat=tstat,
                                 n_events=n, n_quarters=nq))

        # 2. D10-D1 spread by tercile, and high-minus-low spread difference
        spread_q = {}
        for out in OUTCOMES:
            qm = (sub[sub["decile"].isin([1, 10])]
                  .groupby(["ann_quarter", "terc", "decile"])[out].mean().unstack("decile"))
            spread = (qm[10] - qm[1]).unstack("terc")
            spread_q[out] = spread
            res = {}
            for t in range(3):
                mean, tstat, nq = fm_series(spread[t])
                res[TERCILE_NAMES[t]] = dict(mean=mean, t_stat=tstat, n_quarters=nq)
                rows.append(dict(measure=m, table="d10_minus_d1", outcome=out, decile=None,
                                 tercile=TERCILE_NAMES[t], era="all", mean=mean, t_stat=tstat,
                                 n_events=None, n_quarters=nq))
            mean, tstat, nq = fm_series(spread[2] - spread[0])
            res["high_minus_low"] = dict(mean=mean, t_stat=tstat, n_quarters=nq)
            summary[m][f"d10_minus_d1_{out}"] = res

        # 3. Strategy 6's legs, signed, by tercile x era
        legs = sub[sub["side"] != 0].copy()
        legs["signed_hold_ret"] = legs["side"] * legs["hold_ret"]
        leg_summary = {}
        for era, (y0, y1) in {"all": (0, 9999), **ERAS}.items():
            e = legs[(legs["year"] >= y0) & (legs["year"] <= y1)]
            for (side, t), g in e.groupby(["side", "terc"]):
                mean, se, tstat, n, nq = fama_macbeth(g, "signed_hold_ret")
                leg = "long" if side > 0 else "short"
                rows.append(dict(measure=m, table="strategy6_leg", outcome="signed_hold_ret",
                                 decile=None, tercile=f"{leg}_{TERCILE_NAMES[t]}", era=era,
                                 mean=mean, t_stat=tstat, n_events=n, n_quarters=nq))
                leg_summary.setdefault(era, {})[f"{leg}_{TERCILE_NAMES[t]}"] = dict(
                    mean=mean, t_stat=tstat, n_events=n)
        summary[m]["strategy6_legs_signed_hold_ret"] = leg_summary

        # 4. FM regression with interaction
        coefs = []
        for q, g in sub.dropna(subset=["hold_ret", f"{m}_pct"]).groupby("ann_quarter"):
            if len(g) < 50:
                continue
            dd = g["sue_rank_pct"].to_numpy() - 0.5
            vv = g[f"{m}_pct"].to_numpy() - 0.5
            X = np.column_stack([np.ones(len(g)), dd, vv, dd * vv])
            b, *_ = np.linalg.lstsq(X, g["hold_ret"].to_numpy(), rcond=None)
            coefs.append(b)
        coefs = pd.DataFrame(coefs, columns=["const", "sue", "vol", "sue_x_vol"])
        summary[m]["fm_regression_hold_ret"] = {
            c: dict(zip(["mean", "t_stat", "n_quarters"], fm_series(coefs[c]))) for c in coefs.columns}

    out = pd.DataFrame(rows)
    out.to_csv(DATA_DIR / "vol_decile_diagnostic.csv", index=False)
    with open(DATA_DIR / "vol_decile_diagnostic_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=float)

    pd.set_option("display.width", 200)
    for m in MEASURES:
        print(f"\n================ {m} ================")
        tab = out[(out.measure == m) & (out.table == "decile_x_tercile") & (out.outcome == "hold_ret")]
        print("holding-window return, FM mean (t) by decile x vol tercile:")
        piv = tab.pivot(index="decile", columns="tercile", values="mean")[["low", "mid", "high"]]
        pt = tab.pivot(index="decile", columns="tercile", values="t_stat")[["low", "mid", "high"]]
        print((piv.map(lambda x: f"{x:+.2%}") + pt.map(lambda x: f" ({x:+.1f})")).to_string())
        for o in OUTCOMES:
            r = summary[m][f"d10_minus_d1_{o}"]
            print(f"D10-D1 [{o}]: " + "  ".join(f"{k}={v['mean']:+.2%} (t={v['t_stat']:+.2f})" for k, v in r.items()))
        print("Strategy 6 legs, signed holding return (t) by era:")
        L = summary[m]["strategy6_legs_signed_hold_ret"]
        cells = ["long_low", "long_mid", "long_high", "short_low", "short_mid", "short_high"]
        print("  era        " + "".join(f"{c:>18}" for c in cells))
        for era, v in L.items():
            print(f"  {era:<10} " + "".join(
                f"{v[c]['mean']:>+10.2%} ({v[c]['t_stat']:+.1f})" if c in v else f"{'':>18}" for c in cells))
        r = summary[m]["fm_regression_hold_ret"]
        print("FM regression hold_ret ~ sue + vol + sue*vol: " + "  ".join(
            f"{k}={v['mean']:+.4f} (t={v['t_stat']:+.2f})" for k, v in r.items()))


if __name__ == "__main__":
    main()
