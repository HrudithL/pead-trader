"""
Quarterly-rebalanced portfolio backtest comparing two ways to trade the SUE-decile signal with
options, at the 60-trading-day horizon (~84 calendar days, just under one announcement quarter --
each cohort's position is closed just before the next quarter's cohort opens, so this is a
non-overlapping, discrete-cohort book, not a continuously-overlapping daily engine like the
equity backtest in scripts/12 and 17. That is a deliberate first-version scope simplification.)

Each announcement quarter, form two legs from that quarter's events:
  - Long leg:  D10 events (most positive surprise)
  - Short leg: D1 events (most negative surprise), position structured to profit from decline

Portfolio return each quarter = 0.5 * mean(long leg's own return) + 0.5 * mean(short leg's own
return) -- equal capital to each leg, mirroring the equal-count dollar-neutral-style construction
of the equity "Extreme decile L/S" strategy (scripts/11-12).

IMPORTANT sizing convention -- fixed notional, not compounded NAV: a first pass at this backtest
that reinvested 100% of a growing NAV every quarter (like the equity engine's trailing-NAV
compounding) produced a mathematically-correct but practically meaningless result for the naked
long-option strategies (O0): quarterly returns on a near-ATM 60-day call/put average +15-30%
(genuinely, per PEAD_Options_Report.pdf), and compounding *that* for 65 quarters explodes to a
six-figure-percent total return -- exactly the kind of unchecked-concentration blowup the equity
book's own leverage caps (scripts/21-24) were built to prevent, just showing up here for a
different reason (option convexity rather than cross-sectional concentration). Rather than pick
an arbitrary leverage cap to force a "reasonable-looking" compounded number, every strategy below
is instead reported on a FIXED-NOTIONAL basis: each quarter deploys the same capital rather than
reinvesting the prior quarter's gains, so results are the arithmetic mean/vol/Sharpe of the
quarterly return series (annualized: mean*4, vol*2, Sharpe=ann_ret/ann_vol) and a linear
(additive, not compounding) cumulative P&L curve for the drawdown calculation. This keeps O0's
huge, real per-event returns from being misrepresented as a compoundable growth rate, and puts
every strategy (including the equity comparison) on the same footing for the Sharpe comparison.

Strategies compared:
  O0_raw   naked long call (D10) / long put (D1), gross mid-to-mid -- the headline of
           PEAD_Options_Report.pdf, shown here as a portfolio-level Sharpe for comparability.
  O0_net   same, net of the empirical ~11.8% bid-ask cost from script 41.
  O1_raw   risk reversal (long call + short put, near-zero net premium), gross.
  O1_mktadj  risk reversal, market-adjusted (subtracts each event's own market-return component,
           since a risk reversal's combined delta ~1.0 makes it a synthetic stock position -- see
           script 41's docstring and the literature cited in the strategy report).
  O1_net_mktadj  risk reversal, market-adjusted AND net of cost -- the most conservative figure.

Output: data/options_strategy_quarterly.csv (all 5 strategies' quarterly returns)
        data/options_strategy_summary.json (headline stats per strategy, fixed-notional basis)
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path("data")
panel = pd.read_parquet(DATA / "event_options" / "option_event_panel_rr.parquet")
panel = panel[panel["decile"].notna()].copy()
panel["decile"] = panel["decile"].astype(int)

H = 60
STRATS = {
    "O0_raw": (f"call_ret_fwd_{H}d", f"put_ret_fwd_{H}d", False),
    "O0_net": (f"call_ret_net_fwd_{H}d", f"put_ret_net_fwd_{H}d", False),
    "O1_raw": (f"rr_ret_fwd_{H}d", f"rr_ret_fwd_{H}d", True),
    "O1_mktadj": (f"rr_ret_mktadj_fwd_{H}d", f"rr_ret_mktadj_fwd_{H}d", True),
    "O1_net_mktadj": (f"rr_ret_net_mktadj_fwd_{H}d", f"rr_ret_net_mktadj_fwd_{H}d", True),
}

d10 = panel[panel["decile"] == 10]
d1 = panel[panel["decile"] == 1]

quarterly = {}
for name, (long_col, short_col, flip_short) in STRATS.items():
    long_q = d10.groupby("ann_quarter")[long_col].mean()
    short_q = d1.groupby("ann_quarter")[short_col].mean()
    if flip_short:
        short_q = -short_q  # bearish risk reversal = -1 * bullish-formula return on D1 events
    common = long_q.index.intersection(short_q.index)
    port_q = 0.5 * long_q.loc[common] + 0.5 * short_q.loc[common]
    port_q = port_q.sort_index()
    quarterly[name] = port_q

q_df = pd.DataFrame(quarterly)
q_df.index = q_df.index.astype(str)
q_df.to_csv(DATA / "options_strategy_quarterly.csv")
print(f"quarters in backtest: {len(q_df)}  ({q_df.index.min()} .. {q_df.index.max()})")

summary = {}
for name in STRATS:
    r = q_df[name].dropna()
    n_q = len(r)
    cum_pnl = r.cumsum()  # fixed-notional additive NAV: NAV(t) = 1 + cum_pnl(t)
    nav = 1 + cum_pnl
    total_ret = float(cum_pnl.iloc[-1])
    ann_ret = float(r.mean() * 4)
    ann_vol = float(r.std(ddof=1) * 2)  # quarterly vol * sqrt(4)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else np.nan
    running_max = nav.cummax()
    drawdown = nav / running_max - 1
    max_dd = float(drawdown.min())
    # for reference only: what naive full compounding would have produced (see docstring)
    naive_compounded_total = float((1 + r).prod() - 1)
    summary[name] = dict(n_quarters=int(n_q), total_return_fixed_notional=total_ret,
                          ann_return=ann_ret, ann_vol=ann_vol, sharpe=float(sharpe),
                          max_drawdown=max_dd,
                          naive_compounded_total_return=naive_compounded_total)
    print(f"{name:16s} n={n_q:3d}  total(fixed)={total_ret:+7.1%}  ann_ret={ann_ret:+7.2%}  "
          f"ann_vol={ann_vol:6.2%}  Sharpe={sharpe:5.2f}  maxDD={max_dd:6.1%}  "
          f"[naive compounded total: {naive_compounded_total:+,.0%}]")

with open(DATA / "options_strategy_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"\nwrote data/options_strategy_quarterly.csv and data/options_strategy_summary.json")
