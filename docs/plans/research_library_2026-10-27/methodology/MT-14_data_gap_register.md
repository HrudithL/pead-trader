# MT-14: Data-gap register (every dataset the library assumes vs what this repo has)
- Status: planned | Priority: P1 | Effort: S (register) + varies (acquisition) | Source(s): library `foundations/06_DATA_SCHEMAS_WRDS_OPTIONMETRICS.md`, `reproduction/01` section 1 (collection 1A-1D), `reproduction/04` section 2 (frozen input schema), `foundations/02` (PIT validation requirements), `foundations/04` (option chain fields), `references/01_SOURCES.md` (data and methodology references); repo `data/metadata/wrds_pull_manifest_*.csv`, `data/metadata/wrds_available_libraries.txt`, raw folders under `data/raw_wrds/`

## 1. Purpose & what we take from the library
One authoritative table: for every dataset the library's methods need, what the repo has (with evidence), what degraded proxy is acceptable, or whether the item is BLOCKED. Status vocabulary: **PRESENT** (used), **PRESENT-UNUSED** (local, not wired), **PROXY** (degraded substitute exists, label results), **WRDS-PULL** (a WRDS table that appears in `wrds_available_libraries.txt`; that file lists schemas visible on the server, which is *not* proof of entitlement -- verify before planning), **EXTERNAL** (free public download; the user must approve any download first), **BLOCKED** (licensed/unavailable; do not fabricate).

## 2. Gap analysis vs the repo today
Local data inventory (verified 2026-10-27): CRSP `dsf_1995..2014` (cols `permno,date,ret,prc,shrout,cfacpr,cfacshr,retx,vol,bid,ask,bidlo,askhi,openprc,numtrd`), `dsedelist`, `dsi` (cols `date,vwretd,sprtrn,vwretx,ewretd,ewretx`, 1995-01-03..2014-06-30, 4,908 rows), `stocknames` (`exchcd,shrcd,siccd`), `ccmxpf_lnkhist`; IBES `actu_epsus`, `detu_epsus` (14.6M rows), `statsumu_epsus`, `idsum`; Compustat `funda`, `fundq` (incl. `rdq`, `epspxq`), `company`; `wrdsapps.ibcrsphist`; OptionMetrics IvyDB extract on `D:` (EOD, 2011 `opprcd` missing, `link_events_secid.py:80-83`); derived events/IBES parquet. Sample window 1996-2013 (panel to 2014-06).

## 3. The register
| # | Dataset (library source) | What the library does with it | Repo status | Degraded proxy / action | Consumers |
|---|---|---|---|---|---|
| D1 | Release timestamps in UTC: press release, 8-K acceptance (foundations/02, 06) | decision/fill clocks; BMO/AMC | PROXY: IBES `anntims` (ET, minute) for all events; 12.6% unknown (`anntims_unknown`) all pushed to next session | 8-K Item 2.02 acceptance times from SEC EDGAR: **EXTERNAL** (free, public; coverage thin before ~2003 so would not cover 1996-2002); wire headlines BLOCKED | MT-02, MT-11 R7 |
| D2 | Analyst-level forecasts with publish/effective times (`analyst_forecast`) | consensus rebuilt strictly pre-release, revisions, dispersion | PRESENT-UNUSED: `detu_epsus` (`analys, revdats, revtims`) | build in MT-07 | MT-07 |
| D3 | Consensus snapshot vintage with `observed_at` | PIT consensus | PROXY: `statsumu` `statpers` strictly before `anndats` for all 194,316 analyst rows; staleness unbounded | cap variants; true vendor as-of archive BLOCKED | MT-02 E8/E9 |
| D4 | First-reported (vintage) EPS and restatement flags | first-print actual, seasonal flavour | PROXY: IBES actuals; Compustat `epspxq` as a cross-check; restatements invisible | label `vintage=assumed`; compare IBES vs Compustat sign agreement | MT-07 |
| D5 | Compustat report date `rdq` | independent date check | PRESENT-UNUSED (`fundq.rdq`) | MT-02 E7 | MT-02 |
| D6 | Daily CRSP returns incl. delisting | `RET` x `DLRET` | PRESENT-UNUSED: `dsedelist` (3,970 rows; `dlret` 4.06% missing) not applied | MT-05 | MT-05 |
| D7 | Opening prices | next-open fills, gap decomposition | PRESENT-UNUSED: `openprc` in raw dsf, dropped from normalized panel | stage `panel_quotes` | MT-13, MT-10 |
| D8 | Quoted bid/ask | spread costs | PRESENT-UNUSED: raw dsf `bid, ask` (closing quotes; ~1-2% missing) | `panel_quotes`; intraday NBBO BLOCKED | MT-06 |
| D9 | Intraday and extended-hours bars, volume | confirmation features, after-hours reaction, same-clock RVOL, intraday fills | BLOCKED: no bars; `wrds_available_libraries.txt` shows only TAQ *sample* schemas (`taqsamp`, `taqmsamp`) -- verify | daily-bar policies (session-level delays) only | MT-11 R7, MT-13 |
| D10 | Historical index constituents (S&P 500) | PIT universe, survivorship | BLOCKED/WRDS-PULL: not in repo; `crsp_a_indexes` appears in the schema list -- entitlement unverified; `live/config.py:36` assumes S&P 500 for live | proxy: dynamic top-N by trailing market cap/dollar volume at day0-1 (all inputs local: `shrout`, `prc`, `vol`) | ROADMAP #3, MT-09 universe ablation |
| D11 | Borrow rates, locates, short interest, recalls | short-leg costs, rejections | BLOCKED (no lending data) | grid by size quintile (MT-06); `wrdsapps_eushort` is non-US | MT-06 |
| D12 | Daily risk-free rate | excess returns, cash yield | PROXY: `core/rates.py` annual T-bill averages typed from memory (file docstring says so) | `crsp_a_treasuries` (WRDS-PULL) or FRED DGS3MO/TB3MS (**EXTERNAL**); ROADMAP #2 | MT-03, MT-06 |
| D13 | Fama-French 5 + momentum daily factors | multi-factor HAC alpha | MISSING: `alpha.py` uses `vwretd`, `ewretd-vwretd` only | `ff` schema is listed (WRDS-PULL, verify) or Ken French library (**EXTERNAL**) | MT-03, MT-09 |
| D14 | SPY / QQQ / leveraged-ETF series | benchmark ladder, matched-beta controls | PROXY: `sprtrn` (S&P 500 return) in `dsi` covers SPY-like benchmark 1995-2014; QQQ/TQQQ/TECL did not exist for most of the sample -> ETF-regime strategies BLOCKED for 1996-2013 (post-2013 data needed) | use `sprtrn`, `vwretd`; ETF regime area needs post-2013 data | MT-09 L0 |
| D15 | Data after 2013 (true holdout, modern regime, library tests 2020-2026) | frozen holdout, post-2020 PEAD compression (strategies/12) | BLOCKED: events and panel end 2013 / 2014-06 (ROADMAP #7) | extend CRSP/IBES/OptionMetrics via WRDS pull when entitlement/time allows | MT-04 `pristine` |
| D16 | Investable small-cap benchmark | alpha vs a like-for-like benchmark | MISSING (ROADMAP #1) | construct from local CRSP (size-decile VW, monthly rebal., same dollar-volume floor) | MT-09, STEADY_DRIFT |
| D17 | Option chain as of decision time: bid/ask, size, OI, IV, Greeks, multiplier, exercise style | contract selection, spreads | PRESENT (EOD): OptionMetrics; no sizes, no intraday; 2011 `opprcd` missing | EOD-only caveat on every options result | options area |
| D18 | Dividends / ex-dates | assignment, early exercise, dividends-in-lieu | PROXY: `ret - retx` gives distributions for stock; option early exercise not modelled | options area | MT-06 |
| D19 | Customer/supplier graph with effective dates (FactSet Revere) | spillover | BLOCKED (`factsamp_revere` is a sample schema only) | none; `wrdsapps_link_supplychain` listed -- verify | overlays area |
| D20 | Earnings calendar (scheduled dates, pre-announcement) | avoid-earnings, expected-move straddles | BLOCKED/PROXY: scheduled dates not in repo; realized `anndats` only (look-ahead if used as schedule) | `wrdsapps_evtstudy_us`/IBES `fpedats`-based schedule estimates; flag as inferred | options/overlay areas |
| D21 | Trade/fill ledger and execution data | slippage calibration | BLOCKED (no live fills; paper only `pead/live`) | grids; calibrate from paper-trading ledger when it accumulates | MT-06 |
| D22 | News, transcripts, attention proxies | strategies 23/24 | BLOCKED (`ciq_transcripts`-type schemas listed as samples only) | out of scope | -- |
| D23 | Sector/industry as of date | sector neutrality | UNVERIFIABLE: `ff12_sector` producer absent from repo (MT-02 E18); `stocknames.siccd` has SIC history | rebuild FF12 from dated `siccd` | MT-02 |
| D24 | Market cap/size as of date | size quintiles, universe | PROXY: derivable from `prc*shrout` at day0-1 | rebuild + compare to stored `size_quintile` | MT-02 |
| D25 | Security master with dated identifiers | joins without ticker matching | PRESENT: `stocknames`, `ccmxpf_lnkhist`, `ibcrsphist`, `resolved_universe_ids.parquet`; ambiguous links tracked (`data/metadata/ambiguous_links.csv`, `permno_ambiguous` flag in events) | keep flags visible in reports | MT-02 |

## 4. Data requirements
This document *is* the data requirement ledger. Acquisition rules: nothing is downloaded or pulled without the user's explicit go-ahead (filename, source, size stated first); WRDS pulls are scripted through the existing manifest pattern (`wrds_pull_manifest_*.csv`) so provenance is recorded.

## 5. Framework mapping
Machine-readable copy `data/metadata/data_gaps.csv` (columns above); `python -m pead data-gaps` prints it and checks that every PRESENT/PRESENT-UNUSED path still exists; each strategy spec (equity/options/overlay areas) must list the D-numbers it depends on; manifest (MT-12) records which proxies a run used (`proxies: ["D12","D1"]`).

## 5b. Build vs. change vs. restructure
- **Cross-area IDs:** REF-tbill-real (OV) = D12; REF-fundq-pull (OPT) = D5/D4.
- **NEW:** `data/metadata/data_gaps.csv`; `pead/core/gaps.py` (loader/checker); `python -m pead data-gaps`; acquisition scripts (`pead/prep/external/{fred_rf.py,ff_factors.py,edgar_8k.py}`) written but not run until approved.
- **CHANGE:** `pead/core/rates.py` (swap typed-from-memory annual averages for a loader reading a downloaded daily series; fallback keeps current constants and warns; regression risk: MEDIUM -- Steady Drift cash-leg returns and Sharpe change slightly, re-pin `steady_drift*`); `pead/core/alpha.py` (accept factor frame); `pead/prep/stages.py` (register `panel_quotes`, external-data stages with `kind="external"` that never run under `--all` without a flag).
- **RESTRUCTURE:** separate raw (`data/raw_wrds`, `data/normalized_equity`) from derived vintage and external additions (`data/external/`) in `pead/common/paths.py` (new `EXTERNAL_DIR`) so hash manifests (`verify_raw_data.py` FOLDERS dict) cover them.
- **IMPROVE:** the typed-from-memory T-bill series and the unused delisting/quote/open/rdq data are the cheapest accuracy gains in the whole program (D5-D8, D12); register shows all of them are already local or one free download away.

## 6. Acceptance tests
- `python -m pead data-gaps --check`: every row marked PRESENT/PRESENT-UNUSED resolves to an existing path with expected columns (column list asserted from parquet metadata).
- Each BLOCKED row has a written "what we do instead" and no code path silently substitutes it (grep test: modules referencing D9/D11/D19 raise `BlockedDataError`).
- Manifest `proxies` field populated for runs that use D1/D3/D4/D12 proxies.

## 7. Rollout plan
Publish the register now; wire D5-D8 (local data) first; request approval for D12/D13/D1 downloads; schedule D15 (post-2013 extension) as the gating item for any `pristine` holdout and for QQQ/ETF-regime work.

## 8. Risks
- WRDS schema visibility is mistaken for entitlement; verify with a one-row query before planning.
- Free external series (FF, FRED, EDGAR) change vintages; record download date/hash in the manifest.
- Treating a proxy as the real thing: every proxy row must surface in the report footer.

## 9. Dependencies and implementation checklist
Depends on: REF-tbill-real, REF-factor-data, REF-panel-delist. Blocks: MT-03 multi-factor alpha, MT-04 pristine holdout, ETF-regime strategies.
- [ ] commit `data_gaps.csv`
- [ ] `data-gaps --check`
- [ ] wire D5-D8 (local)
- [ ] ask user to approve D12/D13 downloads
- [ ] one-row entitlement probes for D10/D19/D20
- [ ] decide post-2013 extension (D15)
