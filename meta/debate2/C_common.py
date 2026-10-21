"""Shared helpers for perspective C (capital efficiency, costs, implementability). Read-only on V."""
import json, os, numpy as np, pandas as pd
V = "C:/Users/hrudi/Documents/BEI/_pead_consolidation/vintage_fixed/"
OUT = os.path.dirname(os.path.abspath(__file__)) + "/"
# Annual-average 3-month T-bill yield (%), typed from memory of FRED TB3MS annual means (+-5bp). ASSUMPTION, not downloaded.
TBILL = {1995:5.49,1996:5.01,1997:5.06,1998:4.78,1999:4.64,2000:5.82,2001:3.40,2002:1.61,2003:1.01,2004:1.37,
         2005:3.15,2006:4.73,2007:4.36,2008:1.37,2009:0.15,2010:0.14,2011:0.05,2012:0.09,2013:0.06,2014:0.03,2015:0.05}
def rf_daily(dates):
    """per-row accrual (fraction) for the calendar gap since the previous row."""
    d = pd.DatetimeIndex(dates)
    gap = np.r_[1, np.diff(d.values).astype('timedelta64[D]').astype(int)]
    rate = np.array([TBILL[y] for y in d.year]) / 100
    return rate * gap / 365, gap
# --- cost model (ASSUMPTIONS, per side, bps) ---
HALF_SPREAD = {1:35, 2:20, 3:10, 4:6, 5:3}   # effective half-spread by 21d-ADV liquidity quintile (1 = least liquid), post-2001
PRE_DECIMAL = pd.Timestamp("2001-04-09")      # before: fractions, spreads x1.5
SCEN = {"engine": None, "base": dict(sp=1.0, Y=0.5), "harsh": dict(sp=1.5, Y=1.0)}
def real_cost_bps(pos, sp=1.0, Y=0.5):
    liq = pos.liquidity_quintile.fillna(2).astype(int).clip(1,5)
    hs = liq.map(HALF_SPREAD).to_numpy(float) * sp
    hs = np.where(pos.day0_date < PRE_DECIMAL, hs*1.5, hs)
    sig = pos.tvol_60d.fillna(pos.tvol_60d.median()).to_numpy(float)
    part = (pos.notional.abs() / pos.avg_dollar_volume_21d).clip(upper=0.25).to_numpy(float)
    imp = Y * sig * np.sqrt(part) * 1e4          # square-root impact, sigma = daily vol
    imp = np.where(np.isnan(imp), Y*0.02*np.sqrt(0.02)*1e4, imp)   # missing ADV/vol: 2% vol, 2% participation
    return hs + imp
def ann_geo(r, gap=None):
    r = np.asarray(r); n = len(r)
    return float(np.prod(1+r)**(365.25/ (gap.sum() if gap is not None else n*365.25/252)) - 1)
def load_run(name):
    d = pd.read_csv(V+f"runs/{name}/daily.csv.gz", parse_dates=["date"])
    j = json.load(open(V+f"runs/{name}/summary.json"))
    return d, j
