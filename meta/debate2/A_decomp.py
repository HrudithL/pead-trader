"""A_decomp.py: P&L attribution of every post-fix equity book (reads V read-only, writes meta/debate2/A_*.csv).
position return basis: raw_i=CS[h-1], m_i=CM[h-1] (arith sums; exactly reproduces hold_ret=raw-m),
 a_i = raw_i - beta_i*m_i  (CAPM alpha over the hold), beta_i = trailing beta_252d (pit).
 mktadj book P&L ret x = a + (beta-1)m   ; raw book ret = a + beta*m
 a_i = U (quarter mean of a over ALL events, same horizon) + S (quarter x size cell - quarter)
       + Vo (quarter x size x ivol-tercile cell - quarter x size) + R (residual = surprise-specific + noise)
 leave-one-out cell means; fallback to coarser cell if n<10."""
import numpy as np, pandas as pd, json
from pathlib import Path
D = Path(__file__).resolve().parent
V = Path("C:/Users/hrudi/Documents/BEI/_pead_consolidation/vintage_fixed/runs")
E = pd.read_parquet(D/"A_events.parquet"); z = np.load(D/"A_paths.npz"); CS, CM = z["CS"], z["CM"]
E["beta"] = E.beta_252d.astype(float).fillna(1.0).clip(-1, 4)
def codes(*cols):
    return pd.factorize(pd.MultiIndex.from_arrays([E[c].astype("float64").fillna(-1) if E[c].dtype.kind!="O" and str(E[c].dtype)!="period[Q-DEC]" else E[c].astype(str) for c in cols]))[0]
cq = codes("ann_quarter"); cqs = codes("ann_quarter","size_quintile"); cqsv = codes("ann_quarter","size_quintile","ivol_252d_tercile")
beta = E.beta.to_numpy()
cache = {}
def bench(h):
    if h in cache: return cache[h]
    A = (CS[:,h-1] - beta*CM[:,h-1]).astype(float)
    out = []
    for c in (cq, cqs, cqsv):
        out.append((np.bincount(c, A, minlength=c.max()+1), np.bincount(c, minlength=c.max()+1).astype(float)))
    cache[h] = (A, out); return cache[h]
def loo(S, N, c, a, mn=10):
    n = N[c]-1; return np.where(n >= mn, (S[c]-a)/np.maximum(n,1), np.nan)
def era(y): return np.where(y<=2001,"1996-01",np.where(y<=2007,"2002-07","2008-13"))

def attribute(pos, raw_basis):
    ev = E.reset_index()[["index","permno","day0_date"]]
    p = pos.merge(ev, on=["permno","day0_date"], how="left")
    assert p["index"].notna().all()
    i = p["index"].to_numpy(int); h = p.holding_days.to_numpy(int)
    raw = np.empty(len(p)); m = np.empty(len(p)); a = np.empty(len(p))
    L1=np.empty(len(p)); L2=L1.copy(); L3=L1.copy()
    hc = np.minimum(h,150)
    for hh in np.unique(hc):
        s = np.where(hc==hh)[0]; ii = i[s]
        A, ((S1,N1),(S2,N2),(S3,N3)) = bench(hh)
        raw[s]=CS[ii,hh-1]; m[s]=CM[ii,hh-1]; a[s]=A[ii]
        l1=loo(S1,N1,cq[ii],A[ii],30); l2=loo(S2,N2,cqs[ii],A[ii]); l3=loo(S3,N3,cqsv[ii],A[ii])
        l2=np.where(np.isnan(l2),l1,l2); l3=np.where(np.isnan(l3),l2,l3)
        L1[s]=l1; L2[s]=l2; L3[s]=l3
    b = beta[i]
    p["term_beta"] = (b if raw_basis else b-1.0)*m
    x = p.hold_ret.to_numpy()   # exact mkt-adj hold return
    raw = x + m   # raw return = mktadj + market (exact up to float32 on m)
    a = raw - b*m
    p["term_U"]=L1; p["term_S"]=L2-L1; p["term_V"]=L3-L2; p["term_R"]=a-L3
    ret_basis = raw if raw_basis else x
    p["chk_err"] = (p.notional*ret_basis - p.gross_pnl).abs()
    p["term_R"] = ret_basis - p.term_beta - L3 + 0*L3 if False else p["term_R"]
    for t in ["beta","U","S","V","R"]: p["pnl_"+t] = p.notional*p["term_"+t]
    p["era"]=era(p.day0_date.dt.year.to_numpy()); p["a_idio"]=a-L3; p["raw"]=raw
    return p

books = "balanced_leverage calm_weighted calm_rank_tilt conviction_tilt sector_balanced extreme_picks graded_bets size_balanced market_boost market_exposed balanced_leverage_max calm_rank_levered".split()
rows=[]; allp={}
for s in books:
    j=json.load(open(V/s/"summary.json")); raw_basis = j["params"]["return_basis"]=="raw"
    pos=pd.read_parquet(V/s/"positions.parquet")
    pos = pos[pos.holding_days>0].copy()
    pa = attribute(pos, raw_basis); allp[s]=pa
    d = pd.read_csv(V/s/"daily.csv.gz", parse_dates=["date"]); d["nav_prev"]=d.nav.shift(1)
    d["era"]=era(d.date.dt.year.to_numpy()); d=d[(d.date.dt.year>=1996)&(d.date.dt.year<=2013)]
    ovl = d.groupby("era").pnl_overlay.sum() if "pnl_overlay" in d else None
    print(s, "gross check max err", pa.chk_err.max().round(3), "n", len(pa), flush=True)
    for e in ["1996-01","2002-07","2008-13","ALL"]:
        g = pa if e=="ALL" else pa[pa.era==e]
        dd = d if e=="ALL" else d[d.era==e]
        yrs = len(dd)/252; navm = dd.nav.mean()
        net = g.net_pnl.sum(); 
        qs = g.groupby("ann_quarter").apply(lambda x:(x.pnl_R).sum()/navm, include_groups=False)
        tR = qs.mean()/(qs.std(ddof=1)/np.sqrt(len(qs))) if len(qs)>3 else np.nan
        r = dict(book=s, era=e, years=round(yrs,1), net_pts_yr=net/navm/yrs*100, n_pos=len(g), cost_pts_yr=-g.cost.sum()/navm/yrs*100)
        for sd,nm in ((1,"long"),(-1,"short")): r[f"net_{nm}_pts"] = g[g.side==sd].net_pnl.sum()/navm/yrs*100
        for t in ["beta","U","S","V","R"]:
            r[f"{t}_pts"]=g["pnl_"+t].sum()/navm/yrs*100
            r[f"R_pts_long"]=g[g.side==1].pnl_R.sum()/navm/yrs*100; r["R_pts_short"]=g[g.side==-1].pnl_R.sum()/navm/yrs*100
        r["overlay_pts"]= (dd.pnl_overlay.sum()/navm/yrs*100) if "pnl_overlay" in dd else 0.0
        r["R_share_of_net"]= r["R_pts"]/r["net_pts_yr"] if r["net_pts_yr"]!=0 else np.nan
        r["R_t_quarterly"]=tR; r["n_quarters"]=len(qs)
        rows.append(r)
T=pd.DataFrame(rows); T.to_csv(D/"A_decomp_by_book_era.csv", index=False)
pd.set_option("display.width",250); pd.set_option("display.max_columns",30)
print(T[T.era=="ALL"].round(2).to_string())
for e in ["1996-01","2002-07","2008-13"]:
    print(e); print(T[T.era==e][["book","net_pts_yr","net_long_pts","net_short_pts","beta_pts","U_pts","S_pts","V_pts","R_pts","R_pts_long","R_pts_short","cost_pts_yr","R_share_of_net","R_t_quarterly"]].round(2).to_string())
pd.to_pickle({k:v[["permno","day0_date","side","notional","net_pnl","cost","decile","size_quintile","ivol_252d_tercile","ivol_252d_pct","beta_252d","liquidity_quintile","holding_days","era","ann_quarter","a_idio","term_U","term_S","term_V","term_R","term_beta","raw","hold_ret","pnl_R","pnl_U","pnl_S","pnl_V","pnl_beta"]] for k,v in allp.items()}, D/"A_positions_attr.pkl")
