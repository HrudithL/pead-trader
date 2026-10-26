"""Frozen risk preferences and strictly prior-information option selection."""
from dataclasses import dataclass, asdict
import hashlib
import json
import numpy as np
import pandas as pd
from pead.earnings_options.structures import Leg, Structure
from pead.earnings_options.liquidity_v2 import LiquidityPolicy, eligible, capacity


def lagged_beta(stock_returns, market_returns, lookback=126, min_periods=60):
    """Clipped covariance beta known before the current session.

    Local-worker draft reviewed: fixed missing index checks, NaN warmup and an
    unshifted variance mask that could otherwise revise a current decision.
    """
    if (not stock_returns.index.equals(market_returns.index)
            or not stock_returns.index.is_unique
            or not stock_returns.index.is_monotonic_increasing
            or not 2 <= min_periods <= lookback):
        raise ValueError('aligned unique sorted returns and valid windows required')
    for s in (stock_returns, market_returns):
        if not np.isfinite(s).all() or (s <= -1).any():
            raise ValueError('finite returns greater than -1 required')
    cov=stock_returns.rolling(lookback,min_periods=min_periods).cov(market_returns)
    var=market_returns.rolling(lookback,min_periods=min_periods).var()
    return (cov/var.where(var>1e-15)).clip(0.,1.).shift(1).fillna(0.)


LIQUIDITY=LiquidityPolicy(min_oi=100,max_relative_spread=.50,
    max_absolute_spread=5.,participation=.05,oi_participation=.02,small_lot_floor=0)


@dataclass(frozen=True)
class Policy:
    name: str
    carry: bool=False
    require_hedge: bool=False
    equity_scale: float=0.
    hedge: bool=False
    ticket: float=.05
    aggregate: float=.20
    expiry_limit: float=.10
    annual_premium: float=.01
    market_vol_target: float=0.
    stock_vol_target: float=.12
    gross_target: float=0.
    min_iv_rv: float=0.
    hedge_cover: float=.25
    financing_spread: float=.02
    commission: float=1.50
    slippage: float=0.
    small_lot_floor: int=0
    exit_on_signal: bool=True

    def __post_init__(self):
        if not self.name or not all(np.isfinite(v) for v in asdict(self).values()
                                   if isinstance(v,(float,int))):
            raise ValueError('finite named policy required')
        if not 0 < self.ticket <= self.expiry_limit <= self.aggregate <= 1:
            raise ValueError('invalid carry risk hierarchy')
        if not 0<=self.equity_scale<=1.5 or not 0<=self.annual_premium<=.1:
            raise ValueError('invalid equity scale or premium budget')
        if not 0<=self.gross_target<=1.2:raise ValueError('gross target outside study limits')
        if self.min_iv_rv<0:raise ValueError('invalid compensation threshold')
        if min(self.commission,self.slippage,self.financing_spread,self.market_vol_target,self.stock_vol_target)<0:
            raise ValueError('negative costs or volatility target')
        if not 0<=self.hedge_cover<=1 or self.small_lot_floor not in (0,1):
            raise ValueError('invalid coverage/capacity floor')
        if self.require_hedge and not (self.carry and self.hedge):
            raise ValueError('protected carry requires both carry and hedge')

    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True).encode()).hexdigest()


def hedge_menu(chain, commission=1.5, liquidity=LIQUIDITY):
    """Long 25 / short 10 prior-delta monthly puts; no current Greek fallback."""
    if 'prior_delta' not in chain:return []
    q=eligible(chain.assign(delta=chain.prior_delta),liquidity)
    q=q[(q.cp=='P') & (q.delta<0) &
        (q.expiry-q.date).dt.days.between(60,120) &
        (q.expiry.dt.day_of_week==5) & q.expiry.dt.day.between(16,22)]
    choices=[]
    for expiry,g in q.groupby('expiry',sort=True):
        longs=g.assign(distance=(g.delta.abs()-.25).abs()).sort_values(['distance','optionid']).head(4)
        shorts=g.assign(distance=(g.delta.abs()-.10).abs()).sort_values(['distance','optionid']).head(4)
        for lo in longs.itertuples():
            for sh in shorts.itertuples():
                debit=lo.ask-sh.bid; width=lo.strike-sh.strike
                if (width<=0 or not 0<debit<width or abs(abs(lo.delta)-.25)>.08
                        or abs(abs(sh.delta)-.10)>.05):continue
                cap=capacity(g[g.optionid.isin([lo.optionid,sh.optionid])],liquidity)
                if cap<1:continue
                legs=tuple(Leg(int(r.optionid),qty,'P',float(r.strike),expiry,
                    float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
                    for r,qty in ((lo,1),(sh,-1)))
                key=(abs((expiry-lo.date).days-90),abs(abs(lo.delta)-.25)+abs(abs(sh.delta)-.10),
                     (lo.ask-lo.bid+sh.ask-sh.bid+4*commission/100)/debit,
                     int(lo.optionid),int(sh.optionid))
                choices.append((key,Structure('hedge',legs,-debit,debit,expiry),cap))
    return [(st,cap) for _,st,cap in sorted(choices,key=lambda x:x[0])]


def attach_prior_iv(quotes,calendar):
    """Lag IV only across adjacent real sessions; no gap/year forward fill."""
    q=quotes.copy()
    if q.duplicated(['date','optionid']).any():raise ValueError('duplicate option keys')
    q['_original_order']=np.arange(len(q));q=q.sort_values(['optionid','date'])
    cal=pd.DatetimeIndex(calendar);ix=cal.get_indexer(q.date)
    if (ix<0).any():raise ValueError('quote date outside actual market calendar')
    group=q.groupby('optionid',sort=False)
    aligned=(ix>0)&(group.date.shift().to_numpy()==cal[np.maximum(ix-1,0)].to_numpy())
    q['prior_iv']=np.where(aligned,group.iv.shift(),np.nan)
    return q.sort_values('_original_order').drop(columns='_original_order')
