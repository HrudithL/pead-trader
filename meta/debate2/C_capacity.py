"""Perspective C part 3: capacity.  (a) strategy-level scale curve under the realistic cost model, NAV at which return halves,
(b) per-recipe deployable dollars/yr at 5% ADV and net bp/$ after realistic costs.   -> C_capacity_strategy.csv, C_capacity_curve.csv, C_capacity_recipes.csv"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from C_common import *
KS = [0.002,0.005,0.01,0.02,0.05,0.1,0.25,0.5,1,2,4,8,16,32,64]
rows=[]; curve=[]
for n in sorted(os.listdir(V+"runs")):
    if not os.path.exists(V+f"runs/{n}/positions.parquet") or n.startswith("options") or n.endswith("_legacy") or "midfill" in n: continue
    d,j=load_run(n); pos=pd.read_parquet(V+f"runs/{n}/positions.parquet")
    _,gap=rf_daily(d.date); yrs=gap.sum()/365.25
    nav_mean=d.nav.mean(); an=pos.notional.abs().to_numpy(dtype=float); adv=pos.avg_dollar_volume_21d.to_numpy(dtype=float,na_value=np.nan); cap=0.05*adv
    capped=(an>=0.98*cap)|np.isnan(cap)
    gpnl=pos.ret_on_notional.to_numpy(dtype=float)   # gross return per notional dollar
    sig=pos.tvol_60d.fillna(pos.tvol_60d.median()).to_numpy()
    base=[]
    for k in KS:
        nk=np.where(capped,an,np.minimum(an*k,np.where(np.isnan(cap),an*k,cap)))
        nk=np.where(capped & (k<1), an*k, nk)                      # scaling down: everything shrinks
        nk=np.where(k<1, an*k, nk)
        p=pos.assign(notional=nk)
        c=2*real_cost_bps(p,sp=1.0,Y=0.5)/1e4*nk
        pnl=(gpnl*nk - c).sum()/yrs
        curve.append(dict(strategy=n,k=k,nav=k*nav_mean,ret_on_nav=pnl/(k*nav_mean),gross_ret=(gpnl*nk).sum()/yrs/(k*nav_mean),cost_ret=c.sum()/yrs/(k*nav_mean),
                          share_at_cap=float(np.mean(nk>=0.98*np.where(np.isnan(cap),np.inf,cap)))))
C=pd.DataFrame(curve); C.to_csv(OUT+"C_capacity_curve.csv",index=False)
out=[]
for n,g in C.groupby("strategy"):
    g=g.sort_values("k"); ref=g.ret_on_nav.iloc[:2].max()          # small-account return (no cap binding, minimal impact)
    # last k where return >= ref/2
    half=g[g.ret_on_nav>=ref/2]
    if g.ret_on_nav.iloc[-1]>=ref/2: nav_half=np.inf
    else:
        i=np.where(g.ret_on_nav.values<ref/2)[0][0]
        if i==0: nav_half=g.nav.iloc[0]
        else:
            x0,x1=np.log(g.nav.values[i-1]),np.log(g.nav.values[i]); y0,y1=g.ret_on_nav.values[i-1],g.ret_on_nav.values[i]
            nav_half=float(np.exp(x0+(ref/2-y0)/(y1-y0)*(x1-x0)))
    r1=g[g.k==1].ret_on_nav.iloc[0]
    out.append(dict(strategy=n,small_acct_ret=ref,ret_at_runNAV=r1,run_mean_nav=g.nav[g.k==1].iloc[0],nav_where_return_halves=nav_half,
       ret_100k=np.interp(np.log(1e5),np.log(g.nav),g.ret_on_nav),ret_1M=np.interp(np.log(1e6),np.log(g.nav),g.ret_on_nav),ret_10M=np.interp(np.log(1e7),np.log(g.nav),g.ret_on_nav),ret_100M=np.interp(np.log(1e8),np.log(g.nav),g.ret_on_nav)))
O=pd.DataFrame(out).round(4); O.to_csv(OUT+"C_capacity_strategy.csv",index=False)
# average positions and size per position (implementability at small NAV)
sz=[]
for n in O.strategy:
    d,_=load_run(n); a=(d.gross_exposure/d.nav)[d.n_open_positions>0]; sz.append(dict(strategy=n,avg_open=d.n_open_positions[d.n_open_positions>0].mean(),avg_gross_over_nav=a.mean(),
        avg_pos_pct_nav=(a/d.n_open_positions[d.n_open_positions>0]).mean()*100))
O=O.merge(pd.DataFrame(sz),on="strategy"); O.to_csv(OUT+"C_capacity_strategy.csv",index=False)
pd.options.display.width=250; print(O.to_string())
# ---- recipes on calm_weighted positions (36 recipes) ----
for ref in ["calm_weighted","calm_rank_tilt"]:
    d,j=load_run(ref); pos=pd.read_parquet(V+f"runs/{ref}/positions.parquet")
    pos["liquidity_quintile"]=pos.liquidity_quintile.astype(float).fillna(2); pos["avg_dollar_volume_21d"]=pos.avg_dollar_volume_21d.astype(float); pos["beta_252d"]=pos.beta_252d.astype(float)
    pos["side_s"]=np.where(pos.notional>0,"L","S"); an=pos.notional.abs()
    pos["liq"]=np.where(pos.liquidity_quintile<=2,"thin",np.where(pos.liquidity_quintile==3,"mid","liquid"))
    pos["beta_t"]=pd.qcut(pos.beta_252d,3,labels=["lowb","midb","highb"])
    pos["hold"]=np.where(pos.holding_days>60,">60d","<=60d")
    c_eng=pos.cost; c_real=2*real_cost_bps(pos,1.0,0.5)/1e4*an
    pos["net_eng"]=pos.gross_pnl-c_eng; pos["net_real"]=pos.gross_pnl-c_real
    pos["yr"]=pos.day0_date.dt.year
    cap5=0.05*pos.avg_dollar_volume_21d
    pos["deploy_yr"]=cap5/ (pos.yr.nunique() if False else 18.5)       # $ tradable per year at the 5%-ADV cap if EVERY event in the recipe is taken
    pos["concurrent"]=cap5*pos.holding_days/252/18.5                      # average concurrent capital that the same events support
    g=pos.groupby(["side_s","liq","beta_t","hold"],observed=True)
    R=g.apply(lambda x: pd.Series(dict(n=len(x),money_share=x.notional.abs().sum()/an.sum(),gross_bp=x.gross_pnl.sum()/x.notional.abs().sum()*1e4,
        net_bp_engine=x.net_eng.sum()/x.notional.abs().sum()*1e4,net_bp_real=x.net_real.sum()/x.notional.abs().sum()*1e4,
        deployable_per_yr_5pctADV=x.deploy_yr.sum(),supports_concurrent_capital=x.concurrent.sum(),
        pct_quarters_pos_real=(x.groupby("ann_quarter").net_real.sum()>0).mean(),
        real_total_pnl=x.net_real.sum())),include_groups=False).reset_index()
    R.sort_values("net_bp_real",ascending=False).round(3).to_csv(OUT+f"C_capacity_recipes_{ref}.csv",index=False)
    print(ref); print(R.sort_values("net_bp_real",ascending=False).round(3).head(12).to_string())
