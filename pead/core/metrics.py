"""Performance statistics, computed one way for every strategy.

Sharpe is the true NAV-percentage Sharpe: daily P&L divided by the PRIOR day's NAV (the corrected
method; the older fixed-initial-capital denominator overstated volatility as NAV compounded and is
kept only as `sharpe_fixed_capital` for comparison with legacy summary files)."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252
ERAS = {"1996_2001": ("1996", "2001"), "2002_2007": ("2002", "2007"), "2008_2013": ("2008", "2013")}


def drawdown(nav):
    nav = np.asarray(nav, dtype=float)
    return nav / np.maximum.accumulate(nav) - 1.0


def summarize(daily: pd.DataFrame, initial_capital: float) -> dict:
    """Headline stats from a daily frame with `date`, `daily_pnl`, `nav` columns."""
    nav = daily["nav"].to_numpy()
    pnl = daily["daily_pnl"].to_numpy()
    prior = np.concatenate([[initial_capital], nav[:-1]])
    ret = pnl / prior
    years = len(nav) / TRADING_DAYS
    total = nav[-1] / initial_capital - 1.0
    ann_ret = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    vol = ret.std(ddof=1) * np.sqrt(TRADING_DAYS)
    sharpe = ret.mean() * TRADING_DAYS / vol if vol > 0 else np.nan
    dd = drawdown(nav)
    fixed = pnl / initial_capital
    vol_fixed = fixed.std(ddof=1) * np.sqrt(TRADING_DAYS)
    downside = ret[ret < 0]
    dvol = np.sqrt((downside ** 2).sum() / max(len(ret) - 1, 1)) * np.sqrt(TRADING_DAYS)
    out = dict(
        total_return=float(total), annualized_return=float(ann_ret), annualized_vol=float(vol),
        sharpe=float(sharpe),
        sortino=float(ret.mean() * TRADING_DAYS / dvol) if dvol > 0 else np.nan,
        sharpe_fixed_capital=float(fixed.mean() * TRADING_DAYS / vol_fixed) if vol_fixed > 0 else np.nan,
        max_drawdown=float(dd.min()), calmar=float(ann_ret / -dd.min()) if dd.min() < 0 else np.nan,
        final_nav=float(nav[-1]), n_days=int(len(nav)),
        best_day=float(ret.max()), worst_day=float(ret.min()),
        pct_positive_days=float((ret > 0).mean()),
    )
    s = pd.Series(ret, index=pd.DatetimeIndex(daily["date"]))
    for name, (y0, y1) in ERAS.items():
        x = s.loc[y0:y1]
        out[f"sharpe_{name}"] = float(x.mean() / x.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(x) > 2 and x.std() > 0 else np.nan
    return out


def yearly_table(daily: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    """Calendar-year return / vol / Sharpe / worst drawdown-from-year-start-peak."""
    nav = pd.Series(daily["nav"].to_numpy(), index=pd.DatetimeIndex(daily["date"]))
    prior = nav.shift(1).fillna(initial_capital)
    ret = (nav - prior) / prior
    rows = []
    for y, r in ret.groupby(ret.index.year):
        n = nav[nav.index.year == y]
        start = prior[r.index[0]]
        rows.append(dict(year=int(y), ret=float(n.iloc[-1] / start - 1),
                         vol=float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(r) > 2 else np.nan,
                         sharpe=float(r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if r.std() > 0 else np.nan,
                         max_dd=float(drawdown(np.concatenate([[start], n.to_numpy()])).min())))
    return pd.DataFrame(rows)
