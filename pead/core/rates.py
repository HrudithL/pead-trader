"""Cash rate used for idle money in the long-only strategies.

ASSUMPTION, not downloaded data: annual averages of the 3-month Treasury bill yield (percent), typed from memory of the
FRED TB3MS series (+-5 bp). Replace with the real series before quoting cash-yield-sensitive numbers to anyone."""
import numpy as np
import pandas as pd

TBILL_PCT = {1995: 5.49, 1996: 5.01, 1997: 5.06, 1998: 4.78, 1999: 4.64, 2000: 5.82, 2001: 3.40, 2002: 1.61, 2003: 1.01,
             2004: 1.37, 2005: 3.15, 2006: 4.73, 2007: 4.36, 2008: 1.37, 2009: 0.15, 2010: 0.14, 2011: 0.05, 2012: 0.09,
             2013: 0.06, 2014: 0.03, 2015: 0.05}


def daily_rf(calendar) -> np.ndarray:
    """Per-trading-day cash return for each date of `calendar` (annual rate / 252)."""
    years = pd.DatetimeIndex(calendar).year
    return np.array([TBILL_PCT.get(int(y), 0.0) for y in years]) / 100.0 / 252.0
