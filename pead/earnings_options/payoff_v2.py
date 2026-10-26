"""Prior-matured quoted-payoff forecasts, with issuer and date sample controls."""
import numpy as np
import pandas as pd


def _features(frame):
    friction=frame.friction.to_numpy(dtype=float)
    iv=np.log(np.clip(frame.iv_rv.to_numpy(dtype=float),.25,4.))
    signal=frame.signal_bin.to_numpy(dtype=float)/3
    regime=frame.market_regime.to_numpy(dtype=float) if 'market_regime' in frame else np.zeros(len(frame))
    return np.column_stack([friction,iv,signal,signal**2,regime,regime*signal,friction*iv])


def _conditional_scores(history,targets,min_events,z):
    grouped=history.groupby('event_id',sort=False)
    h=grouped.first();h['return_on_risk']=grouped.return_on_risk.mean()
    raw=_features(h);valid=np.isfinite(raw).all(axis=1)&np.isfinite(h.return_on_risk)
    h=h.loc[valid];raw=raw[valid]
    if len(h)<min_events:return None
    mean=raw.mean(axis=0);scale=raw.std(axis=0);scale[scale<1e-8]=1.
    x=np.column_stack([np.ones(len(h)),np.clip((raw-mean)/scale,-3,3)])
    target=_features(targets)
    xp=np.column_stack([np.ones(len(targets)),np.clip((target-mean)/scale,-3,3)])
    penalty=np.eye(x.shape[1])*5.;penalty[0,0]=1e-8
    inverse=np.linalg.inv(x.T@x+penalty)
    y=h.return_on_risk.to_numpy(dtype=float)
    coefficient=inverse@x.T@y
    predicted=np.clip(xp@coefficient,y.min(),y.max())
    sd=float(np.sqrt(np.mean((y-x@coefficient)**2)*len(h)/max(1,len(h)-x.shape[1])))
    effective=min(len(h),max(1,h.secid.nunique()*3),max(1,pd.to_datetime(h.entry_date).nunique()*3))
    uncertainty=sd*np.sqrt(np.maximum(1/effective,np.einsum('ij,jk,ik->i',xp,inverse,xp)))
    score=pd.DataFrame(dict(mean=predicted,lower=predicted-z*uncertainty-.01,
                           events=len(h),effective_events=effective),index=targets.index)
    score.loc[~np.isfinite(target).all(axis=1),'lower']=np.nan
    return score


def forecast(history,target,asof,min_events=50,z=1.0):
    h=history[(pd.to_datetime(history.exit_date)<pd.Timestamp(asof))&
              (history.archetype==target.archetype)].copy()
    h=h[np.isfinite(h.return_on_risk)]
    if h.empty:return None
    if {'friction','iv_rv'}.issubset(h.columns) and 'friction' in target and 'iv_rv' in target:
        result=_conditional_scores(h,pd.DataFrame([target]),min_events,z)
        if result is None:return None
        return result.iloc[0].to_dict()
    # One event contributes once, regardless of duplicate contracts/variants.
    global_values=h.groupby('event_id').return_on_risk.mean()
    if len(global_values)<min_events:return None
    cell=h[(h.signal_bin==target.signal_bin)&(h.vol_bin==target.vol_bin)]
    values=cell.groupby('event_id').return_on_risk.mean()
    if len(values)<min_events:
        values=global_values;cell=h
    n=len(values)
    # Conservative effective n: repeated issuers and earnings-season days do
    # not manufacture independent evidence by adding highly related trades.
    issuers=cell.secid.nunique();days=pd.to_datetime(cell.entry_date).nunique()
    effective=min(n,max(1,issuers*3),max(1,days*3))
    mean=float(values.mean());sd=float(values.std(ddof=1))
    global_mean=float(global_values.mean())
    shrunk=(n*mean+50*global_mean)/(n+50)
    lower=shrunk-z*sd/np.sqrt(effective)-.01
    return dict(mean=shrunk,lower=float(lower),events=int(n),effective_events=int(effective))


def select_decisions(history,candidates,min_events=50,z=1.0,min_edge=.01,retain_all=False):
    if candidates.empty:return candidates.copy()
    rows=[]
    # Predictors are frozen at the beginning of each quarter. No own outcome,
    # same-quarter future outcome, or missing-quote test exclusion can enter.
    for quarter,g in candidates.groupby(pd.to_datetime(candidates.entry_date).dt.to_period('Q'),sort=True):
        cutoff=quarter.start_time
        past=history[(pd.to_datetime(history.exit_date)<cutoff)&(pd.to_datetime(history.entry_date)>=cutoff-pd.DateOffset(years=4))]
        if {'friction','iv_rv'}.issubset(g.columns):
            for archetype,cell in g.groupby('archetype',sort=True):
                h=past[past.archetype==archetype]
                if h.empty:continue
                scores=_conditional_scores(h,cell,min_events,z)
                if scores is None:continue
                scored=cell.assign(predicted_edge=scores.lower,predicted_mean=scores['mean'],training_events=scores.events,model_asof=cutoff)
                rows.extend(scored[scored.predicted_edge>=min_edge].to_dict('records'))
            continue
        for _,cell in g.groupby(['archetype','signal_bin','vol_bin'],sort=True):
            score=forecast(past,cell.iloc[0],cutoff,min_events,z)
            if score is None or score['lower']<min_edge:continue
            scored=cell.assign(predicted_edge=score['lower'],predicted_mean=score['mean'],training_events=score['events'],model_asof=cutoff)
            rows.extend(scored.to_dict('records'))
    if not rows:return candidates.iloc[:0].copy()
    d=pd.DataFrame(rows)
    d=d.sort_values(['event_id','sleeve','predicted_edge','candidate_id'],ascending=[True,True,False,True])
    return (d if retain_all else d.drop_duplicates(['event_id','sleeve'])).reset_index(drop=True)
