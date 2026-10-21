"""hedged_blend / calm_blend unlevered ROI (tied capital = w_eq*(L+1.5S)/NAV + w_opt*premium/NAV), base realistic cost on equity sleeve, graded borrow fee. -> C_blend.csv"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from C_common import *
rows=[]
for bl,eq in [("hedged_blend","calm_weighted"),("calm_blend","calm_rank_tilt")]:
    b,_=load_run(bl); d,_=load_run(eq); pos=pd.read_parquet(V+f"runs/{eq}/positions.parquet")
    o,_=load_run("options_cap20"); op=pd.read_parquet(V+"runs/options_cap20/positions.parquet")
    D=b.date.values; N=len(b); rf,gap=rf_daily(b.date); yrs=gap.sum()/365.25
    w=json.load(open(V+f"runs/{bl}/summary.json"))["params"]; weq=w[f"w_{eq}"]; wop=w["w_options_cap20"]
    prev=np.r_[d.nav.values[0],d.nav.values[:-1]]
    T=(d.long_exposure.values+1.5*d.short_exposure.abs().values)/prev
    ei=np.clip(np.searchsorted(D,op.day0_date.values,side="right"),0,N); xi=np.clip(np.searchsorted(D,(op.day0_date+pd.Timedelta(days=84)).values,side="left"),0,N)
    a=np.zeros(N+1); np.add.at(a,ei,op.premium_paid.values); np.add.at(a,xi,-op.premium_paid.values); prem=np.cumsum(a)[:N]
    oprev=np.r_[o.nav.values[0],o.nav.values[:-1]]; oN=pd.Series(o.nav.values,index=o.date).reindex(b.date).ffill().bfill().values
    P=prem/np.r_[oN[0],oN[:-1]]
    Tb=weq*T+wop*P
    ei2=np.clip(np.searchsorted(D,pos.day0_date.values,side="right"),0,N-1); xi2=np.clip(np.searchsorted(D,pos.exit_date.values,side="left"),0,N-1)
    an=pos.notional.abs().to_numpy(float); dx=(2*real_cost_bps(pos,1,0.5)/1e4*an-pos.cost.values)/2
    e=np.zeros(N); np.add.at(e,ei2,dx); np.add.at(e,xi2,dx)
    sh=pos.notional<0; q=pos.size_quintile.fillna(3).astype(int).values
    fee_q={1:8,2:4,3:2,4:1,5:0.5}; fee=np.zeros(N)
    for k in range(1,6):
        m=(sh.values)&(q==k); s_=np.zeros(N+1); np.add.at(s_,ei2[m],an[m]); np.add.at(s_,xi2[m],-an[m]); fee+=fee_q[k]/100*np.cumsum(s_)[:N]/365*gap/prev
    r0=b.daily_return.values
    for lab,r in [("engine cost",r0),("realistic cost",r0-weq*e/prev),("realistic + graded fee",r0-weq*e/prev-weq*fee)]:
        s=min(1,1/Tb.max()); acct=s*r+(1-s*Tb)*rf
        rows.append(dict(strategy=bl,scenario=lab,ann_ret_nav=np.prod(1+r)**(1/yrs)-1,avgT=Tb[Tb>0.01].mean(),maxT=Tb.max(),scale=s,unlev_acct_ret=np.prod(1+acct)**(1/yrs)-1,tbill=rf.sum()/yrs,
           roi_per_avg_tied=(r.sum()/yrs)/Tb[Tb>0.01].mean()))
R=pd.DataFrame(rows).round(4); R.to_csv(OUT+"C_blend.csv",index=False); print(R.to_string())
