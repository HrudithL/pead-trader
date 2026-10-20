# PEAD strategy suite -- comprehensive report

_60 strategies, 1996-2013, $10M start, NAV-percentage Sharpe_

## 1. League table

Every strategy that has been run, by asset class and Sharpe. `validated` strategies are candidates for use, `research` are explorations/variants, `reference` are kept as documented counter-examples.

| name | family | status | annualized_return | annualized_vol | sharpe | max_drawdown | calmar | sharpe_1996_2001 | sharpe_2002_2007 | sharpe_2008_2013 |
|---|---|---|---|---|---|---|---|---|---|---|
| calm_rank_tilt_legacy | equity-volatility | reference | 7.1% | 3.8% | 1.81 | -11.6% | 0.61 | 2.94 | 1.89 | 0.78 |
| calm_rank_levered_legacy | equity-volatility | reference | 9.2% | 5.2% | 1.73 | -15.9% | 0.58 | 2.86 | 1.96 | 0.50 |
| calm_weighted_legacy | equity-volatility | reference | 6.7% | 4.5% | 1.47 | -15.6% | 0.43 | 2.40 | 1.63 | 0.59 |
| calm_rank_tilt | equity-volatility | research | 6.0% | 4.0% | 1.47 | -13.6% | 0.44 | 2.31 | 1.63 | 0.51 |
| calm_weighted_matched_legacy | equity-volatility | reference | 7.3% | 4.9% | 1.45 | -17.0% | 0.43 | 2.39 | 1.66 | 0.52 |
| calm_weighted_filter_legacy | equity-volatility | reference | 6.5% | 4.6% | 1.41 | -15.7% | 0.42 | 2.26 | 1.57 | 0.60 |
| calm_rank_levered | equity-volatility | research | 8.1% | 5.7% | 1.39 | -19.6% | 0.41 | 2.17 | 1.66 | 0.38 |
| sector_balanced_legacy | equity-neutralized | reference | 4.1% | 3.0% | 1.33 | -10.8% | 0.37 | 2.19 | 1.34 | 0.66 |
| balanced_leverage_legacy | equity-neutralized | reference | 6.5% | 4.9% | 1.30 | -16.6% | 0.39 | 2.07 | 1.52 | 0.47 |
| calm_weighted | equity-volatility | validated | 5.6% | 4.5% | 1.24 | -17.5% | 0.32 | 2.03 | 1.54 | 0.29 |
| volatility_filter_legacy | equity-volatility | reference | 6.3% | 5.0% | 1.23 | -16.7% | 0.38 | 1.90 | 1.44 | 0.48 |
| calm_weighted_matched | equity-volatility | validated | 5.8% | 4.9% | 1.18 | -18.7% | 0.31 | 1.94 | 1.47 | 0.25 |
| conviction_tilt_legacy | equity-neutralized | reference | 5.2% | 4.4% | 1.18 | -13.1% | 0.40 | 1.74 | 1.31 | 0.60 |
| market_exposed_legacy | equity-directional | reference | 7.3% | 6.6% | 1.11 | -15.5% | 0.47 | 1.79 | 1.48 | 0.32 |
| balanced_leverage | equity-neutralized | validated | 5.4% | 4.9% | 1.09 | -18.6% | 0.29 | 1.76 | 1.45 | 0.19 |
| market_boost_max_legacy | equity-max | reference | 15.0% | 13.7% | 1.09 | -37.0% | 0.41 | 1.39 | 1.73 | 0.21 |
| sector_balanced | equity-neutralized | validated | 3.1% | 2.9% | 1.08 | -11.4% | 0.28 | 1.72 | 1.25 | 0.41 |
| calm_weighted_filter | equity-volatility | research | 5.1% | 4.7% | 1.08 | -17.6% | 0.29 | 1.79 | 1.22 | 0.30 |
| graded_bets_legacy | equity-baseline | reference | 3.9% | 3.9% | 1.01 | -14.0% | 0.28 | 1.39 | 1.55 | 0.32 |
| balanced_leverage_max_legacy | equity-max | reference | 12.1% | 12.2% | 1.00 | -36.5% | 0.33 | 1.53 | 1.71 | -0.07 |
| sector_balanced_max_legacy | equity-max | reference | 12.1% | 12.2% | 1.00 | -36.5% | 0.33 | 1.53 | 1.71 | -0.07 |
| market_exposed_max_legacy | equity-max | reference | 11.8% | 11.8% | 1.00 | -33.0% | 0.36 | 1.64 | 1.65 | 0.13 |
| conviction_tilt | equity-neutralized | validated | 3.8% | 4.0% | 0.97 | -14.5% | 0.26 | 1.24 | 1.35 | 0.41 |
| market_boost_legacy | equity-overlay | reference | 10.1% | 10.5% | 0.97 | -23.2% | 0.43 | 1.30 | 1.14 | 0.48 |
| extreme_picks_legacy | equity-baseline | reference | 4.9% | 5.3% | 0.93 | -18.5% | 0.27 | 1.28 | 1.58 | 0.28 |
| size_balanced_legacy | equity-baseline | reference | 4.7% | 5.3% | 0.91 | -17.5% | 0.27 | 1.32 | 1.28 | 0.34 |
| market_boost | equity-overlay | validated | 9.2% | 10.4% | 0.90 | -25.1% | 0.36 | 1.20 | 1.10 | 0.40 |
| graded_bets_max_legacy | equity-max | reference | 10.8% | 12.4% | 0.89 | -38.8% | 0.28 | 1.24 | 1.63 | 0.09 |
| conviction_tilt_max_legacy | equity-max | reference | 10.7% | 12.3% | 0.89 | -40.0% | 0.27 | 1.30 | 1.54 | 0.07 |
| volatility_filter | equity-volatility | research | 4.6% | 5.2% | 0.89 | -18.8% | 0.25 | 1.43 | 1.07 | 0.19 |
| market_boost_max | equity-max | research | 11.6% | 14.0% | 0.86 | -44.6% | 0.26 | 1.17 | 1.48 | -0.05 |
| size_balanced | equity-baseline | validated | 3.8% | 4.8% | 0.81 | -18.1% | 0.21 | 0.93 | 1.44 | 0.24 |
| extreme_picks_max_legacy | equity-max | reference | 9.5% | 12.2% | 0.80 | -36.2% | 0.26 | 1.02 | 1.72 | -0.01 |
| conviction_tilt_max | equity-max | research | 9.3% | 12.6% | 0.76 | -40.5% | 0.23 | 1.08 | 1.45 | -0.05 |
| market_exposed | equity-directional | reference | 6.6% | 9.5% | 0.72 | -34.1% | 0.19 | 1.42 | 1.10 | 0.04 |
| size_balanced_max_legacy | equity-max | reference | 8.4% | 12.3% | 0.72 | -37.5% | 0.23 | 1.00 | 1.52 | 0.03 |
| size_balanced_max | equity-max | research | 8.9% | 13.0% | 0.72 | -43.6% | 0.20 | 0.82 | 1.52 | 0.09 |
| market_exposed_max | equity-max | research | 11.5% | 17.2% | 0.72 | -53.8% | 0.21 | 1.33 | 1.13 | 0.06 |
| balanced_leverage_max | equity-max | research | 8.3% | 12.8% | 0.69 | -44.9% | 0.19 | 1.20 | 1.39 | -0.41 |
| sector_balanced_max | equity-max | research | 8.3% | 12.8% | 0.69 | -44.9% | 0.19 | 1.20 | 1.39 | -0.41 |
| extreme_picks | equity-baseline | validated | 3.3% | 5.2% | 0.65 | -25.2% | 0.13 | 0.86 | 1.62 | -0.11 |
| graded_bets_max | equity-max | research | 8.6% | 14.4% | 0.64 | -48.0% | 0.18 | 0.97 | 1.53 | -0.20 |
| extreme_picks_max | equity-max | research | 9.0% | 15.6% | 0.63 | -56.4% | 0.16 | 0.78 | 1.58 | -0.10 |
| graded_bets | equity-baseline | validated | 2.4% | 3.9% | 0.62 | -19.5% | 0.12 | 0.96 | 1.52 | -0.23 |
| options_cap20_legacy | options-kelly | reference | 8.6% | 2.2% | 3.78 | -10.3% | 0.83 | 1.64 | 4.80 | 4.39 |
| options_cap30_legacy | options-kelly | reference | 12.3% | 3.3% | 3.49 | -17.4% | 0.70 | 0.98 | 4.67 | 4.10 |
| options_cap40_legacy | options-kelly | reference | 15.8% | 4.5% | 3.28 | -23.9% | 0.66 | 0.54 | 4.58 | 3.93 |
| options_cap20_midfill | options-kelly | reference | 4.4% | 2.4% | 1.85 | -14.5% | 0.31 | 1.19 | 2.01 | 2.37 |
| options_cap20_prefix_fix | options-kelly | reference | 4.1% | 2.3% | 1.77 | -13.8% | 0.30 | 0.69 | 2.04 | 2.39 |
| options_cap30_midfill | options-kelly | reference | 6.1% | 3.7% | 1.62 | -22.5% | 0.27 | 0.57 | 1.91 | 2.16 |
| options_cap30_prefix_fix | options-kelly | reference | 5.7% | 3.6% | 1.55 | -22.3% | 0.26 | 0.12 | 1.95 | 2.17 |
| options_worst_case_prefix_fix | options-kelly | reference | 2.3% | 2.4% | 0.99 | -17.1% | 0.14 | 0.32 | 0.99 | 1.60 |
| options_cap20 | options-kelly | validated | 0.3% | 1.9% | 0.16 | -18.9% | 0.01 | 0.07 | 0.31 | 0.00 |
| options_cap30 | options-kelly | validated | -0.1% | 3.0% | -0.02 | -29.0% | -0.00 | -0.48 | 0.28 | -0.10 |
| options_worst_case | options-kelly | research | -1.0% | 2.0% | -0.52 | -32.3% | -0.03 | -0.30 | -0.58 | -0.66 |
| legacy_blend | portfolio | reference | 14.2% | 4.2% | 3.21 | -9.9% | 1.44 | 2.79 | 4.64 | 2.61 |
| calm_blend_prefix_fix | portfolio | reference | 12.4% | 4.7% | 2.50 | -15.5% | 0.80 | 2.92 | 2.84 | 2.10 |
| hedged_blend_prefix_fix | portfolio | reference | 11.3% | 4.8% | 2.25 | -16.7% | 0.68 | 2.41 | 2.70 | 1.91 |
| calm_blend | portfolio | research | 5.8% | 4.5% | 1.28 | -20.1% | 0.29 | 2.13 | 1.36 | 0.43 |
| hedged_blend | portfolio | validated | 4.9% | 4.5% | 1.09 | -21.8% | 0.22 | 1.82 | 1.31 | 0.25 |

![Each dot is one backtest. Options books and the hedged blend sit high-left (high return per unit of risk).](figures/frontier.png)

*Each dot is one backtest. Options books and the hedged blend sit high-left (high return per unit of risk).*

## 2. What each iteration changed

| strategy | parent | d_return | d_vol | d_sharpe | d_maxdd |
|---|---|---|---|---|---|
| balanced_leverage | sector_balanced | +2.24% | +2.02% | +0.01 | -7.2% |
| balanced_leverage_legacy | balanced_leverage | +1.12% | +0.03% | +0.21 | +1.9% |
| balanced_leverage_max | balanced_leverage | +2.97% | +7.91% | -0.40 | -26.3% |
| balanced_leverage_max_legacy | balanced_leverage_max | +3.80% | -0.62% | +0.31 | +8.4% |
| calm_blend | hedged_blend | +0.90% | +0.01% | +0.19 | +1.7% |
| calm_blend_prefix_fix | calm_blend | +6.63% | +0.25% | +1.22 | +4.6% |
| calm_rank_levered | calm_rank_tilt | +2.06% | +1.66% | -0.08 | -6.0% |
| calm_rank_levered_legacy | calm_rank_levered | +1.16% | -0.53% | +0.34 | +3.7% |
| calm_rank_tilt | calm_weighted | +0.37% | -0.47% | +0.23 | +3.9% |
| calm_rank_tilt_legacy | calm_rank_tilt | +1.11% | -0.20% | +0.35 | +2.0% |
| calm_weighted | balanced_leverage | +0.26% | -0.40% | +0.15 | +1.0% |
| calm_weighted_filter | balanced_leverage | -0.32% | -0.24% | -0.01 | +0.9% |
| calm_weighted_filter_legacy | calm_weighted_filter | +1.48% | -0.11% | +0.33 | +2.0% |
| calm_weighted_legacy | calm_weighted | +1.07% | -0.02% | +0.23 | +1.9% |
| calm_weighted_matched | calm_weighted | +0.19% | +0.37% | -0.05 | -1.1% |
| calm_weighted_matched_legacy | calm_weighted_matched | +1.48% | +0.05% | +0.27 | +1.6% |
| conviction_tilt | graded_bets | +1.47% | +0.06% | +0.35 | +5.0% |
| conviction_tilt_legacy | conviction_tilt | +1.41% | +0.44% | +0.21 | +1.4% |
| conviction_tilt_max | conviction_tilt | +5.42% | +8.68% | -0.20 | -26.0% |
| conviction_tilt_max_legacy | conviction_tilt_max | +1.43% | -0.40% | +0.13 | +0.5% |
| extreme_picks_legacy | extreme_picks | +1.59% | +0.08% | +0.28 | +6.7% |
| extreme_picks_max | extreme_picks | +5.67% | +10.37% | -0.02 | -31.3% |
| extreme_picks_max_legacy | extreme_picks_max | +0.55% | -3.37% | +0.18 | +20.2% |
| graded_bets_legacy | graded_bets | +1.52% | -0.05% | +0.39 | +5.5% |
| graded_bets_max | graded_bets | +6.24% | +10.51% | +0.03 | -28.6% |
| graded_bets_max_legacy | graded_bets_max | +2.23% | -2.03% | +0.25 | +9.3% |
| hedged_blend | calm_weighted | -0.74% | -0.05% | -0.14 | -4.3% |
| hedged_blend_prefix_fix | hedged_blend | +6.43% | +0.37% | +1.15 | +5.1% |
| legacy_blend | hedged_blend | +9.35% | -0.28% | +2.11 | +11.9% |
| market_boost | balanced_leverage | +3.77% | +5.48% | -0.20 | -6.5% |
| market_boost_legacy | market_boost | +0.93% | +0.12% | +0.07 | +1.9% |
| market_boost_max | balanced_leverage_max | +3.27% | +1.14% | +0.17 | +0.3% |
| market_boost_max_legacy | market_boost_max | +3.39% | -0.26% | +0.23 | +7.7% |
| market_exposed | balanced_leverage | +1.26% | +4.57% | -0.37 | -15.6% |
| market_exposed_legacy | market_exposed | +0.66% | -2.93% | +0.38 | +18.6% |
| market_exposed_max | market_exposed | +4.89% | +7.72% | -0.01 | -19.6% |
| market_exposed_max_legacy | market_exposed_max | +0.26% | -5.36% | +0.28 | +20.8% |
| options_cap20_midfill | options_cap20 | +4.15% | +0.43% | +1.69 | +4.4% |
| options_cap20_prefix_fix | options_cap20 | +3.84% | +0.38% | +1.61 | +5.0% |
| options_cap30 | options_cap20 | -0.38% | +1.08% | -0.17 | -10.1% |
| options_cap30_legacy | options_cap20_legacy | +3.68% | +1.15% | -0.29 | -7.1% |
| options_cap30_midfill | options_cap20_midfill | +1.67% | +1.34% | -0.23 | -8.1% |
| options_cap30_prefix_fix | options_cap30 | +5.79% | +0.60% | +1.57 | +6.7% |
| options_cap40_legacy | options_cap20_legacy | +7.21% | +2.32% | -0.50 | -13.6% |
| options_worst_case | options_cap20 | -1.32% | +0.06% | -0.67 | -13.5% |
| options_worst_case_prefix_fix | options_worst_case | +3.38% | +0.38% | +1.50 | +15.2% |
| sector_balanced | conviction_tilt | -0.70% | -1.08% | +0.12 | +3.1% |
| sector_balanced_legacy | sector_balanced | +0.92% | +0.13% | +0.25 | +0.6% |
| sector_balanced_max | sector_balanced | +5.21% | +9.93% | -0.40 | -33.5% |
| sector_balanced_max_legacy | sector_balanced_max | +3.80% | -0.62% | +0.31 | +8.4% |
| size_balanced | extreme_picks | +0.50% | -0.47% | +0.16 | +7.1% |
| size_balanced_legacy | size_balanced | +0.92% | +0.49% | +0.10 | +0.5% |
| size_balanced_max | size_balanced | +5.09% | +8.23% | -0.09 | -25.6% |
| size_balanced_max_legacy | size_balanced_max | -0.46% | -0.74% | +0.00 | +6.2% |
| volatility_filter | balanced_leverage | -0.77% | +0.30% | -0.20 | -0.2% |
| volatility_filter_legacy | volatility_filter | +1.66% | -0.16% | +0.34 | +2.1% |

`d_*` columns are the change from the parent strategy. Reading down: neutralizing size and sector (Conviction Tilt -> Sector Balanced) and inverse-vol sizing (Balanced Leverage -> Calm Weighted) buy Sharpe; raising the leverage cap buys return; the max-size variants buy return at the cost of Sharpe because the liquidity cap starts to bind.

## 3. Stability across eras

| name | sharpe_1996_2001 | sharpe_2002_2007 | sharpe_2008_2013 |
|---|---|---|---|
| extreme_picks | 0.86 | 1.62 | -0.11 |
| graded_bets | 0.96 | 1.52 | -0.23 |
| conviction_tilt | 1.24 | 1.35 | 0.41 |
| sector_balanced | 1.72 | 1.25 | 0.41 |
| balanced_leverage | 1.76 | 1.45 | 0.19 |
| market_exposed | 1.42 | 1.10 | 0.04 |
| calm_weighted | 2.03 | 1.54 | 0.29 |
| volatility_filter | 1.43 | 1.07 | 0.19 |
| calm_rank_tilt | 2.31 | 1.63 | 0.51 |
| market_boost | 1.20 | 1.10 | 0.40 |
| options_cap20 | 0.07 | 0.31 | 0.00 |
| options_cap30 | -0.48 | 0.28 | -0.10 |
| options_cap20_legacy | 1.64 | 4.80 | 4.39 |
| hedged_blend | 1.82 | 1.31 | 0.25 |

| year | calm_weighted | options_cap20 | hedged_blend | market |
|---|---|---|---|---|
| 1996 | 12.8% | 0.0% | 10.1% | 21.2% |
| 1997 | 13.3% | 0.0% | 10.5% | 30.4% |
| 1998 | 14.6% | 3.6% | 18.0% | 22.3% |
| 1999 | 4.0% | -1.9% | 0.1% | 25.7% |
| 2000 | 18.6% | 1.6% | 17.7% | -11.3% |
| 2001 | -0.2% | -2.8% | -4.5% | -11.0% |
| 2002 | 4.7% | -9.6% | -11.8% | -20.9% |
| 2003 | 1.3% | 11.3% | 20.0% | 33.1% |
| 2004 | 9.1% | 6.3% | 18.2% | 13.0% |
| 2005 | 9.4% | -4.2% | 0.4% | 7.3% |
| 2006 | 5.3% | -0.8% | 2.9% | 16.2% |
| 2007 | 10.9% | 2.7% | 13.4% | 7.3% |
| 2008 | 9.7% | -10.3% | -9.5% | -38.2% |
| 2009 | -11.8% | 2.0% | -6.6% | 31.2% |
| 2010 | 2.7% | 0.8% | 3.6% | 17.7% |
| 2011 | 5.9% | - | 4.7% | -1.1% |
| 2012 | 0.3% | -6.6% | -10.1% | 15.8% |
| 2013 | 2.3% | 15.9% | 29.0% | 30.5% |

![Calendar-year return of the equity sleeve, the options sleeve, the blend and the market.](figures/years_sleeves.png)

*Calendar-year return of the equity sleeve, the options sleeve, the blend and the market.*

**The equity drift has decayed**: Calm Weighted's return averaged 11.2% a year in 1996-99 and 2.8% in 2011-13, the usual signature of a drift being arbitraged away.

## 4. Equity books: long leg vs. short leg

| strategy | long_pnl_M | short_pnl_M | long_excess_M | short_excess_M | long_bp_per_dollar | short_bp_per_dollar | corr_long_short |
|---|---|---|---|---|---|---|---|
| extreme_picks | 18.3 | -6.7 | 7.6 | 4.1 | 259.7 | -110.4 | -0.59 |
| graded_bets | 15.1 | -6.7 | 4.8 | 4.0 | 223.4 | -107.4 | -0.71 |
| conviction_tilt | 20.5 | -6.3 | 8.8 | 6.1 | 293.2 | -80.2 | -0.77 |
| sector_balanced | 16.2 | -5.4 | 7.2 | 3.8 | 277.9 | -90.8 | -0.79 |
| balanced_leverage | 36.7 | -13.1 | 13.3 | 11.0 | 252.7 | -88.0 | -0.80 |
| calm_weighted | 37.5 | -12.3 | 13.6 | 12.2 | 252.2 | -81.1 | -0.79 |

- **In every equity strategy the short leg loses money in absolute terms** while the long leg earns more than the total. The headline is real but misleading: returns are measured against the cap-weighted market, and the average event stock beats that benchmark (small-cap tilt plus the earnings-announcement premium), flattering the long leg and penalizing the short leg by about the same amount.
- Stripped of that common component (columns `*_excess_M`, measured against the average announcer of the quarter) the long leg's signal earns 1.1x to 1.9x the short leg's: close to equal against this benchmark, not exactly equal. Against a size-matched benchmark the long leg is clearly ahead per dollar (`docs/STRATEGY_PROFILES.md`, `results/profiles_benchmark_compare.csv`); the by-side conclusion therefore depends on the benchmark.
- The legs' daily P&L are -0.80 to -0.59 correlated: the short leg is a hedge. Dropping it for a long-only book would multiply the volatility and add market-relative factor exposure without adding alpha.
- The backtest charges no stock-borrow fee. The short leg is small-cap-heavy where borrow is expensive; the per-strategy report's borrow table gives the cost of 0.5% to 5% a year.
- Balanced Leverage, alpha per dollar traded against the average announcer of the quarter (all-announcer benchmark): smallest size quintile 28 bp, second 125 bp, largest 29 bp; least-liquid quintile 133 bp, most liquid 24 bp; holds over 90 days 185 bp vs 59 bp for holds up to 40 days; by era 120 / 82 / 19 bp (6.4x fall from first to last period). The size pattern depends on the benchmark: a size-matched benchmark (docs/STRATEGY_PROFILES.md) gives the smallest quintile a much higher figure.

## 5. Options book: what the return is made of, and how much of it is real

**The originally reported options result does not survive a careful look at the quotes and the fills.** The original backtest marked a missing forward quote at the previous live quote and filled at the mid. Re-querying one year of quotes (a pre-fix check, not repeated) found that most missing 60-day quotes were zero-bid dead contracts that the convention never marked down. Results: 8.58% / Sharpe 3.78 as reported -> 4.43% / 1.85 with mid fills on the corrected deciles (`options_cap20_midfill`) -> 0.28% / 0.16 filled at the real ask in and bid out (`options_cap20`) -> -1.04% / -0.52 if dead options are worth nothing. **After real fills the options book has no measurable edge** (Sharpe 0.16).

- 97.1% of funded trades are calls, producing 59.8% of gross P&L -- across ALL deciles, including the worst surprises. The Kelly selector finds cheap OTM calls better than puts even on bad news.
- Win rate 31%, median trade -50%, mean 1%: a convex pay-off. The top 1% of trades are 1151% of gross P&L.
- Where the (near-zero) net result comes from, by moneyness and volatility, is in the tables below and in `results/options_cap20/report.md`: deep in-the-money calls take 41% of the premium at -3.0% on it, and the high-volatility tercile returns -4.0% on premium.
- Return on premium does not rise cleanly with SUE decile, so this is not a pure drift trade, and the profit is dominated by a few years (largest single-year share of net P&L: 308%).

| vol_tercile | trades | premium_M | gross_M | net_M | share_of_net | roi_on_premium |
|---|---|---|---|---|---|---|
| low vol | 19,712 | 14.15 | 1.25 | 1.25 | 258.4% | 8.8% |
| mid vol | 19,712 | 12.36 | 0.04 | 0.04 | 8.9% | 0.3% |
| high vol | 19,712 | 20.38 | -0.81 | -0.81 | -167.6% | -4.0% |

### Execution and marking sensitivity

| case | annualized_return | annualized_vol | sharpe | max_drawdown | trades |
|---|---|---|---|---|---|
| as registered | 0.28% | 1.92% | 0.16 | -18.9% | 59,153 |
| ORIGINAL convention: dead quotes carried forward, flat 3% cost | 2.45% | 1.82% | 1.33 | -12.9% | 62,494 |
| dead quotes at half-ask, flat 3% cost (no tick floor) | 0.28% | 1.92% | 0.16 | -18.9% | 59,153 |
| dead quotes worth zero (pessimistic bound) | -1.04% | 1.99% | -0.52 | -32.3% | 57,102 |
| + $0.10/share minimum cost | -3.28% | 1.99% | -1.66 | -52.3% | 53,826 |
| skip contracts under $0.25 | 0.03% | 1.73% | 0.03 | -18.6% | 47,184 |
| skip contracts under $0.50 | -0.06% | 1.60% | -0.03 | -19.1% | 39,309 |
| flat 10% round-trip cost | -2.31% | 1.96% | -1.19 | -44.4% | 54,475 |

The `_legacy` and `_prefix_fix` options books are the historical headline only. Everything the hedged blend says about options is built on the corrected `options_cap20`, whose edge is approximately zero.

## 6. How the strategies move together

![Equity books cluster together; options are independent of them.](figures/corr.png)

*Equity books cluster together; options are independent of them.*

- The market-neutral equity books are 0.58 to 0.98 correlated with each other -- they are one bet expressed several ways, so combining them adds nothing.
- Equity vs. options correlation: -0.06. The options book's correlation to the market is 0.19; the market-neutral equity books' range is -0.09 to 0.05.
- `market_boost` is 0.94 correlated with the market by construction; it answers 'beat the S&P', not 'diversify'.

## 7. Combining an equity book with an options book

### Capital split: fraction of NAV in each sleeve (weights sum to 1)

![Sharpe by equity share of capital; the in-sample peak is at 70% equity.](figures/frontier_sharpe.png)

*Sharpe by equity share of capital; the in-sample peak is at 70% equity.*

| method | w_equity | w_options | annualized_return | annualized_vol | sharpe | max_drawdown |
|---|---|---|---|---|---|---|
| equal | 0.50 | 0.50 | 2.9% | 2.4% | 1.23 | -9.0% |
| inverse_vol | 0.28 | 0.72 | 1.8% | 1.8% | 1.01 | -10.2% |
| max_sharpe (in-sample) | 0.69 | 0.31 | 4.0% | 3.1% | 1.26 | -11.8% |
| walk-forward max-Sharpe (out-of-sample) | 0.63 | 0.37 | 3.2% | 3.0% | 1.09 | -12.9% |

### Stacked on one NAV (weights are multiples of each sleeve's own sizing)

| target_vol | equity_multiple | options_multiple | annualized_return | annualized_vol | sharpe | max_drawdown | worst_day |
|---|---|---|---|---|---|---|---|
| 3% | 0.49 | 1.23 | 3.0% | 3.0% | 1.01 | -16.9% | -1.8% |
| 5% | 0.81 | 2.05 | 5.0% | 5.0% | 1.01 | -26.6% | -3.0% |
| 8% | 1.29 | 3.28 | 8.1% | 8.0% | 1.01 | -39.2% | -4.7% |

Equal-risk weights (each sleeve contributes the same stand-alone volatility). The 5% row is the registered `hedged_blend`. Larger multiples assume the extra options premium and equity gross exposure can actually be financed and traded at the backtested costs.

### The hedge in action: stress years

| year | calm_weighted | options_cap20 | hedged_blend | market |
|---|---|---|---|---|
| 2000 | 18.6% | 1.6% | 17.7% | -11.3% |
| 2001 | -0.2% | -2.8% | -4.5% | -11.0% |
| 2002 | 4.7% | -9.6% | -11.8% | -20.9% |
| 2008 | 9.7% | -10.3% | -9.5% | -38.2% |
| 2009 | -11.8% | 2.0% | -6.6% | 31.2% |

- The options book's worst year (2008, -10.3%) coincided with the equity book's +9.7%; in 2009 the signs flipped. A low correlation across time is the only hedge here; it is not an offsetting position.
- **Blend vs its equity sleeve.** The registered blend (0.8x equity + 1.6x options) has return 4.9%, Sharpe 1.09, max drawdown -21.8%; `calm_weighted` alone has 5.6%, 1.24, -17.5%. The best in-sample capital split has Sharpe 1.26; the walk-forward split 1.09. Where these are not clearly above the equity sleeve alone, the options sleeve adds risk, not return.
- The blend's market beta is 0.018: there is nothing left for an index hedge to remove. Layering a short-index overlay on top would only pay costs; a market-beta overlay (`market_boost`) goes the other way, adding the equity risk premium on purpose.
- Everything here is in-sample except the walk-forward row. The caveats that apply to the sleeves apply to the blend: the options sleeve's costs, the equity sleeve's missing borrow fees, and no financing cost on the stacked multiples.

## 8. Conclusions and next steps

- Use `calm_weighted` as the equity alpha sleeve (validated, no fitted parameters; `calm_rank_tilt` is the stronger research candidate; the constants of both were chosen before the methodology fixes and not re-selected). The options sleeve (`options_cap20`: 0.28% a year, Sharpe 0.16) shows no measurable edge after real bid/ask fills, so no options sleeve or blend weights are recommended; `hedged_blend` is kept as a reference. See section 7 for how the blend compares with its equity sleeve.
- Do not add more market-neutral equity variants -- they are one bet. New equity ideas must change the signal or the holding rule, not the sizing.
- Options: the quote and fill conventions were the biggest errors found in this project (docs/FIXES.md). Open follow-ups: re-score the Kelly selector at the ask instead of the mid, and test on a holdout; do not size anything on the options sleeve until an edge survives real fills.
- The measured weak spots to attack on the equity side: the era decay (Balanced Leverage's alpha per dollar by era is in section 4), the <=40-day holding cells that earn little after costs, and the mid deciles that earn almost no excess per dollar.
