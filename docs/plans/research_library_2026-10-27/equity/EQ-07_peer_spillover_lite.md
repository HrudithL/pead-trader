# EQ-07: Peer-announcement spillover, "graph-lite" (industry peers of a strong reporter), with a rewired-peer placebo
- Status: planned | Priority: P2 | Effort: L | Source(s): library `strategies/01_EA1904.md` sections 5-6 and 8 (graph extension decision tree), `audits/01_EA1904_SOURCE_AUDIT.md`, `foundations/07_ACADEMIC_CONTEXT.md` (mechanism 3: information network diffusion, Cohen-Frazzini customer momentum); https://github.com/EA1904/pead-sp500-research

## 1. Thesis & what we take from the source (and what we deliberately do NOT take)
Thesis: an earnings shock at one firm is information about its industry peers; peers that have not yet reported may under-react, so a strong report should predict the peers' subsequent returns. This is the only "new information" idea in the assigned sources that does not depend on intraday data, and roadmap item 5 asks for information beyond the own-firm surprise.
Take: the library's own decision tree for a graph extension, restricted to what we can build PIT: (a) no graph, (b) same-sector peers only, (c) fixed weighted neighbour SUE, (d) degree-preserving random rewiring as placebo. Time-stamp every edge; neighbour data restricted to what was published by t.
Do NOT take: the multiplex GAT (needs FactSet Revere supplier/customer edges, 4-digit GICS history and return-correlation graphs that would leak structure if built over the full sample); the trained-attention claim (the author's own test: trained Sharpe 2.259 vs mean of 10 random untrained graphs 2.249, p = .30, AUC 0.497 for both, i.e. no evidence that learning the weights adds anything); the classical-to-GAT increments (+1.4 to +4.15 points).

## 2. Evidence grade & known defects in the source
Library grade C/D for the whole source; the graph part is weaker: the trained model fails its own random-graph control; only 10 random networks were used for the p-value; edges are non-PIT unless rolling availability records exist; sector filter selection may have used the OOS period (unknown). No evidence in the library supports a spillover edge after costs. Our proxy is therefore a hypothesis test, with the placebo as the main output.

## 3. Exact rules
Peer groups (static, PIT-safe by construction because SIC/FF12 membership is taken at the event date from CRSP `stocknames`/`ff12_sector` already in events; no future relabelling): group `g(j,t)` = FF12 sector of stock j as of its most recent event before t (fallback CRSP `siccd` mapped to FF12 with the same map used in `pead/prep/equity/ff_adjustment.py` - verify the map source) x optionally SIC3 for a finer arm.
Lead event: any event `i` with `day0_date = t`, `sue_rank_pct >= 0.9` (top decile, PIT rank), liquid (`avg_dollar_volume_21d >= 2e6`).
Peer score at date t for stock j in group g: `S(j,t) = mean(sue_rank_pct of events in group g with day0_date in (t-20 sessions, t], excluding j's own events)`, requires >= 3 such events, weights equal (arm b) or log dollar-volume weights (arm c). 
Eligible trade (pre-registered, not tuned): `S(j,t) >= 0.70`; stock j has NOT announced since the previous fiscal quarter-end signal, i.e. its last event `day0_date` is >= 50 sessions before t (known at t; "not yet reported this quarter"); stock j's `avg_dollar_volume_21d >= 2e6`; one new position per (j, quarter) at the first date the rule is met (no stacking).
Entry: close of date t (the last lead event's day0 close; the decision uses data through that close). Hold: 20 sessions. No stop (EQ-04 optional). Exit at the stock's last row if delisted.
Sizing: as `steady_drift_plus` (same Sizing, `W.causal_inverse_variance`, cap 0.80, `risk_target_vol` 0.12, `long_only_cash`).
Own-announcement overlap: j will usually report during the 20 sessions, so the return includes j's own surprise reaction; this is noise with mean zero IF the peer score is unrelated to j's own SUE and is exactly the economic effect being tested (information transfer). Decompose P&L into pre-own-report and post-own-report days using the position table (`exit_reason`-style flag `own_event_in_window`, computed AFTER the fact for diagnostics only, never used as a filter - filtering on a future announcement is look-ahead).
Placebo (library: degree-preserving random graph): permute the group labels across stocks within each date (preserving group sizes), recompute S, trade identically. Second placebo: replace `S` with random draws having the same cross-sectional distribution.

## 4. Data requirements & availability in THIS repo
Available: `ff12_sector`, `size_quintile`, `sue_rank_pct`, `day0_date`, `avg_dollar_volume_21d`, `ivol_252d` in `equity_vol_panel`; `data/raw_wrds/crsp/stocknames.parquet` (SIC history) and `data/raw_wrds/comp` (GICS codes may exist in `company.parquet`; verify the column set); CRSP daily panel. Missing: supplier-customer links (FactSet Revere or Compustat segment customers `wrds`'s `seg_customer` are not in `data/raw_wrds`), historical GICS membership with dates, analyst co-coverage links, return-correlation graphs built with rolling windows. The engine consumes EVENTS with `entry_row_idx`/`exit_row_idx`; peer trades are not earnings events, so pseudo-events must be built (see 5).

## 5. Framework mapping
- NEW event source: `pead/prep/equity/peer_events.py` builds a pseudo-event table in the same schema as `equity_vol_panel` (columns `permno, day0_date, exit_date, entry_row_idx, exit_row_idx, ann_quarter, size_quintile, ff12_sector, liquidity_quintile, avg_dollar_volume_21d, ivol_252d, tvol_60d, sue_rank_pct (= peer score), event_id`), output `data/peer_events.parquet`. To avoid copy-paste, factor the trailing-feature computation of `pead/prep/equity/vol_features.py` (it produces `tvol_60d`, `ivol_252d`, `beta_252d` for `positions`) into a function over `(permno, entry_row_idx)` pairs (REF-event-source).
- Strategy file `pead/strategies/equity/peer_spillover.py`: `PeerSpilloverSector` `name="peer_spillover_sector"`, `PeerSpilloverWeighted` `name="peer_spillover_weighted"`, `PeerSpilloverPlacebo` `name="peer_spillover_placebo"` (reference). These must run on the pseudo-event universe, not on `equity_vol_panel`: add to `EquityStrategy` an optional `universe_loader` (default `load_equity_universe`; `peer` strategies return `EquityUniverse` with `events=peer_events`, same panel arrays). Engine: unchanged `run_equity_backtest`, hold 20 via `hold_sessions=20`.
- Params: `score_threshold=0.70, lookback_sessions=20, min_peers=3, not_reported_sessions=50, hold=20, weight_mode in {"equal","dollar_volume"}, group in {"ff12","sic3"}`. `family="equity-spillover"`, `status="research"`, `parent="steady_drift_plus"`, `legacy_twin=False`.

## 5b. Build vs. change vs. restructure
(1) NEW: `peer_events.py` prep stage + `Stage("peer_events", ...)` in `pead/prep/stages.py`; `peer_spillover.py`; `tests/test_peer_spillover.py`; placebo generator.
(2) CHANGE: `pead/strategies/base.py::EquityStrategy.run/backtest` to take the universe from `self.load_universe(ctx)` (default `ctx.equity()`; zero change for existing strategies; test: all pinned NAVs equal); `pead/runner.py` must not assume one universe per worker (check how it calls `ctx.equity()` before the change).
(3) RESTRUCTURE: REF-event-source (shared builder of position rows + trailing features from arbitrary (permno, date) pairs; also what any non-earnings event source - e.g. a future news or revision spec - will need); REF-event-feature-store.
(4) IMPROVE: no industry-link data beyond sector codes (document; ask for Compustat segment-customer file if wanted); `ff12_sector` is available only on event rows, so a PIT stock-sector lookup table (`permno, from_date, ff12`) is needed; reports label events as earnings reactions (single report template should show "peer" events).

## 6. Acceptance tests
- Synthetic: 3 lead events in sector X on days 1-3 with ranks 0.95, a peer with no events in 50 sessions -> exactly one position opened at day 3 close, none on days 4+ in the same quarter; peer excluded if it reported 10 sessions earlier.
- No-look-ahead: truncation invariance of `S` (rewrite events after date t; `S(.,t)` unchanged); the eligibility rule uses only past events (add a future event for peer j after t and assert eligibility at t unchanged); own-announcement overlap is never used in selection (assert the filter is not a function of future rows).
- Group-label permutation placebo preserves group sizes per date (assert counts identical).
- Own events excluded from `S(j,t)` for j.
- Day0 handling: the lead's day0 return is not earned by the peer position (entry at the lead's day0 close, first P&L next session).
- Determinism; pseudo-event row count; cap engine behaviour with 20-session holds.

## 7. Experiment plan
Pre-registered grid is the single point above plus the three arms (equal-weight sector, dollar-volume weighted, SIC3); SEARCH day0_date <= 2005-12-31, HOLDOUT 2006-2013 (REF-period-split) (seen already for other books; say so). Report: mean 20-session market-adjusted return of eligible peers vs all non-announcing same-size stocks (portfolio-free event study, clustered by date-quarter) FIRST; only if that is positive and significant at t >= 2 run the portfolio. Placebos: rewired groups (200 draws, report the empirical distribution of the real result against them, not 10 as in the source), random scores. Comparators: `steady_drift_plus` 12.07% / Sharpe 1.19; the peer book is a separate sleeve, so also report correlation with `steady_drift_plus` daily returns and the blended Sharpe at 20% sleeve weight (sleeve idea: PortfolioStrategy in `pead/strategies/portfolio/sleeves.py`). Decision: continue only if real exceeds 95th percentile of the rewired placebo and alpha t >= 2 in both windows.

## 8. Expected outcome / hypotheses and risks
H: a small positive, mostly early-era effect that decays (the repo's own era table shows own-surprise skill falling 9.4 -> 4.1 points). The honest prior is no tradable edge: the source's own control says learned structure adds nothing, and a sector-wide aggregate is highly correlated with sector returns (industry momentum), so alpha vs the market and size could be pure sector beta - add FF12-sector-neutral returns as a second benchmark. Risks: sector tilt, overlap with `steady_drift_plus` positions (same names later), 20-session holds raise turnover and costs, requires an invented event schema (engineering risk), data ends 2013, no customer-supplier data, capacity not studied.

## 9. Dependencies and implementation checklist
Depends on EQ-02 (rank columns, timing), EQ-08 (invariance kit), REF-event-source. Independent of EQ-01/03/04/05/06 (lowest priority).
- [ ] PIT stock-sector lookup table
- [ ] refactor trailing-feature builder (REF-event-source)
- [ ] `peer_events` stage + QA
- [ ] portfolio-free event study first (gate)
- [ ] strategies + `load_universe` hook
- [ ] placebo distribution (200 permutations) and verdict
