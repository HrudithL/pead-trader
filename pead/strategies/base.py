"""The Strategy contract.

A strategy is ONE module that defines ONE class (plus, optionally, a few parameterized instances).
It declares what it trades and how it sizes; it never loads data, schedules threads, writes files or
draws charts -- the runner, engines and reporting package do all of that. That separation is what
lets the same strategy file run in a backtest, in a parameter sweep, in the cross-strategy report
and in live paper trading.

    class MyStrategy(EquityStrategy):
        name = "my_strategy"
        sizing = Sizing(base_unit_fraction=0.003, max_gross_leverage=2.5)
        def weights(self, events): ...      # signed weight per earnings event

Register it with `@register` (see registry.py) and it is runnable as `python -m pead run my_strategy`.
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from pead.core.data import EquityUniverse, OptionsUniverse, load_equity_universe, load_options_universe
from pead.engines.equity import EquityRun, Sizing, run_equity_backtest, apply_beta_overlay


@dataclass
class BacktestResult:
    """Everything a run produces; reporting and storage consume only this."""
    name: str
    asset_class: str                       # 'equity' | 'options'
    daily: pd.DataFrame                    # date, daily_pnl, nav, daily_return (+ engine extras)
    summary: dict
    quarterlog: pd.DataFrame
    positions: Optional[pd.DataFrame] = None   # funded positions with per-position P&L
    meta: dict = field(default_factory=dict)   # strategy metadata (description, params, lineage...)


class RunContext:
    """Lazily-loaded shared inputs for one worker process."""

    def __init__(self, mock: bool = False):
        self.mock = mock

    def equity(self) -> EquityUniverse:
        return load_equity_universe()

    def options(self) -> OptionsUniverse:
        return load_options_universe()


class Strategy:
    """Common metadata. `thesis` / `edge_source` feed the generated strategy report."""
    name: str = ""
    asset_class: str = ""
    family: str = ""                       # grouping label in comparison tables
    parent: Optional[str] = None           # the strategy this one was derived from (lineage)
    status: str = "validated"              # validated | research | unverified | reference
    description: str = ""
    thesis: str = ""                       # why this should make money
    live_ready: bool = False               # True when the live engine can run it

    def params(self) -> dict:
        return {}

    def meta(self) -> dict:
        import sys
        module_doc = (sys.modules[type(self).__module__].__doc__ or "").strip()
        return dict(module_doc=module_doc, name=self.name, asset_class=self.asset_class, family=self.family, parent=self.parent,
                    status=self.status, description=self.description, thesis=self.thesis,
                    params=self.params(), live_ready=self.live_ready)

    def run(self, ctx: RunContext) -> BacktestResult:
        raise NotImplementedError


class EquityStrategy(Strategy):
    """A book of earnings-event positions on the equity engine.

    Subclasses implement `weights(events)`. `events` is a DataFrame of earnings events carrying at
    least ann_quarter, decile, sue_rank_pct, size_quintile, ff12_sector, liquidity_quintile and
    avg_dollar_volume_21d (plus trailing-vol columns for volatility-aware strategies). Because it is
    a pure function of those columns, the live engine calls it unchanged on today's reporters."""
    asset_class = "equity"
    sizing: Sizing = Sizing()
    beta_overlay: float = 0.0              # market-index position as a multiple of NAV, 0 = none
    required_columns: tuple = ("ann_quarter", "sue_rank_pct", "size_quintile", "ff12_sector")

    def weights(self, events: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def params(self) -> dict:
        s = self.sizing
        return dict(base_unit_fraction=s.base_unit_fraction, max_gross_leverage=s.max_gross_leverage,
                    liquidity_cap_frac=s.liquidity_cap_frac, return_basis=s.return_basis,
                    cap_mode=s.cap_mode, beta_overlay=self.beta_overlay)

    def backtest(self, uni: EquityUniverse) -> EquityRun:
        run = run_equity_backtest(uni, self.weights(uni.events), self.sizing)
        if self.beta_overlay:
            run = apply_beta_overlay(run, uni.vwretd, uni.initial_capital, beta=self.beta_overlay)
        return run

    def run(self, ctx: RunContext) -> BacktestResult:
        run = self.backtest(ctx.equity())
        return BacktestResult(self.name, "equity", run.daily, run.summary, run.quarterlog,
                              run.positions, self.meta())


class OptionsStrategy(Strategy):
    """A book of options bought around earnings events, marked to market at checkpoints."""
    asset_class = "options"

    def run(self, ctx: RunContext) -> BacktestResult:
        raise NotImplementedError
