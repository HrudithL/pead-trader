"""Post-run stability and market-model diagnostics for the frozen PEAD call audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from pead.common.paths import DATA_DIR, REPO_ROOT
from pead.research.french_factors import load_french_daily


CELL = ("C", 120, 0.5, 60)


def hac_regression(y: np.ndarray, x: np.ndarray, lags: int = 60) -> dict:
    """OLS with Bartlett Newey-West covariance; annualize only the intercept."""
    y = np.asarray(y, float)
    x = np.asarray(x, float)
    keep = np.isfinite(y) & np.isfinite(x).all(axis=1)
    y, x = y[keep], x[keep]
    X = np.column_stack([np.ones(len(y)), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    u = y - X @ beta
    xu = X * u[:, None]
    meat = xu.T @ xu
    for lag in range(1, min(lags, len(y) - 1) + 1):
        gamma = xu[lag:].T @ xu[:-lag]
        meat += (1 - lag / (lags + 1)) * (gamma + gamma.T)
    bread = np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.maximum(0, np.diag(bread @ meat @ bread)))
    return {
        "n": len(y), "daily_intercept": float(beta[0]),
        "annualized_intercept": float(beta[0] * 252),
        "annualized_intercept_95ci": [float((beta[0] - 1.96 * se[0]) * 252),
                                       float((beta[0] + 1.96 * se[0]) * 252)],
        "intercept_hac_t": float(beta[0] / se[0]) if se[0] > 0 else None,
        "slopes": [float(v) for v in beta[1:]],
        "lag": lags,
    }


def year_block_sharpe_interval(frame: pd.DataFrame, n_boot: int = 5000,
                               seed: int = 20261022) -> dict:
    """Resample whole calendar years of daily excess return, preserving within-year overlap."""
    groups = [g["excess_return"].to_numpy(dtype=float)
              for _, g in frame.groupby(frame.date.dt.year)]
    observed = frame.excess_return.to_numpy(dtype=float)
    def ratio(values):
        sd = np.std(values, ddof=1)
        return float(np.mean(values) / sd * np.sqrt(252)) if sd > 0 else np.nan
    rng = np.random.default_rng(seed)
    draws = np.array([ratio(np.concatenate([groups[i] for i in
                       rng.integers(0, len(groups), len(groups))]))
                      for _ in range(n_boot)])
    return {"observed_excess_rf_sharpe": ratio(observed),
            "year_block_95ci": [float(np.nanquantile(draws, 0.025)),
                                float(np.nanquantile(draws, 0.975))],
            "resamples": n_boot, "seed": seed}


def grid_stability(yearly: pd.DataFrame, n_boot: int = 10000, seed: int = 20261022):
    keys = ["direction", "dte_target", "delta_target", "hold"]
    years = sorted(yearly.year.unique())
    cells = yearly[keys].drop_duplicates().sort_values(keys)
    panel = cells.assign(_key=range(len(cells))).merge(yearly, on=keys, how="left")
    wide = panel.pivot_table(index="year", columns="_key", values="net_pnl_realized",
                             aggfunc="sum").reindex(years).fillna(0.0)
    mat = wide.to_numpy()
    n = len(years)
    mean = mat.mean(axis=0)
    sd = mat.std(axis=0, ddof=1)
    tstat = np.divide(mean * np.sqrt(n), sd, out=np.zeros_like(mean), where=sd > 0)
    centered = mat - mean
    rng = np.random.default_rng(seed)
    maxima = []
    for _ in range(n_boot):
        draw = centered[rng.integers(0, n, size=n)]
        dsd = draw.std(axis=0, ddof=1)
        dt = np.divide(draw.mean(axis=0) * np.sqrt(n), dsd,
                       out=np.zeros(mat.shape[1]), where=dsd > 0)
        maxima.append(float(np.max(dt)))
    maxima = np.array(maxima)
    cells = cells.reset_index(drop=True).copy()
    cells["total_pnl"] = mat.sum(axis=0)
    cells["mean_annual_pnl"] = mean
    cells["annual_pnl_t"] = tstat
    cells["positive_observed_years"] = (mat > 0).sum(axis=0)
    cells["pnl_ex_2013"] = mat[[y != 2013 for y in years]].sum(axis=0)
    cells["pnl_ex_2003_2013"] = mat[[y not in (2003, 2013) for y in years]].sum(axis=0)
    cells["pnl_2008_2012"] = mat[[y in (2008, 2009, 2010, 2012) for y in years]].sum(axis=0)
    cells["fixed_1998_2013_cagr"] = np.where(
        1 + cells.total_pnl / 10_000_000 > 0,
        np.maximum(0, 1 + cells.total_pnl / 10_000_000) ** (1 / 16) - 1, -1.0)
    winner = cells[(cells.direction == CELL[0]) & (cells.dte_target == CELL[1])
                   & (cells.delta_target == CELL[2]) & (cells.hold == CELL[3])].iloc[0]
    win_idx = int(winner.name)
    ci = stats.t.interval(0.95, n - 1, loc=mean[win_idx], scale=sd[win_idx] / np.sqrt(n))
    evidence = {
        "observed_years": [int(y) for y in years], "cells": len(cells),
        "winner_annual_pnl_t": float(tstat[win_idx]),
        "winner_annual_pnl_95ci": [float(ci[0]), float(ci[1])],
        "winner_one_sided_annual_t_p": float(stats.t.sf(tstat[win_idx], n - 1)),
        "max_t_observed": float(tstat.max()),
        "max_t_familywise_bootstrap_p": float((1 + (maxima >= tstat.max()).sum()) / (n_boot + 1)),
        "max_t_null_95pct": float(np.quantile(maxima, 0.95)),
        "bootstrap_resamples": n_boot, "bootstrap_seed": seed,
        "note": "Illustrative joint-year centered max-t across an already mined archive; not untouched proof.",
    }
    return cells, evidence


def run(grid_yearly: Path, daily_csv: Path, benchmark_path: Path,
        factor_dir: Path, output_dir: Path):
    yearly = pd.read_csv(grid_yearly)
    stability, evidence = grid_stability(yearly)
    daily = pd.read_csv(daily_csv, parse_dates=["date"])
    benchmark = pd.read_parquet(benchmark_path, columns=["date", "vwretd"])
    benchmark["date"] = pd.to_datetime(benchmark.date)
    merged = daily.merge(benchmark, on="date", how="left", validate="one_to_one")
    clean = merged.dropna(subset=["return", "vwretd"])
    market = clean.vwretd.to_numpy(dtype=float)
    excess = clean["return"].to_numpy(dtype=float)
    evidence["market_model_zero_rf"] = hac_regression(excess, market[:, None])
    evidence["market_and_centered_market_squared_zero_rf"] = hac_regression(
        excess, np.column_stack([market, market ** 2 - (market ** 2).mean()]))
    evidence["missing_market_days"] = int(merged.vwretd.isna().sum())
    factors, provenance = load_french_daily(factor_dir)
    factor_daily = daily.merge(factors, on="date", how="left", validate="one_to_one")
    names = ["Mkt-RF", "SMB", "HML", "Mom", "RF"]
    factor_clean = factor_daily.dropna(subset=["return", *names])
    evidence["missing_factor_days"] = int(len(factor_daily) - len(factor_clean))
    factor_y = factor_clean["return"].to_numpy(dtype=float) - factor_clean.RF.to_numpy(dtype=float)
    factor_x = factor_clean[["Mkt-RF", "SMB", "HML", "Mom"]].to_numpy(dtype=float)
    evidence["four_factor_excess_rf"] = hac_regression(factor_y, factor_x)
    squared_market = factor_x[:, 0] ** 2
    evidence["four_factor_plus_centered_market_squared_excess_rf"] = hac_regression(
        factor_y, np.column_stack([factor_x, squared_market - squared_market.mean()]))
    factor_clean = factor_clean.copy()
    factor_clean["excess_return"] = factor_y
    evidence["excess_rf_sharpe_year_block"] = year_block_sharpe_interval(factor_clean)
    evidence["factor_provenance"] = provenance
    output_dir.mkdir(parents=True, exist_ok=True)
    stability.to_csv(output_dir / "grid_stability.csv", index=False)
    (output_dir / "deep_diagnostics.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return stability, evidence


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--grid-yearly", type=Path,
                   default=REPO_ROOT / "results/options_expiry_grid_portfolio/portfolio_yearly.csv")
    p.add_argument("--daily-csv", type=Path,
                   default=REPO_ROOT / "results/options_frozen_call_daily/riskfree_daily_portfolio.csv")
    p.add_argument("--benchmark", type=Path,
                   default=DATA_DIR / "normalized_equity/market_benchmark.parquet")
    p.add_argument("--factor-dir", type=Path, default=REPO_ROOT / "data/factor")
    p.add_argument("--output-dir", type=Path,
                   default=REPO_ROOT / "results/options_frozen_call_daily")
    a = p.parse_args()
    run(a.grid_yearly, a.daily_csv, a.benchmark, a.factor_dir, a.output_dir)


if __name__ == "__main__":
    main()
