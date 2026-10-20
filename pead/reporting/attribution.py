"""Where does a strategy's return come from?

Equity books are long the best earnings surprises and short the worst, and every position's return is
measured against the cap-weighted market. That makes the headline long/short split misleading on its
own: the average event stock beats the cap-weighted market by ~1.5% over a holding window (small-cap
tilt + the earnings-announcement premium), so the LONG leg is flattered and the SHORT leg is penalized
by the same amount regardless of any earnings signal. Every table here therefore splits a position's P&L
into two parts:

    common  = notional x (mean holding-window return of ALL events announced that quarter)
              what the book earns just from being long/short the average event stock
    excess  = notional x (this event's return - that quarter mean)
              the PEAD-specific part: did the surprise rank actually predict relative performance
    alpha_net = excess - trading costs

The common part nets to ~zero across a dollar-neutral book (it is +x on the long leg and -x on the
short leg); the excess part is the signal. Options books are split by contract side, moneyness, the
stock's volatility and premium size instead.
"""
import numpy as np
import pandas as pd

from pead.core.metrics import yearly_table

MONEYNESS_BINS = [0, 0.8, 0.9, 0.97, 1.03, 1.1, 1.2, 10]
MONEYNESS_LABELS = ["deep ITM <0.8", "ITM 0.8-0.9", "ITM 0.9-0.97", "ATM 0.97-1.03", "OTM 1.03-1.1",
                    "OTM 1.1-1.2", "far OTM >1.2"]


# ------------------------------------------------------------------------------------------------
# equity
# ------------------------------------------------------------------------------------------------
def equity_positions_with_alpha(result, events: pd.DataFrame = None) -> pd.DataFrame:
    """result.positions + common / excess / alpha_net columns and era/year/bucket labels."""
    p = result.positions.copy()
    if events is None:
        from pead.core.data import load_equity_universe
        events = load_equity_universe().events
    qmean = events.groupby("ann_quarter")["hold_ret"].mean()
    p["common"] = p["notional"] * p["ann_quarter"].map(qmean)
    p["excess"] = p["notional"] * (p["hold_ret"] - p["ann_quarter"].map(qmean))
    p["alpha_net"] = p["excess"] - p["cost"]
    p["year"] = p["day0_date"].dt.year
    p["era"] = pd.cut(p["year"], [1994, 2001, 2007, 2015], labels=["1996-01", "2002-07", "2008-13"])
    p["hold_bucket"] = pd.cut(p["holding_days"], [0, 40, 60, 90, 1000], labels=["<=40d", "41-60d", "61-90d", ">90d"])
    p["leg"] = np.where(p["side"] > 0, "long", "short")
    if "ivol_252d_tercile" in p.columns:
        p["vol_tercile"] = p["ivol_252d_tercile"].map({0: "low vol", 1: "mid vol", 2: "high vol"})
    return p


def _agg(p: pd.DataFrame, by, total_alpha: float) -> pd.DataFrame:
    g = p.groupby(by, observed=True)
    t = g.agg(positions=("net_pnl", "size"), notional_M=("notional", lambda v: v.abs().sum() / 1e6),
              net_pnl_M=("net_pnl", lambda v: v.sum() / 1e6), common_M=("common", lambda v: v.sum() / 1e6),
              excess_M=("excess", lambda v: v.sum() / 1e6), cost_M=("cost", lambda v: v.sum() / 1e6))
    t["alpha_net_M"] = t["excess_M"] - t["cost_M"]
    t["alpha_share"] = t["alpha_net_M"] / total_alpha if total_alpha else np.nan
    t["alpha_bp_per_$"] = 1e4 * t["alpha_net_M"] / t["notional_M"]
    t["hit_rate"] = g["alpha_net"].apply(lambda v: (v > 0).mean())
    return t.round(3)


def equity_attribution(result, events: pd.DataFrame = None) -> dict:
    """Dict of DataFrames: leg, decile, size, liquidity, sector, era, year, hold, vol (when available)."""
    p = equity_positions_with_alpha(result, events)
    total = (p["excess"] - p["cost"]).sum() / 1e6
    out = {"_positions": p, "leg": _agg(p, "leg", total),
           "decile_leg": _agg(p, ["leg", "decile"], total),
           "size": _agg(p, "size_quintile", total), "liquidity": _agg(p, "liquidity_quintile", total),
           "sector": _agg(p, "ff12_sector", total), "era": _agg(p, "era", total), "year": _agg(p, "year", total),
           "holding": _agg(p, "hold_bucket", total)}
    if "vol_tercile" in p.columns:
        out["vol"] = _agg(p, "vol_tercile", total)
    return out


def leg_summary(result) -> dict:
    """Headline long-vs-short facts used in report text and the cross-strategy table."""
    d, p = result.daily, result.positions
    a = equity_positions_with_alpha(result)
    prior = np.concatenate([[1e7], d["nav"].to_numpy()[:-1]])
    out = {}
    for leg, col in (("long", "pnl_long"), ("short", "pnl_short")):
        x = d[col].to_numpy() / prior
        sub = a[a["leg"] == leg]
        out[leg] = dict(
            pnl_M=float(d[col].sum() / 1e6), net_of_cost_M=float(sub["net_pnl"].sum() / 1e6),
            ann_contrib=float(x.mean() * 252), ann_vol=float(x.std(ddof=1) * np.sqrt(252)),
            sharpe_standalone=float(x.mean() / x.std(ddof=1) * np.sqrt(252)),
            common_M=float(sub["common"].sum() / 1e6), excess_M=float(sub["excess"].sum() / 1e6),
            alpha_net_M=float((sub["excess"] - sub["cost"]).sum() / 1e6),
            gross_bp_per_dollar=float(1e4 * sub["gross_pnl"].sum() / sub["notional"].abs().sum()),
            hit_rate=float((sub["gross_pnl"] > 0).mean()), positions=int(len(sub)))
    out["corr_long_short_daily_pnl"] = float(np.corrcoef(d["pnl_long"], d["pnl_short"])[0, 1])
    return out


def borrow_cost_sensitivity(result, annual_rates=(0.0, 0.005, 0.01, 0.02, 0.05)) -> pd.DataFrame:
    """The engine charges no stock-borrow fee on the short leg. Return what a flat annual fee on short
    notional would take off the strategy's annualized return (hard-to-borrow small caps cost more)."""
    d = result.daily
    years = len(d) / 252
    short_dollar_years = d["short_exposure"].abs().sum() / 252
    rows = []
    for r in annual_rates:
        fee = r * short_dollar_years
        nav_end = d["nav"].iloc[-1] - fee
        ann = (nav_end / 1e7) ** (1 / years) - 1 if nav_end > 0 else -1.0   # fee exceeds the book: wiped out
        rows.append(dict(borrow_rate=r, fee_M=fee / 1e6, annualized_return=ann,
                         drag=result.summary["annualized_return"] - ann))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------
# options
# ------------------------------------------------------------------------------------------------
def options_positions_with_features(result) -> pd.DataFrame:
    """result.positions + moneyness (strike/spot), the stock's pre-event volatility tercile, premium
    bucket and year. Spot comes from the full-chain scan; vol from the 47c event features."""
    from pead.common.paths import DATA_DIR
    p = result.positions.copy()
    p["day0_date"] = pd.to_datetime(p["day0_date"])
    chain = pd.read_parquet(DATA_DIR / "event_options" / "full_chain_with_underlying.parquet",
                            columns=["optionid", "secid", "day0_date", "underlying_price", "dte"])
    chain["day0_date"] = pd.to_datetime(chain["day0_date"])
    chain = chain.drop_duplicates(["optionid", "secid", "day0_date"])
    n0 = len(p)
    p = p.merge(chain, left_on=["kelly_optionid", "secid", "day0_date"],
                right_on=["optionid", "secid", "day0_date"], how="left")
    assert len(p) == n0, "chain merge fanned out"
    p["moneyness"] = p["kelly_strike"] / p["underlying_price"]
    p["moneyness_bucket"] = pd.cut(p["moneyness"], MONEYNESS_BINS, labels=MONEYNESS_LABELS)
    feats_path = DATA_DIR / "event_options" / "event_vol_features.parquet"
    if feats_path.exists():
        f = pd.read_parquet(feats_path, columns=["secid", "day0_date", "tvol_60d", "iv_rv"])
        f["day0_date"] = pd.to_datetime(f["day0_date"])
        p = p.merge(f, on=["secid", "day0_date"], how="left")
        p["vol_tercile"] = pd.qcut(p["tvol_60d"], 3, labels=["low vol", "mid vol", "high vol"])
    p["premium_bucket"] = pd.cut(p["kelly_premium"], [0, 0.25, 0.5, 1, 2, 5, 1000],
                                 labels=["<$0.25", "$0.25-0.5", "$0.5-1", "$1-2", "$2-5", ">$5"])
    p["year"] = p["day0_date"].dt.year
    p["leg"] = np.where(p["kelly_side"] == "C", "call", "put")
    return p


def _agg_opt(p: pd.DataFrame, by) -> pd.DataFrame:
    total = p["net_pnl"].sum()
    g = p.groupby(by, observed=True)
    t = g.agg(trades=("net_pnl", "size"), premium_M=("premium_paid", lambda v: v.sum() / 1e6),
              gross_M=("gross_pnl", lambda v: v.sum() / 1e6), cost_M=("cost", lambda v: v.sum() / 1e6),
              net_M=("net_pnl", lambda v: v.sum() / 1e6), hit_rate=("gross_pnl", lambda v: (v > 0).mean()))
    t["share_of_net"] = 1e6 * t["net_M"] / total if total else np.nan
    t["roi_on_premium"] = t["gross_M"] / t["premium_M"]
    return t.round(3)


def options_attribution(result) -> dict:
    p = options_positions_with_features(result)
    out = {"_positions": p, "leg": _agg_opt(p, "leg"), "decile": _agg_opt(p, "decile"),
           "moneyness": _agg_opt(p, ["leg", "moneyness_bucket"]), "premium": _agg_opt(p, "premium_bucket"),
           "year": _agg_opt(p, "year")}
    if "vol_tercile" in p.columns:
        out["vol"] = _agg_opt(p, "vol_tercile")
        out["vol_moneyness"] = _agg_opt(p[p["leg"] == "call"], ["vol_tercile", "moneyness_bucket"])
    srt = p["gross_pnl"].sort_values(ascending=False)
    n = len(srt)
    out["concentration"] = dict(
        trades=n, hit_rate=float((p["gross_pnl"] > 0).mean()), median_roi=float(p["ret_on_premium"].median()),
        mean_roi=float(p["gross_pnl"].sum() / p["premium_paid"].sum()),
        top1pct_share_of_gross=float(srt.head(max(n // 100, 1)).sum() / srt.sum()),
        top10pct_share_of_gross=float(srt.head(max(n // 10, 1)).sum() / srt.sum()),
        pct_calls=float((p["leg"] == "call").mean()), call_share_of_gross=float(p.loc[p["leg"] == "call", "gross_pnl"].sum() / p["gross_pnl"].sum()))
    return out


def options_execution_sensitivity(strategy_cls_instance, ctx) -> pd.DataFrame:
    """Re-run the same options strategy under harsher, more realistic execution assumptions. The pick
    and the quote history are fixed; only the cost of getting into the contract changes."""
    from dataclasses import replace
    from pead.engines.options import run_options_backtest
    uni = ctx.options()
    base = strategy_cls_instance.sizing
    cases = [("as registered", {}),
             ("ORIGINAL convention: dead quotes carried forward, flat 3% cost",
              dict(dead_quote_mark="carry_last", min_roundtrip_cost_per_share=0.0)),
             ("dead quotes at half-ask, flat 3% cost (no tick floor)", dict(min_roundtrip_cost_per_share=0.0)),
             ("dead quotes worth zero (pessimistic bound)", dict(dead_quote_mark="zero_bid_zero")),
             ("+ $0.10/share minimum cost", dict(min_roundtrip_cost_per_share=0.10)),
             ("skip contracts under $0.25", dict(min_premium=0.25)),
             ("skip contracts under $0.50", dict(min_premium=0.50)),
             ("flat 10% round-trip cost", dict(roundtrip_cost_pct=0.10))]
    rows = []
    for label, kw in cases:
        d, _, pos, s = run_options_backtest(uni.picks, uni.calendar, replace(base, **kw), uni.initial_capital)
        rows.append(dict(case=label, annualized_return=s["annualized_return"], annualized_vol=s["annualized_vol"],
                         sharpe=s["sharpe"], max_drawdown=s["max_drawdown"], trades=s["n_positions_sized"]))
    return pd.DataFrame(rows)
