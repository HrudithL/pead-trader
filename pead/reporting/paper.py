"""Build the research paper from the saved results: numbers, tables, figures, PDF.

    python -m pead.reporting.paper            # everything, including the PDF (needs MiKTeX / TeX Live)
    python -m pead.reporting.paper --no-pdf   # stats + tables + figures only
    python -m pead paper                      # same as the first line

Nothing a reader sees in docs/paper/PEAD_Research_Paper.tex is typed by hand. This module

  1. reads results/<name>/summary.json and daily.csv.gz, results/leaderboard.json, results/alpha_benchmark.csv,
     results/situations_*.csv, results/profiles_*.csv, results/portfolio/frontier.csv, and the committed
     signal-evidence tables under data/ (decile / spread / subsample / sweep files);
  2. writes docs/paper/paper_stats.json (every raw number) and docs/paper/stats.tex (one \\newcommand per number,
     e.g. \\statCalmWeightedSharpe; digits in a key are spelled out because TeX macro names cannot contain them);
  3. writes the tables as docs/paper/generated/tab_*.tex fragments and the comparisons whose direction could flip
     when results change as generated sentences (docs/paper/generated/sent_*), so the prose never hard-codes
     "highest" or "beats";
  4. regenerates every figure with matplotlib into docs/paper/figures/;
  5. checks that every \\stat... macro the .tex uses is defined, that the abstract's numbers agree with
     results/leaderboard.md, and compiles the PDF twice.

A few quantities need the gitignored position files and the licensed event panel (the beta x volatility
test, per-trade option statistics, the CRSP market series). They are computed when the main repository's data
is reachable (set PEAD_MAIN_REPO, default below) and the run it holds is the same run as results/; otherwise
docs/paper/cache/ (committed) is used and the paper says so.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
DATA = ROOT / "data"
DOCS = ROOT / "docs"
OUT = DOCS / "paper"
FIG = OUT / "figures"
GEN = OUT / "generated"
CACHE = OUT / "cache"
TEX = OUT / "PEAD_Research_Paper.tex"
MAIN_REPO = Path(os.environ.get(
    "PEAD_MAIN_REPO",
    r"C:\Users\hrudi\Documents\BEI\PEAD_Trading\.claude\worktrees\strategy-analysis-backtest-481b86"))

FIXED_DATA = Path(os.environ.get("PEAD_FIXED_DATA", str(ROOT.parent / "vintage_fixed" / "data")))   # corrected data vintage
HORIZONS = [1, 5, 10, 20, 40, 60]
TRADING_DAYS = 252
# Assumptions of the stylized model in Section 2 (not estimated from data): used only to evaluate the elasticity band.
MODEL = dict(delta=0.5, T_years=1 / 3, sigma_lo=0.35, sigma_hi=0.45, atm_const=0.4)
# The nine equity books profiled in docs/STRATEGY_PROFILES.md, in the order of the lineage.
PROFILED = ["extreme_picks", "graded_bets", "size_balanced", "conviction_tilt", "sector_balanced",
            "balanced_leverage", "calm_weighted", "calm_rank_tilt", "market_exposed"]
# Old paper labels -> plain names (the rename commit retired the S1..S10 / opt_kelly_* labels).
OLD_LABELS = [
    ("Strategy 1 (extreme-decile L/S)", "extreme_picks"), ("Strategy 2 (rank-weighted)", "graded_bets"),
    ("Strategy 3 (balanced)", "size_balanced"), ("Strategy 4", "conviction_tilt"), ("Strategy 5", "sector_balanced"),
    ("Strategy 6", "balanced_leverage"), ("Strategy 6 + 0.5x beta overlay", "market_boost"),
    ("Strategy 7", "market_exposed"), ("Strategy 9a", "volatility_filter"), ("Strategy 9b", "calm_weighted"),
    ("Strategy 9c", "calm_weighted_filter"), ("Strategy 10", "calm_rank_tilt"),
    ("Strategy 6 / 5 / 4 ... Aggressive", "balanced_leverage_max, sector_balanced_max, ... (the *_max books)"),
    ("Strategy I (earlier drafts' equity headline)", "balanced_leverage_max (= sector_balanced_max)"),
    ("Strategy II, opt_kelly_cap30 (earlier drafts' options headline)", "options_cap30_legacy (overstated); corrected: options_cap30"),
    ("opt_kelly_cap20", "options_cap20_legacy (overstated); corrected: options_cap20"),
    ("opt_kelly_cap40", "options_cap40_legacy"),
    ("Strategy 8 (walk-forward ML)", "not in the framework; excluded (ran on an unverified data copy)"),
]


# ======================================================================================================
# formatting
# ======================================================================================================
def tx(s):
    """Escape plain text for LaTeX."""
    return (str(s).replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("#", r"\#")
            .replace("_", r"\_").replace("$", r"\$"))


def sn(name):
    return r"\sn{%s}" % name


def neg(s, x):
    return ("$-$" + s) if x < 0 else s


def f2(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "--"
    return neg(f"{abs(x):.{nd}f}", x)


def pc(x, nd=2):
    """Fraction -> percent string, e.g. 0.0671 -> 6.71\\%."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "--"
    return neg(f"{abs(x) * 100:.{nd}f}\\%", x)


def pp(x, nd=1):
    """Percent value that is already in percent units."""
    return neg(f"{abs(x):.{nd}f}\\%", x)


def bp(x):
    return neg(f"{abs(x):.0f}", x)


def sg(x, nd=2):
    """Signed number: +0.05 / $-$0.58."""
    return ("$-$" if x < 0 else "+") + f"{abs(x):.{nd}f}"


def intc(x):
    return f"{int(round(x)):,}".replace(",", "{,}")


def usd_m(x, nd=1):
    return neg(f"\\${abs(x) / 1e6:,.{nd}f}M", x)


def usd_m_raw(x_millions, nd=1):
    return neg(f"\\${abs(x_millions):,.{nd}f}M", x_millions)


DIGITS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]


def macro_name(key):
    parts = re.split(r"[^A-Za-z0-9]+", key)
    out = ""
    for p in parts:
        for ch in p:
            out += DIGITS[int(ch)] if ch.isdigit() else ch
        # capitalise the first letter of each part
    camel = "".join((q[:1].upper() + q[1:]) for q in re.split(r"[^A-Za-z0-9]+", key) if q)
    camel = "".join(DIGITS[int(c)] if c.isdigit() else c for c in camel)
    return "stat" + camel


class Stats:
    """key -> (raw value, TeX string). Raw goes to paper_stats.json, TeX to stats.tex."""

    def __init__(self):
        self.raw, self.tex = {}, {}

    def put(self, key, raw, tex=None):
        if key in self.raw:
            raise KeyError(f"duplicate stat {key}")
        self.raw[key] = raw
        self.tex[key] = str(raw) if tex is None else tex

    def get(self, key):
        return self.raw[key]

    def write(self):
        lines = ["% GENERATED by pead/reporting/paper.py -- do not edit. One macro per number.",
                 "\\makeatletter"]
        # \stv{key} is the working form (key = the Python key, e.g. calm_weighted.sharpe); \statCamelCase aliases follow.
        lines.append("\\providecommand{\\stv}[1]{\\csname st:#1\\endcsname}")
        for k in sorted(self.tex):
            lines.append("\\expandafter\\gdef\\csname st:" + k + "\\endcsname{" + self.tex[k] + "\\xspace}")
            lines.append("\\providecommand{\\" + macro_name(k) + "}{\\stv{" + k + "}}")
        lines.append("\\makeatother")
        (OUT / "stats.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")

        (GEN / "macro_index.txt").parent.mkdir(parents=True, exist_ok=True)
        (GEN / "macro_index.txt").write_text(
            "\n".join(f"{k:44s} = {self.tex[k][:70]}" for k in sorted(self.tex)) + "\n", encoding="utf-8")

        def clean(v):
            if isinstance(v, (np.floating, np.integer)):
                return v.item()
            return v
        (OUT / "paper_stats.json").write_text(
            json.dumps({k: clean(v) for k, v in sorted(self.raw.items())}, indent=1, default=str), encoding="utf-8")


def write_fragment(name, text):
    GEN.mkdir(parents=True, exist_ok=True)
    (GEN / name).write_text(text, encoding="utf-8")


def table(colspec, header, rows, midrules=(), size=None):
    """Return a tabular environment. `rows` is a list of lists of ready TeX cells; a row equal to "MID" is a midrule."""
    out = []
    if size:
        out.append("{" + size)
    out.append(r"\begin{adjustbox}{max width=\linewidth}")     # shrinks only a table wider than the text
    out.append(r"\begin{tabular}{%s}" % colspec)
    out.append(r"\toprule")
    out.append(" & ".join(header) + r" \\")
    out.append(r"\midrule")
    for i, r in enumerate(rows):
        if r == "MID":
            out.append(r"\midrule")
            continue
        out.append(" & ".join(str(c) for c in r) + r" \\")
    out.append(r"\bottomrule")
    out.append(r"\end{tabular}")
    out.append(r"\end{adjustbox}")
    if size:
        out.append("}")
    return "\n".join(out) + "\n"


# ======================================================================================================
# loading
# ======================================================================================================
def load_runs():
    runs = {}
    for d in sorted(RES.iterdir()):
        s = d / "summary.json"
        if s.exists():
            m = json.loads(s.read_text(encoding="utf-8"))
            m["dir"] = d
            runs[d.name] = m
    return runs


def daily(name):
    return pd.read_csv(RES / name / "daily.csv.gz", parse_dates=["date"])


def active_returns(d):
    r = d.set_index("date")["daily_return"]
    start = r.ne(0).idxmax()
    return r.loc[start:], start


def read_csv(name, folder=DATA):
    return pd.read_csv(folder / name)


# ======================================================================================================
# external (gitignored / licensed) quantities, cached
# ======================================================================================================
def _stamp(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["run"]["timestamp"]
    except Exception:
        return None


def external(runs, refresh=False):
    """beta x volatility test, option trade statistics, CRSP market series. Cached under docs/paper/cache/."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_json = CACHE / "external.json"
    cache_mkt = CACHE / "market_daily.csv.gz"
    stamps_now = {n: _stamp(RES / n / "summary.json") for n in ("balanced_leverage", "options_cap20")}
    pos_bl = MAIN_REPO / "results" / "balanced_leverage" / "positions.parquet"
    pos_op = MAIN_REPO / "results" / "options_cap20" / "positions.parquet"
    events_p = FIXED_DATA / "equity_vol_panel.parquet"           # corrected (point-in-time) event panel
    mkt_p = MAIN_REPO / "data" / "normalized_equity" / "market_benchmark.parquet"
    dec_p = FIXED_DATA / "decile_events.parquet"
    same_run = all(_stamp(MAIN_REPO / "results" / n / "summary.json") == stamps_now[n] and stamps_now[n] is not None
                   for n in stamps_now)
    can = same_run and all(p.exists() for p in (pos_bl, pos_op, events_p, mkt_p, dec_p))
    if can and not refresh and cache_json.exists() and cache_mkt.exists():
        prev = json.loads(cache_json.read_text(encoding="utf-8"))
        if prev.get("stamps") == stamps_now:          # same run: nothing to recompute
            prev["source"] = "cache"
            return prev, pd.read_csv(cache_mkt, parse_dates=["date"])
    if can:
        ext = {"source": "recomputed", "stamps": stamps_now}
        ext.update(_beta_vol(pos_bl, events_p))
        ext.update(_option_trades(pos_op))
        ext.update(_events_facts(events_p))
        ext.update(_evidence_from_events(dec_p))
        PIT = CACHE / "pit"
        PIT.mkdir(exist_ok=True)
        for f in ("ff_decile_horizon_stats.csv", "ff_spread_horizon_stats.csv", "ff_coverage_stats.csv", "cell_decay_days.csv"):
            shutil.copyfile(FIXED_DATA / f, PIT / f)
        mk = pd.read_parquet(mkt_p, columns=["date", "vwretd", "ewretd"]).drop_duplicates("date").sort_values("date")
        mk["date"] = pd.to_datetime(mk["date"])
        mk.to_csv(cache_mkt, index=False)
        ext = json.loads(json.dumps(ext))                 # same types as when read back from the cache
        cache_json.write_text(json.dumps(ext, indent=1), encoding="utf-8")
        return ext, mk
    if cache_json.exists() and cache_mkt.exists():
        ext = json.loads(cache_json.read_text(encoding="utf-8"))
        stale = ext.get("stamps") != stamps_now
        ext["source"] = "cache-stale" if stale else "cache"
        if stale:
            print("WARNING: docs/paper/cache was built from a different run than results/ and the main "
                  "repository's positions are not available; the cached beta x volatility / option-trade "
                  "numbers are STALE.", file=sys.stderr)
        mk = pd.read_csv(cache_mkt, parse_dates=["date"])
        return ext, mk
    raise SystemExit("No positions/event data reachable (PEAD_MAIN_REPO) and no cache in docs/paper/cache/.")


def _fm(x):
    x = x.dropna()
    return float(x.mean()), float(x.std(ddof=1) / np.sqrt(len(x))), len(x)


def _evidence_from_events(path):
    """Fama-MacBeth decile means, D10-D1 spreads and subsample spreads recomputed from the corrected event file
    (market-adjusted forward returns). The same function reproduces the committed pre-correction tables exactly
    when run on the old file (checked when this was written)."""
    e = pd.read_parquet(path)
    dec, spr = [], []
    for h in HORIZONS:
        c = f"ret_fwd_{h}d_mktadj"
        d = e.dropna(subset=[c, "decile"])
        q = d.groupby(["ann_quarter", "decile"])[c].mean().unstack()
        for k in range(1, 11):
            m, se, n = _fm(q[k])
            dec.append(dict(horizon=h, decile=k, mean=m, se=se, t_stat=m / se, n_events=int((d.decile == k).sum()), n_quarters=n))
        m, se, n = _fm(q[10] - q[1])
        spr.append(dict(horizon=h, mean_spread=m, se=se, t_stat=m / se, n_quarters=n))
    c = "ret_fwd_60d_mktadj"
    subs = {}
    for tag, col in (("size", "size_quintile"), ("era", "era"), ("sector", "ff12_sector")):
        rows = []
        for g, d in e.dropna(subset=[col]).groupby(col):
            d = d.dropna(subset=[c, "decile"])
            q = d.groupby(["ann_quarter", "decile"])[c].mean().unstack()
            if 1 not in q or 10 not in q:
                continue
            m, se, n = _fm(q[10] - q[1])
            rows.append(dict(group=str(g), mean_spread=m, se=se, t_stat=m / se, n_events=int(len(d)), n_quarters=n))
        subs[tag] = rows
    return {"evidence": dict(dec=dec, spr=spr, subs=subs, n_events=int(len(e)), source=str(path.name))}


def _tstat_q(s):
    s = s.dropna()
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if len(s) >= 8 and s.std(ddof=1) > 0 else float("nan")


def _beta_vol(pos_path, events_path):
    """3x3 (beta tercile x idiosyncratic-volatility tercile) of alpha per dollar, balanced_leverage positions.
    Alpha is measured two ways, as in scripts/strategy_profiles.py and pead/reporting/attribution.py:
    against the average announcer of the same size group and quarter ('size-matched') and against the average
    of all announcers that quarter ('all-announcer')."""
    p = pd.read_parquet(pos_path)
    ev = pd.read_parquet(events_path, columns=["ann_quarter", "size_quintile", "hold_ret"]).dropna(subset=["hold_ret"])
    p = p[p["notional"].abs() > 0].copy()
    bm_size = ev.groupby(["ann_quarter", "size_quintile"])["hold_ret"].mean()
    bm_all = ev.groupby("ann_quarter")["hold_ret"].mean()
    p["bm_size"] = bm_size.reindex(pd.MultiIndex.from_arrays([p["ann_quarter"], p["size_quintile"]])).to_numpy()
    p["bm_all"] = p["ann_quarter"].map(bm_all)
    q = p["beta_252d"].quantile([1 / 3, 2 / 3]).to_numpy()
    p["beta_t"] = pd.cut(p["beta_252d"], [-99, q[0], q[1], 99], labels=["low", "mid", "high"])
    p["vol_t"] = p["ivol_252d_tercile"].map({0: "calm", 1: "mid", 2: "jumpy"})
    p = p.dropna(subset=["beta_t", "vol_t", "bm_size"])
    out = {}
    for tag, bmcol in (("size", "bm_size"), ("all", "bm_all")):
        p["alpha_net"] = p["notional"] * (p["hold_ret"] - p[bmcol]) - p["cost"]
        cells = {}
        for (b, v), g in p.groupby(["beta_t", "vol_t"], observed=True):
            n = g["notional"].abs().sum()
            qs = g.groupby("ann_quarter", observed=True).apply(
                lambda x: 1e4 * x["alpha_net"].sum() / x["notional"].abs().sum(), include_groups=False)
            cells[f"{b}|{v}"] = dict(bp=1e4 * g["alpha_net"].sum() / n, t=_tstat_q(qs), pct_dollars=n / p["notional"].abs().sum(),
                                     n=len(g))
        marg = {}
        for col, lab in (("beta_t", "beta"), ("vol_t", "vol")):
            for k, g in p.groupby(col, observed=True):
                n = g["notional"].abs().sum()
                qs = g.groupby("ann_quarter", observed=True).apply(
                    lambda x: 1e4 * x["alpha_net"].sum() / x["notional"].abs().sum(), include_groups=False)
                marg[f"{lab}|{k}"] = dict(bp=1e4 * g["alpha_net"].sum() / n, t=_tstat_q(qs),
                                          pct_dollars=n / p["notional"].abs().sum())
        # paired-by-quarter contrasts: calm-minus-jumpy within each beta tercile; low-minus-high beta within each vol tercile
        def qbp(g):
            return g.groupby("ann_quarter", observed=True).apply(
                lambda x: 1e4 * x["alpha_net"].sum() / x["notional"].abs().sum(), include_groups=False)
        contrasts = {}
        for b in ("low", "mid", "high"):
            a = qbp(p[(p.beta_t == b) & (p.vol_t == "calm")]); c = qbp(p[(p.beta_t == b) & (p.vol_t == "jumpy")])
            d = (a - c).dropna()
            contrasts[f"calm-jumpy|beta={b}"] = dict(bp=float(d.mean()), t=_tstat_q(d))
        for v in ("calm", "mid", "jumpy"):
            a = qbp(p[(p.beta_t == "low") & (p.vol_t == v)]); c = qbp(p[(p.beta_t == "high") & (p.vol_t == v)])
            d = (a - c).dropna()
            contrasts[f"low-high|vol={v}"] = dict(bp=float(d.mean()), t=_tstat_q(d))
        out[tag] = dict(cells=cells, marginals=marg, contrasts=contrasts)
    # share of calm-tercile dollars that are low beta and of jumpy that are high beta (the overlap that confounds the two)
    ct = p.groupby(["vol_t", "beta_t"], observed=True)["notional"].apply(lambda v: v.abs().sum()).unstack()
    out["overlap"] = {"calm_low_beta_share": float(ct.loc["calm", "low"] / ct.loc["calm"].sum()),
                      "jumpy_high_beta_share": float(ct.loc["jumpy", "high"] / ct.loc["jumpy"].sum()),
                      "corr_beta_ivolpct": float(p[["beta_252d", "ivol_252d_pct"]].corr().iloc[0, 1])}
    return {"beta_vol": out}


def _option_trades(pos_path):
    p = pd.read_parquet(pos_path)
    n = len(p)
    gross = p["gross_pnl"].sum()
    net = p["net_pnl"]
    out = dict(n_trades=n, pct_calls=float((p["kelly_side"] == "C").mean()),
               pct_gross_pnl_calls=float(p.loc[p.kelly_side == "C", "gross_pnl"].sum() / gross),
               win_rate=float((p["net_pnl"] > 0).mean()), median_ret_on_premium=float(p["ret_on_premium"].median()),
               mean_ret_on_premium_w=float(p["gross_pnl"].sum() / p["premium_paid"].sum()),
               top1pct_share_of_net=float(net.nlargest(max(1, n // 100)).sum() / net.sum()),
               total_net_M=float(net.sum() / 1e6), total_premium_M=float(p["premium_paid"].sum() / 1e6),
               median_entry_premium=float(p["kelly_premium"].median()))
    put_share = p.groupby("decile")["kelly_side"].apply(lambda s: float((s == "P").mean()))
    out["put_share_by_decile"] = {int(k): float(v) for k, v in put_share.items()}
    # moneyness requires spot: not stored; use the decile split only. Year split of net P&L:
    yr = p.groupby(p["day0_date"].dt.year)["net_pnl"].sum()
    out["net_pnl_share_by_year"] = {int(k): float(v / net.sum()) for k, v in yr.items()}
    return out


def _events_facts(events_path):
    ev = pd.read_parquet(events_path, columns=["permno", "ann_quarter"])
    return {"events": dict(n_events=int(len(ev)), n_firms=int(ev["permno"].nunique()),
                           n_quarters=int(ev["ann_quarter"].nunique()))}


# ======================================================================================================
# the compute phase: fills Stats and writes table fragments; returns a context for the figures
# ======================================================================================================
def compute(S, runs, ext, mkt):
    ctx = {"runs": runs, "ext": ext}
    names = sorted(runs)
    meta = pd.DataFrame([dict(name=n, asset=runs[n]["asset_class"], status=runs[n]["status"], family=runs[n].get("family"),
                              parent=runs[n].get("parent"), desc=runs[n].get("description", ""), **runs[n]["summary"])
                         for n in names]).set_index("name")
    ctx["meta"] = meta

    # ---------- per-run scalars ----------
    daily_cache = {}
    for n in names:
        s = runs[n]["summary"]
        S.put(f"{n}.ret", s["annualized_return"], pc(s["annualized_return"]))
        S.put(f"{n}.vol", s["annualized_vol"], pc(s["annualized_vol"]))
        S.put(f"{n}.sharpe", s["sharpe"], f2(s["sharpe"]))
        S.put(f"{n}.dd", s["max_drawdown"], pc(s["max_drawdown"], 1))
        S.put(f"{n}.calmar", s["calmar"], f2(s["calmar"]))
        S.put(f"{n}.nav", s["final_nav"], usd_m(s["final_nav"]))
        S.put(f"{n}.status", runs[n]["status"], tx(runs[n]["status"]))
        for k, lab in (("sharpe_1996_2001", "shA"), ("sharpe_2002_2007", "shB"), ("sharpe_2008_2013", "shC")):
            S.put(f"{n}.{lab}", s[k], f2(s[k]))
        d = daily(n)
        daily_cache[n] = d
        r, start = active_returns(d)
        S.put(f"{n}.active_start", str(start.date()), str(start.date()))
        S.put(f"{n}.sharpe_active", float(r.mean() / r.std() * np.sqrt(TRADING_DAYS)),
              f2(r.mean() / r.std() * np.sqrt(TRADING_DAYS)))
        if "gross_exposure" in d:
            g = (d["gross_exposure"] / d["nav"])[d["gross_exposure"] > 0]
            S.put(f"{n}.gross_avg", float(g.mean()), f"{g.mean():.1f}")
            S.put(f"{n}.gross_max", float(g.max()), f"{g.max():.1f}")
            S.put(f"{n}.gross_p99", float(g.quantile(0.99)), f"{g.quantile(0.99):.1f}")
            S.put(f"{n}.net_to_gross", float(abs(d["net_exposure"]).sum() / d["gross_exposure"].sum()),
                  pc(abs(d["net_exposure"]).sum() / d["gross_exposure"].sum(), 0))
        if "pct_liquidity_capped" in s:
            S.put(f"{n}.liqcap", s["pct_liquidity_capped"], pc(s["pct_liquidity_capped"], 1))
        if "n_positions" in s:
            S.put(f"{n}.npos", s["n_positions"], intc(s["n_positions"]))
            S.put(f"{n}.avgopen", s["avg_n_open_positions"], intc(s["avg_n_open_positions"]))
    ctx["daily"] = daily_cache
    S.put("n_runs", len(names), str(len(names)))
    S.runs_names = set(names)
    # breadth, borrow sensitivity and the sharpe-denominator convention, all from daily.csv.gz / summary.json
    for n in ("calm_weighted", "balanced_leverage", "calm_rank_tilt", "options_cap20", "options_cap30"):
        d = daily_cache[n]
        r, start = active_returns(d)
        yrs = len(r) / TRADING_DAYS
        npos = runs[n]["summary"].get("n_positions", runs[n]["summary"].get("n_positions_sized"))
        S.put(f"{n}.active_years", float(yrs), f"{yrs:.1f}")
        S.put(f"{n}.pos_per_year", float(npos / yrs), intc(npos / yrs))
        ic = runs[n]["summary"]["sharpe"] / np.sqrt(npos / yrs)
        S.put(f"{n}.implied_ic", float(ic), f"{ic:.3f}")
        S.put(f"{n}.sharpe_fixed", runs[n]["summary"]["sharpe_fixed_capital"], f2(runs[n]["summary"]["sharpe_fixed_capital"]))
        if "short_exposure" in d:
            sh = (d["short_exposure"].abs() / d["nav"])[d["gross_exposure"] > 0]
            S.put(f"{n}.short_avg", float(sh.mean()), f"{sh.mean():.2f}")
            S.put(f"{n}.borrow_1pct", float(sh.mean() * 0.01), pc(sh.mean() * 0.01, 2))
            S.put(f"{n}.borrow_breakeven", float(runs[n]["summary"]["annualized_return"] / sh.mean()), pc(runs[n]["summary"]["annualized_return"] / sh.mean(), 1))
            S.put(f"{n}.ret_at5", float(runs[n]["summary"]["annualized_return"] - 0.05 * sh.mean()), pc(runs[n]["summary"]["annualized_return"] - 0.05 * sh.mean(), 1))
    first = names[0]
    S.put("calendar_start", str(daily_cache["calm_weighted"]["date"].iloc[0].date()))
    S.put("calendar_end", str(daily_cache["calm_weighted"]["date"].iloc[-1].date()))
    S.put("initial_capital", float(daily_cache["calm_weighted"]["nav"].iloc[0]), usd_m(daily_cache["calm_weighted"]["nav"].iloc[0], 0))

    # ---------- market (CRSP value-weighted) ----------
    m = mkt.set_index("date")["vwretd"].fillna(0.0)
    # same calendar the strategy statistics use (first to last day of the equity daily series), so the comparison is like for like
    d0, d1 = daily_cache["calm_weighted"]["date"].iloc[0], daily_cache["calm_weighted"]["date"].iloc[-1]
    m = m.loc[d0:d1]
    S.put("market.window", f"{d0.year}--{d1.year}", f"{d0.year}--{d1.year}")
    mnav = (1 + m).cumprod()
    mdd = (mnav / mnav.cummax() - 1).min()
    ann = mnav.iloc[-1] ** (TRADING_DAYS / len(m)) - 1
    S.put("market.ret", float(ann), pc(ann, 1))
    S.put("market.vol", float(m.std() * np.sqrt(TRADING_DAYS)), pc(m.std() * np.sqrt(TRADING_DAYS), 0))
    S.put("market.sharpe", float(m.mean() / m.std() * np.sqrt(TRADING_DAYS)), f2(m.mean() / m.std() * np.sqrt(TRADING_DAYS)))
    S.put("market.dd", float(mdd), pc(mdd, 0))
    ctx["market"] = mkt

    leader(S, runs, meta)
    signal_evidence(S, ctx)
    equity_section(S, ctx)
    long_short_section(S, ctx)
    beta_vol_section(S, ctx)
    options_section(S, ctx)
    blend_section(S, ctx)
    frontier_section(S, ctx)
    inference_section(S, ctx)
    sentences(S, ctx)
    ladder_section(S, ctx)
    audit_section(S, ctx)
    reproducibility_section(S, ctx)
    return ctx


# ------------------------------------------------------------------------------------------------------
def leader(S, runs, meta):
    """Leaderboard facts: parse results/leaderboard.json (what the repo itself ranks) and check results/leaderboard.md."""
    lb = json.loads((RES / "leaderboard.json").read_text(encoding="utf-8"))
    for ac in lb:
        for metric, v in lb[ac].items():
            S.put(f"lb.{ac}.{metric}.name", v["strategy"], sn(v["strategy"]))
            S.put(f"lb.{ac}.{metric}.value", v["value"], f2(v["value"]) if metric != "annualized_return" else pc(v["value"]))
    # verify results/leaderboard.md rows against summary.json (2 dp), the numbers the abstract quotes
    md = (RES / "leaderboard.md").read_text(encoding="utf-8")
    bad = []
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 8 and cells[0] in runs:
            n = cells[0]
            s = runs[n]["summary"]
            exp = [f"{s['annualized_return'] * 100:.2f}%", f"{s['annualized_vol'] * 100:.2f}%", f"{s['sharpe']:.2f}",
                   f"{s['max_drawdown'] * 100:.2f}%", f"{s['calmar']:.2f}"]
            if cells[3:] != exp:
                bad.append((n, cells[3:], exp))
    if bad:
        raise SystemExit(f"leaderboard.md disagrees with summary.json: {bad[:3]}")
    S.put("leaderboard_checked", True, "")


# ------------------------------------------------------------------------------------------------------
def signal_evidence(S, ctx):
    # Equity signal tables use the CORRECTED (point-in-time decile) vintage, recomputed from the corrected event file or
    # read from docs/paper/cache; the committed data/ tables are the pre-correction (within-quarter ranking) vintage and
    # are kept only for the before/after comparison. Option-spread, option-coverage, reversal and volatility-diagnostic
    # tables exist only in the pre-correction vintage and are labelled as such in the text.
    pit = ctx["ext"]["evidence"]
    PIT = CACHE / "pit"
    dec = pd.DataFrame(pit["dec"])
    ffd = pd.read_csv(PIT / "ff_decile_horizon_stats.csv")
    spr = pd.DataFrame(pit["spr"]).set_index("horizon")
    ffs = pd.read_csv(PIT / "ff_spread_horizon_stats.csv").set_index("horizon")
    pre_spr = read_csv("spread_horizon_stats.csv").set_index("horizon")
    pre_ffs = read_csv("ff_spread_horizon_stats.csv").set_index("horizon")
    osp = read_csv("option_spread_horizon_stats.csv")
    ocov = read_csv("option_coverage_stats.csv")
    cov = pd.read_csv(PIT / "ff_coverage_stats.csv").iloc[0]
    size = pd.DataFrame(pit["subs"]["size"]).assign(group=lambda d: d["group"].astype(float).astype(int)).sort_values("group")
    era = pd.DataFrame(pit["subs"]["era"])
    era["start"] = era["group"].str[:4].astype(int)
    era = era.sort_values("start")
    sec = pd.DataFrame(pit["subs"]["sector"]).sort_values("mean_spread")
    rev = json.loads((DATA / "sue_reversal_summary.json").read_text(encoding="utf-8"))
    decay = pd.read_csv(PIT / "cell_decay_days.csv")
    ctx.update(dec=dec, ffd=ffd, spr=spr, ffs=ffs, osp=osp, size=size, era=era, sec=sec, pre_spr=pre_spr, pre_ffs=pre_ffs)
    for h in HORIZONS:
        S.put(f"pre.spread.mkt.h{h}", float(pre_spr.loc[h, "mean_spread"]), f"{pre_spr.loc[h, 'mean_spread'] * 100:.2f}\\%")
        S.put(f"pre.spread.ff.h{h}", float(pre_ffs.loc[h, "mean_spread"]), f"{pre_ffs.loc[h, 'mean_spread'] * 100:.2f}\\%")
        S.put(f"pre.spread.mkt.t.h{h}", float(pre_spr.loc[h, "t_stat"]), f"{pre_spr.loc[h, 't_stat']:.2f}")
    S.put("pre.Q", int(read_csv("decile_horizon_stats.csv")["n_quarters"].max()), str(int(read_csv("decile_horizon_stats.csv")["n_quarters"].max())))
    S.put("pre.n_events", int(read_csv("ff_coverage_stats.csv").iloc[0]["n_events"]), intc(read_csv("ff_coverage_stats.csv").iloc[0]["n_events"]))
    rows = []
    for h in HORIZONS:
        rows.append([f"$+{h}$d", f2(pre_spr.loc[h, "mean_spread"] * 100), f2(pre_spr.loc[h, "t_stat"]), f2(spr.loc[h, "mean_spread"] * 100), f2(spr.loc[h, "t_stat"]),
                     f2(pre_ffs.loc[h, "mean_spread"] * 100), f2(pre_ffs.loc[h, "t_stat"]), f2(ffs.loc[h, "mean_spread"] * 100), f2(ffs.loc[h, "t_stat"])])
    tab = table("lrrrrrrrr", ["Horizon", r"$\hat\mu$", "$t$", r"$\hat\mu$", "$t$", r"$\hat\mu$", "$t$", r"$\hat\mu$", "$t$"], rows, size=r"\small")
    tab = tab.replace(r"\toprule" + "\n", r"\toprule" + "\n & \\multicolumn{2}{c}{Mkt-adj., within-quarter} & \\multicolumn{2}{c}{Mkt-adj., point-in-time} & "
                      r"\multicolumn{2}{c}{Size/BM-adj., within-quarter} & \multicolumn{2}{c}{Size/BM-adj., point-in-time}\\" + "\n"
                      r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}" + "\n")
    write_fragment("tab_pit_compare.tex", tab)
    S.put("pit.drop60", float(pre_spr.loc[60, "mean_spread"] - spr.loc[60, "mean_spread"]), pp((pre_spr.loc[60, "mean_spread"] - spr.loc[60, "mean_spread"]) * 100, 2))
    S.put("sent.pit_effect", "lowers" if spr.loc[60, "mean_spread"] < pre_spr.loc[60, "mean_spread"] else "does not lower")

    Q = int(dec["n_quarters"].max())
    S.put("Q", Q, str(Q))
    S.put("n_events", int(cov["n_events"]), intc(cov["n_events"]))
    S.put("n_events_sizebm", int(cov["has_both_size_and_bm"]), intc(cov["has_both_size_and_bm"]))
    S.put("n_events_bookeq", int(cov["has_book_equity"]), intc(cov["has_book_equity"]))
    ext_ev = ctx["ext"].get("events", {})
    S.put("n_firms", ext_ev.get("n_firms", 0), intc(ext_ev.get("n_firms", 0)))
    S.put("n_tradeable_events", ext_ev.get("n_events", 0), intc(ext_ev.get("n_events", 0)))
    callsel = ocov[(ocov.cp_type == "call") & (ocov.horizon == 1)]["n_contracts_selected"].sum()
    callpr = ocov[(ocov.cp_type == "call") & (ocov.horizon == 60)]["n_priced"].sum()
    S.put("n_events_call_atm", int(callsel), intc(callsel))
    S.put("n_events_call_priced60", int(callpr), intc(callpr))
    # decile / spread tables
    rows = []
    for d in range(1, 11):
        lab = "D1 (most negative)" if d == 1 else ("D10 (most positive)" if d == 10 else f"D{d}")
        r = [lab]
        for h in HORIZONS:
            v = dec[(dec.decile == d) & (dec.horizon == h)]["mean"].iloc[0] * 100
            r.append(f"${v:+.2f}$")
        rows.append(r)
    write_fragment("tab_deciles.tex", table("lrrrrrr", ["Decile"] + [f"$+{h}$d" for h in HORIZONS], rows, size=r"\small"))
    for h in HORIZONS:
        S.put(f"spread.mkt.h{h}", float(spr.loc[h, "mean_spread"]), f"{spr.loc[h, 'mean_spread'] * 100:.2f}\\%")
        S.put(f"spread.mkt.t.h{h}", float(spr.loc[h, "t_stat"]), f"{spr.loc[h, 't_stat']:.2f}")
        S.put(f"spread.ff.h{h}", float(ffs.loc[h, "mean_spread"]), f"{ffs.loc[h, 'mean_spread'] * 100:.2f}\\%")
        S.put(f"spread.ff.t.h{h}", float(ffs.loc[h, "t_stat"]), f"{ffs.loc[h, 't_stat']:.2f}")
    rows = []
    for h in HORIZONS:
        r = [f"$+{h}$d", f2(spr.loc[h, "mean_spread"] * 100), f2(spr.loc[h, "t_stat"]),
             f2(ffs.loc[h, "mean_spread"] * 100), f2(ffs.loc[h, "t_stat"])]
        for cp in ("call", "put", "straddle"):
            o = osp[(osp.cp_type == cp) & (osp.horizon == h)].iloc[0]
            r += [f2(o["mean_spread"] * 100), f2(o["t_stat"])]
        rows.append(r)
    hdr = ["Horizon", r"$\hat\mu$", "$t$", r"$\hat\mu$", "$t$", r"$\hat\mu$", "$t$", r"$\hat\mu$", "$t$", r"$\hat\mu$", "$t$"]
    tab = table("lrrrrrrrrrr", hdr, rows, size=r"\small")
    tab = tab.replace(r"\toprule" + "\n", r"\toprule" + "\n" +
                      r" & \multicolumn{2}{c}{Mkt-adj.\ equity} & \multicolumn{2}{c}{Size/BM-adj.\ equity} & "
                      r"\multicolumn{2}{c}{Call} & \multicolumn{2}{c}{Put} & \multicolumn{2}{c}{Straddle}\\" + "\n" +
                      r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}\cmidrule(lr){10-11}" + "\n")
    write_fragment("tab_spreads.tex", tab)
    nq_opt = int(osp["n_quarters"].max())
    S.put("Q_opt", nq_opt, str(nq_opt))
    S.put("Q_opt60", int(osp[osp.horizon == 60]["n_quarters"].min()), str(int(osp[osp.horizon == 60]["n_quarters"].min())))
    # D1 characteristic-adjusted 60d
    d1ff = ffd[(ffd.decile == 1) & (ffd.horizon == 60)]["mean"].iloc[0] * 100
    d1mk = dec[(dec.decile == 1) & (dec.horizon == 60)]["mean"].iloc[0] * 100
    d10mk = dec[(dec.decile == 10) & (dec.horizon == 60)]["mean"].iloc[0] * 100
    S.put("d1_ff_60", float(d1ff), pp(d1ff, 2))
    S.put("d1_mk_60", float(d1mk), pp(d1mk, 2))
    S.put("d10_mk_60", float(d10mk), pp(d10mk, 2))
    S.put("d1_mk_peak", float(dec[dec.decile == 1].sort_values("horizon")["mean"].min() * 100),
          pp(dec[dec.decile == 1]["mean"].min() * 100, 2))
    # is the 60d D1 market-adjusted mean positive, and does the FF adjustment push it down? (generated sentence)
    S.put("sent.d1recovery",
          ("the worst decile's market-adjusted return recovers to %s by day 60" % pp(d1mk, 2)) if d1mk > 0 else
          ("the worst decile's market-adjusted return stays negative at day 60 (%s)" % pp(d1mk, 2)), None)
    S.put("sent.ffvsmkt",
          "enlarges" if ffs.loc[60, "mean_spread"] > spr.loc[60, "mean_spread"] else "shrinks")
    # monotone D1..D10 at which horizons
    mono = [h for h in HORIZONS if np.all(np.diff(dec[dec.horizon == h].sort_values("decile")["mean"].to_numpy()) > 0)]
    S.put("mono_horizons", mono, ", ".join(f"+{h}d" for h in mono) if mono else "none")
    S.put("mono_from", min(mono) if mono else -1, f"+{min(mono)}" if mono else "no horizon")
    S.put("tmin_spread", float(min(spr["t_stat"].min(), ffs["t_stat"].min())), f"{min(spr['t_stat'].min(), ffs['t_stat'].min()):.1f}")
    S.put("tmin_mkt_spread", float(spr["t_stat"].min()), f"{spr['t_stat'].min():.1f}")
    # power-law and saturating-exponential fits to the market-adjusted and characteristic-adjusted spreads
    hs = np.array(HORIZONS, float)
    for tag, ser in (("mkt", spr["mean_spread"]), ("ff", ffs["mean_spread"])):
        y = ser.loc[HORIZONS].to_numpy() * 100
        b, a = np.polyfit(np.log(hs), np.log(y), 1)
        fit = np.exp(a) * hs ** b
        r2_log = 1 - ((np.log(y) - (a + b * np.log(hs))) ** 2).sum() / ((np.log(y) - np.log(y).mean()) ** 2).sum()
        r2_lvl = 1 - ((y - fit) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        try:
            from scipy.optimize import curve_fit
            f = lambda h, A, tau: A * (1 - np.exp(-h / tau))
            (A, tau), _ = curve_fit(f, hs, y, p0=[y.max(), 20], maxfev=20000)
            r2_exp = 1 - ((y - f(hs, A, tau)) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        except Exception:
            A = tau = r2_exp = float("nan")
        S.put(f"pl.{tag}.coef", float(np.exp(a)), f"{np.exp(a):.2f}")
        S.put(f"pl.{tag}.exp", float(b), f"{b:.2f}")
        S.put(f"pl.{tag}.r2log", float(r2_log), f"{r2_log:.2f}")
        S.put(f"pl.{tag}.r2lvl", float(r2_lvl), f"{r2_lvl:.2f}")
        S.put(f"pl.{tag}.exp_r2", float(r2_exp), f"{r2_exp:.2f}")
        S.put(f"pl.{tag}.exp_tau", float(tau), f"{tau:.0f}")
        ctx[f"pl_{tag}"] = (np.exp(a), b)
    S.put("sent.expfit",
          "fits visibly worse" if S.get("pl.mkt.exp_r2") < S.get("pl.mkt.r2lvl") and S.get("pl.ff.exp_r2") < S.get("pl.ff.r2lvl")
          else "does not fit worse")
    # options amplification
    call60 = osp[(osp.cp_type == "call") & (osp.horizon == 60)].iloc[0]
    put60 = osp[(osp.cp_type == "put") & (osp.horizon == 60)].iloc[0]
    str60 = osp[(osp.cp_type == "straddle") & (osp.horizon == 60)].iloc[0]
    S.put("call60", float(call60["mean_spread"] * 100), f"{call60['mean_spread'] * 100:.2f}\\%")
    S.put("call60.t", float(call60["t_stat"]), f"{call60['t_stat']:.2f}")
    S.put("put60", float(put60["mean_spread"] * 100), pp(put60["mean_spread"] * 100, 2))
    S.put("put60.t", float(put60["t_stat"]), f2(put60["t_stat"]))
    S.put("str60", float(str60["mean_spread"] * 100), f"{str60['mean_spread'] * 100:.2f}\\%")
    S.put("str60.t", float(str60["t_stat"]), f"{str60['t_stat']:.2f}")
    ratio = call60["mean_spread"] / spr.loc[60, "mean_spread"]
    S.put("amp_ratio", float(ratio), f"{ratio:.1f}")
    S.put("str_over_call", float(str60["mean_spread"] / call60["mean_spread"]), f"{str60['mean_spread'] / call60['mean_spread']:.2f}")
    om = [MODEL["delta"] / (MODEL["atm_const"] * s * math.sqrt(MODEL["T_years"])) for s in (MODEL["sigma_hi"], MODEL["sigma_lo"])]
    S.put("omega_lo", om[0], f"{om[0]:.1f}")
    S.put("omega_hi", om[1], f"{om[1]:.1f}")
    S.put("sent.ampband",
          "below the lower end of" if ratio < om[0] else ("inside" if ratio <= om[1] else "above the upper end of"))
    S.put("model.delta", MODEL["delta"], f"{MODEL['delta']:.1f}")
    S.put("model.sig_lo", MODEL["sigma_lo"], f"{MODEL['sigma_lo']:.2f}")
    S.put("model.sig_hi", MODEL["sigma_hi"], f"{MODEL['sigma_hi']:.2f}")
    S.put("model.T_months", MODEL["T_years"] * 12, f"{MODEL['T_years'] * 12:.0f}")
    # heterogeneity: Cochran Q on group means (independence approximation; the ANOVA of the drafts needs the raw series)
    def cochran(df):
        w = 1 / df["se"].to_numpy() ** 2
        m = (w * df["mean_spread"].to_numpy()).sum() / w.sum()
        Qs = (w * (df["mean_spread"].to_numpy() - m) ** 2).sum()
        k = len(df)
        return float(Qs), int(k - 1), float(1 - sps.chi2.cdf(Qs, k - 1))
    for tag, df in (("size", size), ("sector", sec), ("era", era)):
        Qs, dfree, p = cochran(df)
        S.put(f"het.{tag}.Q", Qs, f"{Qs:.1f}")
        S.put(f"het.{tag}.df", dfree, str(dfree))
        S.put(f"het.{tag}.p", p, f"{p:.3f}" if p >= 0.001 else r"<0.001")
    small, large = size.iloc[0], size.iloc[-1]       # group "1" is the smallest quintile, "5" the largest
    S.put("size.small", float(small["mean_spread"] * 100), pp(small["mean_spread"] * 100, 2))
    S.put("size.small.t", float(small["t_stat"]), f"{small['t_stat']:.2f}")
    S.put("size.large", float(large["mean_spread"] * 100), pp(large["mean_spread"] * 100, 2))
    S.put("size.large.t", float(large["t_stat"]), f"{large['t_stat']:.2f}")
    S.put("sent.sizemono",
          "falls monotonically" if np.all(np.diff(size.sort_values("group")["mean_spread"].to_numpy()) < 0) else "does not fall monotonically")
    S.put("sector.min", float(sec.iloc[0]["mean_spread"] * 100), pp(sec.iloc[0]["mean_spread"] * 100, 2))
    S.put("sector.min.name", sec.iloc[0]["group"], tx(sec.iloc[0]["group"]))
    S.put("sector.max", float(sec.iloc[-1]["mean_spread"] * 100), pp(sec.iloc[-1]["mean_spread"] * 100, 2))
    S.put("sector.max.name", sec.iloc[-1]["group"], tx(sec.iloc[-1]["group"]))
    S.put("sector.n", len(sec), str(len(sec)))
    S.put("era.first", float(era.iloc[0]["mean_spread"] * 100), pp(era.iloc[0]["mean_spread"] * 100, 2))
    S.put("era.first.name", era.iloc[0]["group"], tx(era.iloc[0]["group"].replace("-", "--")))
    S.put("era.last", float(era.iloc[-1]["mean_spread"] * 100), pp(era.iloc[-1]["mean_spread"] * 100, 2))
    S.put("era.last.name", era.iloc[-1]["group"], tx(era.iloc[-1]["group"].replace("-", "--")))
    rows = [[f"Size quintile {int(g)}" + (" (smallest)" if int(g) == 1 else (" (largest)" if int(g) == 5 else "")),
             f2(r["mean_spread"] * 100), f2(r["t_stat"]), intc(r["n_events"])]
            for _, r in size.sort_values("group").iterrows() for g in [r["group"]]]
    rows.append("MID")
    rows += [[f"Era {r['group'].replace('-', '--')}", f2(r["mean_spread"] * 100), f2(r["t_stat"]), intc(r["n_events"])]
             for _, r in era.iterrows()]
    write_fragment("tab_het.tex", table("lrrr", ["Group", "60d D10$-$D1 (\\%)", "$t$", "Events"], rows, size=r"\small"))
    # reversal
    S.put("rev.ac", rev["autocorrelation_lag4"], sg(rev["autocorrelation_lag4"]))
    S.put("rev.lit", rev["literature_reference_bernard_thomas_1990"], sg(rev["literature_reference_bernard_thomas_1990"]))
    S.put("rev.n", rev["n_pairs"], intc(rev["n_pairs"]))
    S.put("rev.d1", rev["decile1_lag4_mean_sue_t"], sg(rev["decile1_lag4_mean_sue_t"]))
    S.put("rev.d10", rev["decile10_lag4_mean_sue_t"], sg(rev["decile10_lag4_mean_sue_t"]))
    # EAR: reported in docs/HISTORY.md (no machine-readable output is kept)
    hist = (DOCS / "HISTORY.md").read_text(encoding="utf-8")
    mm = re.search(r"flat Sharpe \(~([\d.]+)-([\d.]+)\)", hist)
    S.put("ear.range", f"{mm.group(1)}--{mm.group(2)}" if mm else "", f"{mm.group(1)}--{mm.group(2)}" if mm else "")
    # decay days
    S.put("decay.n", len(decay), str(len(decay)))
    S.put("decay.min", int(decay["decay_day"].min()), str(int(decay["decay_day"].min())))
    S.put("decay.max", int(decay["decay_day"].max()), str(int(decay["decay_day"].max())))
    S.put("decay.median", float(decay["decay_day"].median()), f"{decay['decay_day'].median():.0f}")
    S.put("decay.mean", float(decay["decay_day"].mean()), f"{decay['decay_day'].mean():.0f}")
    S.put("decay.d1", float(decay[decay.decile == 1]["decay_day"].mean()), f"{decay[decay.decile == 1]['decay_day'].mean():.0f}")
    S.put("decay.d10", float(decay[decay.decile == 10]["decay_day"].mean()), f"{decay[decay.decile == 10]['decay_day'].mean():.0f}")
    S.put("decay.d6", float(decay[decay.decile == 6]["decay_day"].mean()), f"{decay[decay.decile == 6]['decay_day'].mean():.0f}")
    S.put("decay.fine", int(decay["cell"].str.startswith("fine").sum()), str(int(decay["cell"].str.startswith("fine").sum())))
    ctx["decay"] = decay
    # estimator constants read from the source of the decay-day stage
    src = (ROOT / "pead" / "prep" / "equity" / "decay_days.py").read_text(encoding="utf-8")
    for nm in ("WINDOW", "BURN_IN", "PERSIST"):
        m_ = re.search(rf"^{nm}\s*=\s*(\d+)", src, re.M)
        S.put(f"decay.{nm.lower()}", int(m_.group(1)) if m_ else -1, m_.group(1) if m_ else "--")
    m_ = re.search(r"threshold\s*=\s*([\d.]+)\s*\*\s*peak_mag", src)
    S.put("decay.thresh", float(m_.group(1)) if m_ else -1, f"{float(m_.group(1)) * 100:.0f}" + chr(92) + "%" if m_ else "--")
    # volatility diagnostic: D10-D1 by volatility tercile
    vd = json.loads((DATA / "vol_decile_diagnostic_summary.json").read_text(encoding="utf-8"))
    ctx["vd"] = vd
    meas = "ivol_252d" if "ivol_252d" in vd else "tvol_60d"
    blk = vd[meas]["d10_minus_d1_hold_ret"]
    for t in ("low", "mid", "high"):
        S.put(f"vd.{t}", blk[t]["mean"], pp(blk[t]["mean"] * 100, 1))
        S.put(f"vd.{t}.t", blk[t]["t_stat"], f"{blk[t]['t_stat']:.1f}")
    S.put("vd.measure", meas, tx(meas))
    S.put("vd.diff.t", blk["high_minus_low"]["t_stat"], f"{blk['high_minus_low']['t_stat']:.1f}")


# ------------------------------------------------------------------------------------------------------
def equity_section(S, ctx):
    runs, meta = ctx["runs"], ctx["meta"]
    eq = meta[meta.asset == "equity"]
    ctx["eq"] = eq
    reg = None
    try:
        sys.path.insert(0, str(ROOT))
        from pead.strategies import registry
        reg = registry.all_strategies()
        S.put("n_registered", len(reg), str(len(reg)))
        S.put("n_registered_equity", sum(1 for s in reg.values() if s.asset_class == "equity"), str(sum(1 for s in reg.values() if s.asset_class == "equity")))
        S.put("n_registered_options", sum(1 for s in reg.values() if s.asset_class == "options"), str(sum(1 for s in reg.values() if s.asset_class == "options")))
        S.put("n_registered_portfolio", sum(1 for s in reg.values() if s.asset_class == "portfolio"), str(sum(1 for s in reg.values() if s.asset_class == "portfolio")))
    except Exception as e:     # keep going from results/ alone
        print("note: strategy registry not importable:", e, file=sys.stderr)
    ctx["reg"] = reg

    # construction constants straight from the strategy classes (TRIM / TILT / POWER) and sizing specs
    def attr(n, a, default=None):
        s = reg.get(n) if reg else None
        return getattr(s, a, default) if s is not None else default
    for n in ("balanced_leverage", "conviction_tilt", "calm_rank_tilt"):
        for a in ("TRIM", "TILT", "POWER"):
            v = attr(n, a)
            if v is not None:
                S.put(f"{n}.{a.lower()}", v, f"{v:g}")
    for n in ctx["runs"]:
        p = runs[n].get("params", {})
        if "base_unit_fraction" in p:
            S.put(f"{n}.base", p["base_unit_fraction"], f"{p['base_unit_fraction']:g}")
            S.put(f"{n}.cap", p["max_gross_leverage"], f"{p['max_gross_leverage']:g}")
            S.put(f"{n}.liqfrac", p["liquidity_cap_frac"], f"{p['liquidity_cap_frac'] * 100:g}\\%")
            S.put(f"{n}.overlay", p.get("beta_overlay", 0.0), f"{p.get('beta_overlay', 0.0):g}")
    # engine cost table
    try:
        from pead.engines.equity import COST_BPS
        S.put("cost.bps", COST_BPS, "/".join(str(COST_BPS[k]) for k in sorted(COST_BPS, reverse=False)))
        S.put("cost.max", max(COST_BPS.values()), str(max(COST_BPS.values())))
        S.put("cost.min", min(COST_BPS.values()), str(min(COST_BPS.values())))
    except Exception:
        pass

    # alpha vs market (results/alpha_benchmark.csv)
    ab = read_csv("alpha_benchmark.csv", RES).set_index("name")
    ctx["ab"] = ab
    for n, r in ab.iterrows():
        S.put(f"{n}.alpha", r["alpha"], pc(r["alpha"], 1))
        S.put(f"{n}.alpha_t", r["alpha_t"], f"{r['alpha_t']:.1f}")
        S.put(f"{n}.beta", r["beta"], f2(r["beta"]))
        S.put(f"{n}.alpha_sz", r["alpha_size"], pc(r["alpha_size"], 1))
        S.put(f"{n}.alpha_sz_t", r["alpha_size_t"], f"{r['alpha_size_t']:.1f}")
        S.put(f"{n}.ir", r["info_ratio"], f2(r["info_ratio"]))
        S.put(f"{n}.alphaA", r["a_96_01"], pc(r["a_96_01"], 1))
        S.put(f"{n}.alphaB", r["a_02_07"], pc(r["a_02_07"], 1))
        S.put(f"{n}.alphaC", r["a_08_13"], pc(r["a_08_13"], 1))
    # market-neutral books: is beta ~ 0?
    neutral = [n for n in ab.index if not n.startswith(("market_boost", "market_exposed")) and not n.endswith(("_legacy", "_midfill"))]
    S.put("beta.neutral.maxabs", float(ab.loc[neutral, "beta"].abs().max()), f"{ab.loc[neutral, 'beta'].abs().max():.2f}")
    S.put("beta.neutral.n", len(neutral), str(len(neutral)))
    exposed = [n for n in ab.index if n.startswith(("market_boost", "market_exposed")) and not n.endswith(("_legacy", "_midfill"))]
    S.put("beta.exposed.min", float(ab.loc[exposed, "beta"].min()), f2(ab.loc[exposed, "beta"].min()))
    S.put("beta.exposed.max", float(ab.loc[exposed, "beta"].max()), f2(ab.loc[exposed, "beta"].max()))

    # ---- headline tables ----
    def row(n, extra=()):
        s = runs[n]["summary"]
        return [sn(n), pc(s["annualized_return"]), pc(s["annualized_vol"]), f2(s["sharpe"]), pc(s["max_drawdown"], 1), f2(s["calmar"]), *extra]

    keys_main = [("calm_weighted", "validated headline"), ("calm_rank_tilt", "research headline"), ("balanced_leverage", "reference"),
                 ("market_exposed", "reference"), ("market_boost", "validated")]
    rows = [row(n) + [lab] for n, lab in keys_main if n in runs]
    write_fragment("tab_equity_headline.tex",
                   table("lrrrrrl", ["Book", "Return", "Vol", "Sharpe", "Max DD", "Calmar", "Role"], rows, size=r"\small"))

    # the whole equity family, in lineage order, with plain description from summary.json
    order = ["extreme_picks", "graded_bets", "size_balanced", "conviction_tilt", "sector_balanced", "balanced_leverage",
             "market_boost", "market_exposed", "calm_weighted", "calm_weighted_matched", "volatility_filter", "calm_weighted_filter",
             "calm_rank_tilt", "calm_rank_levered"]
    rows = []
    for n in order:
        if n not in runs:
            continue
        s = runs[n]["summary"]
        rows.append([sn(n), tx(runs[n]["status"]), tx(runs[n]["description"]), f"{runs[n]['params']['base_unit_fraction']:g} / {runs[n]['params']['max_gross_leverage']:g}$\\times$",
                     pc(s["annualized_return"], 1), f2(s["sharpe"]), pc(s["max_drawdown"], 1)])
    write_fragment("tab_equity_family.tex",
                   "{\\footnotesize\n\\begin{tabularx}{\\linewidth}{@{}l l X l r r r@{}}\n\\toprule\nBook & Status & What it does & Unit / cap & Return & Sharpe & Max DD\\\\\n\\midrule\n"
                   + "\n".join(" & ".join(r) + r" \\" for r in rows) + "\n\\bottomrule\n\\end{tabularx}\n}\n")

    # levered / market-exposed research variants with realized leverage
    maxes = [n for n in runs if n.endswith("_max")]
    maxes = sorted(maxes, key=lambda n: -runs[n]["summary"]["sharpe"])
    rows = []
    for n in maxes:
        s = runs[n]["summary"]
        rows.append([sn(n), pc(s["annualized_return"], 1), pc(s["annualized_vol"], 1), f2(s["sharpe"]), pc(s["max_drawdown"], 1),
                     f"{S.get(n + '.gross_avg'):.1f}$\\times$", f"{S.get(n + '.gross_max'):.1f}$\\times$",
                     pc(S.get(n + ".liqcap"), 0) if (n + ".liqcap") in S.raw else "--", f2(ab.loc[n, "beta"]) if n in ab.index else "--"])
    refs = ["balanced_leverage", "calm_weighted", "calm_rank_tilt", "calm_rank_levered", "market_boost"]
    rows.append("MID")
    for n in refs:
        if n in runs:
            s = runs[n]["summary"]
            rows.append([sn(n), pc(s["annualized_return"], 1), pc(s["annualized_vol"], 1), f2(s["sharpe"]), pc(s["max_drawdown"], 1),
                         f"{S.get(n + '.gross_avg'):.1f}$\\times$", f"{S.get(n + '.gross_max'):.1f}$\\times$",
                         pc(S.get(n + ".liqcap"), 0) if (n + ".liqcap") in S.raw else "--", f2(ab.loc[n, "beta"]) if n in ab.index else "--"])
    write_fragment("tab_levered.tex", table("lrrrrrrrr", ["Book", "Return", "Vol", "Sharpe", "Max DD", "Avg gross", "Max gross", "ADV-capped", "Beta"],
                                            rows, size=r"\small"))
    # generated sentences about the levered rows
    eqs = eq[eq.status.isin(["validated", "research", "reference"]) & ~eq.index.str.endswith(("_legacy", "_midfill"))]
    best_ret = eqs["annualized_return"].idxmax()
    best_sh = eqs["sharpe"].idxmax()
    best_dd = eqs["max_drawdown"].idxmax()
    S.put("best_eq_ret.name", best_ret, sn(best_ret))
    S.put("best_eq_ret.val", eqs.loc[best_ret, "annualized_return"], pc(eqs.loc[best_ret, "annualized_return"]))
    S.put("best_eq_sh.name", best_sh, sn(best_sh))
    S.put("best_eq_sh.val", eqs.loc[best_sh, "sharpe"], f2(eqs.loc[best_sh, "sharpe"]))
    S.put("best_eq_dd.name", best_dd, sn(best_dd))
    S.put("best_eq_dd.val", eqs.loc[best_dd, "max_drawdown"], pc(eqs.loc[best_dd, "max_drawdown"], 1))
    # progression sharpe: balanced_leverage -> calm_weighted -> calm_rank_tilt (generated verb)
    chain = ["balanced_leverage", "calm_weighted", "calm_rank_tilt"]
    ups = all(runs[b]["summary"]["sharpe"] > runs[a]["summary"]["sharpe"] for a, b in zip(chain[:-1], chain[1:]))
    S.put("sent.calmprogress", "rises" if ups else "does not rise monotonically")
    for era_lab, k in (("A", "sharpe_1996_2001"), ("B", "sharpe_2002_2007"), ("C", "sharpe_2008_2013")):
        S.put(f"calm_vs_bl.{era_lab}.better", runs["calm_weighted"]["summary"][k] > runs["balanced_leverage"]["summary"][k],
              "better" if runs["calm_weighted"]["summary"][k] > runs["balanced_leverage"]["summary"][k] else "not better")
    n_better = sum(runs["calm_weighted"]["summary"][k] > runs["balanced_leverage"]["summary"][k]
                   for k in ("sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013"))
    S.put("calm_vs_bl.n_better", n_better, str(n_better))
    n_better2 = sum(runs["calm_rank_tilt"]["summary"][k] > runs["calm_weighted"]["summary"][k]
                    for k in ("sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013"))
    S.put("crt_vs_cw.n_better", n_better2, str(n_better2))
    # relation to the old headline
    S.put("oldhead.same", bool(np.isclose(runs["balanced_leverage_max"]["summary"]["sharpe"], runs["sector_balanced_max"]["summary"]["sharpe"])),
          "identical" if np.isclose(runs["balanced_leverage_max"]["summary"]["sharpe"], runs["sector_balanced_max"]["summary"]["sharpe"]) else "different")
    # what earlier drafts reported as the equity headline: the pre-correction run of balanced_leverage_max (its legacy twin
    # once the equity corrections are promoted; otherwise the saved run itself, which is that run)
    old = "balanced_leverage_max_legacy" if "balanced_leverage_max_legacy" in runs else "balanced_leverage_max"
    so = runs[old]["summary"]
    S.put("olddraft.eq.name", old, sn(old))
    S.put("olddraft.eq.ret", so["annualized_return"], pc(so["annualized_return"]))
    S.put("olddraft.eq.vol", so["annualized_vol"], pc(so["annualized_vol"]))
    S.put("olddraft.eq.sharpe", so["sharpe"], f2(so["sharpe"]))
    S.put("olddraft.eq.dd", so["max_drawdown"], pc(so["max_drawdown"], 1))
    S.put("sent.filter_vs_sizing",
          "do not beat" if max(runs["volatility_filter"]["summary"]["sharpe"], runs["calm_weighted_filter"]["summary"]["sharpe"]) <= runs["calm_weighted"]["summary"]["sharpe"] else "beat")
    # vs cap: realized gross exceeds the nominal cap on carry-over positions
    for n in [x for x in ("balanced_leverage", "calm_weighted", "calm_rank_tilt", "balanced_leverage_max") for x in (x, x + "_legacy") if x in runs]:
        cap = runs[n]["params"]["max_gross_leverage"]
        d = ctx["daily"][n]
        g = (d["gross_exposure"] / d["nav"])[d["gross_exposure"] > 0]
        S.put(f"{n}.days_over_cap", float((g > cap + 1e-9).mean()), pc(float((g > cap + 1e-9).mean()), 1))
        S.put(f"{n}.max_over_cap", float(g.max() / cap), f"{g.max() / cap:.2f}")

    # ---- alpha vs the market table ----
    rows = []
    for n in ["calm_rank_tilt", "calm_weighted", "balanced_leverage", "conviction_tilt", "sector_balanced", "extreme_picks", "size_balanced", "graded_bets",
              "market_exposed", "market_boost", "calm_rank_levered", "balanced_leverage_max", "market_boost_max"]:
        if n in ab.index:
            r = ab.loc[n]
            rows.append([sn(n), pc(r["ann_return"], 1), pc(r["alpha"], 1), f"{r['alpha_t']:.1f}", f2(r["beta"]),
                         pc(r["alpha_size"], 1), f"{r['alpha_size_t']:.1f}", f2(r["info_ratio"]),
                         pc(r["a_96_01"], 1), pc(r["a_02_07"], 1), pc(r["a_08_13"], 1)])
    write_fragment("tab_alpha.tex", table("lrrrrrrrrrr", ["Book", "Return", "Alpha", "$t$", "Beta", "Alpha (+size)", "$t$", "IR", "96--01", "02--07", "08--13"],
                                          rows, size=r"\small"))
    # decay of alpha across eras (CRSP-market alpha), generated sentence
    nm = [n for n in ab.index if not n.startswith(("market_boost", "market_exposed")) and not n.endswith(("_legacy", "_midfill"))]
    frac_fade = float((ab.loc[nm, "a_08_13"] < ab.loc[nm, "a_96_01"]).mean())
    S.put("alpha.fade.frac", frac_fade, pc(frac_fade, 0))
    S.put("alpha.ratio.bl", float(ab.loc["balanced_leverage", "a_96_01"] / ab.loc["balanced_leverage", "a_08_13"]),
          f"{ab.loc['balanced_leverage', 'a_96_01'] / ab.loc['balanced_leverage', 'a_08_13']:.1f}")

    # ---- calendar returns ----
    def cal_ret(d):
        nav = d.set_index("date")["nav"]
        out, prev = {}, nav.iloc[0]
        for y, g in nav.groupby(nav.index.year):
            out[int(y)] = g.iloc[-1] / prev - 1
            prev = g.iloc[-1]
        return out
    mk = ctx["market"].set_index("date")["vwretd"].fillna(0.0)
    my = ((1 + mk).groupby(mk.index.year).prod() - 1).to_dict()
    ce, co = cal_ret(ctx["daily"]["calm_weighted"]), cal_ret(ctx["daily"]["options_cap20"])
    chb = cal_ret(ctx["daily"]["hedged_blend"])
    ctx["cal"] = dict(eq=ce, op=co, bl=chb, mk={int(k): v for k, v in my.items()})
    yrs = list(range(1996, 2014))
    rows = []
    half = (len(yrs) + 1) // 2
    for i in range(half):
        y1, y2 = yrs[i], (yrs[i + half] if i + half < len(yrs) else None)
        def cells(y):
            if y is None:
                return ["", "", "", ""]
            o = co.get(y, None)
            oc = "--" if (o is None or abs(o) < 1e-12) else pp(o * 100)
            return [str(y), pp(ce.get(y, 0) * 100), oc, pp(my.get(y, 0) * 100)]
        rows.append(cells(y1) + cells(y2))
    write_fragment("tab_calendar.tex", table("lrrr@{\\hspace{2em}}lrrr", ["Year", "Equity", "Options", "Market"] * 2, rows, size=r"\small"))
    worst_eq = min(ce, key=lambda y: ce[y] if y in range(1996, 2014) else 9)
    S.put("cal.eq.worst.year", worst_eq, str(worst_eq))
    S.put("cal.eq.worst", ce[worst_eq], pp(ce[worst_eq] * 100))
    S.put("cal.op.in.eqworst", co.get(worst_eq, float("nan")), pp(co.get(worst_eq, float("nan")) * 100))
    S.put("cal.eq.2008", ce[2008], pp(ce[2008] * 100))
    S.put("cal.op.2008", co[2008], pp(co[2008] * 100))
    S.put("cal.n_eq_loss_years", int(sum(1 for y in yrs if ce[y] < 0)), str(sum(1 for y in yrs if ce[y] < 0)))
    S.put("cal.eq.loss_years", [y for y in yrs if ce[y] < 0], ", ".join(str(y) for y in yrs if ce[y] < 0))


# ------------------------------------------------------------------------------------------------------
def long_short_section(S, ctx):
    """Long vs short legs under two benchmarks, high-beta shorts, recipes, eras, situation drivers."""
    runs = ctx["runs"]
    tm = read_csv("profiles_timing.csv", RES).set_index("strategy")
    bl = read_csv("profiles_beta_legs.csv", RES)
    rc = read_csv("profiles_recipes.csv", RES)
    sa = read_csv("situations_all.csv", RES)
    sb = read_csv("situations_books.csv", RES).set_index("strategy")
    sc = read_csv("situations_contrasts.csv", RES)
    sd = read_csv("situations_drivers.csv", RES)
    pt = read_csv("profiles_trades.csv", RES)
    ctx.update(tm=tm, bl=bl, rc=rc, sa=sa)

    # --- by-leg under both benchmarks ---
    rows = []
    ratios = []
    for n in PROFILED:
        if n not in tm.index:
            continue
        t = tm.loc[n]
        # all-announcer benchmark: aggregate the 'Side and surprise size' rows by leg
        x = sa[(sa.strategy == n) & (sa.dimension == "Side and surprise size")].copy()
        tot_dollars = sb.loc[n, "alpha_M"] * 1e4 / sb.loc[n, "alpha_bp"] if n in sb.index else np.nan   # $M traded
        lg = x[x.situation.str.startswith("long")]
        sh = x[x.situation.str.startswith("short")]
        long_all = 1e4 * lg["alpha_M"].sum() / (lg["pct_of_dollars"].sum() * tot_dollars)
        short_all = 1e4 * sh["alpha_M"].sum() / (sh["pct_of_dollars"].sum() * tot_dollars)
        S.put(f"{n}.long_bp_size", float(t["long_bp"]), bp(t["long_bp"]))
        S.put(f"{n}.short_bp_size", float(t["short_bp"]), bp(t["short_bp"]))
        S.put(f"{n}.long_bp_all", float(long_all), bp(long_all))
        S.put(f"{n}.short_bp_all", float(short_all), bp(short_all))
        S.put(f"{n}.long_M_size", float(t["long_alpha_M"]), usd_m_raw(t["long_alpha_M"]))
        S.put(f"{n}.short_M_size", float(t["short_alpha_M"]), usd_m_raw(t["short_alpha_M"]))
        ratios.append(t["long_bp"] / t["short_bp"])
        rows.append([sn(n), bp(long_all), bp(short_all), f"{long_all / short_all:.1f}", bp(t["long_bp"]), bp(t["short_bp"]),
                     f"{t['long_bp'] / t['short_bp']:.1f}", usd_m_raw(t["long_alpha_M"]), usd_m_raw(t["short_alpha_M"])])
        S.put(f"{n}.ls_ratio_size", float(t["long_bp"] / t["short_bp"]), f"{t['long_bp'] / t['short_bp']:.1f}")
        S.put(f"{n}.ls_ratio_all", float(long_all / short_all), f"{long_all / short_all:.1f}")
    write_fragment("tab_legs.tex", table("lrrrrrrrr", ["Book", "Long", "Short", "L/S", "Long", "Short", "L/S", "Long \\$", "Short \\$"], rows, size=r"\small")
                   .replace(r"\toprule" + "\n", r"\toprule" + "\n & \\multicolumn{3}{c}{All-announcer benchmark (bp per \\$)} & \\multicolumn{3}{c}{Size-matched benchmark (bp per \\$)} & \\multicolumn{2}{c}{Size-matched alpha}\\\\\n\\cmidrule(lr){2-4}\\cmidrule(lr){5-7}\\cmidrule(lr){8-9}\n"))
    S.put("ls.ratio.min", float(min(ratios)), f"{min(ratios):.1f}")
    S.put("ls.ratio.max", float(max(ratios)), f"{max(ratios):.1f}")
    S.put("ls.ratio.median", float(np.median(ratios)), f"{np.median(ratios):.1f}")
    S.put("ls.n_long_gt_short", int(sum(r > 1 for r in ratios)), str(sum(r > 1 for r in ratios)))
    S.put("ls.n", len(ratios), str(len(ratios)))
    allr = [S.get(f"{n}.ls_ratio_all") for n in PROFILED if f"{n}.ls_ratio_all" in S.raw]
    S.put("ls.all.ratio.min", float(min(allr)), f"{min(allr):.1f}")
    S.put("ls.all.ratio.max", float(max(allr)), f"{max(allr):.1f}")
    # --- high-beta shorts ---
    hb = bl[bl.group == "high-beta shorts"].set_index("strategy")
    lm = bl[bl.group == "low/mid-beta shorts"].set_index("strategy")
    hl = bl[bl.group == "high-beta longs"].set_index("strategy")
    S.put("hbs.n", len(hb), str(len(hb)))
    S.put("hbs.n_all3neg", int((hb["eras_real_neg"] == 3).sum()), str(int((hb["eras_real_neg"] == 3).sum())))
    S.put("hbs.n_noalpha", int((hb["t"].abs() < 2).sum()), str(int((hb["t"].abs() < 2).sum())))
    S.put("hbs.bp.min", float(hb["alpha_bp"].min()), bp(hb["alpha_bp"].min()))
    S.put("hbs.bp.max", float(hb["alpha_bp"].max()), bp(hb["alpha_bp"].max()))
    S.put("hbs.real.min", float(hb["real_M"].min()), usd_m_raw(hb["real_M"].min()))
    S.put("hbs.real.max", float(hb["real_M"].max()), usd_m_raw(hb["real_M"].max()))
    S.put("hbs.money.min", float(hb["money"].min()), f"{hb['money'].min():.0f}\\%")
    S.put("hbs.money.max", float(hb["money"].max()), f"{hb['money'].max():.0f}\\%")
    S.put("lms.bp.min", float(lm["alpha_bp"].min()), bp(lm["alpha_bp"].min()))
    S.put("lms.bp.max", float(lm["alpha_bp"].max()), bp(lm["alpha_bp"].max()))
    S.put("lms.t.min", float(lm["t"].min()), f"{lm['t'].min():.1f}")
    S.put("lms.t.max", float(lm["t"].max()), f"{lm['t'].max():.1f}")
    S.put("lms.n_pos_t2", int((lm["t"] >= 2).sum()), str(int((lm["t"] >= 2).sum())))
    S.put("lms.real.neg", int((lm["real_M"] < 0).sum()), str(int((lm["real_M"] < 0).sum())))
    S.put("hbl.bp.min", float(hl["alpha_bp"].min()), bp(hl["alpha_bp"].min()))
    S.put("hbl.bp.max", float(hl["alpha_bp"].max()), bp(hl["alpha_bp"].max()))
    S.put("hbl.real.min", float(hl["real_M"].min()), usd_m_raw(hl["real_M"].min()))
    S.put("hbl.real.max", float(hl["real_M"].max()), usd_m_raw(hl["real_M"].max()))
    rows = []
    for n in PROFILED:
        if n in hb.index:
            rows.append([sn(n), bp(hb.loc[n, "alpha_bp"]), f"{hb.loc[n, 't']:.1f}".replace("-", "$-$"), usd_m_raw(hb.loc[n, "real_M"]), f"{int(hb.loc[n, 'eras_real_neg'])}/3",
                         bp(lm.loc[n, "alpha_bp"]), f"{lm.loc[n, 't']:.1f}", usd_m_raw(lm.loc[n, "real_M"]),
                         bp(hl.loc[n, "alpha_bp"]), f"{hl.loc[n, 't']:.1f}", usd_m_raw(hl.loc[n, "real_M"])])
    write_fragment("tab_beta_legs.tex", table("lrrrrrrrrrr", ["Book", "bp", "$t$", "Real \\$", "Losing eras", "bp", "$t$", "Real \\$", "bp", "$t$", "Real \\$"], rows, size=r"\small")
                   .replace(r"\toprule" + "\n", r"\toprule" + "\n & \\multicolumn{4}{c}{High-beta shorts} & \\multicolumn{3}{c}{Low/mid-beta shorts} & \\multicolumn{3}{c}{High-beta longs}\\\\\n\\cmidrule(lr){2-5}\\cmidrule(lr){6-8}\\cmidrule(lr){9-11}\n"))
    # --- recipes: best recipe per book ---
    BEST = "long | thin | low-beta | long hold (>60d)"
    best_rows, n_best_is_recipe = [], 0
    for n in PROFILED:
        r = rc[rc.strategy == n]
        top = r.sort_values("alpha_M", ascending=False).iloc[0]
        is_best = top["recipe"] == BEST
        n_best_is_recipe += int(is_best)
        b = r[r.recipe == BEST].iloc[0]
        S.put(f"{n}.best.money", float(b["pct_dollars"]), pc(b["pct_dollars"], 1))
        S.put(f"{n}.best.alpha_share", float(b["pct_alpha"]), pc(b["pct_alpha"], 1))
        S.put(f"{n}.best.bp", float(b["bp"]), bp(b["bp"]))
        S.put(f"{n}.best.t", float(b["t"]), f"{b['t']:.1f}")
        S.put(f"{n}.best.qpos", float(b["pct_q_pos"]), pc(b["pct_q_pos"], 0))
        S.put(f"{n}.best.eras_neg", int(b["eras_neg"]), str(int(b["eras_neg"])))
        S.put(f"{n}.best.net", float(b["net_M"]), usd_m_raw(b["net_M"]))
        best_rows.append([sn(n), pc(b["pct_dollars"], 1), pc(b["pct_alpha"], 1), bp(b["bp"]), f"{b['t']:.1f}", pc(b["pct_q_pos"], 0),
                          f"{3 - int(b['eras_neg'])}/3", usd_m_raw(b["net_M"]), "yes" if is_best else "no"])
    write_fragment("tab_best_recipe.tex", table("lrrrrrrrr", ["Book", "Money", "Alpha share", "bp", "$t$", "Quarters +", "Eras +", "Real \\$", "Top recipe"], best_rows, size=r"\small"))
    S.put("best.n_top", n_best_is_recipe, str(n_best_is_recipe))
    bb = pd.DataFrame([{k: S.get(f"{n}.best.{k}") for k in ("money", "alpha_share", "bp", "t", "qpos")} | {"net": S.get(f"{n}.best.net")} for n in PROFILED])
    S.put("best.money.min", float(bb["money"].min()), pc(bb["money"].min(), 0)); S.put("best.money.max", float(bb["money"].max()), pc(bb["money"].max(), 0))
    S.put("best.share.min", float(bb["alpha_share"].min()), pc(bb["alpha_share"].min(), 0)); S.put("best.share.max", float(bb["alpha_share"].max()), pc(bb["alpha_share"].max(), 0))
    S.put("best.bp.min", float(bb["bp"].min()), bp(bb["bp"].min())); S.put("best.bp.max", float(bb["bp"].max()), bp(bb["bp"].max()))
    S.put("best.qpos.min", float(bb["qpos"].min()), pc(bb["qpos"].min(), 0)); S.put("best.qpos.max", float(bb["qpos"].max()), pc(bb["qpos"].max(), 0))
    S.put("best.net.min", float(bb["net"].min()), usd_m_raw(bb["net"].min())); S.put("best.net.max", float(bb["net"].max()), usd_m_raw(bb["net"].max()))
    neg_books = [n for n in PROFILED if S.get(f"{n}.best.eras_neg") > 0]
    S.put("best.n_all3", len(PROFILED) - len(neg_books), str(len(PROFILED) - len(neg_books)))
    S.put("best.neg_books", neg_books, ", ".join(sn(n) for n in neg_books) if neg_books else "none")
    # recipe lose counts
    cnt = []
    for n in PROFILED:
        r = rc[rc.strategy == n]
        lose = r[r.bp < 0]
        cnt.append((n, len(lose), lose["pct_dollars"].sum()))
    S.put("recipes.n", int(rc.groupby("strategy").size().iloc[0]), str(int(rc.groupby("strategy").size().iloc[0])))
    S.put("recipes.lose.first", cnt[0][1], str(cnt[0][1]))
    S.put("recipes.lose.first.money", float(cnt[0][2]), pc(cnt[0][2], 0))
    S.put("recipes.lose.last", [c for c in cnt if c[0] == "calm_rank_tilt"][0][1], str([c for c in cnt if c[0] == "calm_rank_tilt"][0][1]))
    S.put("recipes.lose.last.money", float([c for c in cnt if c[0] == "calm_rank_tilt"][0][2]), pc([c for c in cnt if c[0] == "calm_rank_tilt"][0][2], 0))
    unproven = []
    for n in PROFILED:
        r = rc[rc.strategy == n]
        unproven.append((r[r.t.abs() < 2]["pct_dollars"].sum(), r[r.t.abs() < 2]["pct_alpha"].sum()))
    S.put("recipes.unproven.money.min", float(min(u[0] for u in unproven)), pc(min(u[0] for u in unproven), 0))
    S.put("recipes.unproven.money.max", float(max(u[0] for u in unproven)), pc(max(u[0] for u in unproven), 0))
    S.put("recipes.unproven.alpha.min", float(min(u[1] for u in unproven)), pc(min(u[1] for u in unproven), 0))
    S.put("recipes.unproven.alpha.max", float(max(u[1] for u in unproven)), pc(max(u[1] for u in unproven), 0))
    # timing / tails
    S.put("worst_q.n2009Q1", int((tm.loc[PROFILED, "worst_q"] == "2009Q1").sum()), str(int((tm.loc[PROFILED, "worst_q"] == "2009Q1").sum())))
    S.put("neg_years.only2009", int((tm.loc[PROFILED, "neg_years"].astype(str) == "2009").sum()), str(int((tm.loc[PROFILED, "neg_years"].astype(str) == "2009").sum())))
    S.put("cost_share.min", float(tm.loc[PROFILED, "cost_pct_of_excess"].min()), pc(tm.loc[PROFILED, "cost_pct_of_excess"].min(), 0))
    S.put("cost_share.max", float(tm.loc[PROFILED, "cost_pct_of_excess"].max()), pc(tm.loc[PROFILED, "cost_pct_of_excess"].max(), 0))
    S.put("qpos.min", float(tm.loc[PROFILED, "pct_quarters_pos"].min()), pc(tm.loc[PROFILED, "pct_quarters_pos"].min(), 0))
    S.put("qpos.max", float(tm.loc[PROFILED, "pct_quarters_pos"].max()), pc(tm.loc[PROFILED, "pct_quarters_pos"].max(), 0))
    S.put("best10.min", float(tm.loc[PROFILED, "best10_share"].min()), pc(tm.loc[PROFILED, "best10_share"].min(), 0))
    S.put("best10.max", float(tm.loc[PROFILED, "best10_share"].max()), pc(tm.loc[PROFILED, "best10_share"].max(), 0))
    S.put("top20.min", float(tm.loc[PROFILED, "top_name_pct_alpha"].min()), pc(tm.loc[PROFILED, "top_name_pct_alpha"].min(), 0))
    S.put("top20.max", float(tm.loc[PROFILED, "top_name_pct_alpha"].max()), pc(tm.loc[PROFILED, "top_name_pct_alpha"].max(), 0))
    t1 = pt[pt.group == "top1%"].set_index("strategy")["share_of_total_alpha"]
    b1 = pt[pt.group == "bottom1%"].set_index("strategy")["share_of_total_alpha"]
    wr = pt[pt.group == "all"].set_index("strategy")["win_rate"]
    S.put("tail.top.min", float(t1.loc[PROFILED].min()), f"{t1.loc[PROFILED].min():.1f}")
    S.put("tail.top.max", float(t1.loc[PROFILED].max()), f"{t1.loc[PROFILED].max():.1f}")
    S.put("tail.bot.min", float(-b1.loc[PROFILED].max()), f"{-b1.loc[PROFILED].max():.1f}")
    S.put("tail.bot.max", float(-b1.loc[PROFILED].min()), f"{-b1.loc[PROFILED].min():.1f}")
    S.put("win.min", float(wr.loc[PROFILED].min()), pc(wr.loc[PROFILED].min(), 0))
    S.put("win.max", float(wr.loc[PROFILED].max()), pc(wr.loc[PROFILED].max(), 0))
    # --- situations for balanced_leverage (the reference) ---
    def srow(dim, sit, n="balanced_leverage"):
        x = sa[(sa.strategy == n) & (sa.dimension == dim) & (sa.situation == sit)]
        return x.iloc[0] if len(x) else None
    def contrast(label, n="balanced_leverage"):
        x = sc[(sc.strategy == n) & (sc.dimension == label)]
        return x.iloc[0] if len(x) else None
    ctx["srow"], ctx["contrast"] = srow, contrast
    dims = sa[sa.strategy == "balanced_leverage"]["dimension"].unique().tolist()
    rows = []
    wanted = [("Company size", None), ("How easily traded", None), ("Stock's market sensitivity", None), ("Hold length", None), ("Period", None)]
    for dim, _ in wanted:
        x = sa[(sa.strategy == "balanced_leverage") & (sa.dimension == dim)]
        for _, r in x.iterrows():
            rows.append([tx(dim), tx(str(r["situation"]).replace("-", "--")), pc(r["pct_of_dollars"], 0), pc(r["pct_of_alpha"], 0), bp(r["alpha_bp_per_dollar"]),
                         f2(r["t_stat"], 1), tx(r["verdict"])])
        rows.append("MID")
    rows = rows[:-1]
    write_fragment("tab_situations.tex", table("llrrrrl", ["Cut", "Situation", "Money", "Alpha", "bp/\\$", "$t$", "Verdict"], rows, size=r"\footnotesize"))
    c_thin = contrast("Thin vs heavily traded"); c_small = contrast("Small vs large companies"); c_hold = contrast("Long holds vs short holds")
    c_era = contrast("Early vs late period"); c_calm = contrast("Calm vs jumpy stocks"); c_mkt = contrast("Market rose vs fell during hold")
    for tag, c in (("thin", c_thin), ("small", c_small), ("hold", c_hold), ("era", c_era), ("calm", c_calm), ("mkt", c_mkt)):
        S.put(f"sit.{tag}.a", float(c["a_bp"]), bp(c["a_bp"])); S.put(f"sit.{tag}.b", float(c["b_bp"]), bp(c["b_bp"]))
        S.put(f"sit.{tag}.diff", float(c["diff_bp"]), bp(c["diff_bp"])); S.put(f"sit.{tag}.t", float(c["t_stat"]), f"{c['t_stat']:.1f}")
    drv = sd[sd.strategy == "balanced_leverage"].set_index("driver")
    for key, lab in (("beta", "Higher beta"), ("hold", "Longer hold window"), ("vol", "Jumpier stock (stock-specific vol)"), ("thin", "Thinner trading"), ("small", "Smaller company")):
        S.put(f"drv.{key}.bp", float(drv.loc[lab, "bp_per_1sd"]), bp(drv.loc[lab, "bp_per_1sd"]))
        S.put(f"drv.{key}.t", float(drv.loc[lab, "t_stat"]), f"{drv.loc[lab, 't_stat']:.1f}".replace("-", "$-$"))
    # period shares and alpha per era from the situation table
    pr = sa[(sa.strategy == "balanced_leverage") & (sa.dimension == "Period")].set_index("situation")
    for k, lab in (("A", "1996-01"), ("B", "2002-07"), ("C", "2008-13")):
        S.put(f"era{k}.bp", float(pr.loc[lab, "alpha_bp_per_dollar"]), bp(pr.loc[lab, "alpha_bp_per_dollar"]))
        S.put(f"era{k}.money", float(pr.loc[lab, "pct_of_dollars"]), pc(pr.loc[lab, "pct_of_dollars"], 0))
        S.put(f"era{k}.alpha", float(pr.loc[lab, "pct_of_alpha"]), pc(pr.loc[lab, "pct_of_alpha"], 0))
    # era bp across all profiled strategies (for the decay figure)
    erarows = []
    for n in PROFILED:
        x = sa[(sa.strategy == n) & (sa.dimension == "Period")].set_index("situation")
        erarows.append([n] + [float(x.loc[lab, "alpha_bp_per_dollar"]) for lab in ("1996-01", "2002-07", "2008-13")])
    ctx["era_bp"] = pd.DataFrame(erarows, columns=["name", "A", "B", "C"]).set_index("name")
    S.put("era.all.fade.n", int((ctx["era_bp"]["C"] < ctx["era_bp"]["A"]).sum()), str(int((ctx["era_bp"]["C"] < ctx["era_bp"]["A"]).sum())))
    S.put("era.all.ratio.min", float((ctx["era_bp"]["A"] / ctx["era_bp"]["C"]).min()), f"{(ctx['era_bp']['A'] / ctx['era_bp']['C']).min():.1f}")
    S.put("era.all.ratio.max", float((ctx["era_bp"]["A"] / ctx["era_bp"]["C"]).max()), f"{(ctx['era_bp']['A'] / ctx['era_bp']['C']).max():.1f}")
    # verdict counts
    v = sa[(sa.strategy == "balanced_leverage")]["verdict"].value_counts().to_dict()
    S.put("sit.n", int(len(sa[sa.strategy == "balanced_leverage"])), str(int(len(sa[sa.strategy == "balanced_leverage"]))))
    S.put("sit.n_engine", int(v.get("ENGINE", 0)), str(int(v.get("ENGINE", 0))))
    # sleeve market-mood (market direction does not matter): quarterly alpha vs market return
    S.put("hist.leaks", int(sa[(sa.strategy == "balanced_leverage") & (sa.verdict == "LEAKS")].shape[0]), str(int(sa[(sa.strategy == "balanced_leverage") & (sa.verdict == "LEAKS")].shape[0])))


# ------------------------------------------------------------------------------------------------------
def beta_vol_section(S, ctx):
    bv = ctx["ext"].get("beta_vol")
    if not bv:
        return
    S.put("bv.source", ctx["ext"]["source"], tx(ctx["ext"]["source"]))
    stale = ctx["ext"]["source"] == "cache-stale"
    S.put("bv.stale_note", stale, r"\textbf{[These beta$\times$volatility numbers are cached from an earlier run and are stale.]}" if stale else "")
    for tag in ("size", "all"):
        cells = bv[tag]["cells"]
        rows = []
        for b in ("low", "mid", "high"):
            r = [f"{b.capitalize()}-beta"]
            for v in ("calm", "mid", "jumpy"):
                c = cells[f"{b}|{v}"]
                r.append(f"{bp(c['bp'])} ({c['t']:.1f})".replace("(-", "($-$"))
            m = bv[tag]["marginals"][f"beta|{b}"]
            r.append(f"{bp(m['bp'])} ({m['t']:.1f})".replace("(-", "($-$"))
            rows.append(r)
        rows.append("MID")
        r = ["All betas"]
        for v in ("calm", "mid", "jumpy"):
            m = bv[tag]["marginals"][f"vol|{v}"]
            r.append(f"{bp(m['bp'])} ({m['t']:.1f})".replace("(-", "($-$"))
        r.append("")
        rows.append(r)
        write_fragment(f"tab_betavol_{tag}.tex", table("lrrrr", ["", "Calm", "Mid-vol", "Jumpy", "All volatilities"], rows, size=r"\small"))
        for key, c in bv[tag]["contrasts"].items():
            k = key.replace("|", ".").replace("=", ".").replace("-", "_")
            S.put(f"bv.{tag}.{k}.bp", c["bp"], bp(c["bp"]))
            S.put(f"bv.{tag}.{k}.t", c["t"], f"{c['t']:.1f}".replace("-", "$-$"))
        for key, c in bv[tag]["cells"].items():
            S.put(f"bv.{tag}.cell.{key.replace('|', '_')}.bp", c["bp"], bp(c["bp"]))
            S.put(f"bv.{tag}.cell.{key.replace('|', '_')}.money", c["pct_dollars"], pc(c["pct_dollars"], 0))
        for key, c in bv[tag]["marginals"].items():
            S.put(f"bv.{tag}.marg.{key.replace('|', '_')}.bp", c["bp"], bp(c["bp"]))
    ov = bv["overlap"]
    S.put("bv.calm_lowbeta", ov["calm_low_beta_share"], pc(ov["calm_low_beta_share"], 0))
    S.put("bv.jumpy_highbeta", ov["jumpy_high_beta_share"], pc(ov["jumpy_high_beta_share"], 0))
    S.put("bv.corr", ov["corr_beta_ivolpct"], f"{ov['corr_beta_ivolpct']:.2f}")
    # the generated verdict: which margin is larger once the other is held fixed? (size-matched benchmark)
    c = bv["size"]["contrasts"]
    cj = np.mean([c[f"calm-jumpy|beta={b}"]["bp"] for b in ("low", "mid", "high")])
    lh = np.mean([c[f"low-high|vol={v}"]["bp"] for v in ("calm", "mid", "jumpy")])
    S.put("bv.avg_calm_jumpy", float(cj), bp(cj))
    S.put("bv.avg_low_high", float(lh), bp(lh))
    S.put("sent.bv", ("Holding beta fixed, the calm-minus-jumpy gap shrinks to an average of %s bp per dollar, while holding volatility fixed the low-minus-high-beta gap remains %s bp"
                      % (bp(cj), bp(lh))))
    S.put("sent.bv.larger", "beta" if abs(lh) > abs(cj) else "volatility")
    if abs(lh) > abs(cj):
        plain = ("In plain terms, the calm edge is mostly a low-beta edge: most of the apparent gain from sizing by volatility is a gain from tilting "
                 "toward low-beta stocks, which calm stocks tend to be. It means the Sharpe gain of the volatility-sized books should not be attributed to "
                 "volatility itself, and that a direct beta rule is the more natural next candidate.")
    else:
        plain = ("In plain terms, volatility carries more of the edge than beta in this cut, so the calm edge is not simply a low-beta edge.")
    S.put("sent.bv.plain", plain)


# ------------------------------------------------------------------------------------------------------
def options_section(S, ctx):
    runs = ctx["runs"]
    ext = ctx["ext"]
    # options table
    rows = []
    order = [("options_cap20", "bid/ask fills, point-in-time deciles (current)"), ("options_cap30", "bid/ask fills, point-in-time deciles (current)"),
             ("options_worst_case", "bid/ask fills, dead options worth zero (current)"),
             "MID", ("options_cap20_midfill", "mid fills + tick floor, point-in-time deciles"), ("options_cap30_midfill", "mid fills + tick floor, point-in-time deciles"),
             ("options_cap20_prefix_fix", "mid fills + tick floor, within-quarter deciles (reported before the fixes)"),
             ("options_cap30_prefix_fix", "mid fills + tick floor, within-quarter deciles (reported before the fixes)"),
             ("options_worst_case_prefix_fix", "as above, dead options worth zero"),
             "MID", ("options_cap20_legacy", "original convention: carry-forward, flat 3\\%"), ("options_cap30_legacy", "original convention"),
             ("options_cap40_legacy", "original convention")]
    for it in order:
        if it == "MID":
            rows.append("MID"); continue
        n, lab = it
        if n not in runs:
            continue
        s = runs[n]["summary"]
        rows.append([sn(n), lab, pc(s["annualized_return"]), pc(s["annualized_vol"]), f2(s["sharpe"]), pc(s["max_drawdown"], 1)])
    write_fragment("tab_options.tex", table("llrrrr", ["Book", "Fills and deciles", "Return", "Vol", "Sharpe", "Max DD"], rows, size=r"\small"))
    s20 = runs["options_cap20"]["summary"]
    S.put("opt.nask", s20.get("n_entries_filled_at_ask", 0), intc(s20.get("n_entries_filled_at_ask", 0)))
    if "total_net_M" in ext:
        S.put("ot.net_M", ext["total_net_M"], usd_m_raw(ext["total_net_M"]))
        S.put("ot.prem_M", ext["total_premium_M"], usd_m_raw(ext["total_premium_M"], 0))
        S.put("ot.net_over_prem", ext["total_net_M"] / ext["total_premium_M"], pc(ext["total_net_M"] / ext["total_premium_M"], 2))
    # the audit chain from the strategy's own docstring
    doc = runs["options_cap20"].get("module_doc", "")
    mm = re.search(r"([\d.]+)% / Sharpe ([\d.]+) as originally reported\s+->\s+([\d.]+)% / ([\d.]+) with\s+the corrected mark\s+->\s+([\d.]+)% / ([\d.]+) once each contract also pays.*?->\s+([\d.]+)% / ([\d.]+) if dead", doc, re.S)
    if mm:
        a, b, c, d, e, f, g, h = mm.groups()
        S.put("optchain.legacy.ret", float(a), f"{a}\\%"); S.put("optchain.legacy.sh", float(b), b)
        S.put("optchain.mark.ret", float(c), f"{c}\\%"); S.put("optchain.mark.sh", float(d), d)
        S.put("optchain.floor.ret", float(e), f"{e}\\%"); S.put("optchain.floor.sh", float(f), f)
        S.put("optchain.zero.ret", float(g), f"{g}\\%"); S.put("optchain.zero.sh", float(h), h)
        # they must agree with the saved runs
        chk = [(float(a) / 100, runs["options_cap20_legacy"]["summary"]["annualized_return"]),
               (float(e) / 100, runs["options_cap20_prefix_fix"]["summary"]["annualized_return"]),
               (float(g) / 100, runs["options_worst_case_prefix_fix"]["summary"]["annualized_return"])]
        S.put("optchain.consistent", all(abs(x - y) < 6e-4 for x, y in chk), "")
        S.put("optchain.ok", True, "")
    else:
        S.put("optchain.ok", False, "")
    d20, d20l = ctx["daily"]["options_cap20"], ctx["daily"]["options_cap20_legacy"]
    # call/put split of P&L
    for n in ("options_cap20", "options_cap30", "options_cap20_legacy"):
        d = ctx["daily"][n]
        S.put(f"{n}.call_pnl_share", float(d["pnl_call"].sum() / (d["pnl_call"].sum() + d["pnl_put"].sum())),
              pc(d["pnl_call"].sum() / (d["pnl_call"].sum() + d["pnl_put"].sum()), 1))
        S.put(f"{n}.pct_calls", runs[n]["summary"]["pct_calls"], pc(runs[n]["summary"]["pct_calls"], 1))
    for n in ("options_cap20", "options_cap30", "options_worst_case", "options_cap20_legacy"):
        s = runs[n]["summary"]
        S.put(f"{n}.nsized", s["n_positions_sized"], intc(s["n_positions_sized"]))
        S.put(f"{n}.nelig", s["n_positions_eligible"], intc(s["n_positions_eligible"]))
        S.put(f"{n}.maxprem", s["max_outstanding_premium_frac"], pc(s["max_outstanding_premium_frac"], 1))
        S.put(f"{n}.costs", s["total_transaction_costs"], usd_m(s["total_transaction_costs"]))
        S.put(f"{n}.premium", s["total_premium_paid"], usd_m(s["total_premium_paid"]))
        S.put(f"{n}.cost_pct", s["assumed_roundtrip_cost_pct"], pc(s["assumed_roundtrip_cost_pct"], 0))
        S.put(f"{n}.minshare", s.get("half_kelly_multiplier", 0), f"{s.get('half_kelly_multiplier', 0):g}")
    S.put("opt.floor", runs["options_cap20"]["params"]["min_roundtrip_cost_per_share"], f"\\${runs['options_cap20']['params']['min_roundtrip_cost_per_share']:.2f}")
    S.put("opt.cost_pct", runs["options_cap20"]["params"]["roundtrip_cost_pct"], pc(runs["options_cap20"]["params"]["roundtrip_cost_pct"], 0))
    S.put("opt.halfkelly", runs["options_cap20"]["params"]["half_kelly"], f"{runs['options_cap20']['params']['half_kelly']:g}")
    S.put("opt.horizon", runs["options_cap20"]["params"]["hold_horizon"], str(runs["options_cap20"]["params"]["hold_horizon"]))
    S.put("opt.minprem", runs["options_cap20"]["params"]["min_premium"], f"{runs['options_cap20']['params']['min_premium']:g}")
    # kelly grid and states from code
    try:
        from pead.core.kelly import F_GRID
        S.put("kelly.fmin", float(F_GRID.min()), f"{F_GRID.min():.2f}")
        S.put("kelly.fmax", float(F_GRID.max()), f"{F_GRID.max():.2f}")
        S.put("kelly.nf", len(F_GRID), str(len(F_GRID)))
    except Exception:
        pass
    rd = (ROOT / "pead" / "prep" / "options" / "return_distributions.py").read_text(encoding="utf-8")
    for nm, key in (("N_STATES_DEFAULT", "kelly.states"), ("MIN_WARMUP_QUARTERS", "kelly.warmup")):
        m_ = re.search(rf"^{nm}\s*=\s*(\d+)", rd, re.M)
        S.put(key, int(m_.group(1)) if m_ else -1, m_.group(1) if m_ else "--")
    # facts the options strategy's own docstring records about the quote error
    mq = re.search(r"(\d+)% of picks have no quote at 60 days, and (\d+)% of those", doc)
    S.put("opt.missing60", int(mq.group(1)) if mq else -1, f"{mq.group(1)}\\%" if mq else "--")
    S.put("opt.zerobid", int(mq.group(2)) if mq else -1, f"{mq.group(2)}\\%" if mq else "--")
    # the sizing rule stated in the *_max module docstring
    mr = re.search(r"Sharpe >= ([\d.]+) and max\s+drawdown >= -(\d+)%", runs["balanced_leverage_max"].get("module_doc", ""))
    S.put("rule.sharpe", float(mr.group(1)) if mr else float("nan"), mr.group(1) if mr else "--")
    S.put("rule.dd", float(mr.group(2)) / 100 if mr else float("nan"), f"{mr.group(2)}\\%" if mr else "--")
    # worst days of the corrected options book
    dd_ = ctx["daily"]["options_cap20"].set_index("date")["daily_return"].nsmallest(3)
    S.put("opt.worstdays", [str(i.date()) for i in dd_.index], ", ".join(str(i.date()) for i in sorted(dd_.index)))
    S.put("opt.worstday.min", float(dd_.min()), pc(dd_.min(), 1))
    try:
        from pead.engines.options import CHECKPOINTS, CONTRACT_MULTIPLIER
        S.put("opt.checkpoints", CHECKPOINTS, ", ".join(str(c) for c in CHECKPOINTS))
        S.put("opt.mult", CONTRACT_MULTIPLIER, str(CONTRACT_MULTIPLIER))
    except Exception:
        pass
    # corrections: effect sizes
    for a, b in (("options_cap20", "options_cap20_legacy"), ("options_cap30", "options_cap30_legacy")):
        ra, rb = runs[a]["summary"], runs[b]["summary"]
        S.put(f"{a}.over_ret", rb["annualized_return"] / ra["annualized_return"], f"{rb['annualized_return'] / ra['annualized_return']:.1f}")
        S.put(f"{a}.over_sh", rb["sharpe"] / ra["sharpe"], f"{rb['sharpe'] / ra['sharpe']:.1f}")
    # trade-level facts (cached external)
    ot = ext.get("n_trades") is not None
    if ot:
        S.put("ot.source", ext["source"], tx(ext["source"]))
        S.put("ot.n", ext["n_trades"], intc(ext["n_trades"]))
        S.put("ot.pct_calls", ext["pct_calls"], pc(ext["pct_calls"], 1))
        S.put("ot.pct_pnl_calls", ext["pct_gross_pnl_calls"], pc(ext["pct_gross_pnl_calls"], 1))
        S.put("ot.win", ext["win_rate"], pc(ext["win_rate"], 0))
        S.put("ot.median", ext["median_ret_on_premium"], pc(ext["median_ret_on_premium"], 0))
        S.put("ot.mean_w", ext["mean_ret_on_premium_w"], pc(ext["mean_ret_on_premium_w"], 0))
        S.put("ot.top1", ext["top1pct_share_of_net"], pc(ext["top1pct_share_of_net"], 0))
        S.put("ot.entry_prem", ext["median_entry_premium"], f"\\${ext['median_entry_premium']:.2f}")
        ys = ext["net_pnl_share_by_year"]
        top2 = sorted(ys.items(), key=lambda kv: -kv[1])[:2]
        S.put("ot.top2year", sum(v for _, v in top2), pc(sum(v for _, v in top2), 0))
        S.put("ot.top2year.names", [k for k, _ in top2], " and ".join(str(k) for k, _ in sorted(top2)))
        losers = [k for k, v in ys.items() if v < 0]
        S.put("ot.loss_years", losers, ", ".join(str(k) for k in sorted(losers)) if losers else "none")
        ps = ext["put_share_by_decile"]
        S.put("ot.put.d1", ps["1"], pc(ps["1"], 1)); S.put("ot.put.d10", ps["10"], pc(ps["10"], 1))
        ctx["put_share"] = {int(k): v for k, v in ps.items()}
        ctx["opt_year_share"] = {int(k): v for k, v in ys.items()}


# ------------------------------------------------------------------------------------------------------
def blend_section(S, ctx):
    runs = ctx["runs"]
    cors = {}
    for n in ("hedged_blend", "calm_blend", "legacy_blend"):
        s = runs[n]["summary"]
        S.put(f"{n}.beta_mkt", s.get("market_beta", float("nan")), f"{s.get('market_beta', float('nan')):.3f}")
        S.put(f"{n}.corr_mkt", s.get("corr_market", float("nan")), f"{s.get('corr_market', float('nan')):.2f}")
        S.put(f"{n}.sleeve_corr", s.get("sleeve_correlation", float("nan")), f2(s.get("sleeve_correlation", float("nan")), 3))
        S.put(f"{n}.w", runs[n].get("params", {}), "")
        for k, v in runs[n].get("params", {}).items():
            S.put(f"{n}.{k}", v, f"{v:g}")
    # sleeve and market correlations from the daily returns
    r_e = ctx["daily"]["calm_weighted"].set_index("date")["daily_return"]
    r_o = ctx["daily"]["options_cap20"].set_index("date")["daily_return"]
    mk = ctx["market"].set_index("date")["vwretd"]
    J = pd.concat([r_e.rename("e"), r_o.rename("o"), mk.rename("m")], axis=1, join="inner").dropna()
    J = J.loc[(J.o != 0).idxmax():]
    S.put("corr.eo", float(J.e.corr(J.o)), f2(J.e.corr(J.o), 2))
    S.put("corr.em", float(J.e.corr(J.m)), f2(J.e.corr(J.m), 2))
    S.put("corr.om", float(J.o.corr(J.m)), f2(J.o.corr(J.m), 2))
    mo = pd.concat([ctx["daily"]["calm_weighted"].set_index("date")["nav"].resample("ME").last().pct_change(),
                    ctx["daily"]["options_cap20"].set_index("date")["nav"].resample("ME").last().pct_change()], axis=1, keys=["e", "o"]).dropna()
    mo = mo[mo.index >= J.index[0]]
    S.put("corr.eo.monthly", float(mo.e.corr(mo.o)), f2(mo.e.corr(mo.o), 2))
    S.put("corr.eo.months", len(mo), str(len(mo)))
    # blend table
    def blend_row(n, role):
        s = runs[n]["summary"]
        return [sn(n), role, pc(s["annualized_return"]), pc(s["annualized_vol"]), f2(s["sharpe"]), pc(s["max_drawdown"], 1),
                f2(s["sharpe_1996_2001"]), f2(s["sharpe_2002_2007"]), f2(s["sharpe_2008_2013"])]
    rows = [[sn("calm_weighted"), "equity sleeve", pc(runs["calm_weighted"]["summary"]["annualized_return"]), pc(runs["calm_weighted"]["summary"]["annualized_vol"]),
             f2(runs["calm_weighted"]["summary"]["sharpe"]), pc(runs["calm_weighted"]["summary"]["max_drawdown"], 1),
             f2(runs["calm_weighted"]["summary"]["sharpe_1996_2001"]), f2(runs["calm_weighted"]["summary"]["sharpe_2002_2007"]), f2(runs["calm_weighted"]["summary"]["sharpe_2008_2013"])],
            [sn("options_cap20"), "options sleeve", pc(runs["options_cap20"]["summary"]["annualized_return"]), pc(runs["options_cap20"]["summary"]["annualized_vol"]),
             f2(runs["options_cap20"]["summary"]["sharpe"]), pc(runs["options_cap20"]["summary"]["max_drawdown"], 1),
             f2(runs["options_cap20"]["summary"]["sharpe_1996_2001"]), f2(runs["options_cap20"]["summary"]["sharpe_2002_2007"]), f2(runs["options_cap20"]["summary"]["sharpe_2008_2013"])],
            "MID", blend_row("hedged_blend", "validated"), blend_row("calm_blend", "research"),
            "MID"] + [blend_row(n, lab) for n, lab in (("hedged_blend_prefix_fix", "before the fixes"), ("calm_blend_prefix_fix", "before the fixes"),
                                                       ("legacy_blend", "overstated legacy")) if n in runs]
    write_fragment("tab_blend.tex", table("llrrrrrrr", ["Book", "Role", "Return", "Vol", "Sharpe", "Max DD", "Sh 96--01", "Sh 02--07", "Sh 08--13"], rows, size=r"\small"))
    # generated: does the blend beat both sleeves on Sharpe?
    sb = runs["hedged_blend"]["summary"]["sharpe"]
    se, so = runs["calm_weighted"]["summary"]["sharpe"], runs["options_cap20"]["summary"]["sharpe"]
    S.put("sent.blend_vs_sleeves", "exceeds the Sharpe ratio of either sleeve alone" if sb > max(se, so) else "does not exceed the better sleeve's Sharpe ratio")
    S.put("blend.vs_legacy.ratio", runs["legacy_blend"]["summary"]["sharpe"] / sb, f"{runs['legacy_blend']['summary']['sharpe'] / sb:.2f}")
    # worst era-by-era: the minimum era Sharpe of each
    for n in ("calm_weighted", "options_cap20", "hedged_blend"):
        s = runs[n]["summary"]
        S.put(f"{n}.minera", min(s["sharpe_1996_2001"], s["sharpe_2002_2007"], s["sharpe_2008_2013"]),
              f2(min(s["sharpe_1996_2001"], s["sharpe_2002_2007"], s["sharpe_2008_2013"])))
    # capital-split frontier
    fr = pd.read_csv(RES / "portfolio" / "frontier.csv")
    sub = fr[(fr.equity == "calm_weighted") & (fr.options == "options_cap20")].sort_values("w_equity")
    if sub.empty:
        sub = fr[fr.options == "options_cap20"].sort_values("w_equity")
    ctx["frontier"] = sub
    pick = sub.loc[sub["sharpe"].idxmax()]
    S.put("frontier.best.w", float(pick["w_equity"]), f"{pick['w_equity'] * 100:.0f}\\%")
    S.put("frontier.best.sh", float(pick["sharpe"]), f2(pick["sharpe"]))
    S.put("frontier.best.ret", float(pick["annualized_return"]), pc(pick["annualized_return"], 1))
    S.put("frontier.best.vol", float(pick["annualized_vol"]), pc(pick["annualized_vol"], 1))
    S.put("frontier.sh.min", float(sub["sharpe"].min()), f2(sub["sharpe"].min()))
    S.put("frontier.sh.max", float(sub["sharpe"].max()), f2(sub["sharpe"].max()))
    S.put("frontier.vol.max", float(sub["annualized_vol"].max()), pc(sub["annualized_vol"].max(), 1))
    S.put("frontier.n", len(sub), str(len(sub)))
    S.put("frontier.equity", sub["equity"].iloc[0] if len(sub) else "", sn(sub["equity"].iloc[0]) if len(sub) else "")


def frontier_section(S, ctx):
    """Equity capacity frontier from the base-unit sweep of the balanced_leverage construction at a 6x cap
    (data/sweep_aggressive_strategy6.csv), checked against the saved balanced_leverage_max run."""
    runs = ctx["runs"]
    sw = read_csv("sweep_aggressive_strategy6.csv")
    chosen_b = runs["balanced_leverage_max"]["params"]["base_unit_fraction"]
    rows = []
    for _, r in sw.iterrows():
        mark = abs(r["base_unit_fraction"] - chosen_b) < 1e-9
        cells = [f"{r['base_unit_fraction'] * 1e3:g}", pc(r["annualized_return"]), pc(r["annualized_vol"]), f2(r["sharpe"]), pc(r["max_drawdown"], 1),
                 pc(r["pct_liquidity_capped"], 1), f"{r['avg_leverage_vs_initial_capital']:.1f}" + r"$\times$"]
        rows.append([(r"\textbf{" + c + "}") if mark else c for c in cells])
    write_fragment("tab_frontier.tex", table("rrrrrrr", [r"$b\ (\times10^{-3})$", "Return", "Vol", "Sharpe", "Max DD", "ADV-capped", "Avg gross / initial cap."], rows, size=r"\small"))
    row = sw[np.isclose(sw["base_unit_fraction"], chosen_b)]
    s = runs["balanced_leverage_max"]["summary"]
    if len(row):
        r = row.iloc[0]
        ok = abs(r["annualized_return"] - s["annualized_return"]) < 5e-4 and abs(r["sharpe"] - s["sharpe"]) < 5e-3
    else:
        ok = False
    S.put("sweep.consistent", bool(ok), "")
    S.put("sent.sweepnote",
          r"The sweep row at the chosen base unit reproduces the saved \sn{balanced_leverage_max} run." if ok else
          r"\textbf{Caution: the sweep file was produced before the latest corrections and no longer matches the saved \sn{balanced_leverage_max} run; read it as the shape of the frontier, not as levels.}")
    imax = sw["annualized_return"].idxmax()
    S.put("sweep.peak.b", float(sw.loc[imax, "base_unit_fraction"]), f"{sw.loc[imax, 'base_unit_fraction'] * 1e3:g}")
    S.put("sweep.peak.ret", float(sw.loc[imax, "annualized_return"]), pc(sw.loc[imax, "annualized_return"]))
    S.put("sweep.peak.cap", float(sw.loc[imax, "pct_liquidity_capped"]), pc(sw.loc[imax, "pct_liquidity_capped"], 1))
    S.put("sweep.peak.is_chosen", bool(np.isclose(sw.loc[imax, "base_unit_fraction"], chosen_b)),
          "is" if np.isclose(sw.loc[imax, "base_unit_fraction"], chosen_b) else "is not")
    last = sw.iloc[-1]
    S.put("sweep.last.b", float(last["base_unit_fraction"]), f"{last['base_unit_fraction'] * 1e3:g}")
    S.put("sweep.last.ret", float(last["annualized_return"]), pc(last["annualized_return"]))
    S.put("sweep.last.cap", float(last["pct_liquidity_capped"]), pc(last["pct_liquidity_capped"], 0))
    S.put("sweep.n", len(sw), str(len(sw)))
    S.put("sweep.first.b", float(sw.iloc[0]["base_unit_fraction"]), f"{sw.iloc[0]['base_unit_fraction'] * 1e3:g}")
    # chosen rule: highest return subject to Sharpe >= 0.6 and drawdown >= -40%
    ok_rows = sw[(sw["sharpe"] >= S.get("rule.sharpe")) & (sw["max_drawdown"] >= -S.get("rule.dd"))]
    S.put("sweep.rule.b", float(ok_rows.loc[ok_rows["annualized_return"].idxmax(), "base_unit_fraction"]) if len(ok_rows) else -1,
          f"{ok_rows.loc[ok_rows['annualized_return'].idxmax(), 'base_unit_fraction'] * 1e3:g}" if len(ok_rows) else "--")


# ------------------------------------------------------------------------------------------------------
def deflated(sr_ann, T, skew, kurt, trial_sr_ann):
    g = 0.5772156649
    v = np.var(np.asarray(trial_sr_ann) / np.sqrt(TRADING_DAYS), ddof=1)
    N = len(trial_sr_ann)
    sr0 = np.sqrt(v) * ((1 - g) * sps.norm.ppf(1 - 1 / N) + g * sps.norm.ppf(1 - 1 / (N * np.e)))
    s = sr_ann / np.sqrt(TRADING_DAYS)
    z = (s - sr0) * np.sqrt(T - 1) / np.sqrt(1 - skew * s + (kurt - 1) / 4 * s ** 2)
    return sr0 * np.sqrt(TRADING_DAYS), float(sps.norm.cdf(z))


def dsr_at(sr_ann, T, skew, kurt, n, var_ann):
    """DSR when N trials with annualized-Sharpe variance var_ann are assumed."""
    g = 0.5772156649
    v = var_ann / TRADING_DAYS
    sr0 = np.sqrt(v) * ((1 - g) * sps.norm.ppf(1 - 1 / n) + g * sps.norm.ppf(1 - 1 / (n * np.e)))
    s = sr_ann / np.sqrt(TRADING_DAYS)
    z = (s - sr0) * np.sqrt(T - 1) / np.sqrt(1 - skew * s + (kurt - 1) / 4 * s ** 2)
    return sr0 * np.sqrt(TRADING_DAYS), float(sps.norm.cdf(z))


def inference_section(S, ctx):
    """Trial counts established from the repository, probabilistic and deflated Sharpe ratios."""
    runs, meta = ctx["runs"], ctx["meta"]
    # ---- trial accounting: every count below is a count of rows/entries in a committed file ----
    eq_runs = meta[meta.asset == "equity"]
    op_runs = meta[meta.asset == "options"]
    sweep_files = sorted(p for p in DATA.glob("sweep_*.csv"))
    sweeps = {}
    for p in sweep_files:
        x = pd.read_csv(p)
        if "sharpe" in x:
            sweeps[p.name] = x["sharpe"].dropna().to_numpy()
    opt_files = ["backtest_options_optimal_comparison.csv", "backtest_options_optimal_cap30_comparison.csv",
                 "backtest_options_optimal_cap40_comparison.csv", "backtest_options_v1_comparison.csv"]
    opt_old = {f: pd.read_csv(DATA / f)["sharpe"].dropna().to_numpy() for f in opt_files if (DATA / f).exists()}
    n_sweep = sum(len(v) for v in sweeps.values())
    n_eq_trials = len(eq_runs) + n_sweep
    n_op_trials = len(op_runs) + sum(len(v) for v in opt_old.values())
    S.put("trials.eq.runs", len(eq_runs), str(len(eq_runs)))
    S.put("trials.eq.sweepfiles", len(sweeps), str(len(sweeps)))
    S.put("trials.eq.sweeprows", n_sweep, str(n_sweep))
    S.put("trials.eq", n_eq_trials, str(n_eq_trials))
    S.put("trials.op.runs", len(op_runs), str(len(op_runs)))
    S.put("trials.op.oldfiles", len(opt_old), str(len(opt_old)))
    S.put("trials.op.oldrows", sum(len(v) for v in opt_old.values()), str(sum(len(v) for v in opt_old.values())))
    S.put("trials.op", n_op_trials, str(n_op_trials))
    eq_sr = np.concatenate([eq_runs["sharpe"].to_numpy()] + list(sweeps.values()))
    op_sr = np.concatenate([op_runs["sharpe"].to_numpy()] + list(opt_old.values()))
    op_sr_corrected = op_runs[~op_runs.index.str.endswith("_legacy")]["sharpe"].to_numpy()
    S.put("trials.eq.sd", float(np.std(eq_sr, ddof=1)), f2(np.std(eq_sr, ddof=1)))
    S.put("trials.op.sd", float(np.std(op_sr, ddof=1)), f2(np.std(op_sr, ddof=1)))
    S.put("trials.op.sd_corr", float(np.std(op_sr_corrected, ddof=1)), f2(np.std(op_sr_corrected, ddof=1)))
    S.put("trials.op.n_corr", len(op_sr_corrected), str(len(op_sr_corrected)))
    # the volatility research experiments recorded in docs/STRATEGIES.md (not a file with Sharpes): count of table rows
    st = (DOCS / "STRATEGIES.md").read_text(encoding="utf-8")
    m3 = re.search(r"\*\*What was tested.*?\n\n(\|.*?)\n\n", st, re.S)
    n_rows = 0
    if m3:
        n_rows = sum(1 for ln in m3.group(1).splitlines()[2:] if ln.startswith("|"))
    S.put("trials.volrows", n_rows, str(n_rows))
    # split-sample check recorded in the calm_rank_tilt module doc
    doc = runs["calm_rank_tilt"].get("module_doc", "")
    ms = re.search(r"picked on (\d{4})-(\d{4}) data alone \(best k ~ (\d)\) lifts (\d{4})-(\d{4}) Sharpe\s+([\d.]+) -> ([\d.]+)", doc)
    if ms:
        S.put("split.train", f"{ms.group(1)}--{ms.group(2)}", f"{ms.group(1)}--{ms.group(2)}")
        S.put("split.test", f"{ms.group(4)}--{ms.group(5)}", f"{ms.group(4)}--{ms.group(5)}")
        S.put("split.k", int(ms.group(3)), ms.group(3))
        S.put("split.from", float(ms.group(6)), ms.group(6)); S.put("split.to", float(ms.group(7)), ms.group(7))
        S.put("split.ok", True, "")
    else:
        S.put("split.ok", False, "")
    # ---- PSR / DSR for the headline books ----
    books = [("calm_weighted", "eq"), ("calm_rank_tilt", "eq"), ("balanced_leverage", "eq"), ("balanced_leverage_max", "eq"),
             ("options_cap20", "op"), ("options_cap30", "op"), ("hedged_blend", "pf"), ("calm_blend", "pf")]
    rows, curves = [], {}
    for n, kind in books:
        r, start = active_returns(ctx["daily"][n])
        sr = r.mean() / r.std() * np.sqrt(TRADING_DAYS)
        T = len(r)
        sk, ku = float(sps.skew(r)), float(sps.kurtosis(r, fisher=False))
        psr0 = float(sps.norm.cdf(sr / np.sqrt(TRADING_DAYS) * np.sqrt(T - 1) / np.sqrt(1 - sk * sr / np.sqrt(TRADING_DAYS) + (ku - 1) / 4 * (sr / np.sqrt(TRADING_DAYS)) ** 2)))
        trials = eq_sr if kind in ("eq", "pf") else op_sr
        N = n_eq_trials if kind in ("eq", "pf") else n_op_trials
        sr0, dsr = deflated(sr, T, sk, ku, trials)
        var_ann = float(np.var(trials, ddof=1))
        # N at which DSR first falls below 0.95 under the same trial-Sharpe variance
        grid = np.unique(np.round(np.logspace(0.3, 6, 400)).astype(int))
        below = [g for g in grid if dsr_at(sr, T, sk, ku, g, var_ann)[1] < 0.95]
        nstar = int(below[0]) if below else None
        S.put(f"{n}.psr", psr0, f"{psr0:.3f}")
        S.put(f"{n}.sr0", sr0, f2(sr0))
        S.put(f"{n}.dsr", dsr, f"{dsr:.2f}")
        S.put(f"{n}.dsr_N", N, str(N))
        S.put(f"{n}.dsr_Nstar", nstar if nstar else -1, f"{nstar:,}".replace(",", "{,}") if nstar else r"$>10^6$")
        S.put(f"{n}.T", T, intc(T))
        S.put(f"{n}.skew", sk, f2(sk)); S.put(f"{n}.kurt", ku, f"{ku:.1f}")
        rows.append([sn(n), str(N), f2(sr), f2(sr0), f"{psr0:.3f}", f"{dsr:.2f}", S.tex[f"{n}.dsr_Nstar"]])
        curves[n] = (sr, T, sk, ku, var_ann, kind)
    ctx["dsr_curves"] = curves
    write_fragment("tab_dsr.tex", table("lrrrrrr", ["Book", "$N$", "Active Sharpe", "$\\widehat{\\mathrm{SR}}_0$", "PSR vs 0", "DSR", "$N^\\ast$ for DSR$<0.95$"], rows, size=r"\small"))
    # options with corrected-only variance as a sensitivity
    for n in ("options_cap20", "options_cap30"):
        sr, T, sk, ku, _, _ = curves[n]
        sr0c, dsrc = deflated(sr, T, sk, ku, op_sr_corrected) if len(op_sr_corrected) > 2 else (float("nan"), float("nan"))
        S.put(f"{n}.dsr_corr", dsrc, f"{dsrc:.2f}")
        # same variance as corrected runs but with the full trial count
        sr0n, dsrn = dsr_at(sr, T, sk, ku, n_op_trials, float(np.var(op_sr_corrected, ddof=1)))
        S.put(f"{n}.dsr_corrvar_fullN", dsrn, f"{dsrn:.2f}")
    # N-sensitivity table for the two validated headlines
    Ns = [10, 30, 100, 300, 1000, 10000]
    rows = []
    for n in ("calm_weighted", "calm_rank_tilt", "options_cap20", "options_cap30"):
        sr, T, sk, ku, var_ann, kind = curves[n]
        rows.append([sn(n)] + [f"{dsr_at(sr, T, sk, ku, g, var_ann)[1]:.2f}" for g in Ns])
    write_fragment("tab_dsr_sens.tex", table("l" + "r" * len(Ns), ["Book"] + [f"$N={g:,}$".replace(",", "{,}") for g in Ns], rows, size=r"\small"))
    S.put("sent.eq_dsr_status",
          "exceeds" if S.get("calm_weighted.dsr") >= 0.95 else "falls short of")


# ------------------------------------------------------------------------------------------------------
def ladder_section(S, ctx):
    """The fix-attribution ladder: each methodology fix applied in turn (copied with provenance from the fixes branch's
    isolated vintages into docs/paper/cache/ladders/; checked against the saved _legacy twins)."""
    runs = ctx["runs"]
    SRC = ROOT.parent
    LAD = CACHE / "ladders"
    LAD.mkdir(parents=True, exist_ok=True)
    for f in ("equity_ladder.csv", "options_ladder.csv"):
        if (SRC / f).exists():
            shutil.copyfile(SRC / f, LAD / f)
    (LAD / "README.txt").write_text(
        "equity_ladder.csv and options_ladder.csv were produced by the methodology-fixes work (see docs/FIXES.md, 'Measured effect') from four\n"
        "isolated data vintages (published, +fix1 open-book cap, +fix1+2 point-in-time deciles, all fixes; options: fix3-only, fix2-only, all).\n"
        "Copied unchanged from the folder one level above the worktree. The paper builder cross-checks 'published' against the saved *_legacy\n"
        "runs and 'all' against the saved current runs.\n", encoding="utf-8")
    eq = pd.read_csv(LAD / "equity_ladder.csv").set_index("name")
    op = pd.read_csv(LAD / "options_ladder.csv")
    ctx["ladder_eq"], ctx["ladder_op"] = eq, op
    ok = True
    for n in eq.index:
        if n + "_legacy" in runs:
            ok &= abs(eq.loc[n, "published_ret"] - runs[n + "_legacy"]["summary"]["annualized_return"]) < 1e-4
        if n in runs:
            ok &= abs(eq.loc[n, "all_ret"] - runs[n]["summary"]["annualized_return"]) < 1e-4
    S.put("ladder.consistent", bool(ok), "")
    if not ok:
        print("WARNING: equity ladder does not match the saved runs", file=sys.stderr)
    stages = [("published", "Published"), ("fix1", "+ open-book cap"), ("fix12", "+ point-in-time deciles"), ("all", "+ search-window decay days")]
    for n, r in eq.iterrows():
        for st, _ in stages:
            S.put(f"ladder.{n}.{st}.ret", float(r[f"{st if st != 'published' else 'published'}_ret"]), pc(r[f"{st}_ret"], 1))
            S.put(f"ladder.{n}.{st}.sharpe", float(r[f"{st}_sharpe"]), f2(r[f"{st}_sharpe"]))
            S.put(f"ladder.{n}.{st}.dd", float(r[f"{st}_maxdd"]), pc(r[f"{st}_maxdd"], 1))
    d1 = eq["fix1_ret"] - eq["published_ret"]
    d2 = eq["fix12_ret"] - eq["fix1_ret"]
    d4 = eq["all_ret"] - eq["fix12_ret"]
    tot = eq["all_ret"] - eq["published_ret"]
    for tag, d in (("fix1", d1), ("fix2", d2), ("fix4", d4), ("total", tot)):
        S.put(f"ladder.med.{tag}", float(d.median()), sg(d.median() * 100, 1) + " points")
        S.put(f"ladder.min.{tag}", float(d.min()), sg(d.min() * 100, 1))
        S.put(f"ladder.max.{tag}", float(d.max()), sg(d.max() * 100, 1))
    steps = {"the open-book leverage cap": d1.median(), "point-in-time deciles": d2.median(), "search-window decay days": d4.median()}
    worst = min(steps, key=steps.get)
    S.put("sent.biggest_fix", f"the largest median cost to return is {worst} ({sg(steps[worst] * 100, 1)} percentage points a year)")
    S.put("ladder.n_books", len(eq), str(len(eq)))
    S.put("ladder.n_lower", int((tot < 0).sum()), str(int((tot < 0).sum())))
    S.put("ladder.n_fix2_largest", int(((d2 < d1) & (d2 < d4)).sum()), str(int(((d2 < d1) & (d2 < d4)).sum())))
    S.put("ladder.n_sharpe_lower", int((eq["all_sharpe"] < eq["published_sharpe"]).sum()), str(int((eq["all_sharpe"] < eq["published_sharpe"]).sum())))
    S.put("ladder.n_fix4_raise", int((d4 > 0).sum()), str(int((d4 > 0).sum())))
    # tables
    pick = ["balanced_leverage", "calm_weighted", "calm_rank_tilt", "extreme_picks", "balanced_leverage_max", "market_boost", "market_boost_max"]
    def row(n):
        r = eq.loc[n]
        return [sn(n), pc(r["published_ret"], 1), pc(r["fix1_ret"], 1), pc(r["fix12_ret"], 1), pc(r["all_ret"], 1),
                f"{f2(r['published_sharpe'])} $\\to$ {f2(r['all_sharpe'])}", f"{pc(r['published_maxdd'], 1)} $\\to$ {pc(r['all_maxdd'], 1)}"]
    hdr = ["Book", "Published", "+ cap", "+ deciles", "+ decay days", "Sharpe", "Max DD"]
    write_fragment("tab_ladder_equity.tex", table("lrrrrrr", hdr, [row(n) for n in pick if n in eq.index], size=r"\small"))
    write_fragment("tab_ladder_equity_all.tex", table("lrrrrrr", hdr, [row(n) for n in eq.index], size=r"\footnotesize"))
    names = {"published": "Published", "fix3_only_bidask": "Bid/ask fills only", "fix2_only_midfill": "Point-in-time deciles only (mid fills)", "all_bidask": "All fixes"}
    rows = []
    for n in op["name"].unique():
        sub = op[op.name == n].set_index("stage")
        def cell(st):
            if st not in sub.index:
                return "--"
            return f"{pc(sub.loc[st, 'ret'])} / {f2(sub.loc[st, 'sharpe'])}"
        rows.append([sn(n)] + [cell(st) for st in names])
        for st in sub.index:
            S.put(f"ladder.{n}.{st}.ret", float(sub.loc[st, "ret"]), pc(sub.loc[st, "ret"]))
            S.put(f"ladder.{n}.{st}.sharpe", float(sub.loc[st, "sharpe"]), f2(sub.loc[st, "sharpe"]))
    write_fragment("tab_ladder_options.tex", table("lrrrr", ["Book"] + list(names.values()), rows, size=r"\small"))
    # fix 2 and fix 4 settings recorded in docs/FIXES.md
    fx = (DOCS / "FIXES.md").read_text(encoding="utf-8")
    m_ = re.search(r"first (\d+)% of announcement quarters \((\d+) of (\d+)\)", fx)
    S.put("fix.decay.pct", int(m_.group(1)) if m_ else -1, f"{m_.group(1)}\\%" if m_ else "--")
    S.put("fix.decay.n", int(m_.group(2)) if m_ else -1, m_.group(2) if m_ else "--")
    S.put("fix.decay.of", int(m_.group(3)) if m_ else -1, m_.group(3) if m_ else "--")
    S.put("pit.dropped", S.get("pre.n_events") - S.get("n_events"), intc(S.get("pre.n_events") - S.get("n_events")))
    # leverage: before and after, from the legacy twin and the current run
    for n in ("calm_weighted", "balanced_leverage_max"):
        if n + "_legacy" in runs:
            for k in ("days_over_cap", "max_over_cap"):
                pass
    # drawdown screen of the *_max books
    mx = [n for n in runs if n.endswith("_max") and not n.endswith("_legacy")]
    breach = [n for n in mx if runs[n]["summary"]["max_drawdown"] < -S.get("rule.dd")]
    S.put("max.n", len(mx), str(len(mx)))
    S.put("max.n_breach", len(breach), str(len(breach)))
    S.put("max.breach", breach, ", ".join(sn(n) for n in sorted(breach)))
    S.put("max.worst_dd", float(min(runs[n]["summary"]["max_drawdown"] for n in mx)), pc(min(runs[n]["summary"]["max_drawdown"] for n in mx), 1))
    # alpha ratio of the last era to the first, across the profiled books (all can be tiny or negative)
    e = ctx["era_bp"]
    S.put("era.all.CA.min", float((e["C"] / e["A"]).min()), pc((e["C"] / e["A"]).min(), 0))
    S.put("era.all.CA.max", float((e["C"] / e["A"]).max()), pc((e["C"] / e["A"]).max(), 0))
    # recipe: in how many eras is the best recipe positive
    b3 = S.get("best.n_all3")
    S.put("sent.best_eras", (f"It is positive in all three eras in {b3} of nine books" if b3 else "It is not positive in all three eras in any of the nine books")
          + (f" (the exceptions are {S.tex['best.neg_books']})" if 0 < b3 < 9 else ""))


def audit_section(S, ctx):
    runs = ctx["runs"]
    fixes = (DOCS / "FIXES.md").exists()
    S.put("fixes.exists", fixes, "")
    S.put("sent.fixes_pending",
          "the correction is documented in \\texttt{docs/FIXES.md}" if fixes else
          "identified, not yet applied to the reported numbers (no \\texttt{docs/FIXES.md} exists in this checkout)")
    S.put("sent.fixes_short", "see \\texttt{docs/FIXES.md}" if fixes else "identified; not yet applied to the reported numbers")
    # equity twins (appear only after the leverage / decile / decay corrections land)
    twins = []
    for n in sorted(runs):
        for suf in ("_legacy", "_midfill"):
            if n.endswith(suf):
                base = n[: -len(suf)]
                if base in runs and runs[n]["asset_class"] == "equity":
                    twins.append((base, n))
    S.put("twins.eq.n", len(twins), str(len(twins)))
    tw = dict(twins)
    if "calm_weighted" in tw:
        a, b = runs["calm_weighted"]["summary"], runs[tw["calm_weighted"]]["summary"]
        lev = (sn("calm_weighted") + ": " + pc(b["annualized_return"]) + " / " + f2(b["sharpe"]) + r" $\to$ " + pc(a["annualized_return"]) + " / " + f2(a["sharpe"])
               + "; open-book gross now bounded by the cap (peak " + S.tex["calm_weighted.gross_max"] + r"$\times$)")
    else:
        lev = (sn("calm_weighted") + ": cap " + S.tex["calm_weighted.cap"] + r"$\times$, peak " + S.tex["calm_weighted.gross_max"] + r"$\times$, over the cap on "
               + S.tex["calm_weighted.days_over_cap"] + " of days. " + S.tex["sent.fixes_short"][0:0] + "Correction " + S.tex["sent.fixes_short"])
    S.put("audit.lev", lev)
    rows = []
    for base, n in twins:
        a, b = runs[base]["summary"], runs[n]["summary"]
        rows.append([sn(base), pc(b["annualized_return"], 1), f2(b["sharpe"]), pc(b["max_drawdown"], 1), pc(a["annualized_return"], 1), f2(a["sharpe"]), pc(a["max_drawdown"], 1)])
    if rows:
        body = (table("lrrrrrr", ["Book", "Return", "Sharpe", "Max DD", "Return", "Sharpe", "Max DD"], rows, size=r"\small")
                .replace(r"\toprule" + "\n", r"\toprule" + "\n & \\multicolumn{3}{c}{Before the fix (legacy twin)} & \\multicolumn{3}{c}{After the fix}\\\\\n\\cmidrule(lr){2-4}\\cmidrule(lr){5-7}\n"))
        write_fragment("tab_equity_twins.tex", "\\begin{table}[t]\\centering\n\\caption{Effect of the equity corrections on every book that has a saved pre-correction twin.}\\label{tab:twins}\n"
                       + body + "\\end{table}\n")
        write_fragment("sent_equity_twins.tex", "Table~\\ref{tab:twins} shows the effect on every equity book that has a legacy twin in \\texttt{results/}.\n")
    else:
        write_fragment("tab_equity_twins.tex", "")
        write_fragment("sent_equity_twins.tex", "No equity book has a legacy twin in \\texttt{results/} yet, so no before/after comparison of the three equity corrections can be produced from the saved runs.\n")
    # audit effect numbers for the options quote fix
    a, b = runs["options_cap20"]["summary"], runs["options_cap20_legacy"]["summary"]
    S.put("audit.opt20.dret", b["annualized_return"] - a["annualized_return"], pc(b["annualized_return"] - a["annualized_return"]))
    # benchmark artifact: long/short ratio all-announcer vs size-matched for balanced_leverage
    # (already in long_short_section); defects table is assembled in the tex from macros.
    # lists of old headline vs corrected for the 'overstated' comparison
    for pair in (("options_cap20", "options_cap20_legacy"), ("options_cap30", "options_cap30_legacy")):
        pass


def reproducibility_section(S, ctx):
    runs = ctx["runs"]
    # git revs of the runs
    revs = sorted({runs[n]["run"].get("git_rev", "?") for n in runs})
    S.put("runs.revs", revs, ", ".join(revs))
    S.put("runs.pandas", sorted({runs[n]["run"].get("pandas", "?") for n in runs}), ", ".join(sorted({runs[n]["run"].get("pandas", "?") for n in runs})))
    ts = sorted(runs[n]["run"]["timestamp"] for n in runs)
    S.put("runs.first", ts[0][:10], ts[0][:10]); S.put("runs.last", ts[-1][:10], ts[-1][:10])
    # parameter table (equity)
    rows = []
    for n in ["extreme_picks", "graded_bets", "size_balanced", "conviction_tilt", "sector_balanced", "balanced_leverage", "market_boost", "market_exposed",
              "calm_weighted", "calm_rank_tilt", "calm_rank_levered", "balanced_leverage_max", "market_boost_max"]:
        if n not in runs:
            continue
        p = runs[n]["params"]
        rows.append([sn(n), f"{p['base_unit_fraction']:g}", f"{p['max_gross_leverage']:g}$\\times$", f"{p['liquidity_cap_frac'] * 100:g}\\%", tx(p["return_basis"]),
                     tx(p["cap_mode"]), tx(p.get("exposure_cap", "entry_cohort")), f"{p.get('beta_overlay', 0):g}"])
    write_fragment("tab_params_equity.tex", table("lrrrllll", ["Book", "Base unit", "Gross cap", "ADV cap", "Returns", "Trim rule", "Cap on", "Overlay"], rows, size=r"\small"))
    rows = []
    for n in ["options_cap20", "options_cap30", "options_worst_case", "options_cap20_legacy", "options_cap30_legacy", "options_cap40_legacy"]:
        if n not in runs:
            continue
        p = runs[n]["params"]
        rows.append([sn(n), f"{p['max_premium_frac']:g}", f"{p['half_kelly']:g}", str(p["hold_horizon"]), f"{p['roundtrip_cost_pct'] * 100:g}\\%",
                     f"\\${p['min_roundtrip_cost_per_share']:.2f}", tx(p.get("fill_mode", "mid")), tx(p["dead_quote_mark"])])
    write_fragment("tab_params_options.tex", table("lrrrrrll", ["Book", "Premium cap", "Kelly mult.", "Hold (d)", "Round-trip", "Floor / share", "Fills", "Dead-quote mark"], rows, size=r"\small"))
    rows = [[tx(a), tx(b) if "*" not in b else b.replace("*", r"$\ast$")] for a, b in OLD_LABELS]
    rows = [[tx(a), (sn(b) if re.fullmatch(r"[a-z_0-9]+", b) else tx(b).replace(r"\_", r"\_"))] for a, b in OLD_LABELS]
    write_fragment("tab_oldnames.tex", ("{\\small\n\\begin{tabularx}{\\linewidth}{@{}p{0.43\\linewidth}X@{}}\n\\toprule\nLabel in earlier drafts & Plain name here\\\\\n\\midrule\n"
                                        + "\n".join(" & ".join(r) + r" \\" for r in rows) + "\n\\bottomrule\n\\end{tabularx}\n}\n"))
    # sample / data files present and their sizes (reproducibility map)
    S.put("n_results_dirs", len(runs), str(len(runs)))


# ------------------------------------------------------------------------------------------------------
def sentences(S, ctx):
    """Comparisons whose direction could change when results/ change, rendered as text from the numbers."""
    runs = ctx["runs"]
    dec, spr, ffs = ctx["dec"], ctx["spr"], ctx["ffs"]
    # monotonicity of the decile profile
    mono = [h for h in HORIZONS if np.all(np.diff(dec[dec.horizon == h].sort_values("decile")["mean"].to_numpy()) > 0)]
    rest = [h for h in HORIZONS if h not in mono]
    lst = lambda hs: ", ".join(f"$+{h}$d" for h in hs)
    if len(mono) == len(HORIZONS):
        t = "Decile means are strictly increasing in surprise at every horizon."
    elif mono:
        t = (f"Decile means are strictly increasing in surprise at {lst(mono)}; at {lst(rest)} the tails are ordered but the middle deciles "
             "are not strictly monotone.")
    else:
        t = "Decile means are not strictly monotone in surprise at any horizon, although the tails are ordered."
    S.put("sent.monotone", t)
    for tag, d in (("mkt", spr), ("ff", ffs)):
        v = d.loc[HORIZONS, "mean_spread"].to_numpy()
        S.put(f"sent.rising.{tag}", "rises with every step in the horizon" if np.all(np.diff(v) > 0) else "does not rise at every step")
    S.put("sent.d1ff", ("no longer recovers: its size/book-to-market-adjusted 60-day return is %s" % S.tex["d1_ff_60"]) if S.get("d1_ff_60") < 0
          else ("still recovers to %s" % S.tex["d1_ff_60"]))
    p = S.get("het.sector.p")
    S.put("sent.het_sector", "indistinguishable across industries" if p > 0.05 else "different across industries")
    pe = S.get("het.era.p")
    S.put("sent.het_era", ("The across-era difference is statistically detectable" if pe < 0.05 else "The across-era difference is not statistically detectable")
          + f" (Cochran $Q={S.tex['het.era.Q']}$ on {S.tex['het.era.df']} d.f., $p={S.tex['het.era.p']}$, independence approximation)")
    ps = S.get("het.size.p")
    S.put("sent.het_size", f"significant (Cochran $Q={S.tex['het.size.Q']}$ on {S.tex['het.size.df']} d.f., $p{'=' if ps >= 0.001 else ''}{S.tex['het.size.p']}$)" if ps < 0.05
          else f"not significant ($p={S.tex['het.size.p']}$)")
    # long vs short leg
    lo, hi, med = S.get("ls.ratio.min"), S.get("ls.ratio.max"), S.get("ls.ratio.median")
    S.put("sent.legs", f"the long leg earns {S.tex['ls.ratio.min']} to {S.tex['ls.ratio.max']} times the short leg per dollar traded (median {S.tex['ls.ratio.median']})"
          if lo > 1 else f"the long leg earns between {S.tex['ls.ratio.min']} and {S.tex['ls.ratio.max']} times the short leg per dollar traded")
    S.put("sent.legs_all", f"{S.tex['ls.all.ratio.min']} to {S.tex['ls.all.ratio.max']}")
    # high-beta shorts
    n, k, m = S.get("hbs.n"), S.get("hbs.n_all3neg"), S.get("hbs.n_noalpha")
    S.put("sent.hbs", f"In {m} of {n} books the alpha of high-beta shorts is statistically indistinguishable from zero ($|t|<2$), and in {k} of {n} they lose real dollars "
                      f"in all three eras")
    # alpha decay vs the market
    S.put("sent.alphafade", f"In {S.tex['alpha.fade.frac']} of the market-neutral books the annualized alpha against the CRSP market is lower in 2008--13 than in 1996--2001")
    # levered book vs validated
    bl, cw = runs["balanced_leverage_max"]["summary"], runs["calm_weighted"]["summary"]
    S.put("sent.levered_ret", "higher" if bl["annualized_return"] > cw["annualized_return"] else "lower")
    S.put("sent.levered_sh", "higher" if bl["sharpe"] > cw["sharpe"] else "lower")
    S.put("sent.levered_dd", "deeper" if bl["max_drawdown"] < cw["max_drawdown"] else "shallower")
    mb = runs["market_boost_max"]["summary"]
    S.put("sent.mbmax_vs_mkt", "above" if mb["annualized_return"] > S.get("market.ret") else "below")
    mb2 = runs["market_boost"]["summary"]
    S.put("sent.mb_vs_mkt", "above" if mb2["annualized_return"] > S.get("market.ret") else "below")
    S.put("sent.cw_vs_mkt", "above" if cw["annualized_return"] > S.get("market.ret") else "below")
    # options deflation
    c20, c20c = S.get("options_cap20.dsr"), S.get("options_cap20.dsr_corr")
    if c20 >= 0.95:
        t = "clears the 0.95 bar under either measure of trial dispersion"
    elif c20c >= 0.95:
        t = ("clears the 0.95 bar only when trial dispersion is measured across the corrected runs; measured across all counted trials "
             "(which include the overstated legacy runs) it does not")
    else:
        t = "does not clear the 0.95 bar under either measure of trial dispersion"
    S.put("sent.opt_dsr", t)
    S.put("sent.eq_dsr", "clears the 0.95 bar at the counted $N$" if S.get("calm_weighted.dsr") >= 0.95 else "does not clear the 0.95 bar at the counted $N$")
    ct = S.get("calm_rank_tilt.dsr")
    S.put("sent.crt_dsr", "clears" if ct >= 0.95 else "does not clear")
    # overstated comparison
    l20, c20s = runs["options_cap20_legacy"]["summary"], runs["options_cap20"]["summary"]
    S.put("sent.opt_over", f"overstated the return by a factor of {S.tex['options_cap20.over_ret']} and the Sharpe ratio by a factor of {S.tex['options_cap20.over_sh']}")
    # options trade shape
    if "ot.n" in S.raw:
        S.put("sent.top1", "more than the whole net profit" if S.get("ot.top1") > 1 else "of the net profit")
    # sharpe progression statement for the blend
    S.put("sent.blend_sleeve_in_eras",
          "in every era" if all(runs["hedged_blend"]["summary"][k] > min(runs["calm_weighted"]["summary"][k], runs["options_cap20"]["summary"][k])
                                for k in ("sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013")) else "not in every era")
    # which sleeve is the better one in each era
    eras = ("sharpe_1996_2001", "sharpe_2002_2007", "sharpe_2008_2013")
    lead = ["equity" if runs["calm_weighted"]["summary"][k] > runs["options_cap20"]["summary"][k] else "options" for k in eras]
    S.put("sent.sleeve_lead", "the equity sleeve has the higher Sharpe ratio in the first era and the options sleeve in the last" if lead[0] == "equity" and lead[-1] == "options"
          else "the sleeves' relative strength does not alternate in the way earlier drafts reported: leaders by era are " + ", ".join(lead))
    # corrected options book: is there a distinguishable edge at all?
    p20, c20r, c30r = S.get("options_cap20.psr"), runs["options_cap20"]["summary"]["annualized_return"], runs["options_cap30"]["summary"]["annualized_return"]
    S.put("sent.opt_edge", ("has a probabilistic Sharpe ratio against zero of %s, so its edge is not statistically distinguishable from none" % S.tex["options_cap20.psr"])
          if p20 < 0.95 else ("has a probabilistic Sharpe ratio against zero of %s" % S.tex["options_cap20.psr"]))
    S.put("sent.opt_cap30_sign", "positive" if c30r > 0 else "negative")
    S.put("sent.opt_vs_prefix", "lower" if c20r < runs["options_cap20_prefix_fix"]["summary"]["annualized_return"] else "higher")
    eb, ec = runs["hedged_blend"]["summary"], runs["calm_weighted"]["summary"]
    S.put("sent.blend_vs_equity", f"a Sharpe ratio of {S.tex['hedged_blend.sharpe']} against {S.tex['calm_weighted.sharpe']} for its equity sleeve " +
          ("(higher)" if eb["sharpe"] > ec["sharpe"] else "(lower)") + f", and a return of {S.tex['hedged_blend.ret']} against {S.tex['calm_weighted.ret']} at a volatility of "
          f"{S.tex['hedged_blend.vol']} against {S.tex['calm_weighted.vol']}")
    S.put("sent.mb_clears", "clears" if mb2["annualized_return"] > S.get("market.ret") else "does not clear")
    S.put("sent.mbmax_clears", "clears" if mb["annualized_return"] > S.get("market.ret") else "does not clear")
    # legacy-to-current changes of the headline equity books, direction
    S.put("sent.eq_fix_dir", "lower" if runs["calm_weighted"]["summary"]["annualized_return"] < runs["calm_weighted_legacy"]["summary"]["annualized_return"] else "not lower")
    # beta gap
    S.put("sent.sweep_peak", "peaks at the chosen base unit" if S.get("sweep.peak.is_chosen") == "is" or S.get("sweep.peak.is_chosen") is True else "does not peak at the chosen base unit")


# ======================================================================================================
# driver
# ======================================================================================================
def check_macros(S):
    tex = TEX.read_text(encoding="utf-8")
    strip = lambda x: "\n".join(ln for ln in x.splitlines() if not ln.lstrip().startswith("%"))
    texts = [strip(tex)] + [f.read_text(encoding="utf-8") for f in GEN.glob("*.tex")]
    used_keys = set()
    for t in texts:
        used_keys |= {k for k in re.findall(r"\\stv\{([^}]+)\}", t) if "#" not in k}
    used_names = set()
    for t in texts:
        used_names |= set(re.findall(r"\\(stat[A-Z][A-Za-z]*)", t))
    missing = sorted(k for k in used_keys if k not in S.tex)
    name_to_key = {macro_name(k): k for k in S.tex}
    missing += sorted(n for n in used_names if n not in name_to_key)
    if missing:
        raise SystemExit("Undefined number macros in the paper: " + ", ".join(missing[:40]))
    used = used_keys | {name_to_key[n] for n in used_names}
    print(f"macros: {len(used)} used, {len(S.tex)} defined ({len(S.tex) - len(used)} spare)")
    # every strategy named in the text must exist in results/ (guards against renamed or removed books)
    names_used = set(re.findall(r"\\sn\{([a-z0-9_]+)\}", tex))
    unknown = sorted(n for n in names_used if n not in S.runs_names)
    if unknown:
        raise SystemExit("strategy names not in results/: " + ", ".join(unknown))
    bad = re.findall(r"Strategy\s*\d|opt_kelly", tex)
    # the only allowed occurrences are in the old-label table, which is generated (tab_oldnames.tex), not in the .tex
    if bad:
        raise SystemExit(f"legacy labels found in {TEX.name}: {bad[:5]}")


def run_latex():
    cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", TEX.name]
    for i in range(3):
        r = subprocess.run(cmd, cwd=OUT, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-3000:])
            raise SystemExit("pdflatex failed")
        if i >= 1 and "Rerun to get cross-references" not in r.stdout and "undefined" not in r.stdout.lower():
            break
    log = (OUT / "PEAD_Research_Paper.log").read_text(encoding="utf-8", errors="replace")
    und = re.findall(r"(?:Reference|Citation) `[^']+' on page \d+ undefined", log)
    if und:
        raise SystemExit("undefined references: " + "; ".join(und[:10]))
    over = re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)", log)
    big = [float(x) for x in over if float(x) > 10]
    print(f"pdflatex ok; overfull boxes: {len(over)} (>10pt: {len(big)})")
    return log


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--refresh-external", action="store_true", help="recompute the beta x vol / option-trade numbers even if the cache is current")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True); FIG.mkdir(exist_ok=True); GEN.mkdir(exist_ok=True)
    runs = load_runs()
    ext, mkt = external(runs, refresh=args.refresh_external)
    S = Stats()
    ctx = compute(S, runs, ext, mkt)
    S.write()
    if not args.no_figures:
        from pead.reporting import paper_figures
        paper_figures.make_all(S, ctx, FIG)
    if TEX.exists():
        check_macros(S)
        if not args.no_pdf:
            run_latex()
    print("paper stats:", len(S.raw), "numbers ->", OUT / "stats.tex")


if __name__ == "__main__":
    main()
