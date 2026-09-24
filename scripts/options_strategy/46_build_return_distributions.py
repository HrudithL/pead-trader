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

## --vol-conditioned: condition on the stock's own pre-event volatility too

The default distribution is conditioned on decile alone, so a 70%-vol stock and a 20%-vol stock in
the same decile are priced off the same return spread -- wrong for options, whose payoffs depend
mostly on that spread. The equity-side diagnostic (equity_strategy/40_vol_decile_diagnostic.py) also
found the PEAD drift itself is WEAKER in high-vol stocks, so the center is off too, not just the
width. With --vol-conditioned this script instead builds one distribution per (decile, vol tercile)
cell, with each historical return first divided by that stock's own trailing 60-day daily vol
(common.volatility, days strictly before day0). States are stored in those vol units (z_state); 48
turns them back into returns for a specific event as market_drift + z_state * that event's own
tvol_60d. Vol terciles use the PREVIOUS announcement quarter's 33rd/67th tvol percentiles (stored per
row as tvol_lo/tvol_hi, so 48 classifies the event being priced with the exact same breakpoints),
and the same walk-forward (strictly prior quarters) rule as the default mode.

--vol-units return keeps the same (decile, vol tercile) cells but leaves returns in plain return
units (z_state is then a return, and 48 adds it to market_drift without rescaling by the event's
own vol). It separates "condition on the vol tercile" from "rescale every stock by its own
trailing vol", which matters because trailing vol mean-reverts (see the Tier 1.5 vol results).

Output (--vol-conditioned): data/metadata/decile_return_distributions_vol.parquet
        (--vol-units return: decile_return_distributions_volterc.parquet)
        columns: as_of_quarter, decile, vol_tercile (0 low / 1 / 2 high), horizon, state, prob,
                 z_state, market_drift, tvol_lo, tvol_hi, units ("vol" or "return")
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import DATA_DIR, METADATA_DIR, EVENTS_DIR, RAW_EQUITY_DIR
from common.volatility import event_total_vol

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


def prior_quarter_tvol_breakpoints(events: pd.DataFrame) -> dict:
    """{quarter Q: (lo, hi)} = 33rd/67th percentile of tvol_60d among events announced in the
    quarter BEFORE Q -- the breakpoints usable to classify an event announced in Q."""
    q_sorted = sorted(events["ann_quarter"].unique())
    out = {}
    for q_prev, q in zip(q_sorted[:-1], q_sorted[1:]):
        ref = events.loc[events["ann_quarter"] == q_prev, "tvol_60d"].dropna()
        if len(ref) >= 30:
            out[q] = tuple(np.quantile(ref, [1 / 3, 2 / 3]))
    return out


def build_vol_distribution_for_window(prior: pd.DataFrame, n_states: int, units: str = "vol") -> list[dict]:
    """--vol-conditioned counterpart of build_distribution_for_window: one distribution per
    (decile, vol_tercile, horizon), states in units of each stock's own daily vol."""
    market_drift = {}
    for h in HORIZONS:
        raw_col, adj_col = f"ret_fwd_{h}d", f"ret_fwd_{h}d_mktadj"
        both = prior[[raw_col, adj_col]].dropna()
        market_drift[h] = float((both[raw_col] - both[adj_col]).mean()) if len(both) else 0.0

    usable = prior[prior["vol_tercile"].notna() & (prior["tvol_60d"] > 0)]
    rows = []
    for h in HORIZONS:
        col = f"ret_fwd_{h}d_mktadj"
        for (d, t), g in usable.groupby(["decile", "vol_tercile"]):
            z = (g[col] / g["tvol_60d"] if units == "vol" else g[col]).dropna().to_numpy()
            if len(z) < n_states * MIN_OBS_PER_CELL_MULT:
                return None
            quantile_edges = np.quantile(z, np.linspace(0, 1, n_states + 1))
            bucket_idx = np.clip(np.digitize(z, quantile_edges[1:-1]), 0, n_states - 1)
            for state in range(n_states):
                in_state = z[bucket_idx == state]
                if len(in_state) == 0:
                    continue
                rows.append(dict(decile=int(d), vol_tercile=int(t), horizon=h, state=state,
                                  prob=len(in_state) / len(z), z_state=float(in_state.mean()),
                                  market_drift=market_drift[h], units=units))
    if usable.groupby(["decile", "vol_tercile"]).ngroups < 30:
        return None
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-states", type=int, default=N_STATES_DEFAULT,
                         help="quantile buckets per (decile, horizon) cell. Default: %(default)s")
    parser.add_argument("--vol-conditioned", action="store_true",
                         help="build per-(decile, vol tercile) distributions in units of each "
                              "stock's own pre-event vol (see docstring); writes "
                              "decile_return_distributions_vol.parquet instead")
    parser.add_argument("--vol-units", choices=["vol", "return"], default="vol",
                         help="with --vol-conditioned: states in units of each stock's own daily "
                              "vol (default) or plain returns (writes ..._volterc.parquet)")
    args = parser.parse_args()

    events = pd.read_parquet(EVENTS_DIR / "equity_events_1996_2013.parquet")
    events = events[(events["day0_status"] == "ok") & (events["sue_source"] == "analyst")].copy()
    events["decile"] = build_decile(events)
    events["ann_quarter"] = pd.to_datetime(events["anndats"]).dt.to_period("Q")
    print(f"events with a decile: {len(events):,}")

    quarters = sorted(events["ann_quarter"].unique())
    print(f"{len(quarters)} announcement quarters spanning {quarters[0]}..{quarters[-1]}")

    if args.vol_conditioned:
        return main_vol_conditioned(events, quarters, args)

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


def main_vol_conditioned(events, quarters, args):
    t0 = time.time()
    events = events[events["permno"].notna()].copy()
    events["tvol_60d"] = event_total_vol(events["permno"], events["day0_date"], RAW_EQUITY_DIR)
    print(f"tvol_60d coverage: {events['tvol_60d'].notna().mean():.1%}  [{time.time()-t0:.1f}s]")
    bps = prior_quarter_tvol_breakpoints(events)
    lo = events["ann_quarter"].map(lambda q: bps.get(q, (np.nan, np.nan))[0]).astype(float)
    hi = events["ann_quarter"].map(lambda q: bps.get(q, (np.nan, np.nan))[1]).astype(float)
    v = events["tvol_60d"]
    events["vol_tercile"] = np.where(v.isna() | lo.isna(), np.nan,
                                      np.where(v <= lo, 0, np.where(v <= hi, 1, 2)))

    all_rows, skipped = [], []
    for qi, q in enumerate(quarters):
        if qi < MIN_WARMUP_QUARTERS or q not in bps:
            skipped.append(q)
            continue
        rows = build_vol_distribution_for_window(events[events["ann_quarter"] < q], args.n_states,
                                                  args.vol_units)
        if rows is None:
            skipped.append(q)
            continue
        for r in rows:
            r["as_of_quarter"] = str(q)
            r["tvol_lo"], r["tvol_hi"] = bps[q]
        all_rows.extend(rows)

    dist = pd.DataFrame(all_rows)
    suffix = "vol" if args.vol_units == "vol" else "volterc"
    out_path = METADATA_DIR / f"decile_return_distributions_{suffix}.parquet"
    dist.to_parquet(out_path, index=False)
    print(f"{len(quarters) - len(skipped)} quarters priced, {len(skipped)} skipped "
          f"(warmup / thin cells)  [{time.time()-t0:.1f}s]")
    last_q = dist["as_of_quarter"].iloc[-1]
    last = dist[(dist["as_of_quarter"] == last_q) & (dist["horizon"] == 60)].copy()
    last["contrib"] = last["prob"] * last["z_state"]
    ez = last.groupby(["decile", "vol_tercile"])["contrib"].sum().unstack("vol_tercile")
    unit_label = "DAILY-VOL units" if args.vol_units == "vol" else "return units"
    print(f"\nexpected 60d mkt-adj return in {unit_label} by decile x vol tercile, as-of {last_q}:")
    print(ez.round(3).to_string())
    print(f"\nwrote {out_path} ({len(dist):,} rows)")


if __name__ == "__main__":
    main()
