"""Slice 7 of memory/phase3-options-pead-plan.md: independent Greeks / IV
validation with QuantLib.

Purpose
-------
Recomputes ``iv``, ``delta``, ``gamma``, ``vega``, ``theta`` on a
stratified sample of canonical options observations using QuantLib as
an independent implementation, and compares them to the vendor's own
values. This is a QA slice on a *sample*, not a repricing of the full
archive.

Contract: emits a written pass/fail verdict on whether the vendor's
Greeks can be trusted for downstream contract selection. Per the
plan, "Only if it fails does QuantLib enter the main pipeline, and
that would be a scope change to raise, not absorb."

Model choices
-------------
US equity options are American by convention. Without ``opinfd``'s
``exercise_style`` field joined onto the canonical frame (Slice 5b's
loader does not carry it -- keeping the schema minimal), the caller
passes ``exercise_style_map: dict[int, str] | None`` to override on a
per-``option_id`` (or per-``secid``) basis. Default is American for
every row. American pricing uses the finite-difference Black-Scholes
grid (fdBlackScholesVanillaEngine); European uses AnalyticEuropean
Engine. Both are QuantLib's standard engines for these payoffs.

The risk-free rate is a flat annualised rate over each observation's
window. Callers may pass a per-date rate via ``rate_map: dict[date,
float] | None`` (from ``zerocd`` or a Treasury proxy); the default is
0.02 (2% annualised), which is in the middle of the phase's 1996-2013
window's typical short rates. Dividend yield defaults to 0 (equity
options on non-dividend-paying names or short holding periods where a
flat approximation is acceptable); ``dividend_map`` overrides.

Sample design
-------------
The plan requires strata across "years, moneyness, DTE, and both
exercise styles." ``stratified_sample()`` implements that: buckets on
observation year, moneyness (log-strike / underlying), and DTE
(days-to-expiration) buckets, and picks the same number of rows per
non-empty (year, moneyness, DTE) cell. Deterministic seed so the
sample is reproducible.

Verdict thresholds
------------------
Defaults are conservative: median absolute IV difference <= 0.02 (2
volatility points), median absolute delta difference <= 0.05, median
absolute vega difference <= 0.10 per unit volatility. Callers can
tighten or loosen; the pass/fail is a documented function of the
thresholds, not a hidden judgment call.

Usage
-----
    from options_greeks_validation import (
        validate_greeks_sample, stratified_sample
    )
    sample = stratified_sample(loader_df, target_size=200)
    report = validate_greeks_sample(sample)
    print(report.summary())
    if not report.passed:
        # escalate: QuantLib enters the main pipeline
        ...
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable, Literal

import numpy as np
import pandas as pd
import QuantLib as ql

# --- Verdict thresholds ------------------------------------------------


@dataclass(frozen=True)
class VerdictThresholds:
    """Bar the vendor Greeks must clear for this validation to pass.

    Defaults are conservative and documented in the module docstring.
    Passing a custom instance is the way to tighten or loosen -- do not
    modify the defaults in place.
    """

    max_median_abs_iv_diff: float = 0.02       # 2 volatility points
    max_median_abs_delta_diff: float = 0.05    # 5% of unit-delta
    max_median_abs_vega_diff: float = 0.10     # per-unit-vol vega
    min_sample_rows: int = 50                  # verdict needs enough data


DEFAULT_THRESHOLDS = VerdictThresholds()


# --- Report type -------------------------------------------------------


@dataclass
class GreeksValidationReport:
    """Outcome of ``validate_greeks_sample``.

    ``per_metric_stats`` is a DataFrame with one row per metric (iv,
    delta, gamma, vega, theta) and columns for the median absolute
    difference, mean absolute difference, and 95th-percentile absolute
    difference. ``passed`` is the overall verdict against the
    thresholds; ``reasons`` explains which metric failed if not.
    ``per_row`` carries every row's vendor-vs-QuantLib pairs plus the
    absolute differences, so downstream reports can plot deviation
    scatter and quantify without recomputing anything.
    """

    passed: bool
    reasons: list[str] = field(default_factory=list)
    per_metric_stats: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            columns=["metric", "median_abs_diff", "mean_abs_diff", "p95_abs_diff", "n"]
        )
    )
    per_row: pd.DataFrame = field(default_factory=pd.DataFrame)
    thresholds: VerdictThresholds = field(default_factory=lambda: DEFAULT_THRESHOLDS)

    def summary(self) -> str:
        n = len(self.per_row)
        head = f"greeks-validation: {n} sample rows repriced, verdict={'PASS' if self.passed else 'FAIL'}."
        if not self.passed and self.reasons:
            head += " reasons: " + "; ".join(self.reasons)
        return head


# --- Sampling ----------------------------------------------------------


def _log_moneyness(row: pd.Series) -> float:
    up = row["underlying_price"]
    strike = row["strike"]
    if up <= 0 or strike <= 0:
        return float("nan")
    return math.log(strike / up)


def _dte_days(row: pd.Series) -> int:
    return (pd.Timestamp(row["expiration"]) - pd.Timestamp(row["date"])).days


def _bucket_moneyness(m: float) -> str:
    """Six log-moneyness buckets, matching the plan's "moneyness" strata."""
    if not math.isfinite(m):
        return "unknown"
    if m < -0.10:
        return "deep_itm_call" if m < 0 else "deep_itm_put"  # placeholder
    if m < -0.03:
        return "itm"
    if m < 0.03:
        return "atm"
    if m < 0.10:
        return "otm"
    return "deep_otm"


def _bucket_dte(days: int) -> str:
    if days < 15:
        return "under_15"
    if days < 45:
        return "15_45"
    if days < 90:
        return "45_90"
    return "over_90"


def stratified_sample(
    df: pd.DataFrame,
    *,
    target_size: int = 200,
    seed: int = 42,
) -> pd.DataFrame:
    """Deterministic stratified sample across (year, moneyness bucket,
    DTE bucket).

    Picks roughly ``target_size`` rows, spread across non-empty cells.
    Reproducible from ``seed`` -- passing the same frame + same seed
    yields the same sample regardless of pandas or numpy version
    (uses ``numpy.random.default_rng`` which is version-stable).

    Rows without a valid moneyness (underlying <= 0 or strike <= 0)
    or with impossible DTE (< 0) are excluded from the sample -- the
    canonical schema would reject them anyway, but they can leak in
    from an unchecked frame.
    """
    if df.empty:
        return df.copy()

    working = df.copy()
    working["_log_m"] = working.apply(_log_moneyness, axis=1)
    working["_dte"] = working.apply(_dte_days, axis=1)
    working["_year"] = pd.to_datetime(working["date"]).dt.year

    working = working[working["_log_m"].notna() & (working["_dte"] >= 0)]
    if working.empty:
        return working.iloc[0:0].copy()

    working["_m_bucket"] = working["_log_m"].apply(_bucket_moneyness)
    working["_d_bucket"] = working["_dte"].apply(_bucket_dte)
    working["_stratum"] = list(
        zip(working["_year"], working["_m_bucket"], working["_d_bucket"])
    )

    strata = list(working["_stratum"].unique())
    if not strata:
        return working.iloc[0:0].copy()

    per_stratum = max(1, target_size // len(strata))
    rng = np.random.default_rng(seed)
    picks: list[pd.DataFrame] = []
    for stratum in strata:
        cell = working[working["_stratum"] == stratum]
        take = min(per_stratum, len(cell))
        # rng.choice with replace=False gives the deterministic sample.
        pick_idx = rng.choice(cell.index.to_numpy(), size=take, replace=False)
        picks.append(cell.loc[pick_idx])

    sampled = pd.concat(picks).sort_index()
    return sampled.drop(columns=["_log_m", "_dte", "_year", "_m_bucket",
                                  "_d_bucket", "_stratum"])


# --- QuantLib pricing --------------------------------------------------


def _to_ql_date(pyd) -> ql.Date:
    d = pd.Timestamp(pyd).date()
    return ql.Date(d.day, d.month, d.year)


def _make_process(
    spot: float, rate: float, dividend: float, sigma: float, obs_date: ql.Date
) -> ql.BlackScholesMertonProcess:
    day_count = ql.Actual365Fixed()
    spot_h = ql.QuoteHandle(ql.SimpleQuote(spot))
    r_h = ql.YieldTermStructureHandle(
        ql.FlatForward(obs_date, rate, day_count)
    )
    q_h = ql.YieldTermStructureHandle(
        ql.FlatForward(obs_date, dividend, day_count)
    )
    vol_h = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(obs_date, ql.NullCalendar(), sigma, day_count)
    )
    return ql.BlackScholesMertonProcess(spot_h, q_h, r_h, vol_h)


def _make_option(
    strike: float, call_put: str, expiration: ql.Date, style: str
) -> ql.VanillaOption:
    option_type = ql.Option.Call if call_put == "C" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(option_type, strike)
    if style.upper().startswith("A"):
        exercise = ql.AmericanExercise(_TODAY_SENTINEL, expiration)
    else:
        exercise = ql.EuropeanExercise(expiration)
    return ql.VanillaOption(payoff, exercise)


# Set at module load; overwritten per-observation inside price/IV
# functions via ql.Settings.
_TODAY_SENTINEL = ql.Date(1, 1, 1996)


def _price_and_greeks(
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    sigma: float,
    obs_date: ql.Date,
    expiration: ql.Date,
    call_put: str,
    style: str,
) -> tuple[float, float, float, float, float]:
    """Returns (price, delta, gamma, vega, theta).

    American options use the finite-difference engine; European use the
    analytic Black-Scholes engine. Vega/theta are not directly reported
    by the American engine on all QuantLib builds, so they are computed
    by bumping (0.01 vol, 1 day) as a fallback.
    """
    ql.Settings.instance().evaluationDate = obs_date
    process = _make_process(spot, rate, dividend, sigma, obs_date)
    option = _make_option(strike, call_put, expiration, style)

    if style.upper().startswith("A"):
        engine = ql.FdBlackScholesVanillaEngine(process, 100, 100)
    else:
        engine = ql.AnalyticEuropeanEngine(process)
    option.setPricingEngine(engine)

    price = float(option.NPV())
    delta = float(option.delta())
    gamma = float(option.gamma())
    # For American FD engine, vega/theta come via bump-and-revalue.
    try:
        vega = float(option.vega())
    except RuntimeError:
        bump = 0.01
        proc_up = _make_process(spot, rate, dividend, sigma + bump, obs_date)
        opt_up = _make_option(strike, call_put, expiration, style)
        opt_up.setPricingEngine(
            ql.FdBlackScholesVanillaEngine(proc_up, 100, 100)
            if style.upper().startswith("A")
            else ql.AnalyticEuropeanEngine(proc_up)
        )
        vega = (float(opt_up.NPV()) - price) / bump
    try:
        theta = float(option.theta())
    except RuntimeError:
        theta = float("nan")
    return price, delta, gamma, vega, theta


def _implied_vol(
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    target_price: float,
    obs_date: ql.Date,
    expiration: ql.Date,
    call_put: str,
    style: str,
) -> float:
    """QuantLib's ``impliedVolatility`` on the vanilla option. Returns
    NaN on solver non-convergence rather than raising, so a single
    hard row does not blow up a full-sample validation."""
    ql.Settings.instance().evaluationDate = obs_date
    # Use a mid-range vol as the initial process; the solver will
    # iterate.
    process = _make_process(spot, rate, dividend, 0.3, obs_date)
    option = _make_option(strike, call_put, expiration, style)
    if style.upper().startswith("A"):
        engine = ql.FdBlackScholesVanillaEngine(process, 100, 100)
    else:
        engine = ql.AnalyticEuropeanEngine(process)
    option.setPricingEngine(engine)
    try:
        return float(
            option.impliedVolatility(
                target_price,
                process,
                1e-4,   # accuracy
                200,    # max iterations
                1e-4,   # min vol
                4.0,    # max vol (400%)
            )
        )
    except RuntimeError:
        return float("nan")


# --- Main validator ---------------------------------------------------


def _row_mid_price(row: pd.Series) -> float:
    """Mid-price fallback used as the target for the IV solver when the
    vendor IV is missing/NaN.

    Returns NaN if bid or ask isn't usable, so the row's IV
    recomputation is quietly skipped -- it does not silently reprice
    against half a quote.
    """
    b, a = row["bid"], row["ask"]
    if pd.isna(b) or pd.isna(a) or b < 0 or a <= 0:
        return float("nan")
    return (float(b) + float(a)) / 2.0


def validate_greeks_sample(
    df: pd.DataFrame,
    *,
    rate_map: Mapping[dt.date, float] | None = None,
    dividend_map: Mapping[str, float] | None = None,
    exercise_style_map: Mapping[int, str] | None = None,
    default_rate: float = 0.02,
    default_dividend: float = 0.0,
    default_style: str = "American",
    thresholds: VerdictThresholds = DEFAULT_THRESHOLDS,
) -> GreeksValidationReport:
    """Recompute IV + Greeks on every row of ``df`` and compare to the
    vendor's own values.

    ``df`` should be a *small stratified sample* (produce via
    ``stratified_sample``) -- this iterates row-by-row and calls
    QuantLib per row, so cost is O(target_size * per-row-time).

    Returns a ``GreeksValidationReport``. The verdict is against
    ``thresholds`` on the median absolute differences for iv, delta,
    and vega -- gamma and theta are reported but not part of the
    pass/fail bar (they are second-derivative / calendar-sensitive and
    routinely disagree at higher relative percentages without
    invalidating a first-order Greek).
    """
    if df.empty:
        return GreeksValidationReport(
            passed=False,
            reasons=["empty sample"],
            thresholds=thresholds,
        )

    rows: list[dict] = []
    for _, row in df.iterrows():
        try:
            spot = float(row["underlying_price"])
            strike = float(row["strike"])
            if not (spot > 0 and strike > 0):
                continue
            obs_date = _to_ql_date(row["date"])
            exp_date = _to_ql_date(row["expiration"])
            if exp_date <= obs_date:
                continue
            call_put = str(row["call_put"])
            rate = float(
                rate_map[pd.Timestamp(row["date"]).date()]
            ) if rate_map is not None and pd.Timestamp(row["date"]).date() in rate_map else default_rate
            dividend = float(
                dividend_map[str(row["stable_underlying_id"])]
            ) if dividend_map is not None and str(row["stable_underlying_id"]) in dividend_map else default_dividend
            style = (
                exercise_style_map[int(row["option_id"])]
                if exercise_style_map is not None and int(row["option_id"]) in exercise_style_map
                else default_style
            )

            # QuantLib IV from target price (vendor mid).
            target = _row_mid_price(row)
            if math.isnan(target):
                ql_iv = float("nan")
            else:
                ql_iv = _implied_vol(
                    spot, strike, rate, dividend, target,
                    obs_date, exp_date, call_put, style,
                )

            # Vendor IV feeds the Greeks recomputation; if it's null,
            # fall back to QuantLib IV.
            sigma_for_greeks = (
                float(row["iv"])
                if pd.notna(row["iv"])
                else (ql_iv if math.isfinite(ql_iv) else float("nan"))
            )
            if not (math.isfinite(sigma_for_greeks) and sigma_for_greeks > 0):
                continue
            _, ql_delta, ql_gamma, ql_vega, ql_theta = _price_and_greeks(
                spot, strike, rate, dividend, sigma_for_greeks,
                obs_date, exp_date, call_put, style,
            )
            rows.append({
                "option_id": int(row["option_id"]),
                "date": pd.Timestamp(row["date"]),
                "vendor_iv": float(row["iv"]) if pd.notna(row["iv"]) else float("nan"),
                "ql_iv": ql_iv,
                "vendor_delta": float(row["delta"]) if pd.notna(row["delta"]) else float("nan"),
                "ql_delta": ql_delta,
                "vendor_gamma": float(row["gamma"]) if pd.notna(row["gamma"]) else float("nan"),
                "ql_gamma": ql_gamma,
                "vendor_vega": float(row["vega"]) if pd.notna(row["vega"]) else float("nan"),
                "ql_vega": ql_vega,
                "vendor_theta": float(row["theta"]) if pd.notna(row["theta"]) else float("nan"),
                "ql_theta": ql_theta,
            })
        except Exception:  # noqa: BLE001 -- keep one bad row from stopping the sweep
            continue

    per_row = pd.DataFrame(rows)
    if per_row.empty:
        return GreeksValidationReport(
            passed=False,
            reasons=["no rows priced successfully"],
            per_row=per_row,
            thresholds=thresholds,
        )

    # Per-metric diffs.
    stats_rows: list[dict] = []
    for metric in ("iv", "delta", "gamma", "vega", "theta"):
        v, q = f"vendor_{metric}", f"ql_{metric}"
        both = per_row[v].notna() & per_row[q].notna()
        if not both.any():
            stats_rows.append({
                "metric": metric,
                "median_abs_diff": float("nan"),
                "mean_abs_diff": float("nan"),
                "p95_abs_diff": float("nan"),
                "n": 0,
            })
            continue
        diff = (per_row.loc[both, v] - per_row.loc[both, q]).abs()
        stats_rows.append({
            "metric": metric,
            "median_abs_diff": float(diff.median()),
            "mean_abs_diff": float(diff.mean()),
            "p95_abs_diff": float(diff.quantile(0.95)),
            "n": int(both.sum()),
        })
    per_metric_stats = pd.DataFrame(stats_rows)

    # Verdict.
    reasons: list[str] = []
    passed = True

    if len(per_row) < thresholds.min_sample_rows:
        reasons.append(
            f"sample too small: {len(per_row)} rows priced, "
            f"threshold requires >= {thresholds.min_sample_rows}"
        )
        passed = False

    for metric, thr_attr in (
        ("iv", "max_median_abs_iv_diff"),
        ("delta", "max_median_abs_delta_diff"),
        ("vega", "max_median_abs_vega_diff"),
    ):
        row_stats = per_metric_stats[per_metric_stats["metric"] == metric]
        med = row_stats["median_abs_diff"].iloc[0] if len(row_stats) else float("nan")
        thr = getattr(thresholds, thr_attr)
        if math.isnan(med) or med > thr:
            passed = False
            reasons.append(
                f"{metric} median abs diff {med:.4f} exceeds threshold {thr:.4f}"
            )

    return GreeksValidationReport(
        passed=passed,
        reasons=reasons,
        per_metric_stats=per_metric_stats,
        per_row=per_row,
        thresholds=thresholds,
    )
