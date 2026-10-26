"""Known-date avoidance of nonregular actions; no inferred adjusted multipliers."""
from pathlib import Path
import pandas as pd
import numpy as np

SUPPLEMENTS=[
    dict(secid=109348,declare_date='1999-04-20',ex_date='1999-05-11',action='split',
         source='https://www.qualcomm.com/media/documents/files/qc-earnings-release-q2-1999.pdf'),
    dict(secid=8690,declare_date='1999-10-07',ex_date='1999-11-22',action='pending_merger',
         source='https://www.abbottinvestor.com/static-files/ae83411b-2d4b-4461-9684-465c6c302995'),
]

def action_calendar(calendar):
    raw=pd.read_parquet('D:/OptionMetrics/parquet/distrd.parquet')
    kind=raw.distr_type.astype(str)
    # Never condition on final cancellation status: it was not known when declared.
    mask=kind.isin(['2','3','5','6','7','8','9'])|(pd.to_numeric(raw.link_secid,errors='coerce')>0)|raw.liquid_flag.astype(str).eq('1')
    d=raw.loc[mask,['secid','declare_date','ex_date','distr_type']].copy()
    d=d.rename(columns={'distr_type':'action'});d['source']='native OptionMetrics distrd'
    d=pd.concat([d,pd.DataFrame(SUPPLEMENTS)],ignore_index=True)
    for col in ('declare_date','ex_date'):d[col]=pd.to_datetime(d[col],errors='coerce')
    d=d[d.declare_date.notna()&d.ex_date.notna()&(d.declare_date>=pd.Timestamp('1995-01-01'))]
    sessions=pd.DatetimeIndex(calendar).sort_values().unique()
    loc=sessions.searchsorted(d.declare_date,side='right')
    good=loc<len(sessions);d=d.loc[good].copy();loc=loc[good]
    d['known_date']=sessions[loc];d['effective_date']=d.ex_date
    # A declaration arriving after the action cannot justify a pre-action exit.
    d=d[d.known_date<=d.effective_date].copy()
    return d[['secid','known_date','effective_date','action','source']].drop_duplicates().sort_values(['known_date','secid']).reset_index(drop=True)
