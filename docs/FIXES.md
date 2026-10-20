# Methodology fixes (2026-10): what was wrong, what changed, what it cost

## Bottom line

**The previously published headline numbers were overstated by four methodology defects.** Corrected, the equity books earn
roughly 1 to 4 points less per year at a lower Sharpe, the options book no longer has a measurable edge once it is filled at the
quoted bid/ask, and the hedged equity+options blend falls from 11.3% / Sharpe 2.25 to 4.9% / Sharpe 1.09. The reference equity book
`balanced_leverage` went from 6.49% / Sharpe 1.30 to 5.38% / 1.09; `options_cap20` from 4.12% / 1.77 (itself already corrected
from the originally reported 8.58% / 3.78) to 0.28% / 0.16.

These corrected numbers are still **partly in-sample**: the strategy constants (conviction_tilt trim/tilt, the `_max` size multipliers, the
calm_weighted unit, blend weights) were chosen on the full sample before the fixes and were deliberately NOT re-selected (decision D6).
Re-selecting them on a search window is an open research task and would most likely lower the figures further.

## The four fixes

| # | Defect | Where it lived | Fix | Switch back to the old behaviour |
|---|---|---|---|---|
| 1 | Leverage cap counted only each quarter's NEW positions, not exposure still open from earlier quarters, so realized gross/NAV ran above the stated cap | `pead/engines/equity.py` (`cap_notionals`, `cap_open_book`, `Sizing.exposure_cap`) | `exposure_cap="open_book"` (default): a quarter's new positions only use the room left under the cap on every day they will be held | Strategy `<name>_legacy` (all 25 equity strategies, `exposure_cap="entry_cohort"`, status reference) |
| 2 | SUE percentile was ranked within the same announcement quarter, i.e. against firms that had not reported yet (look-ahead in every decile) | `pead/common/pit_rank.py`, used by `prep/equity/build_deciles.py`, `prep/options/link_events_secid.py`, `prep/options/return_distributions.py` | Rank against the expanding pool of strictly earlier quarters; the first quarter (2,090 events) is dropped | `PEAD_DECILE_RANKING=within_quarter` and rebuild into a separate `PEAD_DATA_DIR` (reproduces the stored `sue_rank_pct` exactly) |
| 3 | Options were bought and sold at the mid with a flat 3% cost | `pead/engines/options.py` (`OptionsSizing.fill_mode`), `prep/options/forward_prices_optimal.py` (now also fetches the horizon-0 bid/ask) | `fill_mode="bid_ask"` (default): ask in, bid out at the final checkpoint, zero bid via the dead-quote mark, no extra flat cost | `options_cap20_midfill`, `options_cap30_midfill` (mid fills, 3% + $0.05 floor), `options_cap20_legacy` (original convention) |
| 4 | Holding periods (decay days) were fitted on the full sample | `pead/prep/equity/daily_decay.py` | Decay days estimated on the first 70% of announcement quarters (49 of 71) and applied to every event | `PEAD_DECAY_SEARCH_FRAC=1.0` |

Also: `python -m pead prep ... --device auto` used to pass `auto` to scripts that accept only cpu|cuda; it now resolves to cuda only when cupy and a CUDA device exist, else cpu.

Design caveat for fix 2: a pooled expanding rank cannot absorb market-wide surprise regimes (in a boom quarter most events rank high against the older
pool), so decile sizes are uneven per quarter. Trailing-window and same-day-or-earlier in-quarter rankings were not evaluated.

## Measured effect: equity, one fix at a time

Ladder (annualized return). *Published* = committed data with the original cap. *+fix 1* = open-book cap on the published data. *+fix 2* = also point-in-time deciles
(decay days still fitted on the full sample, `PEAD_DECAY_SEARCH_FRAC=1.0`). *+fix 4 (all)* = also search-window decay days = the current default.
Published numbers are reproduced exactly by the `_legacy` twins (final NAV difference 0.0 for all 22 strategies). Alpha is vs the CRSP market (`scripts/alpha_benchmark.py` method).
"Max gross/NAV" is gross exposure divided by the NAV the quarter was SIZED on.

| Strategy | Published | +fix 1 (cap) | +fix 2 (deciles) | +fix 4 (all) | Sharpe pub -> all | MaxDD pub -> all | Alpha pub -> all | Max gross/NAV (sizing NAV) pub -> all | cap |
|---|---|---|---|---|---|---|---|---|---|
| `balanced_leverage` | 6.5% | 6.4% | 5.1% | 5.4% | 1.30 -> 1.09 | -16.6% -> -18.6% | 6.7% -> 5.8% | 2.87x -> 2.50x | 2.5x |
| `balanced_leverage_max` | 12.1% | 10.3% | 7.6% | 8.3% | 1.00 -> 0.69 | -36.5% -> -44.9% | 13.0% -> 9.6% | 7.48x -> 6.00x | 6.0x |
| `calm_rank_levered` | 9.2% | 9.2% | 7.6% | 8.1% | 1.73 -> 1.39 | -15.9% -> -19.6% | 9.5% -> 8.8% | 3.77x -> 4.00x | 4.0x |
| `calm_rank_tilt` | 7.1% | 7.1% | 5.8% | 6.0% | 1.81 -> 1.47 | -11.6% -> -13.6% | 7.3% -> 6.5% | 2.68x -> 2.50x | 2.5x |
| `calm_weighted` | 6.7% | 6.6% | 5.4% | 5.6% | 1.47 -> 1.24 | -15.6% -> -17.5% | 6.9% -> 6.1% | 2.83x -> 2.50x | 2.5x |
| `calm_weighted_filter` | 6.5% | 6.4% | 5.1% | 5.1% | 1.41 -> 1.08 | -15.7% -> -17.6% | 6.7% -> 5.4% | 2.83x -> 2.50x | 2.5x |
| `calm_weighted_matched` | 7.3% | 6.7% | 5.4% | 5.8% | 1.45 -> 1.18 | -17.0% -> -18.7% | 7.6% -> 6.3% | 3.15x -> 2.50x | 2.5x |
| `conviction_tilt` | 5.2% | 4.7% | 3.7% | 3.8% | 1.18 -> 0.97 | -13.1% -> -14.5% | 5.4% -> 4.0% | 2.05x -> 1.50x | 1.5x |
| `conviction_tilt_max` | 10.7% | 10.7% | 8.7% | 9.3% | 0.89 -> 0.76 | -40.0% -> -40.5% | 11.3% -> 10.0% | 5.68x -> 6.00x | 6.0x |
| `extreme_picks` | 4.9% | 4.3% | 2.8% | 3.3% | 0.93 -> 0.65 | -18.5% -> -25.2% | 5.2% -> 3.7% | 2.13x -> 1.50x | 1.5x |
| `extreme_picks_max` | 9.5% | 9.5% | 8.2% | 9.0% | 0.80 -> 0.63 | -36.2% -> -56.4% | 9.9% -> 10.0% | 4.66x -> 6.00x | 6.0x |
| `graded_bets` | 3.9% | 3.3% | 2.0% | 2.4% | 1.01 -> 0.62 | -14.0% -> -19.5% | 4.1% -> 2.6% | 2.00x -> 1.50x | 1.5x |
| `graded_bets_max` | 10.8% | 10.4% | 7.2% | 8.6% | 0.89 -> 0.64 | -38.8% -> -48.0% | 11.5% -> 9.8% | 7.39x -> 6.00x | 6.0x |
| `market_boost` | 10.1% | 10.0% | 9.0% | 9.2% | 0.97 -> 0.90 | -23.2% -> -25.1% | 4.4% -> 3.6% | 2.87x -> 2.50x | 2.5x |
| `market_boost_max` | 15.0% | 13.5% | 11.0% | 11.6% | 1.09 -> 0.86 | -37.0% -> -44.6% | 9.5% -> 6.4% | 7.48x -> 6.00x | 6.0x |
| `market_exposed` | 7.3% | 7.2% | 5.6% | 6.6% | 1.11 -> 0.72 | -15.5% -> -34.1% | 5.8% -> 5.2% | 2.80x -> 2.50x | 2.5x |
| `market_exposed_max` | 11.8% | 11.8% | 10.0% | 11.5% | 1.00 -> 0.72 | -33.0% -> -53.8% | 8.3% -> 8.0% | 4.44x -> 4.67x | 6.0x |
| `sector_balanced` | 4.1% | 4.0% | 3.1% | 3.1% | 1.33 -> 1.08 | -10.8% -> -11.4% | 4.2% -> 3.4% | 1.59x -> 1.50x | 1.5x |
| `sector_balanced_max` | 12.1% | 10.3% | 7.6% | 8.3% | 1.00 -> 0.69 | -36.5% -> -44.9% | 13.0% -> 9.6% | 7.48x -> 6.00x | 6.0x |
| `size_balanced` | 4.7% | 4.3% | 3.3% | 3.8% | 0.91 -> 0.81 | -17.5% -> -18.1% | 5.0% -> 4.1% | 1.88x -> 1.50x | 1.5x |
| `size_balanced_max` | 8.4% | 8.4% | 8.1% | 8.9% | 0.72 -> 0.72 | -37.5% -> -43.6% | 9.3% -> 9.8% | 4.08x -> 4.71x | 6.0x |
| `volatility_filter` | 6.3% | 6.1% | 4.7% | 4.6% | 1.23 -> 0.89 | -16.7% -> -18.8% | 6.4% -> 4.9% | 2.88x -> 2.50x | 2.5x |

Reading it:
* **Fix 2 (point-in-time deciles) is the biggest equity cost** (about 1 to 2 points on the base books): a good part of the published edge was look-ahead.
* **Fix 1 matters mainly for the 6x `_max` books** (balanced_leverage_max 12.1% -> 10.3%) and barely for the 2.5x books. Surprising direction: **fix 4 slightly RAISES returns** (balanced_leverage 5.1% -> 5.4%, market_exposed Sharpe 0.64 -> 0.73); search-window decay days were not worse. Treat as noise-level, not as evidence the fix is "good".
* The cap now holds at sizing time (max gross over the sizing quarter's NAV equals the cap exactly: 2.50x, 6.00x, 1.50x). Gross over CURRENT NAV can still exceed the cap (balanced_leverage_max peak 7.6x vs 6x, 3.7% of days over versus 11.4% before) because NAV falls in drawdowns while positions are fixed in dollars.
* Several books now breach the screens that picked their sizing: `balanced_leverage_max` drawdown -44.9% (the sweep required -40% or better) and `extreme_picks_max` -56.4%.

## Measured effect: options

| Book (return / Sharpe) | Published | Fix 3 only (real fills, same contracts) | Fix 2 only (mid fills, new deciles) | All fixes (default) |
|---|---|---|---|---|
| `options_cap20` | 4.12% / 1.77 | **0.16% / 0.10** | 4.43% / 1.85 | **0.28% / 0.16** |
| `options_cap30` | 5.69% / 1.55 | -0.27% / -0.08 | 6.10% / 1.62 | -0.10% / -0.02 |
| `options_worst_case` | 2.34% / 0.99 | -1.11% / -0.57 | n/a | -1.04% / -0.52 |
| `options_cap20_legacy` (original convention) | 8.58% / 3.78 | n/a | 9.17% / 3.93 | n/a |

* **Fix 3 removes essentially the whole options edge**: paying the actual ask and receiving the actual bid costs about 4 points a year against the mid-plus-3% convention.
* **Surprising: the point-in-time deciles slightly RAISE the options result** (4.12% -> 4.43% mid-fill), the opposite of equity. This is a within-noise difference, not evidence that the fix helps.
* The fetched day-0 mid matches `kelly_premium` for 100.0% of 113,927 picks (consistency check in `forward_prices_optimal`).
* Blends: `hedged_blend` 11.3% / 2.25 -> 4.9% / 1.09 (the 0.8x / 1.6x weights came from the overstated sleeve and were not re-optimised); `calm_blend` 12.4% / 2.50 -> 5.8% / 1.28. With a near-zero options sleeve the blend is essentially its equity sleeve plus extra volatility.

## Where the old numbers are kept (decision D7)

* Equity: `results/<name>_legacy/` (the `_legacy` twin run on the committed data; final NAV identical to the published one). The old report files were copied in.
* Options and blends: `results/options_cap20_prefix_fix`, `options_cap30_prefix_fix`, `options_worst_case_prefix_fix`, `hedged_blend_prefix_fix`, `calm_blend_prefix_fix` are verbatim copies of the pre-fix result folders. `options_cap20_legacy`, `options_cap30_legacy`, `options_cap40_legacy`, `legacy_blend` are unchanged.
* `results/portfolio/` was regenerated on the new data, so its `*_legacy` combinations use `options_cap20_legacy` re-run on the new deciles (9.17% / 3.93), not the published 8.58% / 3.78.
* The tests pin the legacy numbers on the committed data (`tests/test_framework.py`, `tests/test_fixes.py`). The committed `data/` is the PRE-fix vintage; the corrected data vintage was built in an isolated directory (`_pead_consolidation/vintage_fixed`) and is not committed (gitignored parquet). To rebuild it run `python -m pead prep vol_features --with-deps --force` and the options chain (`link_events_secid return_distributions scan_full_chain attach_underlying_price event_vol_features optimal_contract_selector forward_prices_optimal`, `--force`) with `PEAD_DATA_DIR` / `PEAD_METADATA_DIR` pointed at a new folder. The contract selector ran on CPU in 93 s.
* With the corrected default, `options_cap20` etc. cannot run against the old committed data (it has no horizon-0 quotes); they raise a clear error.

## Not fixed / residual issues

In-sample choices still baked in (not re-selected, D6): `conviction_tilt` trim 0.15 / tilt 1.0 / 0.002, the `_max` base-unit multipliers and the 6x ceiling (picked by a full-sample sweep with a Sharpe/drawdown screen), the `calm_weighted` base unit, the `calm_rank_tilt` exponent, the blend weights, and the vol-percentile thresholds. Sweep results quoted in `docs/STRATEGIES.md` section 3 are pre-fix and were not re-run.

Residual look-ahead / leakage that remains:
* Fix 4 only moves the decay-day estimation; the fine/medium/coarse back-off thresholds (MIN_QUARTERS/MIN_EVENTS) and the leave-one-out risk benchmark still see the full sample.
* `weights.neutralized_tilt` normalises by quarter-wide group totals that include later reporters (sizing look-ahead, magnitude unverified).
* About 28% of positions overlap the same stock's next event (holding up to 200 days); 105 duplicate (permno, day0) rows exist in `positions_rankweighted_v2`; the per-name liquidity cap is not aggregated across overlapping positions.
* The Kelly selector still scores contracts at the MID but the book pays the ask (optimizer's curse; "Tier B" re-scoring at the ask is a documented follow-up, not done). Return distributions rest on an 8-quarter warm-up.
* Survivorship / delisting handling: unknown. Short-borrow, financing and market impact are not modelled.
* Fix 2 uses an expanding pool, so the first quarter is lost and decile sizes are uneven.
* Provenance: the committed `optimal_contracts.parquet` carries a bulk-copy timestamp, so whether it was built on the CPU or GPU path cannot be verified; the CPU run here saw 113,927 priceable events, the same count as the committed file.

## Reproducing this table

Isolated vintages under `C:\Users\hrudi\Documents\BEI\_pead_consolidation\`: `vintage_old` (committed data, open-book cap), `vintage_fix2only` (`PEAD_DECAY_SEARCH_FRAC=1.0`), `vintage_fix3only` (fix 3 on the committed contracts), `vintage_fixed` (all fixes). Ladder CSVs `equity_ladder.csv` and `options_ladder.csv` sit in the same folder.
