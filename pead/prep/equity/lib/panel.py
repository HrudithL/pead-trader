"""Daily-panel and position loaders for the prep stages (the strategy engines load via pead.core.data)."""
import numpy as np
import pandas as pd


def load_mktadj_panel(raw_dir):
    """Daily permno panel with market-adjusted returns, sorted (permno, date) -- the row order
    entry_row_idx/exit_row_idx in every positions_*_v2.parquet index into. Also returns the
    trading calendar (market benchmark dates)."""
    frames = [pd.read_parquet(raw_dir / f"equity_{y}.parquet", columns=["permno", "date", "ret"])
              for y in range(1995, 2015)]
    eq = pd.concat(frames, ignore_index=True)
    mkt = pd.read_parquet(raw_dir / "market_benchmark.parquet", columns=["date", "vwretd"])
    eq = eq.merge(mkt, on="date", how="left")
    eq["ret_mktadj"] = (eq["ret"] - eq["vwretd"]).fillna(0.0)
    eq = eq.sort_values(["permno", "date"]).drop_duplicates(subset=["permno", "date"]).reset_index(drop=True)
    calendar_dt = pd.DatetimeIndex(sorted(mkt["date"].unique()))
    return eq, calendar_dt


def load_positions(data_dir):
    """positions_rankweighted_v2.parquet (every tradeable event with its per-cell holding window),
    cleaned exactly as 23/24 clean it."""
    pos = pd.read_parquet(data_dir / "positions_rankweighted_v2.parquet").reset_index(drop=True)
    pos = pos.dropna(subset=["entry_row_idx", "exit_row_idx"]).copy().reset_index(drop=True)
    pos["entry_row_idx"] = pos["entry_row_idx"].astype(int)
    pos["exit_row_idx"] = pos["exit_row_idx"].astype(int)
    pos["day0_date"] = pd.to_datetime(pos["day0_date"])
    pos["exit_date"] = pd.to_datetime(pos["exit_date"])
    return pos
