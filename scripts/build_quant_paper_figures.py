"""Figures and supplementary statistics for reports/paper/PEAD_Quant_Research_Paper.tex.

Reads only committed result files under data/ (plus data/normalized_equity/market_benchmark.parquet
for market beta/correlation). Writes vector PDF figures to reports/paper/figures/ and every
statistic the paper quotes that is NOT already in a committed summary JSON/CSV to
reports/paper/paper_stats.json, so each number in the paper traces to a file.

The two headline books are:
  - equity:  Strategy 6 Aggressive  (data/backtest_v2_strategy6_aggressive.csv)
  - options: Kelly-optimal selector, 60d hold, 30% outstanding-premium cap
             (data/backtest_options_optimal_cap30_h60d.csv)
"""
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR, RAW_EQUITY_DIR, REPORTS_DIR

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = REPORTS_DIR / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

NAVY, GREEN, RED, GREY, GOLD = "#1f3a5f", "#2e6b4f", "#9b2335", "#7f7f7f", "#b8860b"
plt.rcParams.update({
    "font.family": "serif", "mathtext.fontset": "cm", "font.size": 9.5,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": "#e6e6e6", "grid.linewidth": 0.6, "axes.edgecolor": "#444444",
    "legend.frameon": False, "figure.dpi": 150, "savefig.bbox": "tight",
})
HORIZONS = [1, 5, 10, 20, 40, 60]


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf")
    plt.close(fig)


# ---------------------------------------------------------------------------------------------
# Evidence figures
# ---------------------------------------------------------------------------------------------
dec = pd.read_csv(DATA_DIR / "decile_horizon_stats.csv")
ffd = pd.read_csv(DATA_DIR / "ff_decile_horizon_stats.csv")
spr = pd.read_csv(DATA_DIR / "spread_horizon_stats.csv")
ffs = pd.read_csv(DATA_DIR / "ff_spread_horizon_stats.csv")
osp = pd.read_csv(DATA_DIR / "option_spread_horizon_stats.csv")

fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9))
cmap = plt.get_cmap("RdYlGn")
for d in range(1, 11):
    s = dec[dec.decile == d].sort_values("horizon")
    axes[0].plot(s.horizon, 100 * s["mean"], marker="o", ms=2.5, lw=1.1, color=cmap((d - 1) / 9),
                 label=f"D{d}" if d in (1, 5, 10) else None)
axes[0].axhline(0, color="#444444", lw=0.6)
axes[0].set(xlabel="Trading days after announcement", ylabel="Mean CAR, market-adjusted (%)",
            title="(a) Decile drift, market-adjusted")
axes[0].legend(fontsize=8, loc="upper left")
for d, c in [(1, RED), (10, GREEN)]:
    a = dec[dec.decile == d].sort_values("horizon")
    b = ffd[ffd.decile == d].sort_values("horizon")
    axes[1].plot(a.horizon, 100 * a["mean"], color=c, lw=1.2, marker="o", ms=2.5, label=f"D{d} market-adj.")
    axes[1].plot(b.horizon, 100 * b["mean"], color=c, lw=1.2, ls="--", marker="s", ms=2.5,
                 label=f"D{d} size/BM-adj.")
axes[1].axhline(0, color="#444444", lw=0.6)
axes[1].set(xlabel="Trading days after announcement", title="(b) Extreme deciles, two benchmarks")
axes[1].legend(fontsize=7.5, loc="upper left")
save(fig, "fig1_decile_drift")

fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
for df, lab, c, off in [(spr, "Market-adjusted", NAVY, -0.9), (ffs, "Size/BM characteristic-adjusted", GOLD, 0.9)]:
    x = np.arange(len(HORIZONS)) + off * 0.12
    axes[0].errorbar(x, 100 * df.mean_spread, yerr=100 * 1.96 * df.se, fmt="o", ms=3.5, capsize=2.5,
                     color=c, label=lab)
axes[0].set_xticks(range(len(HORIZONS)), [f"+{h}" for h in HORIZONS])
axes[0].set(xlabel="Horizon (trading days)", ylabel="D10 $-$ D1 spread (%)", title="(a) Equity: long D10 / short D1")
axes[0].legend(fontsize=7.5, loc="upper left")
for cp, c, off in [("call", GREEN, -1), ("put", RED, 0), ("straddle", GREY, 1)]:
    s = osp[osp.cp_type == cp].sort_values("horizon")
    x = np.arange(len(HORIZONS)) + off * 0.16
    axes[1].errorbar(x, 100 * s.mean_spread, yerr=100 * 1.96 * s.se, fmt="o", ms=3.5, capsize=2.5,
                     color=c, label=f"Near-ATM {cp}")
axes[1].axhline(0, color="#444444", lw=0.6)
axes[1].set_xticks(range(len(HORIZONS)), [f"+{h}" for h in HORIZONS])
axes[1].set(xlabel="Horizon (trading days)", title="(b) Options: D10 $-$ D1 premium return")
axes[1].legend(fontsize=7.5, loc="lower left")
save(fig, "fig2_spreads")

size = pd.read_csv(DATA_DIR / "subsample_size.csv").sort_values("group")
era = pd.read_csv(DATA_DIR / "subsample_era.csv")
era["start"] = era.group.str[:4].astype(int)
era = era.sort_values("start")
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
axes[0].bar(size.group.astype(str), 100 * size.mean_spread, yerr=100 * 1.96 * size.se, color=NAVY,
            capsize=3, width=0.6)
axes[0].set(xlabel="Size quintile (1 = smallest)", ylabel="60-day D10 $-$ D1 (%)",
            title="(a) Size gradient ($F=6.50$, $p<10^{-4}$)")
axes[0].grid(axis="x", visible=False)
axes[1].bar(era.group, 100 * era.mean_spread, yerr=100 * 1.96 * era.se, color=GREY, capsize=3, width=0.6)
axes[1].set(xlabel="Era", title="(b) Era ($F=1.81$, $p=0.138$)")
axes[1].tick_params(axis="x", labelsize=7.5)
axes[1].grid(axis="x", visible=False)
save(fig, "fig3_heterogeneity")

# ---------------------------------------------------------------------------------------------
# Kelly mechanics (stylized illustration -- NOT estimated from data; labelled as such in the paper)
# ---------------------------------------------------------------------------------------------
rng = np.random.default_rng(7)
n_states, n_draw = 40, 200_000
# stylized 60-day return law: a diffuse PEAD-drift body plus a thin right-tail jump component
jump = rng.random(n_draw) < 0.04
draws = np.sort(np.where(jump, rng.normal(0.55, 0.08, n_draw), rng.normal(0.02, 0.08, n_draw)))
edges = np.quantile(draws, np.linspace(0, 1, n_states + 1))
bucket = np.clip(np.digitize(draws, edges[1:-1]), 0, n_states - 1)
r_state = np.array([draws[bucket == s].mean() for s in range(n_states)])
p_state = np.full(n_states, 1 / n_states)
S0, sigma_iv, T_exp = 100.0, 0.30, 120 / 365
F_GRID = np.linspace(0.05, 0.95, 19)          # the selector's actual grid
f_fine = np.linspace(0.001, 0.95, 600)


def bs_call(S, K, T, sig):
    d1 = (np.log(S / K) + 0.5 * sig ** 2 * T) / (sig * np.sqrt(T))
    return S * stats.norm.cdf(d1) - K * stats.norm.cdf(d1 - sig * np.sqrt(T))


def growth(K, fs):
    R = np.maximum(0, S0 * (1 + r_state) - K) / bs_call(S0, K, T_exp, sigma_iv) - 1
    return np.array([np.sum(p_state * np.log1p(f * R)) for f in fs]), np.sum(p_state * R)


fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
for K, c in [(90, NAVY), (125, GOLD), (155, RED)]:
    G, ev = growth(K, f_fine)
    axes[0].plot(f_fine, 1e3 * G, color=c, lw=1.2, label=rf"$K={K}$, $E[R]={100*ev:+.0f}\%$")
axes[0].axhline(0, color="#444444", lw=0.6)
axes[0].axvspan(0, 0.05, color="#dddddd", alpha=0.6, lw=0)
axes[0].text(0.055, -17, r"$f<f_{\min}$", fontsize=7.5)
axes[0].set_ylim(-20, 16)
axes[0].set_xlim(0, 0.6)
axes[0].set(xlabel="Bet fraction $f$", ylabel=r"$G(f)=E[\log(1+fR)]\times 10^3$",
            title="(a) Growth curves by strike (stylized)")
axes[0].legend(fontsize=7, loc="upper right")
Ks = np.linspace(80, 170, 91)
ev_k, g_cont, g_grid = [], [], []
for K in Ks:
    G, ev = growth(K, f_fine)
    Gg, _ = growth(K, F_GRID)
    ev_k.append(ev); g_cont.append(G.max()); g_grid.append(Gg.max())
ax2 = axes[1]
ax2.plot(Ks / S0, 1e3 * np.array(g_cont), color=GREY, lw=1.1, ls="--", label=r"$\sup_{f>0} G(f)$")
ax2.plot(Ks / S0, 1e3 * np.array(g_grid), color=NAVY, lw=1.3, label=r"$\max_{f\in\mathcal{F}} G(f)$ (selector)")
ax2.axhline(0, color="#444444", lw=0.6)
ax2.set_ylim(-15, 16)
ax2.set(xlabel="Moneyness $K/S_0$", ylabel=r"$g^\ast\times 10^3$", title="(b) Growth-optimal score vs. strike")
ax3 = ax2.twinx()
ax3.plot(Ks / S0, 100 * np.array(ev_k), color=RED, lw=1.0, alpha=0.8, label="Naive $E[R]$ (right)")
ax3.set_ylabel("$E[R]$ (%)")
ax3.spines["right"].set_visible(True)
ax3.grid(False)
h1, l1 = ax2.get_legend_handles_labels()
h2, l2 = ax3.get_legend_handles_labels()
ax2.legend(h1 + h2, l1 + l2, fontsize=6.8, loc="upper center", bbox_to_anchor=(0.35, -0.24), ncol=3)
save(fig, "fig4_kelly_illustration")

# Put share of Kelly-optimal picks by decile: transcribed from the committed chart
# reports/figures/26_optimal_selector_put_share_by_decile.png (the underlying
# data/event_options/optimal_contracts.parquet is not tracked in this repo).
put_share = [32, 29, 19, 21, 12, 13, 13, 8, 6, 5]
fig, ax = plt.subplots(figsize=(3.6, 2.4))
ax.bar(range(1, 11), put_share, color=GREEN, width=0.65)
for d, v in zip(range(1, 11), put_share):
    ax.text(d, v + 0.6, f"{v}", ha="center", fontsize=7)
ax.set(xlabel="SUE decile", ylabel="Put share of picks (%)", xticks=range(1, 11))
ax.grid(axis="x", visible=False)
save(fig, "fig5_put_share")

# ---------------------------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------------------------
eq = pd.read_csv(DATA_DIR / "backtest_v2_strategy6_aggressive.csv", parse_dates=["date"]).set_index("date")
op = pd.read_csv(DATA_DIR / "backtest_options_optimal_cap30_h60d.csv", parse_dates=["date"]).set_index("date")
mkt = pd.read_parquet(RAW_EQUITY_DIR / "market_benchmark.parquet", columns=["date", "vwretd"])
mkt["date"] = pd.to_datetime(mkt["date"])
mkt = mkt.set_index("date")["vwretd"]


def risk_stats(nav):
    r = nav.pct_change().dropna()
    first = r.ne(0).idxmax()
    r = r[r.index >= first]
    T = len(r)
    s = r.mean() / r.std()
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    psr = stats.norm.cdf(s * np.sqrt(T - 1) / np.sqrt(1 - sk * s + (ku - 1) / 4 * s ** 2))
    m = mkt.reindex(r.index).fillna(0.0)
    X = np.column_stack([np.ones(T), m])
    b, *_ = np.linalg.lstsq(X, r.to_numpy(), rcond=None)
    e = r.to_numpy() - X @ b
    cov = e.var(ddof=2) * np.linalg.inv(X.T @ X)
    var99 = np.percentile(r, 1)
    dd = nav.loc[first:] / nav.loc[first:].cummax() - 1
    cagr = (nav.iloc[-1] / nav.loc[first:].iloc[0]) ** (252 / T) - 1
    return dict(active_start=str(first.date()), n_days=T, sharpe_active=s * np.sqrt(252),
                skew=sk, kurtosis=ku, psr_vs_zero=psr,
                sortino=r.mean() * 252 / (r[r < 0].std() * np.sqrt(252)),
                beta=b[1], corr_mkt=np.corrcoef(r, m)[0, 1], alpha_ann=b[0] * 252,
                t_alpha=b[0] / np.sqrt(cov[0, 0]), var99_daily=var99,
                cvar99_daily=r[r <= var99].mean(), cagr_active=cagr, max_dd=dd.min(),
                max_dd_date=str(dd.idxmin().date()), calmar=cagr / abs(dd.min())), r


eq_stats, r_eq = risk_stats(eq["nav"])
op_stats, r_op = risk_stats(op["nav"])
gross_lev = (eq.gross_exposure / eq.nav)[eq.gross_exposure > 0]
eq_stats.update(avg_gross_over_nav=gross_lev.mean(), max_gross_over_nav=gross_lev.max(),
                avg_open_positions=eq.n_open_positions.mean())


def deflated_sharpe(sr_ann, T, sk, ku, trial_srs):
    g = 0.5772156649
    v = np.var(np.asarray(trial_srs) / np.sqrt(252), ddof=1)
    N = len(trial_srs)
    sr0 = np.sqrt(v) * ((1 - g) * stats.norm.ppf(1 - 1 / N) + g * stats.norm.ppf(1 - 1 / (N * np.e)))
    s = sr_ann / np.sqrt(252)
    z = (s - sr0) * np.sqrt(T - 1) / np.sqrt(1 - sk * s + (ku - 1) / 4 * s ** 2)
    return dict(n_trials=N, sr0_ann=sr0 * np.sqrt(252), dsr=stats.norm.cdf(z))


eq_trials = sum((pd.read_csv(f)["sharpe"].dropna().tolist()
                 for f in sorted(glob.glob(str(DATA_DIR / "sweep_aggressive_*.csv")))), [])
op_trials = sum((pd.read_csv(DATA_DIR / f)["sharpe"].tolist() for f in [
    "backtest_options_optimal_comparison.csv", "backtest_options_optimal_cap30_comparison.csv",
    "backtest_options_optimal_cap40_comparison.csv", "backtest_options_v1_comparison.csv"]), [])
eq_stats["deflated"] = deflated_sharpe(eq_stats["sharpe_active"], eq_stats["n_days"], eq_stats["skew"],
                                       eq_stats["kurtosis"], eq_trials)
op_stats["deflated"] = deflated_sharpe(op_stats["sharpe_active"], op_stats["n_days"], op_stats["skew"],
                                       op_stats["kurtosis"], op_trials)

# Blend over the options book's active window; options days absent from its calendar (2011) are
# treated as flat (0 return), not dropped, so the equity sleeve's 2011 is still counted.
idx = eq.loc[op_stats["active_start"]:op.index[-1]].index
J = pd.DataFrame({"e": eq["nav"].pct_change().reindex(idx).fillna(0.0),
                  "o": op["nav"].pct_change().reindex(idx).fillna(0.0)})
blends = []
for w in [1.0, 0.75, 0.5, 0.25, 0.0]:
    c = w * J.e + (1 - w) * J.o
    nav = (1 + c).cumprod()
    blends.append(dict(w_equity=w, sharpe=c.mean() / c.std() * np.sqrt(252),
                       cagr=nav.iloc[-1] ** (252 / len(c)) - 1, vol=c.std() * np.sqrt(252),
                       max_dd=(nav / nav.cummax() - 1).min()))
monthly = pd.concat([eq["nav"].resample("ME").last().pct_change(),
                     op["nav"].resample("ME").last().pct_change()], axis=1, keys=["e", "o"]).dropna()
monthly = monthly[monthly.index >= op_stats["active_start"]]


def calendar_returns(nav):
    out, prev = {}, nav.iloc[0]
    for y, g in nav.groupby(nav.index.year):
        out[int(y)] = g.iloc[-1] / prev - 1
        prev = g.iloc[-1]
    return out


mkt_year = ((1 + mkt).groupby(mkt.index.year).prod() - 1).to_dict()
paper_stats = dict(
    equity=eq_stats, options=op_stats,
    corr_daily_blend_window=J.corr().iloc[0, 1], corr_monthly=monthly.corr().iloc[0, 1],
    n_months=len(monthly), blends=blends,
    calendar_returns=dict(equity=calendar_returns(eq["nav"]), options=calendar_returns(op["nav"]),
                          market={int(k): v for k, v in mkt_year.items()}),
)
with open(REPORTS_DIR / "paper" / "paper_stats.json", "w") as f:
    json.dump(paper_stats, f, indent=2, default=float)

# NAV + drawdown
bl = 0.5 * J.e + 0.5 * J.o
fig, axes = plt.subplots(2, 1, figsize=(7.2, 4.4), sharex=True, gridspec_kw=dict(height_ratios=[2.2, 1]))
m_nav = (1 + mkt.loc["1996":"2014-06"]).cumprod() * 1e7
series = [(eq["nav"], NAVY, "Equity book (market-neutral)"),
          (op["nav"], GREEN, "Kelly options book (30% premium cap)"),
          ((1 + bl).cumprod() * eq["nav"].loc[idx[0]], GOLD, "50/50 blend (from 1998)"),
          (m_nav, GREY, "CRSP VW market")]
for s, c, lab in series:
    axes[0].plot(s.index, s / 1e6, color=c, lw=1.1, label=lab)
    dd = s / s.cummax() - 1
    axes[1].plot(dd.index, 100 * dd, color=c, lw=0.9)
axes[0].set_yscale("log")
axes[0].set(ylabel="NAV (\\$M, log)")
axes[0].legend(fontsize=7.5, loc="upper left")
axes[1].set(ylabel="Drawdown (%)")
save(fig, "fig6_nav_drawdown")

# Calendar-year bars
years = list(range(1996, 2014))
ce, co = paper_stats["calendar_returns"]["equity"], paper_stats["calendar_returns"]["options"]
fig, ax = plt.subplots(figsize=(7.2, 2.5))
x = np.arange(len(years))
ax.bar(x - 0.27, [100 * ce.get(y, np.nan) for y in years], 0.27, color=NAVY, label="Equity book")
ax.bar(x, [100 * co.get(y, np.nan) for y in years], 0.27, color=GREEN, label="Options book")
ax.bar(x + 0.27, [100 * mkt_year.get(y, np.nan) for y in years], 0.27, color=GREY, label="Market")
ax.axhline(0, color="#444444", lw=0.6)
ax.set_xticks(x, [str(y) for y in years], rotation=45, fontsize=7.5)
ax.set(ylabel="Calendar-year return (%)")
ax.grid(axis="x", visible=False)
ax.legend(fontsize=7.5, ncol=3, loc="upper right")
save(fig, "fig7_calendar_returns")

# Equity capacity frontier (base-unit sweep) + options premium-budget frontier
sw = pd.read_csv(DATA_DIR / "sweep_aggressive_strategy6.csv")
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
a = axes[0]
a.plot(1e3 * sw.base_unit_fraction, 100 * sw.annualized_return, color=NAVY, marker="o", ms=3, label="Ann. return")
a.plot(1e3 * sw.base_unit_fraction, 100 * sw.annualized_vol, color=RED, marker="s", ms=3, label="Ann. vol")
a.plot(1e3 * sw.base_unit_fraction, 100 * sw.pct_liquidity_capped, color=GREY, ls="--", lw=1,
       label="% positions ADV-capped")
a.axvline(12, color=GOLD, lw=0.9, ls=":")
a.set(xlabel=r"Base-unit fraction $b$ ($\times 10^{-3}$ of NAV)", ylabel="%", title="(a) Equity: capacity frontier in $b$")
a.legend(fontsize=7, loc="upper left")
caps = {0.2: "backtest_options_optimal_comparison.csv", 0.3: "backtest_options_optimal_cap30_comparison.csv",
        0.4: "backtest_options_optimal_cap40_comparison.csv"}
for (cap, fn), c in zip(caps.items(), [GREY, GREEN, NAVY]):
    d = pd.read_csv(DATA_DIR / fn)
    axes[1].plot(d.hold_horizon_days, 100 * d.annualized_return, marker="o", ms=3, color=c,
                 label=f"$c={cap:.0%}$".replace("%", "\\%"))
nv = pd.read_csv(DATA_DIR / "backtest_options_v1_comparison.csv")
axes[1].plot(nv.hold_horizon_days, 100 * nv.annualized_return, marker="x", ms=3.5, color=RED, ls="--",
             label="Near-ATM heuristic")
axes[1].axhline(0, color="#444444", lw=0.6)
axes[1].set(xlabel="Holding horizon (trading days)", title="(b) Options: return by horizon and budget")
axes[1].legend(fontsize=7, loc="upper left")
save(fig, "fig8_frontiers")

print(json.dumps({k: v for k, v in paper_stats.items() if k != "calendar_returns"}, indent=2, default=float))
print(f"wrote figures to {OUT}")
