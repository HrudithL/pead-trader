"""In which situations does an equity strategy make (or lose) money, how often do they occur, and is the
difference real?

Every funded position is tagged with the situation it was in (what kind of surprise, company size, how
jumpy the stock, which industry, how long the hold, what the market did, ...). For each situation we
report how OFTEN it occurs (share of positions and of dollars traded), how much it EARNS (alpha in
basis points per dollar traded, and its share of the book's total alpha), and how SURE we are (a
t-statistic computed across announcement quarters, not across positions: positions announced in the
same quarter share the same market moves, so counting them separately would overstate confidence).

alpha = excess - cost, where excess is the return beyond the average event stock that quarter (the part
the surprise rank is supposed to predict; see attribution.py). The market-neutral books earn their
return through this part.

Verdict rules (per slice, deliberately conservative -- ~60 slices are tested per strategy, so some will
look good by luck):
    ENGINE     alpha t >= 2.5 and positive in >= 2 of 3 eras and earns more than the book's average per dollar
    WORKS      alpha t >= 2   and positive in >= 2 of 3 eras
    UNPROVEN   not distinguishable from zero (|t| < 2)
    LEAKS      alpha t <= -2 (reliably loses money in this situation)
"""
import numpy as np
import pandas as pd

from pead.reporting.attribution import equity_positions_with_alpha

MIN_QUARTERS = 8


def _tag_market(p: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """Add what the overall market did during the hold, and how stormy it was at entry."""
    m = market.drop_duplicates("date").sort_values("date").set_index("date")["vwretd"].fillna(0.0)
    idx = m.index
    cum = np.log1p(m.to_numpy()).cumsum()
    a = idx.searchsorted(p["day0_date"].to_numpy(), side="left")
    b = idx.searchsorted(p["exit_date"].to_numpy(), side="right") - 1
    a, b = np.clip(a, 1, len(idx) - 1), np.clip(b, 1, len(idx) - 1)
    p["mkt_ret_hold"] = np.expm1(cum[b] - cum[a - 1])
    p["market_during_hold"] = pd.cut(p["mkt_ret_hold"], [-9, -0.05, 0.05, 9], labels=["market fell >5%", "market flat (+-5%)", "market rose >5%"])
    vol60 = m.rolling(60).std().shift(1) * np.sqrt(252)
    p["mkt_vol_entry"] = vol60.reindex(p["day0_date"].to_numpy()).to_numpy()
    q = p["mkt_vol_entry"].quantile([1 / 3, 2 / 3]).to_numpy()
    p["market_mood"] = pd.cut(p["mkt_vol_entry"], [-1, q[0], q[1], 9], labels=["calm market", "normal market", "stormy market"])
    return p


def _tag_other(p: pd.DataFrame) -> pd.DataFrame:
    cell = p["holding_cell"].str.extract(r"(\d+)_(\d+)_(\d+)$").astype(float)
    p["bm_tercile"] = cell[2]
    p["bm_group"] = p["bm_tercile"].map({1: "growth (low book/market)", 2: "middle", 3: "value (high book/market)"})
    p["season"] = p["ann_quarter"].dt.quarter.map({1: "Q1 reports (Jan-Mar: year-end results)", 2: "Q2 reports (Apr-Jun)",
                                                   3: "Q3 reports (Jul-Sep)", 4: "Q4 reports (Oct-Dec)"})
    if "beta_252d" in p.columns:
        q = p["beta_252d"].quantile([1 / 3, 2 / 3]).to_numpy()
        p["beta_group"] = pd.cut(p["beta_252d"], [-99, q[0], q[1], 99], labels=["low-beta stocks", "mid-beta stocks", "high-beta stocks"])
    p["decile_group"] = p["leg"] + " D" + p["decile"].astype(str).str.zfill(2)
    p["size_group"] = "size " + p["size_quintile"].astype("Int64").astype(str) + p["size_quintile"].map({1: " (smallest)", 5: " (largest)"}).fillna("")
    p["liq_group"] = "liquidity " + p["liquidity_quintile"].astype("Int64").astype(str) + p["liquidity_quintile"].map({1: " (thinnest)", 5: " (most traded)"}).fillna("")
    p["vol_group"] = p["vol_tercile"].map({"low vol": "calm stocks", "mid vol": "average stocks", "high vol": "jumpy stocks"}) if "vol_tercile" in p else np.nan
    return p


DIMENSIONS = [  # (label, column, description)
    ("Side and surprise size", "decile_group", "long = bought good news, short = sold bad news; D10 = biggest beat, D01 = biggest miss"),
    ("Company size", "size_group", "market value quintile"),
    ("How easily traded", "liq_group", "21-day dollar volume quintile"),
    ("How jumpy the stock is", "vol_group", "stock-specific volatility tercile"),
    ("Industry", "ff12_sector", "Fama-French 12 industry groups"),
    ("Hold length", "hold_bucket", "days the position was held"),
    ("Cheap vs expensive", "bm_group", "book-to-market tercile (as used by the holding-window cells)"),
    ("Stock's market sensitivity", "beta_group", "beta tercile"),
    ("Period", "era", "three sub-periods"),
    ("What the market did", "market_during_hold", "overall market return while the position was open"),
    ("How stormy the market was", "market_mood", "trailing 60-day market volatility at entry (terciles)"),
    ("Earnings season", "season", "which quarter's results were being announced"),
]


def _quarter_series(g: pd.DataFrame) -> pd.Series:
    """alpha bp per $ traded for each announcement quarter."""
    s = g.groupby("ann_quarter", observed=True).agg(a=("alpha_net", "sum"), n=("notional", lambda v: v.abs().sum()))
    s = s[s["n"] > 0]
    return 1e4 * s["a"] / s["n"]


def _tstat(s: pd.Series) -> float:
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if len(s) >= MIN_QUARTERS and s.std(ddof=1) > 0 else np.nan


def slice_table(p: pd.DataFrame, col: str) -> pd.DataFrame:
    by_era = col == "era"
    total_alpha = p["alpha_net"].sum()
    total_notional = p["notional"].abs().sum()
    book_bp = 1e4 * total_alpha / total_notional
    years = (p["exit_date"].max() - p["day0_date"].min()).days / 365.25
    rows = []
    for key, g in p.groupby(col, observed=True):
        n_not = g["notional"].abs().sum()
        qs = _quarter_series(g)
        eras = {e: (1e4 * x["alpha_net"].sum() / x["notional"].abs().sum() if len(x) and x["notional"].abs().sum() > 0 else np.nan)
                for e, x in g.groupby("era", observed=True)}
        bp = 1e4 * g["alpha_net"].sum() / n_not if n_not else np.nan
        t = _tstat(qs)
        eras_pos = 3 if by_era else sum(1 for v in eras.values() if v > 0)
        if np.isnan(t):
            verdict = "TOO FEW"
        elif t >= 2.5 and eras_pos >= 2 and bp > book_bp:
            verdict = "ENGINE"
        elif t >= 2 and eras_pos >= 2:
            verdict = "WORKS"
        elif t <= -2:
            verdict = "LEAKS"
        else:
            verdict = "UNPROVEN"
        rows.append(dict(situation=key, positions=len(g), positions_per_year=len(g) / years,
                         pct_of_positions=len(g) / len(p), pct_of_dollars=n_not / total_notional,
                         net_pnl_M=g["net_pnl"].sum() / 1e6, alpha_M=g["alpha_net"].sum() / 1e6,
                         pct_of_alpha=g["alpha_net"].sum() / total_alpha if total_alpha else np.nan,
                         alpha_bp_per_dollar=bp, lift=(g["alpha_net"].sum() / total_alpha) / (n_not / total_notional) if total_alpha else np.nan,
                         hit_rate=float((g["alpha_net"] > 0).mean()), quarters=len(qs), pct_quarters_positive=float((qs > 0).mean()) if len(qs) else np.nan,
                         t_stat=t, bp_1996_01=eras.get("1996-01", np.nan), bp_2002_07=eras.get("2002-07", np.nan),
                         bp_2008_13=eras.get("2008-13", np.nan), verdict=verdict))
    return pd.DataFrame(rows)


def contrast(p: pd.DataFrame, col: str, hi, lo) -> dict:
    """Is the alpha in situation `hi` really different from `lo`? Paired by quarter."""
    a = _quarter_series(p[p[col] == hi])
    b = _quarter_series(p[p[col] == lo])
    d = (a - b).dropna()
    if len(d) >= 30:                                   # same quarters on both sides: paired comparison
        diff, t, n, how = float(d.mean()), _tstat(d), int(len(d)), "paired by quarter"
    else:                                              # different quarters (eras, market moods): Welch t-test
        se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        diff, t, n, how = float(a.mean() - b.mean()), float((a.mean() - b.mean()) / se), int(min(len(a), len(b))), "different quarters (Welch)"
    return dict(a=hi, b=lo, a_bp=float(a.mean()), b_bp=float(b.mean()), diff_bp=diff, t_stat=t, quarters=n, method=how)


def analyze(result, events, market) -> dict:
    p = equity_positions_with_alpha(result, events)
    p = _tag_other(_tag_market(p, market))
    p = p[p["notional"].abs() > 0]
    out = {"book": dict(positions=len(p), alpha_M=float(p["alpha_net"].sum() / 1e6), net_pnl_M=float(p["net_pnl"].sum() / 1e6),
                        alpha_bp=float(1e4 * p["alpha_net"].sum() / p["notional"].abs().sum()),
                        quarters=int(p["ann_quarter"].nunique()))}
    tabs = []
    for label, col, desc in DIMENSIONS:
        if col not in p.columns or p[col].isna().all():
            continue
        t = slice_table(p, col)
        t.insert(0, "dimension", label)
        tabs.append(t)
    out["slices"] = pd.concat(tabs, ignore_index=True)
    out["drivers"] = driver_regression(p)
    out["contrasts"] = pd.DataFrame([dict(dimension=lab, **contrast(p, col, hi, lo)) for lab, col, hi, lo in [
        ("Calm vs jumpy stocks", "vol_group", "calm stocks", "jumpy stocks"),
        ("Small vs large companies", "size_quintile", 1, 5),
        ("Thin vs heavily traded", "liquidity_quintile", 1, 5),
        ("Long holds vs short holds", "hold_bucket", ">90d", "<=40d"),
        ("Early vs late period", "era", "1996-01", "2008-13"),
        ("Biggest beats (long D10) vs biggest misses (short D01)", "decile_group", "long D10", "short D01"),
        ("Market rose vs fell during hold", "market_during_hold", "market rose >5%", "market fell >5%"),
        ("Calm vs stormy market at entry", "market_mood", "calm market", "stormy market"),
    ] if (p[col] == hi).any() and (p[col] == lo).any()])
    return out


FEATURES = [  # (label, column, how it is built)
    ("Smaller company", "size_quintile", -1), ("Thinner trading", "liquidity_quintile", -1),
    ("Jumpier stock (stock-specific vol)", "ivol_252d_pct", 1), ("Higher beta", "beta_pct", 1),
    ("Longer hold window", "log_hold", 1), ("More extreme surprise (|rank - median|)", "extremity", 1),
    ("Value (high book/market)", "bm_tercile", 1), ("Long side (vs short)", "is_long", 1),
    ("Market return while held (known only afterwards)", "mkt_ret_hold", 1),
]


def driver_regression(p: pd.DataFrame) -> pd.DataFrame:
    """Dollar-weighted regression of each position's alpha (bp per $ traded) on all situation features at once,
    standard errors clustered by announcement quarter. Coefficient = bp per $ traded for a 1-standard-deviation
    move in that feature, HOLDING THE OTHERS FIXED -- which is what separates, say, 'small' from 'thinly traded'."""
    d = p.copy()
    d["beta_pct"] = d["beta_252d"].rank(pct=True)
    d["log_hold"] = np.log(d["holding_days"])
    d["extremity"] = (d["sue_rank_pct"] - 0.5).abs() * 2
    d["is_long"] = (d["side"] > 0).astype(float)
    d = d.dropna(subset=[c for _, c, _ in FEATURES] + ["alpha_net"])
    w = d["notional"].abs().to_numpy()
    y = 1e4 * d["alpha_net"].to_numpy() / w
    X = np.column_stack([np.ones(len(d))] + [((d[c] - d[c].mean()) / d[c].std() * sgn).to_numpy() for _, c, sgn in FEATURES])
    sw = np.sqrt(w)
    Xw, yw = X * sw[:, None], y * sw
    XtX_inv = np.linalg.inv(Xw.T @ Xw)
    b = XtX_inv @ Xw.T @ yw
    e = yw - Xw @ b
    cl = d["ann_quarter"].to_numpy()
    meat = np.zeros((X.shape[1],) * 2)
    for q in np.unique(cl):
        s = (Xw[cl == q] * e[cl == q][:, None]).sum(axis=0)
        meat += np.outer(s, s)
    G = len(np.unique(cl))
    cov = XtX_inv @ meat @ XtX_inv * G / (G - 1)
    se = np.sqrt(np.diag(cov))
    return pd.DataFrame({"driver": ["(baseline: average position)"] + [f for f, _, _ in FEATURES],
                         "bp_per_1sd": b, "t_stat": b / se})
