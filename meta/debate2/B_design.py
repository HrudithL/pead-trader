"""B_design.py: which design properties predict consistency; sleeve correlations; no-borrow combinations.
Reads B_scorecard.csv, B_daily_returns.parquet (made by B_score.py). Writes B_*.csv."""
import numpy as np, pandas as pd
from scipy import stats
O = 'C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-evaluation-roi-74c7dd/meta/debate2/'
sc = pd.read_csv(O + 'B_scorecard.csv', index_col=0)
R = pd.read_parquet(O + 'B_daily_returns.parquet')
pd.set_option('display.width', 250, 'display.max_columns', 50, 'display.float_format', lambda x: f'{x:.3f}')
W = R.loc['1998-01-01':]
rows = {}
for c in W:
    r = W[c].dropna()
    if r.std() == 0:
        continue
    k = 0.05 / (r.std() * np.sqrt(252)); s = r * k; nav = (1 + s).cumprod(); dd = nav / nav.cummax() - 1
    q = (1 + s).groupby(s.index.to_period('Q')).prod() - 1
    nr = (1 + r).cumprod()
    rows[c] = dict(vt_maxdd=dd.min(), vt_worst_q=q.min(), vt_worst12m=(nav / nav.shift(252) - 1).min(),
                   w_sharpe=r.mean() / r.std() * np.sqrt(252), w_pos_q=(q > 0).mean(),
                   w_ret=nr.iloc[-1] ** (252 / len(r)) - 1, w_maxdd=(nr / nr.cummax() - 1).min())
vt = pd.DataFrame(rows).T
vt.to_csv(O + 'B_voltarget_1998_2013.csv')

excl = ['legacy_blend', 'sector_balanced_max', 'options_cap20_midfill', 'options_cap30_midfill', 'options_worst_case',
        'INDEX_vwretd', 'DRIFT_CORE_cash_fill(approx)', 'hedged_blend', 'calm_blend', 'DRIFT_CORE_events+index',
        'options_cap20', 'options_cap30']
eq = sc.drop([e for e in excl if e in sc.index]).copy()
inv = ['calm_weighted', 'calm_weighted_filter', 'calm_weighted_matched', 'calm_rank_tilt', 'calm_rank_levered']
eq['invvol'] = eq.index.isin(inv).astype(int)
eq['is_max'] = eq.index.str.endswith('_max').astype(int)
eq['mkt_exposed'] = eq.index.isin(['market_boost', 'market_boost_max', 'market_exposed', 'market_exposed_max']).astype(int)
eq['sector_neutral'] = eq.index.isin(['sector_balanced', 'balanced_leverage'] + inv + ['market_boost', 'market_boost_max']).astype(int)
eq = eq.join(vt)
eq['log_npos'] = np.log(eq.avg_n_open_positions)
feats = ['avg_gross_over_nav', 'invvol', 'is_max', 'mkt_exposed', 'sector_neutral', 'log_npos', 'long_share_gross',
         'avg_hold_days', 'sector_hhi', 'size_hhi', 'net_over_gross']
targ = ['w_sharpe', 'w_pos_q', 'vt_maxdd', 'vt_worst_q', 'vt_worst12m', 'era_min_sharpe', 'sh_e3', 'roll3y_slope_per_yr',
        'top10q_share_logret', 'ret_ex_top10q']
res = []
for f in feats:
    for t in targ:
        x = eq[[f, t]].dropna()
        if len(x) > 6 and x[f].nunique() > 1:
            rho, p = stats.spearmanr(x[f], x[t]); res.append((f, t, len(x), rho, p))
rd = pd.DataFrame(res, columns=['feature', 'target', 'n', 'spearman', 'p'])
rd.to_csv(O + 'B_design_corr.csv', index=False)
print('N equity books', len(eq))
print(rd.pivot(index='feature', columns='target', values='spearman'))
print(rd[rd.p < 0.05].sort_values('p'))
cols_keep = ['ann_ret', 'sharpe', 'avg_gross_over_nav', 'invvol', 'is_max', 'mkt_exposed', 'sector_neutral', 'avg_n_open_positions',
             'long_share_gross', 'avg_hold_days', 'sector_hhi', 'size_hhi', 'net_over_gross', 'w_sharpe', 'vt_maxdd',
             'vt_worst_q', 'pct_pos_q', 'era_min_sharpe', 'sh_e3', 'roll3y_slope_per_yr']
eq[cols_keep].to_csv(O + 'B_design_table.csv')
print(eq[['avg_gross_over_nav', 'avg_n_open_positions', 'long_share_gross', 'avg_hold_days', 'sector_hhi', 'size_hhi', 'net_over_gross', 'w_sharpe', 'vt_maxdd', 'sh_e3']])
for g in ['invvol', 'is_max', 'mkt_exposed', 'sector_neutral']:
    print(g); print(eq.groupby(g)[['w_sharpe', 'w_pos_q', 'vt_maxdd', 'vt_worst_q', 'sh_e3', 'era_min_sharpe', 'roll3y_slope_per_yr']].mean())

cols = ['calm_weighted', 'calm_rank_tilt', 'balanced_leverage', 'market_boost', 'options_cap20', 'options_cap30',
        'options_cap20_midfill', 'DRIFT_CORE_events+index', 'INDEX_vwretd', 'DRIFT_CORE_cash_fill(approx)']
C = W[cols].dropna().corr(); C.to_csv(O + 'B_corr_daily.csv'); print(C.round(2))
Q = (1 + W[cols]).groupby(W.index.to_period('Q')).prod() - 1
print(Q.corr().round(2)[['DRIFT_CORE_events+index', 'calm_weighted', 'options_cap20']])
for a, b in [('1998', '2001'), ('2002', '2007'), ('2008', '2013')]:
    x = W.loc[a:b]
    print(a, b, 'cw~opt', round(x.calm_weighted.corr(x.options_cap20), 2), 'cw~DC', round(x.calm_weighted.corr(x['DRIFT_CORE_events+index']), 2),
          'cw~idx', round(x.calm_weighted.corr(x.INDEX_vwretd), 2), 'DC~idx', round(x['DRIFT_CORE_events+index'].corr(x.INDEX_vwretd), 2))

g = sc.loc['calm_weighted', 'avg_gross_over_nav']
print('calm_weighted avg gross/NAV', g)
ls = W.calm_weighted / g          # return per $1 of average gross capital (no borrow)
dc = W['DRIFT_CORE_events+index']; dcc = W['DRIFT_CORE_cash_fill(approx)']; opt = W.options_cap20; idx = W.INDEX_vwretd


def stat(r, n):
    r = r.dropna(); nav = (1 + r).cumprod(); q = (1 + r).groupby(r.index.to_period('Q')).prod() - 1
    sh = [r.loc[a:b].mean() / r.loc[a:b].std() * np.sqrt(252) for a, b in [('1998', '2001'), ('2002', '2007'), ('2008', '2013')]]
    return dict(name=n, ret=nav.iloc[-1] ** (252 / len(r)) - 1, vol=r.std() * np.sqrt(252), sharpe=r.mean() / r.std() * np.sqrt(252),
                maxdd=(nav / nav.cummax() - 1).min(), pos_q=(q > 0).mean(), worst_q=q.min(), sh1=sh[0], sh2=sh[1], sh3=sh[2],
                stress08_09=(1 + r.loc['2008':'2009']).prod() - 1)


combos = [('DriftCore', dc), ('DC cash-fill', dcc), ('LS unlev (calm_weighted/gross)', ls), ('index', idx), ('options_cap20', opt)]
for wl in [0.2, 0.4, 0.6]:
    combos.append((f'DC {1-wl:.0%} + LS_unlev {wl:.0%}', (1 - wl) * dc + wl * ls))
    combos.append((f'DCcash {1-wl:.0%} + LS_unlev {wl:.0%}', (1 - wl) * dcc + wl * ls))
combos.append(('DC80+opt20 (post-fix opt)', 0.8 * dc + 0.2 * opt))
combos.append(('DC60+LS30+opt10', 0.6 * dc + 0.3 * ls + 0.1 * opt))
combos.append(('DCcash50+index50', 0.5 * dcc + 0.5 * idx))
T = pd.DataFrame([stat(r, n) for n, r in combos]).set_index('name')
T.to_csv(O + 'B_combos_noborrow.csv'); print(T)
print('corr LS_unlev vs DC', ls.corr(dc), 'vs DCcash', ls.corr(dcc))
# all-pairs sleeve diversification: avg pairwise corr among LS-type equity books
eqb = ['balanced_leverage', 'calm_weighted', 'calm_rank_tilt', 'size_balanced', 'extreme_picks', 'graded_bets', 'conviction_tilt', 'sector_balanced']
cc = W[eqb].corr().values; print('avg pairwise corr among 8 neutral equity books', cc[np.triu_indices(8, 1)].mean())
