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



def render_candidate_paths(holdout_folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    fig,axes=plt.subplots(2,2,figsize=(12,6.7),layout='constrained',sharex='col')
    for col,(folder,title) in enumerate([
        (ROOT/'results/selective_earnings_index_settlement_risk_exit','Development 1999–2010'),
        (holdout_folder,'Frozen holdout 2012–August 2013')]):
        for capital,color in [(100000,'#1976b5'),(1000000,'#df7a22')]:
            d=pd.read_csv(folder/f'deployed_{capital//1000}k'/'daily.csv.gz',parse_dates=['date'])
            wealth=d.nav/capital
            axes[0,col].plot(d.date,wealth,color=color,label=f'${capital:,} account',lw=1.2)
            axes[1,col].plot(d.date,(wealth/wealth.cummax()-1)*100,color=color,lw=1)
        spy=(1+d.benchmark).cumprod()
        axes[0,col].plot(d.date,spy,color='#555555',label='S&P proxy',lw=1.2)
        axes[1,col].plot(d.date,(spy/spy.cummax()-1)*100,color='#555555',lw=1)
        axes[0,col].set_title(title)
        axes[0,col].legend(fontsize=8)
        for row in (0,1):axes[row,col].grid(alpha=.2)
        locator=mdates.AutoDateLocator(minticks=3,maxticks=5)
        axes[1,col].xaxis.set_major_locator(locator)
        axes[1,col].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        axes[1,col].tick_params(axis='x',labelsize=9)
    axes[0,0].set_ylabel('NAV / starting capital')
    axes[1,0].set_ylabel('Drawdown (%)')
    fig.suptitle('Frozen deployed spread ladder: high return with substantial interim losses')
    fig.savefig(OUT/'candidate_paths.png',dpi=160);plt.close(fig)


def render_figure(full):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(9,5.2),layout='constrained')
    groups=np.select([full.experiment.str.contains('settlement'),full.experiment.str.contains('carry'),
                      full.experiment.str.contains('put')],['SPX settlement','Continuous earnings calls','Cash-secured puts'],default='Other recorded designs')
    for label in np.unique(groups):
        subset=full[groups==label]
        ax.scatter(-subset.drawdown*100,subset.cagr*100,s=25,alpha=.65,label=label)
    ax.axhspan(12,15,color='#329a68',alpha=.14,label='12–15% return target')
    ax.axvline(30,color='#555555',ls='--',lw=1,label='Declared validation DD ceiling')
    ax.set(xlabel='Maximum development drawdown (%)',ylabel='Full development NAV CAGR (%)',
           title='Recorded development outcomes: return must be assessed with risk')
    ax.grid(alpha=.2);ax.legend(fontsize=8,loc='best')
    fig.savefig(OUT/'risk_return.png',dpi=160);plt.close(fig)


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
        'A high-risk candidate was frozen before evaluation on January 2012–August 30, 2013; that holdout is now consumed and reported separately above. '
        'Repeated redesigns have already consumed the earlier development data; none of these are a pristine new final holdout.','',
        '## Validation-selected profiles','',
        'Selections use validation Sharpe, minimum trade counts and declared drawdown limits. The v2 $100K case transfers the fixed $1M-selected profile; later families select separately by capital. '
        'A selection can still have negative returns. “None” means no profile cleared the selection filters. '
        'All percentages below are NAV CAGR after quoted entry/exit costs and commissions.','',
        '| Experiment/vintage | Capital | Selected profile | Validation CAGR | 2008–2010 CAGR | Full-history CAGR |',
        '|---|---:|---|---:|---:|---:|']
    def percent(value):return '—' if pd.isna(value) else f'{value:.2%}'
    for row in selected.to_dict('records'):
        lines.append(f'| {row["experiment"]} | ${row["capital"]:,} | {row["scenario"] if pd.notna(row["scenario"]) else "None"} | '
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
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.earnings_carry --intrinsic --market-gate --cash-sweep --capacity-ceiling',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_settlement --risk-exit',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_settlement --risk-exit --double-fee',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_settlement --risk-exit --zero-floor',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.index_holdout',
        '& ./.venv/Scripts/python.exe -m pead.earnings_options.research_audit',
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
    full=trials[trials.period=='all_development'].copy()
    canonical=full[~full.experiment.str.contains('boundary|pre_funding|zero_rf',regex=True)]
    numerical=canonical[canonical.cagr>=.12].sort_values('cagr',ascending=False)
    lower_dd=canonical[canonical.drawdown>=-.30].sort_values('cagr',ascending=False)
    snapshot=['## Current decision','',
        'The expanded research can clear the numerical return hurdle by deploying more short-volatility risk. '
        'That does not establish consistent annual profits or investment readiness. High-return profiles must be judged '
        'with their drawdowns, later-period returns, execution stresses and selection history.','',
        '| Development profile | Capital | Full CAGR | Full drawdown | 2008–2010 CAGR |',
        '|---|---:|---:|---:|---:|']
    focus=[('selective_earnings_index_settlement_risk_exit','deployed_100k'),
           ('selective_earnings_index_settlement_risk_exit','deployed_1000k'),
           ('selective_earnings_index_settlement_risk_exit','moderate_100k'),
           ('selective_earnings_index_settlement_risk_exit_double_fee','deployed_100k'),
           ('selective_earnings_index_settlement_risk_exit_zero_floor','deployed_100k'),
           ('selective_earnings_carry_intrinsic_trend_cash_ceiling','car5_100k')]
    for family,label in focus:
        match=canonical[(canonical.experiment==family)&(canonical.scenario==label)]
        if match.empty:continue
        row=match.iloc[0]
        later=trials[(trials.experiment==family)&(trials.scenario==label)&(trials.period=='development_test')]
        later_cagr=float(later.cagr.iloc[0]) if len(later) else np.nan
        snapshot.append(f'| {family} / {label} | ${int(row.capital):,} | {row.cagr:.2%} | {row.drawdown:.2%} | {percent(later_cagr)} |')
    snapshot+=['',
        'The declared validation selector retains a 30% drawdown ceiling. An aggressive profile failing that ceiling '
        'is a high-risk research candidate, not the validation-approved winner. Repeated development redesigns '
        'also mean these results are not pristine out-of-sample evidence.','',
        '![Actual development risk and return](risk_return.png)','',
        '## Settlement and implementation findings','',
        'Native SET SECID118963 supplies all 144 monthly opening settlements in 1999–2010. Traditional monthly '
        'SPX uses actual SOQ cash payoff, not the ordinary index close. Monthly historical Saturday expirations '
        'fall on days 16–22; an earlier menu incorrectly excluded day 22. The first January session now uses '
        'the actual December quote for prior Greeks and liquidity. Earlier vintages remain separately named.','',
        'The pure settlement ladder makes its cash-secured worst loss explicit and promises no early exit. '
        'The risk-exit version initiates liquidation when the existing lagged market trend is negative or lagged '
        'realized volatility exceeds30%; earnings breadth still gates entry. Exits are staged against the two legs and their '
        'shared prior-volume/OI capacity. They preserve pending exit intent and cannot spend collateral reserved '
        'for another position. Contracts still open at SOQ resolve to actual cash payoff.','',
        'Continuous single-name calls carry across calendar years, use prior-session Greeks or an explicitly '
        'Greek-free intrinsic selector, normalize costs to stock-equivalent value, and share capacity across '
        'eight strike/maturity tickets. The 80% ceiling variant allows extreme company concentration. '
        'It improves affordability, not the stock forecast.','']
    snapshot+=['',
        'For the index candidate,30% per ticket/cohort and80% aggregate are maximum-loss admission limits. '
        'They are not a cap on future drawdown or a continuously rebalanced fraction of current NAV. '
        'Cash secures contractual width, but several overlapping expiries can lose together. '
        'Risk-off liquidation can take several sessions because participation is constrained.','']
    annual_path=OUT/'candidate_annual_returns.csv'
    if annual_path.exists():
        annual=pd.read_csv(annual_path)
        dev=annual[annual.experiment=='selective_earnings_index_settlement_risk_exit']
        snapshot+=['## Annual consistency and independent cash audit','',
            'Long-run CAGR is not an annual floor. The fixed candidate has the following development-year outcomes:','',
            '| Account | Profitable years | Losing years | Worst calendar year |',
            '|---|---:|---:|---:|']
        for label,g in dev.groupby('scenario',sort=True):
            worst=g.loc[g.strategy_return.idxmin()]
            snapshot.append(f'| {label} | {int((g.strategy_return>0).sum())} | {int((g.strategy_return<0).sum())} | {int(worst.year)}: {worst.strategy_return:.2%} |')
        audit=json.loads((OUT/'settlement_trade_audit.json').read_text(encoding='utf-8'))
        snapshot+=['',f'Independent entry/exit price and native SET payoff audit: {audit["records"]} closed trade records, '
            f'maximum P&L discrepancy ${audit["maximum_absolute_pnl_gap"]:.10f}. '
            'This validates the implemented arithmetic against its source quotes; it does not validate quote-size availability or market impact.','']
    audit_path=OUT/'call_entry_audit_summary.csv'
    if audit_path.exists():
        audit=pd.read_csv(audit_path)
        snapshot+=['## Entry-price and liquidity audit','',
            'This is a post-run diagnostic; it does not filter or reprice historical entries. The native stock '
            'close can follow the option quote, so the intrinsic-value check is not an executable arbitrage test.','',
            '| Profile | Closed records | Ask below close-intrinsic by>1%stock | Records requiring one-lot floor | P&L on those records |',
            '|---|---:|---:|---:|---:|']
        for r in audit.itertuples():
            snapshot.append(f'| {r.experiment}/{r.scenario} | {r.closed_records} | {r.pricing_flags} | {r.low_volume_records} | ${r.low_volume_pnl:,.2f} |')
        snapshot+=['','PnL attributed to these records is not the portfolio return that would result from deleting them. '
            'Removing an order changes cash, later contract choices and compounding.','']
    incomplete=[]
    for folder in sorted((ROOT/'results').glob('selective_earnings*')):
        if not (folder/'comparison.csv').exists() and list(folder.glob('*polic*.json')):
            incomplete.append(folder.name)
    if incomplete:
        snapshot+=['## Preparation-only or incomplete runs','',
            'These preserved folders have policy metadata but no completed comparison table; they are excluded '
            'from the completed-trial count and must not be represented as measured portfolio performance.','',
            *[f'- {name}' for name in incomplete],'']
    holdout_folder=ROOT/'results/selective_earnings_index_holdout'
    holdout_path=holdout_folder/'comparison.csv'
    holdout_complete=(holdout_folder/'holdout_evaluated.json').exists() and holdout_path.exists()
    holdout=pd.read_csv(holdout_path) if holdout_complete else pd.DataFrame()
    if holdout_complete:
        snapshot+=['## Frozen holdout: January 2012–August 30, 2013','',
            'The deployed 30/30/80% risk profile was fixed before this evaluation, including code hashes, '
            'both capitals, actual SET and capacity-controlled market-risk exits. Native options and SET both '
            'end August 30, 2013; the S&P comparator ends on the same day. 2011 has no native option extract, '
            'so the first January 2012 weekly entry has no prior Greeks and is safely skipped. '
            'No policy was retuned after viewing the holdout. The original 30% drawdown selector still rejects '
            'this candidate; a high holdout return does not override its risk failure.','',
            '| Capital | Holdout CAGR | Drawdown | S&P proxy CAGR | Distinct entry weeks |',
            '|---|---:|---:|---:|---:|']
        for r in holdout.itertuples():
            snapshot.append(f'| ${int(r.capital):,} | {r.cagr:.2%} | {r.drawdown:.2%} | {r.spy_cagr:.2%} | {r.trade_count} |')
        risk_stats=[]
        for r in holdout.itertuples():
            summary=json.loads((holdout_folder/r.scenario/'summary.json').read_text(encoding='utf-8'))
            stats=summary['periods']['final_holdout']
            risk_stats.append((r.scenario,stats))
        snapshot+=['',
            'Risk-adjusted comparison: the S&P proxy had9.69% maximum drawdown and Sharpe1.52. '
            'The candidate had Sharpe0.86/0.81 and estimated annual market alpha of−11.86%/−15.12% '
            'at100K/1M. Its higher raw CAGR therefore does not establish positive risk-adjusted alpha. '
            'The short-sample block-bootstrap intervals are wide and include negative CAGR and alpha; '
            'see the saved holdout summaries.','']
        snapshot+=['',
            'Both holdout ledgers have zero missing marks and zero terminal reserve exits. The large holdout '
            'drawdowns are therefore quoted option liabilities, not missing-data write-offs. '
            'For example, the $100K account fell from a $117,862 peak to $63,960 on June 4, 2012 before recovering. '
            'The two-year partial sample is short and mostly a rising equity market; it does not establish '
            'consistent annual returns, current-market viability or immunity to another crash.','',
            '![Actual candidate wealth and drawdowns](candidate_paths.png)','']
        render_candidate_paths(holdout_folder)
    lines[2:2]=snapshot
    render_figure(canonical)
    (OUT/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    verdict=dict(target_cagr=[.12,.15],tested_capitals=[100000,1000000],
                 final_holdout_evaluated=holdout_complete,investment_accepted=False,
                 frozen_holdout_results=holdout.to_dict('records') if holdout_complete else [],
                 reason='Development results, data provenance and execution assumptions do not establish a deployable edge.',
                 numerical_full_history_hits=numerical[['experiment','scenario','capital','cagr','drawdown']].to_dict('records'),
                 best_cagr_with_drawdown_at_most30pct=float(lower_dd.cagr.max()) if len(lower_dd) else None)
    (OUT/'verdict.json').write_text(json.dumps(verdict,indent=2))
    print(json.dumps(dict(scenarios=len(trials[['experiment','scenario']].drop_duplicates()),period_rows=len(trials),
                         max_cash_gap=float(reconciliation.reconciliation_gap.abs().max()),verdict=verdict),indent=2))

if __name__=='__main__':main()
