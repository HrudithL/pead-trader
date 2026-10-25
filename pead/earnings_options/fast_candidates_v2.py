"""Array implementation of the reviewed finite candidate menu."""
from dataclasses import replace
import numpy as np
import pandas as pd
from .structures import Leg,Structure
from .liquidity_v2 import LiquidityPolicy


def enumerate_structures(chain,sleeve,direction=1,policy=LiquidityPolicy()):
    if sleeve not in ('directional','condor') or direction not in (1,-1):raise ValueError('invalid candidate family')
    if chain.empty:return []
    names=['optionid','strike','bid','ask','delta','prior_volume','prior_oi']
    a=chain[names].to_numpy(dtype=float,na_value=np.nan)
    order=np.argsort(a[:,0],kind='stable');a=a[order]
    ids,strikes,bid,ask,delta,volume,oi=a.T
    cp=chain.cp.to_numpy(dtype=str)[order]
    expiry=pd.to_datetime(chain.expiry).to_numpy(dtype='datetime64[D]')[order]
    date=pd.to_datetime(chain.date).to_numpy(dtype='datetime64[D]')[order]
    dte=(expiry-date).astype('timedelta64[D]').astype(float)
    standard=chain.standard_contract.fillna(False).to_numpy(dtype=bool)[order]
    low,high=(21,60) if sleeve=='condor' else (35,90)
    spread=ask-bid;mid=(ask+bid)/2
    common=np.isfinite(a).all(axis=1)&standard&(bid>=0)&(ask>0)&(spread>=0)&(oi>=policy.min_oi)&(np.abs(delta)>0)&(np.abs(delta)<=1)&(spread<=policy.max_absolute_spread)&(dte>=low)&(dte<=high)
    with np.errstate(divide='ignore',invalid='ignore'):
        base=common&(spread/mid<=policy.max_relative_spread)
        wings=common&(spread/mid<=2.)
    possible=np.flatnonzero(base)
    if not len(possible):return []
    target=35 if sleeve=='condor' else 50
    best=min(possible,key=lambda i:(abs(dte[i]-target),expiry[i]))
    exp=expiry[best];base &= expiry==exp;wings &= expiry==exp
    caps=np.minimum(np.floor(policy.oi_participation*oi),np.maximum(policy.small_lot_floor,np.floor(policy.participation*volume)))
    indices={};cached={}
    def pick(flag,target_delta,quantity,protection=False):
        key=(flag,target_delta,quantity,protection)
        if key in cached:return cached[key]
        valid=(wings if protection else base)&(cp==flag)&(delta*(1 if flag=='C' else -1)>0)
        positions=np.flatnonzero(valid)
        if not len(positions):return None
        i=positions[np.argmin(np.abs(np.abs(delta[positions])-target_delta))]
        if abs(abs(delta[i])-target_delta)>.20:return None
        leg=Leg(int(ids[i]),quantity,flag,float(strikes[i]),pd.Timestamp(exp),float(bid[i]),float(ask[i]),float(delta[i]),float(volume[i]))
        indices[leg.optionid]=i;cached[key]=leg
        return leg
    specs=[]
    if sleeve=='directional':
        sign='bull' if direction==1 else 'bear';flag='C' if direction==1 else 'P';other='P' if direction==1 else 'C'
        for long,short in ((.65,.30),(.85,.50),(.85,.20)):
            specs.append((f'{sign}_{flag}_{int(long*100)}_{int(short*100)}',(pick(flag,long,1),pick(flag,short,-1)),False))
        for short,long in ((.30,.10),(.50,.20)):
            specs.append((f'{sign}_{other}_credit_{int(short*100)}_{int(long*100)}',(pick(other,long,1,True),pick(other,short,-1)),True))
    else:
        for short,long in ((.20,.05),(.30,.10)):
            specs.append((f'condor_{int(short*100)}_{int(long*100)}',(pick('P',long,1,True),pick('P',short,-1),pick('C',short,-1),pick('C',long,1,True)),True))
    out=[];seen=set()
    for key,legs,credit in specs:
        if any(x is None for x in legs):continue
        legids=tuple(x.optionid for x in legs)
        if len(set(legids))!=len(legids) or legids in seen:continue
        if sleeve=='condor':
            lp,sp,sc,lc=legs
            if not lp.strike<sp.strike<sc.strike<lc.strike:continue
            width=max(sp.strike-lp.strike,lc.strike-sc.strike)
        else:
            long,short=legs
            if direction*(short.strike-long.strike)<=0:continue
            width=abs(short.strike-long.strike)
        cf=sum(-x.quantity*(x.ask if x.quantity>0 else x.bid) for x in legs)
        if credit:
            if not 0<cf<width:continue
            loss=width-cf
        else:
            if not -width<cf<0:continue
            loss=-cf
        friction=(sum(x.ask-x.bid for x in legs)+2*.65*len(legs)/100)/loss
        if friction>.30 or (not credit and -cf/width<.10):continue
        cap=int(min(caps[indices[i]] for i in legids))
        if cap<1:continue
        seen.add(legids)
        out.append(dict(key=key,structure=Structure(sleeve,legs,cf,loss,pd.Timestamp(exp)),capacity=cap,friction=friction))
    return sorted(out,key=lambda x:x['key'])
