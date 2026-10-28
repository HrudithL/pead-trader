# Leaderboard v2

Ranked within validation level by the lower 95% bound of a month-block bootstrap of the active-window Sharpe (300 draws, seed 20261027). `dsr` uses the registry's 114 trials, a lower bound. Holdout = 2008Q3-2013Q4 and is burned. `V1` = passes the look-ahead suite; `thin` = fewer than 3,000 funded positions (a mostly-cash book whose Sharpe is not comparable); no strategy is frozen, so none is V2 or above. `ref` = legacy or reference runs.

## Current runs (V1)

| name | asset_class | V | cagr_active | sharpe | sharpe_lo95 | sharpe_hi95 | psr0 | dsr | holdout_sharpe | holdout_over_search | max_drawdown |
|---|---|---|---|---|---|---|---|---|---|---|---|
| calm_rank_tilt | equity | V1 | 6.44% | 1.52 | 1.0 | 2.07 | 1.0 | 0.44 | 0.41 | 0.21 | -13.59% |
| calm_rank_levered | equity | V1 | 8.66% | 1.44 | 0.96 | 1.99 | 1.0 | 0.31 | 0.28 | 0.14 | -19.6% |
| steady_drift_plus_stop5 | equity | V1 | 9.81% | 1.3 | 0.87 | 1.75 | 1.0 | 0.14 | 0.61 | 0.35 | -19.21% |
| steady_drift_plus_gate_entry | equity | V1 | 9.53% | 1.24 | 0.86 | 1.62 | 1.0 | 0.09 | 1.09 | 0.79 | -12.21% |
| steady_drift_plus_gate_half | equity | V1 | 10.15% | 1.28 | 0.85 | 1.67 | 1.0 | 0.11 | 1.01 | 0.7 | -15.07% |
| steady_drift_plus_confirm_notneg | equity | V1 | 12.01% | 1.25 | 0.84 | 1.65 | 1.0 | 0.09 | 0.85 | 0.57 | -21.72% |
| steady_drift_plus_confirm_z | equity | V1 | 11.98% | 1.24 | 0.84 | 1.62 | 1.0 | 0.09 | 0.85 | 0.57 | -21.5% |
| steady_drift_plus_decay60 | equity | V1 | 8.37% | 1.2 | 0.83 | 1.61 | 1.0 | 0.06 | 0.51 | 0.34 | -19.95% |
| steady_drift_calm | equity | V1 | 9.55% | 1.21 | 0.82 | 1.61 | 1.0 | 0.07 | 0.89 | 0.65 | -15.9% |
| steady_drift_plus_orj_weak_gap | equity | V1 | 6.48% | 1.17 | 0.82 | 1.59 | 1.0 | 0.05 | 0.75 | 0.49 | -13.55% |
| steady_drift_plus_decay | equity | V1 | 10.06% | 1.21 | 0.82 | 1.63 | 1.0 | 0.07 | 0.77 | 0.53 | -20.98% |
| steady_drift_plus_confirm_pos | equity | V1 | 11.17% | 1.22 | 0.81 | 1.62 | 1.0 | 0.07 | 0.83 | 0.56 | -21.83% |
| steady_drift_plus_seas_avg | equity | V1 | 6.86% | 1.19 | 0.81 | 1.58 | 1.0 | 0.06 | 0.52 | 0.33 | -17.72% |
| steady_drift_plus_react_agree | equity | V1 | 10.85% | 1.22 | 0.8 | 1.61 | 1.0 | 0.07 | 0.83 | 0.56 | -22.14% |
| steady_drift_plus_seas_and | equity | V1 | 9.56% | 1.17 | 0.8 | 1.56 | 1.0 | 0.05 | 0.72 | 0.49 | -21.61% |
| steady_drift_plus_stop10 | equity | V1 | 10.67% | 1.21 | 0.79 | 1.63 | 1.0 | 0.07 | 0.71 | 0.47 | -22.53% |
| steady_drift_plus | equity | V1 | 12.07% | 1.19 | 0.79 | 1.62 | 1.0 | 0.06 | 0.87 | 0.63 | -22.07% |
| steady_drift_plus_dlret | equity | V1 | 12.07% | 1.19 | 0.79 | 1.62 | 1.0 | 0.06 | 0.87 | 0.63 | -22.07% |
| steady_drift_plus_dlret_half | equity | V1 | 12.07% | 1.19 | 0.79 | 1.62 | 1.0 | 0.06 | 0.87 | 0.63 | -22.07% |
| steady_drift_plus_dlret_only | equity | V1 | 12.07% | 1.19 | 0.79 | 1.62 | 1.0 | 0.06 | 0.87 | 0.63 | -22.07% |
| steady_drift_plus_trend | equity | V1 | 8.93% | 1.2 | 0.79 | 1.63 | 1.0 | 0.06 | 0.94 | 0.7 | -14.42% |
| steady_drift_plus_mom_tilt | equity | V1 | 12.1% | 1.17 | 0.77 | 1.6 | 1.0 | 0.05 | 0.81 | 0.59 | -22.15% |
| steady_drift | equity | V1 | 10.59% | 1.16 | 0.76 | 1.58 | 1.0 | 0.04 | 0.85 | 0.65 | -20.0% |
| steady_drift_plus_mom_randdrop4 | equity | V1 | 8.82% | 1.12 | 0.74 | 1.51 | 1.0 | 0.03 | 0.8 | 0.6 | -21.08% |
| steady_drift_plus_trail | equity | V1 | 8.18% | 1.17 | 0.74 | 1.63 | 1.0 | 0.05 | 0.55 | 0.32 | -16.7% |
| steady_drift_plus_mom_randdrop3 | equity | V1 | 8.7% | 1.11 | 0.74 | 1.52 | 1.0 | 0.03 | 0.79 | 0.59 | -21.47% |
| steady_drift_plus_gate | equity | V1 | 8.12% | 1.16 | 0.74 | 1.59 | 1.0 | 0.04 | 1.03 | 0.82 | -13.07% |
| steady_drift_plus_lag1 | equity | V1 | 11.47% | 1.14 | 0.74 | 1.54 | 1.0 | 0.03 | 0.86 | 0.66 | -21.87% |
| steady_drift_plus_orj_sue_and_orj | equity | V1 | 8.98% | 1.12 | 0.73 | 1.53 | 1.0 | 0.03 | 0.7 | 0.49 | -22.6% |
| steady_drift_plus_mom_randdrop1 | equity | V1 | 8.56% | 1.1 | 0.73 | 1.48 | 1.0 | 0.02 | 0.75 | 0.56 | -22.45% |
| steady_drift_plus_mom_randdrop2 | equity | V1 | 8.62% | 1.1 | 0.72 | 1.49 | 1.0 | 0.02 | 0.71 | 0.51 | -22.5% |
| calm_weighted | equity | V1 | 6.05% | 1.28 | 0.72 | 1.86 | 1.0 | 0.13 | 0.27 | 0.15 | -17.53% |
| steady_drift_plus_lag2 | equity | V1 | 11.19% | 1.11 | 0.71 | 1.51 | 1.0 | 0.03 | 0.86 | 0.68 | -21.87% |
| steady_drift_plus_mom_contrarian | equity | V1 | 6.92% | 1.1 | 0.71 | 1.5 | 1.0 | 0.02 | 0.63 | 0.42 | -20.94% |
| steady_drift_plus_mom_randdrop0 | equity | V1 | 8.36% | 1.08 | 0.71 | 1.51 | 1.0 | 0.02 | 0.73 | 0.54 | -22.7% |
| calm_weighted_matched | equity | V1 | 6.25% | 1.23 | 0.7 | 1.79 | 1.0 | 0.09 | 0.25 | 0.15 | -18.65% |
| steady_drift_plus_contrarian | equity | V1 | 5.81% | 1.07 | 0.69 | 1.5 | 1.0 | 0.02 | 0.62 | 0.43 | -16.57% |
| steady_drift_plus_react_contra | equity | V1 | 5.32% | 1.04 | 0.69 | 1.45 | 1.0 | 0.01 | 0.56 | 0.38 | -14.14% |
| steady_drift_plus_orj_comp | equity | V1 | 9.94% | 1.04 | 0.68 | 1.46 | 1.0 | 0.01 | 0.88 | 0.75 | -24.26% |
| steady_drift_plus_mom_filter | equity | V1 | 9.28% | 1.08 | 0.67 | 1.48 | 1.0 | 0.02 | 0.68 | 0.51 | -21.32% |
| steady_drift_plus_seas_or | equity | V1 | 10.67% | 1.01 | 0.64 | 1.4 | 1.0 | 0.01 | 0.88 | 0.82 | -22.33% |
| steady_drift_plus_seas_s5 | equity | V1 | 5.49% | 1.04 | 0.62 | 1.4 | 1.0 | 0.01 | 0.55 | 0.44 | -16.23% |
| steady_drift_core | equity | V1 | 11.99% | 0.93 | 0.58 | 1.31 | 1.0 | 0.0 | 0.6 | 0.48 | -40.47% |
| calm_weighted_filter | equity | V1 | 5.42% | 1.12 | 0.57 | 1.69 | 1.0 | 0.04 | 0.27 | 0.18 | -17.63% |
| balanced_leverage | equity | V1 | 5.76% | 1.13 | 0.55 | 1.67 | 1.0 | 0.04 | 0.18 | 0.11 | -18.56% |
| steady_drift_plus_seas_s | equity | V1 | 8.47% | 0.92 | 0.52 | 1.28 | 1.0 | 0.0 | 0.87 | 0.93 | -22.22% |
| sector_balanced | equity | V1 | 3.36% | 1.12 | 0.51 | 1.66 | 1.0 | 0.04 | 0.36 | 0.24 | -11.41% |
| market_boost | equity | V1 | 9.15% | 0.9 | 0.51 | 1.26 | 1.0 | 0.0 | 0.51 | 0.45 | -25.08% |
| calm_blend | portfolio | V1 | 6.21% | 1.33 | 0.48 | 2.11 | 1.0 | 0.16 | 0.5 | 0.3 | -20.13% |
| conviction_tilt | equity | V1 | 4.11% | 1.0 | 0.46 | 1.52 | 1.0 | 0.01 | 0.4 | 0.32 | -14.51% |
| steady_drift_plus_orj_only | equity | V1 | 5.99% | 0.89 | 0.46 | 1.29 | 1.0 | 0.0 | 0.91 | 1.02 | -18.19% |
| market_boost_max | equity | V1 | 11.62% | 0.86 | 0.46 | 1.3 | 1.0 | 0.0 | 0.08 | 0.06 | -44.65% |
| steady_drift_plus_ea1904 | equity | V1 | 4.96% | 0.92 | 0.43 | 1.35 | 1.0 | 0.0 | 0.04 | 0.03 | -17.63% |
| steady_drift_plus_bouddat_60s | equity | V1 | 8.39% | 0.8 | 0.41 | 1.19 | 1.0 | 0.0 | 0.71 | 0.86 | -21.51% |
| steady_drift_plus_ea1904_nostop | equity | V1 | 5.76% | 0.83 | 0.38 | 1.33 | 1.0 | 0.0 | 0.08 | 0.06 | -18.0% |
| steady_drift_plus_bouddat | equity | V1 | 8.27% | 0.79 | 0.38 | 1.17 | 1.0 | 0.0 | 0.69 | 0.83 | -22.32% |
| market_exposed_max | equity | V1 | 12.38% | 0.74 | 0.33 | 1.19 | 1.0 | 0.0 | 0.09 | 0.08 | -53.76% |
| size_balanced | equity | V1 | 4.11% | 0.84 | 0.32 | 1.41 | 1.0 | 0.0 | 0.12 | 0.1 | -18.05% |
| volatility_filter | equity | V1 | 4.93% | 0.92 | 0.31 | 1.51 | 1.0 | 0.0 | 0.19 | 0.15 | -18.76% |
| conviction_tilt_max | equity | V1 | 9.94% | 0.79 | 0.3 | 1.29 | 1.0 | 0.0 | -0.11 | -0.09 | -40.51% |
| hedged_blend | portfolio | V1 | 5.25% | 1.13 | 0.25 | 1.86 | 1.0 | 0.04 | 0.38 | 0.26 | -21.79% |
| size_balanced_max | equity | V1 | 9.59% | 0.75 | 0.24 | 1.24 | 1.0 | 0.0 | 0.0 | 0.0 | -43.62% |
| extreme_picks_max | equity | V1 | 9.67% | 0.65 | 0.16 | 1.1 | 1.0 | 0.0 | -0.2 | -0.18 | -56.45% |
| extreme_picks | equity | V1 | 3.56% | 0.67 | 0.15 | 1.23 | 1.0 | 0.0 | -0.22 | -0.18 | -25.17% |
| graded_bets_max | equity | V1 | 9.23% | 0.67 | 0.14 | 1.15 | 1.0 | 0.0 | -0.25 | -0.22 | -48.04% |
| balanced_leverage_max | equity | V1 | 8.96% | 0.71 | 0.14 | 1.23 | 1.0 | 0.0 | -0.36 | -0.3 | -44.9% |
| sector_balanced_max | equity | V1 | 8.96% | 0.71 | 0.14 | 1.23 | 1.0 | 0.0 | -0.36 | -0.3 | -44.9% |
| graded_bets | equity | V1 | 2.53% | 0.64 | 0.03 | 1.22 | 1.0 | 0.0 | -0.28 | -0.25 | -19.49% |
| options_cap20 | options | V1 | 0.32% | 0.17 | -1.12 | 1.53 | 0.74 | 0.0 | 0.29 | 2.4 | -18.87% |
| options_cap30 | options | V1 | -0.11% | -0.02 | -1.22 | 1.39 | 0.47 | 0.0 | 0.06 | -1.23 | -28.97% |
| options_worst_case | options | V1 | -1.19% | -0.55 | -1.8 | 0.89 | 0.02 | 0.0 | -0.37 | 0.59 | -32.33% |

## Thin books (not rankable)

| name | asset_class | V | cagr_active | sharpe | sharpe_lo95 | sharpe_hi95 | psr0 | dsr | holdout_sharpe | holdout_over_search | max_drawdown |
|---|---|---|---|---|---|---|---|---|---|---|---|
| steady_drift_plus_react_flat | equity | thin | 4.01% | 2.27 | 1.81 | 2.81 | 1.0 | 1.0 | 0.86 | 0.27 | -2.81% |

## Reference / legacy runs

| name | asset_class | V | cagr_active | sharpe | sharpe_lo95 | sharpe_hi95 | psr0 | dsr | holdout_sharpe | holdout_over_search | max_drawdown |
|---|---|---|---|---|---|---|---|---|---|---|---|
| options_cap20_legacy | options | ref | 9.82% | 4.05 | 2.88 | 5.32 | 1.0 | 1.0 | 4.7 | 1.24 | -10.28% |
| options_cap30_legacy | options | ref | 14.06% | 3.73 | 2.49 | 5.03 | 1.0 | 1.0 | 4.37 | 1.26 | -17.39% |
| legacy_blend | portfolio | ref | 15.08% | 3.3 | 2.47 | 4.2 | 1.0 | 1.0 | 2.57 | 0.7 | -9.91% |
| options_cap40_legacy | options | ref | 18.15% | 3.51 | 2.22 | 4.85 | 1.0 | 1.0 | 4.19 | 1.29 | -23.86% |
| calm_blend_prefix_fix | portfolio | ref | 13.15% | 2.57 | 1.71 | 3.44 | 1.0 | 1.0 | 2.11 | 0.76 | -15.5% |
| hedged_blend_prefix_fix | portfolio | ref | 11.98% | 2.31 | 1.43 | 3.22 | 1.0 | 1.0 | 1.95 | 0.78 | -16.68% |
| calm_rank_tilt_legacy | equity | ref | 7.52% | 1.86 | 1.4 | 2.31 | 1.0 | 0.9 | 0.66 | 0.27 | -11.59% |
| calm_rank_levered_legacy | equity | ref | 9.75% | 1.78 | 1.31 | 2.27 | 1.0 | 0.83 | 0.39 | 0.16 | -15.93% |
| calm_weighted_legacy | equity | ref | 7.09% | 1.51 | 0.99 | 2.02 | 1.0 | 0.43 | 0.52 | 0.26 | -15.58% |
| calm_weighted_matched_legacy | equity | ref | 7.72% | 1.49 | 0.97 | 2.0 | 1.0 | 0.4 | 0.45 | 0.22 | -17.01% |
| calm_weighted_filter_legacy | equity | ref | 6.91% | 1.45 | 0.94 | 1.97 | 1.0 | 0.34 | 0.53 | 0.28 | -15.65% |
| sector_balanced_legacy | equity | ref | 4.29% | 1.37 | 0.83 | 1.95 | 1.0 | 0.24 | 0.58 | 0.33 | -10.83% |
| balanced_leverage_legacy | equity | ref | 6.86% | 1.33 | 0.8 | 1.82 | 1.0 | 0.18 | 0.4 | 0.22 | -16.63% |
| options_cap20_midfill | options | ref | 5.06% | 1.97 | 0.74 | 3.32 | 1.0 | 0.95 | 2.67 | 1.57 | -14.45% |
| market_exposed_legacy | equity | ref | 7.71% | 1.14 | 0.7 | 1.58 | 1.0 | 0.04 | 0.33 | 0.2 | -15.54% |
| volatility_filter_legacy | equity | ref | 6.62% | 1.26 | 0.69 | 1.8 | 1.0 | 0.11 | 0.42 | 0.25 | -16.68% |
| market_boost_max_legacy | equity | ref | 15.01% | 1.09 | 0.67 | 1.51 | 1.0 | 0.02 | 0.31 | 0.21 | -36.98% |
| conviction_tilt_legacy | equity | ref | 5.54% | 1.21 | 0.66 | 1.74 | 1.0 | 0.08 | 0.55 | 0.36 | -13.07% |
| options_cap20_prefix_fix | options | ref | 4.71% | 1.89 | 0.65 | 3.24 | 1.0 | 0.91 | 2.68 | 1.72 | -13.83% |
| steady_drift_plus_breadth_only | equity | ref | 9.27% | 1.02 | 0.62 | 1.42 | 1.0 | 0.01 | 0.77 | 0.65 | -24.43% |
| market_exposed_max_legacy | equity | ref | 12.46% | 1.03 | 0.61 | 1.53 | 1.0 | 0.01 | 0.19 | 0.12 | -32.97% |
| market_boost_legacy | equity | ref | 10.08% | 0.97 | 0.59 | 1.32 | 1.0 | 0.0 | 0.58 | 0.47 | -23.23% |
| options_cap30_midfill | options | ref | 6.97% | 1.73 | 0.49 | 3.07 | 1.0 | 0.75 | 2.36 | 1.6 | -22.54% |
| extreme_picks_legacy | equity | ref | 5.19% | 0.95 | 0.48 | 1.42 | 1.0 | 0.01 | 0.14 | 0.1 | -18.48% |
| graded_bets_legacy | equity | ref | 4.1% | 1.03 | 0.47 | 1.65 | 1.0 | 0.01 | 0.22 | 0.16 | -13.96% |
| balanced_leverage_max_legacy | equity | ref | 12.85% | 1.03 | 0.46 | 1.61 | 1.0 | 0.01 | -0.07 | -0.05 | -36.46% |
| sector_balanced_max_legacy | equity | ref | 12.85% | 1.03 | 0.46 | 1.61 | 1.0 | 0.01 | -0.07 | -0.05 | -36.46% |
| size_balanced_legacy | equity | ref | 5.01% | 0.93 | 0.43 | 1.41 | 1.0 | 0.0 | 0.24 | 0.18 | -17.53% |
| conviction_tilt_max_legacy | equity | ref | 11.31% | 0.91 | 0.42 | 1.49 | 1.0 | 0.0 | 0.02 | 0.01 | -40.0% |
| graded_bets_max_legacy | equity | ref | 11.46% | 0.92 | 0.4 | 1.45 | 1.0 | 0.0 | 0.01 | 0.01 | -38.76% |
| extreme_picks_max_legacy | equity | ref | 10.09% | 0.83 | 0.39 | 1.29 | 1.0 | 0.0 | -0.1 | -0.08 | -36.2% |
| options_cap30_prefix_fix | options | ref | 6.5% | 1.65 | 0.39 | 3.04 | 1.0 | 0.65 | 2.37 | 1.75 | -22.29% |
| market_exposed | equity | ref | 7.12% | 0.75 | 0.3 | 1.18 | 1.0 | 0.0 | 0.06 | 0.05 | -34.14% |
| size_balanced_max_legacy | equity | ref | 8.94% | 0.74 | 0.27 | 1.22 | 1.0 | 0.0 | -0.06 | -0.05 | -37.45% |
| options_worst_case_prefix_fix | options | ref | 2.66% | 1.05 | -0.22 | 2.52 | 1.0 | 0.03 | 1.89 | 2.67 | -17.08% |
