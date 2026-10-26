import pandas as pd
import pytest
from pead.earnings_options.index_settlement import settlement_calendar,credit_menu


def test_monthly_soq_calendar_includes_saturday_22_and_good_friday():
    dates=pd.bdate_range('2008-02-01','2008-03-31').difference(pd.DatetimeIndex(['2008-03-21']))
    obs=pd.DataFrame(dict(date=pd.to_datetime(['2008-02-15','2008-03-20']),close=[1350.,1330.]))
    s=settlement_calendar(dates,obs)
    assert list(s.expiry)==list(pd.to_datetime(['2008-02-16','2008-03-22']))
    assert list(s.settlement_date)==list(pd.to_datetime(['2008-02-15','2008-03-20']))
    assert list(s.value)==[1350.,1330.]
    with pytest.raises(ValueError,match='missing native settlement'):
        settlement_calendar(dates,obs.iloc[:1])


def test_credit_menu_uses_prior_delta_and_accepts_saturday_22():
    date=pd.Timestamp('2005-02-01');expiry=pd.Timestamp('2005-03-19')
    def chain(ex):
        return pd.DataFrame([dict(date=date,expiry=ex,optionid=i,cp='P',strike=k,
            bid=bid,ask=ask,delta=.9,prior_delta=delta,prior_volume=1000,
            prior_oi=1000,standard_contract=True) for i,k,bid,ask,delta in
            [(1,1200,12.,12.5,-.35),(2,1150,3.,3.5,-.15)]])
    assert len(credit_menu(chain(expiry),1.5))==1
    assert len(credit_menu(chain(pd.Timestamp('2005-01-22')).assign(date=pd.Timestamp('2004-12-01')),1.5))==1
    q=chain(expiry);q['prior_delta']=float('nan')
    assert credit_menu(q,1.5)==[]


def test_first_january_session_uses_actual_december_inputs(monkeypatch,tmp_path):
    import pead.earnings_options.index_settlement as m
    monkeypatch.setattr(m,'CACHE',tmp_path)
    cal=pd.to_datetime(['2009-12-31','2010-01-04'])
    rows=pd.DataFrame([dict(date=d,secid=m.SPX,optionid=1,delta=delta,volume=volume,
        open_interest=1000,ss_flag='0',cfadj=1.) for d,delta,volume in
        [(cal[0],-.35,400),(cal[1],-.50,600)]])
    monkeypatch.setattr(m,'index_quotes',lambda year,calendar:rows.iloc[1:].copy())
    monkeypatch.setattr(m,'scan',lambda *args,**kwargs:rows.iloc[:1].copy(),raising=False)
    q=m.load_quotes(2010,cal)
    assert len(q)==1
    assert q.prior_delta.iloc[0]==pytest.approx(-.35)
    assert q.prior_volume.iloc[0]==400


def test_signal_cache_is_separate_for_holdout_extension(monkeypatch,tmp_path):
    import pead.earnings_options.index_research as m
    monkeypatch.setattr(m,'CACHE',tmp_path)
    pd.DataFrame(dict(version=[2010])).to_parquet(tmp_path/'signals_1999_2010.parquet')
    pd.DataFrame(dict(version=[2013])).to_parquet(tmp_path/'signals_1999_2013.parquet')
    assert m.signals(2013).version.iloc[0]==2013
