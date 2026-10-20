"""A self-updating, decile-conditioned forward-return model for the options Kelly selector.

The backtest's Kelly selector (scripts/options_strategy/48_optimal_contract_selector.py) scores
candidate contracts against `data/metadata/decile_return_distributions.parquet` -- an empirical
distribution built from WRDS/IBES/OptionMetrics history. That file is deliberately **not**
committed to this repo (see .gitignore: "not ours to redistribute"), so it will not exist on a
fresh clone or a CI runner, and this live system has to work from one.

Rather than ship a static parametric guess forever, this module treats the decile model as a
belief that starts from a conservative literature-typical prior and updates itself from this
system's own realized, live-settled option outcomes (recorded by run_daily.py when a position is
closed). Cold start uses a Gaussian prior per decile with drift magnitudes an order of magnitude
smaller than the backtest's own headline PEAD numbers (deliberately conservative until real data
accumulates) and volatility typical of a ~60-day single-name option holding period. The model is
persisted at data/live/decile_return_model.json and is meant to *converge toward* this system's
own live edge estimate, not to reproduce the backtest's exact historical distribution -- it cannot,
without the underlying WRDS/OM data, and pretending otherwise would be a worse mistake than an
honest, self-correcting proxy. See docs/LIVE_TRADING.md.
"""
import numpy as np

from . import config, ledger


def _cold_start_prior():
    # decile 1 = most negative SUE, decile 10 = most positive SUE. mean_ret is the prior mean
    # *underlying* return over the holding period; n_obs=0 signals "prior only, not yet updated".
    deciles = {}
    for d in range(1, 11):
        drift = (d - 5.5) / 4.5 * 0.03  # +-3% at the extremes, 0 near the middle, linear in decile
        deciles[str(d)] = {"mean_ret": drift, "std_ret": 0.12, "n_obs": 0}
    return deciles


def load_model():
    model = ledger._load_json(config.DECILE_MODEL_JSON, None)
    if model is None:
        model = {"deciles": _cold_start_prior()}
        ledger._save_json(config.DECILE_MODEL_JSON, model)
    return model


def save_model(model):
    ledger._save_json(config.DECILE_MODEL_JSON, model)


def decile_for_sue_z(sue_z, cohort_sue_zs):
    """Rank sue_z within the live cohort (trailing-window reporters) into a 1-10 decile, matching
    the backtest's cross-sectional SUE-rank construction as closely as a rolling live cohort
    allows (see config.EQUITY_COHORT_WINDOW_TRADING_DAYS)."""
    if not cohort_sue_zs:
        return 5
    pct = float(np.mean([1.0 if x <= sue_z else 0.0 for x in cohort_sue_zs]))
    return int(np.clip(np.ceil(pct * 10), 1, 10))


def get_decile_params(decile):
    model = load_model()
    d = model["deciles"][str(decile)]
    return d["mean_ret"], d["std_ret"]


def update_with_realized_return(decile, realized_underlying_return):
    """Bayesian-ish running update: blend the new observation into the decile's mean/std with a
    weight that shrinks as n_obs grows, so a handful of early live trades don't overreact but the
    model keeps learning indefinitely rather than freezing after some fixed window."""
    model = load_model()
    d = model["deciles"][str(decile)]
    n = d["n_obs"]
    w = 1.0 / (n + 5)  # new obs always carries >=~1/(n+5) weight; never fully frozen
    old_mean = d["mean_ret"]
    d["mean_ret"] = (1 - w) * old_mean + w * realized_underlying_return
    # Welford-style running variance update toward the new residual
    resid = realized_underlying_return - old_mean
    d["std_ret"] = float(np.sqrt(max((1 - w) * d["std_ret"] ** 2 + w * resid ** 2, 1e-6)))
    d["n_obs"] = n + 1
    model["deciles"][str(decile)] = d
    save_model(model)


def get_return_states(decile, horizon=60, n_states=40):
    """(ret_state, prob) arrays for the underlying's forward return given its SUE decile.

    Preferred: the latest walk-forward EMPIRICAL distribution the backtest itself prices contracts
    against (data/metadata/decile_return_distributions.parquet, built by the options prep stage from
    WRDS + OptionMetrics -- licensed, so never committed; it exists only on machines that ran the prep).
    Fallback (CI, fresh clone): a Gaussian per decile that starts conservative and learns from this
    system's own settled live trades."""
    import pandas as pd
    from pead.common.paths import METADATA_DIR
    path = METADATA_DIR / "decile_return_distributions.parquet"
    if path.exists():
        d = pd.read_parquet(path)
        d = d[(d["horizon"] == horizon) & (d["decile"] == decile)]
        if len(d):
            d = d[d["as_of_quarter"] == d["as_of_quarter"].max()].sort_values("state")
            return d["ret_state"].to_numpy(), d["prob"].to_numpy()
    from scipy.stats import norm
    mean, std = get_decile_params(decile)
    q = (np.arange(n_states) + 0.5) / n_states
    return mean + std * norm.ppf(q), np.full(n_states, 1.0 / n_states)
