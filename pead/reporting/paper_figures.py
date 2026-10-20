"""Figures for the research paper (docs/paper/figures/*.pdf), drawn from the same context pead.reporting.paper builds
its numbers from. Every curve and bar comes from results/ or data/; the one stylized exhibit (the Kelly mechanics
figure) is labelled as an illustration in its caption and in its own title."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats as sps

NAVY, GREEN, RED, GREY, GOLD, TEAL = "#1f3a5f", "#2e6b4f", "#9b2335", "#7f7f7f", "#b8860b", "#2a7f86"
plt.rcParams.update({
    "font.family": "serif", "mathtext.fontset": "cm", "font.size": 9.5,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": "#e6e6e6", "grid.linewidth": 0.6, "axes.edgecolor": "#444444",
    "legend.frameon": False, "figure.dpi": 150, "savefig.bbox": "tight",
})
HORIZONS = [1, 5, 10, 20, 40, 60]
SHORT = {"extreme_picks": "extreme", "graded_bets": "graded", "size_balanced": "size-bal.", "conviction_tilt": "conviction",
         "sector_balanced": "sector-bal.", "balanced_leverage": "bal.-lev.", "calm_weighted": "calm-w.",
         "calm_rank_tilt": "calm-rank", "market_exposed": "mkt-exp."}
PROFILED = list(SHORT)


def _save(fig, out, name):
    fig.savefig(Path(out) / f"{name}.pdf")
    plt.close(fig)


def fig_decile(ctx, out):
    dec, ffd = ctx["dec"], ctx["ffd"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9))
    cmap = plt.get_cmap("RdYlGn")
    for d in range(1, 11):
        s = dec[dec.decile == d].sort_values("horizon")
        axes[0].plot(s.horizon, 100 * s["mean"], marker="o", ms=2.5, lw=1.1, color=cmap((d - 1) / 9), label=f"D{d}" if d in (1, 5, 10) else None)
    axes[0].axhline(0, color="#444444", lw=0.6)
    axes[0].set(xlabel="Trading days after announcement", ylabel="Mean CAR, market-adjusted (%)", title="(a) Decile drift")
    axes[0].legend(fontsize=8, loc="upper left")
    for d, c in [(1, RED), (10, GREEN)]:
        a = dec[dec.decile == d].sort_values("horizon")
        b = ffd[ffd.decile == d].sort_values("horizon")
        axes[1].plot(a.horizon, 100 * a["mean"], color=c, lw=1.2, marker="o", ms=2.5, label=f"D{d} market-adj.")
        axes[1].plot(b.horizon, 100 * b["mean"], color=c, lw=1.2, ls="--", marker="s", ms=2.5, label=f"D{d} size/BM-adj.")
    axes[1].axhline(0, color="#444444", lw=0.6)
    axes[1].set(xlabel="Trading days after announcement", title="(b) Extreme deciles, two benchmarks")
    axes[1].legend(fontsize=7.5, loc="upper left")
    _save(fig, out, "fig_decile_drift")


def fig_spreads(ctx, out):
    spr, ffs, osp = ctx["spr"], ctx["ffs"], ctx["osp"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
    for df, lab, c, off in [(spr, "Market-adjusted", NAVY, -0.9), (ffs, "Size/BM characteristic-adjusted", GOLD, 0.9)]:
        x = np.arange(len(HORIZONS)) + off * 0.12
        axes[0].errorbar(x, 100 * df.loc[HORIZONS, "mean_spread"], yerr=100 * 1.96 * df.loc[HORIZONS, "se"], fmt="o", ms=3.5, capsize=2.5, color=c, label=lab)
    axes[0].set_xticks(range(len(HORIZONS)), [f"+{h}" for h in HORIZONS])
    axes[0].set(xlabel="Horizon (trading days)", ylabel="D10 $-$ D1 spread (%)", title="(a) Equity: long D10 / short D1")
    axes[0].legend(fontsize=7.5, loc="upper left")
    for cp, c, off in [("call", GREEN, -1), ("put", RED, 0), ("straddle", GREY, 1)]:
        s = osp[osp.cp_type == cp].sort_values("horizon")
        x = np.arange(len(HORIZONS)) + off * 0.16
        axes[1].errorbar(x, 100 * s.mean_spread, yerr=100 * 1.96 * s.se, fmt="o", ms=3.5, capsize=2.5, color=c, label=f"Near-ATM {cp}")
    axes[1].axhline(0, color="#444444", lw=0.6)
    axes[1].set_xticks(range(len(HORIZONS)), [f"+{h}" for h in HORIZONS])
    axes[1].set(xlabel="Horizon (trading days)", title="(b) Options: D10 $-$ D1 premium return")
    axes[1].legend(fontsize=7.5, loc="lower left")
    _save(fig, out, "fig_spreads")


def fig_het(ctx, out):
    size, era = ctx["size"].sort_values("group"), ctx["era"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
    axes[0].bar(size.group.astype(str), 100 * size.mean_spread, yerr=100 * 1.96 * size.se, color=NAVY, capsize=3, width=0.6)
    axes[0].set(xlabel="Size quintile (1 = smallest)", ylabel="60-day D10 $-$ D1 (%)", title="(a) By company size")
    axes[0].grid(axis="x", visible=False)
    axes[1].bar(era.group.str.replace("-", "–"), 100 * era.mean_spread, yerr=100 * 1.96 * era.se, color=GREY, capsize=3, width=0.6)
    axes[1].set(xlabel="Era", title="(b) By era")
    axes[1].tick_params(axis="x", labelsize=7.5)
    axes[1].grid(axis="x", visible=False)
    _save(fig, out, "fig_heterogeneity")


def fig_nav(ctx, out):
    d = ctx["daily"]
    mk = ctx["market"].set_index("date")["vwretd"].fillna(0.0).loc["1996":"2013-08-30"]
    series = [(d["calm_weighted"].set_index("date")["nav"].loc["1996":"2013-08-30"], NAVY, "calm_weighted"),
              (d["calm_rank_tilt"].set_index("date")["nav"].loc["1996":"2013-08-30"], TEAL, "calm_rank_tilt (research)"),
              (d["options_cap20"].set_index("date")["nav"].loc["1996":"2013-08-30"], GREEN, "options_cap20"),
              (d["hedged_blend"].set_index("date")["nav"].loc["1996":"2013-08-30"], GOLD, "hedged_blend"),
              ((1 + mk).cumprod() * 1e7, GREY, "CRSP value-weighted market")]
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 4.4), sharex=True, gridspec_kw=dict(height_ratios=[2.2, 1]))
    for s, c, lab in series:
        s = s / s.iloc[0] * 1e7
        axes[0].plot(s.index, s / 1e6, color=c, lw=1.1, label=lab.replace("_", r"\_") if False else lab)
        dd = s / s.cummax() - 1
        axes[1].plot(dd.index, 100 * dd, color=c, lw=0.9)
    axes[0].set_yscale("log")
    axes[0].set_yticks([10, 20, 40, 80])
    axes[0].set_yticklabels(["10", "20", "40", "80"])
    axes[0].minorticks_off()
    axes[0].set(ylabel="NAV from \\$10M (\\$M, log)")
    axes[0].legend(fontsize=7.5, loc="upper left")
    axes[1].set(ylabel="Drawdown (%)")
    _save(fig, out, "fig_nav_drawdown")


def fig_calendar(ctx, out):
    c = ctx["cal"]
    years = list(range(1996, 2014))
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    x = np.arange(len(years))
    ax.bar(x - 0.27, [100 * c["eq"].get(y, np.nan) for y in years], 0.27, color=NAVY, label="calm_weighted")
    oc = [100 * c["op"].get(y, np.nan) if abs(c["op"].get(y, 0)) > 1e-12 else np.nan for y in years]
    ax.bar(x, oc, 0.27, color=GREEN, label="options_cap20")
    ax.bar(x + 0.27, [100 * c["mk"].get(y, np.nan) for y in years], 0.27, color=GREY, label="Market")
    ax.axhline(0, color="#444444", lw=0.6)
    ax.set_xticks(x, [str(y) for y in years], rotation=45, fontsize=7.5)
    ax.set(ylabel="Calendar-year return (%)")
    ax.grid(axis="x", visible=False)
    ax.legend(fontsize=7.5, ncol=3, loc="upper right")
    _save(fig, out, "fig_calendar_returns")


def fig_leverage(ctx, out):
    d, runs = ctx["daily"], ctx["runs"]
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    for n, c in [("balanced_leverage", NAVY), ("calm_weighted", TEAL), ("balanced_leverage_max", RED), ("market_boost_max", GOLD)]:
        x = d[n].set_index("date")
        g = (x["gross_exposure"] / x["nav"]).where(x["gross_exposure"] > 0)
        ax.plot(g.index, g.rolling(21, min_periods=1).mean(), color=c, lw=1.0, label=n)
    for cap, c in sorted({(runs[n]["params"]["max_gross_leverage"], c) for n, c in [("balanced_leverage", NAVY), ("balanced_leverage_max", RED)]}):
        ax.axhline(cap, color=c, ls=":", lw=0.9)
    ax.set(ylabel="Gross exposure / NAV (21-day mean)", xlabel="")
    ax.legend(fontsize=7.5, loc="upper left", ncol=2)
    _save(fig, out, "fig_leverage")


def fig_legs(S, ctx, out):
    names = [n for n in PROFILED if f"{n}.long_bp_all" in S.raw]
    x = np.arange(len(names))
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9), sharey=True)
    for ax, tag, title in ((axes[0], "all", "(a) All-announcer benchmark"), (axes[1], "size", "(b) Size-matched benchmark")):
        lg = [S.get(f"{n}.long_bp_{tag}") for n in names]
        sh = [S.get(f"{n}.short_bp_{tag}") for n in names]
        ax.bar(x - 0.2, lg, 0.4, color=GREEN, label="long leg")
        ax.bar(x + 0.2, sh, 0.4, color=RED, label="short leg")
        ax.set_xticks(x, [SHORT[n] for n in names], rotation=60, ha="right", fontsize=7.5)
        ax.set(title=title)
        ax.axhline(0, color="#444444", lw=0.6)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("Alpha per \\$ traded (bp)")
    axes[0].legend(fontsize=7.5)
    _save(fig, out, "fig_legs")


def fig_betalegs(ctx, out):
    bl = ctx["bl"]
    g = {k: bl[bl.group == k].set_index("strategy") for k in ("high-beta shorts", "low/mid-beta shorts", "high-beta longs")}
    names = [n for n in PROFILED if n in g["high-beta shorts"].index]
    x = np.arange(len(names))
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9))
    cols = {"high-beta shorts": RED, "low/mid-beta shorts": GOLD, "high-beta longs": GREEN}
    for i, (k, c) in enumerate(cols.items()):
        axes[0].bar(x + (i - 1) * 0.27, [g[k].loc[n, "alpha_bp"] for n in names], 0.27, color=c, label=k)
        axes[1].bar(x + (i - 1) * 0.27, [g[k].loc[n, "real_M"] for n in names], 0.27, color=c, label=k)
    for ax, yl, t in ((axes[0], "Alpha per \\$ traded (bp)", "(a) Alpha (size-matched)"), (axes[1], "Real profit and loss (\\$M)", "(b) Real dollars")):
        ax.set_xticks(x, [SHORT[n] for n in names], rotation=60, ha="right", fontsize=7.5)
        ax.axhline(0, color="#444444", lw=0.6)
        ax.set(ylabel=yl, title=t)
        ax.grid(axis="x", visible=False)
    axes[0].legend(fontsize=7)
    _save(fig, out, "fig_beta_legs")


def fig_betavol(S, ctx, out):
    bv = ctx["ext"].get("beta_vol")
    if not bv:
        return
    cells = bv["size"]["cells"]
    betas, vols = ["low", "mid", "high"], ["calm", "mid", "jumpy"]
    M = np.array([[cells[f"{b}|{v}"]["bp"] for v in vols] for b in betas])
    T = np.array([[cells[f"{b}|{v}"]["t"] for v in vols] for b in betas])
    W = np.array([[cells[f"{b}|{v}"]["pct_dollars"] for v in vols] for b in betas])
    fig, ax = plt.subplots(figsize=(4.3, 3.2))
    im = ax.imshow(M, cmap="Greens", vmin=0, vmax=max(M.max(), 1))
    for i in range(3):
        for j in range(3):
            ax.text(j, i, f"{M[i, j]:.0f} bp\n$t$={T[i, j]:.1f}".replace("=-", "=−") + f"\n{100 * W[i, j]:.0f}% of \\$", ha="center", va="center", fontsize=7.5,
                    color="white" if M[i, j] > 0.6 * M.max() else "black")
    ax.set_xticks(range(3), ["calm", "mid", "jumpy"])
    ax.set_yticks(range(3), ["low beta", "mid beta", "high beta"])
    ax.set(xlabel="Idiosyncratic-volatility tercile")
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    _save(fig, out, "fig_beta_vol")


def fig_era(S, ctx, out):
    e = ctx["era_bp"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
    xs = ["1996–01", "2002–07", "2008–13"]
    for n in e.index:
        axes[0].plot(xs, e.loc[n, ["A", "B", "C"]], marker="o", ms=3, lw=1.0, label=SHORT[n])
    axes[0].set(ylabel="Alpha per \\$ traded (bp)", title="(a) Alpha per dollar by era (profiled books)")
    axes[0].legend(fontsize=6.5, ncol=2)
    ab = ctx["ab"]
    names = [n for n in ["extreme_picks", "graded_bets", "size_balanced", "conviction_tilt", "sector_balanced", "balanced_leverage", "calm_weighted", "calm_rank_tilt"] if n in ab.index]
    x = np.arange(len(names))
    for i, (col, lab, c) in enumerate((("a_96_01", "1996–01", NAVY), ("a_02_07", "2002–07", GOLD), ("a_08_13", "2008–13", RED))):
        axes[1].bar(x + (i - 1) * 0.27, 100 * ab.loc[names, col], 0.27, color=c, label=lab)
    axes[1].set_xticks(x, [SHORT[n] for n in names], rotation=60, ha="right", fontsize=7.5)
    axes[1].axhline(0, color="#444444", lw=0.6)
    axes[1].set(ylabel="Annualized alpha vs CRSP market (%)", title="(b) Alpha vs the market by era")
    axes[1].grid(axis="x", visible=False)
    axes[1].legend(fontsize=7)
    _save(fig, out, "fig_era")


def fig_recipes(S, ctx, out):
    rc = ctx["rc"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), sharex=True, sharey=True)
    for ax, n in zip(axes, ("balanced_leverage", "calm_rank_tilt")):
        r = rc[rc.strategy == n]
        best = r.recipe == "long | thin | low-beta | long hold (>60d)"
        hbs = r.recipe.str.startswith("short") & r.recipe.str.contains("high-beta")
        ax.scatter(100 * r.pct_dollars[~best & ~hbs], 100 * r.pct_alpha[~best & ~hbs], s=14, color=GREY, alpha=0.7, label="other recipes")
        ax.scatter(100 * r.pct_dollars[hbs], 100 * r.pct_alpha[hbs], s=18, color=RED, label="high-beta shorts")
        ax.scatter(100 * r.pct_dollars[best], 100 * r.pct_alpha[best], s=40, color=GREEN, marker="D", label="long, thin, low-beta, long hold")
        lim = max(r.pct_dollars.max(), r.pct_alpha.max()) * 100 * 1.05
        ax.plot([0, lim], [0, lim], color="#444444", lw=0.6, ls=":")
        ax.axhline(0, color="#444444", lw=0.5)
        ax.set(title=n.replace("_", " "), xlabel="Share of money traded (%)")
    axes[0].set_ylabel("Share of alpha (%)")
    axes[0].legend(fontsize=7, loc="upper left")
    _save(fig, out, "fig_recipes")


def fig_options_audit(S, ctx, out):
    runs, d = ctx["runs"], ctx["daily"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9), gridspec_kw=dict(width_ratios=[1.35, 1]))
    for n, c, ls in (("options_cap20_legacy", RED, "-"), ("options_cap20_prefix_fix", GOLD, "-"), ("options_cap20", GREEN, "-"), ("options_worst_case", GREY, "--")):
        x = d[n].set_index("date")["nav"]
        x = x[x.index >= x.ne(x.iloc[0]).idxmax()]
        axes[0].plot(x.index, x / x.iloc[0], color=c, ls=ls, lw=1.1, label=n)
    axes[0].set_yscale("log")
    axes[0].set(ylabel="NAV, rebased to 1 at first trade (log)", title="(a) Four conventions")
    axes[0].legend(fontsize=7, loc="upper left")
    names = ["options_cap20_legacy", "options_cap20_prefix_fix", "options_cap20", "options_cap30_legacy", "options_cap30_prefix_fix", "options_cap30", "options_worst_case"]
    x = np.arange(len(names))
    cols = [RED, GOLD, GREEN, RED, GOLD, GREEN, GREY]
    axes[1].bar(x, [runs[n]["summary"]["sharpe"] for n in names], color=cols, width=0.65)
    axes[1].axhline(0, color="#444444", lw=0.6)
    axes[1].set_xticks(x, [n.replace("options_", "").replace("_prefix_fix", "\npre-fix").replace("_legacy", "\nlegacy").replace("_", "\n") for n in names], fontsize=6)
    axes[1].set(ylabel="Sharpe ratio", title="(b) Sharpe by book")
    axes[1].grid(axis="x", visible=False)
    _save(fig, out, "fig_options_audit")


def fig_options_trades(S, ctx, out):
    ps, ys = ctx.get("put_share"), ctx.get("opt_year_share")
    if not ps:
        return
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.5))
    axes[0].bar(sorted(ps), [100 * ps[k] for k in sorted(ps)], color=GREEN, width=0.65)
    axes[0].set(xlabel="SUE decile", ylabel="Put share of picks (%)", title="(a) Selector direction (options_cap20)", xticks=sorted(ps))
    axes[0].grid(axis="x", visible=False)
    yrs = sorted(ys)
    axes[1].bar(yrs, [100 * ys[y] for y in yrs], color=[GREEN if ys[y] >= 0 else RED for y in yrs], width=0.7)
    axes[1].set(xlabel="Entry year", ylabel="Share of net P&L (%)", title="(b) Where the profit comes from", xticks=yrs)
    axes[1].tick_params(axis="x", labelsize=7.5, rotation=60)
    axes[1].grid(axis="x", visible=False)
    _save(fig, out, "fig_options_trades")


def fig_frontiers(S, ctx, out):
    runs = ctx["runs"]
    sw = pd.read_csv(Path(__file__).resolve().parents[2] / "data" / "sweep_aggressive_strategy6.csv")
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.8))
    a = axes[0]
    a.plot(1e3 * sw.base_unit_fraction, 100 * sw.annualized_return, color=NAVY, marker="o", ms=3, label="Return")
    a.plot(1e3 * sw.base_unit_fraction, 100 * sw.annualized_vol, color=RED, marker="s", ms=3, label="Vol")
    a.plot(1e3 * sw.base_unit_fraction, 100 * sw.pct_liquidity_capped, color=GREY, ls="--", lw=1, label="% ADV-capped")
    chosen = runs["balanced_leverage_max"]["params"]["base_unit_fraction"]
    a.axvline(1e3 * chosen, color=GOLD, lw=0.9, ls=":")
    a.set(xlabel=r"Base unit $b$ ($\times10^{-3}$ NAV)", ylabel="%", title="(a) Equity capacity")
    a.legend(fontsize=6.5, loc="upper left")
    b = axes[1]
    for n, c, mk in (("options_cap20", GREEN, "o"), ("options_cap30", GREEN, "s"), ("options_worst_case", GREY, "^"),
                     ("options_cap20_legacy", RED, "o"), ("options_cap30_legacy", RED, "s"), ("options_cap40_legacy", RED, "D")):
        if n in runs:
            s = runs[n]["summary"]
            b.scatter(100 * s["annualized_vol"], 100 * s["annualized_return"], color=c, marker=mk, s=28, label=n.replace("options_", ""))
    b.set(xlabel="Volatility (%)", ylabel="Return (%)", title="(b) Options budget dial")
    b.legend(fontsize=6, loc="upper left", ncol=1)
    fr = ctx["frontier"]
    c_ax = axes[2]
    c_ax.plot(100 * fr.annualized_vol, 100 * fr.annualized_return, color=GOLD, marker="o", ms=2.5)
    c_ax.set(xlabel="Volatility (%)", ylabel="Return (%)", title="(c) Equity/options capital split")
    c_ax.annotate("100% options", (100 * fr.iloc[0].annualized_vol, 100 * fr.iloc[0].annualized_return), fontsize=6.5, xytext=(3, -9), textcoords="offset points")
    c_ax.annotate("100% equity", (100 * fr.iloc[-1].annualized_vol, 100 * fr.iloc[-1].annualized_return), fontsize=6.5, xytext=(-45, 4), textcoords="offset points")
    fig.tight_layout()
    _save(fig, out, "fig_frontiers")


def fig_dsr(S, ctx, out):
    cur = ctx["dsr_curves"]
    from pead.reporting.paper import dsr_at
    Ns = np.unique(np.round(np.logspace(0.5, 6, 120)).astype(int))
    fig, ax = plt.subplots(figsize=(5.2, 3.0))
    for n, c in (("calm_weighted", NAVY), ("calm_rank_tilt", TEAL), ("balanced_leverage_max", RED), ("options_cap20", GREEN)):
        sr, T, sk, ku, var_ann, kind = cur[n]
        ax.plot(Ns, [dsr_at(sr, T, sk, ku, g, var_ann)[1] for g in Ns], color=c, lw=1.2, label=n)
        if n == "options_cap20":
            runs = ctx["runs"]
            vc = float(np.var(ctx["meta"][(ctx["meta"].asset == "options") & ~ctx["meta"].index.str.endswith("_legacy")]["sharpe"].to_numpy(), ddof=1))
            ax.plot(Ns, [dsr_at(sr, T, sk, ku, g, vc)[1] for g in Ns], color=GREEN, lw=1.0, ls="--", label="options_cap20 (corrected-run dispersion)")
    ax.axhline(0.95, color="#444444", lw=0.7, ls=":")
    for key, c in (("trials.eq", NAVY), ("trials.op", GREEN)):
        ax.axvline(S.get(key), color=c, lw=0.7, ls=":")
    ax.set_xscale("log")
    ax.set(xlabel="Assumed number of independent trials $N$", ylabel="Deflated Sharpe ratio", ylim=(-0.02, 1.02))
    ax.legend(fontsize=6.8, loc="lower left")
    _save(fig, out, "fig_dsr")


def fig_kelly(ctx, out):
    """Stylized illustration, NOT estimated from data: a 40-state law with a diffuse body and a thin right-tail jump."""
    rng = np.random.default_rng(7)
    n_states, n_draw = 40, 200_000
    jump = rng.random(n_draw) < 0.04
    draws = np.sort(np.where(jump, rng.normal(0.55, 0.08, n_draw), rng.normal(0.02, 0.08, n_draw)))
    edges = np.quantile(draws, np.linspace(0, 1, n_states + 1))
    bucket = np.clip(np.digitize(draws, edges[1:-1]), 0, n_states - 1)
    r_state = np.array([draws[bucket == s].mean() for s in range(n_states)])
    p_state = np.full(n_states, 1 / n_states)
    S0, sigma_iv, T_exp = 100.0, 0.30, 120 / 365
    from pead.core.kelly import F_GRID
    f_fine = np.linspace(0.001, 0.95, 600)

    def bs_call(S, K, T, sig):
        d1 = (np.log(S / K) + 0.5 * sig ** 2 * T) / (sig * np.sqrt(T))
        return S * sps.norm.cdf(d1) - K * sps.norm.cdf(d1 - sig * np.sqrt(T))

    def growth(K, fs):
        R = np.maximum(0, S0 * (1 + r_state) - K) / bs_call(S0, K, T_exp, sigma_iv) - 1
        return np.array([np.sum(p_state * np.log1p(f * R)) for f in fs]), np.sum(p_state * R)
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
    for K, c in [(90, NAVY), (125, GOLD), (155, RED)]:
        G, ev = growth(K, f_fine)
        axes[0].plot(f_fine, 1e3 * G, color=c, lw=1.2, label=rf"$K={K}$, $E[R]={100 * ev:+.0f}\%$")
    axes[0].axhline(0, color="#444444", lw=0.6)
    axes[0].axvspan(0, F_GRID.min(), color="#dddddd", alpha=0.6, lw=0)
    axes[0].set_ylim(-20, 16)
    axes[0].set_xlim(0, 0.6)
    axes[0].set(xlabel="Bet fraction $f$", ylabel=r"$G(f)=E[\log(1+fR)]\times 10^3$", title="(a) Growth curves (stylized)")
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
    ax2.set(xlabel="Moneyness $K/S_0$", ylabel=r"$g^\ast\times 10^3$", title="(b) Score vs strike (stylized)")
    ax3 = ax2.twinx()
    ax3.plot(Ks / S0, 100 * np.array(ev_k), color=RED, lw=1.0, alpha=0.8, label="Naive $E[R]$ (right)")
    ax3.set_ylabel("$E[R]$ (%)")
    ax3.spines["right"].set_visible(True)
    ax3.grid(False)
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax3.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, fontsize=6.8, loc="upper center", bbox_to_anchor=(0.35, -0.24), ncol=3)
    _save(fig, out, "fig_kelly_illustration")


def fig_ladder(S, ctx, out):
    eq, op = ctx["ladder_eq"], ctx["ladder_op"]
    pick = [n for n in ["balanced_leverage", "calm_weighted", "calm_rank_tilt", "extreme_picks", "balanced_leverage_max", "market_boost_max"] if n in eq.index]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9), gridspec_kw=dict(width_ratios=[1.5, 1]))
    x = np.arange(len(pick))
    for i, (st, lab, c) in enumerate((("published", "published", GREY), ("fix1", "+ open-book cap", GOLD), ("fix12", "+ point-in-time deciles", RED), ("all", "+ decay days (all fixes)", NAVY))):
        axes[0].bar(x + (i - 1.5) * 0.2, [100 * eq.loc[n, f"{st}_ret"] for n in pick], 0.2, color=c, label=lab)
    axes[0].set_xticks(x, [SHORT.get(n, n.replace("_", "\n")) if n in SHORT else n.replace("balanced_leverage", "bal.-lev.").replace("_", "\n") for n in pick], fontsize=7)
    axes[0].set(ylabel="Annualized return (%)", title="(a) Equity: fixes applied in turn")
    axes[0].legend(fontsize=6.5, loc="upper left")
    axes[0].grid(axis="x", visible=False)
    stg = [("published", GREY), ("fix3_only_bidask", RED), ("all_bidask", NAVY)]
    nm = [n for n in ["options_cap20", "options_cap30"] if n in set(op.name)]
    x2 = np.arange(len(nm))
    for i, (st, c) in enumerate(stg):
        axes[1].bar(x2 + (i - 1) * 0.27, [100 * op[(op.name == n) & (op.stage == st)]["ret"].iloc[0] for n in nm], 0.27, color=c,
                    label={"published": "mid fills (published)", "fix3_only_bidask": "bid/ask fills only", "all_bidask": "all fixes"}[st])
    axes[1].axhline(0, color="#444444", lw=0.6)
    axes[1].set_xticks(x2, [n.replace("options_", "") for n in nm])
    axes[1].set(ylabel="Annualized return (%)", title="(b) Options")
    axes[1].legend(fontsize=6.5)
    axes[1].grid(axis="x", visible=False)
    _save(fig, out, "fig_ladder")


def make_all(S, ctx, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fig_decile(ctx, out); fig_spreads(ctx, out); fig_het(ctx, out)
    fig_nav(ctx, out); fig_calendar(ctx, out); fig_leverage(ctx, out)
    fig_legs(S, ctx, out); fig_betalegs(ctx, out); fig_betavol(S, ctx, out); fig_era(S, ctx, out); fig_recipes(S, ctx, out)
    fig_options_audit(S, ctx, out); fig_options_trades(S, ctx, out); fig_frontiers(S, ctx, out); fig_dsr(S, ctx, out); fig_kelly(ctx, out); fig_ladder(S, ctx, out)
    print("figures ->", out)
