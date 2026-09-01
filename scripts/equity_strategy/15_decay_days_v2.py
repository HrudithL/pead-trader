"""
Fix the decay-day detection: a single day's marginal return is too noisy (daily cross-quarter
averages bounce several bps in either direction from pure noise) for a tight fixed threshold to
reliably tell "the drift has stopped" from "this particular day happened to dip." Reuses the
already-computed cell_daily_curve.csv rather than rebuilding the expensive return matrix.

Method: for each cell, compute a trailing 20-TRADING-DAY windowed slope of the CUMULATIVE mean
curve (i.e. how much the cumulative return moved over the last 20 days, divided by 20 -- a much
less noisy quantity than a single day's marginal return, since it averages out day-to-day noise
the same way the original 1/5/10/20/... checkpoints did, just recomputed at every possible
20-day window instead of a few fixed ones). Decay day = the first day (after a 30-day burn-in,
to skip the initial reaction window) where that windowed slope, on the side that matters for
this cell's decile (shrinking for the positive side, no-longer-falling for the negative side),
crosses toward zero AND STAYS there for the next 10 consecutive days (a persistence check, so
one noisy crossing doesn't trigger a false decay call).

Output: data/cell_decay_days.csv (overwritten with the fixed logic)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR

DATA = DATA_DIR
WINDOW = 20
BURN_IN = 30
PERSIST = 10

df = pd.read_csv(DATA / "cell_daily_curve.csv")

decay_rows = []
for cell_name, sub in df.groupby("cell"):
    sub = sub.sort_values("day").reset_index(drop=True)
    decile = int(cell_name.split(":")[1].split("_")[0])
    positive_side = decile >= 6

    cum = sub["cumulative_mean"].to_numpy()
    days = sub["day"].to_numpy()
    n = len(cum)

    # windowed slope: (cum[d] - cum[d-WINDOW]) / WINDOW, per-day rate over the trailing window
    windowed_slope = np.full(n, np.nan)
    for i in range(WINDOW, n):
        if not np.isnan(cum[i]) and not np.isnan(cum[i - WINDOW]):
            windowed_slope[i] = (cum[i] - cum[i - WINDOW]) / WINDOW

    # the cell's own peak windowed slope (early, strong-signal period) sets a relative
    # threshold: "decayed" means the windowed slope has fallen to under 15% of its own peak
    # magnitude (data-driven per cell, not one fixed bps number for every cell regardless of
    # how strong its signal ever was)
    early = windowed_slope[BURN_IN:min(BURN_IN + 40, n)]
    early = early[~np.isnan(early)]
    if len(early) == 0 or np.all(np.isnan(windowed_slope)):
        decay_rows.append(dict(cell=cell_name, decile=decile, decay_day=n, method="no_data"))
        continue
    peak_mag = np.nanmax(np.abs(windowed_slope[:min(BURN_IN + 60, n)])) if np.any(~np.isnan(windowed_slope[:min(BURN_IN+60,n)])) else np.nanmax(np.abs(windowed_slope))
    threshold = 0.15 * peak_mag

    decay_day = n  # default: never decays within the measured window
    for i in range(BURN_IN, n):
        s = windowed_slope[i]
        if np.isnan(s):
            continue
        decayed_now = abs(s) < threshold or (positive_side and s < 0) or (not positive_side and s > 0)
        if decayed_now:
            # persistence check: stay "decayed" for the next PERSIST days (or to the end)
            window_end = min(i + PERSIST, n)
            future = windowed_slope[i:window_end]
            future = future[~np.isnan(future)]
            if len(future) == 0:
                continue
            still_decayed = np.all(
                (np.abs(future) < threshold) |
                (future < 0 if positive_side else future > 0)
            )
            if still_decayed:
                decay_day = days[i]
                break

    decay_rows.append(dict(cell=cell_name, decile=decile, decay_day=int(decay_day),
                            peak_windowed_slope_bps_per_day=peak_mag * 10000,
                            threshold_bps_per_day=threshold * 10000, method="windowed_slope"))

decay_df = pd.DataFrame(decay_rows).sort_values(["decile", "cell"])
decay_df.to_csv(DATA / "cell_decay_days.csv", index=False)
print(decay_df.to_string(index=False))
print()
print("decay_day distribution:")
print(decay_df["decay_day"].describe())
