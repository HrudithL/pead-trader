"""RESEARCH.md's selective earnings portfolio, using its own multi-leg ledger.

The registered strategy uses strict provenance and therefore skips unverifiable
events. The dedicated `earnings-options backtest --exploratory` command exposes
proxy research explicitly. Neither route establishes an investable edge.
"""
from dataclasses import asdict
from pead.strategies.base import Strategy, BacktestResult
from pead.strategies.registry import register
from pead.earnings_options.engine import Config,run


@register
class SelectiveEarningsOptions(Strategy):
    name="selective_earnings_options"
    asset_class="options"
    family="selective_earnings"
    status="research"
    live_ready=False
    description="Cost-aware directional verticals, residual-IV condors and next-event reversal verticals."
    thesis="Trade earnings information only when prior-matured scenarios justify executable option costs."
    config=Config()

    def params(self):
        return asdict(self.config)

    def run(self,ctx):
        from pead.core.metrics import summarize
        import pandas as pd
        e,q,b=ctx.earnings_options()
        r=run(e,q,b,self.config)
        daily=r.daily.copy()
        prior=daily.nav.shift(1,fill_value=self.config.initial_nav)
        daily["daily_pnl"]=daily.nav-prior
        daily["daily_return"]=daily.ret
        summary=summarize(daily,self.config.initial_nav)
        summary.update(investment_accepted=False,execution_diagnostics=r.diagnostics,
                       trade_count=len(r.trades),note="Strict research; inspect dedicated earnings-options report and skip audit.")
        quarters=daily.assign(quarter=daily.date.dt.to_period("Q").astype(str)).groupby("quarter").daily_pnl.sum().reset_index()
        meta=self.meta()|dict(execution_diagnostics=r.diagnostics,skip_counts=r.skips.reason.value_counts().to_dict())
        return BacktestResult(self.name,"options",daily,summary,quarters,r.trades,meta)
