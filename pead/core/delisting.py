"""CRSP delisting returns (MT-05): compound the terminal delisting return onto the stock's last panel row.

The daily panel uses `ret` from the daily stock file, which stops at the last trading day; the delisting return (`dlret` in
dsedelist) is the move from that last close to the delisting value and is missing from every P&L. Policy (Shumway 1997,
Shumway-Warther 1999 convention): r_term = (1 + ret_last)(1 + dlret) - 1; when dlret is missing and the code is a performance
delisting (4xx-5xx), impute -30% (NYSE/AMEX) or -55% (Nasdaq) -- these are parameters, not facts about this sample.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pead.common.paths import DATA_DIR

CRSP = DATA_DIR / "raw_wrds" / "crsp"


@dataclass(frozen=True)
class DelistPolicy:
    use_dlret: bool = True
    impute_missing_performance: bool = True
    loss_nyse_amex: float = -0.30
    loss_nasdaq: float = -0.55
    performance_codes: tuple = field(default=tuple(range(400, 600)))


def load_delistings() -> pd.DataFrame:
    d = pd.read_parquet(CRSP / "dsedelist.parquet")
    d["dlstdt"] = pd.to_datetime(d["dlstdt"])
    names = pd.read_parquet(CRSP / "stocknames.parquet", columns=["permno", "nameenddt", "exchcd"])
    names["nameenddt"] = pd.to_datetime(names["nameenddt"])
    last = names.sort_values("nameenddt").groupby("permno").tail(1)[["permno", "exchcd"]]
    return d.merge(last, on="permno", how="left")


def apply_delisting_returns(ret_raw, ret_mktadj, panel_date, panel_permno, delist: pd.DataFrame, policy: DelistPolicy = DelistPolicy()):
    """Returns (ret_raw2, ret_mktadj2, info). The same additive delta goes to both arrays (market return is unchanged).
    A delisting is applied to the stock's last panel row on or before dlstdt, only if that row is within 10 days of dlstdt."""
    r1, r2 = ret_raw.copy(), ret_mktadj.copy()
    pdates = np.asarray(panel_date, dtype="datetime64[ns]")
    starts = np.searchsorted(panel_permno, delist["permno"].to_numpy(), side="left")
    ends = np.searchsorted(panel_permno, delist["permno"].to_numpy(), side="right")
    n_applied = n_imputed = 0
    total_delta = 0.0
    for (permno, dt, dlret, code, exch), s, e in zip(
            delist[["permno", "dlstdt", "dlret", "dlstcd", "exchcd"]].itertuples(index=False, name=None), starts, ends):
        if e <= s:
            continue
        j = s + np.searchsorted(pdates[s:e], np.datetime64(dt), side="right") - 1
        if j < s or (np.datetime64(dt) - pdates[j]) > np.timedelta64(10, "D"):
            continue
        x = dlret
        if pd.isna(x):
            if policy.impute_missing_performance and code in policy.performance_codes:
                x = policy.loss_nasdaq if exch == 3 else policy.loss_nyse_amex
                n_imputed += 1
            else:
                x = 0.0
        elif not policy.use_dlret:
            x = 0.0
        term = (1.0 + r1[j]) * (1.0 + x) - 1.0
        delta = term - r1[j]
        r1[j] += delta
        r2[j] += delta
        total_delta += delta
        n_applied += 1
    return r1, r2, dict(n_applied=n_applied, n_imputed=n_imputed, mean_delta=total_delta / max(n_applied, 1))
