"""
28_sue_reversal_test.py

Tests the "post-earnings-announcement reversal" hypothesis from the literature:
Bernard & Thomas (1990) and later work document that a firm's own SUE (standardized
unexpected earnings) tends to show negative autocorrelation at a 4-quarter lag --
i.e. a firm that beats big this quarter tends to have a smaller (or negative)
surprise four quarters later, roughly -0.24 in their sample. If that reversal is
present and tradeable, it would be a second, independent signal layered on top of
the standard PEAD drift trade: fade a firm's own SUE history, not just its
cross-sectional rank.

This script reproduces the diagnostic run earlier in this project (as ad hoc
inline commands) as a standalone, re-runnable script.

Method:
  1. For every (permno, ann_quarter) event, find that same permno's event roughly
     4 fiscal quarters (~340-400 calendar days) earlier.
  2. Compute the correlation between SUE at t and SUE at t-4 across all such
     same-firm pairs.
  3. Break the correlation down by the decile of SUE at t-4, to check whether any
     reversal is concentrated in the extremes (where it would matter most for a
     trading strategy) even if the overall correlation is weak or positive.

Finding: autocorrelation is *positive* (+0.04) in this data, not the literature's
-0.24, and decile-level breakdown shows persistence, not reversal -- firms with a
very high SUE 4 quarters ago still average a high SUE now (decile-10 firms'
lag-4 SUE averaged +1.53), and firms with a very low SUE 4 quarters ago still
average a low SUE now (decile-1 firms' averaged -0.41). There is no reversal
signal to trade here. This is a negative result, but it's an important one: it's
why this project's strategies (4 through 7) don't attempt to fade a firm's own
SUE history, and instead focus on cross-sectional rank (relative to the same
quarter's other announcers) and on risk construction (sizing, sector/size
neutrality, leverage) to improve the strategy.
"""

import pandas as pd
import numpy as np

DATA = "data/decile_events.parquet"
OUT_SUMMARY = "data/sue_reversal_summary.json"
OUT_DECILE = "data/sue_reversal_by_decile.csv"

LAG_DAYS_MIN = 340
LAG_DAYS_MAX = 400

def main():
    df = pd.read_parquet(DATA, columns=[
        "permno", "anndats", "ann_quarter", "sue_analyst_r", "decile",
    ]).dropna(subset=["permno", "anndats", "sue_analyst_r"])
    df["anndats"] = pd.to_datetime(df["anndats"])
    df = df.sort_values(["permno", "anndats"]).reset_index(drop=True)

    print(f"Loaded {len(df):,} events with a valid SUE.")

    # For each permno, self-join on a ~1-year lag window to find same-firm pairs
    # roughly 4 fiscal quarters apart.
    pairs = []
    for permno, grp in df.groupby("permno", sort=False):
        if len(grp) < 2:
            continue
        dates = grp["anndats"].to_numpy()
        sues = grp["sue_analyst_r"].to_numpy()
        deciles = grp["decile"].to_numpy()
        n = len(grp)
        for i in range(n):
            # look for a prior event 340-400 days before event i
            target_lo = dates[i] - np.timedelta64(LAG_DAYS_MAX, "D")
            target_hi = dates[i] - np.timedelta64(LAG_DAYS_MIN, "D")
            # events are sorted ascending; scan backward from i
            for j in range(i - 1, -1, -1):
                if dates[j] < target_lo:
                    break
                if target_lo <= dates[j] <= target_hi:
                    pairs.append((sues[j], sues[i], deciles[j]))
                    break  # take the closest matching prior event only

    pair_df = pd.DataFrame(pairs, columns=["sue_lag4", "sue_t", "decile_lag4"])
    print(f"Found {len(pair_df):,} same-firm quarter pairs ~340-400 days apart.")

    autocorr = pair_df["sue_lag4"].corr(pair_df["sue_t"])
    print(f"\nSUE lag-4 autocorrelation (this data): {autocorr:+.3f}")
    print("Literature reference (Bernard & Thomas 1990): approx -0.24 (reversal)")

    by_decile = (
        pair_df.groupby("decile_lag4")["sue_t"]
        .agg(["mean", "std", "count"])
        .rename(columns={"mean": "sue_t_mean", "std": "sue_t_std", "count": "n_pairs"})
        .reset_index()
        .sort_values("decile_lag4")
    )
    print("\nMean SUE at t, grouped by decile of SUE at t-4 (lag-4 quarter):")
    print(by_decile.to_string(index=False))

    d1 = by_decile.loc[by_decile["decile_lag4"] == by_decile["decile_lag4"].min(), "sue_t_mean"].iloc[0]
    d10 = by_decile.loc[by_decile["decile_lag4"] == by_decile["decile_lag4"].max(), "sue_t_mean"].iloc[0]
    print(f"\nDecile-1 (lag-4 lowest SUE) firms' current SUE averages: {d1:+.3f}")
    print(f"Decile-10 (lag-4 highest SUE) firms' current SUE averages: {d10:+.3f}")
    if d10 > d1:
        print("=> Persistence, not reversal: high stays high, low stays low.")
    else:
        print("=> Reversal confirmed: high flips low, low flips high.")

    import json
    summary = {
        "n_pairs": int(len(pair_df)),
        "autocorrelation_lag4": float(autocorr),
        "literature_reference_bernard_thomas_1990": -0.24,
        "decile1_lag4_mean_sue_t": float(d1),
        "decile10_lag4_mean_sue_t": float(d10),
        "conclusion": "persistence_not_reversal" if d10 > d1 else "reversal_confirmed",
    }
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=2)
    by_decile.to_csv(OUT_DECILE, index=False)
    print(f"\nWrote {OUT_SUMMARY} and {OUT_DECILE}")


if __name__ == "__main__":
    main()
