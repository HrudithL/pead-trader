"""
Shared infrastructure for the equity-strategy GPU pipeline (scripts 32-35, "Strategy 8" --
see the README's "Equity-strategy GPU roadmap" section). Mirrors
scripts/options_strategy/lib/gpu.py's structure exactly: the device backend, --device/
--smoke-test flags, and checkpoint/status-log machinery are generic and live in common/gpu.py
(re-exported here); this module holds only what's equity-specific -- the --mock-data flag and
the synthetic daily-panel/position generators that let every stage below be built and
smoke-tested without data/normalized_equity/ or data/positions_rankweighted_v2.parquet on hand.
"""
import numpy as np
import pandas as pd

from common.gpu import (  # noqa: F401 -- re-exported for `from lib.gpu import ...` call sites
    get_backend, to_host, add_device_arg, add_smoke_test_arg, StageTimer, stage_done, STATUS_PATH,
)


def add_mock_data_arg(parser):
    parser.add_argument("--mock-data", action="store_true",
                         help="use a synthetic daily equity panel + event/position panel instead "
                              "of the real WRDS-derived data (data/normalized_equity/, "
                              "data/positions_rankweighted_v2.parquet). Exercises the identical "
                              "code path (including the trailing-window GPU feature gather and "
                              "the walk-forward retraining loop) at a chosen scale.")
    parser.add_argument("--mock-n-permnos", type=int, default=200,
                         help="number of synthetic permnos when --mock-data is set. Default: %(default)s")
    parser.add_argument("--mock-n-events", type=int, default=4_000,
                         help="number of synthetic events when --mock-data is set "
                              "(real panel is ~100k+ tradeable events). Default: %(default)s")
    return parser


_FF12_SECTORS = [f"sector_{i}" for i in range(1, 13)]
_MOCK_START_YEAR = 1996
_MOCK_N_YEARS = 8          # gives ~2000 trading days/permno -- enough room for a 252d lookback
                            # plus a holding window plus several walk-forward fold years
_MOCK_WARMUP_ROWS = 260    # entry must be at least this far into its permno's history, so the
                            # 12-1 momentum window (252d) is always fully in-bounds


def make_mock_equity_daily_panel(n_permnos=200, seed=0):
    """Synthetic stand-in for the concatenated data/normalized_equity/equity_{year}.parquet panel
    (permno, date, ret) + data/normalized_equity/market_benchmark.parquet (date, vwretd), built
    with the exact same global-row-idx convention the real pipeline uses everywhere (sorted by
    permno then date, one contiguous integer row_idx across all permnos) -- a rectangular panel
    (every permno spans the identical calendar), which downstream mock position generation relies
    on to place entries via simple arithmetic instead of re-deriving offsets per permno.

    Returns (eq_ret, eq_permno, eq_date, calendar, n_days_per_permno) -- eq_ret is already
    market-adjusted (ret - vwretd), matching what every real backtest script computes right after
    loading, since that's the array every downstream stage actually consumes."""
    rng = np.random.default_rng(seed)
    calendar = pd.bdate_range(f"{_MOCK_START_YEAR}-01-01", f"{_MOCK_START_YEAR + _MOCK_N_YEARS - 1}-12-31")
    n_days = len(calendar)
    mkt_ret = rng.normal(0.0003, 0.008, size=n_days)
    idio = rng.normal(0.0, 0.02, size=(n_permnos, n_days))
    ret_mktadj = idio  # idiosyncratic component IS the market-adjusted return by construction
    eq_ret = ret_mktadj.reshape(-1)
    permnos = np.arange(10_000, 10_000 + n_permnos)
    eq_permno = np.repeat(permnos, n_days)
    eq_date = np.tile(calendar.values, n_permnos)
    return eq_ret, eq_permno, eq_date, calendar, n_days, mkt_ret


def make_mock_equity_positions(eq_ret, eq_permno, eq_date, calendar, n_days_per_permno, mkt_ret,
                                n_events=4_000, seed=0, signal_strength=0.06):
    """Synthetic stand-in for data/positions_rankweighted_v2.parquet, sampled from the mock daily
    panel above. Injects a real decile-ordered drift directly into `eq_ret` over each sampled
    event's own holding window (mutates it in place, same convention as the options mock's
    decile-ordered spread) so the SAME downstream feature/label/model code that runs on real data
    sees genuine directional signal-to-noise character here too, not pure noise.

    Returns the positions DataFrame with the same columns real positions_rankweighted_v2.parquet
    carries (see 16_positions_v2.py's KEEP_COLS)."""
    rng = np.random.default_rng(seed)
    n_permnos = len(eq_ret) // n_days_per_permno
    holding_choices = np.array([20, 40, 60])

    permno_i = rng.integers(0, n_permnos, size=n_events)
    holding_days = rng.choice(holding_choices, size=n_events)
    max_hold = int(holding_choices.max())
    local_row = rng.integers(_MOCK_WARMUP_ROWS, n_days_per_permno - max_hold - 1, size=n_events)
    entry_row_idx = permno_i * n_days_per_permno + local_row
    exit_row_idx = entry_row_idx + holding_days

    decile = rng.integers(1, 11, size=n_events)
    rank = (decile - 5.5) / 4.5

    # inject decile-ordered drift additively over each event's own holding window
    for h in np.unique(holding_days):
        sel = np.where(holding_days == h)[0]
        if len(sel) == 0:
            continue
        offsets = np.arange(1, h + 1)
        rows = entry_row_idx[sel][:, None] + offsets[None, :]           # (n_sel, h)
        drift = (rank[sel] * signal_strength / h)[:, None]              # (n_sel, 1)
        np.add.at(eq_ret, rows.reshape(-1), np.broadcast_to(drift, rows.shape).reshape(-1))

    permno = eq_permno[entry_row_idx]
    day0_date = pd.to_datetime(eq_date[entry_row_idx])
    exit_date = pd.to_datetime(eq_date[exit_row_idx])
    ann_quarter = day0_date.to_period("Q").astype(str)
    sue_rank_pct = np.clip((decile - 1) / 9.0 + rng.normal(0, 0.03, size=n_events), 0.0, 1.0)
    size_quintile = rng.integers(1, 6, size=n_events)
    bm_tercile = rng.integers(1, 4, size=n_events)
    ff12_sector = rng.choice(_FF12_SECTORS, size=n_events)
    liquidity_quintile = rng.integers(1, 6, size=n_events)
    avg_dollar_volume_21d = np.exp(rng.normal(15.0, 1.5, size=n_events))  # ~$1M-$30M, lognormal
    holding_cell = pd.Series(decile).astype(str) + "_" + pd.Series(size_quintile).astype(str) \
        + "_" + pd.Series(bm_tercile).astype(str)
    event_id = pd.Series(permno).astype(str) + "_" + day0_date.strftime("%Y%m%d")

    return pd.DataFrame({
        "event_id": event_id, "permno": permno, "day0_date": day0_date, "exit_date": exit_date,
        "entry_row_idx": entry_row_idx, "exit_row_idx": exit_row_idx, "decile": decile,
        "sue_rank_pct": sue_rank_pct, "holding_days": holding_days, "holding_cell": holding_cell,
        "size_quintile": size_quintile, "bm_tercile": bm_tercile, "ff12_sector": ff12_sector,
        "avg_dollar_volume_21d": avg_dollar_volume_21d, "liquidity_quintile": liquidity_quintile,
        "ann_quarter": ann_quarter,
    })


# ---------------------------------------------------------------------------
# Trailing-window feature gather + forward-return label -- shared by 32_gpu_feature_panel.py
# (real data, xp-batched so it runs on the GPU) and make_mock_feature_panel below (always plain
# numpy, matching options_strategy/lib/gpu.py's make_mock_daily_paths convention of building mock
# inputs on the host regardless of --device).
# ---------------------------------------------------------------------------

MOM_WINDOW = 252    # trailing days spanned by the momentum lookback
MOM_SKIP = 21       # most recent days excluded from momentum (the short-term reversal window)
VOL_WINDOW = 60      # trailing days used for realized volatility
MIN_MOM_DAYS = 100   # minimum valid trailing days required to trust a momentum estimate


def compute_trailing_features(xp, eq_ret, eq_permno, entry_row_idx, event_permno):
    """Dense, GPU-batchable trailing-window feature gather. For every event, builds a
    (n_events, MOM_WINDOW) matrix of the MOM_WINDOW days strictly before entry_row_idx, masks out
    any cell that either runs off the front of the panel or belongs to a different permno (the
    window crossed into the previous stock's history), then reduces to momentum_12_1 and vol_60d.
    Runs identically on numpy (CPU) or cupy (GPU) -- xp is the only thing that changes."""
    offsets = xp.arange(-MOM_WINDOW, 0)                                   # (W,)
    gather_idx = entry_row_idx[:, None] + offsets[None, :]                # (n, W)
    in_bounds = gather_idx >= 0
    gather_idx_safe = xp.clip(gather_idx, 0, len(eq_ret) - 1)
    same_permno = eq_permno[gather_idx_safe] == event_permno[:, None]
    valid = in_bounds & same_permno

    rets = xp.where(valid, eq_ret[gather_idx_safe], 0.0)
    log_ret = xp.log1p(xp.clip(rets, -0.999, None))

    mom_mask = valid & (offsets <= -MOM_SKIP)[None, :]
    mom_sum = xp.where(mom_mask, log_ret, 0.0).sum(axis=1)
    mom_count = mom_mask.sum(axis=1)
    momentum = xp.expm1(mom_sum)
    momentum = xp.where(mom_count >= MIN_MOM_DAYS, momentum, xp.nan)

    vol_mask = valid & (offsets >= -VOL_WINDOW)[None, :]
    vol_vals = xp.where(vol_mask, rets, xp.nan)
    vol = xp.nanstd(vol_vals, axis=1)
    vol_count = xp.sum(~xp.isnan(vol_vals), axis=1)
    vol = xp.where(vol_count >= VOL_WINDOW // 2, vol, xp.nan)

    return to_host(xp, momentum), to_host(xp, vol)


def compute_fwd_realized_return(eq_ret, entry_row_idx, exit_row_idx):
    """Each position's own realized market-adjusted return over (entry_row_idx, exit_row_idx],
    via a prefix-sum of log(1+ret) -- O(n_days) once + O(n_positions) lookup, safe because
    16_positions_v2.py already guarantees this exact window is single-permno-contiguous."""
    log_ret = np.log1p(np.clip(eq_ret, -0.999, None))
    cumsum = np.concatenate([[0.0], np.cumsum(log_ret)])
    return np.expm1(cumsum[exit_row_idx + 1] - cumsum[entry_row_idx + 1])


def make_mock_backtest_inputs(n_events=4_000, n_permnos=200, seed=0):
    """One-call synthetic stand-in for BOTH data/equity_feature_panel.parquet (32's real output)
    AND the raw daily panel arrays 34/35's NAV backtest loop needs -- built together, from the
    same mock daily panel, so a position's entry_row_idx/exit_row_idx index correctly into the
    eq_ret/eq_permno/eq_date arrays this function also returns (calling
    make_mock_equity_daily_panel a second time with a different seed would silently break that
    alignment). Used by every stage's own --mock-data path so each is independently
    smoke-testable without requiring the stage before it to have been run first, matching the
    options-strategy pipeline's convention (see e.g. 42_gpu_exit_optimizer.py's --mock-data path).

    Returns (eq_ret, eq_permno, eq_date, calendar, pos) -- pos already carries every engineered
    feature + the fwd_realized_ret label 32 computes."""
    eq_ret, eq_permno, eq_date, calendar, n_days, mkt_ret = make_mock_equity_daily_panel(
        n_permnos=n_permnos, seed=seed)
    pos = make_mock_equity_positions(eq_ret, eq_permno, eq_date, calendar, n_days, mkt_ret,
                                      n_events=n_events, seed=seed + 1)
    pos["ear"] = eq_ret[pos["entry_row_idx"].to_numpy()]
    momentum, vol = compute_trailing_features(
        np, eq_ret, eq_permno, pos["entry_row_idx"].to_numpy(), pos["permno"].to_numpy())
    pos["momentum_12_1"] = momentum
    pos["vol_60d"] = vol
    pos["fwd_realized_ret"] = compute_fwd_realized_return(
        eq_ret, pos["entry_row_idx"].to_numpy(), pos["exit_row_idx"].to_numpy())
    return eq_ret, eq_permno, eq_date, calendar, pos


def make_mock_feature_panel(n_events=4_000, n_permnos=200, seed=0):
    """Like make_mock_backtest_inputs, but returns only the positions DataFrame -- what
    32_gpu_feature_panel.py's and 33_gpu_walkforward_signal.py's --mock-data paths need."""
    _, _, _, _, pos = make_mock_backtest_inputs(n_events=n_events, n_permnos=n_permnos, seed=seed)
    return pos


def make_mock_ml_rank_proxy(pos, seed=2):
    """Cheap, non-degenerate stand-in for a trained walk-forward ml_rank_pct, used by
    34_gpu_param_sweep.py's and 35_finalize_strategy8.py's own --mock-data paths so the blend
    axis has something to search over without re-running the real walk-forward trainer (that's
    what 33_gpu_walkforward_signal.py's own --mock-data path exercises). Correlated with
    sue_rank_pct plus noise, re-ranked cross-sectionally per quarter so it's on the same [0,1]
    scale ml_rank_pct really carries."""
    rng = np.random.default_rng(seed)
    noisy = pos["sue_rank_pct"].to_numpy() * 0.5 + rng.uniform(0, 0.5, size=len(pos))
    return pd.Series(noisy).groupby(pos["ann_quarter"].to_numpy()).rank(pct=True).to_numpy()
