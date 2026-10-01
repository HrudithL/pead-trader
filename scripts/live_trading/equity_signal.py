"""Live equity signal: Strategy 6's weight construction, applied to a rolling cohort of live
earnings-surprise events instead of a fixed fiscal quarter.

Reproduces `compute_weights_size_sector()` from
scripts/equity_strategy/24_finalize_strategy6.py almost exactly: same trim/tilt on SUE rank, same
size-quintile x sector two-way neutralization, same final normalization to 0.5 average gross
weight per side. The one structural difference is the grouping cohort: the backtest neutralizes
within a fiscal quarter (all firms that reported in the same calendar quarter); live earnings are
scanned daily, so this uses a rolling trailing window (config.EQUITY_COHORT_WINDOW_TRADING_DAYS,
default 5 trading days) as the neutralization cohort instead. See LIVE_TRADING.md for why a
quarter-long live cohort would be too stale to trade the drift promptly.
"""
import numpy as np
import pandas as pd

from . import config


def _size_quintile(market_caps):
    ranks = pd.Series(market_caps).rank(pct=True)
    return np.ceil(ranks * 5).clip(1, 5).astype(int)


def compute_target_weights(cohort_df):
    """cohort_df: DataFrame with columns [ticker, sue_z, sector, market_cap]. Returns cohort_df
    with an added `weight` column (can be 0), on the same 0.5-average-gross-weight-per-side scale
    Strategy 6 uses, so downstream dollar sizing (equity_sleeve_nav * base_unit_fraction * weight)
    matches the backtest's sizing convention exactly.
    """
    df = cohort_df.copy().reset_index(drop=True)
    if df.empty:
        df["weight"] = []
        return df

    rank_pct = df["sue_z"].rank(pct=True)
    d = rank_pct - 0.5
    frac = np.clip(np.abs(d) / 0.5, 0, 1)
    tilt = np.sign(d) * (frac ** config.EQUITY_TILT_POWER)
    keep = np.abs(d) > config.EQUITY_TRIM
    w = np.where(keep, tilt, 0.0)

    df["w"] = w
    df["side"] = np.sign(w)
    df["size_quintile"] = _size_quintile(df["market_cap"].fillna(df["market_cap"].median()))

    kept = df[df["w"] != 0].copy()
    if kept.empty:
        df["weight"] = 0.0
        return df.drop(columns=["w", "side"])

    grp_key = list(zip(kept["side"], kept["sector"], kept["size_quintile"]))
    kept["grp_key"] = grp_key
    grp_totals = kept.groupby("grp_key")["w"].transform(lambda x: x.abs().sum())
    n_groups = kept.groupby("side")["grp_key"].transform("nunique")
    kept["w_neutral"] = kept["w"] / grp_totals / n_groups

    nz = kept["w_neutral"][kept["w_neutral"] != 0]
    scale = (np.abs(nz).mean() if len(nz) else 1.0) or 1.0
    kept["weight"] = kept["w_neutral"] / scale * 0.5

    df["weight"] = 0.0
    df.loc[kept.index, "weight"] = kept["weight"]
    return df.drop(columns=["w", "side"])


def size_positions(weighted_df, equity_sleeve_nav, adv21_by_ticker):
    """Applies base_unit_fraction sizing, the per-name 5%-of-ADV liquidity cap, and the 2.5x gross
    leverage cap (priority-based trimming: lowest |weight| convictions get cut first, matching
    Strategy 6's cap-trimming rule), then returns [ticker, side, target_dollars, target_shares].
    """
    df = weighted_df[weighted_df["weight"] != 0].copy()
    if df.empty:
        return df
    df["target_dollars"] = df["weight"] * config.EQUITY_BASE_UNIT_FRACTION * equity_sleeve_nav
    for i, row in df.iterrows():
        adv = adv21_by_ticker.get(row["ticker"])
        if adv:
            cap = config.EQUITY_LIQUIDITY_CAP_FRAC * adv
            df.at[i, "target_dollars"] = np.clip(row["target_dollars"], -cap, cap)

    gross = df["target_dollars"].abs().sum()
    max_gross = config.EQUITY_MAX_GROSS_LEVERAGE * equity_sleeve_nav
    if gross > max_gross and gross > 0:
        df = df.reindex(df["target_dollars"].abs().sort_values(ascending=False).index)
        running = 0.0
        keep_mask = []
        for dollars in df["target_dollars"].abs():
            if running + dollars <= max_gross:
                running += dollars
                keep_mask.append(True)
            else:
                keep_mask.append(False)
        df = df[keep_mask]
    return df.reset_index(drop=True)
