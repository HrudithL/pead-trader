# Optimization line (2026-08): volatility targeting, dispersion filter, signal diagnostics

**Status: archived research, not ported into `pead/strategies`.** Recovered on 2026-10-18 from an uncommitted worktree
(`pead-strategy-optimization-634f65`, preserved on git branch `rescue/optimization-line-2026-08`, commit 8d0f6cc). It was built on the
2026-08-25 script layout, so the scripts here import `scripts/...` paths that no longer exist and **will not run unchanged**. They are kept as the record of
what was tried and what it found. Every number below is from a *regenerated* local book that matched the then-committed Strategy 6 only approximately
(script 35's note: 6.32% / Sharpe 0.99 / DD -17.3% here against 6.49% / 1.02 / -16.6% tracked), so treat them as indicative, not pinned. They predate every later correction
(options quote marking, benchmark, leverage cap, decile look-ahead; see `docs/STRATEGY_PROFILES.md`).

"Strategy 6" below is today's `balanced_leverage`; the static half-size market overlay is today's `market_boost`.

## Not the same thing as the GPU "Strategy 8"
Scripts 39-40 call themselves "Strategy 8". That is a **different strategy** from the machine-learning "Strategy 8" (walk-forward MLP, 3.5% / Sharpe 0.93, unverified, not ported) in the
GPU pipeline. To avoid confusion the result files for this one are renamed `dispersion_filter_*` here.

## What each piece found

| Piece | Script | Return | Vol | Sharpe | Worst fall | What it is |
|---|---|---|---|---|---|---|
| Static half-size market overlay (reference) | (existing) | 10.08% | | 0.97 | -23.2% | `market_boost` today |
| **Vol-targeted overlay** on the alpha book | 33 | 11.00% | 10.66% | 1.03 | -18.5% | Overlay size = target vol / trailing 63-day market vol, clipped; instead of a fixed 0.5x |
| Vol-targeted alpha book + static overlay | 34 | 10.74% | 10.78% | 1.00 | -24.4% | |
| **Fully vol-managed** (both sleeves) | 34 | 11.58% | 10.86% | 1.06 | -17.9% | Script 34 states an approximation: the alpha book is re-scaled, not re-run through the position-level engine |
| **Dispersion filter** | 39 | 5.96% | 4.54% | 1.30 | -10.8% | Conviction rank = 0.80 x SUE rank + 0.20 x analyst-dispersion rank (IBES) |
| Dispersion filter + vol-targeted overlay | 40 | 10.58% | 10.84% | 0.98 | -20.0% | |

Plain reading:
- **Volatility targeting the market overlay** cut the worst fall from -23.2% to -18.5% and raised return from 10.1% to 11.0% in this sample. The overlay holds less market when the market is stormy and more when calm.
- **The dispersion filter is a risk filter, not an alpha source.** Script 39's own docstring says analyst dispersion has no standalone predictive power (quintile-sort t = -0.43) and that the blend changes *which* low-conviction positions are trimmed. Its blend weight (0.20) was picked from a sweep of the same sample (Sharpe plateau 1.28-1.32 across 0.05-0.25; drawdown -17.3% at 0 to -9.4% at 0.25), so the drawdown improvement is in-sample.
- **Diagnostics 36-38** (analyst revisions and dispersion, revenue surprise built from Compustat `saleq`, seasonal-random-walk SUE) write **no output files**; they printed to the console. The only recorded conclusion is script 39's note on dispersion above. What 37 (revenue surprise) and 38 (seasonal SUE) found was not saved. Re-running them is the only way to know.
- **Script 35** re-ran the position-sizing "elbow" sweep on the neutralized Strategy 6 book (`results/sweep_strategy6_sizing.csv`).

## Contents
```
scripts/    33-40 as recovered (old layout; docstrings explain method and caveats)
results/    the CSV/JSON outputs those scripts wrote (dispersion-filter files renamed dispersion_filter_*)
figures/    the four figures they produced
```
Large derived parquet files (`positions_*_v2.parquet`, `decile_events*.parquet`) were not committed; they are regenerable and a copy is in the 2026-10-18 backup.

## If you want to turn any of this into a registered strategy
Each is a small, separable change to the current framework:
1. **Vol-targeted overlay**: a variant of `apply_beta_overlay` in `pead/engines/equity.py` taking a trailing-vol scalar (the scalar construction is in script 33).
2. **Dispersion filter**: a new `EquityStrategy` whose `weights()` blends `sue_rank_pct` with a dispersion rank column built from `raw_wrds/ibes/statsumu_epsus.parquet` (point-in-time: latest consensus snapshot strictly before the announcement; construction in script 36). It needs a new prep stage for the dispersion column.
Do the decile and hold-period corrections (`docs/FIXES.md`) first, then re-test, because both were developed against the uncorrected data.
