# PEAD options: backtest findings and decision record (2026-10-22)

## Decision

The largest historical funded-wealth point estimate in the corrected 54-cell run is a long call after a top-SUE event, targeting 120 calendar days to expiry and 0.50 absolute delta, then exiting after 60 subsequent shared CRSP/OptionMetrics sessions. With $10m starting capital it ended $4.865m above the published French daily-RF cash proxy, at 4.21% CAGR versus 2.34% for that proxy. C/120/0.30/60 had nearly the same excess Sharpe (0.213 versus 0.215) and a smaller drawdown, at the cost of lower CAGR. The cash series is rounded and is not an independently reconstructed, investable Treasury-bill account.

The largest observed Sharpe is 0.593 for C/75/0.30/60, but that cell funded only 246 contracts and 89 had a missing or zero exit bid. Its result is too sparse and tail-dependent to promote. The 54-cell familywise year-block test gives p=0.130 for the best observed Sharpe; the settlement annual max-t test gives p=0.110. Across 54 unadjusted HAC alpha intervals, 19 are entirely below zero and none is entirely above zero. Therefore the archive does **not** establish positive PEAD alpha, a globally best rule, or consistently profitable future trades. Keep 120/50/60 as a research benchmark with zero live allocation.

## What was already done

The underlying PEAD work has analyst earnings-surprise events, prior-quarter SUE ranks and CRSP returns. Existing equity studies reported about 5.6% annual return / 1.24 Sharpe for calm_weighted and 6.0% / 1.47 for calm_rank_tilt. Those are not directly comparable to the options account, but they set a useful benchmark.

The options line developed through meaningfully different experiments:

1. A payoff prototype selected options from hand-entered three-state probabilities; it was illustrative, not calibrated evidence.
2. Decile-sorted midpoint results showed a directional difference in quoted marks, not an executable ask-to-bid result.
3. A near-120-DTE/near-50-delta rule and Kelly selector used future stock outcomes. The selector's 60-session labels were only quarter-lagged: 179,874 of 180,633 outcomes (99.6%) matured on or after the next quarter began. Those labels were not available when the subsequent-quarter decision was made.
4. A prior GPU stop/target roadmap had no real all-contract daily option path; its real-data optimizer branch was unimplemented and its saved run used 200 synthetic paths.
5. A fixed 54-cell grid avoided Kelly labels. It compared calls after top-SUE events and puts after bottom-SUE events at 75/120/180 DTE, 0.30/0.50/0.70 absolute delta and 20/40/60-session exits. The archive's top net-P&L cell was C/120/0.50/60. But its allocator was order-dependent for same-day fills and capped OI per entry rather than across all open lots in that option.
6. This continuation reran the settlement grid and all 54 cells through daily bid marks, a published daily French-RF cash proxy, common funding limits, HAC factor alpha and a joint year-block Sharpe test. It also ran the 120/50/60 benchmark's cost, capacity, contract-flag, delayed-entry and same-contract hold sensitivities. Licensed option quotes were streamed read-only from an external archive and retained in memory only; `contract_level_quotes_persisted=false`.

One provisional daily pass used the simulator's default 60-session risk scaler for 20- and 40-session cells. I found the mismatch while checking the leading 40-session cohort, corrected the calls to use each rule's actual hold horizon, added a regression test, and reran the full grid. A later source-collision audit also found that duplicate removal had been applied after selecting only extreme SUE deciles. That pass was stopped after 13 of 54 daily cells. The final run first excludes every row in repeated `(secid, day0_date)` clusters across **all** source deciles, then selects eligible deciles and reruns the full 54-cell grid. Use only the final full-source-cleaned artifacts identified below.

The three independent reviews made competing cases. The mechanism reviewer favored a liquid top-SUE call benchmark but warned that spread, theta and post-event IV contraction can consume the drift; moderate OTM/low-volatility calls were a challenger. The auditor emphasized label maturity, announcement timing, static security links and adjusted deliverables. The skeptic emphasized tail concentration, same-day capacity and the absence of strong annual evidence. The synthesis is to retain C/120/0.50/60 as a fixed wealth benchmark, C/120/0.30/60 as a lower-drawdown challenger, and C/75/0.30/60 only as a sparse Sharpe observation. See PEAD_OPTIONS_RECONSTRUCTION_2026-10-22.md and PEAD_OPTIONS_STRATEGY_SPEC_2026-10-22.md.

## Frozen candidate rule

Use analyst SUE deciles 9 and 10 for calls; deciles 1 and 2 are retained as a put comparison, not a recommended long-put signal. Select entry contracts using only entry-date fields: positive bid, ask at least $0.10, ask not below bid, spread/ask no more than 30%, OI at least 100, DTE within 25 calendar days of target, absolute delta within 0.10, and at least 10 calendar days left at planned exit. Rank candidates by DTE error, delta error, fractional spread and option ID. Entry is at ask; open positions are marked at bid and exited at bid.

The observed funded-call median was 122 DTE, 0.502 delta, strike/spot 1.034 and spread/ask 8.16%; the selected contract was therefore typically about 3.4% OTM, not literally an ATM strike. The 10th–90th percentiles were DTE 106–142, delta 0.431–0.571 and strike/spot 0.994–1.125.

The daily simulation starts with $10m, caps outstanding paid premium at 20% of prior-close NAV, budgets 0.25% NAV per signal before a trailing signal-count scaler, caps aggregate open lots in one option ID at 10% of observed entry OI, and caps one issuer at 2% NAV. Same-day desired orders are scaled pro rata and rounded down; same-close exits do not fund entries. No stops or targets are included: the prior stop optimizer had no real path data.

## Archived pre-correction 54-cell findings

Important: results/options_expiry_grid_portfolio is the original archived grid, created before the corrected CRSP/OptionMetrics calendar intersection. It is useful exploratory evidence, but it is not a corrected full-grid rerun.

| Archived finding | Result |
|---|---|
| Calls vs puts, 27 cells per direction | Mean cell ROI +3.71% for calls vs −11.27% for puts; 59.3% of call cells and 7.4% of put cells positive |
| Call DTE groups | 120-DTE had +$8.24m aggregate P&L across nine cells; 75-DTE had −$8.73m despite higher mean ROI; 180-DTE had +$0.75m |
| Call hold groups | All nine 20-session cells lost; 7/9 40-session and 9/9 60-session cells were positive |
| Thin ROI winner | C/75/0.30/60 earned 63.8% ROI on only 261 funded trades; 94 had a quoted-zero exit bid |
| Same-event delta contrast | 0.50 delta beat 0.70 by 2.56 mean ROI points, but its median advantage was −9.13 points and it won only 26.2% of event pairs |

The archived C/120/0.50/60 settlement book funded 6,029 trades, paid $74.302m premium and earned $4.179m, a 5.624% premium ROI and 2.257% fixed-window settlement CAGR. It had 775 funded contracts with quoted-zero exit bids and 53 with missing exit quotes, both assigned zero proceeds. P&L was positive in 10/15 observed option years; 2013 supplied 68.9% of total and 2008–12 lost $2.370m (2011 has no usable options). The cell's annual P&L t-statistic was 0.67, 95% interval −$613k to +$1.170m. The largest t-statistic across the 54 archived cells was 2.591; the 10,000-draw centered joint-year max-t familywise p-value was 0.165 (95th percentile null maximum 3.719). This is not familywise-significant evidence.

The archived aggregates show why the maximand matters. 75-DTE/0.30 produced the highest cell ROI, while 120-DTE/0.50/60 produced the highest net P&L among tested cells. Delta rankings also change between cell-level totals and same-event pairs. Thin-cell ROI should not be confused with scalable wealth.

## Final corrected 54-cell daily grid

The final event input had 147,142 rows. Full-source collision cleaning retained 147,115 and excluded all 27 rows in 13 repeated security/date clusters (14 were extra rows beyond one per cluster); 15 of the removed rows were eligible extreme-decile signals. Eleven clusters had multiple SUE deciles, and only one cluster had both eligible call and put directions. The eligible signal count changed from 65,527 to 65,512. See `event_collision_audit.json`.

All 54 cells use the shared CRSP/OptionMetrics trading calendar, ask entry, daily bid marks and bid exits, $10m starting capital, the rounded daily French RF cash proxy, a 20%-of-prior-NAV outstanding-premium cap, 0.25%-NAV per-signal budget with a hold-specific trailing scaler, 10%-of-OI aggregate open-lot cap per option ID, and 2%-of-NAV issuer cap. Same-day fills are pro-rata. Missing intermediate bids are zero in the base; missing exits return zero. The study window is 1998-01-02–2013-12-31, including cash-only 2011. The 2013 option source is partial. Eight OptionMetrics-only dates and 336 CRSP-only dates are outside the shared calendar. No funded position days fall on non-option sessions in any of the 54 cells.

| Cell / statistic | Funded trades | CAGR | Excess-RF Sharpe | Max drawdown | Four-factor alpha (HAC t) | Increment over cash |
|---|---:|---:|---:|---:|---:|---:|
| **C/120/0.50/60** (highest funded wealth) | 6,023 | **4.21%** | 0.215 | −33.19% | −1.78% (−0.89) | **+$4.865m** |
| C/75/0.70/40 (lower-tenor/lower-drawdown challenger) | 5,268 | 3.97% | 0.204 | −25.58% | −1.26% (−0.72) | +$4.156m |
| C/120/0.30/60 (similar Sharpe, smaller drawdown) | 4,788 | 3.81% | 0.213 | −24.52% | −0.85% (−0.51) | +$3.700m |
| C/75/0.30/60 (highest observed Sharpe; sparse) | 246 | 3.41% | **0.593** | −2.84% | +0.83% (1.85) | +$2.609m |
| All-cash published French daily-RF proxy | — | 2.34% | — | — | — | — |

The 120/50/60 benchmark paid $85.265m cumulative premium, received $90.173m in exits, earned $4.434m cash interest and finished at $19.342m, versus $14.477m for the rounded French RF proxy. Its actual funded contracts had median 122 DTE, 0.502 delta, strike/spot 1.034 (about 3.4% OTM), and 8.16% median spread/ask. About 35.9% of funded trades were profitable; median trade ROI was −47.6%. The ten largest trades contributed $2.555m. Option P&L before cash interest was +$4.908m. The portfolio lost $3.664m in 2008 and $1.682m in 2002; 2013 option P&L was +$3.506m and 2008–12 option P&L was −$2.671m.

The nearby alternatives change the trade distribution substantially. C/120/0.30/60 bought contracts with median delta 0.309 and strike/spot 1.151 (about 15% OTM); it finished $3.700m above cash, but only 29.1% of trades won, median ROI was −77.8%, and its ten largest trades added $3.451m. The ITM C/75/0.70/40 alternative had median delta 0.699 and strike/spot 0.930 (about 7% ITM); it made $4.156m over cash with 42.4% winners, median ROI −15.9%, and a shallower −25.58% account drawdown. The high-Sharpe C/75/0.30/60 outcome is still more tail-concentrated: 246 trades, 32.9% winners, median ROI −88.2%, and $2.820m from the largest ten trades.

Sixteen of 27 call cells finished ahead of cash, versus one of 27 put cells. None of the nine 20-session call cells beat cash; seven of nine 40-session cells and all nine 60-session cells did. All 20- and 40-session put cells lost against cash; only one of nine 60-session put cells edged cash. These are descriptive overlapping cells, not independent experiments. The 120-DTE group had the strongest average call CAGR, but the optimum changes with objective: 75/30/60 has the highest raw Sharpe, while 120/50/60 has the most ending wealth.

The lone put cell above cash was P/75/0.30/60: it added only about $88k, with 0.040 Sharpe, 202 funded contracts, and −89.7% median trade ROI. P/120/0.50/60 finished about $5.682m behind cash with −$4.730m in option P&L before cash interest. The call/put cell counts therefore favor long calls in this particular signal mapping, but the mapping pairs calls with positive SUE and puts with negative SUE; it is not a same-event test of option direction.

The sparse 75/30/60 call is a warning about maximizing a noisy Sharpe. Its funded contracts actually had median 100 DTE, 0.312 delta and strike/spot 1.149; 88 of 246 exits had a quoted zero bid and one exit quote was missing. Its median trade ROI was −88.2%, and its ten largest winners contributed $2.820m. The unadjusted year-block Sharpe interval is [0.017, 1.111], while the joint max-Sharpe bootstrap across 54 cells has p=0.130 and a 95th-percentile null maximum of 0.699. Search-adjusted evidence does not establish a positive Sharpe.

Across the 54 daily cells, 48 four-factor alpha point estimates were negative. Nineteen of 54 individual HAC 95% intervals are entirely below zero and zero are entirely above zero; these cellwise intervals are unadjusted for the 54-rule search. For C/120/0.50/60, alpha was −1.78% annualized (HAC t=−0.89, 95% CI [−5.71%, +2.16%]). Its year-block excess-Sharpe interval was [−0.388, +0.743]. Separately, the corrected settlement portfolio's annual P&L t-statistic was 0.681 (95% CI [−$605k, +$1.167m]); the corrected 54-cell max-t familywise p-value was 0.110 (10,000 year-block draws). Neither test supports persistent positive alpha.

### Supplemental stress run: C/75/0.70/40

The second-highest broad-sample funded-wealth cell was rerun separately with the corrected 40-session risk scaler and the same daily accounting. It funded 5,268 contracts, returned 3.97% CAGR with 0.204 excess-RF Sharpe, had −25.58% maximum drawdown, and finished $4.156m ahead of the rounded French RF proxy. The funded contracts had median 86 DTE, 0.699 delta, strike/spot 0.930 (about 7% ITM), and 6.06% spread/ask. A higher 42.45% of contracts finished profitable than in C/120/0.50/60, and median trade ROI was less negative (−15.88% versus −47.62%); still, most individual contracts lost money. There were 173 quoted-zero and 31 missing exit bids, both receiving zero proceeds.

| Extra entry-premium friction | CAGR | Excess-RF Sharpe | Max drawdown | Ending wealth over cash |
|---|---:|---:|---:|---:|
| 0% | 3.97% | 0.204 | −25.58% | +$4.156m |
| 1% | 3.41% | 0.151 | −26.73% | +$2.609m |
| 3% | 2.23% | 0.044 | −30.65% | −$0.243m |
| 5% | 1.02% | −0.062 | −35.72% | −$2.722m |
| 7% | −0.16% | −0.161 | −40.72% | −$4.727m |

At a 1%-of-OI contract cap with a 2%-NAV issuer cap, the account kept +$1.734m over cash with 0.276 Sharpe and −6.79% drawdown; at a 5%-of-OI cap it kept +$4.253m with 0.240 Sharpe and −22.37% drawdown. The higher Sharpe at tighter capacity reflects the smaller deployed book and does not mean it created more wealth. Excluding 188 eligible entries with adjustment/special-settlement flags funded 5,835 contracts and produced +$5.712m, 4.49% CAGR, 0.242 Sharpe and −32.37% drawdown; treat that as a post hoc sensitivity, not a deployable screen. A next-session entry in the same selected option, held to the same exit date, retained +$2.918m over cash (3.52% CAGR, 0.155 Sharpe, −34.65% drawdown). This is a timing sensitivity, not an opening-quote simulation.

This supplemental run supports a 75/70/40 challenger for discussion, not selection: its +3% friction case already fell behind cash, and all these scenarios use the same inspected historical period. The corrected full-grid alpha for this cell was −1.26% annualized (HAC t=−0.72; 95% CI [−4.70%, +2.18%]). No result establishes that it is a PEAD-specific or future-optimal contract.

## Corrected-calendar daily result

A final full-source-cleaned settlement rerun for C/120/0.50/60 funded 6,016 contracts, paid $74.270m premium, earned $4.217m, had 5.678% premium ROI and 2.274% fixed-window settlement CAGR. The daily funded book below uses a different pro-rata allocator and accrues daily French RF cash, so its funded count, cumulative premium and P&L are not identical to that settlement-only table.

| Corrected daily portfolio with rounded French RF cash proxy | Result |
|---|---:|
| Window | 1998-01-02–2013-12-31; 2011 cash-only |
| Funded contracts / cumulative premium | 6,023 / $85.265m |
| Option proceeds less premium, before cash interest | +$4.908m |
| Cash interest / final NAV | +$4.434m / $19.342m |
| All-cash French RF proxy final NAV / incremental ending wealth | $14.477m / +$4.865m |
| CAGR / annualized volatility | 4.21% / 11.59% |
| Sharpe vs zero / excess French-RF Sharpe | 0.415 / 0.215 |
| Maximum drawdown | −33.19% |
| Profitable contracts / median trade ROI | 35.88% / −47.62% |
| 2013 trade P&L / 2008–12 trade P&L | +$3.506m / −$2.671m |

The rounded French daily RF proxy compounded at 2.34% over the same 15.995-year window; this candidate added about 1.87 percentage points of historical CAGR over that proxy. The RF field is zero for every trading day in 2003 and 2009–2013 because the source is rounded to two decimals in percent; 2004 and 2008 also contain many zero observations. The proxy consequently understates bill carry in low-rate years, and the $4.865m relative wealth figure is not a comparison to a precisely reconstructed Treasury-bill fund. The 2013 OptionMetrics file is partial, so 2013 contribution is not a complete-year event sample.

### How the option account made money

For one option, executable price change is:

    exit bid − entry ask
      = (exit mid − entry mid) − entry half-spread − exit half-spread.

Among 5,967 trades with quote pairs suitable for this attribution, mid-to-mid change was +$12.372m; entry and exit half-spreads consumed $3.291m and $3.429m. The covered subset's residual was +$5.652m. The whole book's option proceeds less premium were +$4.908m. This is a quoted-price decomposition, not proof that PEAD caused the return. No Greek attribution separated stock delta/gamma from IV, theta, dividends or corporate actions.

The 54-cell scan required 4,780,942 unique contract/date quote keys and matched 4,767,300 (99.714%). The strict C/120/0.50/60 daily scan required 367,403 and matched 366,228 (99.680%). It recorded 1,128 missing open marks, 10,636 quoted-zero open marks, 47 missing exits and 779 quoted-zero exits. Missing exits were valued at zero; missing intermediate marks were zero in the base and last-bid carry in sensitivity. Carry changed funded incremental wealth by about −$9k. All cells reconciled trade-level P&L and had no unclosed positions or live positions on non-option sessions. End-of-day quotes/Greeks are not timestamped displayed-size fills; see [OptionMetrics IvyDB US description](https://optionmetrics.com/data-products/).

### Alpha and stability

The corrected annual-P&L t-statistic was 0.681, 95% interval −$605k to +$1.167m. The daily excess-RF Sharpe of 0.214 had a year-block 95% interval of −0.388 to +0.743. Four-factor daily alpha (Mkt-RF, SMB, HML, momentum; RF subtracted; Newey-West lag 60) was −1.78% annualized, HAC t=−0.89, 95% interval −5.71% to +2.15%. Adding centered market squared gave −1.77%, also indistinguishable from zero. The simple market-model intercept was +1.49% with interval −2.45% to +5.43%, also insignificant and not the primary excess-return model.

Factor loadings were approximately 0.411 market, 0.212 size, 0.097 value and 0.131 momentum. They can account for positive raw returns without PEAD alpha. The regression used the [Kenneth French daily factor library](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/data_library.html); exact ZIP hashes and provenance are in `results/options_frozen_call_daily_full_source_collision_cleaned/daily_audit.json`.

### Hold, fill, capacity and contract stresses

| Exit or stress | CAGR | Excess-RF Sharpe | Max drawdown | Increment vs cash |
|---|---:|---:|---:|---:|
| Hold 10 sessions | 0.43% | −0.489 | −20.74% | −$3.767m |
| Hold 20 sessions | 1.24% | −0.153 | −24.16% | −$2.304m |
| Hold 30 sessions | 2.27% | 0.031 | −20.83% | −$0.161m |
| Hold 40 sessions | 3.49% | 0.167 | −24.81% | +$2.826m |
| Hold 50 sessions | 4.07% | 0.214 | −28.70% | +$4.457m |
| Hold 60 sessions | 4.21% | 0.215 | −33.19% | +$4.865m |
| 1% extra premium cost | 3.84% | 0.183 | −33.93% | +$3.798m |
| 3% extra premium cost | 3.07% | 0.119 | −35.59% | +$1.744m |
| 5% extra premium cost | 2.26% | 0.057 | −37.13% | −$0.172m |
| 7% extra premium cost | 1.42% | −0.007 | −38.72% | −$1.957m |

The hold comparisons price the same 6,023 day-zero-selected contract paths at each exit and rerun the funded account; they are not reselected contracts. The trade-level paired profile keeps all 6,023 contracts in view, while funded counts differ by at most one because of the daily allocator and exit availability. Mean single-contract ROI rises with the hold, while median ROI falls from −11.8% at 10 sessions to −47.6% at 60 sessions and the win rate falls from 38.7% to 35.9%. Thus longer holds win at portfolio level through a small number of large winners, not through a typical trade becoming profitable. The 50-session rule has similar Sharpe and less drawdown than 60, but less wealth; neither is statistically selected. Extra cost is a stress charge beyond observed bid/ask, not a calibrated impact curve. Cash-relative wealth disappears between 3% and 5% additional premium friction.

At 1% OI and a 2%-NAV issuer cap, incremental wealth was +$1.734m with 0.276 Sharpe and −6.79% drawdown; at 5% OI it was +$4.253m with 0.240 Sharpe and −22.37% drawdown. The 10%-OI / loose 100%-NAV issuer cap baseline had more wealth but deeper drawdown. OI is only a capacity proxy, not fill depth.

Excluding the 188 contracts with observed cfadj not equal to one or nonzero special-settlement flags left 5,835 contracts and +$5.712m over cash (4.49% CAGR, 0.242 Sharpe, −32.37% drawdown). This does not reconstruct deliverables because the extract has no contract multiplier/deliverable record. Decile 9 alone added $1.366m over cash at 0.114 Sharpe; decile 10 alone +$4.414m at 0.263. This is exploratory, not a validated decile-10 filter.

A next-session ask entry, held to the same scheduled exit (59 sessions remain), funded 5,913 contracts, added +$2.918m over cash, and had 3.52% CAGR, 0.155 excess Sharpe and −34.65% drawdown. It partially addresses same-day information timing but retains the day-zero-selected contract and is not an opening-quote execution simulation.

## What “best” means and the next experiment

Among the 54 rules tested, C/120/0.50/60 ranks first by funded ending wealth. That establishes the maximum **within this searched grid and sample**, not a globally or prospectively proven optimum. The same-contract hold profile makes 50 sessions a reasonable drawdown-sensitive comparator: its Sharpe is 0.214 versus 0.215 at 60, drawdown −28.70% versus −33.19%, and incremental wealth $4.457m versus $4.865m. Do not claim highest Sharpe or positive alpha: the top observed Sharpe is sparse, its familywise test is not significant, and no cell's HAC alpha interval is entirely above zero. Nineteen cellwise intervals are below zero before correcting for the search. The PEAD literature concerns stock returns and does not establish long-option premium profitability. Lei, Wang and Yan find that pre-announcement abnormal call-put implied-volatility spreads predict announcement returns and a stronger announcement response, which can leave less post-announcement drift; this motivates testing an as-of pre-event volatility-spread/volume interaction, not assuming it will make purchased options profitable ([paper](https://doi.org/10.1016/j.jbankfin.2017.04.002)). Bernard and Thomas document the classic stock-level drift evidence ([Journal of Accounting Research, 1989](https://doi.org/10.2307/2491062)).

A stronger selection still requires no-trade, matched neutral-SUE options and beta/convexity controls; purged outer tests selected only from outcomes matured before the test period; and paper trading on timestamped quotes. The daily 54-cell comparison is complete under common accounting, but this already-inspected 1998–2013 archive cannot become independent evidence by relabeling part of it a holdout.

## Reproduction and preservation

No raw OptionMetrics data were copied. The daily quote scans stream an external archive and write aggregate NAV and diagnostics only. The completed daily grid has 54 cells; `--daily-portfolio` remains the detailed single-cell stress runner. Preserve any ignored local data outside a managed worktree before removing it.

Reproduce the final source-cleaned settlement and daily funded grid (use a fresh output path; it is intentionally different from the earlier, incomplete `options_expiry_grid_event_cleaned` pass):

    .venv\Scripts\python.exe -m pead.research.options_expiry_grid --years 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2012 2013 --data-dir $env:PEAD_DATA_DIR --optionmetrics-dir $env:OPTIONMETRICS_DIR --output-dir results/options_expiry_grid_full_source_collision_reproduction --portfolio --daily-grid

Reproduce the corrected single-cell daily book:

    .venv\Scripts\python.exe -m pead.research.options_expiry_grid --years 1998 1999 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2012 2013 --dte-targets 120 --delta-targets 0.5 --holds 60 --direction C --data-dir $env:PEAD_DATA_DIR --optionmetrics-dir $env:OPTIONMETRICS_DIR --output-dir results/options_frozen_call_daily_reproduction --portfolio --daily-portfolio

Google Cloud compute was not needed; the attached drive supported local streaming. Do not transfer or copy the licensed quote archive.

Local event, factor, and runtime files are outside Git. Before removing a managed worktree, preserve its ignored `data/` directory and any other user files to a separate local backup. Never commit licensed raw option quotes.
