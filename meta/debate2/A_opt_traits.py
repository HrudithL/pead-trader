"""A_opt_traits.py: options books split (spread cost / call-put / era / decile) and traits of best equity trades."""
import numpy as np, pandas as pd
from pathlib import Path
D=Path(__file__).resolve().parent
V=Path("C:/Users/hrudi/Documents/BEI/_pead_consolidation/vintage_fixed/runs")
pd.set_option("display.width",220)
rows=[]
for b in ["options_cap20","options_cap30","options_worst_case"]:
    p=pd.read_parquet(V/b/"positions.parquet"); p["era"]=np.where(p.day0_date.dt.year<=2001,"1996-01",np.where(p.day0_date.dt.year<=2007,"2002-07","2008-13"))
    mid0=p.kelly_fwd_mid_0d; ask0=p.kelly_fwd_ask_0d; mid60=p.kelly_fwd_mid_60d; bid60=p.kelly_fwd_bid_60d
    entry_spread=(ask0-mid0).fillna(0)*p.contracts*100
    # mid-fill P&L = net + entry half-spread + exit half-spread (exit: mid-bid where quotes exist; dead quote -> 0 bid, mark of mid unknown)
    exit_spread=(mid60-bid60).clip(lower=0).fillna(0)*p.contracts*100
    p["mid_pnl"]=p.net_pnl+entry_spread+exit_spread; p["spr"]=entry_spread+exit_spread
    for e in ["ALL","1996-01","2002-07","2008-13"]:
        g=p if e=="ALL" else p[p.era==e]
        for k,gg in [("all",g),("call",g[g.kelly_side=="C"]),("put",g[g.kelly_side=="P"]),("dec1-3",g[g.decile<=3]),("dec8-10",g[g.decile>=8])]:
            rows.append(dict(book=b,era=e,slice=k,n=len(gg),net_M=gg.net_pnl.sum()/1e6,mid_M=gg.mid_pnl.sum()/1e6,spread_M=-gg.spr.sum()/1e6,prem_M=gg.premium_paid.sum()/1e6,net_roi_pct=100*gg.net_pnl.sum()/gg.premium_paid.sum()))
O=pd.DataFrame(rows); O.to_csv(D/"A_options_split.csv",index=False)
print(O[(O.book=="options_cap20")].round(2).to_string())
print(O[(O.book!="options_cap20")&(O.slice=="all")&(O.era=="ALL")].round(2).to_string())
P=pd.read_pickle(D/"A_positions_attr.pkl")
out=[]
for b in ["balanced_leverage","calm_rank_tilt"]:
    p=P[b].copy(); p["w"]=p.notional.abs(); p["roi"]=p.net_pnl/p.w
    p["lowvol"]=p.ivol_252d_tercile; p["hb"]=pd.cut(p.beta_252d,[-9,.8,1.2,9])
    p["R_signed"]=p.term_R*p.side
    thr=p.roi.quantile(.9); win=p.roi>=thr
    print(b,"top10% trades share of positive pnl:",round(p[win].net_pnl.sum()/p[p.net_pnl>0].net_pnl.sum(),3),"| total net M",round(p.net_pnl.sum()/1e6,1),"| top10% net M",round(p[win].net_pnl.sum()/1e6,1))
    for c in ["side","decile","size_quintile","ivol_252d_tercile","liquidity_quintile","hb","era"]:
        t=pd.DataFrame({"share_all":p.groupby(c,observed=True).w.sum()/p.w.sum(),"share_win":p[win].groupby(c,observed=True).w.sum()/p[win].w.sum(),"roi_bp":p.groupby(c,observed=True).apply(lambda x:1e4*x.net_pnl.sum()/x.w.sum(),include_groups=False),"signedR_bp":p.groupby(c,observed=True).apply(lambda x:1e4*(x.pnl_R).sum()/x.w.sum(),include_groups=False)})
        t["lift"]=t.share_win/t.share_all; t.insert(0,"book",b); t.insert(1,"trait",c); out.append(t.reset_index().rename(columns={c:"level"}))
        if b=="balanced_leverage": print(t.round(3))
    print("by-year net M, long/short:",(p.assign(y=p.day0_date.dt.year).pivot_table(index="y",columns="side",values="net_pnl",aggfunc="sum")/1e6).round(1).T.to_string())
pd.concat(out).to_csv(D/"A_best_trade_traits.csv",index=False)
