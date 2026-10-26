"""Bounded-memory actual quote lookup; retains current/previous year only."""
from collections import OrderedDict
import pandas as pd

REQUIRED={'date','secid','optionid','cp','strike','expiry','bid','ask','delta',
          'prior_volume','prior_oi','iv','standard_contract'}

class YearQuoteStore:
    def __init__(self,loader,max_years=2):
        if max_years<2:raise ValueError('current and preceding quote years required')
        self.loader=loader;self.max_years=max_years;self.cache=OrderedDict()
        self.entry_keys=None
        self.quotes=_Lookup(self,False);self.groups=_Lookup(self,True)

    def prepare(self,events):
        self.entry_keys=pd.MultiIndex.from_frame(events[['entry_date','secid']].rename(columns={'entry_date':'date'}).drop_duplicates())
        self.cache.clear()

    def load(self,year):
        if year not in self.cache:
            frame=self.loader(year).copy()
            if REQUIRED-set(frame):raise ValueError('streamed quotes lack required columns')
            for col in ('date','expiry'):frame[col]=pd.to_datetime(frame[col]).dt.normalize()
            if frame.date.isna().any() or frame.optionid.isna().any() or frame.secid.isna().any():raise ValueError('missing quote key')
            if not frame.date.dt.year.eq(year).all():raise ValueError('quote loader returned other-year rows')
            if frame.duplicated(['date','secid','optionid']).any():raise ValueError('duplicate streamed quotes')
            lookup={(r.date,r.secid,int(r.optionid)):r for r in frame.itertuples(index=False)}
            selected=frame[pd.MultiIndex.from_frame(frame[['date','secid']]).isin(self.entry_keys)]
            groups={k:g for k,g in selected.groupby(['date','secid'],sort=False)}
            self.cache[year]=(lookup,groups)
            while len(self.cache)>self.max_years:self.cache.popitem(last=False)
        self.cache.move_to_end(year)
        return self.cache[year]

class _Lookup:
    def __init__(self,store,groups):self.store=store;self.groups=groups
    def get(self,key,default=None):
        lookup=self.store.load(pd.Timestamp(key[0]).year)[int(self.groups)]
        return lookup.get(key,default)
