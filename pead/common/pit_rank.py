"""Point-in-time SUE percentile ranking (shared by every stage that builds deciles).

The original construction ranked each event against EVERY announcement in its own calendar quarter,
including firms that had not reported yet on the event's own date (earnings within a quarter are spread
over 6+ weeks), so an early reporter's decile was informed by data that did not exist on its trade date.
`prior_quarters` ranks each event only against the cumulative pool of strictly EARLIER, fully realized
quarters. The first quarter has no pool and is dropped (NaN). `within_quarter` is the original behaviour,
kept so the old published numbers can be rebuilt (set PEAD_DECILE_RANKING=within_quarter and a separate
PEAD_DATA_DIR); it MUST be the same in build_deciles, link_events_secid and return_distributions.

CAVEAT (design trade-off, not a bug): a pooled EXPANDING rank cannot absorb market-wide surprise regimes. If every
firm beats in a boom quarter, most of that quarter ranks in the top deciles against the older pool, so decile sizes
are uneven per quarter (D10 share ~8-17% of events) and the long/short legs are no longer equal-count by quarter.
Alternatives (trailing 4-8 quarter pool, or same-day-or-earlier in-quarter rank) were not evaluated.
"""
import os

import numpy as np
import pandas as pd

MODE_ENV = "PEAD_DECILE_RANKING"       # 'prior_quarters' (default) | 'within_quarter' (legacy, look-ahead)


def ranking_mode():
    mode = os.environ.get(MODE_ENV, "prior_quarters")
    if mode not in ("prior_quarters", "within_quarter"):
        raise ValueError(f"{MODE_ENV}={mode!r}: expected 'prior_quarters' or 'within_quarter'")
    return mode


def rank_pct(values: pd.Series, quarters: pd.Series, mode=None) -> pd.Series:
    """Percentile rank in (0, 1] of `values`, aligned to values.index; NaN where no prior pool exists."""
    mode = mode or ranking_mode()
    if mode == "within_quarter":
        return values.groupby(quarters).rank(pct=True, method="average")
    codes, _ = pd.factorize(quarters, sort=True)
    v = values.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    pool = np.empty(0)
    for c in range(codes.max() + 1):
        m = codes == c
        if len(pool):
            # average-rank tie handling: midpoint between first and last slot of the tied value in the pool
            out[m] = (np.searchsorted(pool, v[m], "left") + np.searchsorted(pool, v[m], "right")) / 2.0 / len(pool)
        pool = np.sort(np.concatenate([pool, v[m]]))
    return pd.Series(out, index=values.index)
