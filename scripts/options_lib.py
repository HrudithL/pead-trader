"""Shared helpers for the options-PEAD pipeline (scripts 33-39)."""
import gc
import pandas as pd
import pyarrow.parquet as pq

OM_DIR = "D:/OptionMetrics/parquet"
OPPRCD_COLS = ["secid", "date", "exdate", "cp_flag", "strike_price", "best_bid",
               "best_offer", "delta", "impl_volatility", "volume", "open_interest", "optionid"]


def scan_year_for_keys(year, needed_keys, columns=OPPRCD_COLS, batch_size=1_000_000):
    """Stream opprcd{year}.parquet in batches, keeping only rows whose (secid, date) pair
    appears in needed_keys (a DataFrame with columns ['secid','date']). Low peak memory: only
    one batch (~1M rows x len(columns)) is materialized at a time before being filtered down."""
    path = f"{OM_DIR}/opprcd{year}.parquet"
    pf = pq.ParquetFile(path)
    keys = needed_keys[["secid", "date"]].drop_duplicates()
    chunks = []
    for batch in pf.iter_batches(columns=columns, batch_size=batch_size):
        df = batch.to_pandas(date_as_object=False)
        df["secid"] = df["secid"].astype("int64")
        df = df.merge(keys, on=["secid", "date"], how="inner")
        if len(df):
            chunks.append(df)
    del pf
    gc.collect()
    if not chunks:
        return pd.DataFrame(columns=columns)
    out = pd.concat(chunks, ignore_index=True)
    del chunks
    gc.collect()
    return out


def pick_best_contracts_vectorized(raw, cp_flag, target_delta, min_dte, target_dte):
    """Vectorized equivalent of picking, per (secid,date) group, the single best contract of one
    cp_flag: DTE >= min_dte, delta closest to target_delta (ties broken by DTE closest to
    target_dte). Uses sort + groupby().head(1) instead of a per-group Python loop/apply, which is
    orders of magnitude faster at the row counts these opprcd scans produce."""
    c = raw[(raw["cp_flag"] == cp_flag) & (raw["dte"] >= min_dte)].copy()
    if c.empty:
        return c
    c["delta_dist"] = (c["delta"] - target_delta).abs()
    c["dte_dist"] = (c["dte"] - target_dte).abs()
    c = c.sort_values(["secid", "date", "delta_dist", "dte_dist"], kind="mergesort")
    return c.groupby(["secid", "date"], as_index=False, sort=False).head(1)
