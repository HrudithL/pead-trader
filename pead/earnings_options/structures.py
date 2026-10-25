"""Same-expiry, standard 100-share structures selected from entry-time quotes."""
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Leg:
    optionid: int
    quantity: int
    cp: str
    strike: float
    expiry: pd.Timestamp
    bid: float
    ask: float
    delta: float
    volume: float


@dataclass(frozen=True)
class Structure:
    sleeve: str
    legs: tuple[Leg,...]
    cashflow: float
    max_loss: float
    expiry: pd.Timestamp


def select_structure(chain, sleeve, direction=1):
    if sleeve not in ("directional","condor","reversal") or direction not in (1,-1):
        raise ValueError("unsupported sleeve or direction")
    if chain.empty: return None
    c=chain.copy()
    c["date"]=pd.to_datetime(c.date)
    c["expiry"]=pd.to_datetime(c.expiry)
    numeric=["optionid","strike","bid","ask","delta","prior_volume","prior_oi"]
    finite=np.isfinite(c[numeric].to_numpy(dtype=float)).all(axis=1)
    mid=(c.bid+c.ask)/2
    c=c[finite & (c.bid>=0)&(c.ask>=c.bid)&(c.ask>0)&(mid>=.5)&
        ((c.ask-c.bid)/mid<=.10)&(c.prior_volume>=50)&(c.prior_oi>=250)].copy()
    low,high,target={"directional":(30,60,45),"condor":(14,35,25),"reversal":(3,10,7)}[sleeve]
    c["dte"]=(c.expiry-c.date).dt.days
    c=c[c.dte.between(low,high)]
    expiries=c[["expiry","dte"]].drop_duplicates().assign(distance=lambda x:(x.dte-target).abs())
    for exp in expiries.sort_values(["distance","expiry"]).expiry:
        g=c[c.expiry==exp]

        def pick(cp,quantity,lo,hi,target_delta):
            z=g[(g.cp==cp)&g.delta.abs().between(lo,hi)&(g.delta*(1 if cp=="C" else -1)>0)].copy()
            if z.empty: return None
            z["distance"]=(z.delta.abs()-target_delta).abs()
            r=z.sort_values(["distance","optionid"]).iloc[0]
            return Leg(int(r.optionid),quantity,cp,float(r.strike),exp,float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))

        if sleeve=="condor":
            legs=(pick("P",1,.05,.10,.075),pick("P",-1,.15,.20,.175),
                  pick("C",-1,.15,.20,.175),pick("C",1,.05,.10,.075))
            if any(x is None for x in legs): continue
            lp,sp,sc,lc=legs
            if not lp.strike<sp.strike<sc.strike<lc.strike: continue
            width=max(sp.strike-lp.strike,lc.strike-sc.strike)
        else:
            cp="C" if direction==1 else "P"
            bounds=(.55,.65,.60) if sleeve=="directional" else (.50,.60,.55)
            legs=(pick(cp,1,*bounds),pick(cp,-1,.25,.35,.30))
            if any(x is None for x in legs): continue
            if direction*(legs[1].strike-legs[0].strike)<=0: continue
            width=abs(legs[1].strike-legs[0].strike)
        if len({x.optionid for x in legs})!=len(legs): continue
        cashflow=sum(-x.quantity*(x.ask if x.quantity>0 else x.bid) for x in legs)
        if sleeve=="condor":
            if not 0<cashflow<width: continue
            max_loss=width-cashflow
        else:
            if not -width<cashflow<0: continue
            max_loss=-cashflow
        return Structure(sleeve,legs,cashflow,max_loss,exp)
    return None
