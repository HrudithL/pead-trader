"""Alpha against the market, computed one way for every strategy.

Daily NAV-percentage returns (daily P&L / prior-day NAV, as in metrics.py) are regressed on the CRSP
value-weighted market return:

    r_t = alpha + beta * vwretd_t + e_t                       (capm)
    r_t = alpha + beta * vwretd_t + s * (ewretd - vwretd)_t   (capm_size; EW-VW is a size/small-cap proxy)

No risk-free series is in the data, so rf = 0 -- the same convention the Sharpe ratios use. Alpha is
annualized (x252, simple). t-stats use Newey-West (Bartlett kernel, 5 lags) standard errors. The
*information ratio* is alpha / residual volatility, annualized: the Sharpe of the market-hedged book.
"""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _ols_hac(y, X, lags=5):
    n, k = X.shape
    XtX_inv = np.linalg.inv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    Xe = X * e[:, None]
    S = Xe.T @ Xe
    for l in range(1, lags + 1):
        G = Xe[l:].T @ Xe[:-l]
        S += (1 - l / (lags + 1)) * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    return b, np.sqrt(np.diag(cov)), e


def strategy_returns(daily: pd.DataFrame, initial_capital: float) -> pd.Series:
    nav = daily["nav"].to_numpy()
    prior = np.concatenate([[initial_capital], nav[:-1]])
    return pd.Series(daily["daily_pnl"].to_numpy() / prior, index=pd.DatetimeIndex(daily["date"]))


def alpha_stats(ret: pd.Series, market: pd.DataFrame, size_factor: bool = False) -> dict:
    """`market` is indexed by date with `vwretd` and `ewretd`. Only days both series have are used."""
    df = pd.concat([ret.rename("r"), market[["vwretd", "ewretd"]]], axis=1, join="inner").dropna()
    # drop the pre-trading warm-up (flat NAV, zero returns) so it does not dilute alpha toward zero
    df = df.loc[df["r"].ne(0).cummax()]
    cols = ["vwretd"]
    if size_factor:
        df["smb"] = df["ewretd"] - df["vwretd"]
        cols.append("smb")
    X = np.column_stack([np.ones(len(df)), df[cols].to_numpy()])
    b, se, e = _ols_hac(df["r"].to_numpy(), X)
    resid_vol = e.std(ddof=len(b)) * np.sqrt(TRADING_DAYS)
    out = dict(alpha=float(b[0] * TRADING_DAYS), alpha_t=float(b[0] / se[0]), beta=float(b[1]),
               beta_t=float(b[1] / se[1]), resid_vol=float(resid_vol),
               info_ratio=float(b[0] * TRADING_DAYS / resid_vol) if resid_vol > 0 else np.nan,
               r2=float(1 - e.var() / df["r"].var()), n_days=int(len(df)))
    if size_factor:
        out["size_loading"] = float(b[2])
    return out
