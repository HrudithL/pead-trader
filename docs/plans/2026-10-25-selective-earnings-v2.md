# Selective earnings v2: research redesign



User authorized a thorough redesign on 2026-10-25, explicitly including opportunity audits, improved contract selection and alternative liquidity conditions. Scope remains RESEARCH.md; no existing strategy is blended in to manufacture the target.



## Findings and decisions

2007 unique-event funnel: 8,685 raw quoted events; 7,792 with 30â€“60DTE; 2,034 after price/spread/OI/volume rules; only 143 with the fixed delta pair. Fixed delta windows and uniform protection-wing filters are a major bottleneck. This does not establish profitable broader trades.



Replace fixed delta selection with a finite menu of executable directional debit/credit verticals and post-event condors. Compare structure economics at whole-structure level. Use zero-bid protection when ask is positive and settlement standard; OI and small-lot capacity constrain allocation rather than requiring 50 contracts traded in every leg the previous day. Preserve daily quotes and never use future liquidity to select entries.



Replace remaining-IV Blackâ€“Scholes scenario screening with direct, actually quoted historical net structure outcomes. Training observations include entry cashflow, exit bid/ask and commissions, with explicit outcome-maturity dates. Fit only prior-matured outcomes; evaluate issuer/date dependence and within-archetype sample counts. No observed test payoff enters its own prediction.



Chronology: 1999â€“2004 discovery; 2005â€“2007 validation; 2008â€“2010 development test; 2012â€“2013 remains reserved. The old policy's aggregate development results have already been seen; this is a redesign, not a pristine new historical holdout. No knob is selected from final-holdout performance. Target 12â€“15% NAV CAGR plus benchmark/risk gates, not return on option premium.



Finite research comparisons: (1) expanded structures with prior-matured empirical payoff model; (2) directional-only and short-vol-only attribution using the same payoff model; (3) conservative versus moderately wider quoted spread/capacity policy; (4) predefined risk-budget stresses. Record every configuration and do not select a high-return cell without validation. Failed hypotheses remain recorded.



Implementation: configurable liquidity and candidate enumeration; cache full entry chains; quote actual candidate exits for training; chronological predictor; callback into the existing tested cash/collateral ledger; scan daily marks for funded candidates; reports and regression tests. All original caches, source data and worktrees remain preserved. No cleanup or worktree removal.





Discovery refinement before validation: the 1999–2001 direct outcome distributions are negative for every unconditional continuation shape. Preserve these `candidates_YEAR` prototypes. The replacement `candidates_flexible_YEAR` explicitly includes both continuation and fading of current announcement news, conditioned on SUE alignment, announcement-return alignment and IV/RV. It can learn which payoff direction is economically justified. No validation/test return was consulted for this change. The completed prior worker scan was stopped by its verified process ID; no source or cache was removed.





Before validation: predeclare three holding rules (10 sessions, 20 sessions, and carry for up to 45 sessions, each closed no later than expiry minus two calendar days and year-end). This tests early reversal versus slower drift and the economics of collecting more option decay instead of paying to exit with substantial time value remaining. All candidate daily quote histories are cached, making the same data usable for honest daily marking and fixed-horizon labels. Direct payoff regressions will condition on observable friction, IV/RV, surprise/announcement-return alignment and trailing market regime, with a rolling four-year training window. No validation or test returns have been used to choose these hypotheses. The earlier fixed-horizon prototype remains preserved.





## Next independent hypothesis: liquid index expression

The expanded single-name continuation/reversal model fails at both capitals. The balanced ledger reconciles 2,838 trades to independent quoted labels within 2e-12 dollars; the failure is economic, not an accounting artifact. Preserve all results. Next: express aggregate earnings-response information through native SPX European cash-settled credit verticals, reducing single-name quote frictions and removing stock early-assignment exposure.



Before index validation/test: six fixed policies (defensive agreement, trend carry, earnings agreement carry, trend two-sided, earnings agreement two-sided, active trend carry), short/long absolute deltas 25/10 or 35/15; maximum per-week loss budgets 3%, 6% or 9% and aggregate 20%, 30% or 40%; 20-session or <=45-session carry holding, exit at least four calendar days before expiry and flat at year-end. Prior-session market trend and RV determine risk-off closes. Each policy is tested at USD100K and USD1M. All quotes cross bid/ask; commission/exchange allowance USD1.50/leg/side. Select by validation 2005–2007 with >=20 trades and <=30% drawdown, separately per capital; evaluate 2008–2010 afterwards. No final-holdout data read. This is a new earnings-portfolio hypothesis, not a successful replication of the failed single-name design.





The validation-selected index credit policies produce +4.27% (USD100K) and +0.75% (USD1M) in 2008–2010, below target. Next independent mechanism: fully premium-funded longer-dated call/put verticals, not more short-premium risk. Predeclare 90–180DTE, target120; long80/short25 delta; 45-session or market-regime exit; exposure admission targets 1x/1.5x/2x; current-value premium-at-risk budgets 35/50/60% NAV with compulsory current-value risk rebalancing. Trend-only controls and earnings-breadth agreement variants at both capitals. These targets describe inherent option leverage, not borrowing. Select on 2005–2007 validation, judge on 2008–2010, keep 2012–2013 reserved. A separate Treasury-sweep sensitivity may use official French one-month bill returns with a full-month lag and 10bp/year allowance; it must be attributed separately and never called option alpha.





Next simpler expression: stock replacement using fully paid, deep-ITM long SPY calls, rather than selling the upside or buying expensive bear puts. No short option legs, no early-assignment liability. Predeclare 180–365 DTE, target270, delta95, <=63-session holds, weekly entry with at most four strike tickets, target delta exposures 1.5x/2x/3x subject to current-value premium-risk budgets 45/55/70% NAV and cash funding. Compare the prior-close 200-day bull regime with an earnings-confirmed early-recovery regime (above prior 50-day average with positive earnings-response breadth). Macro RV cutoff60%. Both capitals. Vendor-missing delta may use the mathematical upper bound1 only for deep-ITM calls with valid actual quotes; quote prices are never imputed. All bid/ask, dividend effects and implicit option financing remain in observed option prices. Select on validation, retain final holdout. The unlevered same-signal price-return diagnostic is only about4.4% in 2008–2010, so leverage and option costs must be shown explicitly rather than mistaken for free option alpha.





SPY availability correction before observing any call results: native SECID109820 matches the ETF CUSIP78462F10, but has no option quotes before2005. Cboe confirms SPY options launched January10,2005 (https://datashop.cboe.com/faqs). The SPY call experiment therefore starts2005, retains2005-2007 validation and2008-2010 development test, and reports full performance only for2005-2010. Do not count prelisting cash years as a strategy history or manufacture early options.





Call-data execution refinement after2005 diagnostic, before reading2008-2010 calls: multiple deep-ITM SPY quotes are crossed on2005-03-17 (e.g.option23955057 bid35.5/ask35.4). Immediate zero-value risk-reserve liquidations permanently write off valuable calls. Preserve initial full-reserve experiment. New deferred experiment continues worst-loss NAV marks on bad-quote days, freezes new entries, retains the holding, and defers required liquidation until an executable valid quote returns. At final sample end it retains the pessimistic reserve treatment. Never carry forward quotes, impute option prices, or claim these days are execution-validated. Daily reserve drawdown remains visible. This improves ledger realism, not the data provenance.





Post-earnings premium redesign, frozen before any new put outcome: fully cash-secured short puts remove the protection-wing cost. Hypothesis is residual option volatility overpricing after a positive observed earnings reaction. Use price reaction only; do not rely on EPS share-basis assumptions. Forecast non-event realized volatility from preceding60 stock sessions, masking already-observed earnings reaction sessions, minimum30 nonmissing returns; add10 volatility points in quadrature. Use prior-session price and zero physical drift in analytical expected put liability (non-event variance during hold plus current implied variance over remaining option life), with1% probability of an additional40% downside jump. Entry bid minus expected liability and round-trip fees must exceed25bp of strike. Three fixed profiles: income35delta/reaction>=1%/IV-RV>=1.25; drift60delta/reaction>=2%/IV-RV>=1.10; rich50delta/reaction>=0%/IV-RV>=1.50. Compare prior200-day market-bull gate versus no market gate; both capitals. 21-45DTE target30; hold<=20sessions and close>=4calendar days beforeexpiry;8% maximum-loss/name,30%/sector,20%/entrycohort,85%aggregate, fullstrike cash collateral, no account borrowing. Entry menus and daily marks come from realquotes. Same existing source universe is conditional (static links), so no investment-validation claim. American short-put assignment is unverified; cash collateral is necessary but does not establish a realistic exercise/stock-handoff simulation. Any candidate clearing target must resolve that and identity provenance before acceptance. All six profiles, failures, missing quotes and costs remain recorded; no finalholdout is used to pick knobs.





Call selection capacity refinement before its outcomes: the initial closest-to95delta selector can take a one-lot strike while ignoring a qualifying85-100delta strike able to fund materially more exposure, especially at1M. Preserve closest-delta profiles. Add a capacity-aware execution variant with unchanged signals, target exposures, premium-risk caps, quote gates and four weekly tickets. Among feasible quoted candidates maximize funded delta notional, then minimize round-trip quoted friction and deviation from95delta. This is account-size-aware selection, not increased assumed market capacity or relaxed quote validity. Report every profile; do not retroactively call a development-selected winner pristine.





Single-name stock replacement, frozen before validation: discovery1999-2004 audit finds positive announcement CAR>=5% has mean next20-session stock return2.034%, versus negative-CAR names' signed return -0.709%. SUE-tail confirmed symmetric signed means0.46-1.15%, weaker. Thus test positive-news fully paid deep-ITM calls directly, not macro timing or a short leg. Three predeclared CAR floors3%,5%,8%, no EPS ranking, no market-timing gate. Eligible calls75-100delta, prior-price/strike>=1.05,70-180DTE, target120; holds20sessions, expiry buffer7days, <=3% paidpremium risk/event,5%issuer,25%sector,15%cohort,60%aggregate, no borrowing. Quote/OI/volume limits unchanged cash-put liquidity policy except call maxrelative spread15%; round-trip0.65contract commission. Select feasible contract minimizing quoted friction plus linear20-session amortized extrinsic per unit delta (prior-known stock price), then matureexpiry distance; no futurequote liquidity used. Evaluate both capitals and select on2005-2007 validation before examining2008-2010 results. Longholders have no short-option assignment liability; as-of identity links and EODtiming remain research limitations. Record all profiles and retain2012-2013 holdout. This is the outright post-EA call mechanism in RESEARCH.md, not an existing equity strategy imported or blended.



Single-name call source QA, before any portfolio result: require finite positive vendor implied volatility in2%-300% range as well as finite75-100delta, so a contract for which the vendor cannot solve a consistent implied price is not admitted as a cheap directional opportunity.





Before single-name validation (only1999-2000 run progress seen): add one declared funding stress for each CAR floor. Raise paidpremium/event budget from3% to6%, issuer8%,sector30%,cohort25%,aggregate75%, with current-value premium risk rebalancing enabled. This tests whether a3K limit excludes the cheaper-extrinsic deep-ITM contracts rather than only controlling underlying exposure. No account borrowing; fullpremium paid, quoted capacity unchanged, both capitals. Report baseline and funded stress separately, do not select risk from2008-2010 outcomes. All threshold/funding pairs remain in the trial register.





Before single-name validation (progress through2003 only): predeclare a market-risk gate sensitivity using the already frozen prior-close200-day SPY trend, with no new timing parameter. When below the average, admit no new long calls and close existing calls at next valid quoted opportunity. Compare unhedged and gated versions of both3%baseline and6%funding stress. Rationale: discovery losses2001-2002 indicate positive company news does not remove broad equity downside. This controls market exposure rather than claiming options transform weak directional forecasts into alpha. Report the full3 CAR floors x2 funding rules x2 gate matrix per capital. No2008-2010 single-name call outcome has been read and no finalholdout used.





Resume audit: two1999 short-put worst-loss reserves total80019.50 versus9239 net profit on54 normally quoted exits. Native records show QCOM option11430703 became SS1 afterMay11 split, while PERC option11796780 continued underABT SECID100972 afterNov22 merger. This is unresolved deliverable accounting, not an observed100% loss. Native extract lacks ContractSize; never assume SS1 multiplier100. Add explicit ex-ante policy to close/avoid publicly announced nonregular distributions, using native declaration dates strictly delayed to the following benchmark session, and retain unknown-action failures. Never use future cancellation flags to delete known announcements. Supplement two documented missing/late records with issuer primary evidence: QCOM split disclosed inApril20,1999 earnings release (https://www.qualcomm.com/media/documents/files/qc-earnings-release-q2-1999.pdf); PERC pendingmerger publicly discussedOctober7,1999 (issuer-hosted proxy https://www.abbottinvestor.com/static-files/ae83411b-2d4b-4461-9684-465c6c302995). Supplement is incomplete, not a verified comprehensive corporate-action calendar. Preserve all old result vintages.



Capital-granularity experiment: at1M, CAR8 baseline mean paidrisk35K, maximum276K, maxsinglepremium35.5K; funded variant mean37.8K,max381K, maxsingle75.9K. Those contracts are generally unaffordable under100K's3-6K ticket limits. Predeclare concentrated fullypaid call version at both capitals:30%ticket/issuer,50%sector/cohort,80%aggregate current-value premium risk, and a2x NAV admission limit on gross stock-equivalent exposure using delta upperbound1 and prior-known underlying prices. No borrowing, no forced scaling of1M returns into100K. This is a risk-budget/affordability hypothesis, not a stronger stock forecast. CAR3/5/8 unchanged, report unhedged and fixed200day market-gated cases. Development outcomes are already known, so any resulting candidate requires a frozen independent holdout and explicit concentration-risk assessment. No final2012-2013 portfolio outcome is consulted in this redesign.



Integrated carry/implementation hypothesis: positive-EA drift is slow, turnover is expensive, and one-ticket/one-strike entry blocks capacity even when other listed strikes have valid quotes. Predeclare one long-horizon variant: same CAR3/5/8 signals; fully paid75-100delta calls120-270DTE target180; max50session holding, expirybuffer14days; up to8distinct feasible strike/maturity tickets perearnings event sharing per-option capacity; approximate quotedfriction plus50session linear extrinsic amortization <=1%ofdelta-equivalent stock value (50%haircut to discovery CAR5 stockdrift estimate2%). Same concentrated admission limits30%ticket/issuer,50%sector/cohort,80%currentpremium budget and2x gross stock-exposure upperbound; both capitals. Carry positions continuously across calendar years, no yearend liquidation or last22session entry blacklist, finalsample2010 exit remains. Compare no marketgate to existing200day gate, no new timingparameter. Actions avoided using only prior-public declarationdates. This is exploratory design after seeing earlier development data, not pristineOOS; allprofiles preserved and2012-2013 finalportfolio holdout remainsunused. Add bounded-memory year-quote storage; verify exact ledger equivalence to an all-DataFrame run across a year boundary. Never use future quote coverage to select entries; cache IDs based on current entry menus only and fetch subsequent observed history for marks.

Carry implementation audit before portfolio outcomes: the hard50cent spread cap admits only26,25,23,23 events in1999-2002 even though a100/102 call on a500 stock passes the declared1%delta-equivalent totalcost criterion. The normalized variant removes only the absolute-dollar cap, retains15%relative spread/OI/participation and the1%whole-trade cost screen; expensive relative/whole-cost opportunities still fail. Keep the narrow preparation prototype. Fix prior-calendar handling by passing full1995+benchmark calendar (never wrap first1999session to2010), and retain cache paths instead of12year rawentryframes for bounded memory. These are preparation/engineering decisions, no carry portfolio return has yet been read.

Final carry timestamp correction before portfolio outcomes: vendor post-July2009 optionquotes are15:59 while the exact underlying time used for same-day IV/Greeks is not established. Admission now requires previous-session vendor delta and IV, which are known before the current quote. Current bid/ask remains actual execution data; current Greeks cannot change selection. Fetch prior chains from actual prior sessions, version entrycache_v3 and lagged mark caches, and retain both earlier preparation prototypes. This avoids depending on an unverified one-minute analytics alignment. No further carry signal/holding/risk/liquidity knobs are selected from its validation/test outcomes.

Carry statistics correction: original zero-cash-rate run produced CAR5/100K validation13.12%CAGR but later2008-2010 -2.93% and full7.71%. Preserve that complete run as carry_lagged_zero_rf. Zero cash interest is a conservative return assumption, not the correct opportunity-cost rate for excess Sharpe/alpha. New final carry reports use official French dailyRF for excess statistics while explicitly holding cash_rf=0 in baseline, so this correction does not change trades or NAV. A separate --cash-sweep sensitivity may accrue the existing full-prior-month bill-rate proxy, with cash interest separately attributed. No signal, holding, contract or risk knob is changed by the statistics correction.

Final independent implementation comparison: a deepITM stock-replacement call does not require a vendor volatility solve. MissingIV/Greeks can exclude precisely the near-intrinsic standard contracts the method seeks. Predeclare an intrinsic/moneyness variant using only actual standard bid/ask, prior-known stock price, laggedOI/volume, prior-price/strike>=1.20, and a conservative call-delta upperbound1 for stock-exposure admission (not an imputed actual Greek). Require ask>=prior intrinsic minus1%ofstock to reject conspicuously cheap/stale quotes; retain1%stock-equivalent fullcost limit,15%relative quotedspread,50session/120-270DTE,8tickets,allfundingcaps/knownactions/2xexposure. No option prices are imputed and no current/future stockclose is used in admission. Compare the same CAR3/5/8 and predeclared marketgate at bothcapitals. Postrun audit any pricing inconsistencies against native underlying closes; a favorable result dependent on invalid quotes cannot establish an edge. This is the final declared economic comparison in this research batch; retain all failures and do not choose further knobs from its later-test returns.
