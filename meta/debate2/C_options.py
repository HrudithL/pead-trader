"""Perspective C part 4: options_cap20 post-fix edge by moneyness / year / decile / spread / side. -> C_options_*.csv"""
import sys, os, glob; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from C_common import *
pos=pd.read_parquet(V+"runs/options_cap20/positions.parquet")
evf=pd.read_parquet(V+"data/event_options/event_vol_features.parquet")[["secid","day0_date","permno","atm_iv","iv_rv"]].drop_duplicates(["secid","day0_date"])
pos=pos.merge(evf,on=["secid","day0_date"],how="left")
R="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86/data/normalized_equity/"
px=pd.concat([pd.read_parquet(f,columns=["permno","date","prc"]) for f in sorted(glob.glob(R+"equity_*.parquet"))])
px["prc"]=px.prc.abs().astype(float); px=px.rename(columns={"date":"day0_date","prc":"S0"}).drop_duplicates(["permno","day0_date"])
pos=pos.merge(px,on=["permno","day0_date"],how="left")
pos["mny"]=pos.kelly_strike/pos.S0
pos["year"]=pos.day0_date.dt.year
pos["spread0"]=(pos.kelly_fwd_ask_0d-pos.kelly_fwd_bid_0d)/pos.kelly_fwd_mid_0d
pos["rs_ask_to_bid"]=pos.net_pnl/pos.premium_paid   # return on premium actually realised (ask in, bid out)
# mid-to-mid return at the 60d checkpoint, per $ of premium, for the SAME contracts (what the edge looked like pre-spread)
pos["ret_mid"]=pos.kelly_fwd_mid_60d/pos.kelly_fwd_mid_0d-1
pos["ret_ba"]=pos.kelly_fwd_bid_60d/pos.kelly_fwd_ask_0d-1
def tab(g):
    w=g.premium_paid
    return pd.Series(dict(n=len(g),premium=w.sum(),pnl=g.net_pnl.sum(),roi=g.net_pnl.sum()/w.sum(),mean_ret_ba=g.ret_ba.mean(),mean_ret_mid=g.ret_mid.mean(),med_spread=g.spread0.median(),hit=(g.net_pnl>0).mean()))
print("coverage S0",pos.S0.notna().mean())
pos["mny_b"]=pd.cut(pos.mny,[0,.8,.9,.95,1.0,1.05,1.1,1.2,5])
pos["spr_b"]=pd.cut(pos.spread0,[0,.05,.1,.2,.4,10])
pos["prem_b"]=pd.qcut(pos.kelly_premium,5)
pos["dec_b"]=pos.decile
for key in ["year","mny_b","dec_b","spr_b","kelly_side","prem_b"]:
    t=pos.groupby(key,observed=True).apply(tab,include_groups=False).round(4); t.to_csv(OUT+f"C_options_by_{key}.csv"); print("\n",key); print(t.to_string())
# quarterly t-stat of total, and ex-2003/2013
q=pos.assign(qtr=pos.day0_date.dt.to_period("Q")).groupby("qtr").agg(pnl=("net_pnl","sum"),prem=("premium_paid","sum"))
q["r"]=q.pnl/q.prem
print("\nquarterly roi mean %.4f t=%.2f n=%d"%(q.r.mean(),q.r.mean()/q.r.std()*np.sqrt(len(q)),len(q)))
for ex in [[2003],[2013],[2003,2013]]:
    m=~pos.year.isin(ex); print("ex",ex,"roi",pos.net_pnl[m].sum()/pos.premium_paid[m].sum())
print("total roi on premium",pos.net_pnl.sum()/pos.premium_paid.sum(),"mid-mid mean ret",pos.ret_mid.mean(),"ba mean",pos.ret_ba.mean())
# restricted rule: only tight spreads (<10%) and moneyness 0.9-1.1
for lab,m in {"spread<10%":pos.spread0<0.1,"spread<5%":pos.spread0<0.05,"spread<10% & |mny-1|<.1":(pos.spread0<0.1)&(pos.mny.sub(1).abs()<.1),"calls only":pos.kelly_side=="C"}.items():
    g=pos[m]; print(lab,len(g),"roi %.4f"%(g.net_pnl.sum()/g.premium_paid.sum()),"mean ret_ba %.4f"%g.ret_ba.mean())
def qt(m,lab):
    g=pos[m].assign(qtr=pos.day0_date.dt.to_period("Q")).groupby("qtr").agg(p=("net_pnl","sum"),c=("premium_paid","sum")); r=g.p/g.c
    print(lab,"n=%d roi=%.4f quarters=%d qmean=%.4f t=%.2f  roi ex 2003/2013=%.4f"%(m.sum(),pos.net_pnl[m].sum()/pos.premium_paid[m].sum(),len(r),r.mean(),r.mean()/r.std()*np.sqrt(len(r)),
        pos.net_pnl[m&~pos.year.isin([2003,2013])].sum()/pos.premium_paid[m&~pos.year.isin([2003,2013])].sum()))
print()
qt(pos.mny.notna(),"ALL"); qt((pos.mny>1.0)&(pos.mny<=1.2),"OTM 1.0-1.2"); qt((pos.spread0<0.1)&(pos.mny.sub(1).abs()<.1),"tight&ATM")
qt(pos.mny<=0.8,"deep ITM calls"); qt(pos.decile>=9,"deciles 9-10"); qt((pos.decile>=9)&(pos.kelly_side=="C"),"d9-10 calls"); qt(pos.spread0<0.2,"spread<20%")
qt((pos.decile>=9)&(pos.spread0<0.2),"d9-10 & spread<20%")
pos.drop(columns=["mny_b","spr_b","prem_b"]).to_csv(OUT+"C_options_positions_enriched.csv.gz",index=False)
