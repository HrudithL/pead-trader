"""
Shared infrastructure for the options-strategy GPU pipeline (scripts 40+).

Every script in this pipeline is a plain, argparse-driven .py file (no notebooks) so it runs
identically three ways: locally on this CPU-only dev machine, via `colab run --gpu T4 script.py`
/ `colab exec -f script.py` (the Google Colab CLI, Linux/macOS only, released June 2026) for
smoke-testing on a small Colab GPU, and unattended on the Linux 5090 box for the real run.

The device backend (numpy<->cupy), --device/--smoke-test flags, and the checkpoint/status-log
machinery (StageTimer) are generic across every GPU-tiered pipeline in this repo and live in
common/gpu.py -- re-exported here for backward compatibility with every options_strategy script
that already does `from lib.gpu import get_backend, ...`. This module keeps only what's
options-specific:

1. A `--mock-data` path that fabricates a synthetic event panel with the real panel's exact
   schema and realistic statistical properties (decile-ordered drift, matching the actually
   measured spreads in data/option_spread_horizon_stats.csv). This lets every stage be developed,
   unit-tested, and Colab-smoke-tested WITHOUT ever needing access to the licensed OptionMetrics
   drive (which cannot leave its physical drive -- see README) or the multi-hour real scan.
2. The mock event-panel / daily-path / return-distribution / full-chain generators themselves.

See scripts/equity_strategy/lib/gpu.py for the equity-strategy pipeline's equivalent mock-data
generators, built on the same common/gpu.py backend.
"""
import numpy as np
import pandas as pd

from common.gpu import (  # noqa: F401 -- re-exported for `from lib.gpu import ...` call sites
    get_backend, to_host, add_device_arg, add_smoke_test_arg, StageTimer, stage_done, STATUS_PATH,
)


def add_mock_data_arg(parser):
    parser.add_argument("--mock-data", action="store_true",
                         help="use a synthetic event panel instead of reading the real "
                              "OptionMetrics-derived panel. Use this on Colab or anywhere the "
                              "licensed OptionMetrics drive isn't physically attached -- it "
                              "exercises the identical code path at a chosen scale.")
    parser.add_argument("--mock-n-events", type=int, default=20_000,
                         help="number of synthetic events when --mock-data is set "
                              "(real panel is ~165,000). Default: %(default)s")
    return parser


# ---------------------------------------------------------------------------
# Synthetic mock event panel (schema-compatible with data/event_options/option_event_panel.parquet)
# ---------------------------------------------------------------------------

# Empirically measured D10-D1 spreads at each horizon, from data/option_spread_horizon_stats.csv
# (script 37's output on the real panel). The mock generator reproduces this exact decile-ordered
# drift so code that measures "does the strategy respond correctly to the known signal" behaves
# the same on mock data as on the real panel, instead of just being random noise.
_MEASURED_SPREAD_60D = {"call": 0.14381, "put": -0.10794, "straddle": 0.03982}
_HORIZONS = (1, 5, 10, 20, 40, 60)
_FF12_SECTORS = [f"sector_{i}" for i in range(1, 13)]


def make_mock_option_event_panel(n_events=20_000, seed=0) -> pd.DataFrame:
    """Synthetic stand-in for data/event_options/option_event_panel.parquet. Deciles 1-10,
    quarters spanning 1996Q1-2013Q2 (matching the real coverage window), and per-horizon
    call/put/straddle forward returns whose decile means match the measured spreads above (with
    per-event noise), so downstream GPU kernels see the same directional signal-to-noise
    character as the real data without touching any licensed OptionMetrics file."""
    rng = np.random.default_rng(seed)
    decile = rng.integers(1, 11, size=n_events)
    quarters = pd.period_range("1996Q1", "2013Q2", freq="Q")
    quarters = quarters[quarters.year != 2011]  # matches the real 2011 opprcd coverage hole
    ann_quarter = rng.choice(quarters.astype(str), size=n_events)
    ff12_sector = rng.choice(_FF12_SECTORS, size=n_events)
    size_quintile = rng.integers(1, 6, size=n_events)

    df = pd.DataFrame({
        "event_id": np.arange(n_events),
        "decile": decile,
        "ann_quarter": ann_quarter,
        "ff12_sector": ff12_sector,
        "size_quintile": size_quintile,
    })

    entry_price = rng.uniform(5.0, 80.0, size=n_events)
    df["call_entry_mid"] = entry_price
    df["put_entry_mid"] = entry_price * rng.uniform(0.7, 1.0, size=n_events)
    df["straddle_entry_mid"] = df["call_entry_mid"] + df["put_entry_mid"]

    # decile rank in [-1, 1], scaled so D1/D10 hit roughly the measured spread magnitude at 60d,
    # with earlier horizons scaled down proportionally to the measured term structure.
    rank = (decile - 5.5) / 4.5
    for cp_type, spread_60d in _MEASURED_SPREAD_60D.items():
        for h in _HORIZONS:
            term_scale = h / 60.0
            signal = rank * (spread_60d / 2.0) * term_scale
            noise = rng.normal(0.0, 0.25 * max(abs(spread_60d), 0.02), size=n_events)
            ret = signal + noise
            df[f"{cp_type}_ret_fwd_{h}d"] = ret
            entry_col = f"{cp_type}_entry_mid"
            df[f"{cp_type}_fwd_mid_{h}d"] = df[entry_col] * (1.0 + ret)

    df["call_optionid"] = np.arange(n_events)
    df["put_optionid"] = np.arange(n_events) + 10_000_000
    return df


def make_mock_daily_paths(n_events=2_000, max_days=60, seed=0):
    """Synthetic stand-in for the Tier 2 daily price-path panel (script 41's real output: one
    row per (event, trading day offset) with that day's mid price). Real daily OM data has
    quote gaps (thin trading); this reproduces that by randomly dropping ~15% of days per event
    (forward-filled downstream, same convention as script 40's checkpoint handling).

    Returns a dense (n_events, max_days+1) numpy array of mid prices (entry price at column 0),
    NaN-forward-filled so every cell is populated -- exactly the shape script 42's exit-optimizer
    kernel expects, whether it came from this mock generator or the real daily panel."""
    rng = np.random.default_rng(seed)
    entry_price = rng.uniform(5.0, 80.0, size=n_events)
    decile = rng.integers(1, 11, size=n_events)
    drift_per_day = (decile - 5.5) / 4.5 * 0.0025          # matches the mock event panel's scale
    daily_vol = rng.uniform(0.02, 0.06, size=n_events)     # near-ATM options are noisy day to day

    log_returns = rng.normal(drift_per_day[:, None], daily_vol[:, None], size=(n_events, max_days))
    log_path = np.cumsum(log_returns, axis=1)
    price_path = entry_price[:, None] * np.exp(log_path)
    price_path = np.clip(price_path, 0.01, None)  # option premium can't go negative
    full = np.column_stack([entry_price, price_path])

    drop_mask = rng.random(full.shape) < 0.15
    drop_mask[:, 0] = False  # entry price is always known
    full_with_gaps = np.where(drop_mask, np.nan, full)
    filled = pd.DataFrame(full_with_gaps).ffill(axis=1).to_numpy()
    return filled, decile


def make_mock_return_distribution(n_states=40, seed=0):
    """Synthetic stand-in for data/metadata/decile_return_distributions.parquet -- a
    (decile, horizon, state) empirical-style distribution, reproducing the same decile-ordered
    drift as the real one without needing the real historical panel. Equal-probability states
    (1/n_states each, matching the real quantile-bucketing method) with a log-normal-ish spread
    per decile so tails look realistic for pricing option payoffs against."""
    rng = np.random.default_rng(seed)
    n_samples = 20_000
    rows = []
    for decile in range(1, 11):
        rank = (decile - 5.5) / 4.5
        for horizon in [1, 5, 10, 20, 40, 60]:
            mu = rank * 0.02 * (horizon / 60.0)
            sigma = 0.15 * np.sqrt(horizon / 60.0)
            # empirical quantile bucketing of a large lognormal sample -- same method script 46
            # uses on real data (equal-probability states, each valued at its own bucket mean),
            # just applied to a synthetic sample instead of real historical events.
            sample = np.exp(rng.normal(mu, sigma, size=n_samples)) - 1.0
            edges = np.quantile(sample, np.linspace(0, 1, n_states + 1))
            bucket_idx = np.clip(np.digitize(sample, edges[1:-1]), 0, n_states - 1)
            for state in range(n_states):
                in_state = sample[bucket_idx == state]
                ret_state = float(in_state.mean()) if len(in_state) else 0.0
                rows.append(dict(decile=decile, horizon=horizon, state=state,
                                  prob=1.0 / n_states, ret_state=ret_state))
    return pd.DataFrame(rows)


def make_mock_full_chain(n_events=2_000, max_strikes=30, seed=0):
    """Synthetic stand-in for data/event_options/full_chain_with_underlying.parquet: one row per
    (event, contract), with a ragged number of strikes per event (padded/masked downstream by the
    caller). Columns: event_id, decile, underlying_price, cp_flag, strike, entry_mid."""
    rng = np.random.default_rng(seed)
    decile = rng.integers(1, 11, size=n_events)
    spot = rng.uniform(10.0, 200.0, size=n_events)
    n_strikes = rng.integers(8, max_strikes, size=n_events)

    rows = []
    for i in range(n_events):
        k = n_strikes[i]
        strikes = spot[i] * np.linspace(0.7, 1.3, k)
        for cp_flag in ["C", "P"]:
            for strike in strikes:
                # crude Black-Scholes-flavored premium proxy: intrinsic + a time-value bump that
                # shrinks the further OTM the strike is -- good enough for exercising the kernel,
                # not meant to be a real pricer.
                intrinsic = max(0.0, spot[i] - strike) if cp_flag == "C" else max(0.0, strike - spot[i])
                time_value = spot[i] * 0.08 * np.exp(-((strike - spot[i]) / (spot[i] * 0.15)) ** 2)
                premium = max(0.05, intrinsic + time_value)
                rows.append(dict(event_id=i, decile=decile[i], underlying_price=spot[i],
                                  cp_flag=cp_flag, strike=strike, entry_mid=premium))
    return pd.DataFrame(rows)
