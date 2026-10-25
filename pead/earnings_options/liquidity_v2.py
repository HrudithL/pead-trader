"""Configurable quote quality and explicitly assumed small-lot capacity."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class LiquidityPolicy:
    min_oi: float=50
    max_relative_spread: float=.50
    max_absolute_spread: float=1.
    participation: float=.10
    oi_participation: float=.01
    small_lot_floor: int=1

    def __post_init__(self):
        if not np.isfinite([self.min_oi,self.max_relative_spread,self.max_absolute_spread,
                            self.participation,self.oi_participation]).all():
            raise ValueError('finite policy values required')
        if self.min_oi<0 or not 0<self.max_relative_spread<=2 or self.max_absolute_spread<0:
            raise ValueError('invalid quote limits')
        if not 0<self.participation<=1 or not 0<self.oi_participation<=1:
            raise ValueError('invalid participation')
        if not isinstance(self.small_lot_floor,int) or self.small_lot_floor<0:
            raise ValueError('invalid small-lot floor')


def eligible(chain,policy=LiquidityPolicy()):
    finite=np.isfinite(chain[['bid','ask','delta','prior_oi','prior_volume']].to_numpy(dtype=float,na_value=np.nan)).all(axis=1)
    width=chain.ask-chain.bid;mid=(chain.ask+chain.bid)/2
    return chain[finite&(chain.bid>=0)&(chain.ask>0)&(width>=0)&chain.standard_contract.eq(True)&
                 (chain.prior_oi>=policy.min_oi)&(chain.delta.abs()>0)&(chain.delta.abs()<=1)&
                 (width<=policy.max_absolute_spread)&(width/mid<=policy.max_relative_spread)].copy()


def capacity(legs,policy=LiquidityPolicy()):
    if legs.empty or len(eligible(legs,policy))!=len(legs):return 0
    oi=np.floor(policy.oi_participation*legs.prior_oi.to_numpy(dtype=float))
    volume=np.maximum(policy.small_lot_floor,np.floor(policy.participation*legs.prior_volume.to_numpy(dtype=float)))
    return max(0,int(np.minimum(oi,volume).min()))
