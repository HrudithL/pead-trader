import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
SRC='C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/'
rng=np.random.default_rng(1)
def recipes(s):
    p=pd.read_parquet(SRC+s+'/positions.parquet'); p['an']=p.notional.abs(); p['liquidity_quintile']=p.liquidity_quintile.astype(float); p['size_quintile']=p.size_quintile.astype(float)
    p['bench']=p.groupby(['ann_quarter','size_quintile']).hold_ret.transform('mean')
    p['alpha']=p.side*(p.hold_ret-p.bench)*p.an-p.cost
    lo,hi=p.beta_252d.quantile([1/3,2/3])
    p['b']=np.where(p.beta_252d<lo,'low',np.where(p.beta_252d<hi,'mid','high'))
    p['l']=np.select([p.liquidity_quintile<=2,p.liquidity_quintile==3],['thin','mid'],'liq')
    p['h']=np.where(p.holding_days>60,'long','short'); p['sd']=np.where(p.side>0,'L','S')
    p['rc']=p.sd+'|'+p.l+'|'+p.b+'|'+p.h
    g=p.groupby(['ann_quarter','rc']).agg(a=('alpha','sum'),n=('an','sum')).reset_index()
    A=g.pivot(index='ann_quarter',columns='rc',values='a').fillna(0); N=g.pivot(index='ann_quarter',columns='rc',values='n').fillna(0)
    return A,N
def tstat(A,N):
    bp=(A/N.replace(0,np.nan)).fillna(0)  # per-quarter bp
    return bp.mean()/ (bp.std(ddof=1)/np.sqrt(len(bp)))
res={}
for s in ['extreme_picks','calm_weighted','balanced_leverage','calm_rank_tilt']:
    A,N=recipes(s); T=tstat(A,N); tgt='L|thin|low|long'
    print(s,'recipes',A.shape[1],'target t',round(T[tgt],2),'rank',int((T>T[tgt]).sum())+1,'max t',round(T.max(),2),'n recipes |t|>2:',int((T.abs()>2).sum()))
    # sign-flip null for max t
    bpv=(A/N.replace(0,np.nan)).fillna(0).values; nq=bpv.shape[0]; mx=[]
    for _ in range(4000):
        sg=rng.choice([-1,1],nq)[:,None]; x=bpv*sg
        mx.append((x.mean(0)/(x.std(0,ddof=1)/np.sqrt(nq))).max())
    mx=np.array(mx); print('   null max-t over 36 recipes: median',round(np.median(mx),2),'95th',round(np.percentile(mx,95),2),'; P(max>=target t)',(mx>=T[tgt]).mean())
    # raw (no cost-demeaning): fine
# Cross-strategy: how many independent?
ps=['extreme_picks','graded_bets','size_balanced','conviction_tilt','sector_balanced','balanced_leverage','calm_weighted','calm_rank_tilt','market_exposed']
S={}
for s in ps:
    A,N=recipes(s); S[s]=(A/N.replace(0,np.nan)).fillna(0)['L|thin|low|long']
S=pd.DataFrame(S); C=S.corr(); print(C.round(2).to_string()); ev=np.linalg.eigvalsh(C.values)[::-1]; print('eigs',ev.round(2),'effective N (participation)',round(ev.sum()**2/(ev**2).sum(),2))
S.to_csv('skeptic_best_recipe_quarterly_bp.csv')
# split halves: first 36 q vs last 36 q for target recipe in calm_weighted
x=S['calm_weighted']; print('calm_weighted target bp first half',round(x.iloc[:36].mean(),0),'second',round(x.iloc[36:].mean(),0),'last 24q',round(x.iloc[-24:].mean(),0), 't last 36', round(x.iloc[36:].mean()/(x.iloc[36:].std()/6),2), 'median q',round(x.median(),0))
