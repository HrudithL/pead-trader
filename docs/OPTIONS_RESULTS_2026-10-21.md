# PEAD option choice: backtest findings and decision

## What can make the trade profitable

The earnings-surprise signal forecasts a tendency in the underlying stock. A purchased option makes money only when the change in its *saleable bid* exceeds the ask paid, including spread, time decay, any implied-volatility change, and market impact. A put on a negative-surprise stock can still lose if the stock merely underperforms rather than falls. An out-of-the-money call can profit greatly from a large upside tail, but it can expire with no bid. Longer expiration reduces near-term decay and supplies more optionality, while charging more premium. Holding longer gives PEAD more time to accrue while exposing the buyer to further decay and volatility changes.

Three independent reviews reached different emphases. The mechanism review proposed a liquid near-the-money call as the benchmark and a low-volatility, moderately out-of-the-money call as the first challenger. The audit found that the existing book's apparent profits are smaller than the credits assigned to zero or missing bids, and that most desired order sizes lack demonstrated capacity. The skeptical review found event-universe, drive-path, and delta-cell flaws in the first grid; these were corrected before the final grid run. All agreed that a historical winning cell alone would not justify live trading.

## Existing strategy versus stricter execution

The committed `options_cap20` result reports 0.282% annualized return, Sharpe 0.156, and −18.9% maximum drawdown; its zero-bid-at-zero sensitivity reports −1.04% annualized return. The original 8.58%/3.78 claim was an artifact of midpoint fills and stale marks. See `OPTIONS_AUDIT_2026-10-21.md` for the ledger and code review.

The new `pead/research/options_robustness.py` backtest buys the previously selected contracts at ask, liquidates at the actual positive bid, and assigns zero proceeds to a missing or zero bid. It rejects holds crossing the missing 2011 data or expiration, sizes entries in date order under a 20% outstanding-premium cap and a 0.25% per-trade cap, and tests a 10%-of-open-interest sizing limit. The low-volatility screen uses only earlier quarters after an eight-quarter warmup. The challenger is **top-two-decile calls selected by the old Kelly selector, 3–20% out of the money, with prior-quarter low volatility**. The selector still has its known future-label and contract-scoring defects, so this is a diagnostic, not a clean forward simulation.

| Rule | Hold, trading days | Capacity | Funded | Net P&L | ROI on premium | Calendar CAGR from settlements |
|---|---:|---|---:|---:|---:|---:|
| All previously selected | 20 | 10% OI | 47,663 | −$8.312M | −10.36% | −10.75% |
| All previously selected | 40 | 10% OI | 47,265 | −$5.255M | −9.28% | −4.66% |
| All previously selected | 60 | 10% OI | 45,773 | −$2.612M | −5.69% | −1.92% |
| Low-volatility moderate-OTM calls | 20 | 10% OI | 3,331 | −$0.179M | −1.58% | −0.12% |
| Low-volatility moderate-OTM calls | 40 | 10% OI | 3,247 | +$0.540M | +4.83% | +0.34% |
| Low-volatility moderate-OTM calls | 60 | 10% OI | 3,201 | +$1.102M | +10.67% | +0.68% |

The 60-day capped call rule earned $1.102M across 1998–2013, but $0.416M came from partial-year 2013, and the observed 2008–2012 years (2011 missing) lost $0.314M. Eleven of fifteen observed years were positive (unadjusted, independence-assuming one-sided sign-test p = 0.059). Its uncapped result of +$9.219M/+4.34% settlement CAGR depends heavily on orders far above the 10% OI limit. The 10% OI limit is a proxy, not a fill guarantee. The settlement CAGR is measured over calendar time from first funded entry to last funded exit using realized P&L; it is **not** a mark-to-market option return, Sharpe, or factor alpha. The audited result tables are in `results/options_robustness/`.

## Contract-choice grid

The final grid crosses calls on SUE deciles 9–10 and puts on deciles 1–2 with absolute entry deltas 0.30, 0.50, 0.70; expiration targets 75, 120, 180 calendar days; and holds 20, 40, 60 trading days. It buys at ask, sells at bid, values missing/zero bids at zero, and caps diagnostic lots at 10% of entry OI. Entry quotes must have positive bid, at least $0.10 ask, no more than 30% spread, and at least 100 OI; candidates must be within 25 days of the expiration target and 0.10 of the delta target. The planned exit must precede expiry by at least ten days and avoid data gaps. Direct cell ROI includes differing event cohorts; paired comparisons use the same event and direction.

The completed scan produced 54 cells and 240,862 contract/hold outcomes. The percentages below are **premium-weighted per-contract ROI, not annualized portfolio return**. Reusing an event in multiple cells is intentional; cells are alternatives, not additive trades.

| Contract/hold cell | Trades | ROI on premium | Positive years | Stability check |
|---|---:|---:|---:|---|
| Put, 75 DTE, 0.30 absolute delta, 60 days | 229 | +138.6% | Not a broad sample | 80 quoted zero bids; only the unusual 75-DTE contracts still alive at day 60 qualify. |
| Put, 120 DTE, 0.30 delta, 60 days | 3,979 | +27.0% | 3/15 | **−44.8% ROI outside 2007–08**; the crisis gains dominate. |
| Call, 120 DTE, 0.30 delta, 60 days | 4,796 | +17.8% | 11/15 | +0.5% ROI in 2008–12; partial 2013 ROI +158%. |
| Call, 75 DTE, 0.70 delta, 40 days | 5,535 | +9.3% | 10/15 | +9.3% ROI in 2008–12, but −31.7% in 2008. |

Absolute delta near 0.30 is generally out of the money, 0.50 near the money, and 0.70 in the money. These are delta targets rather than guarantees about each strike's exact spot moneyness. The full tables are `results/options_expiry_grid_final/grid_summary.csv` and `grid_yearly.csv`.

The top 1998–2007 cell with at least 1,000 trades was the 120-DTE, 0.30-delta put held 60 days (+28.0% ROI). It also made +31.4% in 2008–12, which superficially looks like a successful chronological test. The 2007 and 2008 crash years create that appearance; outside those two years this cell lost 44.8% of premium. This split was inspected after the full archive, so it is falsification evidence, not an untouched validation.

Same-event comparisons are more informative than the raw rankings. For 120-DTE puts held 60 days, the 0.30-delta contract had **2.98 percentage points lower mean ROI** than the 0.50-delta contract on the 2,402 events that offered both; its median difference was −12.3 points. Thus the 0.30-delta cell's higher aggregate ROI comes from event mix and rare large payoffs, not a consistent within-event advantage. For 0.70-delta calls held 40 days, 75 DTE beat 120 DTE by only **1.66 mean points on 913 matched events**, with a zero median difference. For 0.50-delta 120-DTE calls, holding 40 versus 20 days raised mean total ROI by 4.47 points, and holding 60 versus 40 raised it by 4.62 points, yet the shorter hold won on a majority of paired contracts in each comparison. Later holds are dominated by occasional large winners while many contracts decay. The matched tables are `paired_summary.csv` and `paired_yearly.csv`.

The grid therefore does **not** identify a reliable put strategy. The new sequential portfolio run is more useful than per-contract ROI: each cell is independently sized in entry-date order with a 20% outstanding-premium cap, 0.25% maximum entry budget, and 10%-of-OI contract cap. It buys at ask, sells at bid, assigns zero proceeds to zero or missing bids, and does not reuse proceeds from same-day exits.

Use only `results/options_expiry_grid_final/` and `results/options_expiry_grid_portfolio/` for conclusions. The older `options_expiry_grid/` and `options_expiry_grid_smoke_2003/` directories are retained for auditability but precede the spot-universe, explicit-drive-path, delta-tolerance, and paired-comparison fixes.

| Portfolio rule | Funded trades | Net P&L | Settlement CAGR | Later-period evidence |
|---|---:|---:|---:|---|
| **Top SUE calls, 120 DTE, 0.50 delta, hold 60 days** | 6,029 | +$4.179M | **+2.26%** | 10/15 positive observed years; 2008–12 lost $2.370M; partial 2013 earned $2.877M. |
| Top SUE calls, 120 DTE, 0.70 delta, hold 60 days | 5,669 | +$3.699M | +2.03% | 2008–12 stability remains weak. |
| Top SUE calls, 120 DTE, 0.30 delta, hold 60 days | 4,796 | +$3.107M | +1.74% | 2008–12 lost $2.826M; partial 2013 drives $2.812M. |
| Top SUE calls, 75 DTE, 0.70 delta, hold 40 days | 5,125 | +$1.894M | +1.12% | 8/15 positive observed years; 2008–12 lost $1.381M. |
| Bottom SUE puts, 120 DTE, 0.30 delta, hold 60 days | 3,953 | −$6.296M | −6.15% | 3/15 positive observed years. |

The implemented best historical rule is therefore: buy a **call** after a top-two-decile SUE event; choose the eligible contract nearest **120 calendar days to expiration** and **absolute delta 0.50**; buy at the ask; hold **60 OptionMetrics trading sessions**; require positive bid, ask of at least $0.10, spread no wider than 30% of ask, OI at least 100, a planned exit at least ten calendar days before expiration, and no coverage gap. Size positions sequentially under the stated capital/OI caps. The backtest rejects bottom-SUE puts under the same rules.

This is the best historical *settled-P&L* result within the frozen 54-cell portfolio grid, not proof of a future optimum: almost 69% of its total net P&L comes from partial-year 2013, and its 2008–12 performance was negative. It does not have a daily marked NAV, so it has no valid Sharpe, drawdown, beta, or factor alpha. Its aggregate output is in `results/options_expiry_grid_portfolio/portfolio_summary.csv` and `portfolio_yearly.csv`. The earlier low-volatility, 3–20% OTM call rule earns only 0.68% settlement CAGR before realistic impact and carries inherited selection leakage. Neither result supports live deployment.

## Decision rule and limits

The strongest *research candidate* is the 60-day, low-volatility, moderately OTM top-surprise call under the existing near-120-day expiry selector. **The supported live option allocation is zero** until a fixed call/put, delta, expiry, and hold rule passes the gates in `OPTIONS_VALIDATION_PROTOCOL_2026-10-21.md`. In particular, the backtests do not provide daily marked NAV, an executable Sharpe, factor alpha, quote-size/impact costs, a point-in-time earnings timestamp audit, or an untouched later sample. The entire 1998–2013 archive has been inspected during strategy discovery; a retrospective split can reveal instability but cannot restore independence. A finite historical search cannot prove a global future optimum for annualized return, Sharpe, and alpha simultaneously.

## Reproduction and data handling

From the retained worktree, use its `.venv` and point `--data-dir` to the existing derived data in the original checkout:

```powershell
 .\.venv\Scripts\python.exe -m pead.research.options_robustness --data-dir $env:PEAD_DATA_DIR --output-dir 'results\options_robustness'
 .\.venv\Scripts\python.exe -m pead.research.options_expiry_grid --data-dir $env:PEAD_DATA_DIR --optionmetrics-dir $env:OPTIONMETRICS_DIR --output-dir 'results\options_expiry_grid_final'
```

The grid streams yearly OptionMetrics files in place and writes only aggregate tables; it does not copy raw options data. Keep this worktree: deleting it may erase future ignored `data/` contents. Local compute completed the scan, so a Google Cloud VM is not required; a VM would also lack the physically attached drive without a licensed transfer or remote mount.
