"""PortfolioStrategy: a fixed-weight, daily-rebalanced blend of other registered strategies.

A sleeve weight is a multiple of fund NAV allocated to that strategy's own sizing: weight 1.0 runs the
strategy exactly as backtested on the whole NAV, 0.8 runs it 20% smaller. Weights need not sum to one --
the sleeves are low-correlation books that each use only part of their capital (the options sleeve spends
at most its premium budget, the equity sleeve is margin-financed long/short), so they can share one NAV.
Returns compound daily: r_p = sum(w_i r_i), NAV_t = NAV_{t-1} (1 + r_p).

Sleeve results come from the strategy registry itself (re-run in-process, ~seconds), so the blend always
reflects the current sleeve definitions. Days with no sleeve activity (options in 2011, where the source
file is corrupt) contribute zero for that sleeve.
"""
import numpy as np
import pandas as pd

from pead.common.paths import RAW_EQUITY_DIR
from pead.core.metrics import summarize
from pead.strategies.base import BacktestResult, Strategy
from pead.strategies.registry import get, register_instance


class PortfolioStrategy(Strategy):
    asset_class = "portfolio"
    family = "portfolio"

    def __init__(self, name, weights: dict, description, thesis, parent=None, status="validated"):
        self.name, self.sleeve_weights, self.parent, self.status = name, weights, parent, status
        self.description, self.thesis = description, thesis

    def params(self):
        return {f"w_{k}": v for k, v in self.sleeve_weights.items()}

    def run(self, ctx):
        sleeves = {k: get(k).run(ctx) for k in self.sleeve_weights}
        equity_first = sorted(sleeves, key=lambda k: sleeves[k].asset_class != "equity")
        idx = pd.DatetimeIndex(sleeves[equity_first[0]].daily["date"])
        rets = pd.DataFrame({k: v.daily.set_index("date")["daily_return"].reindex(idx) for k, v in sleeves.items()}).fillna(0.0)
        w = pd.Series(self.sleeve_weights)
        r = rets @ w
        cap0 = ctx.equity().initial_capital
        nav = cap0 * (1 + r).cumprod()
        prior = np.concatenate([[cap0], nav.to_numpy()[:-1]])
        daily = pd.DataFrame({"date": idx, "daily_pnl": prior * r.to_numpy(), "nav": nav.to_numpy(), "daily_return": r.to_numpy()})
        for k in sleeves:
            daily[f"pnl_{k}"] = prior * (rets[k] * w[k]).to_numpy()
        summary = summarize(daily, cap0)
        mk = pd.read_parquet(RAW_EQUITY_DIR / "market_benchmark.parquet", columns=["date", "vwretd"]).drop_duplicates("date").set_index("date")["vwretd"].reindex(idx).fillna(0)
        summary["market_beta"] = float(np.cov(r, mk, ddof=1)[0, 1] / mk.var(ddof=1))
        summary["corr_market"] = float(np.corrcoef(r, mk)[0, 1])
        summary["sleeve_correlation"] = float(rets.corr().iloc[0, 1]) if rets.shape[1] == 2 else None
        return BacktestResult(self.name, "portfolio", daily, summary, pd.DataFrame(), None, self.meta())


register_instance(PortfolioStrategy(
    "hedged_blend",
    {"s9b_inverse_vol": 0.8, "opt_kelly_cap20": 1.6},
    "Market-neutral inverse-vol equity book (0.8x) + corrected-quote Kelly options book (1.6x), on one NAV",
    "The sleeves are uncorrelated with each other (about -0.05) and with the market (~0 / 0.18): equity alpha earned most "
    "of its return in 1996-2003 and made +10% in 2008 when the options book lost; options earned most after 2003. "
    "Weights set so each sleeve contributes equal stand-alone risk, scaled to ~5% portfolio volatility.",
    parent="s9b_inverse_vol"))

register_instance(PortfolioStrategy(
    "hedged_blend_s10",
    {"s10_vol_rank_tilt": 0.9, "opt_kelly_cap20": 1.6},
    "The same blend with the stronger research equity sleeve (s10_vol_rank_tilt: Sharpe 1.8)",
    "As hedged_blend, with the volatility-rank equity book as the alpha sleeve.",
    parent="hedged_blend", status="research"))

register_instance(PortfolioStrategy(
    "hedged_blend_legacy_options",
    {"s9b_inverse_vol": 0.8, "opt_kelly_cap20_legacy": 1.15},
    "The blend with the options book as originally reported (dead quotes carried forward) -- an overstated upper bound",
    "Shows how much of the blend's Sharpe came from the options sleeve's quote-marking convention.",
    parent="hedged_blend", status="reference"))
