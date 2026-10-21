"""Per-leg gross ROI per dollar of capital tied (long: 1.0x, short: 1.5x), market-adjusted P&L, BEFORE costs.  -> C_legs.csv"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from C_common import *
rows=[]
for n in sorted(os.listdir(V+"runs")):
    if not os.path.exists(V+f"runs/{n}/positions.parquet") or n.startswith("options") or n.endswith("_legacy") or "midfill" in n: continue
    d,j=load_run(n); pos=pd.read_parquet(V+f"runs/{n}/positions.parquet")
    _,gap=rf_daily(d.date); yrs=gap.sum()/365.25
    L=d.long_exposure.values; S=d.short_exposure.abs().values; act=(L+S)>0
    pl=d.pnl_long.sum()/yrs; ps=d.pnl_short.sum()/yrs
    cl=pos.loc[pos.notional>0,'cost'].sum()/yrs; cs=pos.loc[pos.notional<0,'cost'].sum()/yrs
    rows.append(dict(strategy=n, long_pnl_per_avg_long_dollar=pl/L[act].mean(), long_net_engine_cost=(pl-cl)/L[act].mean(),
        short_pnl_per_avg_collateral_dollar=ps/(1.5*S[act].mean()), short_net_engine_cost=(ps-cs)/(1.5*S[act].mean()),
        short_pnl_per_short_dollar=ps/S[act].mean(), avg_long_over_nav=(L/d.nav).mean(), avg_short_over_nav=(S/d.nav).mean()))
R=pd.DataFrame(rows).round(4); R.to_csv(OUT+"C_legs.csv",index=False); print(R.to_string())
