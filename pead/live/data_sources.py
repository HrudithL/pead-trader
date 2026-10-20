"""Live market/earnings data, all from free sources.

**Earnings actuals + estimates + surprise history**: yfinance's `Ticker.get_earnings_dates()`.
No key, no documented hard rate limit (Yahoo's undocumented soft throttling is handled by
`_retry`), and gives exactly what's needed to build a SUE-like statistic live: EPS estimate,
reported EPS, surprise %, per quarter, going back several years per ticker -- which is what lets
us standardize each new surprise against that ticker's *own* trailing surprise volatility instead
of a single cross-sectional number, roughly analogous to how IBES-based SUE standardizes against
each firm's own history of prior surprises.

**Prices, market cap, ADV, sector**: also yfinance (`fast_info` for live/last price and market
cap, `history()` for ADV, `info["sector"]` falls back to the bundled S&P 500 GICS-sector table
so we don't pay for an `info` call on every ticker every day).

**Option chains**: yfinance `Ticker.option_chain(expiry)`.

Finnhub's free `/calendar/earnings` endpoint is used only as an optional independent cross-check
(if FINNHUB_API_KEY is set) for which tickers reported today -- see `finnhub_earnings_today()`.
Nothing here is required for the system to run; every function degrades to returning nothing
useful rather than raising, so one ticker's bad data never takes down a whole run (see `_retry`
and the try/except in each `fetch_*` loop in run_daily.py).
"""
import time
import csv
import urllib.request
import json as _json
from datetime import datetime, timedelta, date

import pandas as pd

from . import config

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - guarded so `--help` etc still works without the dep installed
    yf = None


def _retry(fn, tries=3, base_delay=1.5):
    last_exc = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - deliberately broad, this is a best-effort data fetch
            last_exc = e
            time.sleep(base_delay * (2 ** i))
    raise last_exc


def load_universe():
    """S&P 500 tickers + GICS sector, from the bundled snapshot (data/live/sp500_universe.csv).

    Bundled rather than scraped live: it changes rarely (index reconstitutions), scraping it
    every run adds a fragile network dependency for no benefit, and a static universe keeps
    day-to-day runs reproducible. Refresh it occasionally with:
        curl -sL -A "Mozilla/5.0" \
          https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv \
          -o data/live/sp500_universe.csv
    """
    rows = []
    with open(config.UNIVERSE_CSV, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({"ticker": r["Symbol"].replace(".", "-"), "sector": r["GICS Sector"]})
    return rows


def get_earnings_surprise(ticker, as_of=None):
    """Returns the most recent *already-reported* earnings surprise for `ticker`, standardized
    against that ticker's own trailing surprise history (a live analog of SUE), or None if there
    is no new report since `as_of` (a date.isoformat() string) or data is unavailable.

    Standardization: sue_z = surprise_pct / std(trailing up-to-8 prior surprise_pct), with a floor
    on the denominator (2.0 pct points) so a freakishly quiet history doesn't blow up the z-score
    from a small, ordinary surprise. This is an approximation of IBES-based SUE (which standardizes
    the *dollar* surprise by the std of prior *dollar* surprises) -- see docs/LIVE_TRADING.md for why
    percent-surprise is what's used here instead (IBES-detail-level actual/estimate dispersion
    data isn't available for free, live).
    """
    if yf is None:
        return None
    t = yf.Ticker(ticker)
    try:
        ed = _retry(lambda: t.get_earnings_dates(limit=12))
    except Exception:
        return None
    if ed is None or ed.empty:
        return None
    ed = ed.dropna(subset=["Reported EPS"]).sort_index(ascending=False)
    if ed.empty:
        return None
    latest = ed.iloc[0]
    report_date = ed.index[0].date()
    if as_of is not None and report_date <= date.fromisoformat(as_of):
        return None
    surprise_pct = latest["Surprise(%)"]
    if pd.isna(surprise_pct):
        return None
    hist = ed.iloc[1:9]["Surprise(%)"].dropna()
    denom = max(hist.std(), 2.0) if len(hist) >= 3 else 5.0  # generic prior when history is thin
    sue_z = float(surprise_pct) / denom
    return {
        "ticker": ticker,
        "report_date": report_date.isoformat(),
        "eps_estimate": float(latest["EPS Estimate"]) if not pd.isna(latest["EPS Estimate"]) else None,
        "eps_reported": float(latest["Reported EPS"]),
        "surprise_pct": float(surprise_pct),
        "sue_z": sue_z,
    }


def get_price_stats(ticker):
    """Live/last price, market cap, and 21-trading-day average dollar volume."""
    if yf is None:
        return None
    t = yf.Ticker(ticker)
    try:
        fi = _retry(lambda: t.fast_info)
        hist = _retry(lambda: t.history(period="45d", interval="1d", auto_adjust=False))
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    hist = hist.tail(21)
    adv21 = float((hist["Close"] * hist["Volume"]).mean())
    last_price = float(fi.get("lastPrice") or hist["Close"].iloc[-1])
    market_cap = fi.get("marketCap")
    return {
        "last_price": last_price,
        "market_cap": float(market_cap) if market_cap else None,
        "adv21_dollars": adv21,
    }


def get_vol_stats(ticker, market_returns=None):
    """(ivol_252d, tvol_60d): daily-return volatility measures matching the backtest's trailing-vol
    features (pead.common.volatility): total vol over the last 60 sessions, and the residual vol of an OLS
    fit on the market over the last 252 (market_returns: a Series of the same-period market's daily
    returns, e.g. SPY). Either is NaN when there is not enough history."""
    import numpy as np
    if yf is None:
        return float("nan"), float("nan")
    try:
        hist = _retry(lambda: yf.Ticker(ticker).history(period="14mo", interval="1d", auto_adjust=True))
    except Exception:
        return float("nan"), float("nan")
    if hist is None or len(hist) < 60:
        return float("nan"), float("nan")
    r = hist["Close"].pct_change().dropna()
    tvol = float(r.tail(60).std(ddof=1)) if len(r) >= 30 else float("nan")
    ivol = float("nan")
    if market_returns is not None:
        both = pd.concat([r, market_returns], axis=1, join="inner").dropna().tail(252)
        if len(both) >= 120:
            y, x = both.iloc[:, 0].to_numpy(), both.iloc[:, 1].to_numpy()
            beta = np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1)
            resid = y - y.mean() - beta * (x - x.mean())
            ivol = float(np.sqrt((resid ** 2).sum() / (len(y) - 2)))
    return ivol, tvol


def get_market_returns():
    """SPY daily returns, the live stand-in for the CRSP value-weighted market."""
    if yf is None:
        return None
    try:
        h = _retry(lambda: yf.Ticker("SPY").history(period="14mo", interval="1d", auto_adjust=True))
        return h["Close"].pct_change().dropna()
    except Exception:
        return None


def get_option_chain_candidates(ticker, target_dte_days=config.OPTIONS_TARGET_DTE_DAYS, expiry=None):
    """Every call+put strike from the single expiry closest to `target_dte_days` out, with a
    usable mid price. Returns (spot_price, expiry_date, DataFrame[strike, cp_flag, bid, ask, mid,
    openInterest, impliedVolatility]) or (None, None, None) if unavailable.
    """
    if yf is None:
        return None, None, None
    t = yf.Ticker(ticker)
    try:
        expiries = _retry(lambda: t.options)
    except Exception:
        return None, None, None
    if not expiries:
        return None, None, None
    today = date.today()
    if expiry is not None:
        # marking an OPEN position: we need exactly its own expiry, not the one nearest 60 days today
        if expiry not in expiries:
            return None, None, None
        best_expiry = expiry
    else:
        best_expiry = min(expiries, key=lambda e: abs((date.fromisoformat(e) - today).days - target_dte_days))
    try:
        chain = _retry(lambda: t.option_chain(best_expiry))
        fi = _retry(lambda: t.fast_info)
    except Exception:
        return None, None, None
    spot = float(fi.get("lastPrice") or 0.0)
    if not spot:
        return None, None, None
    frames = []
    for cp_flag, df in (("C", chain.calls), ("P", chain.puts)):
        df = df.copy()
        df["cp_flag"] = cp_flag
        frames.append(df)
    all_opts = pd.concat(frames, ignore_index=True)
    all_opts["mid"] = (all_opts["bid"].fillna(0) + all_opts["ask"].fillna(0)) / 2.0
    # fall back to lastPrice when bid/ask is unquoted (illiquid strike) rather than dropping it
    no_quote = all_opts["mid"] <= 0
    all_opts.loc[no_quote, "mid"] = all_opts.loc[no_quote, "lastPrice"]
    all_opts = all_opts[all_opts["mid"] > 0.01]
    all_opts = all_opts[all_opts["openInterest"].fillna(0) >= config.OPTIONS_MIN_OPEN_INTEREST]
    keep = ["strike", "cp_flag", "bid", "ask", "mid", "openInterest", "impliedVolatility"]
    return spot, date.fromisoformat(best_expiry), all_opts[keep].reset_index(drop=True)


def finnhub_earnings_today():
    """Optional cross-check: today's earnings-calendar tickers from Finnhub's free tier.
    Returns a set() (possibly empty) and never raises; only used if FINNHUB_API_KEY is set."""
    if not config.FINNHUB_API_KEY:
        return set()
    today = date.today().isoformat()
    url = (f"https://finnhub.io/api/v1/calendar/earnings?from={today}&to={today}"
           f"&token={config.FINNHUB_API_KEY}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pead-live-trading"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = _json.loads(resp.read().decode())
        return {row["symbol"] for row in payload.get("earningsCalendar", [])}
    except Exception:
        return set()
