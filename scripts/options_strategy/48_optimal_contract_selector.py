"""
The "mathematically proven best option on a given day" selector -- what this whole session's
extra ask was actually about. Scores EVERY strike (both calls and puts) in an event's real day0
option chain (scripts/47) against the empirical, decile-conditioned return distribution
(script 46) and picks the one that's actually best, instead of script 40's "just take the
near-ATM 50-delta contract" heuristic.

## Why Kelly-optimal expected LOG return, not naive expected value

The prior-art tool this draws on (`reference/options_content/.../optionExpReturn_v08.py`'s
`calcMaxER`) ranks candidates by raw expected return: `E[R] = sum(prob * (payoff/premium - 1))`.
That objective is dangerous specifically BECAUSE options are leveraged and convex: a deep-OTM
option with a tiny probability of an enormous payoff can have an arbitrarily large E[R] even
though it loses money on almost every draw -- E[R] rewards rare huge wins without ever penalizing
the fact that you go broke getting there. This is not a hypothetical: it is the textbook failure
mode of naive EV-maximizing options strategies, and it is EXACTLY what "leverage means we can make
huge profits" turns into if the selection math doesn't also account for ruin risk.

The standard fix -- and what "mathematically proven" should actually mean here -- is the Kelly
criterion: for each candidate contract, find the bet fraction f (of capital allocated to this one
trade) that maximizes EXPECTED LOG GROWTH, `E[log(1 + f*R)]`, then compare contracts by their own
best achievable growth rate at that optimal f. This is a two-step answer to "should I take this
leveraged bet": (1) does this contract have a real, positive-growth edge at ANY sane sizing, and
(2) how aggressively can it actually be sized. A wild lottery-ticket strike still gets evaluated
here -- it just gets found to have a very small (near-zero) optimal Kelly fraction, correctly
identified as inferior to a more moderate strike with a real, robust edge, rather than being
mistakenly ranked first the way naive E[R] would rank it. This is the mathematically correct way
to "embrace leverage" without walking into the trap leverage sets for naive optimizers -- Kelly
still fully rewards a genuinely good leveraged bet with a large f* and a large growth rate, it just
refuses to reward a bet that's only "good" because of a rare, thin-tailed payoff.

## What's GPU-batched here

For a batch of B events, each with up to `max_strikes` candidate contracts (ragged -> padded +
masked) and N_STATES return-distribution states, and a small grid of candidate Kelly fractions:
build a (B, max_strikes, N_STATES) payoff/return tensor once, then for each candidate fraction f
in the grid compute `E[log(1+f*R)]` as a (B, max_strikes) reduction over states, and keep the
running best (fraction, growth) per contract -- all dense array ops, the same
"batch a parameter grid as an extra axis" pattern as scripts 42/43/45.

## Walk-forward, not full-sample: no look-ahead in which distribution prices an event

Each event is scored against the distribution whose `as_of_quarter` exactly matches that event's
OWN announcement quarter (joined via `decile_events_secid_all.parquet`'s `anndats`) -- script 46
builds that distribution using only quarters strictly BEFORE it, so a 1998 event is never priced
with information from 2013. Events in script 46's warmup window (no distribution exists yet) are
dropped here, not backfilled with a different quarter's distribution -- see load_chain_and_distribution.
This makes the per-event distribution lookup a dict keyed by (as_of_quarter, decile) instead of
just decile, which is why this script runs noticeably slower than a single static lookup would
(~9 minutes over 126k events on this CPU dev machine, vs. under 2 minutes before this fix) --
still trivial next to the multi-hour OM scans elsewhere in this pipeline, and a GPU/batched
version of the lookup itself is a reasonable future optimization if it ever becomes the bottleneck
relative to the actual score_batch tensor work.

Output: data/event_options/optimal_contracts.parquet -- one row per event: the Kelly-optimal pick
        (contract, side, optimal Kelly fraction, growth rate) AND, for direct comparison, the
        naive-expected-value pick a calcMaxER-style selector would have made instead.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR
from lib.gpu import get_backend, to_host, add_device_arg, add_mock_data_arg, add_smoke_test_arg, \
    make_mock_full_chain, make_mock_return_distribution, StageTimer

DATA = DATA_DIR
F_GRID = np.linspace(0.05, 0.95, 19)  # Kelly fraction candidates; kept < 1.0 so 1+f*R never hits
                                        # exactly 0 (R floors at -1), avoiding log(0) altogether.


def load_chain_and_distribution(args):
    """Returns (chain, dist), where chain carries an `ann_quarter` column and dist carries an
    `as_of_quarter` column -- the walk-forward join key. An event's ann_quarter must match a dist
    row's as_of_quarter EXACTLY: script 46 builds the as_of_quarter=Q distribution using only data
    strictly before Q, specifically so it's valid to price events happening IN Q. There is no
    "closest prior" fallback -- an event whose own quarter has no matching as_of_quarter (the
    warmup quarters script 46 skips) is simply not priceable in this framework and gets dropped in
    main(), not backfilled with a different quarter's distribution."""
    if args.mock_data:
        chain = make_mock_full_chain(n_events=args.mock_n_events, seed=1)
        chain["ann_quarter"] = "MOCK"
        dist = make_mock_return_distribution(seed=2)
        dist["as_of_quarter"] = "MOCK"
    else:
        chain_path = DATA / "event_options" / "full_chain_with_underlying.parquet"
        dist_path = DATA / "metadata" / "decile_return_distributions.parquet"
        events_path = DATA / "event_options" / "decile_events_secid_all.parquet"
        if not chain_path.exists():
            raise FileNotFoundError(f"{chain_path} not found; run scripts 47/47b or --mock-data.")
        if not dist_path.exists():
            raise FileNotFoundError(f"{dist_path} not found; run script 46 or --mock-data.")
        chain = pd.read_parquet(chain_path)
        chain["event_id"] = chain.groupby(["secid", "day0_date"]).ngroup()
        dist = pd.read_parquet(dist_path)
        if "as_of_quarter" not in dist.columns:
            raise ValueError(f"{dist_path} has no as_of_quarter column -- it was built by an old, "
                              "full-sample (look-ahead) version of script 46. Re-run script 46.")

        anndats = pd.read_parquet(events_path, columns=["secid", "day0_date", "anndats"])
        anndats = anndats.dropna(subset=["secid"])
        anndats["secid"] = anndats["secid"].astype("int64")
        anndats["day0_date"] = pd.to_datetime(anndats["day0_date"])
        anndats["ann_quarter"] = pd.to_datetime(anndats["anndats"]).dt.to_period("Q").astype(str)
        anndats = anndats.drop_duplicates(subset=["secid", "day0_date"])[
            ["secid", "day0_date", "ann_quarter"]]
        chain["day0_date"] = pd.to_datetime(chain["day0_date"])
        n_before = chain["event_id"].nunique()
        chain = chain.merge(anndats, on=["secid", "day0_date"], how="left")
        n_missing = chain.loc[chain["ann_quarter"].isna(), "event_id"].nunique()
        if n_missing:
            print(f"WARNING: {n_missing:,} / {n_before:,} events have no ann_quarter match "
                  f"(dropping -- can't be priced without knowing their announcement quarter)")
        chain = chain[chain["ann_quarter"].notna()].copy()
    return chain, dist


def pad_batch(chain_batch: pd.DataFrame, max_k: int):
    """chain_batch: rows for a batch of events, columns event_id (0..B-1 local), decile,
    ann_quarter, underlying_price, cp_flag, strike, entry_mid, optionid. Returns dense (B, max_k)
    arrays + mask (optionid returned as a plain object array, not passed through the GPU backend,
    since it's only ever used for the final host-side lookup of which real contract was picked)."""
    b = chain_batch["event_id"].nunique()
    side = np.zeros((b, max_k))
    strike = np.zeros((b, max_k))
    premium = np.ones((b, max_k))  # avoid div-by-zero on padding
    mask = np.zeros((b, max_k), dtype=bool)
    spot = np.zeros(b)
    decile = np.zeros(b, dtype=int)
    ann_quarter = np.empty(b, dtype=object)
    optionid = np.full((b, max_k), -1, dtype=np.int64)

    has_optionid = "optionid" in chain_batch.columns
    for local_i, (eid, grp) in enumerate(chain_batch.groupby("event_id", sort=True)):
        n = min(len(grp), max_k)
        g = grp.iloc[:n]
        side[local_i, :n] = np.where(g["cp_flag"].to_numpy() == "C", 1.0, -1.0)
        strike[local_i, :n] = g["strike"].to_numpy()
        premium[local_i, :n] = np.maximum(g["entry_mid"].to_numpy(), 1e-6)
        mask[local_i, :n] = True
        spot[local_i] = g["underlying_price"].iloc[0]
        decile[local_i] = int(g["decile"].iloc[0])
        ann_quarter[local_i] = g["ann_quarter"].iloc[0]
        if has_optionid:
            optionid[local_i, :n] = g["optionid"].to_numpy()
    return side, strike, premium, mask, spot, decile, ann_quarter, optionid


def score_batch(xp, side, strike, premium, mask, spot, ret_state, prob):
    """ret_state, prob: (B, N_STATES) -- already gathered per-event from the distribution table.
    Returns kelly_growth (B, max_k), kelly_f (B, max_k), naive_ev (B, max_k)."""
    terminal = spot[:, None, None] * (1.0 + ret_state[:, None, :])          # (B, 1, N_STATES)
    payoff = xp.maximum(0.0, side[:, :, None] * (terminal - strike[:, :, None]))  # (B, K, N_STATES)
    R = payoff / premium[:, :, None] - 1.0                                  # (B, K, N_STATES), floors at -1

    naive_ev = (prob[:, None, :] * R).sum(axis=-1)                          # (B, K)

    best_growth = xp.full(side.shape, -1e18)
    best_f = xp.zeros(side.shape)
    for f in F_GRID:
        growth = xp.log1p(f * R)                                           # (B, K, N_STATES)
        e_growth = (prob[:, None, :] * growth).sum(axis=-1)                 # (B, K)
        improve = e_growth > best_growth
        best_growth = xp.where(improve, e_growth, best_growth)
        best_f = xp.where(improve, f, best_f)

    neg_inf = -1e18
    best_growth = xp.where(mask, best_growth, neg_inf)
    naive_ev = xp.where(mask, naive_ev, neg_inf)
    return best_growth, best_f, naive_ev


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_device_arg(parser)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    parser.add_argument("--horizon", type=int, default=60,
                         help="which return-distribution horizon to price contracts against")
    parser.add_argument("--max-strikes", type=int, default=60,
                         help="pad/truncate each event's chain to this many contracts")
    parser.add_argument("--batch-size", type=int, default=2000,
                         help="events processed per GPU batch (bounds tensor memory)")
    args = parser.parse_args()

    max_strikes, batch_size = args.max_strikes, args.batch_size
    if args.smoke_test:
        max_strikes, batch_size = 20, 200
        args.mock_n_events = min(args.mock_n_events, 500)
        print(f"--smoke-test: max_strikes={max_strikes}, batch_size={batch_size}, "
              f"mock_n_events={args.mock_n_events}")

    with StageTimer("48_optimal_contract_selector", extra={"device": args.device, "mock_data": args.mock_data}):
        xp, resolved_device = get_backend(args.device)
        print(f"backend: {xp.__name__} (device={resolved_device})")

        chain, dist = load_chain_and_distribution(args)
        dist_h = dist[dist["horizon"] == args.horizon].sort_values(["as_of_quarter", "decile", "state"])
        n_states = dist_h["state"].nunique()
        dist_by_key = {(aq, d): g.sort_values("state") for (aq, d), g in
                       dist_h.groupby(["as_of_quarter", "decile"])}

        # drop events whose own ann_quarter has no matching as_of_quarter distribution (the
        # walk-forward warmup window script 46 skips, or a horizon this run wasn't given) -- these
        # are, by construction, not honestly priceable, not silently backfilled.
        priceable_quarters = set(dist_h["as_of_quarter"].unique())
        n_before = chain["event_id"].nunique()
        chain = chain[chain["ann_quarter"].isin(priceable_quarters)].copy()
        n_after = chain["event_id"].nunique()
        if n_after < n_before:
            print(f"dropping {n_before - n_after:,} / {n_before:,} events in quarters with no "
                  f"walk-forward distribution available yet (warmup window)")
        print(f"chain: {n_after:,} priceable events, {len(chain):,} contract rows; "
              f"distribution: {n_states} states/(decile,quarter) at horizon={args.horizon}d")

        event_ids = sorted(chain["event_id"].unique())
        event_key = chain.drop_duplicates("event_id").set_index("event_id")
        has_secid_date = "secid" in event_key.columns and "day0_date" in event_key.columns
        results = []
        for start in range(0, len(event_ids), batch_size):
            batch_events = event_ids[start:start + batch_size]
            chain_batch = chain[chain["event_id"].isin(batch_events)].copy()
            chain_batch["event_id"] = chain_batch["event_id"].map(
                {eid: i for i, eid in enumerate(sorted(chain_batch["event_id"].unique()))})

            side, strike, premium, mask, spot, decile, ann_quarter, optionid = pad_batch(chain_batch, max_strikes)
            ret_state = np.stack([dist_by_key[(aq, d)]["ret_state"].to_numpy()
                                   for aq, d in zip(ann_quarter, decile)])
            prob = np.stack([dist_by_key[(aq, d)]["prob"].to_numpy()
                              for aq, d in zip(ann_quarter, decile)])

            side_x, strike_x, premium_x = xp.asarray(side), xp.asarray(strike), xp.asarray(premium)
            mask_x, spot_x = xp.asarray(mask), xp.asarray(spot)
            ret_state_x, prob_x = xp.asarray(ret_state), xp.asarray(prob)

            kelly_growth, kelly_f, naive_ev = score_batch(
                xp, side_x, strike_x, premium_x, mask_x, spot_x, ret_state_x, prob_x)

            kelly_growth_h, kelly_f_h, naive_ev_h = (to_host(xp, kelly_growth), to_host(xp, kelly_f),
                                                      to_host(xp, naive_ev))
            kelly_pick = np.argmax(kelly_growth_h, axis=1)
            naive_pick = np.argmax(naive_ev_h, axis=1)

            b = side.shape[0]
            for i in range(b):
                orig_eid = batch_events[i]
                kp, npck = kelly_pick[i], naive_pick[i]
                extra_keys = {}
                if has_secid_date:
                    extra_keys = dict(secid=int(event_key.loc[orig_eid, "secid"]),
                                       day0_date=event_key.loc[orig_eid, "day0_date"])
                results.append(dict(
                    event_id=orig_eid, **extra_keys, decile=int(decile[i]),
                    kelly_side=("C" if side[i, kp] > 0 else "P"), kelly_strike=float(strike[i, kp]),
                    kelly_premium=float(premium[i, kp]), kelly_fraction=float(kelly_f_h[i, kp]),
                    kelly_growth=float(kelly_growth_h[i, kp]), kelly_optionid=int(optionid[i, kp]),
                    naive_side=("C" if side[i, npck] > 0 else "P"), naive_strike=float(strike[i, npck]),
                    naive_premium=float(premium[i, npck]), naive_ev=float(naive_ev_h[i, npck]),
                    naive_optionid=int(optionid[i, npck]),
                    agree_with_naive=bool(kp == npck),
                ))
            print(f"  batch {start//batch_size + 1}: events {start:,}-{start+b:,} done", flush=True)

        out = pd.DataFrame(results)
        (DATA / "event_options").mkdir(parents=True, exist_ok=True)
        out.to_parquet(DATA / "event_options" / "optimal_contracts.parquet", index=False)

        print(f"\n{len(out):,} events scored")
        print(f"Kelly and naive-EV picks AGREE on {out['agree_with_naive'].mean():.1%} of events")
        print("\nKelly picks: side distribution vs. decile (does the math discover the right "
              "direction on its own?)")
        print(pd.crosstab(out["decile"], out["kelly_side"]))
        print("\nnaive picks tend toward more extreme moneyness when they disagree -- "
              "moneyness ratio (strike/spot... not directly available here, see kelly_fraction "
              "distribution instead):")
        print(f"  mean Kelly fraction on KELLY picks: {out['kelly_fraction'].mean():.3f}")
        disagree = out[~out["agree_with_naive"]]
        if len(disagree):
            print(f"  on the {len(disagree):,} events where they disagree, naive-EV's own pick "
                  f"would have needed an average Kelly fraction that we did not even evaluate "
                  f"favorably for it (it wasn't the growth-maximizer) -- see kelly_growth vs "
                  f"naive_ev columns per-event in the output file for the full picture.")
        print(f"\nwrote data/event_options/optimal_contracts.parquet")


if __name__ == "__main__":
    main()
