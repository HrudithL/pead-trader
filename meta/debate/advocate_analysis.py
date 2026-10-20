import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/results/"
OUT="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-evaluation-roi-74c7dd/meta/debate/"
EQ=['extreme_picks','graded_bets','size_balanced','conviction_tilt','sector_balanced','balanced_leverage','calm_weighted','calm_rank_tilt','market_exposed']
def era(y): return '96-01' if y<=2001 else ('02-07' if y<=2007 else '08-13')
U=pd.read_parquet(SRC+'graded_bets/positions.parquet',columns=['ann_quarter','size_quintile','hold_ret'])
bench=U.groupby(['ann_quarter','size_quintile']).hold_ret.mean().rename('bench')
def prep(s):
    d=pd.read_parquet(SRC+f'{s}/positions.parquet')
    d=d.join(bench,on=['ann_quarter','size_quintile'])
    d=d.dropna(subset=['liquidity_quintile','beta_252d','size_quintile','hold_ret']).copy()
    d['an']=d.notional.abs()
    d['liquidity_quintile']=d.liquidity_quintile.astype(int)
    d['alpha']=d.side*(d.hold_ret-d.bench)*d.an
    d['alpha_net']=d.alpha-d.cost
    liq=d.liquidity_quintile
    d['liq']=np.where(liq<=2,'thin',np.where(liq==3,'mid','liquid'))
    d['beta_b']=pd.qcut(d.beta_252d,3,labels=['lo','mid','hi'])
    d['hold']=np.where(d.holding_days>60,'long>60','short<=60')
    d['sd']=np.where(d.side>0,'long','short')
    d['yr']=d.ann_quarter.dt.year; d['era']=d.yr.map(era)
    return d
def stats(g):
    q=g.groupby('ann_quarter').agg(a=('alpha_net','sum'),n=('an','sum'))
    bpq=q.a/q.n*1e4
    e=g.groupby('era')[['alpha_net','an']].sum(); e=e.alpha_net/e.an*1e4
    return dict(pos=len(g),bp_net=g.alpha_net.sum()/g.an.sum()*1e4,t_q=bpq.mean()/(bpq.std()/np.sqrt(len(bpq))) if len(bpq)>2 else np.nan,
        q_pos=(q.a>0).mean(),nq=len(q),e96=e.get('96-01',np.nan),e02=e.get('02-07',np.nan),e08=e.get('08-13',np.nan),real_M=g.net_pnl.sum()/1e6,alpha_net_M=g.alpha_net.sum()/1e6,real_bp=g.net_pnl.sum()/g.an.sum()*1e4)
rows=[];P={}
for s in EQ:
    d=P[s]=prep(s)
    tot=d.an.sum()
    for k,g in d.groupby(['sd','liq','beta_b','hold'],observed=True):
        r=stats(g);r['pct_money']=g.an.sum()/tot;r['strategy']=s;r['recipe']=' | '.join(map(str,k));rows.append(r)
R=pd.DataFrame(rows);R.to_csv(OUT+'advocate_recipes.csv',index=False)
R['allpos']=(R[['e96','e02','e08']]>0).all(axis=1)
robust=R[(R.t_q>=2)&(R.allpos)&(R.pct_money>0.01)]
print("=== recipes t>=2 AND net-positive in all 3 eras, count across 9 strategies ===")
print(robust.groupby('recipe').agg(n=('strategy','count'),bp=('bp_net','mean'),t=('t_q','mean'),qpos=('q_pos','mean'),real_bp=('real_bp','mean'),money=('pct_money','mean')).sort_values('n',ascending=False).head(10).round(3).to_string())
print("=== recipes with t<=-1.5 (consistent losers) count ===")
lo=R[(R.t_q<=-1.5)]
print(lo.groupby('recipe').agg(n=('strategy','count'),bp=('bp_net','mean'),real_bp=('real_bp','mean'),money=('pct_money','mean')).sort_values('n',ascending=False).head(6).round(3).to_string())
key='long | thin | lo | long>60'
print("=== key recipe per strategy ===")
print(R[R.recipe==key][['strategy','pct_money','bp_net','t_q','q_pos','e96','e02','e08','real_M','real_bp']].round(3).to_string())
rows=[]
for s in EQ:
    d=P[s]
    for nm,m in {'long thin lo >60':(d.sd=='long')&(d.liq=='thin')&(d.beta_b=='lo')&(d.hold=='long>60'),
      'long lo-beta >60':(d.sd=='long')&(d.beta_b=='lo')&(d.hold=='long>60'),
      'long lo-beta':(d.sd=='long')&(d.beta_b=='lo'),
      'all longs':(d.sd=='long'),'all shorts':(d.sd=='short'),
      'shorts hi-beta':(d.sd=='short')&(d.beta_b=='hi'),
      'shorts lo/mid beta':(d.sd=='short')&(d.beta_b!='hi'),
      'ALL ex hi-beta shorts':~((d.sd=='short')&(d.beta_b=='hi')),
      'ALL':d.an>0}.items():
        g=d[m]
        if len(g)==0: continue
        r=stats(g);r['strategy']=s;r['cut']=nm;r['pct_money']=g.an.sum()/d.an.sum();rows.append(r)
C=pd.DataFrame(rows);C.to_csv(OUT+'advocate_cuts.csv',index=False)
print(C[C.strategy.isin(['calm_weighted','calm_rank_tilt','balanced_leverage','extreme_picks'])][['strategy','cut','pct_money','bp_net','t_q','q_pos','e96','e02','e08','real_M']].round(2).to_string())

print("=== (2) exposure & rescale ===")
def ann(r):
    r=r.dropna(); yrs=len(r)/252; return (1+r).prod()**(1/yrs)-1, r.std()*np.sqrt(252)
def mdd(r): n=(1+r).cumprod(); return (n/n.cummax()-1).min()
rows=[];D={}
for s in EQ:
    x=pd.read_csv(SRC+f'{s}/daily.csv.gz',parse_dates=['date']).set_index('date'); D[s]=x
    g=(x.gross_exposure/x.nav.shift(1))
    act=g[x.n_open_positions>0]
    r=x.daily_return
    k=min(1.0,1/g.max())
    gg=g.fillna(0).clip(lower=1e-9)
    clip=r*np.minimum(1,1/gg)
    a0,v0=ann(r);a1,v1=ann(r*k);ac,vc=ann(clip)
    rows.append(dict(strategy=s,mean_active_gross=act.mean(),p99=g.quantile(.99),peak=g.max(),ret=a0,vol=v0,sharpe=a0/v0,mdd=mdd(r),k_peak=k,ret_peakcap=a1,sharpe_peakcap=a1/v1,mdd_peakcap=mdd(r*k),ret_dailyclip=ac,sharpe_clip=ac/vc,mdd_clip=mdd(clip)))
E=pd.DataFrame(rows);E.to_csv(OUT+'advocate_exposure.csv',index=False)
print(E.round(3).to_string())

op=pd.read_parquet(SRC+'options_cap20/positions.parquet')
od=pd.read_csv(SRC+'options_cap20/daily.csv.gz',parse_dates=['date']).set_index('date')
dates=od.index
prem=pd.Series(0.0,index=dates)
st=pd.to_datetime(op.day0_date); en=st+pd.Timedelta(days=84)
for a,b,p in zip(st,en,op.premium_paid):
    prem[(dates>=a)&(dates<b)]+=p
og=(prem/od.nav.shift(1))
print("options trades",len(op),"; premium outstanding/NAV mean(active)",og[og>0].mean().round(3),"p99",og.quantile(.99).round(3),"peak",og.max().round(3))

ret={}
for nm,f in {'opt20':'options_cap20','opt30':'options_cap30','optworst':'options_worst_case','cw':'calm_weighted','crt':'calm_rank_tilt','bl':'balanced_leverage','hb':'hedged_blend','cb':'calm_blend','opt20_legacy':'options_cap20_legacy'}.items():
    ret[nm]=pd.read_csv(SRC+f'{f}/daily.csv.gz',parse_dates=['date']).set_index('date').daily_return
Rr=pd.DataFrame(ret).fillna(0)
print("=== (4) correlations ===")
for e in ['cw','crt']:
    for o in ['opt20','optworst','opt20_legacy']:
        m=(1+Rr[[e,o]]).resample('ME').prod()-1
        q=(1+Rr[[e,o]]).resample('QE').prod()-1
        print(e,o,'daily',round(Rr[e].corr(Rr[o]),3),'monthly',round(m[e].corr(m[o]),3),'qtr',round(q[e].corr(q[o]),3))
def sh(r): return r.mean()/r.std()*np.sqrt(252)
for c in ['cw','crt','opt20','optworst','opt30','hb','cb']:
    out=[]
    for lab,(a,b) in {'96-01':('1996','2001'),'02-07':('2002','2007'),'08-13':('2008','2013')}.items():
        r=Rr[c][a:b]; out.append(f"{lab}: sh {sh(r):.2f} ann {((1+r).prod()**(252/len(r))-1)*100:.1f}%")
    print(c,' | '.join(out))
Y=((1+Rr[['cw','crt','opt20','optworst']]).resample('YE').prod()-1)*100
Y.index=Y.index.year;print(Y.round(1).T.to_string())
print("annual corr cw/opt20",round(Y.cw.corr(Y.opt20),2),"; cw<0 yrs",list(Y.index[Y.cw<0]),"opt20 then",Y.opt20[Y.cw<0].round(1).tolist(),"; opt20<0 yrs",list(Y.index[Y.opt20<0]),"cw then",Y.cw[Y.opt20<0].round(1).tolist())
# options by year dollars: how concentrated?
od['pnl']=od.daily_pnl
yp=od.pnl.groupby(od.index.year).sum()/1e6
print("opt20 net pnl $M by year:",yp.round(2).to_dict()," top2 share",round(yp.sort_values().iloc[-2:].sum()/yp.sum(),2))
ge=(D['calm_weighted'].gross_exposure/D['calm_weighted'].nav.shift(1))
print("cw peak gross",ge.max().round(2))
print("=== blends ===")
for eqn in ['cw','crt']:
  for oo in ['opt20','optworst']:
    for we,wo in [(1,0),(0,1),(0.8,1.6),(0.5,1.0),(0.5,2.0)]:
        r=we*Rr[eqn]+wo*Rr[oo]
        eg=we*ge.reindex(r.index).fillna(0)+wo*og.reindex(r.index).fillna(0)
        k=min(1,1/eg.max()); rr=r*k
        print(eqn,oo,we,wo,'ann',round(ann(r)[0]*100,2),'sh',round(sh(r),2),'mdd',round(mdd(r)*100,1),'| peakgross',round(eg.max(),2),'-> k',round(k,3),'ann',round(ann(rr)[0]*100,2),'sh',round(sh(rr),2),'mdd',round(mdd(rr)*100,1))
print("=== (3) capacity ===")
rows=[]
for s in ['extreme_picks','graded_bets','calm_weighted','balanced_leverage']:
    d=P[s]
    m=(d.sd=='long')&(d.liq=='thin')&(d.beta_b=='lo')&(d.hold=='long>60')
    g=d[m].copy(); g['cap5']=0.05*g.avg_dollar_volume_21d
    y=g.groupby('yr').agg(n=('an','size'),deployed=('an','sum'),cap5=('cap5','sum'),adv_med=('avg_dollar_volume_21d','median'))
    y['deployed']/=1e6;y['cap5']/=1e6
    y['net_alpha_M']=g.groupby('yr').alpha_net.sum()/1e6
    y['bp']=g.groupby('yr').alpha_net.sum()/g.groupby('yr').an.sum()*1e4
    y['strategy']=s;rows.append(y.reset_index())
    print(s,'positions',len(g),'median pos $k',round(g.an.median()/1e3,1),'median ADV $M',round(g.avg_dollar_volume_21d.median()/1e6,2),'share at 5%ADV cap',round((g.an>=0.0495*g.avg_dollar_volume_21d).mean(),3))
CAP=pd.concat(rows);CAP.to_csv(OUT+'advocate_capacity.csv',index=False)
for s in ['extreme_picks','graded_bets']:
    print(CAP[CAP.strategy==s].round(2).to_string(index=False))
d=P['graded_bets']
for nm,dec in {'D10':[10],'D9-10':[9,10],'D8-10':[8,9,10]}.items():
    m=(d.sd=='long')&(d.liq=='thin')&(d.beta_b=='lo')&(d.hold=='long>60')&(d.decile.isin(dec))
    g=d[m]
    print(nm,'events/yr',round(len(g)/18,1),'5%ADV $M/yr',round(0.05*g.avg_dollar_volume_21d.sum()/18/1e6,1),'; avg hold d',round(g.holding_days.mean()))
print("lowbeta tercile cutoffs (calm_weighted):",P['calm_weighted'].beta_252d.quantile([.33,.67]).round(2).tolist())
