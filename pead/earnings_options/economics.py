"""Prior-matured empirical return scenarios plus an explicit residual-IV assumption.

This is a research valuation model, not observed executable future option prices.
Only realized, matured stock outcomes train it; evaluation uses actual option quotes.
"""
import numpy as np
import pandas as pd
from scipy.special import ndtr


def option_value(spot,strike,years,vol,cp):
    spot=np.asarray(spot,dtype=float)
    sign=1 if cp=="C" else -1
    if years<=0: return np.maximum(sign*(spot-strike),0.)
    sigma=max(float(vol),.01)
    denom=sigma*np.sqrt(years)
    d1=(np.log(spot/strike)+.5*sigma*sigma*years)/denom
    d2=d1-denom
    return sign*(spot*ndtr(sign*d1)-strike*ndtr(sign*d2))


def expected_edge(structure,spot,rv,entry_date,history,hold_sessions=20,min_samples=50,commission=.65):
    if not np.isfinite(spot) or spot<=0 or not np.isfinite(rv) or rv<=0: return np.nan
    ret_col=f"forward_return_{hold_sessions}"
    end_col=f"label_end_{hold_sessions}"
    if ret_col not in history:
        if hold_sessions!=20:return np.nan
        ret_col,end_col="forward_return","label_end"
    h=history[pd.to_datetime(history[end_col])<pd.Timestamp(entry_date)].copy()
    if structure.sleeve=="directional":
        positive=structure.legs[0].cp=="C"
        h=h[(h["rank"]>=.90)&(h.reaction>0)] if positive else h[(h["rank"]<=.10)&(h.reaction<0)]
    elif structure.sleeve=="reversal":
        # Reversal requires independently trained event-window labels and a
        # historical calendar. Ordinary post-event 20-day labels cannot train it.
        return np.nan
    values=h[ret_col].to_numpy(dtype=float)
    values=values[np.isfinite(values)&(values>=-1)]
    if len(values)<min_samples: return np.nan
    remaining=max(0.,((structure.expiry-pd.Timestamp(entry_date)).days-hold_sessions*365.25/252)/365.25)
    terminal=np.maximum(spot*(1+values),1e-12)
    modeled=sum(x.quantity*option_value(terminal,x.strike,remaining,rv,x.cp) for x in structure.legs)
    # Charge an exit spread equal to the currently observed leg spread sum.
    # This is deliberately more costly than liquidation at the theoretical mid.
    exit_spread=sum(x.ask-x.bid for x in structure.legs)/2
    fees=2*len(structure.legs)*commission/100
    expected_pnl=structure.cashflow+float(np.mean(modeled))-exit_spread-fees
    return expected_pnl/structure.max_loss
