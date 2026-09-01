"""Shared helpers for the options-PEAD pipeline (scripts 33-39)."""
import gc
import os
import time
import pandas as pd
import pyarrow.parquet as pq

# The OptionMetrics drive is a physical external HDD that gets plugged directly into whichever
# machine is running the pipeline (it is never copied -- see README's GPU/data-portability
# section). Its mount path is therefore machine-specific: D:/OptionMetrics/parquet on this
# Windows box, something like /media/<user>/OptionMetrics/parquet once plugged into the Linux
# 5090 box. Override OPTIONMETRICS_DIR in the environment rather than editing this default.
OM_DIR = os.environ.get("OPTIONMETRICS_DIR", "D:/OptionMetrics/parquet")
OPPRCD_COLS = ["secid", "date", "exdate", "cp_flag", "strike_price", "best_bid",
               "best_offer", "delta", "impl_volatility", "volume", "open_interest", "optionid"]


def scan_year_for_keys(year, needed_keys, columns=OPPRCD_COLS, batch_size=1_000_000,
                        max_retries=4, retry_wait_sec=15):
    """Stream opprcd{year}.parquet in batches, keeping only rows whose (secid, date) pair
    appears in needed_keys (a DataFrame with columns ['secid','date']). Low peak memory: only
    one batch (~1M rows x len(columns)) is materialized at a time before being filtered down.

    The OptionMetrics files live on a physical external drive (never copied -- see README's
    data-portability section), and streaming 600GB+ off it hits an occasional transient
    "Error reading bytes from file" from pyarrow -- observed at a different, non-corrupt year on
    each of several otherwise-clean runs, i.e. a USB/controller hiccup, not the one genuinely
    corrupt file (opprcd2011, confirmed separately with two different readers -- see the options
    pipeline section of the README). Retrying the whole year's scan from scratch after a short
    pause clears it every time it's been observed; this matters most for an unattended multi-day
    run where nobody is there to notice and re-launch it."""
    path = f"{OM_DIR}/opprcd{year}.parquet"
    keys = needed_keys[["secid", "date"]].drop_duplicates()
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            pf = pq.ParquetFile(path)
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
        except OSError as e:
            last_err = e
            gc.collect()
            if attempt < max_retries:
                print(f"  [scan_year_for_keys] {year}: read error on attempt {attempt}/"
                      f"{max_retries} ({e}) -- retrying in {retry_wait_sec}s", flush=True)
                time.sleep(retry_wait_sec)
            else:
                print(f"  [scan_year_for_keys] {year}: read error persisted after "
                      f"{max_retries} attempts, giving up on this year", flush=True)
    raise last_err


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


def pick_full_chain_vectorized(raw, min_dte, target_dte):
    """Like pick_best_contracts_vectorized, but instead of collapsing each (secid, date) group to
    a single winning contract, picks the single best EXPIRATION (exdate whose dte is closest to
    target_dte, among dte >= min_dte -- same rule script 35 uses) and returns EVERY contract
    (both cp_flag, every strike) quoted for that (secid, date, exdate). This is what
    scripts/48_optimal_contract_selector.py needs: scoring every strike in the actual day0 chain
    against a return distribution, rather than pre-filtering to a single near-ATM guess before
    that scoring ever happens."""
    c = raw[raw["dte"] >= min_dte].copy()
    if c.empty:
        return c
    c["dte_dist"] = (c["dte"] - target_dte).abs()
    # one row per (secid, date, exdate) giving that expiration's dte_dist, then pick the best
    # exdate per (secid, date) and inner-join back to keep every strike/cp_flag at that exdate.
    exp_dist = c[["secid", "date", "exdate", "dte_dist"]].drop_duplicates(subset=["secid", "date", "exdate"])
    exp_dist = exp_dist.sort_values(["secid", "date", "dte_dist"], kind="mergesort")
    best_exp = exp_dist.groupby(["secid", "date"], as_index=False, sort=False).head(1)
    best_exp = best_exp[["secid", "date", "exdate"]]
    return c.merge(best_exp, on=["secid", "date", "exdate"], how="inner")
