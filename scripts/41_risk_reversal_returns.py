"""
Build risk-reversal (long call + short put, financed near-zero net premium by construction)
returns for every event, both GROSS (mid-to-mid, as used throughout PEAD_Options_Report.pdf) and
NET of an empirically-estimated bid-ask cost.

Why a risk reversal instead of a naked long call/put: prior literature on this exact question
(Govindaraj, Liu & Livnat 2012, "The Post Earnings Announcement Drift and Option Traders") finds
option-implied volatility already prices in the prior quarter's earnings surprise -- straddles on
extreme-SUE events are not more profitable than on mild-SUE events -- so the edge documented in
PEAD_Options_Report.pdf is directional (delta), not a volatility mispricing. A risk reversal
(long call + short put at similar delta magnitude) isolates that directional exposure while
largely netting out the volatility/theta cost a naked long option pays for exactly the risk
premium the literature says isn't mispriced here (see the broader variance-risk-premium
literature: implied vol exceeds realized vol on average, so buying naked options pays an
insurance premium). By put-call parity, a ~50-delta risk reversal has combined delta near 1.0,
so it behaves like a synthetic long/short stock position financed at close to zero net premium --
this is standard institutional/FX-market practice for expressing a directional view cheaply
(vs. paying full premium for a naked option), and for equities specifically it also sidesteps the
short-borrow cost the equity strategies in PEAD_Strategy_Showcase.pdf could never avoid.

Transaction cost model: the empirical median relative bid-ask spread across this dataset's
forward-price lookups (computed directly from data/event_options/forward_prices_*.parquet,
which retain best_bid/best_offer) is ~11.8% of mid -- much wider than the 2bps used for the
equity strategies' overlay, reflecting genuinely worse single-name-option liquidity. NET returns
below assume crossing half that spread on every leg, at both entry and exit (4 crossings for a
risk reversal, 2 for a naked option) -- see EMPIRICAL_SPREAD below and its computation note.

Output: data/option_rr_decile_horizon_stats.csv, data/option_rr_spread_horizon_stats.csv
        data/event_options/option_event_panel_rr.parquet (panel + risk-reversal return columns)
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path("data")
OUT_DIR = DATA / "event_options"
HORIZONS = [1, 5, 10, 20, 40, 60]

# median relative bid-ask spread ((offer-bid)/mid) computed across 1.45M forward-price lookups
# in data/event_options/forward_prices_*.parquet (see docstring); median used rather than mean
# (19.9%) since the mean is pulled up by a fat illiquid tail (90th pctile = 43%) that would
# overstate typical trading cost for the near-ATM, ge95-DTE contracts this strategy actually uses.
EMPIRICAL_SPREAD = 0.1176

panel = pd.read_parquet(OUT_DIR / "option_event_panel.parquet")
spot = pd.read_parquet(OUT_DIR / "underlying_spot.parquet")
panel["secid"] = panel["secid"].astype("int64")
spot["secid"] = spot["secid"].astype("int64")
panel = panel.merge(spot, on=["secid", "day0_date"], how="left")
print(f"panel: {len(panel):,} rows, spot matched: {panel['spot_close'].notna().mean():.1%}")

# A risk reversal (combined delta ~1.0) is approximately a synthetic long/short position in the
# UNDERLYING stock -- so, exactly like the equity strategies in PEAD_Strategy_Showcase.pdf, its
# portfolio-level return should be built from MARKET-ADJUSTED legs, not raw ones, or the D10 vs.
# D1 "spread" partly just reflects each leg's incidental market-beta exposure rather than a
# market-neutral PEAD signal. Recover each event's own market-return component as
# ret_fwd_Xd - ret_fwd_Xd_mktadj (both already computed, point-in-time-safe, in the equity panel)
# and subtract it from the risk-reversal (and naked-option) return below.
eq = pd.read_parquet("data/events/equity_events_1996_2013.parquet",
                      columns=["permno", "day0_date"] +
                              [f"ret_fwd_{h}d" for h in HORIZONS] +
                              [f"ret_fwd_{h}d_mktadj" for h in HORIZONS])
for h in HORIZONS:
    eq[f"mkt_ret_{h}d"] = eq[f"ret_fwd_{h}d"] - eq[f"ret_fwd_{h}d_mktadj"]
eq = eq[["permno", "day0_date"] + [f"mkt_ret_{h}d" for h in HORIZONS]]
panel = panel.merge(eq, on=["permno", "day0_date"], how="left")
print(f"market-return component matched: {panel['mkt_ret_60d'].notna().mean():.1%}")

both = panel["call_entry_mid"].notna() & panel["put_entry_mid"].notna() & panel["spot_close"].notna()

for h in HORIZONS:
    cfwd, pfwd = f"call_fwd_mid_{h}d", f"put_fwd_mid_{h}d"
    have_h = both & panel[cfwd].notna() & panel[pfwd].notna()

    call_entry, call_fwd = panel["call_entry_mid"], panel[cfwd]
    put_entry, put_fwd = panel["put_entry_mid"], panel[pfwd]
    spot_close = panel["spot_close"]

    gross_pnl = (call_fwd - call_entry) - (put_fwd - put_entry)
    panel[f"rr_ret_fwd_{h}d"] = np.where(have_h, gross_pnl / spot_close, np.nan)

    s = EMPIRICAL_SPREAD
    cost = (s / 2) * ((call_fwd + call_entry) + (put_fwd + put_entry))
    net_pnl = gross_pnl - cost
    panel[f"rr_ret_net_fwd_{h}d"] = np.where(have_h, net_pnl / spot_close, np.nan)

    # naked-option net-of-cost, for the same apples-to-apples cost comparison in script 42
    call_cost = (s / 2) * (call_fwd + call_entry)
    put_cost = (s / 2) * (put_fwd + put_entry)
    panel[f"call_ret_net_fwd_{h}d"] = np.where(
        panel[cfwd].notna(), ((call_fwd - call_entry) - call_cost) / call_entry, np.nan)
    panel[f"put_ret_net_fwd_{h}d"] = np.where(
        panel[pfwd].notna(), ((put_fwd - put_entry) - put_cost) / put_entry, np.nan)

    # market-adjusted risk-reversal return: subtract the event's own market-return component
    # (assumes combined delta ~1.0, the same implicit beta=1 convention the equity report uses)
    mkt = panel[f"mkt_ret_{h}d"]
    panel[f"rr_ret_mktadj_fwd_{h}d"] = panel[f"rr_ret_fwd_{h}d"] - mkt
    panel[f"rr_ret_net_mktadj_fwd_{h}d"] = panel[f"rr_ret_net_fwd_{h}d"] - mkt

panel.to_parquet(OUT_DIR / "option_event_panel_rr.parquet", index=False)
print(f"wrote {OUT_DIR / 'option_event_panel_rr.parquet'}")

# ---------- decile x horizon summary (Fama-MacBeth, same convention as script 37) ----------
panel_d = panel[panel["decile"].notna()].copy()
panel_d["decile"] = panel_d["decile"].astype(int)


def fama_macbeth(df, ret_col, q_col="ann_quarter"):
    sub = df[[q_col, ret_col]].dropna(subset=[ret_col])
    if sub.empty:
        return np.nan, np.nan, np.nan, 0, 0
    qmeans = sub.groupby(q_col)[ret_col].mean()
    n_q = qmeans.shape[0]
    mean = qmeans.mean()
    se = qmeans.std(ddof=1) / np.sqrt(n_q) if n_q > 1 else np.nan
    t = mean / se if se and se > 0 else np.nan
    return mean, se, t, len(sub), n_q


rows = []
RR_KINDS = ["rr_ret_fwd", "rr_ret_net_fwd", "rr_ret_mktadj_fwd", "rr_ret_net_mktadj_fwd"]
for kind in RR_KINDS:
    for h in HORIZONS:
        col = f"{kind}_{h}d"
        for d in range(1, 11):
            sub = panel_d[panel_d["decile"] == d]
            mean, se, t, n, nq = fama_macbeth(sub, col)
            rows.append(dict(kind=kind, horizon=h, decile=d, mean=mean, se=se, t_stat=t,
                              n_events=n, n_quarters=nq))
decile_stats = pd.DataFrame(rows)
decile_stats.to_csv(DATA / "option_rr_decile_horizon_stats.csv", index=False)

print("\n=== RISK REVERSAL mean return (% of underlying notional), gross ===")
print(decile_stats[decile_stats.kind == "rr_ret_fwd"].pivot(
    index="decile", columns="horizon", values="mean").round(4))
print("\n=== RISK REVERSAL mean return, net of estimated bid-ask cost ===")
print(decile_stats[decile_stats.kind == "rr_ret_net_fwd"].pivot(
    index="decile", columns="horizon", values="mean").round(4))
print("\n=== RISK REVERSAL mean return, market-adjusted (gross) ===")
print(decile_stats[decile_stats.kind == "rr_ret_mktadj_fwd"].pivot(
    index="decile", columns="horizon", values="mean").round(4))
print("\n=== RISK REVERSAL mean return, market-adjusted AND net of cost ===")
print(decile_stats[decile_stats.kind == "rr_ret_net_mktadj_fwd"].pivot(
    index="decile", columns="horizon", values="mean").round(4))

spread_rows = []
for kind in RR_KINDS:
    for h in HORIZONS:
        col = f"{kind}_{h}d"
        d1 = panel_d[panel_d["decile"] == 1][["ann_quarter", col]].dropna()
        d10 = panel_d[panel_d["decile"] == 10][["ann_quarter", col]].dropna()
        q1 = d1.groupby("ann_quarter")[col].mean()
        q10 = d10.groupby("ann_quarter")[col].mean()
        common = q1.index.intersection(q10.index)
        spread_q = q10.loc[common] - q1.loc[common]
        n_q = len(spread_q)
        mean = spread_q.mean()
        se = spread_q.std(ddof=1) / np.sqrt(n_q) if n_q > 1 else np.nan
        t = mean / se if se and se > 0 else np.nan
        spread_rows.append(dict(kind=kind, horizon=h, mean_spread=mean, se=se, t_stat=t,
                                 n_quarters=n_q, n_d1=len(d1), n_d10=len(d10)))
spread_stats = pd.DataFrame(spread_rows)
spread_stats.to_csv(DATA / "option_rr_spread_horizon_stats.csv", index=False)
print("\n=== D10-D1 risk-reversal spread, gross vs. net ===")
print(spread_stats.round(4))
