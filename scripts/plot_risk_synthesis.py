"""Plot saved aggregate experiments; never read or copy licensed quote rows."""
from pathlib import Path
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]/'results/options_risk_synthesis')
    parser.add_argument('--development-trial',default='final_v6_development_all_suite')
    parser.add_argument('--pricing-trial',default='pricing_v1_development_options_suite')
    parser.add_argument('--linear-trial',default='linear_v2_development_equity_suite')
    args=parser.parse_args();root=args.root
    table=pd.read_csv(root/'comparison.csv')
    recipes=[
        ('final_v6_development_all_suite','carry_risk20','Risk20'),
        ('final_v6_development_all_suite','carry_risk20_vol10','Vol throttle'),
        ('final_v6_development_all_suite','carry_protected','Paid protection'),
        ('pricing_v1_development_options_suite','carry_rich_iv','IV screen'),
        ('final_v6_development_all_suite','equity_funded','Stock parent'),
        ('final_v6_development_all_suite','equity_cash80','Less stock / cash'),
        ('final_v6_development_all_suite','equity_hedged','Stock + puts'),
        ('final_v6_development_all_suite','equity_targets120','1.2x stock targets'),
        ('final_v6_development_all_suite','equity_borrow120','120% gross stock'),
        ('linear_v2_development_equity_suite','equity_beta_neutral','SPY beta hedge'),
        ('linear_v2_development_equity_suite','equity_beta_levered','Levered beta hedge')]
    replacements={'final_v6_development_all_suite':args.development_trial,
                  'pricing_v1_development_options_suite':args.pricing_trial,
                  'linear_v2_development_equity_suite':args.linear_trial}
    recipes=[(replacements[trial],policy,label) for trial,policy,label in recipes]
    required=[root/trial/f'{policy}_1000000/daily.csv.gz' for trial,policy,_ in
              [recipes[0],recipes[2],recipes[4],recipes[9],recipes[10]]]
    if not all(path.exists() for path in required):
        raise FileNotFoundError('Full per-account ledgers are required to regenerate charts; published figures '
                                'were preserved. Pass --root and trial names for generated local results.')
    fig,ax=plt.subplots(figsize=(12,6.5),layout='constrained')
    fig.patch.set_facecolor('#fafbfc');ax.set_facecolor('#fafbfc')
    ax.add_patch(Rectangle((0,12),20,2,facecolor='#e7f2e9',edgecolor='none',zorder=0))
    ax.text(1,13.2,'Requested growth/drawdown region\n(Sharpe target also required)',fontsize=9,color='#28633b')
    for i,(trial,policy,label) in enumerate(recipes):
        row=table[table.trial.eq(trial)&table.policy.eq(policy)&table.capital.eq(1_000_000)].iloc[0]
        color='#2775b6' if policy.startswith('carry') else '#15846b'
        code='O'+str(i+1) if i<4 else 'E'+str(i-3)
        ax.scatter(-row.max_drawdown*100,row.cagr*100,s=75,color=color,
                   label=f'{code}  {label}  |  Sharpe {row.excess_sharpe:.2f}')
        offset={'carry_rich_iv':(6,-13),'carry_risk20_vol10':(-24,2),
                'equity_hedged':(-20,-13),'equity_borrow120':(-17,8)}.get(policy,(5,7))
        ax.annotate(code,(-row.max_drawdown*100,row.cagr*100),xytext=offset,
                    textcoords='offset points',fontsize=9,color=color,fontweight='bold')
    ax.axhline(12,color='#b45c52',ls='--',lw=1);ax.axvline(20,color='#b45c52',ls=':',lw=1)
    ax.set(xlim=(0,42),ylim=(0,14),xlabel='Maximum drawdown magnitude (%)',
           ylabel='Net account CAGR (%)',title='Funded development experiments · 1999–2010 · $1M\n'
           'Risk reductions worked; the joint growth, Sharpe and drawdown objective did not\n'
           'Includes disclosed missing-price reserves and modeled execution')
    ax.grid(alpha=.15);ax.legend(loc='center left',bbox_to_anchor=(1.01,.5),frameon=False,fontsize=8)
    fig.savefig(root/'development_frontier.png',dpi=180,bbox_inches='tight')
    fig.savefig(root/'development_frontier.svg',bbox_inches='tight');plt.close(fig)
    svg=root/'development_frontier.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    fig,axes=plt.subplots(2,2,figsize=(12,7),sharex=True,layout='constrained')
    for col,chosen in enumerate([[recipes[0],recipes[2]],[recipes[4],recipes[9],recipes[10]]]):
        for trial,policy,label in chosen:
            d=pd.read_csv(root/trial/f'{policy}_1000000/daily.csv.gz',parse_dates=['date'])
            wealth=d.nav/1_000_000
            axes[0,col].plot(d.date,wealth,label=label,lw=1.1)
            drawdown=100*(wealth/np.maximum(1.,wealth.cummax())-1)
            axes[1,col].plot(d.date,drawdown,label=label,lw=1.1)
        axes[0,col].set_title('Options accounts' if col==0 else 'Equity and direct hedges')
        axes[0,col].legend(fontsize=8);axes[0,col].set_ylabel('Wealth / initial NAV')
        axes[1,col].set_ylabel('Drawdown (%)')
        for axis in axes[:,col]:axis.grid(alpha=.15)
    fig.suptitle('Simulated funded paths · Development evidence, not a forward performance claim\n'
                 'Includes disclosed missing-price reserves and modeled execution')
    fig.savefig(root/'development_paths.png',dpi=180);plt.close(fig)


if __name__=='__main__':main()
