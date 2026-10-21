import pandas as pd, numpy as np, itertools
SRC='C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/'
r=pd.read_csv(SRC+'profiles_recipes.csv')
best=r[r.recipe.str.contains('long \| thin \| low-beta \| long hold',regex=True)]
print(best[['strategy','positions','pct_dollars','bp','t','pct_q_pos','net_bp','eras_neg']].round(3).to_string())
for s in ['extreme_picks','calm_weighted']:
    rr=r[r.strategy==s].sort_values('t',ascending=False); print(s); print(rr[['recipe','pct_dollars','bp','t']].head(5).round(2).to_string())
p=pd.read_parquet(SRC+'extreme_picks/positions.parquet'); tgt=r[(r.strategy=='extreme_picks')]
print(len(p), tgt.positions.sum())
x=tgt[tgt.recipe.str.contains('long \| thin \| low-beta \| long hold',regex=True)].iloc[0]
print('target positions',x.positions,x.pct_dollars)
p['an']=p.notional.abs()
for thin in [[1],[1,2]]:
  for bcut in [(0.6,1.0),(0.8,1.2),(0.7,1.1),'terc']:
    if bcut=='terc': lo,hi=p.beta_252d.quantile([1/3,2/3])
    else: lo,hi=bcut
    m=(p.side>0)&p.liquidity_quintile.isin(thin)&(p.beta_252d<lo)&(p.holding_days>60)
    print(thin,bcut,m.sum(),round(p.an[m].sum()/p.an.sum(),4))
