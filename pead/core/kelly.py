"""Kelly-optimal contract scoring: the objective behind the options selector, in one place.

For a contract with premium p, strike K and side (+1 call / -1 put), and a distribution over the
underlying's terminal return {(r_s, prob_s)}:

    payoff_s = max(0, side * (S (1 + r_s) - K))        return on premium  R_s = payoff_s / p - 1
    growth(f) = sum_s prob_s * log(1 + f * R_s)         expected LOG growth betting fraction f
    score     = max over a grid of f in (0, 1) of growth(f), and the maximizing f*

Contracts are ranked by that best growth rate, not by expected return: an out-of-the-money lottery
ticket has a huge E[R] but a tiny f* and negligible growth, because log utility punishes the ruin it
courts. Position size is then half of f* (full Kelly over-bets when the distribution is an estimate).

Written against an array module `xp` (numpy or cupy) so the batch selector in prep/ can run it on the
GPU and the live engine can run the same code on a single event with numpy.
"""
import numpy as np

F_GRID = np.linspace(0.05, 0.95, 19)   # kept < 1 so 1 + f*R never reaches 0 (R floors at -1)


def score_contracts(xp, side, strike, premium, mask, spot, ret_state, prob, f_grid=F_GRID):
    """Batched scoring.

    side/strike/premium/mask: (B, K); spot: (B,); ret_state/prob: (B, S).
    Returns (best_growth (B, K), best_f (B, K), naive_ev (B, K)); padded slots (mask False) get -1e18."""
    terminal = spot[:, None, None] * (1.0 + ret_state[:, None, :])
    payoff = xp.maximum(0.0, side[:, :, None] * (terminal - strike[:, :, None]))
    R = payoff / premium[:, :, None] - 1.0
    naive_ev = (prob[:, None, :] * R).sum(axis=-1)
    best_growth = xp.full(side.shape, -1e18)
    best_f = xp.zeros(side.shape)
    for f in f_grid:
        e_growth = (prob[:, None, :] * xp.log1p(f * R)).sum(axis=-1)
        improve = e_growth > best_growth
        best_growth = xp.where(improve, e_growth, best_growth)
        best_f = xp.where(improve, f, best_f)
    return xp.where(mask, best_growth, -1e18), best_f, xp.where(mask, naive_ev, -1e18)


def best_contract(spot, sides, strikes, premiums, ret_state, prob):
    """Single-event convenience (numpy): returns (index, kelly_fraction, growth) of the best contract,
    or None if no contract has positive expected log growth."""
    k = len(strikes)
    g, f, _ = score_contracts(np, np.asarray(sides, float)[None, :], np.asarray(strikes, float)[None, :],
                              np.maximum(np.asarray(premiums, float), 1e-6)[None, :], np.ones((1, k), bool),
                              np.array([float(spot)]), np.asarray(ret_state, float)[None, :],
                              np.asarray(prob, float)[None, :])
    i = int(np.argmax(g[0]))
    return None if g[0, i] <= 0 else (i, float(f[0, i]), float(g[0, i]))
