"""Data loading for the backtest engines -- the one place that knows where inputs live.

A strategy never touches a parquet path: it receives an `EquityUniverse` (events + the daily
return panel) or an `OptionsUniverse` (selected contracts + their forward prices) built here. Both
are loaded lazily and cached per process, so a runner worker that executes five strategies reads
the 20-year daily panel once.
"""
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from pead.common.paths import DATA_DIR, RAW_EQUITY_DIR, INITIAL_CAPITAL  # noqa: F401 (re-exported)

PANEL_YEARS = range(1995, 2015)


@dataclass
class EquityUniverse:
    """Every tradeable earnings event (one row each) plus the daily panel its P&L is read from.

    `events` rows index into the (permno, date)-sorted daily panel via entry_row_idx/exit_row_idx,
    so the engine can expand a position's holding window with pure array arithmetic.
      ret_mktadj  stock return minus CRSP value-weighted market return (market-neutral books)
      ret_raw     raw stock return (books that deliberately carry market beta)
      vwretd      market return on the calendar (benchmark / beta overlays)"""
    events: pd.DataFrame
    ret_mktadj: np.ndarray
    ret_raw: np.ndarray
    panel_date: np.ndarray
    calendar: pd.DatetimeIndex
    vwretd: np.ndarray
    initial_capital: float = INITIAL_CAPITAL
    extras: dict = field(default_factory=dict)
    panel_permno: np.ndarray = None       # permno of every panel row; needed to cut a fixed holding window at a stock's last row


def _panel_sources():
    return [RAW_EQUITY_DIR / f"equity_{y}.parquet" for y in PANEL_YEARS] + [RAW_EQUITY_DIR / "market_benchmark.parquet"]


def _load_panel():
    """(ret_mktadj, ret_raw, panel_date, calendar, vwretd, permno) for the (permno, date)-sorted daily panel.
    Cached as one .npz keyed on the source files' sizes and mtimes: the first load takes ~12s of
    parquet reading, every later one ~1s."""
    cache = DATA_DIR / "cache" / "equity_panel.npz"
    srcs = _panel_sources()
    key = ";".join(f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}" for p in srcs)
    if cache.exists():
        z = np.load(cache, allow_pickle=False)
        if str(z["key"]) == key and "permno" in z.files:
            return (z["ret_mktadj"], z["ret_raw"], z["panel_date"], pd.DatetimeIndex(z["calendar"]), z["vwretd"], z["permno"])
    frames = [pd.read_parquet(p, columns=["permno", "date", "ret"]) for p in srcs[:-1]]
    eq = pd.concat(frames, ignore_index=True)
    mkt = pd.read_parquet(srcs[-1], columns=["date", "vwretd"])
    eq = eq.merge(mkt, on="date", how="left")
    eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
    eq["ret_raw"] = eq["ret"].fillna(0.0)
    eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
    mkt = mkt.drop_duplicates("date").sort_values("date")
    calendar = pd.DatetimeIndex(sorted(mkt["date"].unique()))
    vw = mkt.set_index("date")["vwretd"].reindex(calendar).fillna(0.0).to_numpy()
    out = (eq["ret_mktadj"].to_numpy(), eq["ret_raw"].to_numpy(), eq["date"].to_numpy(), calendar, vw,
           eq["permno"].to_numpy())
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, key=key, ret_mktadj=out[0], ret_raw=out[1], panel_date=out[2],
                 calendar=calendar.to_numpy(), vwretd=vw, permno=out[5])
    except OSError:
        pass
    return out


def _load_events():
    """equity_vol_panel.parquet is positions_rankweighted_v2 (every event with its per-cell holding
    window, SUE rank, size/sector/liquidity) plus the leak-free trailing-vol columns built by
    prep.equity_vol_features -- a strict superset, same row order."""
    path = DATA_DIR / "equity_vol_panel.parquet"
    if not path.exists():
        path = DATA_DIR / "positions_rankweighted_v2.parquet"
    pos = pd.read_parquet(path).reset_index(drop=True)
    pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
    pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
    pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    pos["exit_date"] = pd.to_datetime(pos["exit_date"])
    return pos


@lru_cache(maxsize=1)
def load_equity_universe() -> EquityUniverse:
    ret_mktadj, ret_raw, panel_date, calendar, vw, permno = _load_panel()
    return EquityUniverse(events=_load_events(), ret_mktadj=ret_mktadj, ret_raw=ret_raw,
                          panel_date=panel_date, calendar=calendar, vwretd=vw, panel_permno=permno)


@dataclass
class OptionsUniverse:
    """One row per priceable earnings event: the Kelly-selector's pick, its entry premium, and the
    pick's mid price at +1/5/10/20/40/60 trading days (from OptionMetrics, see prep.options_*)."""
    picks: pd.DataFrame
    calendar: np.ndarray
    event_vol: pd.DataFrame
    initial_capital: float = INITIAL_CAPITAL


@lru_cache(maxsize=1)
def load_options_universe() -> OptionsUniverse:
    base = DATA_DIR / "event_options"
    picks = pd.read_parquet(base / "optimal_forward_prices.parquet")
    picks["day0_date"] = pd.to_datetime(picks["day0_date"])
    cal = pd.read_parquet(DATA_DIR / "metadata" / "om_trading_calendar.parquet")
    cal_dates = cal.sort_values("date")["date"].to_numpy()
    feats_path = base / "event_vol_features.parquet"
    feats = pd.read_parquet(feats_path) if feats_path.exists() else pd.DataFrame()
    if len(feats):
        feats["day0_date"] = pd.to_datetime(feats["day0_date"])
    return OptionsUniverse(picks=picks, calendar=cal_dates, event_vol=feats)
