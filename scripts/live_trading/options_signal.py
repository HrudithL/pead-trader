"""Live options signal: half-Kelly contract selection against the (self-updating) decile return
model, matching scripts/options_strategy/48_optimal_contract_selector.py's objective -- maximize
E[log(1 + f*R)] over a grid of Kelly fractions f -- but scored against `decile_model` instead of
the backtest's historical empirical distribution (see decile_model.py for why), and evaluated
one event at a time instead of GPU-batched (live volume is a handful of events/day, not the
backtest's ~126k, so there is no batching to gain).
"""
import numpy as np
from scipy.stats import norm

from . import config


def _payoff_grid(spot, strike, cp_flag, premium, mean_ret, std_ret, n_states=401):
    """Approximates the option's return distribution by sampling the underlying's assumed-Gaussian
    forward return at `n_states` quantile points, pricing the option's terminal payoff at each,
    and converting to a return-on-premium. Intrinsic-value-at-expiry payoff (this system holds to
    its target horizon or an early exit, not necessarily literal expiry, but intrinsic value is
    the right approximation for a 60-day hold on a decile-conditioned drift thesis, not a
    volatility trade)."""
    qs = (np.arange(1, n_states + 1) - 0.5) / n_states
    z = norm.ppf(qs)
    underlying_ret = mean_ret + std_ret * z
    terminal_price = spot * (1 + underlying_ret)
    if cp_flag == "C":
        payoff = np.maximum(terminal_price - strike, 0.0)
    else:
        payoff = np.maximum(strike - terminal_price, 0.0)
    option_ret = payoff / premium - 1.0
    probs = np.full(n_states, 1.0 / n_states)
    return option_ret, probs


def kelly_best_fraction_and_growth(option_ret, probs, f_grid):
    best_f, best_g = 0.0, -np.inf
    for f in f_grid:
        growth = float(np.sum(probs * np.log(np.clip(1 + f * option_ret, 1e-6, None))))
        if growth > best_g:
            best_g, best_f = growth, f
    return best_f, best_g


def select_optimal_contract(spot, chain_df, mean_ret, std_ret,
                             f_grid=config.OPTIONS_KELLY_FRACTION_GRID):
    """chain_df: DataFrame[strike, cp_flag, mid, openInterest, ...]. Returns the best-row dict
    (with kelly_fraction, kelly_growth added) or None if nothing has positive edge."""
    best = None
    for _, row in chain_df.iterrows():
        premium = row["mid"]
        if premium <= 0.01:
            continue
        option_ret, probs = _payoff_grid(spot, row["strike"], row["cp_flag"], premium,
                                          mean_ret, std_ret)
        f_star, growth = kelly_best_fraction_and_growth(option_ret, probs, f_grid)
        if growth <= 0:
            continue
        if best is None or growth > best["kelly_growth"]:
            best = row.to_dict()
            best["kelly_fraction"] = f_star
            best["kelly_growth"] = growth
    return best


def size_option_position(best_contract, options_sleeve_nav, outstanding_premium):
    """Half-Kelly sizing on the selected fraction, capped so outstanding premium across all open
    positions never exceeds OPTIONS_PREMIUM_CAP_FRAC of sleeve NAV -- matching the finalized
    cap30/half-Kelly backtest operating point exactly."""
    f = best_contract["kelly_fraction"] * config.OPTIONS_KELLY_MULTIPLIER
    target_premium = f * options_sleeve_nav
    cap_dollars = config.OPTIONS_PREMIUM_CAP_FRAC * options_sleeve_nav
    room = max(cap_dollars - outstanding_premium, 0.0)
    premium_dollars = min(target_premium, room)
    if premium_dollars <= 0:
        return 0, 0.0
    per_contract_cost = best_contract["mid"] * 100  # 1 contract = 100 shares
    n_contracts = int(premium_dollars // per_contract_cost)
    return n_contracts, n_contracts * per_contract_cost
