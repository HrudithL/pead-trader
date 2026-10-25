"""Finite menu of complete payoff shapes; no future prices in construction."""
from dataclasses import replace
import json
import numpy as np
import pandas as pd
from .structures import Leg,Structure
from .liquidity_v2 import LiquidityPolicy,eligible,capacity


def enumerate_structures(chain,sleeve,direction=1,policy=LiquidityPolicy()):
    if sleeve not in ('directional','condor') or direction not in (1,-1):raise ValueError('invalid candidate family')
    if chain.empty:return []
    c=chain.copy();c['dte']=(pd.to_datetime(c.expiry)-pd.to_datetime(c.date)).dt.days
    c=c[c.dte.between(21 if sleeve=='condor' else 35,60 if sleeve=='condor' else 90)]
    wide=replace(policy,max_relative_spread=2.)
    base=eligible(c,policy);wings=eligible(c,wide)
    target=35 if sleeve=='condor' else 50
    expiries=base[['expiry','dte']].drop_duplicates().assign(distance=lambda x:(x.dte-target).abs())
    out=[];seen=set()
    # One expiry keeps the research search finite and reduces correlated variants.
    for expiry in expiries.sort_values(['distance','expiry']).expiry.head(1):
        g=base[base.expiry==expiry];w=wings[wings.expiry==expiry]
        tables={}
        for protection,frame in ((False,g),(True,w)):
            ordered=list(frame.sort_values('optionid').itertuples(index=False))
            for cp in ('C','P'):
                tables[(protection,cp)]=[r for r in ordered if r.cp==cp and r.delta*(1 if cp=='C' else -1)>0]
        cached={}
        def pick(cp,target_delta,quantity,protection=False):
            key=(cp,target_delta,quantity,protection)
            if key in cached:return cached[key]
            rows=tables[(protection,cp)]
            if not rows:return None
            r=min(rows,key=lambda x:(abs(abs(x.delta)-target_delta),x.optionid))
            if abs(abs(r.delta)-target_delta)>.20:return None
            result=Leg(int(r.optionid),quantity,cp,float(r.strike),pd.Timestamp(expiry),float(r.bid),float(r.ask),float(r.delta),float(r.prior_volume))
            cached[key]=result
            return result
        lot_caps={int(r.optionid):max(0,int(min(np.floor(wide.oi_participation*r.prior_oi),max(wide.small_lot_floor,np.floor(wide.participation*r.prior_volume))))) for r in w.itertuples(index=False)}
        specs=[]
        if sleeve=='directional':
            sign='bull' if direction==1 else 'bear';cp='C' if direction==1 else 'P';other='P' if direction==1 else 'C'
            for long,short in ((.65,.30),(.85,.50),(.85,.20)):
                specs.append((f'{sign}_{cp}_{int(long*100)}_{int(short*100)}',(pick(cp,long,1),pick(cp,short,-1)),False))
            for short,long in ((.30,.10),(.50,.20)):
                specs.append((f'{sign}_{other}_credit_{int(short*100)}_{int(long*100)}',(pick(other,long,1,True),pick(other,short,-1)),True))
        else:
            for short,long in ((.20,.05),(.30,.10)):
                specs.append((f'condor_{int(short*100)}_{int(long*100)}',
                    (pick('P',long,1,True),pick('P',short,-1),pick('C',short,-1),pick('C',long,1,True)),True))
        for key,legs,credit in specs:
            if any(x is None for x in legs):continue
            ids=tuple(x.optionid for x in legs)
            if len(set(ids))!=len(ids) or ids in seen:continue
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
            # Whole-structure friction replaces arbitrary per-wing premium floors.
            friction=(sum(x.ask-x.bid for x in legs)+2*.65*len(legs)/100)/loss
            if friction>.30 or (not credit and -cf/width<.10):continue
            cap=min(lot_caps.get(i,0) for i in ids)
            if cap<1:continue
            seen.add(ids)
            s=Structure(sleeve,legs,cf,loss,pd.Timestamp(expiry))
            out.append(dict(key=key,structure=s,capacity=cap,friction=friction))
    return sorted(out,key=lambda x:x['key'])


def pack(structure):
    return json.dumps([dict(optionid=x.optionid,quantity=x.quantity,cp=x.cp,strike=x.strike,
                           expiry=str(x.expiry.date()),bid=x.bid,ask=x.ask,delta=x.delta,volume=x.volume)
                       for x in structure.legs],sort_keys=True)


def unpack(row):
    legs=tuple(Leg(**(d|dict(expiry=pd.Timestamp(d['expiry'])))) for d in json.loads(row.legs))
    return Structure(row.sleeve,legs,float(row.cashflow),float(row.max_loss),pd.Timestamp(row.expiry))
