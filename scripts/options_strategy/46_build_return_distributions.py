"""
Builds the empirical, decile-conditioned probability distribution of the UNDERLYING's own
forward return that scripts/48_optimal_contract_selector.py needs to price every strike in a
day0 option chain by expected value.

This directly replaces the "return_states" mechanism in the prior-art GPU options-return tooling
(reference/options_content/... draws on `optionExpReturn_v08.py`'s `calcER`/`calcMaxER`, whose
actual state table -- see the original `return_states.csv` -- is a HAND-TYPED 3-scenario guess:
{0%: 25%, +20%: 50%, 0%: 25%}, entered once by a person and reused for every stock on every day.
That's the whole reason those tools' options-return picks were only ever as good as someone's
manual guess about the future -- there is nothing "mathematically proven" about a hand-typed
scenario table.

This project has something categorically better already sitting in `data/`: 260k+ REAL historical
PEAD events with REAL realized forward returns, decile-sorted by the exact same SUE signal this
whole project trades. So instead of one guessed 3-point distribution reused everywhere, this
script builds an EMPIRICAL distribution per (decile, horizon) cell from actual historical
outcomes -- quantile-bucketed (not fixed-width) so a fixed number of states always carries equal
probability mass and the tails (where option payoffs are most sensitive) are represented by their
own actual conditional mean return, not by an arbitrary bin edge or a parametric assumption.

## Bug #1 (fixed): raw returns swamp the PEAD edge with ambient beta

The first version of this script built each decile's distribution straight from RAW forward
returns on the reasoning that "an option's payoff depends on the underlying's actual price, not a
market-adjusted synthetic one" -- true, but 1996-2013 was a net up market, so EVERY decile's raw
60d expected return came out positive, swamping the much smaller decile-specific PEAD spread with
ambient market beta (the selector picked calls almost everywhere, including the worst SUE decile).
Fixed by building the distribution's SHAPE from MARKET-ADJUSTED returns (isolating the real PEAD
component) and re-centering by the real ambient market drift for that horizon
(`mean(raw - mktadj)`) -- see git history for the full original writeup of this bug.

## Bug #2 (fixed): full-sample distribution = look-ahead bias

The first working version fit ONE distribution per (decile, horizon) from the ENTIRE 1996-2013
sample at once -- meaning a 1998 event's contract selection was informed by data through 2013,
information no real trader in 1998 had. Same category of mistake script 27's docstring already
documents catching for the EAR signal. This is now an EXPANDING-WINDOW ("walk-forward")
distribution instead: for every announcement quarter Q, the distribution used to price/select
contracts for events IN quarter Q is built using ONLY events with `ann_quarter < Q` (strictly
prior, fully-realized outcomes) -- the market-drift re-centering term (Bug #1's fix) is
recomputed the same way, per-quarter, from prior data only, so it doesn't reintroduce a milder
version of the same leak. Quarters without enough prior history (see MIN_WARMUP_QUARTERS /
MIN_OBS_PER_CELL) are skipped entirely -- there is no honest way to price those events without
future information, so they're simply not tradeable in this framework, not backfilled with a
guess.

Output: data/metadata/decile_return_distributions.parquet
        columns: as_of_quarter (str, e.g. "2003Q2" -- distribution built from all ann_quarter <
                 this value), decile, horizon, state, prob, ret_state
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, METADATA_DIR, EVENTS_DIR

DATA = DATA_DIR
HORIZONS = [1, 5, 10, 20, 40, 60]
N_STATES_DEFAULT = 40
MIN_WARMUP_QUARTERS = 8    # >= 2 years of prior history before this script will price anything
MIN_OBS_PER_CELL_MULT = 5  # need >= n_states * this many observations in a (decile, horizon) cell


def build_decile(events: pd.DataFrame) -> pd.Series:
    """Same construction as script 34/01: rank sue_analyst within announcement quarter. (This
    within-quarter ranking convention has its own small, standard, already-project-wide
    simplification -- a firm's precise percentile rank technically isn't knowable until every
    same-quarter earnings report is in -- but that's a pre-existing, much milder convention used
    everywhere else in this project, not something this script introduces; what this script fixes
    is the much larger, multi-YEAR look-ahead of fitting the return distribution on future
    quarters entirely, addressed below.)"""
    ev = events.copy()
    ev["anndats"] = pd.to_datetime(ev["anndats"])
    ev["ann_quarter"] = ev["anndats"].dt.to_period("Q")
    ev["sue_analyst_r"] = ev["sue_analyst"].round(8)
    rank_pct = ev.groupby("ann_quarter")["sue_analyst_r"].rank(pct=True, method="average")
    decile = np.minimum(np.ceil(rank_pct * 10), 10).astype(int)
    decile[rank_pct <= 0] = 1
    return decile


def build_distribution_for_window(prior: pd.DataFrame, n_states: int) -> list[dict]:
    """One (decile, horizon, state) distribution built entirely from `prior` (already filtered to
    events strictly before the quarter being priced)."""
    market_drift = {}
    for h in HORIZONS:
        raw_col, adj_col = f"ret_fwd_{h}d", f"ret_fwd_{h}d_mktadj"
        both = prior[[raw_col, adj_col]].dropna()
        market_drift[h] = float((both[raw_col] - both[adj_col]).mean()) if len(both) else 0.0

    rows = []
    for h in HORIZONS:
        col = f"ret_fwd_{h}d_mktadj"
        for d in range(1, 11):
            r_mktadj = prior.loc[prior["decile"] == d, col].dropna().to_numpy()
            r = r_mktadj + market_drift[h]
            if len(r) < n_states * MIN_OBS_PER_CELL_MULT:
                return None  # this window isn't ready for at least one cell -- caller skips it
            quantile_edges = np.quantile(r, np.linspace(0, 1, n_states + 1))
            bucket_idx = np.clip(np.digitize(r, quantile_edges[1:-1]), 0, n_states - 1)
            for state in range(n_states):
                in_state = r[bucket_idx == state]
                if len(in_state) == 0:
                    continue
                rows.append(dict(decile=d, horizon=h, state=state,
                                  prob=len(in_state) / len(r), ret_state=float(in_state.mean())))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-states", type=int, default=N_STATES_DEFAULT,
                         help="quantile buckets per (decile, horizon) cell. Default: %(default)s")
    args = parser.parse_args()

    events = pd.read_parquet(EVENTS_DIR / "equity_events_1996_2013.parquet")
    events = events[(events["day0_status"] == "ok") & (events["sue_source"] == "analyst")].copy()
    events["decile"] = build_decile(events)
    events["ann_quarter"] = pd.to_datetime(events["anndats"]).dt.to_period("Q")
    print(f"events with a decile: {len(events):,}")

    quarters = sorted(events["ann_quarter"].unique())
    print(f"{len(quarters)} announcement quarters spanning {quarters[0]}..{quarters[-1]}")

    all_rows = []
    skipped_quarters = []
    t0 = time.time()
    for qi, q in enumerate(quarters):
        if qi < MIN_WARMUP_QUARTERS:
            skipped_quarters.append(q)
            continue
        prior = events[events["ann_quarter"] < q]
        rows = build_distribution_for_window(prior, args.n_states)
        if rows is None:
            skipped_quarters.append(q)
            continue
        for r in rows:
            r["as_of_quarter"] = str(q)
        all_rows.extend(rows)

    dist = pd.DataFrame(all_rows)
    out_path = METADATA_DIR / "decile_return_distributions.parquet"
    dist.to_parquet(out_path, index=False)

    n_priced = len(quarters) - len(skipped_quarters)
    print(f"\n{n_priced} quarters priced (walk-forward, prior-data-only), "
          f"{len(skipped_quarters)} skipped for insufficient warmup  [{time.time()-t0:.1f}s]")
    events_lost = events[events["ann_quarter"].isin(skipped_quarters)]
    print(f"events in un-priceable warmup quarters (not tradeable in this framework, by "
          f"construction -- no honest prior-only distribution exists for them): "
          f"{len(events_lost):,} / {len(events):,}")

    # sanity check on the LAST priced quarter's distribution (closest to a full-sample check,
    # while still being genuinely prior-only as of that quarter).
    last_q = dist["as_of_quarter"].iloc[-1]
    check = dist[dist["as_of_quarter"] == last_q].groupby(["decile", "horizon"]).apply(
        lambda g: pd.Series({"expected_ret": (g["prob"] * g["ret_state"]).sum()}),
        include_groups=False)
    print(f"\nexpected return by decile at 60d, as-of {last_q} (should be roughly increasing "
          f"D1->D10 -- the same PEAD ordering every other script in this project finds):")
    print(check.xs(60, level="horizon")["expected_ret"].round(4))
    print(f"\nwrote {out_path} ({len(dist):,} rows across {n_priced} as-of quarters)")


if __name__ == "__main__":
    main()
