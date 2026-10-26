"""Compile every earnings-options development trial, including rejected vintages."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'results/selective_earnings_research'

def compile_trials():
    parts=[];selected=[];reconciliations=[]
    for folder in sorted((ROOT/'results').glob('selective_earnings*')):
        table=folder/'comparison.csv'
        if not table.exists():continue
        t=pd.read_csv(table);t['experiment']=folder.name
        if 'capital' not in t:
            t['capital']=np.where(t.scenario.str.endswith('_100k'),100000,1000000)
        parts.append(t)
        path=folder/'validation_selection.json'
        if path.exists():
            meta=json.loads(path.read_text());choices=meta.get('selected_by_validation',{})
            transferred=isinstance(choices,str)
            if transferred:choices={'1000000':choices,'100000':choices+'_100k'}
            for capital,label in (choices or {}).items():
                row=dict(experiment=folder.name,capital=int(capital),scenario=label,selection_basis='fixed $1M policy transfer' if transferred and int(capital)==100000 else 'validation selection',
                         final_holdout_evaluated=bool(meta.get('final_holdout_evaluated',False)))
                for period,prefix in [('validation','validation'),('development_test','test'),('all_development','full')]:
                    sub=t[(t.scenario==label)&(t.period==period)]
                    if len(sub):
                        for col in ('cagr','drawdown','trade_count','spy_cagr'):row[f'{prefix}_{col}']=sub.iloc[0][col]
                selected.append(row)
        for scenario in t.scenario.unique():
            capital=int(t.loc[t.scenario==scenario,'capital'].iloc[0]);sub=folder/scenario
            dp=sub/'daily.csv.gz';tp=sub/'trades.csv.gz'
            if not(dp.exists() and tp.exists()):continue
            d=pd.read_csv(dp);tr=pd.read_csv(tp)
            interest=float(d.cash_interest.sum()) if 'cash_interest' in d else 0.
            pnl=float(tr.pnl.sum()) if 'pnl' in tr else 0.
            final=float(d.nav.iloc[-1]);gap=final-capital-pnl-interest
            reconciliations.append(dict(experiment=folder.name,scenario=scenario,capital=capital,final_nav=final,
                net_option_pnl=pnl,cash_interest=interest,reconciliation_gap=gap,
                terminal_reserve_exits=int(tr.exit_is_worst_loss_reserve.sum()) if 'exit_is_worst_loss_reserve' in tr else 0))
    if not parts:raise ValueError('no completed trial tables')
    return pd.concat(parts,ignore_index=True),pd.DataFrame(selected),pd.DataFrame(reconciliations)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    trials,selected,reconciliation=compile_trials()
    trials.to_csv(OUT/'all_trials.csv',index=False)
    selected.to_csv(OUT/'validation_selected.csv',index=False)
    reconciliation.to_csv(OUT/'cash_reconciliation.csv',index=False)
    if len(reconciliation) and reconciliation.reconciliation_gap.abs().max()>1e-5:
        raise ValueError('portfolio cash reconciliation failed')
    lines=['# Earnings-options research: both account sizes','',
        'This is the complete development-trial register. No failed profile or prior result vintage has been removed. '
        'A numerically attractive later-window return is not a validated investment strategy. '
        'The original source and ignored caches remain in their worktree; raw OptionMetrics files remain read-only on D:.','',
        f'Completed register: {trials[["experiment","scenario"]].drop_duplicates().shape[0]} scenario/vintage pairs, '
        f'{len(trials)} period observations. Vintages share data and are not independent experiments. '
        f'Cash reconciliation maximum absolute residual: ${reconciliation.reconciliation_gap.abs().max():.8f}.','',
        'Chronology: 1999–2004 discovery, 2005–2007 validation, 2008–2010 later development test. '
        'SPY options only existed from January 10, 2005, so SPY call history starts in 2005. '
        '2012–2013 has not been used for portfolio evaluation or selection. '
        'Repeated redesigns have already consumed the earlier development data; none of these are a pristine new final holdout.','',
        '## Validation-selected profiles','',
        'Selections use validation Sharpe, minimum trade counts and declared drawdown limits. The v2 $100K case transfers the fixed $1M-selected profile; later families select separately by capital. '
        'A selection can still have negative returns. “None” means no profile cleared the selection filters. '
        'All percentages below are NAV CAGR after quoted entry/exit costs and commissions.','',
        '| Experiment/vintage | Capital | Selected profile | Validation CAGR | 2008–2010 CAGR | Full-history CAGR |',
        '|---|---:|---|---:|---:|---:|']
    def percent(value):return '—' if pd.isna(value) else f'{value:.2%}'
    for row in selected.to_dict('records'):
        lines.append(f'| {row["experiment"]} | ${row["capital"]:,} | {row["scenario"] or "None"} | '
                     f'{percent(row.get("validation_cagr",np.nan))} | {percent(row.get("test_cagr",np.nan))} | '
                     f'{percent(row.get("full_cagr",np.nan))} |')
    lines+=['','## Why utilization and returns are low','',
        'The original $1M portfolio made only 25 directional trades in 1999–2010, held about 99.97% cash on average, '
        'and lost 0.056% annualized. In the 2007 audit, 8,685 events had raw option quotes; 2,034 survived the old '
        'premium/spread/OI/volume filters, but only 143 had the fixed two-leg delta pair. '
        'That is a contract-construction bottleneck, not proof that the rejected opportunities were profitable.','',
        'The v2 redesign expanded to 317,562 actual quoted candidate outcomes, alternative directions and holding rules, '
        'whole-structure friction screens, and prior-matured payoff forecasts. It generated hundreds to thousands of trades '
        'but lost money. Independent candidate labels reconcile all 2,838 balanced trades within $2e-12 per trade. '
        'More activity therefore exposed weak unit economics rather than curing them.','',
        'Small accounts cannot buy fractional contracts; a $3,000 premium limit excludes an otherwise eligible $4,000 call. '
        'Larger accounts can buy that call but may face only one or a few assumed executable contracts. '
        'Thus the two accounts select different contracts and do not earn scaled copies of the same return. '
        'Capacity-aware selection improves allocation but does not create an underlying forecasting edge.','',
        'Long options pay time value and implicit financing, and bear the volatility change after earnings. '
        'Short options receive that premium while retaining adverse stock and volatility risk. '
        'A quoted spread costs money on entry and exit; a higher win rate or a large return on premium is insufficient. '
        'Portfolio CAGR must include idle cash, simultaneous positions, integer lots, all losses and costs.','',
        '## What was changed','',
        '- Flexible debit/credit verticals and condors; direct net-payoff learning from only prior-matured observations.',
        '- Native SPX credit spreads, explicit delta-targeted long-dated spreads, fully paid SPY stock-replacement calls.',
        '- Cash-secured post-earnings puts with a lagged non-event volatility forecast, split-consistent prior prices, '
        'quoted exit-friction proxy and explicit downside-jump stress.',
        '- Fully paid deep-ITM calls on positive earnings-reaction stocks; three fixed news thresholds, two funding budgets, '
        'and a fixed market-risk gate sensitivity.',
        '- Shared same-day entry capacity, feasible contract selection at both capitals, current-value risk budgets and partial integer-lot trims.',
        '- Missing quotes distinguished from executable exits: pessimistic reserve marks, entry freeze, deferred exits and a terminal contract boundary.','',
        'A crossed March 17, 2005 SPY call market (e.g. option 23955057 bid35.5/ask35.4) previously caused immediate '
        'permanent full-premium write-offs. Deferred exits avoid claiming that such a sale occurred. '
        'They retain pessimistic NAV reserves and fail execution validation. Those reserve drawdowns must not be presented '
        'as measured tradable market drawdowns. Earlier immediate-reserve vintages remain in this register.','',
        '## Limits that prevent an investment claim','',
        '- EOD bid/ask is a simulated taker-execution assumption; intraday quote size, latency and market impact are not validated. '
        'Entry participation does not prove that an accumulated position can exit at the same participation rate.',
        '- Single-name source links are conditional and historical EPS share bases are mostly assumed. '
        'The price-only designs avoid EPS dependence but do not repair the entire historical universe.',
        '- A cash-secured American put reserves the full strike cash. Its stock handoff on early assignment, dividends '
        'and corporate-action deliverables still require a complete path model. The European zero-rate screen is an approximation.',
        '- Long call buyers have no short-option assignment liability. SPY eligibility uses prior-known stock prices; '
        'same-day stock closes are used only for mark plausibility, not entry selection.',
        '- Missing-mark diagnostics count marking calls, not unique bad dates. Terminal reserves remain explicitly flagged.',
        '- Cash-sweep sensitivities use a full-prior-month Treasury-rate proxy with a 10bp/year allowance. '
        'Interest is separately reconciled and is not option alpha.',
        '- Historical data end in 2013. These tests do not establish performance in the current market.','',
        '## Reproduction','',
        'Use the worktree Python 3.12 / pandas3 environment. All output paths are under this worktree. '
        'Do not remove, archive, prune or clean the worktree: ignored caches are research assets.','',
        '```powershell',
        '& ./.venv/Scripts/python.exe -m pytest tests -q',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.research_v2 --help',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_research',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_directional',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_directional --cash-sweep',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_calls',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_calls --capacity-selection',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.cash_puts',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.earnings_calls',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.earnings_calls --funding-stress',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.earnings_calls --market-gate',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.earnings_calls --funding-stress --market-gate',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.research_report',
        '```','',
        '## References and interpretation','',
        '[OptionMetrics vendor manual](https://lib.tsinghua.edu.cn/PDF/OptionMetricsshiyongzhinan.pdf) '
        'establishes contract adjustment and lagged open-interest semantics. '
        '[Cboe SPY listing date](https://datashop.cboe.com/faqs) prevents a fictitious pre-2005 SPY option history. '
        '[Cboe SPX specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/) '
        'support the European cash-settled index design and pre-expiry exit buffers.','',
        '[Chung and Louis](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2886040) motivate post-announcement volatility overpricing; '
        'their finding does not establish this implementation\'s investable CAGR. '
        '[OIC cash-secured puts](https://www.optionseducation.org/strategies/all-strategies/cash-secured-put) '
        'explains full stock-purchase cash and assignment. '
        '[French factor documentation](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library/f-f_factors.html) '
        'documents the Treasury proxy.','',
        'The user\'s 12–15% target is an absolute NAV return hurdle at either $100K or $1M. '
        'Contemporaneous SPY returned about -2.78% annualized in 2008–2010; beating that period alone is insufficient. '
        'A low positive CAGR still makes nominal money; it can nevertheless miss the objective by a large margin. '
        'For example, 3% CAGR compounds $100K to about $142.6K over twelve years, versus $535.0K at15%. '
        'Inflation and taxes are not deducted from that illustration.','']
    (OUT/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    verdict=dict(target_cagr=[.12,.15],tested_capitals=[100000,1000000],
                 final_holdout_evaluated=bool(selected.final_holdout_evaluated.any()),investment_accepted=False,
                 reason='Development results, data provenance and execution assumptions do not establish a deployable edge.')
    (OUT/'verdict.json').write_text(json.dumps(verdict,indent=2))
    print(json.dumps(dict(scenarios=len(trials[['experiment','scenario']].drop_duplicates()),period_rows=len(trials),
                         max_cash_gap=float(reconciliation.reconciliation_gap.abs().max()),verdict=verdict),indent=2))

if __name__=='__main__':main()
