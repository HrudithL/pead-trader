# Leaderboard

Best so far (validated + research strategies):

- **equity** -- best sharpe: `s10_vol_rank_tilt` (1.81); best annualized_return: `s6_beta_overlay_aggressive` (0.15); best calmar: `s10_vol_rank_tilt` (0.614)
- **options** -- best sharpe: `opt_kelly_cap20` (1.77); best annualized_return: `opt_kelly_cap30` (0.0569); best calmar: `opt_kelly_cap20` (0.298)
- **portfolio** -- best sharpe: `hedged_blend_s10` (2.5); best annualized_return: `hedged_blend_s10` (0.124); best calmar: `hedged_blend_s10` (0.802)

| name | asset_class | status | annualized_return | annualized_vol | sharpe | max_drawdown | calmar |
|---|---|---|---|---|---|---|---|
| s10_vol_rank_tilt | equity | research | 7.12% | 3.83% | 1.81 | -11.59% | 0.61 |
| s10_vol_rank_tilt_levered | equity | research | 9.23% | 5.17% | 1.73 | -15.93% | 0.58 |
| s9b_inverse_vol | equity | validated | 6.71% | 4.49% | 1.47 | -15.58% | 0.43 |
| s9b_inverse_vol_matched | equity | validated | 7.31% | 4.94% | 1.45 | -17.01% | 0.43 |
| s9c_inverse_vol_filtered | equity | research | 6.54% | 4.55% | 1.41 | -15.65% | 0.42 |
| s5_sector_neutral | equity | validated | 4.06% | 3.02% | 1.33 | -10.83% | 0.37 |
| s6_neutral_levered | equity | validated | 6.49% | 4.94% | 1.30 | -16.63% | 0.39 |
| s9a_vol_cell_filter | equity | research | 6.27% | 5.05% | 1.23 | -16.68% | 0.38 |
| s4_tilted_size_neutral | equity | validated | 5.25% | 4.41% | 1.18 | -13.07% | 0.40 |
| s7_directional | equity | reference | 7.30% | 6.56% | 1.11 | -15.54% | 0.47 |
| s6_beta_overlay_aggressive | equity | research | 15.01% | 13.70% | 1.09 | -36.98% | 0.41 |
| rank_weighted | equity | validated | 3.88% | 3.86% | 1.01 | -13.96% | 0.28 |
| s5_sector_neutral_aggressive | equity | research | 12.15% | 12.20% | 1.00 | -36.46% | 0.33 |
| s6_neutral_levered_aggressive | equity | research | 12.15% | 12.20% | 1.00 | -36.46% | 0.33 |
| s7_directional_aggressive | equity | research | 11.78% | 11.85% | 1.00 | -32.97% | 0.36 |
| s6_beta_overlay | equity | validated | 10.08% | 10.51% | 0.97 | -23.23% | 0.43 |
| extreme_decile | equity | validated | 4.91% | 5.32% | 0.93 | -18.48% | 0.27 |
| size_balanced | equity | validated | 4.74% | 5.26% | 0.91 | -17.53% | 0.27 |
| rank_weighted_aggressive | equity | research | 10.84% | 12.40% | 0.89 | -38.76% | 0.28 |
| s4_tilted_size_neutral_aggressive | equity | research | 10.70% | 12.25% | 0.89 | -40.00% | 0.27 |
| extreme_decile_aggressive | equity | research | 9.53% | 12.24% | 0.80 | -36.20% | 0.26 |
| size_balanced_aggressive | equity | research | 8.45% | 12.26% | 0.72 | -37.45% | 0.23 |
| opt_kelly_cap20_legacy | options | reference | 8.58% | 2.18% | 3.78 | -10.28% | 0.83 |
| opt_kelly_cap30_legacy | options | reference | 12.26% | 3.33% | 3.49 | -17.39% | 0.70 |
| opt_kelly_cap40_legacy | options | reference | 15.79% | 4.50% | 3.28 | -23.86% | 0.66 |
| opt_kelly_cap20 | options | validated | 4.12% | 2.30% | 1.77 | -13.83% | 0.30 |
| opt_kelly_cap30 | options | validated | 5.69% | 3.61% | 1.55 | -22.29% | 0.26 |
| opt_kelly_cap20_conservative | options | research | 2.34% | 2.37% | 0.99 | -17.08% | 0.14 |
| hedged_blend_legacy_options | portfolio | reference | 14.25% | 4.18% | 3.21 | -9.91% | 1.44 |
| hedged_blend_s10 | portfolio | research | 12.43% | 4.73% | 2.50 | -15.50% | 0.80 |
| hedged_blend | portfolio | validated | 11.33% | 4.83% | 2.25 | -16.68% | 0.68 |
