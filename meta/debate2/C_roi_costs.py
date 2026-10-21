"""Perspective C parts 1-2: true unlevered ROI under no-borrow rules, borrow-fee & cash scenarios, realistic cost re-pricing.
python meta/debate2/C_roi_costs.py  -> C_roi_long.csv, C_roi_summary.csv"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from C_common import *
names = sorted(n for n in os.listdir(V+"runs") if os.path.exists(V+f"runs/{n}/positions.parquet") and not n.startswith("options") and not n.endswith("_legacy") and "midfill" not in n)
FEE = {"fee0":{q:0 for q in range(1,6)}, "fee2":{q:2 for q in range(1,6)}, "fee5":{q:5 for q in range(1,6)},
       "feeGraded":{1:8,2:4,3:2,4:1,5:0.5}}          # %/yr on short notional; graded by size quintile (1 = smallest)
mkt = pd.read_parquet("C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-evaluation-roi-74c7dd/meta/out/market.parquet").set_index("date")["vwretd"]
rows = []; sumrows = []
def stats(r, gap):
    yrs = gap.sum()/365.25; g = np.prod(1+r)**(1/yrs)-1
    return g, r.std()*np.sqrt(252)
for n in names:
    d, j = load_run(n); pos = pd.read_parquet(V+f"runs/{n}/positions.parquet")
    basis = j["params"]["return_basis"]; beta_ov = j["params"].get("beta_overlay", 0)
    dates = d.date.values; nav = d.nav.values; N = len(d)
    rf, gap = rf_daily(d.date)
    prev_nav = np.r_[nav[0], nav[:-1]]
    r0 = d.daily_pnl.values / prev_nav
    L = d.long_exposure.values; S = d.short_exposure.abs().values
    ov = d.overlay_notional.values if "overlay_notional" in d else np.zeros(N)
    tied = L + 1.5*S + np.where(ov>0, ov, -1.5*ov)
    if "overlay_notional" in d: pass
    T = tied / prev_nav                     # exposure carried into day t / NAV at start of day t
    T = np.where(prev_nav>0, T, 0)
    # position -> index mapping
    ei = np.clip(np.searchsorted(d.date.values, pos.day0_date.values, side="right"), 0, N-1)
    xi = np.clip(np.searchsorted(d.date.values, pos.exit_date.values, side="left"), 0, N-1)
    side = np.sign(pos.notional.values); an = np.abs(pos.notional.values); q = pos.size_quintile.fillna(3).astype(int).values
    # (note: exposure arrays in daily file are end-of-day; T above uses same-day exposure / prior NAV, close enough)
    Sq = {}
    for k in range(1,6):
        a = np.zeros(N+1); m = (side<0)&(q==k)
        np.add.at(a, ei[m], an[m]); np.add.at(a, xi[m], -an[m]); Sq[k] = np.cumsum(a)[:N]
    # extra cost scenarios (applied half at entry idx, half at exit idx)
    extra = {"engine": np.zeros(N)}
    for sc, p in SCEN.items():
        if p is None: continue
        newc = 2*real_cost_bps(pos, **p)/1e4 * an
        dx = (newc - pos.cost.values)/2; e = np.zeros(N); np.add.at(e, ei, dx); np.add.at(e, xi, dx); extra[sc] = e
    # net market contribution of residual net exposure (mktadj books only): raw pnl = mktadj pnl + net_expo * mkt
    mk = mkt.reindex(d.date).fillna(0).values
    net_prev = np.r_[0, d.net_exposure.values[:-1]]
    mktcontrib = (net_prev*mk/prev_nav) if basis=="mktadj" else np.zeros(N)
    active = T > 0.01
    for sc, e in extra.items():
        rc = r0 - e/prev_nav
        for fk, fq in FEE.items():
            # borrow fee on shorts: sum_k f_k * S_k,t /365 * gap
            fee = sum(fq[k]/100*Sq[k] for k in range(1,6))/365*gap/prev_nav
            net = rc - fee
            g, vol = stats(net, gap)
            maxT = np.nanmax(T); s = min(1.0, 1/maxT) if maxT>0 else 1.0     # largest feasible scale so (L+1.5S)/NAV <= 1 every day
            s_p99 = min(1.0, 1/np.nanpercentile(T[active], 99.5))
            acct = s*net + (1 - s*T)*rf                      # idle cash earns T-bill; collateral earns 0 (conservative)
            acct_nocash = s*net
            ga, va = stats(acct, gap)
            yrs = gap.sum()/365.25
            roi_avg = (net.sum()/yrs) / T[active].mean()     # annual arithmetic return per average dollar tied up
            rows.append(dict(strategy=n, basis=basis, cost=sc, borrow=fk, ann_ret_on_nav=g, vol=vol, sharpe=(net.mean()*252)/(vol) if vol else np.nan,
                 avgT=T[active].mean(), p995T=np.nanpercentile(T[active],99.5), maxT=maxT, scale_feasible=s,
                 roi_per_avg_tied=roi_avg, roi_per_peak_tied=(net.sum()/yrs)*s, unlev_acct_ret_nocash=ga if False else stats(acct_nocash,gap)[0],
                 unlev_acct_ret_with_cash=ga, tbill_avg=(rf.sum()/yrs), excess_over_tbill=ga-(rf.sum()/yrs),
                 net_mkt_contrib_ann=(mktcontrib.sum()/yrs), cost_extra_ann=(extra[sc]/prev_nav).sum()/yrs, fee_ann=fee.sum()/yrs))
R = pd.DataFrame(rows); R.to_csv(OUT+"C_roi_long.csv", index=False)
# --- options books (premium is the capital; no leverage, no shorts) and blends
orows = []
for n in ["options_cap20","options_cap30","options_worst_case"]:
    d, j = load_run(n); pos = pd.read_parquet(V+f"runs/{n}/positions.parquet")
    nav=d.nav.values; N=len(d); rf,gap=rf_daily(d.date); prev=np.r_[nav[0],nav[:-1]]
    ei=np.searchsorted(d.date.values,pos.day0_date.values,side="right"); xi=np.searchsorted(d.date.values,(pos.day0_date+pd.Timedelta(days=84)).values,side="left")
    ei=np.clip(ei,0,N); xi=np.clip(xi,0,N); a=np.zeros(N+1); np.add.at(a,ei,pos.premium_paid.values); np.add.at(a,xi,-pos.premium_paid.values); prem=np.cumsum(a)[:N]
    P=prem/prev; r=d.daily_pnl.values/prev; yrs=gap.sum()/365.25; act=P>0.001
    g=np.prod(1+r)**(1/yrs)-1
    # 2011 missing in file: days with zero activity are included in NAV span (conservative)
    roi=(r.sum()/yrs)/P[act].mean()
    acct=r+(1-P)*rf; ga=np.prod(1+acct)**(1/yrs)-1
    orows.append(dict(strategy=n,basis="options",cost="bid_ask(engine)",borrow="n/a",ann_ret_on_nav=g,avgT=P[act].mean(),maxT=P.max(),roi_per_avg_tied=roi,
        roi_per_peak_tied=(r.sum()/yrs)/P.max(), unlev_acct_ret_with_cash=ga, tbill_avg=rf.sum()/yrs, excess_over_tbill=ga-rf.sum()/yrs, sharpe=(r.mean()*252)/(r.std()*np.sqrt(252))))
pd.DataFrame(orows).to_csv(OUT+"C_roi_options.csv", index=False)
# --- compact summary: base cost, graded fee vs 0/2/5
b = R[R.cost=="base"].pivot_table(index="strategy", columns="borrow", values="unlev_acct_ret_with_cash")
e = R[(R.cost=="engine")&(R.borrow=="fee0")].set_index("strategy")
w = R[(R.cost=="base")&(R.borrow=="fee0")].set_index("strategy")
S = pd.DataFrame({"basis":w.basis,"engine_ret_nav":e.ann_ret_on_nav,"base_cost_ret_nav":w.ann_ret_on_nav,"avgT":w.avgT,"maxT":w.maxT,"feasible_scale":w.scale_feasible,
  "roi_per_avg_tied_engine":e.roi_per_avg_tied,"roi_per_avg_tied_base":w.roi_per_avg_tied,"roi_per_peak_tied_base":w.roi_per_peak_tied,
  "acct_fee0":b["fee0"],"acct_fee2":b["fee2"],"acct_fee5":b["fee5"],"acct_graded":b["feeGraded"],"tbill":w.tbill_avg,"net_mkt_contrib":w.net_mkt_contrib_ann}).round(4)
S["excess_graded"]=(S.acct_graded-S.tbill).round(4)
S.sort_values("acct_graded",ascending=False).to_csv(OUT+"C_roi_summary.csv")
pd.options.display.width=250; pd.options.display.max_columns=30
print(S.sort_values("acct_graded",ascending=False).to_string())
print(pd.DataFrame(orows).round(4).to_string())
